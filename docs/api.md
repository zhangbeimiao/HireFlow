# HireFlow API contract

Base URL：`http://127.0.0.1:8000`。除认证初始化、登录与健康检查外，接口使用：

```http
Authorization: Bearer <access_token>
```

Token 包含用户、企业和角色声明；服务端不以客户端传入的 `tenant_id` 作为授权依据。

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

成功返回 `201 Created`，响应中的 `access_token` 用于后续请求：

```json
{"access_token":"<jwt>","token_type":"bearer"}
```

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

创建成功返回 `201 Created`。若另一请求已为同一面试官占用重叠时段，则返回：

```http
409 Conflict
{"detail":"interviewer already has an overlapping active interview"}
```

这说明推荐时段仅是读模型结果，最终排期由 PostgreSQL 排他约束裁决。

## 认证与招聘实体

| Method | Path | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/v1/auth/bootstrap` | 公开 | 创建企业与首个 Owner，返回 JWT。 |
| POST | `/v1/auth/tokens` | 公开 | 企业 ID、邮箱、密码登录；失败统一 `401`。 |
| POST | `/v1/users` | Owner | 创建 Recruiter、Hiring Manager 或 Interviewer。 |
| POST | `/v1/jobs` | Owner / Recruiter / Hiring Manager | 创建职位。 |
| POST | `/v1/candidates` | Owner / Recruiter | 创建候选人。 |
| POST | `/v1/applications` | Owner / Recruiter | 候选人与职位关联。 |

## 可用时段与排期

| Method | Path | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/v1/candidates/{candidate_id}/availability` | Owner / Recruiter | 提交候选人时段。 |
| POST | `/v1/interviewers/{interviewer_id}/availability` | Owner / Recruiter / Hiring Manager / Interviewer | 提交面试官时段。 |
| POST | `/v1/scheduling/suggestions` | Owner / Recruiter / Hiring Manager | 返回共同可用时段；不预占。 |
| POST | `/v1/interviews` | Owner / Recruiter / Hiring Manager | 创建面试；竞争冲突 `409`。 |
| POST | `/v1/interviews/{interview_id}/status` | Owner / Recruiter / Hiring Manager / Interviewer | 状态迁移，必须携带读取时的 `version`。 |
| GET | `/v1/interviews/{interview_id}` | 相关角色 | 获取同租户面试。 |
| GET | `/v1/interviews/{interview_id}/audit` | Owner / Recruiter / Hiring Manager | 获取审计记录。 |

时间必须携带偏移，例如 `2026-11-05T10:00:00+08:00`，数据库统一保存 UTC。状态机为 `pending → scheduled → confirmed → completed`，或转 `cancelled` / `no_show`。

## 通知与失败重放

| Method | Path | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/v1/notifications` | Owner / Recruiter / Hiring Manager | 查询本企业站内通知。 |
| GET | `/v1/admin/notification-failures` | Owner | 查询失败 Inbox 记录。 |
| POST | `/v1/admin/notification-failures/{event_id}/replay` | Owner | 标记失败事件待重放。 |

消费者最多处理三次，之后显式投递到 RabbitMQ DLQ；Inbox 保存租户、尝试次数与错误证据，供 Owner 定位并重放。

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
