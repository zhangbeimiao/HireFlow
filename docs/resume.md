# HireFlow 简历描述

**HireFlow｜多租户招聘协同与面试调度平台**  
技术栈：Python、Go、FastAPI、PostgreSQL、Redis、RabbitMQ、Docker、Prometheus、pytest

- 独立设计多租户 ATS，覆盖职位、候选人、申请状态机、结构化面试、评分卡、Offer 与审计闭环；通过 JWT/RBAC 与 PostgreSQL RLS 构建应用层和数据库双重租户隔离。
- 针对多人并发抢占面试时段，采用候选人/面试官可用区间计算、应用层预检与 PostgreSQL GiST 排他约束；压测中 10 个请求竞争唯一时段时仅 1 个成功，其余稳定返回 `409`。
- 基于 Transactional Outbox、Go Relay、RabbitMQ Publisher Confirm、租约与指数退避实现可靠投递，Consumer 使用 Inbox 去重并支持重试、死信和人工重放；统一故障演练覆盖 Broker、缓存、数据库及进程重启恢复。
- 使用 Redis Cache Aside、租户级 Key、写后失效、TTL、Keyset 分页和复合索引治理读性能；接入 Prometheus 指标与 CI 安全扫描，建立 42 个单元测试和 4 个真实 API 集成测试的回归基线。

> 所有吞吐和延迟数据均来自本机 Docker Compose 回归环境，只用于方案验证，不表述为生产容量。
