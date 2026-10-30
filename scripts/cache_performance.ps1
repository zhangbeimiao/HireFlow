param(
    [ValidateRange(1, 100)] [int] $Users = 10,
    [ValidateRange(1, 100)] [int] $SpawnRate = 10,
    [ValidateRange(5, 120)] [int] $RunSeconds = 10,
    [ValidateRange(1, 50)] [int] $ItemsPerList = 20
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowCacheLoadPassword!2026"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$locustfile = Join-Path $projectRoot "load/cache_locustfile.py"

# The full integration workflow intentionally exercises authentication before
# this benchmark. Isolate this fixture from the fixed-window auth counter while
# preserving every business-cache key and the production rate-limit settings.
$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidateDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidateDocker)) { throw "Docker CLI was not found" }
    $docker = $candidateDocker
}
$bootstrapRateLimitKeys = @(
    & $docker compose exec -T redis redis-cli --scan --pattern "hireflow:rate-limit:POST:/v1/auth/bootstrap:*"
)
if ($LASTEXITCODE -ne 0) { throw "Could not enumerate bootstrap rate-limit keys" }
foreach ($key in $bootstrapRateLimitKeys) {
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        & $docker compose exec -T redis redis-cli DEL $key | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not delete bootstrap rate-limit fixture key" }
    }
}

function Invoke-HireFlowJson {
    param([string] $Path, [object] $Body, [hashtable] $Headers = @{})
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

$bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
    tenant_name = "Cache Load $runId"
    owner = @{
        email = "owner.$runId@hireflow.demo"
        display_name = "Cache Load Owner"
        role = "owner"
        password = $password
    }
}
$headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
for ($index = 1; $index -le $ItemsPerList; $index++) {
    Invoke-HireFlowJson "/v1/jobs" @{ title = "Cache Load Job $index" } $headers | Out-Null
    Invoke-HireFlowJson "/v1/candidates" @{
        email = "cache.load.$index.$runId@hireflow.demo"
        full_name = "Cache Load Candidate $index"
    } $headers | Out-Null
}

# Warm both lists once. The cached run must then return HIT for every request.
Invoke-RestMethod -Uri "$baseUrl/v1/jobs" -Headers $headers | Out-Null
Invoke-RestMethod -Uri "$baseUrl/v1/candidates" -Headers $headers | Out-Null
$env:HIREFLOW_CACHE_LOAD_TOKEN = $bootstrap.access_token

function Invoke-CacheLoad {
    param([string] $Mode)
    $prefix = Join-Path ([System.IO.Path]::GetTempPath()) "hireflow-cache-$Mode-$runId"
    $env:HIREFLOW_CACHE_LOAD_MODE = $Mode
    & $python -m locust -f $locustfile --headless --host $baseUrl `
        -u $Users -r $SpawnRate --run-time "$($RunSeconds)s" --only-summary --csv $prefix
    if ($LASTEXITCODE -ne 0) { throw "Locust $Mode run failed with exit code $LASTEXITCODE" }

    $row = Import-Csv "$($prefix)_stats.csv" | Where-Object Name -eq "Aggregated"
    if ($null -eq $row -or [int]$row.'Failure Count' -ne 0) {
        throw "Locust $Mode run did not produce a successful aggregate row"
    }
    $result = [PSCustomObject]@{
        mode = $Mode
        requests = [int]$row.'Request Count'
        rps = [math]::Round([double]::Parse($row.'Requests/s', [cultureinfo]::InvariantCulture), 2)
        p95_ms = [double]::Parse($row.'95%', [cultureinfo]::InvariantCulture)
        p99_ms = [double]::Parse($row.'99%', [cultureinfo]::InvariantCulture)
    }
    foreach ($suffix in @("_stats.csv", "_stats_history.csv", "_failures.csv", "_exceptions.csv")) {
        Remove-Item -LiteralPath "$prefix$suffix" -ErrorAction SilentlyContinue
    }
    return $result
}

$database = Invoke-CacheLoad "database"
$cached = Invoke-CacheLoad "cache"
$gain = if ($database.rps -gt 0) {
    [math]::Round((($cached.rps / $database.rps) - 1) * 100, 1)
} else { 0 }

[PSCustomObject]@{
    database_rps = $database.rps
    database_p95_ms = $database.p95_ms
    database_p99_ms = $database.p99_ms
    cache_rps = $cached.rps
    cache_p95_ms = $cached.p95_ms
    cache_p99_ms = $cached.p99_ms
    throughput_change_percent = $gain
    note = "Machine-local comparison; correctness is asserted, performance is reported without a fixed CI threshold"
} | Format-List
