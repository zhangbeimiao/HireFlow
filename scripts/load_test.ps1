param(
    [ValidateRange(2, 200)] [int] $Users = 20,
    [ValidateRange(1, 100)] [int] $SpawnRate = 10,
    [ValidateRange(5, 120)] [int] $RunSeconds = 30
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowLoadPassword!2026"
$slot = @{ starts_at = "2026-12-20T10:00:00+08:00"; ends_at = "2026-12-20T10:30:00+08:00" }
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$locustfile = Join-Path $projectRoot "load/locustfile.py"

function Invoke-HireFlowJson {
    param([string] $Path, [object] $Body, [hashtable] $Headers = @{})
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

$bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
    tenant_name = "Load Test $runId"
    owner = @{ email = "owner.$runId@hireflow.demo"; display_name = "Load Owner"; role = "owner"; password = $password }
}
$headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
$interviewer = Invoke-HireFlowJson "/v1/users" @{
    email = "interviewer.$runId@hireflow.demo"; display_name = "Load Interviewer"; role = "interviewer"; password = $password
} $headers
$job = Invoke-HireFlowJson "/v1/jobs" @{ title = "Load Test Engineer" } $headers
Invoke-HireFlowJson "/v1/interviewers/$($interviewer.id)/availability" $slot $headers | Out-Null

$candidateIds = @()
for ($index = 1; $index -le $Users; $index++) {
    $candidate = Invoke-HireFlowJson "/v1/candidates" @{
        email = "candidate.$index.$runId@hireflow.demo"; full_name = "Load Candidate $index"
    } $headers
    Invoke-HireFlowJson "/v1/candidates/$($candidate.id)/availability" $slot $headers | Out-Null
    $candidateIds += $candidate.id
}

# Locust prints request count, RPS, average latency and percentile columns including P95/P99.
$env:HIREFLOW_LOAD_TOKEN = $bootstrap.access_token
$env:HIREFLOW_LOAD_INTERVIEWER_ID = $interviewer.id
$env:HIREFLOW_LOAD_JOB_ID = $job.id
$env:HIREFLOW_LOAD_CANDIDATE_IDS = $candidateIds | ConvertTo-Json -Compress
$env:HIREFLOW_LOAD_STARTS_AT = $slot.starts_at
$env:HIREFLOW_LOAD_ENDS_AT = $slot.ends_at

& $python -m locust -f $locustfile --headless --host $baseUrl `
    -u $Users -r $SpawnRate --run-time "$($RunSeconds)s" --only-summary
if ($LASTEXITCODE -ne 0) { throw "Locust failed with exit code $LASTEXITCODE" }

[PSCustomObject]@{
    virtual_users = $Users
    shared_slot = $slot.starts_at
    expected_business_result = "exactly 1 x 201 Created; $($Users - 1) x 409 Conflict"
    report = "Locust summary above contains RPS, P95 and P99 latency"
} | Format-List
