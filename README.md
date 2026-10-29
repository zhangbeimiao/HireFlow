# HireFlow｜多租户招聘面试调度与协同平台

[![CI](https://github.com/zhangbeimiao/HireFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/zhangbeimiao/HireFlow/actions/workflows/ci.yml)

HireFlow 面向中小企业招聘场景，解决候选人与面试官协调时间、并发抢占同一面试时段等高风险问题。项目将排期一致性、权限隔离和异步通知拆为可验证的后端闭环。

## 技术栈

`Python 3.11+` · `FastAPI` · `PostgreSQL 16 + RLS` · `SQLAlchemy Async` · `Alembic` · `Redis` · `RabbitMQ` · `Docker Compose` · `pytest` · `Locust` · `Prometheus`

## 已实现能力

- **多租户与权限**：JWT 签发 `tenant_id` 与角色；Owner、Recruiter、Hiring Manager、Interviewer 由 RBAC 校验，跨租户资源返回 `404`；API 采用受限数据库账号，PostgreSQL RLS 作为数据隔离最终防线。
- **可靠排期**：候选人与面试官时段取交集并扣除活动面试；使用半开区间 `[start, end)`，PostgreSQL GiST 排他约束是并发写入最终裁决。
- **状态与审计**：状态机、乐观锁版本号和状态变更审计记录。
- **可靠通知**：事务内写 Outbox；Relay 用 `FOR UPDATE SKIP LOCKED` 发布事件；Inbox 按 `(consumer_name, event_id)` 幂等消费，支持重试、DLQ 和 Owner 重放。
- **缓存与运行保护**：职位、候选人列表采用租户级 Redis Cache Aside，写后失效并设置 TTL；Redis 同时承担固定窗口限流，请求 ID、结构化日志和 Prometheus 指标覆盖运行状态。
- **招聘工作台**：内置中文 Web 控制台，支持初始化/登录团队、新建职位、录入候选人和安排面试；排期冲突在页面直观呈现为 `409 Conflict`，Swagger 保留为开发者接口文档。

## 架构

```text
Client → FastAPI (JWT/RBAC, Redis Cache Aside + rate limit) → PostgreSQL
                    │                                  ├─ interviews + audit + outbox
                    │                                  └─ notification inbox + in-app notifications
                    ▼
          Outbox Relay → RabbitMQ → Notification Consumer
                              ├─ retry queue (5s)
                              └─ DLQ + Owner replay
```

更多设计取舍见 [架构文档](docs/architecture.md)，接口权限与状态码见 [API 文档](docs/api.md)。

## 运行截图

真实运行截图的采集步骤与脱敏要求见 [截图说明](docs/screenshots/README.md)。建议至少展示 Docker 服务栈、Swagger、RabbitMQ 队列、Prometheus 指标及并发排期 `201/409` 结果。

## 快速启动

```powershell
cd D:\Projects\hireflow
.\.venv\Scripts\Activate.ps1
# Docker Desktop 已启动、但当前 PowerShell 找不到 docker 时执行一次：
$env:Path += ";C:\Program Files\Docker\Docker\resources\bin"
docker compose up --build -d
.\scripts\stack_smoke.ps1
```

- Swagger UI：`http://127.0.0.1:8000/docs`
- 招聘工作台：`http://127.0.0.1:8000/`
- Prometheus：`http://127.0.0.1:8000/metrics`
- RabbitMQ 管理台：`http://127.0.0.1:15672`（开发账号 `hireflow` / `hireflow_dev_only`）

## 开发与验收

```powershell
python -m pip install -e ".[dev]"
python -m ruff check src tests migrations
python -m pytest -q

.\scripts\demo.ps1
.\scripts\workbench_smoke.ps1
.\scripts\stack_smoke.ps1
.\scripts\security_smoke.ps1
.\scripts\rate_limit_smoke.ps1
.\scripts\observability_smoke.ps1
.\scripts\concurrency_smoke.ps1
.\scripts\reliability_smoke.ps1
.\scripts\rls_smoke.ps1
.\scripts\cache_smoke.ps1
# 需要先执行 python -m pip install -e ".[dev]"，默认 20 个并发用户
.\scripts\load_test.ps1 -Users 20 -SpawnRate 10
# 对比强制 PostgreSQL 回源与 Redis 热缓存的 RPS、P95、P99
.\scripts\cache_performance.ps1 -Users 10 -SpawnRate 10 -RunSeconds 10
```

| 脚本 | 验证结果 |
| --- | --- |
| `demo.ps1` | 创建招聘流程、排期、确认状态和 4 条站内通知。 |
| `workbench_smoke.ps1` | 验证工作台依赖的列表、可用时段推荐、dashboard 聚合与排期 `201/409`。 |
| `stack_smoke.ps1` | 检查 Compose 关键服务处于运行态，并验证 API `/health`。 |
| `security_smoke.ps1` | 无 Token `401`、越权 `403`、跨租户读取 `404`。 |
| `rate_limit_smoke.ps1` | 连续 11 次无效登录，第 11 次 `429`。 |
| `observability_smoke.ps1` | `X-Request-ID` 回传，Prometheus HTTP 指标暴露。 |
| `concurrency_smoke.ps1` | 两位候选人并发抢唯一时段：恰好一个 `201`、一个 `409`。 |
| `load_test.ps1` | Locust 并发压测同一唯一时段；输出吞吐、P95/P99，并要求恰好一个 `201`、其余为预期 `409`。 |
| `reliability_smoke.ps1` | 验证 Inbox 重复投递幂等、提交后重投、失败证据/人工重放状态与 Outbox 持久化重试。 |
| `rls_smoke.ps1` | 以 API 受限数据库账号直连验证：无租户上下文为 0 行；A 可读 A、不可读 B。 |
| `cache_smoke.ps1` | 验证职位/候选人列表 MISS→HIT、创建/更新后失效、租户级 Key、TTL 与缓存指标。 |
| `cache_performance.ps1` | Locust 对比 `Cache-Control: no-cache` 数据库回源与热缓存，输出 RPS、P95、P99。 |

一次本机快照（10 用户、每类 20 条数据、每阶段 5 秒）中，热缓存相对数据库回源的吞吐由约 `208 RPS` 提升到 `295 RPS`，P95 从 `44ms` 降至 `28ms`，P99 从 `100ms` 降至 `48ms`。该数据仅用于同机型回归对比，不代表生产容量。

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
- Redis 用于列表缓存和限流，但不承担排期正确性；Redis 故障时列表读取降级回 PostgreSQL，排期始终由 PostgreSQL 约束裁决。
- 指标已暴露，尚未接入 Grafana 仪表盘与告警系统。

下一轮工程化优先级见 [工程审查记录](docs/engineering_review.md)。
