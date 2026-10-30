# HireFlow 面试讲解稿

## 60 秒项目介绍

HireFlow 是我独立实现的多租户 ATS 与面试调度平台，覆盖职位、候选人、申请阶段、结构化面试、评分卡、Offer 和通知。它解决的核心问题不是简单 CRUD，而是招聘系统里最容易出错的并发排期、并发决策覆盖、跨租户隔离和可靠事件投递。

我使用 FastAPI 和 PostgreSQL 实现主业务，时间统一以 UTC 保存、用半开区间计算重叠；应用层先做冲突预检以返回友好的错误，但最终通过 PostgreSQL 的 GiST 排他约束保证多实例并发下只有一个事务提交。并发脚本实际验证了两个请求抢唯一时段时得到一个 201 和一个 409。

通知方面，我采用 Transactional Outbox：面试变更与 Outbox 事件在一个数据库事务中提交，独立 Go Relay 使用 pgx 原子抢占、RabbitMQ Publisher Confirm、租约和指数退避完成可靠投递。Python Consumer 用 Inbox 事件 ID 去重，支持重试、死信与 Owner 人工重放。项目还加入 JWT/RBAC、数据库 RLS、Redis Cache Aside 与限流、请求 ID、结构化日志，并分别暴露 API、Relay、Consumer 的 Prometheus 指标，以 Docker Compose 一键运行。

申请和 Offer 使用显式状态机、版本号乐观锁与审计表；评分卡最终提交后不可修改。接受 Offer 时，Offer、Application、双方审计和 Outbox 事件在同一事务内完成，避免出现“Offer 已接受但候选人仍停留在面试阶段”的跨表不一致。

## 重点追问

### 为什么不用 Redis 锁保证排期？

Redis 锁可减少冲突，但 Redis 不是排期事实来源，也无法替代数据库最终约束。HireFlow 的 PostgreSQL 排他约束直接约束写入数据；Redis 只用于列表缓存和可用性保护的限流，故障时缓存读取回源数据库。

### 为什么缓存命中时不能仍然创建数据库会话？

如果 FastAPI 在进入缓存判断前就初始化 RLS 会话，即使命中 Redis 也会产生数据库连接和 `set_config` 往返，缓存收益会被抵消。HireFlow 将租户数据库会话延迟到 MISS loader 内部；本机 Locust 对比中，热缓存吞吐约提升 42%，同时 P95/P99 均下降。

### 推荐时段为什么仍可能创建失败？

推荐是读时计算，不是预占。两个用户可同时读到唯一空闲时段；第一个写入提交后，第二个必须返回 409 并提示刷新。把最终一致性边界放在数据库写入处，才能覆盖多实例竞争。

### Outbox 如何处理重复消息？

Relay 可能在“已发布、未标记 published”时崩溃，因此语义是至少一次。消费者将 `(consumer_name, event_id)` 持久化到 Inbox，并与通知副作用在同一事务内提交；重投后发现事件已处理便直接确认。

### 为什么只用 Go 重写 Relay？

FastAPI 主业务已经稳定，招聘流程、RLS 和排期约束并不会因为换语言更可靠；Relay 则边界窄、I/O 并发明显，适合用 Go 展示 goroutine、context、pgx 连接池、优雅停机与静态二进制部署。这样得到真实的多语言架构收益，同时避免为了技术栈制造大规模重写风险。

### Publisher Confirm 后为什么还会重复？

Broker ACK 与 PostgreSQL 状态更新不是一个原子事务。如果消息已确认而 Relay 在更新 `published` 前崩溃，租约到期后会再次投递。项目明确承诺 At-least-once，并用稳定 `message_id` 与 Inbox 唯一键保证重复消息不产生重复通知。

### 为什么 Application 和 Offer 还需要乐观锁？

状态机只能限制“哪些跳转合法”，不能阻止两个招聘人员基于同一个旧页面同时做决定。更新请求携带读取时的 `version`，SQL 更新同时匹配 ID、租户和版本；只有一个事务能成功，另一个收到 `409` 后刷新，避免后提交者静默覆盖先提交者。

### 为什么评分卡提交后不可修改？

评分卡是招聘决策证据。草稿允许面试官补充内容，最终提交后服务端锁定，避免看到其他面试官结论后反向修改历史评价。需要更正时应追加新的审计流程，而不是覆盖原始证据。

### 目前有哪些边界？

通知是站内信模拟，未对接邮件供应商；Prometheus/Grafana 已提供本地面板与示例告警，但阈值尚未经过生产流量校准；缓存未实现热点 Key 请求合并，限流仍使用固定窗口，生产场景可根据流量演进。

### 为什么要区分 liveness 和 readiness？

Liveness 只回答进程是否活着，依赖抖动时不应通过重启制造更大压力；readiness 回答实例是否适合接流量。HireFlow 将 PostgreSQL 视为关键依赖，不可用返回 503；Redis 故障可回源数据库，因此返回 degraded 但保持就绪。RabbitMQ 由 Outbox 解耦，不阻断同步 API，故障通过 Relay 重试与积压指标暴露。
