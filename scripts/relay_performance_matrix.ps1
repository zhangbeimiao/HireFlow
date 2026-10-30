param(
    [ValidateRange(10, 10000)] [int] $Events = 500,
    [ValidateRange(3, 5)] [int] $Runs = 3,
    [ValidateSet(10, 50, 100)] [int[]] $BatchSizes = @(10, 50, 100),
    [ValidateRange(30, 600)] [int] $TimeoutSeconds = 180,
    [string] $OutputPath = "docs/relay_benchmark_results.md"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:HIREFLOW_BENCHMARK_PASSWORD = if ($env:HIREFLOW_BENCHMARK_PASSWORD) { $env:HIREFLOW_BENCHMARK_PASSWORD } else { [guid]::NewGuid().ToString("N") }
$env:HIREFLOW_BENCHMARK_APP_PASSWORD = if ($env:HIREFLOW_BENCHMARK_APP_PASSWORD) { $env:HIREFLOW_BENCHMARK_APP_PASSWORD } else { [guid]::NewGuid().ToString("N") }
$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}
$projectRoot = Split-Path $PSScriptRoot -Parent
$composeFile = Join-Path $projectRoot "docker-compose.relay-benchmark.yml"

function Wait-Http {
    param([string] $Uri, [int] $Seconds = 45)
    for ($attempt = 0; $attempt -lt $Seconds; $attempt++) {
        try {
            if ((Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { return }
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
    $target = $ordered[-1].Count * $Quantile
    foreach ($bucket in $ordered) {
        if ($bucket.Count -ge $target) {
            if ([double]::IsPositiveInfinity($bucket.Upper)) { return $null }
            return [math]::Round($bucket.Upper * 1000, 2)
        }
    }
    return $null
}

function Convert-ToMiB {
    param([string] $Value)
    if ($Value -notmatch '^\s*([0-9.]+)\s*(KiB|MiB|GiB)') { return 0.0 }
    $number = [double]::Parse($Matches[1], [cultureinfo]::InvariantCulture)
    switch ($Matches[2]) {
        "KiB" { return $number / 1024 }
        "GiB" { return $number * 1024 }
        default { return $number }
    }
}

function Get-ContainerSample {
    param([string] $ContainerId)
    $raw = (& $docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}' $ContainerId).Trim()
    if ($LASTEXITCODE -ne 0 -or $raw -notmatch '^([^|]+)\|([^/]+)') {
        throw "could not sample relay container resources"
    }
    return [PSCustomObject]@{
        Cpu = [double]::Parse(($Matches[1] -replace '%', '').Trim(), [cultureinfo]::InvariantCulture)
        MemoryMiB = Convert-ToMiB $Matches[2]
    }
}

function Invoke-RelayRun {
    param(
        [ValidateSet("go", "python")] [string] $Implementation,
        [int] $BatchSize,
        [int] $Run,
        [string] $Project,
        [string] $Service,
        [string] $MetricsUri
    )
    & $docker compose -p $Project -f $composeFile --profile $Implementation restart $Service | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to restart $Implementation relay" }
    Wait-Http $MetricsUri
    $containerId = (& $docker compose -p $Project -f $composeFile --profile $Implementation ps -q $Service).Trim()
    $tenantId = [guid]::NewGuid().ToString()
    $sql = "INSERT INTO tenants (id, name) VALUES ('$tenantId', 'relay-benchmark-$tenantId'); " +
        "INSERT INTO outbox_events (id, tenant_id, aggregate_id, event_type, payload, status, attempts, created_at) " +
        "SELECT md5('${tenantId}:event:' || value)::uuid, '$tenantId', md5('${tenantId}:aggregate:' || value)::uuid, " +
        "'benchmark.event', jsonb_build_object('benchmark', true, 'sequence', value), 'pending', 0, " +
        "now() + value * interval '1 microsecond' FROM generate_series(1, $Events) AS value;"
    & $docker compose -p $Project -f $composeFile exec -T postgres psql -U hireflow -d hireflow -v ON_ERROR_STOP=1 -c $sql | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to create isolated benchmark events" }

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $published = 0
    $samples = @()
    $nextSample = 0.0
    while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $value = & $docker compose -p $Project -f $composeFile exec -T postgres psql -U hireflow -d hireflow -Atc "SELECT count(*) FROM outbox_events WHERE tenant_id = '$tenantId' AND status = 'published'"
        if ($LASTEXITCODE -ne 0) { throw "failed to query isolated benchmark progress" }
        $published = [int] "$value".Trim()
        if ($stopwatch.Elapsed.TotalSeconds -ge $nextSample) {
            $samples += Get-ContainerSample $containerId
            $nextSample += 1.0
        }
        if ($published -eq $Events) { break }
        Start-Sleep -Milliseconds 100
    }
    $stopwatch.Stop()
    if ($published -ne $Events) { throw "$Implementation relay published $published/$Events events before timeout" }
    $metrics = (Invoke-WebRequest -Uri $MetricsUri -UseBasicParsing).Content
    return [PSCustomObject]@{
        implementation = $Implementation
        batch_size = $BatchSize
        run = $Run
        events = $Events
        elapsed_seconds = [math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
        throughput = [math]::Round($Events / $stopwatch.Elapsed.TotalSeconds, 2)
        p95_ms = Get-ApproximateQuantileMs $metrics 0.95
        p99_ms = Get-ApproximateQuantileMs $metrics 0.99
        avg_cpu_percent = [math]::Round(($samples | Measure-Object Cpu -Average).Average, 2)
        avg_memory_mib = [math]::Round(($samples | Measure-Object MemoryMiB -Average).Average, 2)
    }
}

$allResults = @()
foreach ($batchSize in $BatchSizes) {
    $env:HIREFLOW_BENCHMARK_BATCH_SIZE = "$batchSize"
    foreach ($implementation in @("go", "python")) {
        $project = "hireflow-relay-bench-$implementation-$batchSize"
        $service = if ($implementation -eq "go") { "go-relay" } else { "python-relay" }
        $metricsUri = if ($implementation -eq "go") { "http://localhost:9201/metrics" } else { "http://localhost:9203/metrics" }
        try {
            & $docker compose -p $project -f $composeFile --profile $implementation up -d --build --wait --wait-timeout 90 $service | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "failed to start isolated $implementation benchmark stack" }
            Wait-Http $metricsUri
            for ($run = 1; $run -le $Runs; $run++) {
                $result = Invoke-RelayRun $implementation $batchSize $run $project $service $metricsUri
                $allResults += $result
                $result | Format-List
            }
        }
        finally {
            & $docker compose -p $project -f $composeFile --profile $implementation down -v --remove-orphans | Out-Null
        }
    }
}
Remove-Item Env:HIREFLOW_BENCHMARK_BATCH_SIZE -ErrorAction SilentlyContinue

$summary = @($allResults | Group-Object implementation, batch_size | ForEach-Object {
    $rows = @($_.Group)
    [PSCustomObject]@{
        implementation = $rows[0].implementation
        batch_size = $rows[0].batch_size
        runs = $rows.Count
        avg_throughput = [math]::Round(($rows | Measure-Object throughput -Average).Average, 2)
        avg_p95_ms = [math]::Round(($rows | Measure-Object p95_ms -Average).Average, 2)
        avg_p99_ms = [math]::Round(($rows | Measure-Object p99_ms -Average).Average, 2)
        avg_cpu_percent = [math]::Round(($rows | Measure-Object avg_cpu_percent -Average).Average, 2)
        avg_memory_mib = [math]::Round(($rows | Measure-Object avg_memory_mib -Average).Average, 2)
    }
} | Sort-Object batch_size, implementation)

$dockerVersion = (& $docker version --format '{{.Server.Version}}').Trim()
$generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm 'UTC'")
$markdown = @(
    "# Go / Python Outbox Relay 性能对比",
    "",
    "测试时间：$generatedAt；Docker Engine：$dockerVersion；宿主机：$([Environment]::OSVersion.VersionString) / $([Environment]::ProcessorCount) 逻辑 CPU。",
    "",
    "每个组合运行 $Runs 轮，每轮 $Events 条事件；PostgreSQL、RabbitMQ 与 Relay 均运行于隔离 Compose 项目。CPU/内存为处理期间每秒容器快照的均值，P95/P99 为 Prometheus 直方图桶近似值。",
    "",
    "| 实现 | 批大小 | 轮数 | 平均吞吐 (events/s) | 平均 P95 (ms) | 平均 P99 (ms) | 平均 CPU (%) | 平均内存 (MiB) |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
)
foreach ($row in $summary) {
    $markdown += "| $($row.implementation) | $($row.batch_size) | $($row.runs) | $($row.avg_throughput) | $($row.avg_p95_ms) | $($row.avg_p99_ms) | $($row.avg_cpu_percent) | $($row.avg_memory_mib) |"
}
$markdown += @(
    "",
    "> 结果仅用于同一台开发机上的实现回归比较。1 秒轮询、Docker Desktop、数据库与 Broker 共享宿主资源都会影响结果，不能外推为生产容量或语言极限。"
)
$resolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputPath)) { $OutputPath } else { Join-Path $projectRoot $OutputPath }
$markdown | Set-Content -LiteralPath $resolvedOutput -Encoding utf8
$summary | Format-Table -AutoSize
Write-Host "Benchmark report written to $resolvedOutput"
