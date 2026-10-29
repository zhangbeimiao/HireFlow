# Query pagination and index evidence

## 设计

职位、候选人和通知列表按 `(created_at DESC, id DESC)` 排序，面试列表按 `(starts_at DESC, id DESC)` 排序。API 返回不透明游标，下一页使用严格小于上一页末项的 Keyset 条件：

```sql
WHERE tenant_id = :tenant_id
  AND (created_at, id) < (:cursor_created_at, :cursor_id)
ORDER BY created_at DESC, id DESC
LIMIT :page_size_plus_one;
```

`id` 是同时间戳下的稳定 Tie-breaker；额外读取一行只用于判断是否需要 `X-Next-Cursor`。相比 `OFFSET`，查询成本不随页码线性增长，也不会因前序数据插入导致翻页重复。

Alembic `20261029_0007` 创建：

```sql
CREATE INDEX ix_jobs_tenant_created_id_desc
ON jobs (tenant_id, created_at DESC, id DESC);

CREATE INDEX ix_candidates_tenant_created_id_desc
ON candidates (tenant_id, created_at DESC, id DESC);

CREATE INDEX ix_interviews_tenant_starts_id_desc
ON interviews (tenant_id, starts_at DESC, id DESC);

CREATE INDEX ix_notifications_tenant_created_id_desc
ON notifications (tenant_id, created_at DESC, id DESC);
```

索引首列匹配 RLS/业务查询的租户边界，后两列同时服务游标过滤与排序。当前项目数据量适合事务内普通建索引；真实大表上线时应评估 `CREATE INDEX CONCURRENTLY` 与非事务迁移，避免长时间写锁。

## 本机 EXPLAIN ANALYZE 快照

执行：

```powershell
.\scripts\explain_analyze.ps1
```

脚本在单个事务中分别生成 5000 条职位、候选人、面试和通知记录，执行 `ANALYZE` 与真实查询形状的游标查询，最后 `ROLLBACK`，不保留测试业务数据，并重新分析真实基表以恢复统计信息。2026-10-30 的本机结果：

| Query | Plan | Rows | Execution Time | Execution Buffers |
| --- | --- | ---: | ---: | ---: |
| Jobs keyset page | `Index Scan using ix_jobs_tenant_created_id_desc` | 21 | 0.045 ms | shared hit=3 |
| Candidates keyset page | `Index Scan using ix_candidates_tenant_created_id_desc` | 21 | 0.027 ms | shared hit=4 |
| Interviews joined keyset page | `Index Scan using ix_interviews_tenant_starts_id_desc` | 21 | 0.453 ms | shared hit=11 |
| Notifications keyset page | `Index Scan using ix_notifications_tenant_created_id_desc` | 21 | 0.029 ms | shared hit=4 |

这组数据证明查询形状与复合索引匹配，不代表生产环境延迟或容量；后续硬件、数据分布或 PostgreSQL 版本变化时应重新采样。
