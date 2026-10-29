$ErrorActionPreference = "Stop"

$baseUrl = "http://localhost:8000"

function Invoke-HireFlowJson {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [object] $Body,
        [hashtable] $Headers = @{}
    )
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

$runId = Get-Date -Format 'yyyyMMddHHmmss'
$password = "HireFlowDemoPassword!2026"
$bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
    tenant_name = "Demo Recruiting $runId"
    owner = @{
        email = "owner.$runId@hireflow.demo"
        display_name = "Olivia Owner"
        role = "owner"
        password = $password
    }
}
$tenantHeaders = @{ "Authorization" = "Bearer $($bootstrap.access_token)" }

$manager = Invoke-HireFlowJson "/v1/users" @{
    email = "manager.$runId@hireflow.demo"
    display_name = "Mia Manager"
    role = "hiring_manager"
    password = $password
} $tenantHeaders

$interviewer = Invoke-HireFlowJson "/v1/users" @{
    email = "interviewer.$runId@hireflow.demo"
    display_name = "Ian Interviewer"
    role = "interviewer"
    password = $password
} $tenantHeaders

$job = Invoke-HireFlowJson "/v1/jobs" @{
    title = "Backend Engineer"
    hiring_manager_id = $manager.id
} $tenantHeaders

$candidate = Invoke-HireFlowJson "/v1/candidates" @{
    email = "candidate.$runId@hireflow.demo"
    full_name = "Chen Candidate"
} $tenantHeaders

$application = Invoke-HireFlowJson "/v1/applications" @{
    job_id = $job.id
    candidate_id = $candidate.id
} $tenantHeaders

$candidateAvailability = Invoke-HireFlowJson "/v1/candidates/$($candidate.id)/availability" @{
    starts_at = "2026-11-02T09:00:00+08:00"
    ends_at = "2026-11-02T12:00:00+08:00"
} $tenantHeaders

$interviewerAvailability = Invoke-HireFlowJson "/v1/interviewers/$($interviewer.id)/availability" @{
    starts_at = "2026-11-02T10:00:00+08:00"
    ends_at = "2026-11-02T11:30:00+08:00"
} $tenantHeaders

$suggestion = Invoke-HireFlowJson "/v1/scheduling/suggestions" @{
    candidate_id = $candidate.id
    interviewer_id = $interviewer.id
    duration_minutes = 30
} $tenantHeaders

$interview = Invoke-HireFlowJson "/v1/interviews" @{
    candidate_id = $candidate.id
    interviewer_id = $interviewer.id
    job_id = $job.id
    starts_at = $suggestion.starts_at
    ends_at = $suggestion.ends_at
    meeting_url = "https://meet.example.test/hireflow-demo"
} $tenantHeaders

$confirmedInterview = Invoke-HireFlowJson "/v1/interviews/$($interview.id)/status" @{
    status = "confirmed"
    version = $interview.version
    reason = "candidate accepted invitation"
} $tenantHeaders

$audit = Invoke-RestMethod -Uri "$baseUrl/v1/interviews/$($interview.id)/audit" -Headers $tenantHeaders

# The relay and consumer are asynchronous processes. This makes the notification leg visible in one demo run.
Start-Sleep -Seconds 2
$notifications = Invoke-RestMethod -Uri "$baseUrl/v1/notifications" -Headers $tenantHeaders

[PSCustomObject]@{
    application_id = $application.id
    scheduled_at = $suggestion.starts_at
    interview_id = $confirmedInterview.id
    interview_status = $confirmedInterview.status
    audit_records = $audit.Count
    notifications = $notifications.Count
} | Format-List
