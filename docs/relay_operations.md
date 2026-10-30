# Go Outbox Relay 监控与生产运行

## 启动本地观测栈

```powershell
$env:HIREFLOW_GRAFANA_ADMIN_PASSWORD = "replace-with-a-local-password"
docker compose -f docker-compose.yml -f docker-compose.observability.yml --profile observability up -d --build
```

- Prometheus：`http://localhost:9090`
- Grafana：`http://localhost:3000`，预置 `HireFlow / HireFlow Outbox Relay` 仪表盘
- Relay liveness/readiness：`http://localhost:9101/live`、`http://localhost:9101/ready`
- Relay Prometheus 指标：`http://localhost:9101/metrics`

开发环境默认 Grafana 密码仅用于本地演示；共享环境必须通过环境注入或容器编排 Secret 覆盖，禁止提交真实密码。

## `/metrics` 示例与含义

```text
hireflow_outbox_claimed_total 240
hireflow_outbox_backlog{state="pending"} 3
hireflow_outbox_relay_events_total{outcome="published"} 237
hireflow_outbox_relay_events_total{outcome="retry"} 2
hireflow_outbox_publish_duration_seconds_bucket{le="0.025"} 235
hireflow_outbox_relay_broker_disconnects_total 1
```

| 指标 | 类型 | 用途 |
| --- | --- | --- |
| `hireflow_outbox_claimed_total` | Counter | Relay 原子抢占的 Outbox 行数。 |
| `hireflow_outbox_backlog{state}` | Gauge | `pending`、`publishing`、`retry` 三类持久化积压。 |
| `hireflow_outbox_relay_events_total{outcome}` | Counter | `published`、`retry`、`state_update_failure` 结果计数。 |
| `hireflow_outbox_publish_duration_seconds` | Histogram | RabbitMQ 发布并等待 Confirm 的耗时，用于 P95/P99。 |
| `hireflow_outbox_relay_broker_disconnects_total` | Counter | Relay 观察到的 RabbitMQ 连接关闭次数。 |

Prometheus 规则覆盖：积压持续超过 100、失败率持续超过 5%、十分钟重试超过 10 次、Broker 断连及 Relay 抓取目标不可达。阈值是开发基线，生产环境应结合正常流量和 SLO 调整。

## 优雅停机与滚动升级

Relay 收到 `SIGTERM` 后停止新一轮抢占；当前正在发布的消息完成 Publisher Confirm，批次内尚未发布的事件被恢复为带退避时间的 `pending`。若进程在回写前被强杀，`publishing` 记录会在租约到期后重新抢占，因此语义是 At-least-once，消费者必须保留 Inbox 幂等。

滚动升级建议：

1. 至少运行两个 Relay 实例；实例共享 PostgreSQL，`FOR UPDATE SKIP LOCKED` 防止重复抢占。
2. `maxUnavailable=1`，停止超时应大于 `HIREFLOW_PUBLISH_TIMEOUT + 10s`，并先让 readiness 退出流量。
3. 新版本 readiness 通过 PostgreSQL 与 RabbitMQ 检查后再终止旧实例。
4. 升级期间观察 `pending/publishing` 积压、重试和断连；积压持续上升则暂停发布。
5. 不把“未重复抢占”等同于 Exactly-once：ACK 后数据库更新失败仍可能重投，最终由消费者 Inbox 去重。

## 密钥、TLS 与最小权限

- PostgreSQL 与 RabbitMQ 凭据放入 Vault、云 Secret Manager 或编排系统 Secret，通过文件或环境注入；不要写入镜像、Compose 或 Git。
- PostgreSQL 使用 `sslmode=verify-full` 并校验受信 CA；RabbitMQ 使用 `amqps://`、服务端证书校验和需要时的双向 TLS。
- 为 Relay 使用独立数据库角色，只授予 Outbox 表所需的 `SELECT/UPDATE` 权限；RabbitMQ 用户只授予目标 vhost 中声明/写入指定队列的权限。
- 生产镜像继续使用非 root 用户，限制出站网络到 PostgreSQL/RabbitMQ/监控端点，并设置 CPU、内存和只读文件系统。
- JWT、数据库、RabbitMQ 与 Grafana 密钥分别轮换；日志只记录事件 ID 和规范化错误，不记录连接串、Token 或消息敏感字段。
