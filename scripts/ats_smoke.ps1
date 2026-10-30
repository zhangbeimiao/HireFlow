$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowAtsPassword!2026"

function Invoke-HireFlowJson {
    param(
        [Parameter(Mandatory)] [string] $Method,
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [object] $Body,
        [hashtable] $Headers = @{}
    )
    Invoke-RestMethod -Method $Method -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 10)
}

function Assert-HttpStatus {
    param(
        [Parameter(Mandatory)] [scriptblock] $Request,
        [Parameter(Mandatory)] [int] $ExpectedStatus,
        [Parameter(Mandatory)] [string] $Description
    )
    try {
        & $Request | Out-Null
        throw "$Description unexpectedly succeeded"
    } catch {
        $response = $_.Exception.Response
        $actualStatus = if ($null -ne $response) { [int]$response.StatusCode } else { 0 }
        if ($actualStatus -ne $ExpectedStatus) {
            throw "$Description expected HTTP $ExpectedStatus, received HTTP ${actualStatus}: $($_.Exception.Message)"
        }
    }
}

function New-TenantOwner {
    param([string] $Suffix)
    $bootstrap = Invoke-HireFlowJson Post "/v1/auth/bootstrap" @{
        tenant_name = "ATS Smoke $Suffix"
        owner = @{
            email = "owner.$Suffix@hireflow.demo"
            display_name = "ATS Owner"
            role = "owner"
            password = $password
        }
    }
    return @{
        headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
    }
}

$tenantA = New-TenantOwner "$runId-a"
$tenantB = New-TenantOwner "$runId-b"
$headers = $tenantA.headers

$manager = Invoke-HireFlowJson Post "/v1/users" @{
    email = "manager.$runId@hireflow.demo"
    display_name = "Mia Manager"
    role = "hiring_manager"
    password = $password
} $headers
$interviewer = Invoke-HireFlowJson Post "/v1/users" @{
    email = "interviewer.$runId@hireflow.demo"
    display_name = "Ian Interviewer"
    role = "interviewer"
    password = $password
} $headers
$interviewerLogin = Invoke-HireFlowJson Post "/v1/auth/tokens" @{
    tenant_id = $interviewer.tenant_id
    email = $interviewer.email
    password = $password
}
$interviewerHeaders = @{ Authorization = "Bearer $($interviewerLogin.access_token)" }

$job = Invoke-HireFlowJson Post "/v1/jobs" @{
    title = "Senior Backend Engineer"
    hiring_manager_id = $manager.id
} $headers
$round = Invoke-HireFlowJson Post "/v1/jobs/$($job.id)/rounds" @{
    name = "System Design"
    sequence = 1
    duration_minutes = 60
    criteria = @(
        @{ key = "architecture"; label = "Architecture trade-offs" },
        @{ key = "reliability"; label = "Reliability reasoning" }
    )
} $headers
$candidate = Invoke-HireFlowJson Post "/v1/candidates" @{
    email = "candidate.$runId@hireflow.demo"
    full_name = "Chen Candidate"
} $headers
$application = Invoke-HireFlowJson Post "/v1/applications" @{
    job_id = $job.id
    candidate_id = $candidate.id
} $headers

$application = Invoke-HireFlowJson Post "/v1/applications/$($application.id)/stage" @{
    stage = "screening"; version = $application.version; reason = "resume passed"
} $headers
$application = Invoke-HireFlowJson Post "/v1/applications/$($application.id)/stage" @{
    stage = "interview"; version = $application.version; reason = "recruiter screen passed"
} $headers

$start = [DateTimeOffset]::Parse(
    [DateTime]::UtcNow.Date.AddDays(2).AddHours(2).ToString("yyyy-MM-ddTHH:mm:ssZ")
)
$end = $start.AddHours(1)
$interview = Invoke-HireFlowJson Post "/v1/interviews" @{
    candidate_id = $candidate.id
    interviewer_id = $interviewer.id
    job_id = $job.id
    application_id = $application.id
    round_id = $round.id
    starts_at = $start.ToString("o")
    ends_at = $end.ToString("o")
    meeting_url = "https://meet.example.test/ats-$runId"
} $headers

$draftScorecard = Invoke-HireFlowJson Put "/v1/interviews/$($interview.id)/scorecard" @{
    recommendation = "hire"
    overall_score = 4
    summary = "Strong system design fundamentals"
    answers = @{ architecture = 4; reliability = 4 }
    submit = $false
} $interviewerHeaders
$submittedScorecard = Invoke-HireFlowJson Put "/v1/interviews/$($interview.id)/scorecard" @{
    recommendation = "strong_hire"
    overall_score = 5
    summary = "Ready for the role"
    answers = @{ architecture = 5; reliability = 5 }
    submit = $true
} $interviewerHeaders
Assert-HttpStatus {
    Invoke-HireFlowJson Put "/v1/interviews/$($interview.id)/scorecard" @{
        recommendation = "no_hire"; overall_score = 1; submit = $false
    } $interviewerHeaders
} 409 "editing a submitted scorecard"

$application = Invoke-HireFlowJson Post "/v1/applications/$($application.id)/stage" @{
    stage = "offer"; version = $application.version; reason = "interview panel approved"
} $headers
$offer = Invoke-HireFlowJson Post "/v1/offers" @{
    application_id = $application.id
    salary_amount = 3500000
    currency = "CNY"
    start_date = "2026-12-01"
    expires_at = "2026-11-15T23:59:59+08:00"
} $headers
$offer = Invoke-HireFlowJson Post "/v1/offers/$($offer.id)/status" @{
    status = "sent"; version = $offer.version
} $headers
$offer = Invoke-HireFlowJson Post "/v1/offers/$($offer.id)/status" @{
    status = "accepted"; version = $offer.version
} $headers

$applications = Invoke-RestMethod -Uri "$baseUrl/v1/applications" -Headers $headers
$finalApplication = @($applications | Where-Object { $_.id -eq $application.id })[0]
if ($finalApplication.stage -ne "hired") {
    throw "accepted offer did not move application to hired"
}
$audits = Invoke-RestMethod -Uri "$baseUrl/v1/applications/$($application.id)/audit" -Headers $headers
$offerAudits = Invoke-RestMethod -Uri "$baseUrl/v1/offers/$($offer.id)/audit" -Headers $headers
$otherTenantApplications = Invoke-RestMethod -Uri "$baseUrl/v1/applications" -Headers $tenantB.headers
if (@($otherTenantApplications | Where-Object { $_.id -eq $application.id }).Count -ne 0) {
    throw "cross-tenant application was visible"
}

Start-Sleep -Seconds 3
$notifications = Invoke-RestMethod -Uri "$baseUrl/v1/notifications" -Headers $headers

[PSCustomObject]@{
    application_id = $application.id
    application_stage = $finalApplication.stage
    application_audits = @($audits).Count
    interview_round = $round.name
    scorecard_submitted = ($null -ne $submittedScorecard.submitted_at)
    submitted_scorecard_locked = "409 verified"
    offer_status = $offer.status
    offer_audits = @($offerAudits).Count
    cross_tenant_isolation = "verified"
    notifications = @($notifications).Count
    result = "complete ATS workflow verified"
} | Format-List
