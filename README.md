# HireFlow｜多租户招聘面试调度与协同平台

[![CI](https://github.com/zhangbeimiao/HireFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/zhangbeimiao/HireFlow/actions/workflows/ci.yml)

HireFlow 面向中小企业招聘场景，解决候选人与面试官协调时间、并发抢占同一面试时段等高风险问题。项目将排期一致性、权限隔离和异步通知拆为可验证的后端闭环。

## 技术栈

`Python 3.11+` · `Go 1.27` · `FastAPI` · `pgx` · `PostgreSQL 16 + RLS` · `SQLAlchemy Async` · `Alembic` · `Redis` · `RabbitMQ` · `Docker Compose` · `pytest` · `Go test` · `Locust` · `Prometheus`

## 已实现能力

- **多租户与权限**：JWT 签发 `tenant_id` 与角色；Owner、Recruiter、Hiring Manager、Interviewer 由 RBAC 校验，跨租户资源返回 `404`；API 采用受限数据库账号，PostgreSQL RLS 作为数据隔离最终防线。
- **可靠排期**：候选人与面试官时段取交集并扣除活动面试；使用半开区间 `[start, end)`，PostgreSQL GiST 排他约束是并发写入最终裁决。
- **状态与审计**：状态机、乐观锁版本号和状态变更审计记录。
- **Go 可靠消息 Relay**：事务内写 Outbox；独立 Go 服务通过 pgx、`FOR UPDATE SKIP LOCKED`、租约和 Publisher Confirm 实现并发抢占、持久化退避及故障恢复；Python Relay 仅作为可选回退。
- **幂等通知**：Python Consumer 的 Inbox 按 `(consumer_name, event_id)` 去重，支持重试队列、DLQ 和 Owner 人工重放。
- **查询与缓存治理**：职位、候选人、面试与通知列表采用稳定的 Keyset/Cursor 分页和租户复合索引；职位/候选人默认热点首页使用 Redis Cache Aside，写后失效并设置 TTL。
- **招聘工作台**：内置中文 Web 控制台，支持初始化/登录团队、新建职位、录入候选人和安排面试；排期冲突在页面直观呈现为 `409 Conflict`，Swagger 保留为开发者接口文档。

## 架构

```text
Client → FastAPI (JWT/RBAC, Redis Cache Aside + rate limit) → PostgreSQL
                    │                                  ├─ interviews + audit + outbox
                    │                                  └─ notification inbox + in-app notifications
                    ▼
          Go Outbox Relay → RabbitMQ → Python Notification Consumer
          (pgx + Confirm)          ├─ retry queue (5s)
                                   └─ DLQ + Owner replay
```

更多设计取舍见 [架构文档](docs/architecture.md)，接口权限与状态码见 [API 文档](docs/api.md)。

## 运行截图

真实运行截图的采集步骤与脱敏要求见 [截图说明](docs/screenshots/README.md)。建议至少展示 Docker 服务栈、Swagger、RabbitMQ 队列、Prometheus 指标及并发排期 `201/409` 结果。

## 快速启动

```powershell
cd D:\Projects\hireflow
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
# 编辑 .env，将所有 replace-with-* 值替换为本机随机值后再启动。
# Docker Desktop 已启动、但当前 PowerShell 找不到 docker 时执行一次：
$env:Path += ";C:\Program Files\Docker\Docker\resources\bin"
docker compose up --build -d
.\scripts\stack_smoke.ps1
```

- Swagger UI：`http://127.0.0.1:8000/docs`
- 招聘工作台：`http://127.0.0.1:8000/`
- 存活探针：`http://127.0.0.1:8000/health/live`
- 就绪探针：`http://127.0.0.1:8000/health/ready`
- API Prometheus：`http://127.0.0.1:8000/metrics`
- Outbox Relay Prometheus：`http://127.0.0.1:9101/metrics`
- Notification Consumer Prometheus：`http://127.0.0.1:9102/metrics`
- RabbitMQ 管理台：`http://127.0.0.1:15672`（账号 `hireflow`，密码来自本机 `.env`）

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
.\scripts\go_relay_recovery_smoke.ps1
.\scripts\rls_smoke.ps1
.\scripts\cache_smoke.ps1
.\scripts\pagination_smoke.ps1
.\scripts\explain_analyze.ps1
# 需要先执行 python -m pip install -e ".[dev]"，默认 20 个并发用户
.\scripts\load_test.ps1 -Users 20 -SpawnRate 10
# 对比强制 PostgreSQL 回源与 Redis 热缓存的 RPS、P95、P99
.\scripts\cache_performance.ps1 -Users 10 -SpawnRate 10 -RunSeconds 10
# 在独立 Compose 项目中对比 Go/Python Relay，不访问主环境数据
.\scripts\relay_performance.ps1 -Events 500
# 3 轮 × 批大小 10/50/100，采集吞吐、P95/P99、CPU 和内存
.\scripts\relay_performance_matrix.ps1 -Events 500 -Runs 3 -BatchSizes 10,50,100
```

CI 还会运行 `pip-audit`、`govulncheck`、Gitleaks Git 历史扫描、Trivy 源码/镜像扫描，并验证 API、Consumer 与 Go Relay 均以非 root 用户运行。应用不启用宽松 CORS；允许的 Host、数据库/RabbitMQ/Grafana 密码和 JWT 密钥必须通过环境注入。

| 脚本 | 验证结果 |
| --- | --- |
| `demo.ps1` | 创建招聘流程、排期、确认状态和 4 条站内通知。 |
| `workbench_smoke.ps1` | 验证工作台依赖的列表、可用时段推荐、dashboard 聚合与排期 `201/409`。 |
| `stack_smoke.ps1` | 检查 Compose 服务、兼容健康接口、进程 liveness 与 PostgreSQL/Redis readiness。 |
| `security_smoke.ps1` | 无 Token `401`、越权 `403`、跨租户读取 `404`。 |
| `rate_limit_smoke.ps1` | 连续 11 次无效登录，第 11 次 `429`。 |
| `observability_smoke.ps1` | 验证请求 ID，以及 API/连接池、排期冲突、Outbox、通知失败三类进程指标。 |
| `concurrency_smoke.ps1` | 两位候选人并发抢唯一时段：恰好一个 `201`、一个 `409`。 |
| `load_test.ps1` | Locust 并发压测同一唯一时段；输出吞吐、P95/P99，并要求恰好一个 `201`、其余为预期 `409`。 |
| `reliability_smoke.ps1` | 验证 Inbox 重复投递幂等、提交后重投、失败证据/人工重放状态与 Outbox 持久化重试。 |
| `go_relay_recovery_smoke.ps1` | 主动中断 RabbitMQ，验证 Go Relay readiness、Outbox 可恢复状态、自动重连与 Inbox 单次副作用。 |
| `rls_smoke.ps1` | 以 API 受限数据库账号直连验证：无租户上下文为 0 行；A 可读 A、不可读 B。 |
| `cache_smoke.ps1` | 验证职位/候选人列表 MISS→HIT、创建/更新后失效、租户级 Key、TTL 与缓存指标。 |
| `pagination_smoke.ps1` | 验证职位/候选人 Keyset 分页 `20 + 3` 条无重复遗漏、非法游标 `422` 及首页缓存策略。 |
| `explain_analyze.ps1` | 事务内生成每表 5000 条数据，验证游标查询命中租户复合索引并回滚测试数据。 |
| `cache_performance.ps1` | Locust 对比 `Cache-Control: no-cache` 数据库回源与热缓存，输出 RPS、P95、P99。 |
| `relay_performance.ps1` | 在隔离 PostgreSQL/RabbitMQ 中以同批大小和轮询周期对比 Go/Python Relay 吞吐、Confirm P95/P99 与内存。 |
| `relay_performance_matrix.ps1` | Go/Python 各跑 3～5 轮和批大小 10/50/100，输出平均吞吐、P95/P99、CPU、内存及 Markdown 报告。 |

一次本机快照（10 用户、每类 20 条数据、每阶段 5 秒）中，热缓存相对数据库回源的吞吐由约 `208 RPS` 提升到 `295 RPS`，P95 从 `44ms` 降至 `28ms`，P99 从 `100ms` 降至 `48ms`。该数据仅用于同机型回归对比，不代表生产容量。

分页以排序时间和 `id` 组成稳定游标，避免深分页 `OFFSET` 随偏移量增长而扫描和丢页。在每类 5000 条事务内样本上，职位、候选人、面试和通知查询均选择目标复合索引；完整计划见 [查询性能记录](docs/query_performance.md)，数值仅用于证明执行计划与回归对比。

Go/Python Relay 在 Windows Docker Desktop、14 逻辑 CPU 上各运行 3 轮，每轮 500 条事件，结果如下；完整环境和口径见 [Relay 性能报告](docs/relay_benchmark_results.md)。

| 实现 | 批大小 | 平均吞吐 (events/s) | 平均 P95/P99 (ms) | 平均 CPU | 平均内存 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Go | 10 | 8.45 | 25 / 25 | 0.47% | 6.46 MiB |
| Python | 10 | 8.27 | 25 / 25 | 2.28% | 43.45 MiB |
| Go | 50 | 30.71 | 10 / 20 | 1.26% | 7.31 MiB |
| Python | 50 | 28.29 | 25 / 25 | 5.65% | 44.05 MiB |
| Go | 100 | 42.93 | 10 / 15 | 1.58% | 7.02 MiB |
| Python | 100 | 36.63 | 25 / 25 | 9.66% | 44.36 MiB |

批大小 10 时两者都受 1 秒轮询主导；批大小提高后 Go 的吞吐、尾延迟和资源占用优势才更明显。数据仅用于同机回归，不外推为生产容量或语言极限。

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
- API readiness 将 PostgreSQL 视为关键依赖；Redis 不可用时返回 `degraded` 但仍接流量，RabbitMQ 由 Outbox 解耦而不阻断同步 API。
- 默认 Compose 使用 Go Relay；Python Relay 保留在 `python-relay` profile 中用于回归对比和紧急回退，不允许两者在同一环境长期同时运行。
- 可选 Prometheus/Grafana 观测栈已预置 Relay 积压、吞吐、Confirm P95/P99、重试和断连面板，以及积压、失败率、重试、断连和目标不可达告警。启动方式、指标样例、滚动升级与 TLS/密钥建议见 [Relay 运维说明](docs/relay_operations.md)。

下一轮工程化优先级见 [工程审查记录](docs/engineering_review.md)。
