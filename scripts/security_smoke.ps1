$ErrorActionPreference = "Stop"
$baseUrl = "http://localhost:8000"
$password = "HireFlowSecurityPassword!2026"
$runId = Get-Date -Format 'yyyyMMddHHmmss'

function Invoke-HireFlowJson {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [object] $Body,
        [hashtable] $Headers = @{}
    )
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
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

function New-TenantOwnerToken {
    param([string] $Suffix)
    $bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
        tenant_name = "Security Demo $Suffix"
        owner = @{
            email = "owner.$Suffix@hireflow.demo"
            display_name = "Owner $Suffix"
            role = "owner"
            password = $password
        }
    }
    return @{ "Authorization" = "Bearer $($bootstrap.access_token)" }
}

$ownerAHeaders = New-TenantOwnerToken "$runId-a"
$ownerBHeaders = New-TenantOwnerToken "$runId-b"

$securityResponse = Invoke-WebRequest -Uri "$baseUrl/health/live" -UseBasicParsing `
    -Headers @{ Origin = "https://untrusted.example" }
foreach ($header in @("X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy", "Referrer-Policy")) {
    if (-not $securityResponse.Headers[$header]) { throw "missing security header: $header" }
}
if ($securityResponse.Headers["Access-Control-Allow-Origin"]) {
    throw "untrusted cross-origin request was allowed"
}

Assert-HttpStatus { Invoke-RestMethod -Uri "$baseUrl/v1/interviews/$([guid]::NewGuid())" } 401 "unauthenticated request"

$interviewer = Invoke-HireFlowJson "/v1/users" @{
    email = "interviewer.$runId@hireflow.demo"
    display_name = "Ian Interviewer"
    role = "interviewer"
    password = $password
} $ownerAHeaders

$recruiter = Invoke-HireFlowJson "/v1/users" @{
    email = "recruiter.$runId@hireflow.demo"
    display_name = "Rita Recruiter"
    role = "recruiter"
    password = $password
} $ownerAHeaders

$recruiterLogin = Invoke-HireFlowJson "/v1/auth/tokens" @{
    tenant_id = $recruiter.tenant_id
    email = $recruiter.email
    password = $password
}
$recruiterHeaders = @{ "Authorization" = "Bearer $($recruiterLogin.access_token)" }

$interviewerLogin = Invoke-HireFlowJson "/v1/auth/tokens" @{
    tenant_id = $interviewer.tenant_id
    email = $interviewer.email
    password = $password
}
$interviewerHeaders = @{ "Authorization" = "Bearer $($interviewerLogin.access_token)" }

Assert-HttpStatus {
    Invoke-HireFlowJson "/v1/jobs" @{ title = "Forbidden Job" } $interviewerHeaders
} 403 "interviewer creating a job"

$job = Invoke-HireFlowJson "/v1/jobs" @{ title = "Recruiter-created Backend Role" } $recruiterHeaders
$candidate = Invoke-HireFlowJson "/v1/candidates" @{
    email = "candidate.$runId@hireflow.demo"
    full_name = "Chen Candidate"
} $recruiterHeaders

Invoke-HireFlowJson "/v1/candidates/$($candidate.id)/availability" @{
    starts_at = "2026-11-03T09:00:00+08:00"
    ends_at = "2026-11-03T12:00:00+08:00"
} $recruiterHeaders | Out-Null
Invoke-HireFlowJson "/v1/interviewers/$($interviewer.id)/availability" @{
    starts_at = "2026-11-03T10:00:00+08:00"
    ends_at = "2026-11-03T12:00:00+08:00"
} $interviewerHeaders | Out-Null

$suggestion = Invoke-HireFlowJson "/v1/scheduling/suggestions" @{
    candidate_id = $candidate.id
    interviewer_id = $interviewer.id
    duration_minutes = 30
} $recruiterHeaders
$interview = Invoke-HireFlowJson "/v1/interviews" @{
    candidate_id = $candidate.id
    interviewer_id = $interviewer.id
    job_id = $job.id
    starts_at = $suggestion.starts_at
    ends_at = $suggestion.ends_at
} $recruiterHeaders

Assert-HttpStatus {
    Invoke-RestMethod -Uri "$baseUrl/v1/interviews/$($interview.id)" -Headers $ownerBHeaders
} 404 "cross-tenant interview lookup"

[PSCustomObject]@{
    unauthenticated_request = "401 verified"
    interviewer_job_creation = "403 verified"
    recruiter_candidate_and_schedule = "verified"
    cross_tenant_lookup = "404 verified"
    browser_security = "security headers and default-deny CORS verified"
} | Format-List
