# HireFlow 5 分钟可靠性 Demo

这套演示不介绍所有功能，只证明四个核心结论：并发写入由数据库最终裁决、消息在 Broker 故障后可恢复、租户数据不能越权读取、关键链路可被指标观测。

## 演示前准备

不要在演示现场重新构建镜像。提前启动并确认服务就绪：

```powershell
cd D:\Projects\hireflow
$env:Path += ";C:\Program Files\Docker\Docker\resources\bin"
docker compose up -d --wait
Invoke-RestMethod http://localhost:8000/health/ready
```

正式演示只运行一条命令：

```powershell
.\scripts\reliability_demo.ps1
```

脚本只创建带时间戳的隔离演示租户，不删除业务数据。RabbitMQ 故障步骤使用 `finally` 恢复 Broker、Relay 和 Consumer。

## 讲解顺序

### 0:00—0:30｜问题和架构

> HireFlow 是面向中小企业的多租户 ATS。我不展开所有页面，今天只演示四个最容易出事故的后端问题：并发排期、可靠消息、跨租户隔离和可观测性。

一句话架构：FastAPI 在 PostgreSQL 事务内写业务记录与 Outbox；Go Relay 投递 RabbitMQ；Python Consumer 用 Inbox 幂等处理；三个进程分别暴露 Prometheus 指标。

### 0:30—1:30｜并发排期得到 `201 + 409`

脚本让两个请求同时抢同一位面试官的最后一个可用时段。预期输出：

```text
first_request  : 201
second_request : 409
```

> 应用层推荐时段不等于预占。两个请求都可能通过预检，最终由 PostgreSQL GiST 排他约束原子裁决，因此恰好一个成功。`23P01` 被接口精确映射为 `409 Conflict`，其他完整性错误不会伪装成排期冲突。

### 1:30—2:50｜Outbox 重试与恢复

脚本停止 RabbitMQ，再通过 API 创建面试。预期证据：Relay readiness 为 `503`，业务事务仍成功，事件以 `pending` 和可重试状态保存在 Outbox；Broker 恢复后事件变为 `published`，Inbox 只有一条已处理记录。

> Outbox 解决“业务提交了但消息丢了”，不是 exactly-once。Relay 使用 Publisher Confirm、租约和退避，端到端是 at-least-once；Consumer 通过事件 ID 唯一约束实现幂等。

### 2:50—3:40｜跨租户拒绝

脚本创建 A、B 两个企业，用 B 的合法 Token 请求 A 的面试 ID。预期输出：

```text
unauthenticated_request  : 401 verified
interviewer_job_creation : 403 verified
cross_tenant_lookup      : 404 verified
```

> JWT 提供租户身份，事务内租户上下文驱动 PostgreSQL RLS；应用过滤与复合外键再提供两层保护。跨租户资源返回 404，避免泄露资源是否存在。

### 3:40—4:40｜Prometheus 指标

脚本抓取三个端点并打印代表性样本：

- API `:8000/metrics`：排期冲突、数据库连接池；
- Go Relay `:9101/metrics`：发布结果、Outbox 积压；
- Consumer `:9102/metrics`：通知投递结果、失败积压。

> 指标按进程暴露，避免假装 Go Relay 和 Python Consumer 共享内存。故障时先看 readiness，再看 Outbox backlog、Relay publish outcome 和 Consumer failure backlog，最后结合 request ID 查结构化日志。

脚本会在演示前后记录通知失败积压。若数据库中保留了此前故障演练样本，会明确打印“历史样本且本次未增加”；如果本次演示使失败积压增长，脚本直接失败，避免把历史证据误解为当前故障。

### 4:40—5:00｜收束

> 这四段分别证明了数据库一致性、异步可靠性、多租户安全和故障可观测性。项目的重点不是技术栈数量，而是每个可靠性结论都有可重复的黑盒验收证据。

## 现场失败时怎么处理

- API 未就绪：运行 `docker compose ps` 与 `docker compose logs api migrate`。
- Broker 恢复超时：运行 `docker compose logs outbox-relay rabbitmq notification-consumer`。
- Bootstrap 返回 `429`：运行 `.\scripts\reset_bootstrap_rate_limit.ps1`，它只清理测试专用限流 Key。
- 指标缺失：确认 `8000`、`9101`、`9102` 端口可访问，并检查对应进程日志。

不要现场改代码。保留失败输出，用它解释 readiness、持久化状态和排查路径，同样能体现工程判断。
