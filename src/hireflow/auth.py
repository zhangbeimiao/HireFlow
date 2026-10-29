"""JWT authentication and role checks. Authorization is derived from signed claims only."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from hireflow.schemas import UserRole

_bearer_scheme = HTTPBearer(auto_error=False)
_password_hash = PasswordHash.recommended()


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    role: UserRole


def _secret() -> str:
    secret = os.getenv("HIREFLOW_JWT_SECRET")
    if not secret:
        raise RuntimeError("HIREFLOW_JWT_SECRET must be configured")
    return secret


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    return password_hash is not None and _password_hash.verify(password, password_hash)


def create_access_token(user_id: UUID, tenant_id: UUID, role: UserRole | str) -> str:
    now = datetime.now(UTC)
    normalized_role = UserRole(role)
    return jwt.encode(
        {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "role": normalized_role.value,
            "iat": now,
            "exp": now + timedelta(minutes=30),
        },
        _secret(),
        algorithm="HS256",
    )


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        claims = jwt.decode(credentials.credentials, _secret(), algorithms=["HS256"])
        return Principal(
            user_id=UUID(claims["sub"]),
            tenant_id=UUID(claims["tenant_id"]),
            role=UserRole(claims["role"]),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token") from error


def require_roles(*allowed_roles: UserRole):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return principal

    return dependency
