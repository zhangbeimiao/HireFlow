from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from hireflow.db import SessionLocal


async def postgres_ready() -> bool:
    """Return whether the API can execute a minimal query with its runtime account."""

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        return False
    return True


async def redis_ready(redis: Redis) -> bool:
    """Return whether the optional cache dependency responds without exposing error details."""

    try:
        return bool(await redis.ping())
    except (RedisError, OSError, TimeoutError):
        return False


def readiness_response(postgres_ok: bool, redis_ok: bool) -> tuple[int, dict[str, Any]]:
    """Build the public readiness contract from dependency states."""

    if not postgres_ok:
        status_code = 503
        overall_status = "not_ready"
    elif not redis_ok:
        status_code = 200
        overall_status = "degraded"
    else:
        status_code = 200
        overall_status = "ready"

    return status_code, {
        "status": overall_status,
        "dependencies": {
            "postgres": "ok" if postgres_ok else "unavailable",
            "redis": "ok" if redis_ok else "degraded",
        },
    }
