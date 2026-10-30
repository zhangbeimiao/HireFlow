"""Database-backed reliability acceptance check, run inside the worker container.

It exercises the durable boundaries without relying on a fake in-memory queue: Inbox idempotency,
failure evidence, manual replay state, and Outbox retry metadata all live in PostgreSQL.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from hireflow.db import SessionLocal
from hireflow.models import Interview, Notification, NotificationInbox, OutboxEvent
from hireflow.notifications import (
    InvalidEvent,
    claim_replay_requests,
    consume_notification_event,
    record_consumer_failure,
    request_replay,
    reset_replay_request,
)
from hireflow.outbox import record_publish_failure


async def run() -> None:
    async with SessionLocal() as session:
        interview = await session.scalar(
            select(Interview).order_by(Interview.created_at.desc()).limit(1)
        )
        if interview is None:
            raise RuntimeError(
                "create an interview first (run scripts/demo.ps1 or workbench_smoke.ps1)"
            )
        tenant_id = interview.tenant_id

    # If a consumer commits business side effects and crashes before ack, RabbitMQ redelivers the
    # same event. The durable Inbox must keep the second delivery from duplicating notifications.
    duplicate_event = {
        "id": str(uuid4()),
        "tenant_id": str(tenant_id),
        "event_type": "interview.scheduled",
        "payload": {"interview_id": str(interview.id)},
    }

    async def consume_duplicate() -> None:
        async with SessionLocal() as session:
            await consume_notification_event(session, duplicate_event)

    # Model two consumer processes receiving the same RabbitMQ delivery at once,
    # followed by another redelivery after the first transaction committed.
    await asyncio.gather(consume_duplicate(), consume_duplicate())
    await consume_duplicate()
    async with SessionLocal() as session:
        created_notifications = await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.source_event_id == duplicate_event["id"])
        )
        duplicate_inbox = await session.scalar(
            select(NotificationInbox).where(NotificationInbox.event_id == duplicate_event["id"])
        )
    if (
        created_notifications != 2
        or duplicate_inbox is None
        or duplicate_inbox.status != "processed"
    ):
        raise RuntimeError("duplicate delivery was not idempotent")

    # A validly shaped but invalid reference follows the same failure persistence and manual replay
    # path as a poison message which the RabbitMQ consumer sends through retry/DLQ handling.
    failed_event = {
        "id": str(uuid4()),
        "tenant_id": str(tenant_id),
        "event_type": "interview.scheduled",
        "payload": {"interview_id": str(uuid4())},
    }
    async with SessionLocal() as session:
        try:
            await consume_notification_event(session, failed_event)
        except InvalidEvent as error:
            await record_consumer_failure(session, failed_event, error)
        else:
            raise RuntimeError("poison event unexpectedly succeeded")
    async with SessionLocal() as session:
        replay = await request_replay(session, tenant_id, failed_event["id"])
        if replay is None or replay.status != "replay_requested":
            raise RuntimeError("failed event could not enter manual replay")
        claimed = await claim_replay_requests(session)
        if not any(item.id == replay.id for item in claimed):
            raise RuntimeError("manual replay request was not claimable")
        await reset_replay_request(session, replay.id)

    # Outbox retry metadata survives a relay/broker outage. The relay claims first, then gives this
    # event a future retry time rather than losing it.
    async with SessionLocal() as session:
        outbox = OutboxEvent(
            tenant_id=tenant_id,
            aggregate_id=interview.id,
            event_type="reliability.probe",
            payload={"interview_id": str(interview.id)},
            status="publishing",
            attempts=1,
        )
        session.add(outbox)
        await session.commit()
        await record_publish_failure(session, outbox.id)
        await session.refresh(outbox)
    if (
        outbox.status != "pending"
        or outbox.next_attempt_at is None
        or outbox.next_attempt_at <= datetime.now(UTC)
    ):
        raise RuntimeError("outbox failure did not schedule a durable retry")

    print("duplicate_delivery : 2 concurrent deliveries plus redelivery -> 2 notifications")
    print("crash_recovery     : repeated committed event kept Inbox processed exactly once")
    print("manual_replay      : failed -> replay_requested -> replay_queued -> replay_requested")
    print("outbox_retry       : publishing -> pending with a persisted next_attempt_at")
    print("result             : reliable messaging persistence verified")


if __name__ == "__main__":
    asyncio.run(run())
