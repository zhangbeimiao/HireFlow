# HireFlow API contract

Base URL：`http://127.0.0.1:8000`。除认证初始化、登录与健康检查外，接口使用：

```http
Authorization: Bearer <access_token>
```

Access Token 包含用户、企业和角色声明；服务端不以客户端传入的 `tenant_id` 作为授权依据。JWT 头携带 `kid`，验签仅接受当前配置的密钥 ID；Refresh Token 为随机不透明值，数据库仅保存 SHA-256 摘要。

## 健康检查

| Method | Path | 成功语义 | 说明 |
| --- | --- | --- | --- |
| GET | `/health` | `200 {"status":"ok"}` | 为已有脚本保留的兼容接口。 |
| GET | `/health/live` | `200 {"status":"alive"}` | 仅确认 API 进程可处理 HTTP，不访问外部依赖。 |
| GET | `/health/ready` | `200 ready/degraded` 或 `503 not_ready` | 检查 PostgreSQL 与 Redis；不返回地址、凭据或异常原文。 |

PostgreSQL 是同步业务的关键依赖，不可用时 readiness 返回 `503`。Redis 承担缓存与限流，项目采用 fail-open 降级策略，因此 Redis 不可用时返回 `200` 与 `status=degraded`。RabbitMQ 不在 API readiness 内：同步事务先可靠写入 PostgreSQL Outbox，Broker 故障由 Relay 的重试与积压指标反映。

## 招聘工作台

| Method | Path | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 公开 | 中文招聘运营工作台；页面本身不含敏感数据。 |
| GET | `/v1/dashboard/overview` | 已登录用户 | 返回当前租户的开放职位、候选人、待进行面试、未读通知计数及最近 5 条面试。 |

工作台通过登录或初始化接口取得短期 JWT，并在 `Authorization` 请求头传递；长期 Refresh Token 仅位于 HttpOnly Cookie，不进入 localStorage 或 JavaScript 响应数据。

## 调用示例

### 初始化企业并获取 Owner Token

```http
POST /v1/auth/bootstrap
Content-Type: application/json

{
  "tenant_name": "Acme",
  "owner": {
    "email": "owner@acme.example",
    "display_name": "Alice",
    "role": "owner",
    "password": "replace-with-a-strong-local-password"
  }
}
```

成功返回 `201 Created`，响应同时返回短期 Access Token 和一次性轮换的 Refresh Token：

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque-token>",
  "expires_in": 1800,
  "token_type": "bearer"
}
```

`POST /v1/auth/refresh` 成功后会撤销旧 Refresh Token 并返回新的 Token Pair；旧 Token 重放返回 `401`，并撤销同一 Token Family 中的所有后继 Token。`POST /v1/auth/logout` 撤销当前 Family，成功返回 `204`。

非浏览器客户端可继续在 JSON Body 中传入 `refresh_token`。工作台则在登录时发送 `X-HireFlow-Client: browser`：服务端不在 JSON 中暴露长期凭据，而是设置 HttpOnly、SameSite=Strict 的 Refresh Cookie 和独立 CSRF Cookie。Cookie 续期/登出必须使用 `X-CSRF-Token` 回传 CSRF 值，否则返回 `403`。并发请求在浏览器内共用一个续期 Promise，避免多次轮换自我触发重放防护。

### 创建面试

```http
POST /v1/interviews
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "candidate_id": "11111111-1111-1111-1111-111111111111",
  "interviewer_id": "22222222-2222-2222-2222-222222222222",
  "job_id": "33333333-3333-3333-3333-333333333333",
  "starts_at": "2026-11-05T10:00:00+08:00",
  "ends_at": "2026-11-05T10:30:00+08:00"
}
```

创建成功返回 `201 Created`。若另一请求已为同一候选人或面试官占用重叠时段，则返回对应的 `409`，例如：

```http
409 Conflict
{"detail":"interviewer already has an overlapping active interview"}
```

这说明推荐时段仅是读模型结果，最终排期由 PostgreSQL 排他约束裁决。

## 认证与招聘实体

| Method | Path | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/v1/auth/bootstrap` | 公开 | 创建企业与首个 Owner，返回 Access/Refresh Token Pair。 |
| POST | `/v1/auth/tokens` | 公开 | 企业 ID、邮箱、密码登录；失败统一 `401`。 |
| POST | `/v1/auth/refresh` | 公开 | Body Token 或 Cookie+CSRF 模式原子换新；过期、撤销或重放返回 `401`。 |
| POST | `/v1/auth/logout` | 公开 | 撤销指定 Refresh Token Family 并清理会话 Cookie；成功返回 `204`。 |
| POST | `/v1/users` | Owner | 创建 Recruiter、Hiring Manager 或 Interviewer。 |
| GET | `/v1/users` | Owner / Recruiter / Hiring Manager | 查询当前租户成员，供排期选择面试官。 |
| POST | `/v1/jobs` | Owner / Recruiter / Hiring Manager | 创建职位。 |
| GET | `/v1/jobs?limit=20&cursor=...` | Owner / Recruiter / Hiring Manager | 按创建时间与 ID 倒序游标分页查询当前租户职位。 |
| PATCH | `/v1/jobs/{job_id}` | Owner / Recruiter / Hiring Manager | 更新职位并使当前租户职位缓存失效。 |
| POST | `/v1/candidates` | Owner / Recruiter | 创建候选人。 |
| GET | `/v1/candidates?limit=20&cursor=...` | Owner / Recruiter / Hiring Manager | 按创建时间与 ID 倒序游标分页查询当前租户候选人。 |
| PATCH | `/v1/candidates/{candidate_id}` | Owner / Recruiter | 更新候选人并使当前租户候选人缓存失效。 |
| POST | `/v1/applications` | Owner / Recruiter | 候选人与职位关联。 |
| GET | `/v1/applications?limit=20&cursor=...` | Owner / Recruiter / Hiring Manager | 游标分页查询当前租户申请。 |
| POST | `/v1/applications/{application_id}/stage` | Owner / Recruiter / Hiring Manager | 按状态机推进申请；必须携带当前 `version`。 |
| GET | `/v1/applications/{application_id}/audit` | Owner / Recruiter / Hiring Manager | 查询申请阶段审计。 |
| POST | `/v1/jobs/{job_id}/rounds` | Owner / Recruiter / Hiring Manager | 配置职位的结构化面试轮次和评分维度。 |
| GET | `/v1/jobs/{job_id}/rounds` | Owner / Recruiter / Hiring Manager | 查询职位面试轮次。 |
| PUT | `/v1/interviews/{interview_id}/scorecard` | Interviewer / Hiring Manager | 保存或最终提交评分卡；提交后不可修改。 |
| GET | `/v1/interviews/{interview_id}/scorecards` | Owner / Recruiter / Hiring Manager | 查询面试评分卡。 |
| POST | `/v1/offers` | Owner / Recruiter | 为 `offer` 阶段申请起草 Offer。 |
| GET | `/v1/offers` | Owner / Recruiter / Hiring Manager | 查询当前租户 Offer。 |
| POST | `/v1/offers/{offer_id}/status` | Owner / Recruiter | 按状态机更新 Offer；必须携带当前 `version`。 |
| GET | `/v1/offers/{offer_id}/audit` | Owner / Recruiter / Hiring Manager | 查询 Offer 状态审计。 |

Application 状态机为 `applied → screening → interview → offer → hired`，中间阶段可转为 `rejected` 或 `withdrawn`。Offer 状态机为 `draft → sent → accepted/declined`，并允许在生效前撤回。接受 Offer 时，服务在同一 PostgreSQL 事务内将 Application 推进为 `hired`，追加双方审计并写入 Transactional Outbox。客户端版本落后或非法跳转统一返回 `409`。

### 列表缓存语义

`GET /v1/jobs` 与 `GET /v1/candidates` 默认 `limit=20`，最大 100。响应仍为数组；若存在下一页，服务端通过 `X-Next-Cursor` 返回不透明游标，客户端原样传给下一次请求。`X-Page-Limit` 表示本次页大小。无法解析或结构非法的游标返回 `422`。

排序键为 `(created_at DESC, id DESC)`，游标同时编码这两个字段，因此即使多条记录创建时间相同，翻页仍保持确定顺序且不会重复。该方案不会像深分页 `OFFSET` 一样扫描并丢弃前 N 行。

默认第一页使用 Redis Cache Aside：Key 包含 JWT 中的 `tenant_id`，默认 TTL 为 60 秒；创建或更新成功提交 PostgreSQL 后，只删除当前租户对应的首页 Key。游标页和自定义页大小绕过缓存，避免维护不受控的多页缓存 Key。响应头 `X-Cache` 的值为：

| 值 | 含义 |
| --- | --- |
| `HIT` | Redis 命中，不创建 PostgreSQL 会话。 |
| `MISS` | Redis 未命中，从 PostgreSQL 回源并写入 TTL 缓存。 |
| `BYPASS` | 请求携带 `Cache-Control: no-cache`，或请求游标页/自定义页大小，直接读取数据库。 |
| `ERROR` | Redis 不可用，服务降级读取 PostgreSQL。 |

Prometheus 暴露 `hireflow_cache_requests_total`、`hireflow_cache_hit_ratio`、`hireflow_cache_db_load_duration_seconds` 和 `hireflow_cache_invalidations_total`。命中率也可由 hit/miss Counter 在 PromQL 中按窗口计算。

## 可用时段与排期

| Method | Path | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/v1/candidates/{candidate_id}/availability` | Owner / Recruiter | 提交候选人时段。 |
| POST | `/v1/interviewers/{interviewer_id}/availability` | Owner / Recruiter / Hiring Manager / Interviewer | 提交面试官时段。 |
| POST | `/v1/scheduling/suggestions` | Owner / Recruiter / Hiring Manager | 返回共同可用时段；不预占。 |
| POST | `/v1/interviews` | Owner / Recruiter / Hiring Manager | 创建面试；竞争冲突 `409`。 |
| GET | `/v1/interviews?limit=20&cursor=...` | Owner / Recruiter / Hiring Manager | 按开始时间与 ID 倒序游标分页查询面试，包含候选人、职位与面试官展示信息。 |
| POST | `/v1/interviews/{interview_id}/reschedule` | Owner / Recruiter / Hiring Manager | 改期并写审计/Outbox；须携带 `version`，重叠或旧版本返回 `409`。 |
| POST | `/v1/interviews/{interview_id}/status` | Owner / Recruiter / Hiring Manager / Interviewer | 状态迁移，必须携带读取时的 `version`。 |
| GET | `/v1/interviews/{interview_id}` | 相关角色 | 获取同租户面试。 |
| GET | `/v1/interviews/{interview_id}/audit` | Owner / Recruiter / Hiring Manager | 获取审计记录。 |

时间必须携带偏移，例如 `2026-11-05T10:00:00+08:00`，数据库统一保存 UTC。状态机为 `pending → scheduled → confirmed → completed`，或转 `cancelled` / `no_show`。

## 通知与失败重放

| Method | Path | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/v1/notifications?limit=20&cursor=...` | Owner / Recruiter / Hiring Manager | 按创建时间与 ID 倒序游标分页查询本企业站内通知。 |
| PATCH | `/v1/notifications/{notification_id}/read` | Owner / Recruiter / Hiring Manager | 标记单条通知已读。 |
| POST | `/v1/notifications/read-all` | Owner / Recruiter / Hiring Manager | 标记当前租户全部通知已读。 |
| GET | `/v1/admin/notification-deliveries?status=sent` | Owner | 查询当前租户邮件发送状态、尝试次数和失败原因。 |
| GET | `/v1/admin/notification-deliveries/{delivery_id}` | Owner | 查询单次投递的模板版本、供应商响应、送达或退信状态。 |
| POST | `/v1/admin/notification-deliveries/{delivery_id}/events` | Owner | 幂等记录供应商 `delivered` / `bounced` 状态事件；生产环境应由受认证的 Provider Webhook Adapter 调用。 |
| GET | `/v1/admin/notification-failures` | Owner | 查询失败 Inbox 记录。 |
| POST | `/v1/admin/notification-failures/{event_id}/replay` | Owner | 标记失败事件待重放。 |

消费者最多处理三次，之后显式投递到 RabbitMQ DLQ；Inbox 保存租户、尝试次数与错误证据，供 Owner 定位并重放。SMTP Dispatcher 独立消费持久化 Delivery：超时、4xx 与供应商限流进入指数退避，5xx 永久拒收立即失败；模板版本、Message-ID、错误分类与供应商状态事件均持久化。本地 Compose 使用 Mailpit 验收真实 SMTP 内容。

## HTTP 状态码

| 状态码 | 语义 |
| --- | --- |
| `200` | 登录、查询、更新、推荐或重放成功。 |
| `201` | 创建资源成功。 |
| `401` | Token 缺失/无效，或登录失败。 |
| `403` | Token 有效但角色无权操作。 |
| `404` | 当前租户内资源不存在；跨租户同样返回 `404`。 |
| `409` | 重复资源、状态非法、乐观锁过期或排期冲突。 |
| `422` | 请求字段、UUID、邮箱或时区时间校验失败。 |
| `429` | Redis 限流触发，响应带 `Retry-After`。 |
| `500` | 未预期服务端错误；排期排他约束已映射为 `409`。 |
