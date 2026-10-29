"""Outbox relay process. Run separately from the web API."""

import asyncio
import json
import os

import aio_pika

from hireflow.broker import EVENT_QUEUE, declare_event_topology
from hireflow.db import SessionLocal
from hireflow.outbox import claim_pending_events, mark_published, record_publish_failure


async def relay_once(channel: aio_pika.abc.AbstractChannel) -> int:
    async with SessionLocal() as session:
        events = await claim_pending_events(session)

    for event in events:
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
            async with SessionLocal() as session:
                await record_publish_failure(session, event.id)
        else:
            async with SessionLocal() as session:
                await mark_published(session, event.id)
    return len(events)


async def run() -> None:
    rabbitmq_url = os.getenv(
        "HIREFLOW_RABBITMQ_URL", "amqp://hireflow:hireflow_dev_only@localhost/"
    )
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await declare_event_topology(channel)
        while True:
            await relay_once(channel)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(run())
