# HireFlow 简历描述

**HireFlow｜多租户招聘协同与面试调度平台**  
技术栈：Python、Go、FastAPI、PostgreSQL、Redis、RabbitMQ、Nginx、Docker、Prometheus、pytest

- 独立设计多租户 ATS，覆盖职位、候选人、申请状态机、面试轮次、评分卡、Offer 与审计闭环；采用 JWT/RBAC、事务级租户上下文、PostgreSQL RLS 与复合外键，兼顾友好错误语义和数据库级隔离。
- 针对候选人与面试官并发撞期，使用半开时间区间、应用层预检和 PostgreSQL GiST 排他约束分层处理；10 个请求竞争唯一时段时仅 1 个提交成功，其余稳定返回 `409`，最终一致性由数据库裁决。
- 基于 Transactional Outbox、Go Relay、RabbitMQ Publisher Confirm、mandatory return、租约续期与 owner fencing 实现至少一次投递；Consumer 通过 Inbox 去重，故障演练验证 Broker 中断恢复后消息不丢失且副作用不重复。
- 使用 Redis Cache Aside、租户级 Key 和短租约合并热点回源，3 个 API 实例下 30 个冷 Key 并发请求仅触发 1 次数据库查询；建立 `67 passed, 12 skipped`、11 个真实集成测试及 1 条 Playwright E2E 的回归证据。

> 所有吞吐和延迟数据均来自本机 Docker Compose 回归环境，只用于方案验证，不表述为生产容量。
