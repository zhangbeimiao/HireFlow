"""Redis fixed-window protection for authentication and mutation endpoints."""

from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError

AUTH_LIMIT = 10
WRITE_LIMIT = 60
WINDOW_SECONDS = 60


def _limit_for(request: Request) -> int | None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    return AUTH_LIMIT if request.url.path.startswith("/v1/auth/") else WRITE_LIMIT


async def enforce_rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    limit = _limit_for(request)
    if limit is None:
        return await call_next(request)

    client: Redis = request.app.state.redis
    client_ip = request.client.host if request.client else "unknown"
    key = f"hireflow:rate-limit:{request.method}:{request.url.path}:{client_ip}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, WINDOW_SECONDS)
        ttl = max(await client.ttl(key), 0)
    except RedisError:
        # Redis protects availability, but PostgreSQL remains the consistency authority.
        # Fail open so a transient cache outage cannot block an interview cancellation.
        return await call_next(request)

    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(limit - count, 0)),
    }
    if count > limit:
        headers["Retry-After"] = str(ttl or WINDOW_SECONDS)
        return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"}, headers=headers)
    response = await call_next(request)
    response.headers.update(headers)
    return response
