$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowCachePassword!2026"

function Invoke-HireFlowJson {
    param([string] $Method, [string] $Path, [object] $Body, [hashtable] $Headers = @{})
    Invoke-RestMethod -Method $Method -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

function Invoke-CachedList {
    param([string] $Path, [hashtable] $Headers)
    $response = Invoke-WebRequest -Method Get -Uri "$baseUrl$Path" -Headers $Headers -UseBasicParsing
    return @{
        Cache = [string]$response.Headers["X-Cache"]
        Items = @($response.Content | ConvertFrom-Json)
    }
}

$bootstrap = Invoke-HireFlowJson Post "/v1/auth/bootstrap" @{
    tenant_name = "Cache Smoke $runId"
    owner = @{
        email = "owner.$runId@hireflow.demo"
        display_name = "Cache Owner"
        role = "owner"
        password = $password
    }
}
$headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }

$jobsMiss = Invoke-CachedList "/v1/jobs" $headers
$jobsHit = Invoke-CachedList "/v1/jobs" $headers
if ($jobsMiss.Cache -ne "MISS" -or $jobsHit.Cache -ne "HIT") {
    throw "jobs cache expected MISS then HIT, got $($jobsMiss.Cache) then $($jobsHit.Cache)"
}

$job = Invoke-HireFlowJson Post "/v1/jobs" @{ title = "Cache Backend Engineer" } $headers
$jobsAfterCreate = Invoke-CachedList "/v1/jobs" $headers
if ($jobsAfterCreate.Cache -ne "MISS" -or $jobsAfterCreate.Items.Count -ne 1) {
    throw "creating a job did not invalidate its tenant list cache"
}

Invoke-HireFlowJson Patch "/v1/jobs/$($job.id)" @{ title = "Updated Cache Engineer" } $headers | Out-Null
$jobsAfterUpdate = Invoke-CachedList "/v1/jobs" $headers
if ($jobsAfterUpdate.Cache -ne "MISS" -or $jobsAfterUpdate.Items[0].title -ne "Updated Cache Engineer") {
    throw "updating a job did not invalidate its tenant list cache"
}

$candidatesMiss = Invoke-CachedList "/v1/candidates" $headers
$candidatesHit = Invoke-CachedList "/v1/candidates" $headers
if ($candidatesMiss.Cache -ne "MISS" -or $candidatesHit.Cache -ne "HIT") {
    throw "candidates cache expected MISS then HIT"
}

$candidate = Invoke-HireFlowJson Post "/v1/candidates" @{
    email = "candidate.$runId@hireflow.demo"
    full_name = "Cache Candidate"
} $headers
$candidatesAfterCreate = Invoke-CachedList "/v1/candidates" $headers
if ($candidatesAfterCreate.Cache -ne "MISS" -or $candidatesAfterCreate.Items.Count -ne 1) {
    throw "creating a candidate did not invalidate its tenant list cache"
}

$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidateDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidateDocker)) { throw "Docker CLI was not found" }
    $docker = $candidateDocker
}
$key = "hireflow:cache:v2:tenant:$($job.tenant_id):jobs:list"
$ttl = & $docker compose exec -T redis redis-cli TTL $key
if ($LASTEXITCODE -ne 0 -or [int]$ttl -lt 1 -or [int]$ttl -gt 60) {
    throw "jobs cache TTL was outside the expected 1..60 second range: $ttl"
}

$metrics = Invoke-WebRequest -Uri "$baseUrl/metrics" -UseBasicParsing
foreach ($metric in @(
    "hireflow_cache_requests_total",
    "hireflow_cache_hit_ratio",
    "hireflow_cache_db_load_duration_seconds"
)) {
    if ($metrics.Content -notmatch $metric) { throw "missing Prometheus metric: $metric" }
}

[PSCustomObject]@{
    jobs_cache = "MISS -> HIT; create/update -> MISS"
    candidates_cache = "MISS -> HIT; create -> MISS"
    tenant_key = $key
    ttl_seconds = [int]$ttl
    metrics = "hit/miss, hit ratio and DB fallback latency exposed"
    result = "Cache Aside behavior verified"
} | Format-List
