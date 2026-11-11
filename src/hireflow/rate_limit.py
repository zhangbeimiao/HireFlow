"""Atomic Redis token-bucket protection for authentication and mutations."""

import math
import os
import time
from collections.abc import Awaitable, Callable
from ipaddress import ip_address, ip_network

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError

from hireflow.auth import Principal, decode_access_token

AUTH_LIMIT = 10
WRITE_LIMIT = 60
READ_LIMIT = 120
TENANT_MULTIPLIER = 10
WINDOW_SECONDS = 60

PROTECTED_LIST_PATHS = {
    "/v1/jobs",
    "/v1/candidates",
    "/v1/applications",
    "/v1/interviews",
    "/v1/notifications",
}

TOKEN_BUCKET_SCRIPT = """
local now = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local buckets = {}
local allowed = 1
local remaining = nil
local retry_ms = 0
for index, key in ipairs(KEYS) do
  local capacity = tonumber(ARGV[2 * index + 1])
  local refill_per_ms = tonumber(ARGV[2 * index + 2])
  local current = redis.call('HMGET', key, 'tokens', 'updated_at')
  local tokens = tonumber(current[1])
  local updated_at = tonumber(current[2])
  if tokens == nil then tokens = capacity end
  if updated_at == nil then updated_at = now end
  tokens = math.min(capacity, tokens + math.max(0, now - updated_at) * refill_per_ms)
  buckets[index] = {key, tokens, refill_per_ms}
  if tokens < 1 then
    allowed = 0
    retry_ms = math.max(retry_ms, math.ceil((1 - tokens) / refill_per_ms))
  end
end
for index, bucket in ipairs(buckets) do
  local tokens = bucket[2]
  if allowed == 1 then tokens = tokens - 1 end
  redis.call('HSET', bucket[1], 'tokens', tokens, 'updated_at', now)
  redis.call('PEXPIRE', bucket[1], ttl_ms)
  local whole_tokens = math.floor(tokens)
  if remaining == nil or whole_tokens < remaining then remaining = whole_tokens end
end
return {allowed, remaining, retry_ms}
"""


def _limit_for(request: Request) -> int | None:
    if request.method in {"GET", "HEAD"}:
        return READ_LIMIT if request.url.path in PROTECTED_LIST_PATHS else None
    if request.method == "OPTIONS":
        return None
    return AUTH_LIMIT if request.url.path.startswith("/v1/auth/") else WRITE_LIMIT


def _trusted_proxy_networks():
    configured = os.getenv("HIREFLOW_TRUSTED_PROXIES", "")
    return tuple(
        ip_network(value.strip(), strict=False) for value in configured.split(",") if value.strip()
    )


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        trusted_peer = any(ip_address(peer) in network for network in _trusted_proxy_networks())
    except ValueError:
        trusted_peer = False
    if trusted_peer:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        try:
            return str(ip_address(forwarded))
        except ValueError:
            pass
    return peer


def _verified_principal(request: Request) -> Principal | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        return decode_access_token(token)
    except (jwt.PyJWTError, KeyError, RuntimeError, ValueError):
        return None


def _bucket_configuration(request: Request, limit: int) -> tuple[list[str], list[int]]:
    path = request.url.path
    principal = _verified_principal(request)
    if principal is None:
        return [f"hireflow:rate-limit:ip:{_client_ip(request)}:{request.method}:{path}"], [limit]
    tenant_prefix = f"hireflow:rate-limit:tenant:{principal.tenant_id}"
    return [
        f"{tenant_prefix}:{request.method}:{path}",
        f"{tenant_prefix}:user:{principal.user_id}:{request.method}:{path}",
    ], [limit * TENANT_MULTIPLIER, limit]


async def enforce_rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    limit = _limit_for(request)
    if limit is None:
        return await call_next(request)

    client: Redis = request.app.state.redis
    keys, capacities = _bucket_configuration(request, limit)
    try:
        now_ms = int(time.time() * 1000)
        arguments: list[int | float] = [now_ms, WINDOW_SECONDS * 2000]
        for capacity in capacities:
            arguments.extend([capacity, capacity / (WINDOW_SECONDS * 1000)])
        allowed, remaining, retry_ms = await client.eval(
            TOKEN_BUCKET_SCRIPT,
            len(keys),
            *keys,
            *arguments,
        )
    except RedisError:
        # Redis protects availability, but PostgreSQL remains the consistency authority.
        # Fail open so a transient cache outage cannot block an interview cancellation.
        return await call_next(request)

    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(int(remaining), 0)),
        "X-RateLimit-Policy": f"token-bucket;w={WINDOW_SECONDS}",
    }
    if not allowed:
        headers["Retry-After"] = str(max(1, math.ceil(int(retry_ms) / 1000)))
        return JSONResponse(
            status_code=429, content={"detail": "rate limit exceeded"}, headers=headers
        )
    response = await call_next(request)
    response.headers.update(headers)
    return response
