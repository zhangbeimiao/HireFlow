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

Go Outbox Relay ──> RabbitMQ ──> Python Notification Consumer ──> PostgreSQL notifications
```

## Go Outbox Relay

Relay 是本项目刻意划出的 Go 服务边界：它没有招聘领域 API，只负责高并发抢占、可靠发布和故障恢复。单条 PostgreSQL CTE 使用 `FOR UPDATE SKIP LOCKED` 选择可投递或租约过期事件，并原子更新为 `publishing`，因此多个实例不会正常抢到同一行。

每条消息以 Outbox ID 作为 `message_id`，使用持久化 Delivery Mode；收到 RabbitMQ Publisher Confirm 后才将数据库状态更新为 `published`。如果 Broker 已确认但数据库更新失败，事件会在租约过期后重投，所以系统明确保持 At-least-once，并由 Python Consumer 的 Inbox 唯一键消除重复副作用。发布失败则回写 `pending + next_attempt_at`，按指数退避重试；连接关闭会立即让 Relay readiness 变为 `503`，重连后继续处理积压。

SIGTERM 到来后 Relay 停止新抢占，批次内尚未发布的事件恢复为可重试状态。Python Relay 仍位于 `python-relay` Compose profile，作为回退与对照实现，默认栈只运行 Go Relay。

## Health boundaries

- `/health/live` 只验证 API 进程存活，不因外部依赖抖动触发无效重启。
- `/health/ready` 以 API 运行账号执行 PostgreSQL 最小查询，并探测 Redis。PostgreSQL 不可用返回 `503 not_ready`；Redis 不可用返回 `200 degraded`，列表读取仍可 fail-open 回源 PostgreSQL。
- RabbitMQ 不属于同步 API 的就绪门禁。面试事务先写 Outbox，Broker 故障由 Relay 重试、Outbox backlog 指标及进程级健康状态暴露。
- 探针只公开规范化状态，不返回连接串、主机、凭据或异常原文。

## Observability boundaries

三个独立进程分别暴露 Prometheus 指标，避免假装不同进程共享 Python 内存：

- API `:8000/metrics`：HTTP 吞吐/延迟、在途请求、缓存、排期冲突和 SQLAlchemy 连接池状态。
- Go Outbox Relay `:9101/metrics`：抢占量、发布结果、Publisher Confirm 延迟 Histogram，以及 pending、publishing、retry 持久化积压 Gauge。
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
