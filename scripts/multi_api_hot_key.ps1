param(
    [ValidateRange(2, 500)] [int] $Requests = 60,
    [ValidateRange(2, 10)] [int] $ApiInstances = 3,
    [string] $ProjectName = "hireflow-cache-scale"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ($ProjectName -notmatch '^hireflow-cache-scale(?:-[a-z0-9-]+)?$') {
    throw "Refusing unsafe Compose project name: $ProjectName"
}

$docker = Get-Command docker -ErrorAction Stop
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$compose = @(
    "compose", "--project-name", $ProjectName,
    "-f", "docker-compose.yml", "-f", "docker-compose.cache-scale.yml"
)
$baseUrl = "http://127.0.0.1:18000"
$runId = [Guid]::NewGuid().ToString("N").Substring(0, 12)

try {
    & $docker @compose up --build -d --wait --wait-timeout 120 --scale "api=$ApiInstances"
    if ($LASTEXITCODE -ne 0) { throw "multi-API Compose stack failed to start" }

    $bootstrapBody = @{
        tenant_name = "Multi API Cache $runId"
        owner = @{
            email = "owner.$runId@hireflow.demo"
            display_name = "Multi API Cache Owner"
            role = "owner"
            password = "HireFlowMultiApiCache!2026"
        }
    } | ConvertTo-Json -Depth 5
    $bootstrap = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/auth/bootstrap" `
        -ContentType "application/json" -Body $bootstrapBody
    $headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
    Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/jobs" -Headers $headers `
        -ContentType "application/json" -Body (@{ title = "Hot Key Backend Engineer" } | ConvertTo-Json) | Out-Null

    & $python (Join-Path $projectRoot "load/hot_key_probe.py") `
        --base-url $baseUrl --token $bootstrap.access_token --requests $Requests
    if ($LASTEXITCODE -ne 0) { throw "multi-API hot-key probe failed" }
}
finally {
    # The isolated project name is validated above; no primary HireFlow volume is addressed.
    & $docker @compose down --volumes --remove-orphans | Out-Null
}

