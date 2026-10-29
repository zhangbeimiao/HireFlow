"""RabbitMQ topology shared by the outbox relay and business consumers."""

import aio_pika

EVENT_QUEUE = "hireflow.events"
RETRY_QUEUE = "hireflow.events.retry"
DLQ = "hireflow.events.dlq"


async def declare_event_topology(channel: aio_pika.abc.AbstractChannel):
    await channel.declare_queue(DLQ, durable=True)
    await channel.declare_queue(
        RETRY_QUEUE,
        durable=True,
        arguments={
            "x-message-ttl": 5_000,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": EVENT_QUEUE,
        },
    )
    # Do not attach DLX arguments to the main queue. Earlier local deployments created it
    # without arguments, and RabbitMQ rejects an in-place queue argument change. The consumer
    # explicitly publishes the original message to DLQ, which keeps this upgrade lossless.
    return await channel.declare_queue(EVENT_QUEUE, durable=True)
