"""Idempotent RabbitMQ notification consumer with retry, DLQ, and manual replay support."""

import asyncio
import json
import os

import aio_pika
from prometheus_client import start_http_server

from hireflow.broker import DLQ, EVENT_QUEUE, RETRY_QUEUE, declare_event_topology
from hireflow.db import SessionLocal
from hireflow.notifications import (
    claim_replay_requests,
    consume_notification_event,
    record_consumer_failure,
    refresh_notification_metrics,
    reset_replay_request,
)
from hireflow.observability import NOTIFICATION_DELIVERIES

MAX_DELIVERIES = 3


def retry_count(message: aio_pika.abc.AbstractIncomingMessage) -> int:
    return int(message.headers.get("x-hireflow-retry-count", 0))


async def publish_event(channel: aio_pika.abc.AbstractChannel, event: dict, routing_key: str, retries: int) -> None:
    await channel.default_exchange.publish(
        aio_pika.Message(
            body=json.dumps(event).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(event.get("id", "")),
            type=str(event.get("event_type", "")),
            headers={"x-hireflow-retry-count": retries},
        ),
        routing_key=routing_key,
    )


async def handle_message(message: aio_pika.abc.AbstractIncomingMessage, channel: aio_pika.abc.AbstractChannel) -> None:
    event: dict | None = None
    try:
        event = json.loads(message.body)
        if not isinstance(event, dict):
            raise TypeError("event body must be a JSON object")
        async with SessionLocal() as session:
            await consume_notification_event(session, event)
    except Exception as error:  # noqa: BLE001 - a consumer must capture all failed business deliveries.
        try:
            if event is None:
                await channel.default_exchange.publish(
                    aio_pika.Message(
                        body=message.body,
                        content_type=message.content_type or "application/octet-stream",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        headers={"x-hireflow-error": str(error)[:500]},
                    ),
                    routing_key=DLQ,
                )
                await message.ack()
                NOTIFICATION_DELIVERIES.labels(outcome="malformed_dlq").inc()
                return
            async with SessionLocal() as session:
                await record_consumer_failure(session, event, error)
            retries = retry_count(message)
            destination = RETRY_QUEUE if retries + 1 < MAX_DELIVERIES else DLQ
            await publish_event(channel, event, destination, retries + 1)
            await message.ack()
            outcome = "retry" if destination == RETRY_QUEUE else "dlq"
            NOTIFICATION_DELIVERIES.labels(outcome=outcome).inc()
        except Exception:  # noqa: BLE001 - preserve the original message when retry publishing fails.
            await message.nack(requeue=True)
            NOTIFICATION_DELIVERIES.labels(outcome="requeued").inc()
    else:
        await message.ack()
        NOTIFICATION_DELIVERIES.labels(outcome="processed").inc()


async def publish_requested_replays(channel: aio_pika.abc.AbstractChannel) -> None:
    async with SessionLocal() as session:
        inboxes = await claim_replay_requests(session)
    for inbox in inboxes:
        try:
            await publish_event(channel, inbox.payload, EVENT_QUEUE, retries=0)
        except Exception:  # noqa: BLE001 - retain replay intent for any broker outage.
            async with SessionLocal() as session:
                await reset_replay_request(session, inbox.id)


async def run() -> None:
    start_http_server(int(os.getenv("HIREFLOW_METRICS_PORT", "9102")))
    rabbitmq_url = os.getenv("HIREFLOW_RABBITMQ_URL")
    if not rabbitmq_url:
        raise RuntimeError("HIREFLOW_RABBITMQ_URL must be configured")
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=20)
        queue = await declare_event_topology(channel)
        await queue.consume(lambda message: handle_message(message, channel))
        while True:
            await publish_requested_replays(channel)
            async with SessionLocal() as session:
                await refresh_notification_metrics(session)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(run())
