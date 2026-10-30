# HireFlow Go Outbox Relay

该服务从 PostgreSQL 事务 Outbox 抢占事件，并在收到 RabbitMQ Publisher Confirm 后才将事件标记为 `published`。FastAPI、RLS、排期和通知消费者仍由原有 Python 服务负责。

## 可靠性语义

1. 单条 PostgreSQL CTE 使用 `FOR UPDATE SKIP LOCKED` 抢占并更新租约，支持多个 Relay 实例并发工作。
2. 消息使用持久化 Delivery Mode，并以 Outbox UUID 作为 RabbitMQ `message_id`。
3. Broker ACK 后才更新数据库；如果 ACK 后数据库更新失败，租约过期会重投，因此明确提供 At-least-once，而非虚假的 Exactly-once。
4. 发布失败将事件恢复为 `pending`，按 `2^attempts` 秒退避，最大 300 秒；进程崩溃则由 60 秒租约恢复。
5. SIGTERM 停止新抢占，并将批次内未发布事件恢复为可重试状态。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HIREFLOW_DATABASE_URL` | 本地 PostgreSQL | pgx 连接串 |
| `HIREFLOW_RABBITMQ_URL` | 本地 RabbitMQ | AMQP 连接串 |
| `HIREFLOW_HTTP_ADDRESS` | `:9101` | 健康检查和指标地址 |
| `HIREFLOW_OUTBOX_BATCH_SIZE` | `100` | 单次抢占上限 |
| `HIREFLOW_OUTBOX_POLL_INTERVAL` | `1s` | 空闲轮询间隔 |
| `HIREFLOW_OUTBOX_LEASE_DURATION` | `60s` | 崩溃恢复租约 |
| `HIREFLOW_PUBLISH_TIMEOUT` | `5s` | 单条 Confirm 超时 |

端点：`/live`、`/ready`、`/metrics`。Readiness 同时要求 PostgreSQL 和 RabbitMQ 可用，响应不会暴露连接串或异常原文。

## 验证

```powershell
go test -race ./...
```

测试分为两层：纯单测覆盖 Publisher Confirm 的 ACK、NACK、关闭和超时；Docker 集成测试连接真实 PostgreSQL/RabbitMQ，覆盖 Channel/Connection 断开、租约过期重抢，以及 4 个 Relay 实例并行抢占不重复。通知消费者验收还会并发投递同一事件，确认 Inbox 唯一键只产生一组业务通知。

仓库级 `go_relay_recovery_smoke.ps1` 验证 Broker 中断时的持久化恢复；`relay_performance.ps1` 在独立 Compose 项目中对比 Go/Python Relay，不访问主环境数据或队列。
