"""Notification consumer persistence and idempotent business side effects."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.models import Interview, Notification, NotificationInbox

CONSUMER_NAME = "notification-consumer"


class InvalidEvent(ValueError):
    pass


def _message_for(event_type: str, interview: Interview) -> tuple[str, str]:
    if event_type == "interview.scheduled":
        return "面试已安排", f"面试时间：{interview.starts_at.isoformat()}"
    if event_type == "interview.status_changed":
        if interview.status == "confirmed":
            return "面试已确认", f"面试已确认，时间：{interview.starts_at.isoformat()}"
        if interview.status == "cancelled":
            return "面试已取消", "该场面试已取消，请等待招聘方后续安排。"
        return "面试状态已更新", f"当前状态：{interview.status}"
    raise InvalidEvent(f"unsupported event type: {event_type}")


async def consume_notification_event(session: AsyncSession, event: dict) -> None:
    """Create recipient notifications exactly once in the same DB transaction as Inbox state."""
    try:
        event_id = str(event["id"])
        tenant_id = UUID(str(event["tenant_id"]))
        event_type = str(event["event_type"])
        interview_id = UUID(str(event["payload"]["interview_id"]))
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidEvent("event is missing a valid id, tenant_id, or interview_id") from error

    inbox = await session.scalar(
        select(NotificationInbox).where(
            NotificationInbox.consumer_name == CONSUMER_NAME,
            NotificationInbox.event_id == event_id,
        )
    )
    if inbox is not None and inbox.status == "processed":
        return
    if inbox is None:
        inbox = NotificationInbox(
            tenant_id=tenant_id,
            consumer_name=CONSUMER_NAME,
            event_id=event_id,
            event_type=event_type,
            payload=event,
            status="processing",
        )
        session.add(inbox)
    else:
        inbox.status = "processing"
        inbox.last_error = None

    interview = await session.scalar(
        select(Interview).where(Interview.id == interview_id, Interview.tenant_id == tenant_id)
    )
    if interview is None:
        raise InvalidEvent("event references an interview outside its tenant")

    title, content = _message_for(event_type, interview)
    session.add_all(
        [
            Notification(
                tenant_id=tenant_id,
                source_event_id=event_id,
                recipient_type="candidate",
                recipient_id=interview.candidate_id,
                title=title,
                content=content,
            ),
            Notification(
                tenant_id=tenant_id,
                source_event_id=event_id,
                recipient_type="interviewer",
                recipient_id=interview.interviewer_id,
                title=title,
                content=content,
            ),
        ]
    )
    inbox.status = "processed"
    inbox.processed_at = datetime.now(UTC)
    await session.commit()


async def record_consumer_failure(session: AsyncSession, event: dict, error: Exception) -> None:
    """Keep failed-message evidence in Postgres even though the broker owns redelivery."""
    try:
        event_id = str(event["id"])
        tenant_id = UUID(str(event["tenant_id"]))
        event_type = str(event["event_type"])
    except (KeyError, ValueError, TypeError):
        return
    inbox = await session.scalar(
        select(NotificationInbox).where(
            NotificationInbox.consumer_name == CONSUMER_NAME,
            NotificationInbox.event_id == event_id,
        )
    )
    if inbox is None:
        inbox = NotificationInbox(
            tenant_id=tenant_id,
            consumer_name=CONSUMER_NAME,
            event_id=event_id,
            event_type=event_type,
            payload=event,
            status="failed",
            attempts=1,
            last_error=str(error)[:2000],
        )
        session.add(inbox)
    else:
        inbox.status = "failed"
        inbox.attempts += 1
        inbox.last_error = str(error)[:2000]
    await session.commit()


async def request_replay(session: AsyncSession, tenant_id: UUID, event_id: str) -> NotificationInbox | None:
    inbox = await session.scalar(
        select(NotificationInbox).where(
            NotificationInbox.tenant_id == tenant_id,
            NotificationInbox.event_id == event_id,
            NotificationInbox.consumer_name == CONSUMER_NAME,
            NotificationInbox.status == "failed",
        )
    )
    if inbox is None:
        return None
    inbox.status = "replay_requested"
    inbox.last_error = None
    await session.commit()
    return inbox


async def claim_replay_requests(session: AsyncSession, limit: int = 20) -> list[NotificationInbox]:
    rows = await session.scalars(
        select(NotificationInbox)
        .where(
            NotificationInbox.consumer_name == CONSUMER_NAME,
            NotificationInbox.status == "replay_requested",
        )
        .order_by(NotificationInbox.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed = list(rows)
    for inbox in claimed:
        inbox.status = "replay_queued"
    await session.commit()
    return claimed


async def reset_replay_request(session: AsyncSession, inbox_id: UUID) -> None:
    inbox = await session.get(NotificationInbox, inbox_id)
    if inbox is not None and inbox.status == "replay_queued":
        inbox.status = "replay_requested"
        await session.commit()
