"""Opaque, rotating refresh tokens persisted behind tenant RLS."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.db import set_tenant_context
from hireflow.models import RefreshToken, User


class InvalidRefreshToken(ValueError):
    pass


def refresh_token_days() -> int:
    try:
        days = int(os.getenv("HIREFLOW_REFRESH_TOKEN_DAYS", "14"))
    except ValueError as error:
        raise RuntimeError("HIREFLOW_REFRESH_TOKEN_DAYS must be an integer") from error
    if not 1 <= days <= 90:
        raise RuntimeError("HIREFLOW_REFRESH_TOKEN_DAYS must be between 1 and 90")
    return days


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _new_token(tenant_id: UUID, user_id: UUID) -> tuple[str, RefreshToken]:
    raw_token = f"{tenant_id}.{secrets.token_urlsafe(48)}"
    return raw_token, RefreshToken(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        token_hash=_token_hash(raw_token),
        expires_at=datetime.now(UTC) + timedelta(days=refresh_token_days()),
    )


def _tenant_from_token(raw_token: str) -> UUID:
    try:
        tenant_part, secret_part = raw_token.split(".", 1)
        if len(secret_part) < 32:
            raise ValueError
        return UUID(tenant_part)
    except (ValueError, AttributeError) as error:
        raise InvalidRefreshToken("invalid refresh token") from error


async def issue_refresh_token(session: AsyncSession, user: User) -> str:
    # Bootstrap and password-login endpoints are public, so their database
    # dependency cannot derive the tenant context from a bearer token. Set it
    # explicitly before writing the RLS-protected session row.
    await set_tenant_context(session, user.tenant_id)
    raw_token, token = _new_token(user.tenant_id, user.id)
    session.add(token)
    await session.commit()
    return raw_token


async def rotate_refresh_token(session: AsyncSession, raw_token: str) -> tuple[User, str]:
    tenant_id = _tenant_from_token(raw_token)
    await set_tenant_context(session, tenant_id)
    now = datetime.now(UTC)
    token = await session.scalar(
        select(RefreshToken)
        .where(
            RefreshToken.tenant_id == tenant_id,
            RefreshToken.token_hash == _token_hash(raw_token),
        )
        .with_for_update()
    )
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        await session.rollback()
        raise InvalidRefreshToken("invalid or expired refresh token")
    user = await session.scalar(
        select(User).where(User.id == token.user_id, User.tenant_id == tenant_id)
    )
    if user is None:
        await session.rollback()
        raise InvalidRefreshToken("refresh token user no longer exists")

    replacement_raw, replacement = _new_token(tenant_id, user.id)
    session.add(replacement)
    await session.flush()
    token.revoked_at = now
    token.replaced_by_id = replacement.id
    await session.commit()
    return user, replacement_raw


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    tenant_id = _tenant_from_token(raw_token)
    await set_tenant_context(session, tenant_id)
    token = await session.scalar(
        select(RefreshToken)
        .where(
            RefreshToken.tenant_id == tenant_id,
            RefreshToken.token_hash == _token_hash(raw_token),
        )
        .with_for_update()
    )
    if token is None or token.revoked_at is not None:
        await session.rollback()
        raise InvalidRefreshToken("invalid refresh token")
    token.revoked_at = datetime.now(UTC)
    await session.commit()
