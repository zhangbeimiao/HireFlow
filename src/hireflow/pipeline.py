"""Auditable applicant-tracking workflow commands.

This module keeps recruitment state changes transactional: the business row,
audit record and Outbox event are committed together.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.db import set_tenant_context
from hireflow.domain import (
    ApplicationStage,
    OfferStatus,
    can_transition_application,
    can_transition_offer,
)
from hireflow.models import (
    Application,
    ApplicationAudit,
    Interview,
    InterviewRound,
    InterviewScorecard,
    Job,
    Offer,
    OfferAudit,
    OutboxEvent,
)
from hireflow.pagination import PageCursor
from hireflow.recruiting import DuplicateResource, InvalidReference
from hireflow.schemas import (
    ApplicationStageUpdate,
    InterviewRoundCreate,
    OfferCreate,
    OfferStatusUpdate,
    ScorecardUpsert,
)


class PipelineInvalidTransition(Exception):
    pass


class PipelineStaleVersion(Exception):
    pass


class ScorecardLocked(Exception):
    pass


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise DuplicateResource("a resource with the same unique key already exists") from error


async def list_applications(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    limit: int,
    cursor: PageCursor | None = None,
) -> list[Application]:
    query = select(Application).where(Application.tenant_id == tenant_id)
    if cursor is not None:
        query = query.where(
            tuple_(Application.created_at, Application.id) < (cursor.created_at, cursor.item_id)
        )
    rows = await session.scalars(
        query.order_by(Application.created_at.desc(), Application.id.desc()).limit(limit + 1)
    )
    return list(rows)


async def transition_application(
    session: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    application_id: UUID,
    payload: ApplicationStageUpdate,
) -> Application:
    application = await session.scalar(
        select(Application).where(
            Application.id == application_id, Application.tenant_id == tenant_id
        )
    )
    if application is None:
        raise InvalidReference("application not found")

    current = ApplicationStage(application.stage)
    if not can_transition_application(current, payload.stage):
        raise PipelineInvalidTransition(
            f"cannot change application from {current.value} to {payload.stage.value}"
        )

    result = await session.execute(
        update(Application)
        .where(
            Application.id == application_id,
            Application.tenant_id == tenant_id,
            Application.version == payload.version,
        )
        .values(
            stage=payload.stage.value,
            version=Application.version + 1,
            updated_at=datetime.now(UTC),
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise PipelineStaleVersion(
            "application was changed by another request; read it again and retry"
        )

    session.add(
        ApplicationAudit(
            tenant_id=tenant_id,
            application_id=application_id,
            from_stage=current.value,
            to_stage=payload.stage.value,
            reason=payload.reason,
            actor_id=actor_id,
        )
    )
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            aggregate_id=application_id,
            event_type="application.stage_changed",
            payload={
                "application_id": str(application_id),
                "from_stage": current.value,
                "to_stage": payload.stage.value,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    await set_tenant_context(session, tenant_id)
    return await session.scalar(select(Application).where(Application.id == application_id))


async def list_application_audits(
    session: AsyncSession, tenant_id: UUID, application_id: UUID
) -> list[ApplicationAudit]:
    rows = await session.scalars(
        select(ApplicationAudit)
        .where(
            ApplicationAudit.tenant_id == tenant_id,
            ApplicationAudit.application_id == application_id,
        )
        .order_by(ApplicationAudit.created_at, ApplicationAudit.id)
    )
    return list(rows)


async def create_interview_round(
    session: AsyncSession,
    tenant_id: UUID,
    job_id: UUID,
    payload: InterviewRoundCreate,
) -> InterviewRound:
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id))
    if job is None:
        raise InvalidReference("job not found")
    round_ = InterviewRound(id=uuid4(), tenant_id=tenant_id, job_id=job_id, **payload.model_dump())
    session.add(round_)
    await _commit(session)
    return round_


async def list_interview_rounds(
    session: AsyncSession, tenant_id: UUID, job_id: UUID
) -> list[InterviewRound]:
    rows = await session.scalars(
        select(InterviewRound)
        .where(InterviewRound.tenant_id == tenant_id, InterviewRound.job_id == job_id)
        .order_by(InterviewRound.sequence)
    )
    return list(rows)


async def upsert_scorecard(
    session: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    interview_id: UUID,
    payload: ScorecardUpsert,
) -> InterviewScorecard:
    interview = await session.scalar(
        select(Interview).where(Interview.id == interview_id, Interview.tenant_id == tenant_id)
    )
    if interview is None:
        raise InvalidReference("interview not found")
    if interview.interviewer_id != actor_id:
        raise InvalidReference("only the assigned interviewer can edit this scorecard")

    scorecard = await session.scalar(
        select(InterviewScorecard).where(
            InterviewScorecard.tenant_id == tenant_id,
            InterviewScorecard.interview_id == interview_id,
            InterviewScorecard.interviewer_id == actor_id,
        )
    )
    if scorecard is not None and scorecard.submitted_at is not None:
        raise ScorecardLocked("a submitted scorecard is immutable")

    values = payload.model_dump(exclude={"submit"})
    values["recommendation"] = payload.recommendation.value
    now = datetime.now(UTC)
    if scorecard is None:
        scorecard = InterviewScorecard(
            id=uuid4(),
            tenant_id=tenant_id,
            interview_id=interview_id,
            interviewer_id=actor_id,
            submitted_at=now if payload.submit else None,
            **values,
        )
        session.add(scorecard)
    else:
        for field, value in values.items():
            setattr(scorecard, field, value)
        scorecard.updated_at = now
        if payload.submit:
            scorecard.submitted_at = now

    if payload.submit:
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                aggregate_id=interview_id,
                event_type="scorecard.submitted",
                payload={
                    "interview_id": str(interview_id),
                    "scorecard_id": str(scorecard.id),
                    "interviewer_id": str(actor_id),
                    "recommendation": payload.recommendation.value,
                    "overall_score": payload.overall_score,
                },
            )
        )
    await _commit(session)
    return scorecard


async def list_scorecards(
    session: AsyncSession, tenant_id: UUID, interview_id: UUID
) -> list[InterviewScorecard]:
    rows = await session.scalars(
        select(InterviewScorecard)
        .where(
            InterviewScorecard.tenant_id == tenant_id,
            InterviewScorecard.interview_id == interview_id,
        )
        .order_by(InterviewScorecard.created_at)
    )
    return list(rows)


async def create_offer(
    session: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    payload: OfferCreate,
) -> Offer:
    application = await session.scalar(
        select(Application).where(
            Application.id == payload.application_id, Application.tenant_id == tenant_id
        )
    )
    if application is None:
        raise InvalidReference("application not found")
    if ApplicationStage(application.stage) != ApplicationStage.OFFER:
        raise PipelineInvalidTransition(
            "application must be in offer stage before drafting an offer"
        )

    offer = Offer(
        id=uuid4(),
        tenant_id=tenant_id,
        created_by=actor_id,
        currency=payload.currency.upper(),
        **payload.model_dump(exclude={"currency"}),
    )
    session.add(offer)
    session.add(
        OfferAudit(
            tenant_id=tenant_id,
            offer_id=offer.id,
            from_status=None,
            to_status=OfferStatus.DRAFT.value,
            actor_id=actor_id,
        )
    )
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            aggregate_id=offer.id,
            event_type="offer.created",
            payload={
                "offer_id": str(offer.id),
                "application_id": str(offer.application_id),
                "status": OfferStatus.DRAFT.value,
            },
        )
    )
    await _commit(session)
    return offer


async def transition_offer(
    session: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    offer_id: UUID,
    payload: OfferStatusUpdate,
) -> Offer:
    offer = await session.scalar(
        select(Offer).where(Offer.id == offer_id, Offer.tenant_id == tenant_id)
    )
    if offer is None:
        raise InvalidReference("offer not found")
    current = OfferStatus(offer.status)
    if not can_transition_offer(current, payload.status):
        raise PipelineInvalidTransition(
            f"cannot change offer from {current.value} to {payload.status.value}"
        )

    result = await session.execute(
        update(Offer)
        .where(Offer.id == offer_id, Offer.tenant_id == tenant_id, Offer.version == payload.version)
        .values(
            status=payload.status.value,
            version=Offer.version + 1,
            updated_at=datetime.now(UTC),
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise PipelineStaleVersion("offer was changed by another request; read it again and retry")

    application_target = {
        OfferStatus.ACCEPTED: ApplicationStage.HIRED,
        OfferStatus.DECLINED: ApplicationStage.REJECTED,
        OfferStatus.WITHDRAWN: ApplicationStage.REJECTED,
    }.get(payload.status)
    if application_target is not None:
        application = await session.scalar(
            select(Application).where(
                Application.id == offer.application_id, Application.tenant_id == tenant_id
            )
        )
        if application is None or ApplicationStage(application.stage) != ApplicationStage.OFFER:
            await session.rollback()
            raise PipelineInvalidTransition("application is no longer in offer stage")
        await session.execute(
            update(Application)
            .where(Application.id == application.id, Application.tenant_id == tenant_id)
            .values(
                stage=application_target.value,
                version=Application.version + 1,
                updated_at=datetime.now(UTC),
            )
        )
        session.add(
            ApplicationAudit(
                tenant_id=tenant_id,
                application_id=application.id,
                from_stage=ApplicationStage.OFFER.value,
                to_stage=application_target.value,
                reason=f"offer {payload.status.value}",
                actor_id=actor_id,
            )
        )
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                aggregate_id=application.id,
                event_type="application.stage_changed",
                payload={
                    "application_id": str(application.id),
                    "from_stage": ApplicationStage.OFFER.value,
                    "to_stage": application_target.value,
                    "reason": f"offer {payload.status.value}",
                },
            )
        )

    session.add(
        OfferAudit(
            tenant_id=tenant_id,
            offer_id=offer_id,
            from_status=current.value,
            to_status=payload.status.value,
            actor_id=actor_id,
        )
    )

    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            aggregate_id=offer_id,
            event_type="offer.status_changed",
            payload={
                "offer_id": str(offer_id),
                "application_id": str(offer.application_id),
                "from_status": current.value,
                "to_status": payload.status.value,
            },
        )
    )
    await session.commit()
    await set_tenant_context(session, tenant_id)
    return await session.scalar(select(Offer).where(Offer.id == offer_id))


async def list_offers(session: AsyncSession, tenant_id: UUID) -> list[Offer]:
    rows = await session.scalars(
        select(Offer).where(Offer.tenant_id == tenant_id).order_by(Offer.created_at.desc())
    )
    return list(rows)


async def list_offer_audits(
    session: AsyncSession, tenant_id: UUID, offer_id: UUID
) -> list[OfferAudit]:
    rows = await session.scalars(
        select(OfferAudit)
        .where(OfferAudit.tenant_id == tenant_id, OfferAudit.offer_id == offer_id)
        .order_by(OfferAudit.created_at, OfferAudit.id)
    )
    return list(rows)
