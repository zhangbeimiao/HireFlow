import os
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from hireflow.observability import DB_POOL_CONNECTIONS

DATABASE_URL = os.getenv("HIREFLOW_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("HIREFLOW_DATABASE_URL must be configured")
engine = create_async_engine(DATABASE_URL, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_pool = engine.sync_engine.pool
DB_POOL_CONNECTIONS.labels(state="size").set_function(_pool.size)
DB_POOL_CONNECTIONS.labels(state="checked_in").set_function(_pool.checkedin)
DB_POOL_CONNECTIONS.labels(state="checked_out").set_function(_pool.checkedout)
DB_POOL_CONNECTIONS.labels(state="overflow").set_function(_pool.overflow)


TENANT_CONTEXT_KEY = "hireflow_tenant_id"


@event.listens_for(Session, "after_begin")
def _apply_transaction_tenant_context(session, _transaction, connection) -> None:
    """Apply the verified tenant to every transaction opened by this ORM session."""

    tenant_id = session.info.get(TENANT_CONTEXT_KEY)
    if tenant_id is not None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )


async def _clear_tenant_context(session: AsyncSession) -> None:
    """Rollback request work; transaction-local GUC state then disappears automatically."""
    if session.in_transaction():
        await session.rollback()
    session.info.pop(TENANT_CONTEXT_KEY, None)


async def get_session():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            # A pooled connection must never carry a tenant variable into the next request.
            await _clear_tenant_context(session)


async def set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None:
    """Bind RLS to the verified tenant for the current and all later transactions."""

    session.info[TENANT_CONTEXT_KEY] = tenant_id
    if session.in_transaction():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
    else:
        # Starting a transaction invokes the after_begin hook above. This also makes the
        # context effective before the caller's first business query.
        await session.execute(text("SELECT 1"))


async def get_tenant_session(tenant_id: UUID):
    """Yield an API session whose database connection is constrained by RLS."""
    async with tenant_session_scope(tenant_id) as session:
        yield session


@asynccontextmanager
async def tenant_session_scope(tenant_id: UUID):
    """Open an RLS-scoped session only when a cache miss actually needs PostgreSQL."""

    async with SessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
        finally:
            await _clear_tenant_context(session)
