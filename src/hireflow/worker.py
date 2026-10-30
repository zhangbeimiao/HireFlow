"""Outbox relay process. Run separately from the web API."""

import asyncio
import json
import os
from time import perf_counter

import aio_pika
from prometheus_client import start_http_server

from hireflow.broker import EVENT_QUEUE, declare_event_topology
from hireflow.db import SessionLocal
from hireflow.observability import OUTBOX_PUBLISH_DURATION, OUTBOX_RELAY_EVENTS
from hireflow.outbox import (
    claim_pending_events,
    mark_published,
    record_publish_failure,
    refresh_outbox_metrics,
)


async def relay_once(channel: aio_pika.abc.AbstractChannel, batch_size: int = 100) -> int:
    async with SessionLocal() as session:
        events = await claim_pending_events(session, limit=batch_size)

    for event in events:
        started_at = perf_counter()
        try:
            body = json.dumps(
                {
                    "id": str(event.id),
                    "tenant_id": str(event.tenant_id),
                    "event_type": event.event_type,
                    "payload": event.payload,
                }
            ).encode()
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=str(event.id),
                    type=event.event_type,
                ),
                routing_key=EVENT_QUEUE,
            )
        except (aio_pika.exceptions.AMQPException, ConnectionError):
            OUTBOX_RELAY_EVENTS.labels(outcome="retry").inc()
            async with SessionLocal() as session:
                await record_publish_failure(session, event.id)
        else:
            OUTBOX_RELAY_EVENTS.labels(outcome="published").inc()
            async with SessionLocal() as session:
                await mark_published(session, event.id)
        finally:
            OUTBOX_PUBLISH_DURATION.observe(perf_counter() - started_at)
    async with SessionLocal() as session:
        await refresh_outbox_metrics(session)
    return len(events)


async def run() -> None:
    start_http_server(int(os.getenv("HIREFLOW_METRICS_PORT", "9101")))
    batch_size = int(os.getenv("HIREFLOW_OUTBOX_BATCH_SIZE", "100"))
    if batch_size < 1:
        raise ValueError("HIREFLOW_OUTBOX_BATCH_SIZE must be positive")
    rabbitmq_url = os.getenv("HIREFLOW_RABBITMQ_URL")
    if not rabbitmq_url:
        raise RuntimeError("HIREFLOW_RABBITMQ_URL must be configured")
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await declare_event_topology(channel)
        while True:
            await relay_once(channel, batch_size=batch_size)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(run())
