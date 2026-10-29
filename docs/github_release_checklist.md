# GitHub 发布清单

## 仓库 About 建议

- **Description**：`Multi-tenant interview scheduling backend with PostgreSQL conflict protection, RabbitMQ Outbox, JWT/RBAC and Redis rate limiting.`
- **Topics**：`fastapi`、`postgresql`、`rabbitmq`、`redis`、`docker-compose`、`jwt`、`rbac`、`prometheus`、`python`

## 建议截图（真实运行后再放入仓库）

1. `docker compose ps`：PostgreSQL、Redis、RabbitMQ、API、Relay、Consumer 均健康。
2. `concurrency_smoke.ps1`：唯一时段得到一个 `201` 与一个 `409`。
3. Swagger UI：认证、排期、通知重放接口。
4. RabbitMQ 管理台：`hireflow.events`、retry queue、DLQ。
5. `/metrics`：`hireflow_http_requests_total` 与延迟直方图。

截图保存到 `docs/screenshots/` 前，应确认画面不包含本地绝对路径、Token、数据库密码或个人信息。

## 发布前命令

```powershell
python -m ruff check src tests migrations
python -m pytest -q
docker compose up --build -d
.\scripts\demo.ps1
.\scripts\security_smoke.ps1
.\scripts\rate_limit_smoke.ps1
.\scripts\observability_smoke.ps1
.\scripts\concurrency_smoke.ps1
```

## 发布边界

- 仅提交 `.env.example`，不要提交 `.env`、JWT 密钥或个人账户信息。
- Docker Compose 中的密码仅用于本地开发；公开仓库 README 已明确其开发用途。
- 首个提交仅在本地创建；创建 GitHub 仓库、添加 remote 和推送需明确授权。
