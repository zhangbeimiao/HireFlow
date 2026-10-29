$ErrorActionPreference = "Stop"

$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}

$sql = @'
\set ON_ERROR_STOP on
BEGIN;
INSERT INTO tenants (id, name)
VALUES ('99999999-9999-9999-9999-999999999999', 'EXPLAIN ANALYZE synthetic tenant');

INSERT INTO jobs (id, tenant_id, title, created_at)
SELECT md5('explain-job-' || value)::uuid,
       '99999999-9999-9999-9999-999999999999'::uuid,
       'Synthetic Job ' || value,
       TIMESTAMPTZ '2026-10-29 12:00:00+00' - value * INTERVAL '1 millisecond'
FROM generate_series(1, 5000) AS value;

INSERT INTO candidates (id, tenant_id, email, full_name, created_at)
SELECT md5('explain-candidate-' || value)::uuid,
       '99999999-9999-9999-9999-999999999999'::uuid,
       'synthetic-' || value || '@explain.invalid',
       'Synthetic Candidate ' || value,
       TIMESTAMPTZ '2026-10-29 12:00:00+00' - value * INTERVAL '1 millisecond'
FROM generate_series(1, 5000) AS value;

ANALYZE jobs;
ANALYZE candidates;

\echo JOBS_KEYSET_PLAN
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, tenant_id, title, status, created_at
FROM jobs
WHERE tenant_id = '99999999-9999-9999-9999-999999999999'
  AND (created_at, id) < (
      TIMESTAMPTZ '2026-10-29 11:59:57.500+00',
      'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid
  )
ORDER BY created_at DESC, id DESC
LIMIT 21;

\echo CANDIDATES_KEYSET_PLAN
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, tenant_id, email, full_name, created_at
FROM candidates
WHERE tenant_id = '99999999-9999-9999-9999-999999999999'
  AND (created_at, id) < (
      TIMESTAMPTZ '2026-10-29 11:59:57.500+00',
      'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid
  )
ORDER BY created_at DESC, id DESC
LIMIT 21;
ROLLBACK;
ANALYZE jobs;
ANALYZE candidates;
'@

$output = $sql | & $docker compose exec -T postgres psql -U hireflow -d hireflow 2>&1
if ($LASTEXITCODE -ne 0) { throw "EXPLAIN ANALYZE failed: $($output -join [Environment]::NewLine)" }
$plan = $output -join [Environment]::NewLine

foreach ($expected in @(
    "ix_jobs_tenant_created_id_desc",
    "ix_candidates_tenant_created_id_desc",
    "actual time=",
    "Buffers:"
)) {
    if ($plan -notmatch [regex]::Escape($expected)) {
        throw "EXPLAIN ANALYZE output did not contain '$expected':`n$plan"
    }
}

$output
[PSCustomObject]@{
    synthetic_rows_per_resource = 5000
    jobs_index = "ix_jobs_tenant_created_id_desc"
    candidates_index = "ix_candidates_tenant_created_id_desc"
    cleanup = "transaction rolled back; base-table statistics refreshed"
    result = "keyset queries used composite indexes under EXPLAIN ANALYZE"
} | Format-List
