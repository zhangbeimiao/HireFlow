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

INSERT INTO users (id, tenant_id, email, display_name, role)
VALUES (
    '88888888-8888-8888-8888-888888888888',
    '99999999-9999-9999-9999-999999999999',
    'interviewer@explain.invalid',
    'Synthetic Interviewer',
    'interviewer'
);

INSERT INTO interviews (
    id, tenant_id, candidate_id, interviewer_id, job_id, starts_at, ends_at, status
)
SELECT md5('explain-interview-' || value)::uuid,
       '99999999-9999-9999-9999-999999999999'::uuid,
       md5('explain-candidate-1')::uuid,
       '88888888-8888-8888-8888-888888888888'::uuid,
       md5('explain-job-1')::uuid,
       TIMESTAMPTZ '2027-01-01 00:00:00+00' - value * INTERVAL '2 minutes',
       TIMESTAMPTZ '2027-01-01 00:01:00+00' - value * INTERVAL '2 minutes',
       'scheduled'
FROM generate_series(1, 5000) AS value;

INSERT INTO notifications (
    id, tenant_id, source_event_id, recipient_type, recipient_id, title, content, created_at
)
SELECT md5('explain-notification-' || value)::uuid,
       '99999999-9999-9999-9999-999999999999'::uuid,
       md5('explain-event-' || value),
       'interviewer',
       '88888888-8888-8888-8888-888888888888'::uuid,
       'Synthetic notification ' || value,
       'Synthetic content',
       TIMESTAMPTZ '2026-10-30 12:00:00+00' - value * INTERVAL '1 millisecond'
FROM generate_series(1, 5000) AS value;

ANALYZE jobs;
ANALYZE candidates;
ANALYZE interviews;
ANALYZE notifications;

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

\echo INTERVIEWS_KEYSET_PLAN
EXPLAIN (ANALYZE, BUFFERS)
SELECT i.id, c.full_name, u.display_name, j.title, i.starts_at, i.ends_at, i.status
FROM interviews i
JOIN candidates c ON c.id = i.candidate_id
JOIN users u ON u.id = i.interviewer_id
JOIN jobs j ON j.id = i.job_id
WHERE i.tenant_id = '99999999-9999-9999-9999-999999999999'
  AND (i.starts_at, i.id) < (
      TIMESTAMPTZ '2026-12-27 00:00:00+00',
      'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid
  )
ORDER BY i.starts_at DESC, i.id DESC
LIMIT 21;

\echo NOTIFICATIONS_KEYSET_PLAN
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, source_event_id, recipient_type, recipient_id, title, content, created_at, read_at
FROM notifications
WHERE tenant_id = '99999999-9999-9999-9999-999999999999'
  AND (created_at, id) < (
      TIMESTAMPTZ '2026-10-30 11:59:57.500+00',
      'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid
  )
ORDER BY created_at DESC, id DESC
LIMIT 21;
ROLLBACK;
ANALYZE jobs;
ANALYZE candidates;
ANALYZE interviews;
ANALYZE notifications;
'@

$output = $sql | & $docker compose exec -T postgres psql -U hireflow -d hireflow 2>&1
if ($LASTEXITCODE -ne 0) { throw "EXPLAIN ANALYZE failed: $($output -join [Environment]::NewLine)" }
$plan = $output -join [Environment]::NewLine

foreach ($expected in @(
    "ix_jobs_tenant_created_id_desc",
    "ix_candidates_tenant_created_id_desc",
    "ix_interviews_tenant_starts_id_desc",
    "ix_notifications_tenant_created_id_desc",
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
    interviews_index = "ix_interviews_tenant_starts_id_desc"
    notifications_index = "ix_notifications_tenant_created_id_desc"
    cleanup = "transaction rolled back; base-table statistics refreshed"
    result = "keyset queries used composite indexes under EXPLAIN ANALYZE"
} | Format-List
