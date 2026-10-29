$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowWorkbenchPassword!2026"

function Invoke-HireFlowJson {
    param([string] $Path, [object] $Body, [hashtable] $Headers = @{})
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

$bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
    tenant_name = "Workbench Smoke $runId"
    owner = @{ email = "owner.$runId@hireflow.demo"; display_name = "Workbench Owner"; role = "owner"; password = $password }
}
$headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
$users = Invoke-RestMethod -Uri "$baseUrl/v1/users" -Headers $headers
$job = Invoke-HireFlowJson "/v1/jobs" @{ title = "Workbench Engineer"; hiring_manager_id = $users[0].id } $headers
$candidate = Invoke-HireFlowJson "/v1/candidates" @{ full_name = "Workbench Candidate"; email = "candidate.$runId@hireflow.demo" } $headers

$availability = @{ starts_at = "2026-12-11T10:00:00+08:00"; ends_at = "2026-12-11T11:00:00+08:00" }
Invoke-HireFlowJson "/v1/candidates/$($candidate.id)/availability" $availability $headers | Out-Null
Invoke-HireFlowJson "/v1/interviewers/$($users[0].id)/availability" $availability $headers | Out-Null
$suggestion = Invoke-HireFlowJson "/v1/scheduling/suggestions" @{ candidate_id = $candidate.id; interviewer_id = $users[0].id; duration_minutes = 30 } $headers

$payload = @{ candidate_id = $candidate.id; interviewer_id = $users[0].id; job_id = $job.id; starts_at = $suggestion.starts_at; ends_at = $suggestion.ends_at } | ConvertTo-Json
$first = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$baseUrl/v1/interviews" -Headers $headers -ContentType "application/json" -Body $payload
try {
    Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$baseUrl/v1/interviews" -Headers $headers -ContentType "application/json" -Body $payload -ErrorAction Stop | Out-Null
    $conflict = 201
} catch { $conflict = [int]$_.Exception.Response.StatusCode }

$overview = Invoke-RestMethod -Uri "$baseUrl/v1/dashboard/overview" -Headers $headers
$jobs = Invoke-RestMethod -Uri "$baseUrl/v1/jobs" -Headers $headers
$candidates = Invoke-RestMethod -Uri "$baseUrl/v1/candidates" -Headers $headers
$interviews = Invoke-RestMethod -Uri "$baseUrl/v1/interviews" -Headers $headers
if ($first.StatusCode -ne 201 -or $conflict -ne 409) { throw "expected scheduling statuses 201 and 409" }
if ($jobs.Count -ne 1 -or $candidates.Count -ne 1 -or $interviews.Count -ne 1) { throw "list endpoints returned unexpected rows" }
if ($overview.open_jobs -ne 1 -or $overview.candidates -ne 1 -or $overview.upcoming_interviews -ne 1) { throw "dashboard aggregation returned unexpected counts" }

[PSCustomObject]@{
    listed_jobs = $jobs.Count
    listed_candidates = $candidates.Count
    listed_interviews = $interviews.Count
    suggested_at = $suggestion.starts_at
    first_schedule = $first.StatusCode
    conflicting_schedule = $conflict
    result = "workbench workflow verified"
} | Format-List
