# 截图采集说明

本目录只存放脱敏后的真实运行截图。截图前请确认浏览器、终端和 Docker Desktop 中没有 Token、密码、绝对本地路径或个人邮箱。

建议按以下文件名采集：

| 文件名 | 操作 | 证明点 |
| --- | --- | --- |
| `01-compose-stack.png` | 执行 `docker compose ps` | PostgreSQL、Redis、RabbitMQ、API、Relay、Consumer 处于可用状态。 |
| `02-swagger.png` | 打开 `http://localhost:8000/docs` | 可浏览、可调试的 OpenAPI 接口。 |
| `03-concurrency.png` | 执行 `.\scripts\concurrency_smoke.ps1` | 同一唯一时段的并发请求得到一个 `201` 与一个 `409`。 |
| `04-rabbitmq.png` | 打开 `http://localhost:15672` | 事件主队列、重试队列与 DLQ。 |
| `05-metrics.png` | 打开 `http://localhost:8000/metrics` | Prometheus HTTP 请求与延迟指标。 |

完成后，在项目根目录执行 `git add docs/screenshots`，截图会随下一次提交发布。README 只引用实际存在的图片，避免展示失效链接。
