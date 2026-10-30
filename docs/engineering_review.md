# HireFlow 工程审查记录

审查目标不是堆叠组件，而是确认关键业务约束是否有唯一、可验证的归属。

## 已收敛的关键决策

| 风险 | 当前设计 | 依据 |
| --- | --- | --- |
| 同一面试官重复排期 | PostgreSQL GiST 排他约束最终裁决 | 并发脚本稳定验证一个 `201`、一个 `409`。 |
| 跨租户数据泄露 | JWT 内携带 `tenant_id`，所有资源查询限定当前租户 | 安全脚本验证跨租户查询 `404`。 |
| 数据提交成功但消息未发送 | 业务事务内写 Outbox，Relay 异步投递 | 消除双写窗口，允许至少一次投递。 |
| Relay 并发抢占或 Broker 中断 | Go Relay 原子 `SKIP LOCKED` 抢占、Publisher Confirm、租约与持久化退避 | PostgreSQL 并发测试和 RabbitMQ 中断恢复脚本覆盖。 |
| 消息重复消费 | Inbox 和 Notification 的唯一键幂等 | Relay 重投不会重复创建站内信。 |
| 招聘决策并发覆盖或跨表不一致 | Application/Offer 状态机、版本乐观锁、审计与同事务 Outbox | ATS 脚本验证非法/过期更新 `409`、评分卡锁定、Offer 接受自动录用。 |
| 突发请求压垮认证与写接口 | Redis 固定窗口限流 | 限流脚本验证第 11 次失败登录返回 `429`。 |
| 重复列表查询增加数据库负载 | 租户级 Redis Cache Aside，提交后失效、TTL 兜底 | 单测覆盖隔离/失效/降级，Locust 对比数据库回源与热缓存。 |

## 当前有意保留的边界

1. 通知仅落库模拟，没有接入第三方邮件或短信供应商；因此不承诺真实外部投递成功率。
2. Redis 承担列表缓存与限流，但不参与排期正确性；短暂不可用时缓存读取 fail-open 回 PostgreSQL，不破坏 PostgreSQL 的一致性约束。
3. GitHub Actions 已运行静态检查、单元测试和完整 Docker 集成验收，包括 RLS、可靠消息、并发排期与缓存性能对比。

## 下一阶段优先级

1. **生产化配置**：用密钥管理替代 Compose 明文开发密码；配置 CORS、HTTPS 终止与日志采集。
2. **指标与告警**：制作 Grafana 面板，围绕缓存命中率、排期冲突率、Outbox 延迟、DLQ 堆积和限流命中率设置告警。
3. **缓存防击穿**：当真实流量证明热点 Key 重建竞争明显时，再引入短租约或请求合并，而不是提前增加分布式锁复杂度。
4. **通知通道**：接入邮件供应商，并把供应商回执与重放审计纳入通知状态机。
# HireFlow 工程化推进记录

## 阶段一：并发与数据一致性

- 使用 `scripts/load_test.ps1` 准备一个面试官、一个职位、多个候选人与唯一 30 分钟可用时段；随后由 Locust 同时提交排期请求。
- `201 Created` 与 `409 Conflict` 都被定义为压测中的有效业务响应；除这两类外均计为失败。压测结束必须满足：**仅一个 `201`，其余全部 `409`**。
- Locust 的汇总输出保留请求总数、RPS、平均延迟、P95 与 P99，作为每次环境/代码调整后的可比较性能证据。
- 正确性不依赖压测脚本或应用层预检：PostgreSQL `EXCLUDE USING gist` 排他约束仍是最终裁决。

## 阶段二：数据库级租户隔离

- API 使用非表所有者 `hireflow_app` 账号；该账号没有超级权限、建库权限、建角色权限或继承权限。
- PostgreSQL 对所有租户表启用并强制 RLS；每个 API 请求在验证 JWT 后设置 `app.tenant_id`，连接归还池前主动清空该变量。
- `scripts/rls_smoke.ps1` 用受限账号直连数据库验证：没有上下文无法读取；指定 A 租户后可读 A，仍不能读取 B。应用层 `tenant_id` 条件从此是易读性与错误语义保障，RLS 才是数据库最后防线。

## 阶段三：可靠消息与故障恢复

- `scripts/reliability_smoke.ps1` 在 Notification Consumer 容器内对真实 PostgreSQL 执行验收：同一事件重复消费只生成候选人和面试官各一条通知；这覆盖“提交成功后、ack 之前消费者宕机”导致的至少一次重投。
- 对毒消息保留 `notification_inbox` 中的失败证据，并验证 `failed → replay_requested → replay_queued → replay_requested` 的人工重放状态机；实际 Consumer 仍负责将失败消息按 5 秒重试队列转发至 DLQ。
- 对 Relay 故障验证 `publishing → pending(next_attempt_at)`，重试时间由指数退避持久化在 Outbox，而非仅存在进程内存。

## 阶段四：Redis 缓存与性能治理

- 职位与候选人列表采用 Cache Aside；Key 由版本、`tenant_id`、资源类型组成，避免跨租户串数据。创建与更新在数据库提交成功后删除当前租户 Key，TTL 默认 60 秒作为最终收敛边界。
- 热缓存命中路径不建立 PostgreSQL 会话；MISS、显式 `Cache-Control: no-cache` 或 Redis 故障才执行 RLS 约束下的数据库查询。Redis 读取异常 fail-open，业务正确性仍由 PostgreSQL 保证。
- Prometheus 记录 hit/miss/error/bypass、进程内命中率、数据库回源耗时和失效结果。单元测试覆盖 MISS→HIT、TTL、失效、跨租户 Key 与 Redis 故障降级。
- 一次本机 Locust 快照（10 用户、每类 20 条数据、每阶段 5 秒）中，数据库回源约 `208 RPS / P95 44ms / P99 100ms`，热缓存约 `295 RPS / P95 28ms / P99 48ms`。数据用于同环境回归，不外推为生产容量。

## 阶段五：分页与查询计划治理

- 职位、候选人、面试和通知列表使用“排序时间 + ID”的 Keyset 游标，默认 20、最大 100；保持数组响应兼容工作台，并以 `X-Next-Cursor` 暴露下一页边界。
- 默认首页进入 Cache Aside；游标页和非默认页大小走 PostgreSQL，控制缓存 Key 数量与写后失效复杂度。
- Alembic 为四类列表增加租户与排序字段复合索引。验收脚本以每类 5000 条事务内数据运行 `EXPLAIN (ANALYZE, BUFFERS)`，真实 SQLAlchemy 查询形状均命中目标索引，随后回滚样本并刷新基表统计信息。

## 阶段六：跨进程可观测性

- API、Go Outbox Relay、Python Notification Consumer 分别暴露 Prometheus 端点，避免把跨语言、跨进程指标错误聚合在同一个进程 Registry 中。
- API 记录排期冲突和数据库连接池 size/checked-in/checked-out/overflow；Relay 记录发布结果与 PostgreSQL Outbox pending/publishing/retry 积压；Consumer 记录处理、重试、DLQ、requeue 与失败/重放积压。
- `observability_smoke.ps1` 同时抓取三个端点并断言关键指标存在；业务积压 Gauge 由持久化状态刷新，进程重启后仍可恢复。

## 阶段七：健康检查分层

- `/health/live` 只验证进程可响应，避免数据库或缓存短暂抖动触发容器重启；`/health/ready` 才判断是否应继续接收业务流量。
- PostgreSQL 是同步事务关键依赖，不可用返回 `503 not_ready`；Redis 的缓存与限流允许 fail-open，因此不可用时返回 `200 degraded`。
- RabbitMQ 由 Transactional Outbox 与同步 API 解耦，不纳入 API readiness；故障通过 Relay 重试、积压指标和独立进程状态暴露。
- 探针仅返回规范化依赖状态，不泄露连接地址、凭据或异常文本；Compose API healthcheck 使用 readiness，验收脚本同时覆盖兼容接口、liveness 与 readiness。

## 阶段八：Go Outbox Relay

- 保留 FastAPI、RLS、排期和 Python Consumer，只将可靠消息 Relay 独立为 Go 服务，避免为了技术栈重写稳定业务。
- pgx 通过单条 CTE 原子完成 `SKIP LOCKED` 抢占、attempts 递增和租约设置；真实 PostgreSQL 并发测试验证 20 条事件被两个 worker 完整且不重复地领取。
- RabbitMQ Publisher Confirm 是标记 `published` 的前置条件；失败回写指数退避，进程崩溃由租约恢复，ACK 后数据库更新失败仍可能重投，因此继续使用 Inbox 幂等实现 At-least-once。
- `/live`、`/ready`、`/metrics` 覆盖进程、PostgreSQL、RabbitMQ、积压、结果及 Confirm 延迟；Broker 连接关闭事件会主动更新 readiness。
- 故障脚本验证 RabbitMQ 中断时 API 仍提交 Outbox、Relay 返回 `503`，事件保持未抢占 `pending` 或持久化退避；Broker 恢复后事件发布且 Consumer Inbox 只处理一次。
- 性能矩阵使用独立 Compose 项目和临时数据卷，不访问主环境队列或数据；Go/Python 在批大小 10/50/100 下各运行 3 轮，每轮 500 条，并记录吞吐、Confirm P95/P99、CPU 与内存。批大小 100 时平均吞吐约为 `42.93/36.63 events/s`，平均内存约为 `7.02/44.36MiB`；批大小 10 时二者均受 1 秒轮询主导。完整口径见 `docs/relay_benchmark_results.md`，结果只用于回归，不包装为语言极限或生产容量。

## 阶段九：ATS 招聘业务闭环

- Application 使用显式状态机和版本号乐观锁，每次合法迁移追加审计并写 Outbox；非法跳转和旧版本更新返回 `409`。
- 职位可配置有顺序、时长和评分维度的 Interview Round；面试可关联 Application 与 Round。
- 面试官评分卡支持草稿与最终提交，提交后不可修改；Offer 使用独立状态机与审计，接受后在同一事务内将 Application 推进至 `hired`。
- 新表全部启用并强制 PostgreSQL RLS；`ats_smoke.ps1` 覆盖主链路、评分卡锁定、审计、通知和跨租户不可见，`notification_idempotency_smoke.ps1` 额外验证 ATS 事件跨事务重复投递只产生一次副作用。
