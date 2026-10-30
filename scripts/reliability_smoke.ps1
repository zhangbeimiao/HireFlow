$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}

# The notification consumer shares the cross-tenant service credential and Python package with the
# former relay. Running the database-focused smoke here keeps the default stack on the Go relay only.
& $docker compose exec -T notification-consumer python -m hireflow.reliability_smoke
if ($LASTEXITCODE -ne 0) { throw "reliability smoke failed with exit code $LASTEXITCODE" }
