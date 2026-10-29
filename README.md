# HireFlow｜多租户招聘面试调度与协同平台

HireFlow 面向中小企业招聘场景，解决候选人与面试官协调时间、并发抢占同一面试时段等高风险问题。项目将排期一致性、权限隔离和异步通知拆为可验证的后端闭环。

## 技术栈

`Python 3.11+` · `FastAPI` · `PostgreSQL 16` · `SQLAlchemy Async` · `Alembic` · `Redis` · `RabbitMQ` · `Docker Compose` · `pytest` · `Prometheus`

## 已实现能力

- **多租户与权限**：JWT 签发 `tenant_id` 与角色；Owner、Recruiter、Hiring Manager、Interviewer 由 RBAC 校验，跨租户资源返回 `404`。
- **可靠排期**：候选人与面试官时段取交集并扣除活动面试；使用半开区间 `[start, end)`，PostgreSQL GiST 排他约束是并发写入最终裁决。
- **状态与审计**：状态机、乐观锁版本号和状态变更审计记录。
- **可靠通知**：事务内写 Outbox；Relay 用 `FOR UPDATE SKIP LOCKED` 发布事件；Inbox 按 `(consumer_name, event_id)` 幂等消费，支持重试、DLQ 和 Owner 重放。
- **运行保护**：Redis 固定窗口限流；请求 ID、JSON 结构化访问日志和 Prometheus 指标。

## 架构

```text
Client → FastAPI (JWT/RBAC, Redis rate limit, request ID) → PostgreSQL
                    │                                  ├─ interviews + audit + outbox
                    │                                  └─ notification inbox + in-app notifications
                    ▼
          Outbox Relay → RabbitMQ → Notification Consumer
                              ├─ retry queue (5s)
                              └─ DLQ + Owner replay
```

更多设计取舍见 [架构文档](docs/architecture.md)，接口权限与状态码见 [API 文档](docs/api.md)。

## 快速启动

```powershell
cd D:\Projects\hireflow
.\.venv\Scripts\Activate.ps1
docker compose up --build -d
```

- Swagger UI：`http://127.0.0.1:8000/docs`
- Prometheus：`http://127.0.0.1:8000/metrics`
- RabbitMQ 管理台：`http://127.0.0.1:15672`（开发账号 `hireflow` / `hireflow_dev_only`）

## 开发与验收

```powershell
python -m pip install -e ".[dev]"
python -m ruff check src tests migrations
python -m pytest -q

.\scripts\demo.ps1
.\scripts\security_smoke.ps1
.\scripts\rate_limit_smoke.ps1
.\scripts\observability_smoke.ps1
.\scripts\concurrency_smoke.ps1
```

| 脚本 | 验证结果 |
| --- | --- |
| `demo.ps1` | 创建招聘流程、排期、确认状态和 4 条站内通知。 |
| `security_smoke.ps1` | 无 Token `401`、越权 `403`、跨租户读取 `404`。 |
| `rate_limit_smoke.ps1` | 连续 11 次无效登录，第 11 次 `429`。 |
| `observability_smoke.ps1` | `X-Request-ID` 回传，Prometheus HTTP 指标暴露。 |
| `concurrency_smoke.ps1` | 两位候选人并发抢唯一时段：恰好一个 `201`、一个 `409`。 |

## 关键一致性约束

```sql
EXCLUDE USING gist (
  interviewer_id WITH =,
  tstzrange(starts_at, ends_at, '[)') WITH &&
) WHERE (status IN ('scheduled', 'confirmed'));
```

应用层预检只用于快速返回友好错误；即使多个实例同时通过预检，最终仍只有一个重叠排期可提交，竞争请求返回 `409 Conflict`。

## 当前边界

- 通知为站内信模拟，尚未接入真实邮件供应商。
- Redis 当前用于限流，不承担排期正确性。
- 指标已暴露，尚未接入 Grafana 仪表盘与告警系统。
