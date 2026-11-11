from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.exceptions import RedisError
from starlette.requests import Request
from starlette.responses import Response

from hireflow.auth import Principal
from hireflow.rate_limit import enforce_rate_limit
from hireflow.schemas import UserRole


class FakeRedis:
    def __init__(self, result: list[int] | None = None, error: Exception | None = None):
        self.result = result or [1, 9, 0]
        self.error = error
        self.calls: list[tuple] = []

    async def eval(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


def request(
    client: FakeRedis,
    *,
    method: str = "POST",
    path: str = "/v1/auth/tokens",
    headers: list[tuple[bytes, bytes]] | None = None,
    client_ip: str = "127.0.0.1",
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "client": (client_ip, 50000),
        "app": SimpleNamespace(state=SimpleNamespace(redis=client)),
    }
    return Request(scope)


async def downstream(_: Request) -> Response:
    return Response(status_code=204)


@pytest.mark.asyncio
async def test_token_bucket_exposes_remaining_capacity() -> None:
    redis = FakeRedis([1, 7, 0])
    response = await enforce_rate_limit(request(redis), downstream)

    assert response.status_code == 204
    assert response.headers["X-RateLimit-Remaining"] == "7"
    assert response.headers["X-RateLimit-Policy"] == "token-bucket;w=60"
    assert len(redis.calls) == 1


@pytest.mark.asyncio
async def test_token_bucket_returns_retry_after_when_empty() -> None:
    response = await enforce_rate_limit(request(FakeRedis([0, 0, 6100])), downstream)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"


@pytest.mark.asyncio
async def test_token_bucket_fails_open_when_redis_is_unavailable() -> None:
    response = await enforce_rate_limit(request(FakeRedis(error=RedisError("offline"))), downstream)

    assert response.status_code == 204
    assert "X-RateLimit-Limit" not in response.headers


@pytest.mark.asyncio
async def test_candidate_list_is_read_rate_limited() -> None:
    redis = FakeRedis()
    response = await enforce_rate_limit(
        request(redis, method="GET", path="/v1/candidates"), downstream
    )

    assert response.headers["X-RateLimit-Limit"] == "120"
    assert len(redis.calls) == 1


@pytest.mark.asyncio
async def test_unprotected_get_bypasses_redis() -> None:
    redis = FakeRedis()
    await enforce_rate_limit(request(redis, method="GET", path="/health/live"), downstream)

    assert redis.calls == []


@pytest.mark.asyncio
async def test_forwarded_ip_is_ignored_for_untrusted_peer(monkeypatch) -> None:
    monkeypatch.setenv("HIREFLOW_TRUSTED_PROXIES", "10.0.0.0/8")
    redis = FakeRedis()
    await enforce_rate_limit(
        request(
            redis,
            headers=[(b"x-forwarded-for", b"203.0.113.9")],
            client_ip="192.0.2.10",
        ),
        downstream,
    )

    assert ":ip:192.0.2.10:" in redis.calls[0][2]


@pytest.mark.asyncio
async def test_forwarded_ip_is_used_only_for_trusted_peer(monkeypatch) -> None:
    monkeypatch.setenv("HIREFLOW_TRUSTED_PROXIES", "127.0.0.1/32")
    redis = FakeRedis()
    await enforce_rate_limit(
        request(redis, headers=[(b"x-forwarded-for", b"203.0.113.9")]), downstream
    )

    assert ":ip:203.0.113.9:" in redis.calls[0][2]


@pytest.mark.asyncio
async def test_verified_token_uses_tenant_and_user_buckets(monkeypatch) -> None:
    principal = Principal(uuid4(), uuid4(), UserRole.RECRUITER)
    monkeypatch.setattr("hireflow.rate_limit.decode_access_token", lambda _: principal)
    redis = FakeRedis()
    await enforce_rate_limit(
        request(redis, headers=[(b"authorization", b"Bearer signed-token")]), downstream
    )

    call = redis.calls[0]
    assert call[1] == 2
    assert f":tenant:{principal.tenant_id}:" in call[2]
    assert f":user:{principal.user_id}:" in call[3]
