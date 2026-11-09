$ErrorActionPreference = "Stop"

$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}

# This database is created exclusively for this script. Refuse to touch a pre-existing database.
$database = "hireflow_migration_check"
[string] $exists = & $docker compose exec -T postgres psql -U hireflow -d postgres -tAc `
    "SELECT 1 FROM pg_database WHERE datname='$database'"
if (-not [string]::IsNullOrWhiteSpace($exists)) {
    throw "refusing to touch pre-existing database $database"
}

& $docker compose exec -T postgres createdb -U hireflow --template=template0 $database
if ($LASTEXITCODE -ne 0) { throw "failed to create isolated database $database" }

try {
    & $docker compose run --rm migrate python scripts/migration_roundtrip.py $database
    if ($LASTEXITCODE -ne 0) { throw "Alembic head -> base -> head failed" }
}
finally {
    & $docker compose exec -T postgres dropdb -U hireflow $database
}

[PSCustomObject]@{
    database = $database
    round_trip = "head -> base -> head"
    isolation = "verified before migration; temporary database removed"
    result = "Alembic migration round-trip verified"
} | Format-List
