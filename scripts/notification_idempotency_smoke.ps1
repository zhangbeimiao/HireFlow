$ErrorActionPreference = "Stop"

$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}

& $docker compose exec -T notification-consumer python -m hireflow.notification_idempotency_smoke
if ($LASTEXITCODE -ne 0) {
    throw "notification idempotency smoke failed with exit code $LASTEXITCODE"
}
