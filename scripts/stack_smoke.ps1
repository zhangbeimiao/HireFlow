$ErrorActionPreference = "Stop"

$services = docker compose ps --format json | ConvertFrom-Json
$expectedServices = @("postgres", "rabbitmq", "redis", "api", "outbox-relay", "notification-consumer")
$unhealthy = @()

foreach ($name in $expectedServices) {
    $service = @($services | Where-Object { $_.Service -eq $name }) | Select-Object -First 1
    if ($null -eq $service) {
        $unhealthy += "$name (missing)"
        continue
    }

    if ($service.State -ne "running") {
        $unhealthy += "$name ($($service.State))"
    }
}

if ($unhealthy.Count -gt 0) {
    throw "services not ready: $($unhealthy -join ', ')"
}

$health = Invoke-RestMethod -Uri "http://localhost:8000/health" -ErrorAction Stop
if ($health.status -ne "ok") {
    throw "unexpected API health response"
}

$liveness = Invoke-RestMethod -Uri "http://localhost:8000/health/live" -ErrorAction Stop
if ($liveness.status -ne "alive") {
    throw "unexpected API liveness response"
}

$readiness = Invoke-RestMethod -Uri "http://localhost:8000/health/ready" -ErrorAction Stop
if (
    $readiness.status -ne "ready" -or
    $readiness.dependencies.postgres -ne "ok" -or
    $readiness.dependencies.redis -ne "ok"
) {
    throw "unexpected API readiness response"
}

[PSCustomObject]@{
    services = $expectedServices.Count
    api_health = $health.status
    liveness = $liveness.status
    readiness = $readiness.status
    result = "compose stack verified"
} | Format-List
