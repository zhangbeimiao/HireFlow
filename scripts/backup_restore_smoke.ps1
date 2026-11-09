param(
    [string]$SourceDatabase = "hireflow"
)

$ErrorActionPreference = "Stop"

if ($SourceDatabase -notmatch '^[a-zA-Z_][a-zA-Z0-9_]*$') {
    throw "Refusing unsafe source database name: $SourceDatabase"
}

$docker = Get-Command docker -ErrorAction Stop
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$restoreDatabase = "hireflow_restore_check_$suffix"
$backupPath = "/tmp/hireflow_backup_restore_$suffix.dump"

if ($restoreDatabase -notmatch '^hireflow_restore_check_[a-f0-9]{12}$') {
    throw "Refusing unsafe restore database name: $restoreDatabase"
}
if ($backupPath -notmatch '^/tmp/hireflow_backup_restore_[a-f0-9]{12}\.dump$') {
    throw "Refusing unsafe backup path: $backupPath"
}

$tables = @(
    "alembic_version",
    "application_audits",
    "applications",
    "candidate_availabilities",
    "candidates",
    "interview_audits",
    "interview_rounds",
    "interview_scorecards",
    "interviewer_availabilities",
    "interviews",
    "jobs",
    "notification_deliveries",
    "notification_inbox",
    "notifications",
    "offer_audits",
    "offers",
    "outbox_events",
    "refresh_tokens",
    "tenants",
    "users"
)

function Invoke-Postgres([string[]]$Arguments) {
    $output = & $docker compose exec -T postgres @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL command failed: $($Arguments -join ' ')"
    }
    return $output
}

function Get-DatabaseManifest([string]$Database) {
    $manifest = [ordered]@{}
    foreach ($table in $tables) {
        # Table names come exclusively from the fixed allow-list above.
        $sql = "SELECT count(*)::text || ':' || md5(COALESCE(string_agg(to_jsonb(t)::text, '' ORDER BY to_jsonb(t)::text), '')) FROM public.`"$table`" AS t;"
        $value = (Invoke-Postgres @("psql", "-X", "-q", "-U", "hireflow", "-d", $Database, "-Atc", $sql) | Out-String).Trim()
        $manifest[$table] = $value
    }
    return $manifest
}

try {
    $sourceManifest = Get-DatabaseManifest $SourceDatabase

    Invoke-Postgres @(
        "pg_dump", "-U", "hireflow", "-d", $SourceDatabase,
        "--format=custom", "--no-owner", "--no-acl", "--file=$backupPath"
    ) | Out-Null

    Invoke-Postgres @("createdb", "-U", "hireflow", "--template=template0", $restoreDatabase) | Out-Null
    Invoke-Postgres @(
        "pg_restore", "-U", "hireflow", "-d", $restoreDatabase,
        "--no-owner", "--no-acl", "--exit-on-error", $backupPath
    ) | Out-Null

    $restoredManifest = Get-DatabaseManifest $restoreDatabase
    $sourceRows = @($sourceManifest.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
    $restoredRows = @($restoredManifest.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
    $differences = Compare-Object -ReferenceObject $sourceRows -DifferenceObject $restoredRows
    if ($differences) {
        throw "Restored database manifest differs from source: $($differences | Out-String)"
    }

    [pscustomobject]@{
        source_database = $SourceDatabase
        restored_database = $restoreDatabase
        tables_verified = $tables.Count
        validation = "exact row counts and deterministic row-content hashes match"
        result = "backup and restore verified"
    }
}
finally {
    # The generated name and path are validated above; the source database is never dropped.
    & $docker compose exec -T postgres dropdb -U hireflow --if-exists $restoreDatabase 2>$null
    & $docker compose exec -T postgres rm -f $backupPath 2>$null
}

