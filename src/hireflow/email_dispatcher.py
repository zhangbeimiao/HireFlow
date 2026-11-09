"""Lease-based SMTP dispatcher for durable notification delivery rows."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select

from hireflow.db import SessionLocal
from hireflow.models import NotificationDelivery
from hireflow.notification_providers import (
    NotificationProvider,
    ProviderMessage,
    provider_from_environment,
)


def retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** max(0, attempts - 1)))


async def claim_deliveries(*, limit: int, lease_seconds: int) -> list[NotificationDelivery]:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(NotificationDelivery)
            .where(
                or_(
                    NotificationDelivery.status.in_(("pending", "retry")),
                    (
                        (NotificationDelivery.status == "sending")
                        & (NotificationDelivery.lease_expires_at < now)
                    ),
                ),
                or_(
                    NotificationDelivery.next_attempt_at.is_(None),
                    NotificationDelivery.next_attempt_at <= now,
                ),
            )
            .order_by(NotificationDelivery.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        claimed = list(rows)
        for delivery in claimed:
            delivery.status = "sending"
            delivery.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await session.commit()
        return claimed


async def record_success(delivery_id: UUID, provider_message_id: str) -> None:
    async with SessionLocal() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is None or delivery.status == "sent":
            return
        delivery.status = "sent"
        delivery.provider_message_id = provider_message_id
        delivery.sent_at = datetime.now(UTC)
        delivery.lease_expires_at = None
        delivery.last_error = None
        await session.commit()


async def record_failure(delivery_id: UUID, error: Exception, *, max_attempts: int) -> None:
    async with SessionLocal() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is None or delivery.status == "sent":
            return
        delivery.attempts += 1
        delivery.last_error = str(error)[:2000]
        delivery.lease_expires_at = None
        if delivery.attempts >= max_attempts:
            delivery.status = "failed"
            delivery.next_attempt_at = None
        else:
            delivery.status = "retry"
            delivery.next_attempt_at = datetime.now(UTC) + retry_delay(delivery.attempts)
        await session.commit()


async def dispatch_once(
    provider: NotificationProvider, *, batch_size: int, lease_seconds: int, max_attempts: int
) -> int:
    deliveries = await claim_deliveries(limit=batch_size, lease_seconds=lease_seconds)
    for delivery in deliveries:
        try:
            result = await provider.send(
                ProviderMessage(
                    delivery_id=str(delivery.id),
                    recipient=delivery.recipient,
                    subject=delivery.subject,
                    text_body=delivery.text_body,
                    html_body=delivery.html_body,
                )
            )
        except Exception as error:  # noqa: BLE001 - provider failures are durable state.
            await record_failure(delivery.id, error, max_attempts=max_attempts)
        else:
            await record_success(delivery.id, result.message_id)
    return len(deliveries)


async def run() -> None:
    provider = provider_from_environment()
    batch_size = int(os.getenv("HIREFLOW_EMAIL_BATCH_SIZE", "20"))
    lease_seconds = int(os.getenv("HIREFLOW_EMAIL_LEASE_SECONDS", "30"))
    max_attempts = int(os.getenv("HIREFLOW_EMAIL_MAX_ATTEMPTS", "3"))
    poll_seconds = float(os.getenv("HIREFLOW_EMAIL_POLL_SECONDS", "1"))
    if os.getenv("HIREFLOW_EMAIL_RUN_ONCE", "false").lower() == "true":
        await dispatch_once(
            provider,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
        return
    while True:
        processed = await dispatch_once(
            provider,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
        if processed == 0:
            await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
