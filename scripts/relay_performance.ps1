param(
    [int] $Events = 500,
    [int] $TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}
$composeFile = Join-Path (Split-Path $PSScriptRoot -Parent) "docker-compose.relay-benchmark.yml"

function Wait-Http {
    param([Parameter(Mandatory)] [string] $Uri, [int] $Seconds = 45)
    for ($attempt = 0; $attempt -lt $Seconds; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "endpoint did not become ready: $Uri"
}

function Get-ApproximateQuantileMs {
    param([string] $Metrics, [double] $Quantile)
    $buckets = @()
    foreach ($line in ($Metrics -split "`n")) {
        if ($line -match '^hireflow_outbox_publish_duration_seconds_bucket\{le="([^"]+)"\}\s+([0-9.eE+-]+)') {
            $upper = if ($Matches[1] -eq "+Inf") { [double]::PositiveInfinity } else { [double] $Matches[1] }
            $buckets += [PSCustomObject]@{ Upper = $upper; Count = [double] $Matches[2] }
        }
    }
    if ($buckets.Count -eq 0) { return $null }
    $ordered = @($buckets | Sort-Object Upper)
    $total = $ordered[-1].Count
    $target = $total * $Quantile
    foreach ($bucket in $ordered) {
        if ($bucket.Count -ge $target) {
            if ([double]::IsPositiveInfinity($bucket.Upper)) { return $null }
            return [math]::Round($bucket.Upper * 1000, 2)
        }
    }
    return $null
}

function Invoke-RelayBenchmark {
    param([Parameter(Mandatory)] [ValidateSet("go", "python")] [string] $Implementation)

    $project = "hireflow-relay-bench-$Implementation"
    $profile = $Implementation
    $service = if ($Implementation -eq "go") { "go-relay" } else { "python-relay" }
    $metricsUri = if ($Implementation -eq "go") { "http://localhost:9201/metrics" } else { "http://localhost:9203/metrics" }
    try {
        & $docker compose -p $project -f $composeFile --profile $profile up -d --build --wait --wait-timeout 90 $service | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to start isolated $Implementation benchmark stack" }
        Wait-Http $metricsUri

        $tenantId = [guid]::NewGuid().ToString()
        $sql = "INSERT INTO tenants (id, name) VALUES ('$tenantId', 'relay-benchmark-$tenantId'); " +
            "INSERT INTO outbox_events (id, tenant_id, aggregate_id, event_type, payload, status, attempts, created_at) " +
            "SELECT md5('${tenantId}:event:' || value)::uuid, '$tenantId', md5('${tenantId}:aggregate:' || value)::uuid, " +
            "'benchmark.event', jsonb_build_object('benchmark', true, 'sequence', value), 'pending', 0, " +
            "now() + value * interval '1 microsecond' FROM generate_series(1, $Events) AS value;"
        & $docker compose -p $project -f $composeFile exec -T postgres psql -U hireflow -d hireflow -v ON_ERROR_STOP=1 -c $sql | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to create isolated benchmark events" }

        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $published = 0
        while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            $value = & $docker compose -p $project -f $composeFile exec -T postgres psql -U hireflow -d hireflow -Atc "SELECT count(*) FROM outbox_events WHERE tenant_id = '$tenantId' AND status = 'published'"
            if ($LASTEXITCODE -ne 0) { throw "failed to query isolated benchmark progress" }
            $published = [int] "$value".Trim()
            if ($published -eq $Events) { break }
            Start-Sleep -Milliseconds 100
        }
        $stopwatch.Stop()
        if ($published -ne $Events) { throw "$Implementation relay published $published/$Events events before timeout" }

        $metricText = (Invoke-WebRequest -Uri $metricsUri -UseBasicParsing).Content
        $containerId = (& $docker compose -p $project -f $composeFile --profile $profile ps -q $service).Trim()
        $memory = (& $docker stats --no-stream --format '{{.MemUsage}}' $containerId).Trim()
        return [PSCustomObject]@{
            implementation = $Implementation
            events = $Events
            elapsed_seconds = [math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
            throughput_events_per_second = [math]::Round($Events / $stopwatch.Elapsed.TotalSeconds, 2)
            publish_p95_ms_approx = Get-ApproximateQuantileMs $metricText 0.95
            publish_p99_ms_approx = Get-ApproximateQuantileMs $metricText 0.99
            container_memory = $memory
        }
    }
    finally {
        # Only the named, isolated benchmark project and its ephemeral volumes are removed.
        & $docker compose -p $project -f $composeFile --profile $profile down -v --remove-orphans | Out-Null
    }
}

if ($Events -lt 1 -or $Events -gt 10000) { throw "Events must be between 1 and 10000" }
$results = @(
    Invoke-RelayBenchmark "go"
    Invoke-RelayBenchmark "python"
)
$results | Format-List
Write-Host "P95/P99 are Prometheus histogram bucket approximations. The isolated, machine-local run is for regression comparison only."
