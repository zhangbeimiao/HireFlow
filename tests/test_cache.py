from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from redis.exceptions import RedisError

from hireflow.cache import (
    cached_list,
    invalidate_cached_list,
    list_cache_key,
)
from hireflow.schemas import JobRead

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.ttls[key] = ex

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.ttls.pop(key, None)


class FailingRedis(FakeRedis):
    async def get(self, key: str) -> str | None:
        raise RedisError("redis unavailable")


def job(tenant_id: UUID, title: str = "Backend Engineer") -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        tenant_id=tenant_id,
        title=title,
        hiring_manager_id=None,
        status="open",
        created_at=datetime(2026, 10, 29, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_cache_miss_writes_ttl_then_hit_avoids_database() -> None:
    redis = FakeRedis()
    loads = 0

    async def loader():
        nonlocal loads
        loads += 1
        return [job(TENANT_A)]

    first = await cached_list(
        redis,
        resource="jobs",
        tenant_id=TENANT_A,
        schema=JobRead,
        loader=loader,
        ttl_seconds=73,
    )
    second = await cached_list(
        redis,
        resource="jobs",
        tenant_id=TENANT_A,
        schema=JobRead,
        loader=loader,
        ttl_seconds=73,
    )

    key = list_cache_key("jobs", TENANT_A)
    assert first.outcome == "MISS"
    assert second.outcome == "HIT"
    assert loads == 1
    assert redis.ttls[key] == 73
    assert second.items[0]["tenant_id"] == str(TENANT_A)


@pytest.mark.asyncio
async def test_invalidation_forces_next_read_to_reload() -> None:
    redis = FakeRedis()
    titles = iter(["Before", "After"])

    async def loader():
        return [job(TENANT_A, next(titles))]

    await cached_list(
        redis,
        resource="jobs",
        tenant_id=TENANT_A,
        schema=JobRead,
        loader=loader,
        ttl_seconds=60,
    )
    await invalidate_cached_list(redis, resource="jobs", tenant_id=TENANT_A)
    refreshed = await cached_list(
        redis,
        resource="jobs",
        tenant_id=TENANT_A,
        schema=JobRead,
        loader=loader,
        ttl_seconds=60,
    )

    assert refreshed.outcome == "MISS"
    assert refreshed.items[0]["title"] == "After"


@pytest.mark.asyncio
async def test_cache_keys_and_values_are_isolated_by_tenant() -> None:
    redis = FakeRedis()

    async def load_a():
        return [job(TENANT_A, "Tenant A")]

    async def load_b():
        return [job(TENANT_B, "Tenant B")]

    result_a = await cached_list(
        redis,
        resource="jobs",
        tenant_id=TENANT_A,
        schema=JobRead,
        loader=load_a,
        ttl_seconds=60,
    )
    result_b = await cached_list(
        redis,
        resource="jobs",
        tenant_id=TENANT_B,
        schema=JobRead,
        loader=load_b,
        ttl_seconds=60,
    )

    assert list_cache_key("jobs", TENANT_A) != list_cache_key("jobs", TENANT_B)
    assert result_a.items[0]["title"] == "Tenant A"
    assert result_b.items[0]["title"] == "Tenant B"
    assert len(redis.values) == 2


@pytest.mark.asyncio
async def test_bypass_and_redis_failure_fall_back_to_database() -> None:
    async def loader():
        return [job(TENANT_A)]

    bypassed = await cached_list(
        FakeRedis(),
        resource="jobs",
        tenant_id=TENANT_A,
        schema=JobRead,
        loader=loader,
        ttl_seconds=60,
        bypass=True,
    )
    degraded = await cached_list(
        FailingRedis(),
        resource="jobs",
        tenant_id=TENANT_A,
        schema=JobRead,
        loader=loader,
        ttl_seconds=60,
    )

    assert bypassed.outcome == "BYPASS"
    assert degraded.outcome == "ERROR"
