# HireFlow 工程审查记录

审查目标不是堆叠组件，而是确认关键业务约束是否有唯一、可验证的归属。

## 已收敛的关键决策

| 风险 | 当前设计 | 依据 |
| --- | --- | --- |
| 同一面试官重复排期 | PostgreSQL GiST 排他约束最终裁决 | 并发脚本稳定验证一个 `201`、一个 `409`。 |
| 跨租户数据泄露 | JWT 内携带 `tenant_id`，所有资源查询限定当前租户 | 安全脚本验证跨租户查询 `404`。 |
| 数据提交成功但消息未发送 | 业务事务内写 Outbox，Relay 异步投递 | 消除双写窗口，允许至少一次投递。 |
| 消息重复消费 | Inbox 和 Notification 的唯一键幂等 | Relay 重投不会重复创建站内信。 |
| 突发请求压垮认证与写接口 | Redis 固定窗口限流 | 限流脚本验证第 11 次失败登录返回 `429`。 |

## 当前有意保留的边界

1. `/health` 是进程就绪探针；数据库、Redis、RabbitMQ 分别由 Compose 健康检查和业务验收脚本覆盖。生产环境可拆为 liveness/readiness，并在 readiness 中检查必要依赖。
2. 通知仅落库模拟，没有接入第三方邮件或短信供应商；因此不承诺真实外部投递成功率。
3. Redis 仅做限流，不参与排期正确性；其短暂不可用不应破坏 PostgreSQL 的一致性约束。
4. CI 当前运行静态检查与单元测试；Docker 全链路验证依赖本地脚本，后续可在 GitHub Actions 增加 service containers 或 Compose job。

## 下一阶段优先级

1. **CI 集成验证**：在 GitHub Actions 启动 PostgreSQL、Redis、RabbitMQ，自动运行安全与并发验收。
2. **生产化配置**：用密钥管理替代 Compose 明文开发密码；配置 CORS、HTTPS 终止与日志采集。
3. **指标与告警**：制作 Grafana 面板，围绕排期冲突率、Outbox 延迟、DLQ 堆积和限流命中率设置告警。
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

- `scripts/reliability_smoke.ps1` 在 Worker 容器内对真实 PostgreSQL 执行验收：同一事件重复消费只生成候选人和面试官各一条通知；这覆盖“提交成功后、ack 之前消费者宕机”导致的至少一次重投。
- 对毒消息保留 `notification_inbox` 中的失败证据，并验证 `failed → replay_requested → replay_queued → replay_requested` 的人工重放状态机；实际 Consumer 仍负责将失败消息按 5 秒重试队列转发至 DLQ。
- 对 Relay 故障验证 `publishing → pending(next_attempt_at)`，重试时间由指数退避持久化在 Outbox，而非仅存在进程内存。
