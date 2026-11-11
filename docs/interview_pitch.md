# HireFlow 面试讲解稿

## 60 秒项目介绍

HireFlow 是我独立实现的多租户 ATS 与面试调度平台，覆盖职位、候选人、申请阶段、结构化面试、评分卡、Offer 和通知。它解决的核心问题不是简单 CRUD，而是招聘系统里最容易出错的并发排期、并发决策覆盖、跨租户隔离和可靠事件投递。

我使用 FastAPI 和 PostgreSQL 实现主业务，时间统一以 UTC 保存、用半开区间计算重叠；应用层先做冲突预检以返回友好的错误，但最终通过 PostgreSQL 的 GiST 排他约束保证多实例并发下只有一个事务提交。并发脚本实际验证了两个请求抢唯一时段时得到一个 201 和一个 409。

通知方面，我采用 Transactional Outbox：面试变更与 Outbox 事件在一个数据库事务中提交，独立 Go Relay 使用 pgx 原子抢占、RabbitMQ Publisher Confirm、租约和指数退避完成可靠投递。Python Consumer 用 Inbox 事件 ID 去重，支持重试、死信、人工重放和 SMTP 投递。项目还加入 JWT/RBAC、数据库 RLS、Redis Cache Aside 与限流、请求 ID、结构化日志，并分别暴露 API、Relay、Consumer 的 Prometheus 指标，以 Docker Compose 一键运行。

申请和 Offer 使用显式状态机、版本号乐观锁与审计表；评分卡最终提交后不可修改。接受 Offer 时，Offer、Application、双方审计和 Outbox 事件在同一事务内完成，避免出现“Offer 已接受但候选人仍停留在面试阶段”的跨表不一致。

## 重点追问

### 为什么不用 Redis 锁保证排期？

Redis 锁可减少冲突，但 Redis 不是排期事实来源，也无法替代数据库最终约束。HireFlow 的 PostgreSQL 排他约束直接约束写入数据；Redis 只用于列表缓存和可用性保护的限流，故障时缓存读取回源数据库。

### 为什么缓存命中时不能仍然创建数据库会话？

如果 FastAPI 在进入缓存判断前就初始化 RLS 会话，即使命中 Redis 也会产生数据库连接和 `set_config` 往返，缓存收益会被抵消。HireFlow 将租户数据库会话延迟到 MISS loader 内部；本机 Locust 对比中，热缓存吞吐约提升 42%，同时 P95/P99 均下降。

### 多实例下如何避免缓存击穿？

本地锁只能约束单进程，因此 HireFlow 用带短租约的 Redis `SET NX` 作为跨实例重建锁，未拿到锁的请求短暂等待并读取首个实例写入的结果。隔离压测通过 Nginx 将 30 个同步冷读分散到 3 个 API 容器，实例响应头证明三者都接到请求，最终只有 1 个 `MISS` 和 1 次数据库回源，回源放大倍数为 `1×`。

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

当前版本已验证排期一致性、可靠投递和幂等消费；后台最小权限、事件严格排序和 Outbox 生命周期治理属于规模化部署前的下一阶段演进，已记录为明确的工程待办。

SMTP Provider、模板、投递记录和失败重试已实现，本地使用 Mailpit 验收，但没有绑定真实生产供应商账号和投递 SLA；TLS 演练使用一次性测试 CA，生产仍需接入托管 PKI 和证书轮换；备份演练目前验证逻辑备份恢复，尚未覆盖 WAL 归档与时间点恢复；限流已使用 Redis Token Bucket，但可信代理 CIDR、容量和分层配额仍需结合真实流量校准。

### Refresh Token 为什么要做 Family？

浏览器只把短期 Access Token 放在内存/短期存储中，长期 Refresh Token 放在 `HttpOnly + Secure + SameSite=Strict` Cookie，刷新和退出使用 CSRF 双提交。每次刷新都会轮换 Token 并继承同一 `family_id`；如果旧 Token 再次出现，服务端将其判断为重放并撤销整个 Family，避免攻击者拿着旧 Token 与合法浏览器长期竞争。

### 如何证明 TLS 和备份不是只写了配置？

`tls_smoke.ps1` 在一次性 Docker 卷中生成 CA、PostgreSQL/RabbitMQ 服务端证书和客户端证书，实际执行 PostgreSQL `verify-full` 查询与 RabbitMQ 双向 TLS 1.3 握手。`backup_restore_smoke.ps1` 对业务库做 custom-format 逻辑备份，恢复到随机临时库，再逐表比较 20 张表的精确行数和确定性内容哈希，最后只清理经过前缀校验的临时库。

### 为什么要区分 liveness 和 readiness？

Liveness 只回答进程是否活着，依赖抖动时不应通过重启制造更大压力；readiness 回答实例是否适合接流量。HireFlow 将 PostgreSQL 视为关键依赖，不可用返回 503；Redis 故障可回源数据库，因此返回 degraded 但保持就绪。RabbitMQ 由 Outbox 解耦，不阻断同步 API，故障通过 Relay 重试与积压指标暴露。

### 你如何证明故障恢复不是只写在设计文档里？

项目提供统一的 `failure_drill.ps1`：依次重启 Relay 和 Consumer、模拟重复投递与人工重放、中断并恢复 RabbitMQ、停止 Redis 验证回源 PostgreSQL，再停止 PostgreSQL 验证 readiness 返回 503 而 liveness 仍为 200。最后脚本检查依赖恢复、Outbox 最终发布以及 Inbox 副作用不重复。它和 8 个真实 API 集成测试都会在 Docker CI 环境执行。

### 项目如何保证测试结果可复现？

开发环境由 Docker Compose 固化，Alembic 脚本在独立空数据库执行 `head → base → head`，黑盒测试通过真实 HTTP 接口而不是直接调用 Service。当前 Python 回归基线为 `67 passed, 12 skipped`；Docker CI 另执行 11 个真实集成测试和 1 条 Playwright E2E 流程，并覆盖 Ruff、Go race test、RLS、并发 `201/409`、多实例热点 Key、TLS、备份恢复、故障演练及依赖/镜像安全扫描。

## 三个深挖题口述卡

### 并发排期为什么不会重复成功？

接口先在当前租户内检查候选人和面试官的活动面试，目的是尽早返回可读错误；真正的并发裁决由 PostgreSQL GiST 排他约束完成。约束使用 `tenant_id + resource_id + tstzrange(starts_at, ends_at, '[)')`，所以相邻两场可以共存，重叠写入只有一个事务能提交。服务只把 SQLSTATE `23P01` 映射为 `409`，其他完整性错误不会伪装成排期冲突。面试与 Outbox 在同一事务提交，因此写事件失败时排期也会回滚。

### RLS 如何避免连接池串租户？

JWT 验签后提取签名内的 `tenant_id`，API 在显式事务中用 `set_config('app.tenant_id', tenant_id, true)` 设置事务级上下文；RLS 同时配置 `USING` 和 `WITH CHECK`。提交或回滚后上下文自动失效，复用连接不会继承上一请求的租户。应用查询仍显式带 `tenant_id` 以获得清晰语义，核心父子表再用 `(tenant_id, id)` 复合外键防止跨租户脏关联。迁移账号与后台服务使用独立权限边界，不接受客户端传入租户上下文。

### 消息可靠性究竟是什么语义？

业务数据与 Outbox 在同一 PostgreSQL 事务落库，Go Relay 用 `FOR UPDATE SKIP LOCKED` 多实例领取，`lease_owner` 是 fencing token，慢 Confirm 时为批次尾部续租。RabbitMQ 发布同时使用持久化消息、Publisher Confirm 和 `mandatory + NotifyReturn`，避免 Broker ACK 但消息未路由。若 ACK 后 Relay 在标记 published 前崩溃，事件会重投，所以端到端明确是 at-least-once；Consumer 以 `(consumer_name, event_id)` 唯一键实现 Inbox 幂等。Outbox 解决不丢消息，Inbox 解决重复副作用，两者都不宣称 exactly-once。
