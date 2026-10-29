"""Reliable outbox claiming rules; a broker adapter is intentionally kept outside domain code."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.models import OutboxEvent
from hireflow.observability import OUTBOX_BACKLOG


def retry_after(attempts: int) -> timedelta:
    """Bounded exponential backoff: 2s, 4s, ... capped at five minutes."""
    return timedelta(seconds=min(2 ** max(attempts, 1), 300))


async def claim_pending_events(session: AsyncSession, limit: int = 100) -> list[OutboxEvent]:
    now = datetime.now(UTC)
    events = await session.scalars(
        select(OutboxEvent)
        .where(
            or_(
                (OutboxEvent.status == "pending")
                & ((OutboxEvent.next_attempt_at.is_(None)) | (OutboxEvent.next_attempt_at <= now)),
                (OutboxEvent.status == "publishing") & (OutboxEvent.lease_expires_at < now),
            )
        )
        .order_by(OutboxEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed = list(events)
    for event in claimed:
        event.status = "publishing"
        event.attempts += 1
        event.lease_expires_at = now + timedelta(seconds=60)
    await session.commit()
    return claimed


async def mark_published(session: AsyncSession, event_id: UUID) -> None:
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        return
    event.status = "published"
    event.lease_expires_at = None
    await session.commit()


async def record_publish_failure(session: AsyncSession, event_id: UUID) -> None:
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        return
    event.status = "pending"
    event.next_attempt_at = datetime.now(UTC) + retry_after(event.attempts)
    event.lease_expires_at = None
    await session.commit()


async def refresh_outbox_metrics(session: AsyncSession) -> None:
    counts = {
        "pending": await session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "pending")
        ),
        "publishing": await session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "publishing")
        ),
        "retry": await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.status == "pending", OutboxEvent.attempts > 0)
        ),
    }
    for state, count in counts.items():
        OUTBOX_BACKLOG.labels(state=state).set(count or 0)
