from datetime import timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.domain import ACTIVE_STATUSES
from hireflow.models import (
    Candidate,
    CandidateAvailability,
    Interview,
    InterviewerAvailability,
    User,
)
from hireflow.recruiting import InvalidReference, require_tenant
from hireflow.scheduling import Availability, earliest_common_slot, subtract_busy_slots
from hireflow.schemas import AvailabilityCreate


async def add_candidate_availability(
    session: AsyncSession, tenant_id: UUID, candidate_id: UUID, payload: AvailabilityCreate
) -> CandidateAvailability:
    await require_tenant(session, tenant_id)
    candidate = await session.scalar(
        select(Candidate).where(Candidate.id == candidate_id, Candidate.tenant_id == tenant_id)
    )
    if candidate is None:
        raise InvalidReference("candidate_id does not belong to this tenant")
    availability = CandidateAvailability(tenant_id=tenant_id, candidate_id=candidate_id, **payload.model_dump())
    session.add(availability)
    await session.commit()
    await session.refresh(availability)
    return availability


async def add_interviewer_availability(
    session: AsyncSession, tenant_id: UUID, interviewer_id: UUID, payload: AvailabilityCreate
) -> InterviewerAvailability:
    await require_tenant(session, tenant_id)
    interviewer = await session.scalar(
        select(User).where(User.id == interviewer_id, User.tenant_id == tenant_id)
    )
    if interviewer is None:
        raise InvalidReference("interviewer_id does not belong to this tenant")
    availability = InterviewerAvailability(
        tenant_id=tenant_id, interviewer_id=interviewer_id, **payload.model_dump()
    )
    session.add(availability)
    await session.commit()
    await session.refresh(availability)
    return availability


async def suggest_earliest_slot(
    session: AsyncSession, tenant_id: UUID, candidate_id: UUID, interviewer_id: UUID, duration_minutes: int
) -> Availability | None:
    await require_tenant(session, tenant_id)
    candidate = await session.scalar(
        select(Candidate).where(Candidate.id == candidate_id, Candidate.tenant_id == tenant_id)
    )
    interviewer = await session.scalar(
        select(User).where(User.id == interviewer_id, User.tenant_id == tenant_id)
    )
    if candidate is None or interviewer is None:
        raise InvalidReference("candidate_id and interviewer_id must both belong to this tenant")
    candidate_slots = list(await session.scalars(
        select(CandidateAvailability).where(
            CandidateAvailability.tenant_id == tenant_id,
            CandidateAvailability.candidate_id == candidate_id,
        )
    ))
    interviewer_slots = list(await session.scalars(
        select(InterviewerAvailability).where(
            InterviewerAvailability.tenant_id == tenant_id,
            InterviewerAvailability.interviewer_id == interviewer_id,
        )
    ))
    booked = list(await session.scalars(
        select(Interview).where(
            Interview.tenant_id == tenant_id,
            Interview.status.in_([status.value for status in ACTIVE_STATUSES]),
            or_(Interview.candidate_id == candidate_id, Interview.interviewer_id == interviewer_id),
        )
    ))
    candidate_busy = [Availability(item.starts_at, item.ends_at) for item in booked if item.candidate_id == candidate_id]
    interviewer_busy = [
        Availability(item.starts_at, item.ends_at)
        for item in booked
        if item.interviewer_id == interviewer_id
    ]
    return earliest_common_slot(
        subtract_busy_slots(
            [Availability(slot.starts_at, slot.ends_at) for slot in candidate_slots], candidate_busy
        ),
        subtract_busy_slots(
            [Availability(slot.starts_at, slot.ends_at) for slot in interviewer_slots], interviewer_busy
        ),
        timedelta(minutes=duration_minutes),
    )
