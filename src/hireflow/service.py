from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.db import set_tenant_context
from hireflow.domain import ACTIVE_STATUSES, InterviewStatus, can_transition, overlaps
from hireflow.models import Candidate, Interview, InterviewAudit, Job, OutboxEvent, User
from hireflow.recruiting import InvalidReference
from hireflow.schemas import DashboardInterviewRead, InterviewCreate, InterviewStatusUpdate


class SchedulingConflict(Exception):
    pass


class InvalidTransition(Exception):
    pass


class StaleVersion(Exception):
    pass


async def create_interview(
    session: AsyncSession, tenant_id: UUID, payload: InterviewCreate
) -> Interview:
    """Create an appointment after an MVP conflict probe.

    PostgreSQL's exclusion constraint is the production concurrency authority. This query
    makes local SQLite behaviour understandable and returns a helpful 409 before the insert.
    """
    candidate = await session.scalar(
        select(Candidate).where(Candidate.id == payload.candidate_id, Candidate.tenant_id == tenant_id)
    )
    interviewer = await session.scalar(
        select(User).where(User.id == payload.interviewer_id, User.tenant_id == tenant_id)
    )
    job = await session.scalar(select(Job).where(Job.id == payload.job_id, Job.tenant_id == tenant_id))
    if candidate is None or interviewer is None or job is None:
        raise InvalidReference("candidate_id, interviewer_id, and job_id must all belong to this tenant")

    existing = await session.scalars(
        select(Interview).where(
            and_(
                Interview.tenant_id == tenant_id,
                Interview.interviewer_id == payload.interviewer_id,
                Interview.status.in_([status.value for status in ACTIVE_STATUSES]),
            )
        )
    )
    for current in existing:
        if overlaps(payload.starts_at, payload.ends_at, current.starts_at, current.ends_at):
            raise SchedulingConflict("interviewer already has an overlapping active interview")

    interview = Interview(
        tenant_id=tenant_id,
        candidate_id=payload.candidate_id,
        interviewer_id=payload.interviewer_id,
        job_id=payload.job_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        meeting_url=payload.meeting_url,
        status=InterviewStatus.SCHEDULED.value,
    )
    session.add(interview)
    try:
        # A PostgreSQL exclusion violation is raised by flush (the INSERT), not necessarily by
        # commit. Keep the entire write unit inside this boundary so every racing request gets
        # the public 409 contract instead of leaking an IntegrityError as HTTP 500.
        await session.flush()
        session.add(
            InterviewAudit(
                interview_id=interview.id,
                from_status=None,
                to_status=InterviewStatus.SCHEDULED.value,
                reason="interview scheduled",
            )
        )
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                aggregate_id=interview.id,
                event_type="interview.scheduled",
                payload={
                    "interview_id": str(interview.id),
                    "candidate_id": str(interview.candidate_id),
                    "interviewer_id": str(interview.interviewer_id),
                    "starts_at": interview.starts_at.isoformat(),
                    "ends_at": interview.ends_at.isoformat(),
                },
            )
        )
        await session.commit()
    except IntegrityError as error:
        # The exclusion constraint is the final authority when two application instances
        # pass the read-time probe concurrently. Keep its database error behind one 409 contract.
        await session.rollback()
        raise SchedulingConflict("interviewer already has an overlapping active interview") from error
    return interview


async def transition_interview(
    session: AsyncSession, tenant_id: UUID, interview_id: UUID, payload: InterviewStatusUpdate
) -> Interview:
    interview = await session.scalar(
        select(Interview).where(Interview.id == interview_id, Interview.tenant_id == tenant_id)
    )
    if interview is None:
        raise LookupError("interview not found")

    current = InterviewStatus(interview.status)
    if not can_transition(current, payload.status):
        raise InvalidTransition(f"cannot change {current.value} to {payload.status.value}")

    result = await session.execute(
        update(Interview)
        .where(
            Interview.id == interview_id,
            Interview.tenant_id == tenant_id,
            Interview.version == payload.version,
        )
        .values(status=payload.status.value, version=Interview.version + 1)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise StaleVersion("interview was changed by another request; read it again and retry")

    session.add(
        InterviewAudit(
            interview_id=interview_id,
            from_status=current.value,
            to_status=payload.status.value,
            reason=payload.reason,
        )
    )
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            aggregate_id=interview_id,
            event_type="interview.status_changed",
            payload={
                "interview_id": str(interview_id),
                "from_status": current.value,
                "to_status": payload.status.value,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    # A committed session may check out another pooled connection for this read.
    await set_tenant_context(session, tenant_id)
    return await session.scalar(select(Interview).where(Interview.id == interview_id))


async def list_audits(session: AsyncSession, tenant_id: UUID, interview_id: UUID) -> list[InterviewAudit]:
    # Join through interviews ensures one tenant cannot enumerate another tenant's audit records.
    rows = await session.scalars(
        select(InterviewAudit)
        .join(Interview, Interview.id == InterviewAudit.interview_id)
        .where(InterviewAudit.interview_id == interview_id, Interview.tenant_id == tenant_id)
        .order_by(InterviewAudit.created_at)
    )
    return list(rows)


async def list_interviews(
    session: AsyncSession, tenant_id: UUID, limit: int = 100
) -> list[DashboardInterviewRead]:
    rows = await session.execute(
        select(Interview, Candidate.full_name, User.display_name, Job.title)
        .join(Candidate, Candidate.id == Interview.candidate_id)
        .join(User, User.id == Interview.interviewer_id)
        .join(Job, Job.id == Interview.job_id)
        .where(Interview.tenant_id == tenant_id)
        .order_by(Interview.starts_at.desc())
        .limit(limit)
    )
    return [
        DashboardInterviewRead(
            id=interview.id,
            candidate_name=candidate_name,
            interviewer_name=interviewer_name,
            job_title=job_title,
            starts_at=interview.starts_at,
            ends_at=interview.ends_at,
            status=interview.status,
        )
        for interview, candidate_name, interviewer_name, job_title in rows
    ]
