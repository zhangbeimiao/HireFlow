$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoRoot = Split-Path -Parent $PSScriptRoot
$originalLocation = Get-Location
$startedAt = Get-Date

function Write-DemoStage {
    param(
        [Parameter(Mandatory)] [int] $Number,
        [Parameter(Mandatory)] [string] $Title,
        [Parameter(Mandatory)] [string] $Evidence
    )
    Write-Host ""
    Write-Host "[$Number/4] $Title" -ForegroundColor Cyan
    Write-Host "Evidence: $Evidence" -ForegroundColor DarkGray
}

function Invoke-DemoScript {
    param([Parameter(Mandatory)] [string] $Name)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    & (Join-Path $PSScriptRoot $Name)
    $watch.Stop()
    Write-Host ("Completed in {0:n1}s" -f $watch.Elapsed.TotalSeconds) -ForegroundColor Green
}

function Get-MetricSamples {
    param(
        [Parameter(Mandatory)] [string] $Uri,
        [Parameter(Mandatory)] [string[]] $Names
    )
    $content = (Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10).Content
    foreach ($name in $Names) {
        $samples = @($content -split "`n" | Where-Object {
            $_ -match "^$([regex]::Escape($name))(\{|\s)"
        } | Select-Object -First 3)
        if ($samples.Count -eq 0) {
            throw "Prometheus sample was not exposed at ${Uri}: $name"
        }
        $samples | ForEach-Object { Write-Host "  $($_.Trim())" }
    }
}

function Get-MetricValue {
    param(
        [Parameter(Mandatory)] [string] $Uri,
        [Parameter(Mandatory)] [string] $Sample
    )
    $content = (Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10).Content
    $line = @($content -split "`n" | Where-Object {
        $_ -match "^$([regex]::Escape($Sample))\s+"
    } | Select-Object -First 1)
    if ($line.Count -ne 1) { throw "Prometheus sample was not exposed at ${Uri}: $Sample" }
    return [double](($line[0].Trim() -split "\s+")[-1])
}

try {
    Set-Location $repoRoot

    $docker = "docker"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
        if (-not (Test-Path $candidate)) {
            throw "Docker CLI was not found. Start Docker Desktop first."
        }
        $docker = $candidate
    }

    $requiredServices = @("api", "postgres", "rabbitmq", "redis", "outbox-relay", "notification-consumer")
    $runningServices = @(& $docker compose ps --status running --services)
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the Docker Compose stack." }
    $missingServices = @($requiredServices | Where-Object { $_ -notin $runningServices })
    if ($missingServices.Count -gt 0) {
        throw "Required services are not running: $($missingServices -join ', '). Run 'docker compose up -d --wait' first."
    }
    $ready = Invoke-RestMethod -Uri "http://localhost:8000/health/ready" -TimeoutSec 10
    if ($ready.status -ne "ready") { throw "HireFlow API dependencies are not ready." }
    $failureSample = 'hireflow_notification_failure_backlog{state="failed"}'
    $initialFailureBacklog = Get-MetricValue "http://localhost:9102/metrics" $failureSample

    Write-Host "HireFlow 5-minute reliability demo" -ForegroundColor White
    Write-Host "The script creates isolated demo tenants and does not delete business data." -ForegroundColor DarkGray

    & (Join-Path $PSScriptRoot "reset_bootstrap_rate_limit.ps1") | Out-Null
    Write-DemoStage 1 "Concurrent scheduling: one 201, one 409" "PostgreSQL exclusion constraint is the final concurrency authority."
    Invoke-DemoScript "concurrency_smoke.ps1"

    Write-DemoStage 2 "Outbox retry and broker recovery" "The API commits while RabbitMQ is down; Relay later publishes and Inbox processes once."
    Invoke-DemoScript "go_relay_recovery_smoke.ps1"

    & (Join-Path $PSScriptRoot "reset_bootstrap_rate_limit.ps1") | Out-Null
    Write-DemoStage 3 "Tenant isolation and authorization" "A tenant-B token receives 404 for tenant-A data; 401/403 boundaries are also verified."
    Invoke-DemoScript "security_smoke.ps1"

    Write-DemoStage 4 "Prometheus evidence" "API, Go Relay and notification consumer expose independent process metrics."
    Invoke-DemoScript "observability_smoke.ps1"
    Write-Host "Selected API samples:" -ForegroundColor Yellow
    Get-MetricSamples "http://localhost:8000/metrics" @(
        "hireflow_scheduling_conflicts_total",
        "hireflow_db_pool_connections"
    )
    Write-Host "Selected Go Relay samples:" -ForegroundColor Yellow
    Get-MetricSamples "http://localhost:9101/metrics" @(
        "hireflow_outbox_relay_events_total",
        "hireflow_outbox_backlog"
    )
    Write-Host "Selected consumer samples:" -ForegroundColor Yellow
    Get-MetricSamples "http://localhost:9102/metrics" @(
        "hireflow_notification_deliveries_total",
        "hireflow_notification_failure_backlog"
    )

    $finalFailureBacklog = Get-MetricValue "http://localhost:9102/metrics" $failureSample
    if ($finalFailureBacklog -gt $initialFailureBacklog) {
        throw "This demo increased the durable notification failure backlog from $initialFailureBacklog to $finalFailureBacklog"
    }
    if ($finalFailureBacklog -gt 0) {
        Write-Host "Pre-existing failure-drill samples (unchanged by this demo): $finalFailureBacklog" -ForegroundColor Yellow
    } else {
        Write-Host "Notification failure backlog remained clean: 0" -ForegroundColor Green
    }

    $elapsed = (Get-Date) - $startedAt
    Write-Host ""
    Write-Host ("PASS: all four reliability claims were verified in {0:mm\:ss}." -f $elapsed) -ForegroundColor Green
    Write-Host "Guide: docs/five_minute_demo.md" -ForegroundColor DarkGray
}
finally {
    Set-Location $originalLocation
}
