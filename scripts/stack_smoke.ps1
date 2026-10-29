$ErrorActionPreference = "Stop"

$services = docker compose ps --format json | ConvertFrom-Json
$expectedServices = @("postgres", "rabbitmq", "redis", "api", "worker", "notification-consumer")
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

[PSCustomObject]@{
    services = $expectedServices.Count
    api_health = $health.status
    result = "compose stack verified"
} | Format-List
