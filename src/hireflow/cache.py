"""Tenant-scoped Cache Aside primitives for read-heavy recruiting lists."""

import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from hireflow.observability import (
    CACHE_DB_LOAD_DURATION,
    CACHE_HIT_RATIO,
    CACHE_INVALIDATIONS,
    CACHE_REQUESTS,
)

CacheLoader = Callable[[], Awaitable[list[Any]]]


@dataclass(frozen=True)
class CachedList:
    items: list[dict[str, Any]]
    outcome: str


_lookup_totals: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
_lookup_lock = Lock()


def list_cache_key(resource: str, tenant_id: UUID) -> str:
    """The tenant id is part of every key, preventing cross-tenant cache sharing."""

    # Bump the namespace when a cached response schema changes. Job/Candidate list items gained
    # created_at for cursor pagination, so v2 cannot read stale v1 payloads after a deployment.
    return f"hireflow:cache:v2:tenant:{tenant_id}:{resource}:list"


def _record_lookup(resource: str, outcome: str) -> None:
    CACHE_REQUESTS.labels(resource=resource, outcome=outcome).inc()
    if outcome not in {"hit", "miss"}:
        return
    with _lookup_lock:
        counts = _lookup_totals[resource]
        counts[0] += int(outcome == "hit")
        counts[1] += 1
        CACHE_HIT_RATIO.labels(resource=resource).set(counts[0] / counts[1])


async def _load_from_database(resource: str, loader: CacheLoader) -> list[Any]:
    started_at = perf_counter()
    try:
        return await loader()
    finally:
        CACHE_DB_LOAD_DURATION.labels(resource=resource).observe(perf_counter() - started_at)


def _serialize(rows: list[Any], schema: type[BaseModel]) -> list[dict[str, Any]]:
    return [
        schema.model_validate(row, from_attributes=True).model_dump(mode="json") for row in rows
    ]


async def cached_list(
    redis: Redis,
    *,
    resource: str,
    tenant_id: UUID,
    schema: type[BaseModel],
    loader: CacheLoader,
    ttl_seconds: int,
    bypass: bool = False,
) -> CachedList:
    """Return a cached tenant list, failing open to PostgreSQL when Redis is unavailable."""

    if bypass:
        _record_lookup(resource, "bypass")
        return CachedList(_serialize(await _load_from_database(resource, loader), schema), "BYPASS")

    key = list_cache_key(resource, tenant_id)
    try:
        cached = await redis.get(key)
        if cached is not None:
            try:
                items = json.loads(cached)
                if not isinstance(items, list):
                    raise TypeError("cached list payload is not a list")
            except (TypeError, ValueError, json.JSONDecodeError):
                _record_lookup(resource, "corrupt")
                await redis.delete(key)
            else:
                _record_lookup(resource, "hit")
                return CachedList(items, "HIT")
    except RedisError:
        _record_lookup(resource, "error")
        return CachedList(_serialize(await _load_from_database(resource, loader), schema), "ERROR")

    _record_lookup(resource, "miss")
    items = _serialize(await _load_from_database(resource, loader), schema)
    try:
        await redis.set(key, json.dumps(items, separators=(",", ":")), ex=ttl_seconds)
    except RedisError:
        _record_lookup(resource, "write_error")
    return CachedList(items, "MISS")


async def invalidate_cached_list(redis: Redis, *, resource: str, tenant_id: UUID) -> None:
    """Delete only the mutated tenant's list cache after a successful database commit."""

    try:
        await redis.delete(list_cache_key(resource, tenant_id))
    except RedisError:
        CACHE_INVALIDATIONS.labels(resource=resource, outcome="error").inc()
    else:
        CACHE_INVALIDATIONS.labels(resource=resource, outcome="success").inc()
