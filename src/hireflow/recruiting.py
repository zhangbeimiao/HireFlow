"""Tenant-scoped recruiting workflow commands."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.auth import hash_password, verify_password
from hireflow.db import set_tenant_context
from hireflow.models import Application, Candidate, Job, Tenant, User
from hireflow.schemas import (
    ApplicationCreate,
    CandidateCreate,
    CandidateUpdate,
    JobCreate,
    JobUpdate,
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
    return tenant


async def bootstrap_tenant(session: AsyncSession, payload: TenantBootstrap) -> User:
    # Generate the UUID before INSERT so PostgreSQL RLS also permits the INSERT ... RETURNING row.
    tenant = Tenant(id=uuid4(), name=payload.tenant_name)
    await set_tenant_context(session, tenant.id)
    session.add(tenant)
    await session.flush()
    values = payload.owner.model_dump(exclude={"password"})
    owner = User(tenant_id=tenant.id, password_hash=hash_password(payload.owner.password), **values)
    session.add(owner)
    await _commit(session)
    return owner


async def authenticate(session: AsyncSession, payload: LoginRequest) -> User | None:
    # Login contains a tenant id, which is enough to establish the least-privilege RLS scope
    # before looking up the user's password hash.
    await set_tenant_context(session, payload.tenant_id)
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
    return user


async def list_users(session: AsyncSession, tenant_id: UUID) -> list[User]:
    rows = await session.scalars(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at, User.display_name)
    )
    return list(rows)


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
    return job


async def list_jobs(session: AsyncSession, tenant_id: UUID) -> list[Job]:
    rows = await session.scalars(
        select(Job).where(Job.tenant_id == tenant_id).order_by(Job.created_at.desc())
    )
    return list(rows)


async def update_job(
    session: AsyncSession, tenant_id: UUID, job_id: UUID, payload: JobUpdate
) -> Job:
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id))
    if job is None:
        raise InvalidReference("job not found")
    values = payload.model_dump(exclude_unset=True)
    manager_id = values.get("hiring_manager_id")
    if manager_id is not None:
        manager = await session.scalar(
            select(User).where(User.id == manager_id, User.tenant_id == tenant_id)
        )
        if manager is None:
            raise InvalidReference("hiring_manager_id does not belong to this tenant")
    for field, value in values.items():
        setattr(job, field, value)
    await _commit(session)
    return job


async def create_candidate(session: AsyncSession, tenant_id: UUID, payload: CandidateCreate) -> Candidate:
    await require_tenant(session, tenant_id)
    candidate = Candidate(tenant_id=tenant_id, **payload.model_dump())
    session.add(candidate)
    await _commit(session)
    return candidate


async def list_candidates(session: AsyncSession, tenant_id: UUID) -> list[Candidate]:
    rows = await session.scalars(
        select(Candidate).where(Candidate.tenant_id == tenant_id).order_by(Candidate.created_at.desc())
    )
    return list(rows)


async def update_candidate(
    session: AsyncSession, tenant_id: UUID, candidate_id: UUID, payload: CandidateUpdate
) -> Candidate:
    candidate = await session.scalar(
        select(Candidate).where(Candidate.id == candidate_id, Candidate.tenant_id == tenant_id)
    )
    if candidate is None:
        raise InvalidReference("candidate not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(candidate, field, value)
    await _commit(session)
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
    return application
