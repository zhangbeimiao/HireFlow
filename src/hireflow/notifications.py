"""Notification consumer persistence and idempotent business side effects."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.models import Application, Interview, Job, Notification, NotificationInbox, Offer
from hireflow.observability import NOTIFICATION_FAILURE_BACKLOG

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


def _application_message(event_type: str, application: Application) -> tuple[str, str]:
    if event_type == "application.created":
        return "申请已进入流程", "招聘团队已收到你的申请。"
    if event_type == "application.stage_changed":
        return "申请进度已更新", f"当前阶段：{application.stage}"
    raise InvalidEvent(f"unsupported event type: {event_type}")


def _offer_message(event_type: str, offer: Offer) -> tuple[str, str]:
    if event_type == "offer.created":
        return "Offer 正在准备", "招聘团队已开始准备录用方案。"
    if event_type == "offer.status_changed":
        return "Offer 状态已更新", f"当前状态：{offer.status}"
    raise InvalidEvent(f"unsupported event type: {event_type}")


async def consume_notification_event(session: AsyncSession, event: dict) -> None:
    """Create recipient notifications exactly once in the same DB transaction as Inbox state."""
    try:
        event_id = str(event["id"])
        tenant_id = UUID(str(event["tenant_id"]))
        event_type = str(event["event_type"])
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidEvent("event is missing a valid id or tenant_id") from error

    # INSERT .. ON CONFLICT is the cross-process idempotency gate. A concurrent
    # duplicate waits for the first transaction, then locks and observes the
    # already-processed Inbox row instead of racing on the unique constraint.
    await session.execute(
        insert(NotificationInbox)
        .values(
            id=uuid4(),
            tenant_id=tenant_id,
            consumer_name=CONSUMER_NAME,
            event_id=event_id,
            event_type=event_type,
            payload=event,
            status="processing",
        )
        .on_conflict_do_nothing(constraint="uq_notification_inbox_event")
    )
    inbox = await session.scalar(
        select(NotificationInbox)
        .where(
            NotificationInbox.consumer_name == CONSUMER_NAME,
            NotificationInbox.event_id == event_id,
        )
        .with_for_update()
    )
    if (
        inbox is None
    ):  # Defensive: the insert or conflicting row must be visible in this transaction.
        raise RuntimeError("notification Inbox idempotency row was not available")
    if inbox is not None and inbox.status == "processed":
        return
    inbox.status = "processing"
    inbox.last_error = None

    recipients: list[tuple[str, UUID]]
    if event_type.startswith("interview."):
        try:
            interview_id = UUID(str(event["payload"]["interview_id"]))
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidEvent("interview event is missing interview_id") from error
        interview = await session.scalar(
            select(Interview).where(Interview.id == interview_id, Interview.tenant_id == tenant_id)
        )
        if interview is None:
            raise InvalidEvent("event references an interview outside its tenant")
        title, content = _message_for(event_type, interview)
        recipients = [
            ("candidate", interview.candidate_id),
            ("interviewer", interview.interviewer_id),
        ]
    elif event_type.startswith("application."):
        try:
            application_id = UUID(str(event["payload"]["application_id"]))
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidEvent("application event is missing application_id") from error
        application = await session.scalar(
            select(Application).where(
                Application.id == application_id, Application.tenant_id == tenant_id
            )
        )
        if application is None:
            raise InvalidEvent("event references an application outside its tenant")
        title, content = _application_message(event_type, application)
        recipients = [("candidate", application.candidate_id)]
    elif event_type.startswith("offer."):
        try:
            offer_id = UUID(str(event["payload"]["offer_id"]))
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidEvent("offer event is missing offer_id") from error
        row = await session.execute(
            select(Offer, Application)
            .join(Application, Application.id == Offer.application_id)
            .where(Offer.id == offer_id, Offer.tenant_id == tenant_id)
        )
        offer_row = row.one_or_none()
        if offer_row is None:
            raise InvalidEvent("event references an offer outside its tenant")
        offer, application = offer_row
        title, content = _offer_message(event_type, offer)
        recipients = [("candidate", application.candidate_id)]
    elif event_type == "scorecard.submitted":
        try:
            interview_id = UUID(str(event["payload"]["interview_id"]))
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidEvent("scorecard event is missing interview_id") from error
        row = await session.execute(
            select(Interview, Job)
            .join(Job, Job.id == Interview.job_id)
            .where(Interview.id == interview_id, Interview.tenant_id == tenant_id)
        )
        interview_row = row.one_or_none()
        if interview_row is None:
            raise InvalidEvent("event references an interview outside its tenant")
        interview, job = interview_row
        title, content = "面试评分已提交", "面试官已提交评分卡，可进入招聘决策。"
        recipient_id = job.hiring_manager_id or interview.interviewer_id
        recipients = [("hiring_manager", recipient_id)]
    else:
        raise InvalidEvent(f"unsupported event type: {event_type}")

    session.add_all(
        [
            Notification(
                tenant_id=tenant_id,
                source_event_id=event_id,
                recipient_type=recipient_type,
                recipient_id=recipient_id,
                title=title,
                content=content,
            )
            for recipient_type, recipient_id in recipients
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


async def request_replay(
    session: AsyncSession, tenant_id: UUID, event_id: str
) -> NotificationInbox | None:
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


async def refresh_notification_metrics(session: AsyncSession) -> None:
    for state in ("failed", "replay_requested", "replay_queued"):
        count = await session.scalar(
            select(func.count())
            .select_from(NotificationInbox)
            .where(
                NotificationInbox.consumer_name == CONSUMER_NAME, NotificationInbox.status == state
            )
        )
        NOTIFICATION_FAILURE_BACKLOG.labels(state=state).set(count or 0)
