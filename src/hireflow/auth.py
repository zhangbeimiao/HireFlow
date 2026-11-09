"""JWT authentication and role checks. Authorization is derived from signed claims only."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from hireflow.schemas import UserRole

_bearer_scheme = HTTPBearer(auto_error=False)
_password_hash = PasswordHash.recommended()
JWT_ISSUER = "hireflow"
JWT_AUDIENCE = "hireflow-api"


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    role: UserRole


def _legacy_secret() -> str:
    secret = os.getenv("HIREFLOW_JWT_SECRET")
    if not secret:
        raise RuntimeError("HIREFLOW_JWT_SECRET must be configured")
    if len(secret.encode()) < 32:
        raise RuntimeError("HIREFLOW_JWT_SECRET must contain at least 32 bytes")
    return secret


def _signing_keys() -> tuple[dict[str, str], str]:
    raw_keys = os.getenv("HIREFLOW_JWT_KEYS")
    if raw_keys:
        try:
            parsed = json.loads(raw_keys)
        except json.JSONDecodeError as error:
            raise RuntimeError("HIREFLOW_JWT_KEYS must be a JSON object") from error
        if not isinstance(parsed, dict) or not parsed:
            raise RuntimeError("HIREFLOW_JWT_KEYS must be a non-empty JSON object")
        keys = {str(key_id): str(secret) for key_id, secret in parsed.items()}
    else:
        keys = {"legacy": _legacy_secret()}
    for key_id, secret in keys.items():
        if not key_id or len(secret.encode()) < 32:
            raise RuntimeError("every JWT key must have an id and contain at least 32 bytes")
    active_key_id = os.getenv("HIREFLOW_JWT_ACTIVE_KEY_ID") or next(iter(keys))
    if active_key_id not in keys:
        raise RuntimeError("HIREFLOW_JWT_ACTIVE_KEY_ID must identify a configured JWT key")
    return keys, active_key_id


def access_token_minutes() -> int:
    try:
        minutes = int(os.getenv("HIREFLOW_ACCESS_TOKEN_MINUTES", "30"))
    except ValueError as error:
        raise RuntimeError("HIREFLOW_ACCESS_TOKEN_MINUTES must be an integer") from error
    if not 1 <= minutes <= 60:
        raise RuntimeError("HIREFLOW_ACCESS_TOKEN_MINUTES must be between 1 and 60")
    return minutes


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    return password_hash is not None and _password_hash.verify(password, password_hash)


def create_access_token(user_id: UUID, tenant_id: UUID, role: UserRole | str) -> str:
    now = datetime.now(UTC)
    normalized_role = UserRole(role)
    keys, active_key_id = _signing_keys()
    return jwt.encode(
        {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "role": normalized_role.value,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=access_token_minutes()),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "jti": str(uuid4()),
            "token_type": "access",
        },
        keys[active_key_id],
        algorithm="HS256",
        headers={"kid": active_key_id},
    )


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        keys, _ = _signing_keys()
        header = jwt.get_unverified_header(credentials.credentials)
        key_id = header.get("kid")
        if not isinstance(key_id, str) or key_id not in keys:
            raise jwt.InvalidKeyError("unknown JWT key id")
        claims = jwt.decode(
            credentials.credentials,
            keys[key_id],
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={
                "require": [
                    "sub",
                    "tenant_id",
                    "role",
                    "iat",
                    "nbf",
                    "exp",
                    "iss",
                    "aud",
                    "jti",
                    "token_type",
                ]
            },
        )
        if claims["token_type"] != "access":
            raise jwt.InvalidTokenError("token is not an access token")
        return Principal(
            user_id=UUID(claims["sub"]),
            tenant_id=UUID(claims["tenant_id"]),
            role=UserRole(claims["role"]),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token"
        ) from error


def require_roles(*allowed_roles: UserRole):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return principal

    return dependency
