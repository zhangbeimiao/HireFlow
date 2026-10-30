import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from hireflow.auth import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    create_access_token,
    current_principal,
    require_roles,
)
from hireflow.schemas import UserRole

SECRET = "test-only-secret-with-more-than-thirty-two-bytes"


@pytest.fixture(autouse=True)
def jwt_environment(monkeypatch):
    monkeypatch.setenv("HIREFLOW_JWT_SECRET", SECRET)
    monkeypatch.setenv("HIREFLOW_ACCESS_TOKEN_MINUTES", "30")


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def signed_token(**overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "tenant_id": str(uuid4()),
        "role": UserRole.RECRUITER.value,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": str(uuid4()),
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def assert_unauthorized(token: str) -> None:
    with pytest.raises(HTTPException) as raised:
        current_principal(bearer(token))
    assert raised.value.status_code == 401


def test_access_token_contains_required_security_claims():
    token = create_access_token(uuid4(), uuid4(), UserRole.OWNER)
    claims = jwt.decode(
        token, SECRET, algorithms=["HS256"], audience=JWT_AUDIENCE, issuer=JWT_ISSUER
    )
    assert {"sub", "tenant_id", "role", "iat", "nbf", "exp", "iss", "aud", "jti"} <= claims.keys()


def test_expired_token_is_rejected():
    assert_unauthorized(
        signed_token(
            iat=datetime.now(UTC) - timedelta(hours=2),
            nbf=datetime.now(UTC) - timedelta(hours=2),
            exp=datetime.now(UTC) - timedelta(hours=1),
        )
    )


def test_token_missing_expiry_is_rejected():
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "tenant_id": str(uuid4()),
            "role": UserRole.RECRUITER.value,
            "iat": now,
            "nbf": now,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "jti": str(uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    assert_unauthorized(token)


def test_tenant_claim_tampering_is_rejected():
    token = create_access_token(uuid4(), uuid4(), UserRole.RECRUITER)
    header, payload, signature = token.split(".")
    claims = jwt.api_jws.get_unverified_header(token)
    assert claims["alg"] == "HS256"
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["tenant_id"] = str(uuid4())
    changed = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()
    assert_unauthorized(f"{header}.{changed}.{signature}")


def test_role_downgrade_is_enforced():
    principal = current_principal(
        bearer(create_access_token(uuid4(), uuid4(), UserRole.INTERVIEWER))
    )
    owner_or_recruiter = require_roles(UserRole.OWNER, UserRole.RECRUITER)
    with pytest.raises(HTTPException) as raised:
        owner_or_recruiter(principal)
    assert raised.value.status_code == 403


def test_short_jwt_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("HIREFLOW_JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        create_access_token(uuid4(), uuid4(), UserRole.OWNER)
