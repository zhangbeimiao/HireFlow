import os
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv(
    "HIREFLOW_DATABASE_URL",
    "postgresql+asyncpg://hireflow:hireflow_dev_only@localhost:5432/hireflow",
)
engine = create_async_engine(DATABASE_URL, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _clear_tenant_context(session: AsyncSession) -> None:
    """Rollback request work and persistently clear a custom GUC before pooling the connection."""
    if session.in_transaction():
        await session.rollback()
    await session.execute(text("SELECT set_config('app.tenant_id', '', false)"))
    await session.commit()


async def get_session():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            # A pooled connection must never carry a tenant variable into the next request.
            await _clear_tenant_context(session)


async def set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None:
    """Bind the PostgreSQL RLS policy to the tenant from a verified JWT claim."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
        {"tenant_id": str(tenant_id)},
    )


async def get_tenant_session(tenant_id: UUID):
    """Yield an API session whose database connection is constrained by RLS."""
    async with SessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
        finally:
            await _clear_tenant_context(session)
