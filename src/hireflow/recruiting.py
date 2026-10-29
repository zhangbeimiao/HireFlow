"""Tenant-scoped recruiting workflow commands."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.auth import hash_password, verify_password
from hireflow.models import Application, Candidate, Job, Tenant, User
from hireflow.schemas import (
    ApplicationCreate,
    CandidateCreate,
    JobCreate,
    LoginRequest,
    TenantBootstrap,
    TenantCreate,
    UserCreate,
)


class TenantNotFound(Exception):
    pass


class InvalidReference(Exception):
    pass


class DuplicateResource(Exception):
    pass


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise DuplicateResource("a resource with the same unique key already exists") from error


async def require_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFound("tenant not found")
    return tenant


async def create_tenant(session: AsyncSession, payload: TenantCreate) -> Tenant:
    tenant = Tenant(name=payload.name)
    session.add(tenant)
    await _commit(session)
    await session.refresh(tenant)
    return tenant


async def bootstrap_tenant(session: AsyncSession, payload: TenantBootstrap) -> User:
    tenant = Tenant(name=payload.tenant_name)
    session.add(tenant)
    await session.flush()
    values = payload.owner.model_dump(exclude={"password"})
    owner = User(tenant_id=tenant.id, password_hash=hash_password(payload.owner.password), **values)
    session.add(owner)
    await _commit(session)
    await session.refresh(owner)
    return owner


async def authenticate(session: AsyncSession, payload: LoginRequest) -> User | None:
    user = await session.scalar(
        select(User).where(User.tenant_id == payload.tenant_id, User.email == payload.email)
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        return None
    return user


async def create_user(session: AsyncSession, tenant_id: UUID, payload: UserCreate) -> User:
    await require_tenant(session, tenant_id)
    values = payload.model_dump(exclude={"password"})
    user = User(tenant_id=tenant_id, password_hash=hash_password(payload.password), **values)
    session.add(user)
    await _commit(session)
    await session.refresh(user)
    return user


async def create_job(session: AsyncSession, tenant_id: UUID, payload: JobCreate) -> Job:
    await require_tenant(session, tenant_id)
    if payload.hiring_manager_id is not None:
        manager = await session.scalar(
            select(User).where(User.id == payload.hiring_manager_id, User.tenant_id == tenant_id)
        )
        if manager is None:
            raise InvalidReference("hiring_manager_id does not belong to this tenant")
    job = Job(tenant_id=tenant_id, **payload.model_dump())
    session.add(job)
    await _commit(session)
    await session.refresh(job)
    return job


async def create_candidate(session: AsyncSession, tenant_id: UUID, payload: CandidateCreate) -> Candidate:
    await require_tenant(session, tenant_id)
    candidate = Candidate(tenant_id=tenant_id, **payload.model_dump())
    session.add(candidate)
    await _commit(session)
    await session.refresh(candidate)
    return candidate


async def create_application(
    session: AsyncSession, tenant_id: UUID, payload: ApplicationCreate
) -> Application:
    await require_tenant(session, tenant_id)
    job = await session.scalar(select(Job).where(Job.id == payload.job_id, Job.tenant_id == tenant_id))
    candidate = await session.scalar(
        select(Candidate).where(Candidate.id == payload.candidate_id, Candidate.tenant_id == tenant_id)
    )
    if job is None or candidate is None:
        raise InvalidReference("job_id and candidate_id must both belong to this tenant")
    application = Application(tenant_id=tenant_id, **payload.model_dump())
    session.add(application)
    await _commit(session)
    await session.refresh(application)
    return application
