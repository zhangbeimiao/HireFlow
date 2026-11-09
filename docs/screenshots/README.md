# 截图采集说明

本目录只存放脱敏后的真实运行截图。截图前请确认浏览器、终端和 Docker Desktop 中没有 Token、密码、绝对本地路径或个人邮箱。

若新开的 PowerShell 窗口无法识别 `docker`，先执行：

```powershell
$env:Path += ";C:\Program Files\Docker\Docker\resources\bin"
```

当前已采集并随仓库发布的证据：

| 文件名 | 操作 | 证明点 |
| --- | --- | --- |
| `01-ats-audit-timeline.jpg` | 打开申请详情 | Application 状态、版本、负责人及不可覆盖的审计时间线。 |
| `02-workbench.jpg` | 打开招聘工作台 | 职位、候选人、申请和系统状态的真实聚合视图。 |
| `03-scheduling-conflict.jpg` | 对已占用时段再次排期 | 数据库最终裁决返回可理解的 `409 Conflict`。 |
| `04-prometheus-metrics.jpg` | 打开 `http://localhost:8000/metrics` | HTTP、依赖和业务指标已暴露给 Prometheus。 |

截图均来自本地 Docker Compose 真实运行，不使用生产数据。2026-11-09 已复核四张图片：未包含 Token、Cookie、密码、绝对本地路径或个人邮箱，且与当前 ATS 工作台、审计、冲突处理和指标接口一致。README 只引用实际存在的图片，避免展示失效链接；新增图片前仍需执行同样检查。
