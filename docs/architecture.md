# HireFlow architecture and consistency notes

## Request path

```text
Browser / HR system
        |
        v
FastAPI API ──> PostgreSQL
   |               ├─ recruiting entities
   |               ├─ interviews + audit trail
   |               └─ transactional outbox
   |
   v
Scheduling service
  ├─ candidate availability
  ├─ interviewer availability
  └─ active interviews subtracted before matching

Outbox Relay ──> RabbitMQ ──> Notification Consumer ──> PostgreSQL notifications
```

## Observability boundaries

三个独立进程分别暴露 Prometheus 指标，避免假装不同进程共享 Python 内存：

- API `:8000/metrics`：HTTP 吞吐/延迟、在途请求、缓存、排期冲突和 SQLAlchemy 连接池状态。
- Outbox Relay `:9101/metrics`：发布成功/重试 Counter，以及 pending、publishing、retry 持久化积压 Gauge。
- Notification Consumer `:9102/metrics`：processed、retry、DLQ、requeue 结果，以及 failed/replay 状态积压。

Outbox 与通知积压指标直接根据 PostgreSQL 持久化状态刷新；进程重启不会让失败证据消失。当前阶段只暴露采集端点，告警阈值需要结合生产流量基线后配置。

## Scheduling invariants

1. Timestamps include an offset at the API boundary and are stored as `timestamptz` in PostgreSQL.
2. An appointment uses a half-open interval `[starts_at, ends_at)`: an interview ending at 10:00 does
   not conflict with one beginning at 10:00.
3. The suggestion algorithm computes a candidate/interviewer availability intersection only after
   subtracting their active scheduled or confirmed interviews.
4. A suggestion is not a reservation. The actual create operation remains the concurrency boundary.
5. PostgreSQL enforces the decisive invariant with a GiST exclusion constraint; a service-level check
   is retained only to return a useful 409 before the database rejects a request.

```sql
EXCLUDE USING gist (
  interviewer_id WITH =,
  tstzrange(starts_at, ends_at, '[)') WITH &&
) WHERE (status IN ('scheduled', 'confirmed'));
```

## State machine

```text
pending ───> scheduled ───> confirmed ───> completed
   |              |               └──────> no_show
   └──────────────┴──────────────────────> cancelled
```

Every mutation sends the version read by the caller. The status update includes that version in its
`WHERE` condition, so a stale HR screen cannot overwrite a concurrent cancellation or confirmation.
Each successful mutation also appends an audit record.

## Why the outbox exists

Creating an interview and delivering an email are two different systems. A database transaction cannot
atomically commit both PostgreSQL data and a RabbitMQ publish. HireFlow instead writes `outbox_events`
inside the same transaction as the interview state. A separate relay claims events using
`FOR UPDATE SKIP LOCKED`, publishes a persistent broker message with the outbox event ID as `message_id`,
then marks the event published. A crash or broker failure leaves an event pending or lets its lease expire;
it is retried with bounded exponential backoff.

This is **at-least-once** delivery. Consumers must deduplicate by `message_id`; claiming exactly-once
delivery across a database and broker would be a misleading promise.

## Notification delivery loop

The notification consumer writes an Inbox row keyed by `(consumer_name, event_id)` and creates the
in-app notifications in the same PostgreSQL transaction. A redelivery after the database commit sees
`processed` and performs no business side effect. A failed delivery is recorded with its error and attempt
count, then sent to a 5-second retry queue. After three total deliveries the consumer explicitly copies the
message to `hireflow.events.dlq`; this explicit publish keeps upgrades compatible with queues created by
older versions. An Owner can list failed deliveries and request a replay through the admin API; the consumer
durably claims those requests and republishes the original payload.

## Interview questions to rehearse

| Question | Expected answer direction |
| --- | --- |
| Why not only lock Redis when creating an interview? | Redis is not the system of record; the PostgreSQL exclusion constraint survives process restarts and protects every application instance. |
| How is cache isolation enforced? | Every list key includes the JWT tenant id; PostgreSQL RLS still constrains every miss, and writes invalidate only that tenant's key. |
| What happens when Redis is unavailable? | List reads fail open to PostgreSQL and cache invalidation relies on TTL convergence; scheduling correctness never depends on Redis. |
| Why can a suggested slot still fail to create? | It is a read-time recommendation; a competing request may reserve the slot before submission. The final write must return 409 and allow a retry. |
| How do you avoid duplicate notifications? | Publish with outbox event ID; consumers persist handled IDs and perform idempotent side effects. |
| What happens if the relay dies after publishing? | The DB state may still be `publishing`; after its lease expires it is republished. This is why consumers deduplicate. |
| Why retain failure records if RabbitMQ already has a DLQ? | The broker stores the message, while the Inbox stores tenant-scoped error evidence and makes authorized, auditable replay possible. |
| Why do you use UTC? | It provides one unambiguous storage instant; local zones are converted only at input and display. |
