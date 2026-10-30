"""Integration proof that duplicate ATS deliveries create one durable side effect."""

import asyncio
import json
from uuid import uuid4

from sqlalchemy import func, select

from hireflow.db import SessionLocal, engine
from hireflow.models import Application, Notification, NotificationInbox
from hireflow.notifications import CONSUMER_NAME, consume_notification_event


async def run() -> None:
    async with SessionLocal() as session:
        application = await session.scalar(
            select(Application).order_by(Application.created_at.desc()).limit(1)
        )
    if application is None:
        raise RuntimeError("no application exists; run scripts/ats_smoke.ps1 first")

    event_id = str(uuid4())
    event = {
        "id": event_id,
        "tenant_id": str(application.tenant_id),
        "aggregate_id": str(application.id),
        "event_type": "application.stage_changed",
        "payload": {"application_id": str(application.id), "stage": str(application.stage)},
    }

    # Model two broker deliveries in separate transactions, as happens after an ACK timeout.
    async with SessionLocal() as session:
        await consume_notification_event(session, event)
    async with SessionLocal() as session:
        await consume_notification_event(session, event)

    async with SessionLocal() as session:
        notification_count = await session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.source_event_id == event_id
            )
        )
        inbox_count = await session.scalar(
            select(func.count()).select_from(NotificationInbox).where(
                NotificationInbox.consumer_name == CONSUMER_NAME,
                NotificationInbox.event_id == event_id,
                NotificationInbox.status == "processed",
            )
        )

    if notification_count != 1 or inbox_count != 1:
        raise RuntimeError(
            "duplicate delivery escaped the Inbox gate: "
            f"notifications={notification_count}, inbox={inbox_count}"
        )
    print(
        json.dumps(
            {
                "duplicate_deliveries": 2,
                "notifications_created": notification_count,
                "processed_inbox_rows": inbox_count,
                "result": "notification consumer idempotency verified",
            },
            ensure_ascii=False,
        )
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
