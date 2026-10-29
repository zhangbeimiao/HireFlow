$ErrorActionPreference = "Stop"
$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowConcurrencyPassword!2026"

function Invoke-HireFlowJson {
    param([string] $Path, [object] $Body, [hashtable] $Headers = @{})
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

function Add-Availability {
    param([string] $Path, [hashtable] $Headers)
    Invoke-HireFlowJson $Path @{
        starts_at = "2026-11-05T10:00:00+08:00"
        ends_at = "2026-11-05T10:30:00+08:00"
    } $Headers | Out-Null
}

$bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
    tenant_name = "Concurrency Demo $runId"
    owner = @{
        email = "owner.$runId@hireflow.demo"
        display_name = "Concurrency Owner"
        role = "owner"
        password = $password
    }
}
$headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
$interviewer = Invoke-HireFlowJson "/v1/users" @{
    email = "interviewer.$runId@hireflow.demo"
    display_name = "Concurrent Interviewer"
    role = "interviewer"
    password = $password
} $headers
$job = Invoke-HireFlowJson "/v1/jobs" @{ title = "Concurrency Backend Engineer" } $headers
$candidateA = Invoke-HireFlowJson "/v1/candidates" @{
    email = "candidate.a.$runId@hireflow.demo"
    full_name = "Candidate A"
} $headers
$candidateB = Invoke-HireFlowJson "/v1/candidates" @{
    email = "candidate.b.$runId@hireflow.demo"
    full_name = "Candidate B"
} $headers

Add-Availability "/v1/interviewers/$($interviewer.id)/availability" $headers
Add-Availability "/v1/candidates/$($candidateA.id)/availability" $headers
Add-Availability "/v1/candidates/$($candidateB.id)/availability" $headers

# Both candidates see the same final 30-minute recommendation. A recommendation does not reserve it.
$suggestionA = Invoke-HireFlowJson "/v1/scheduling/suggestions" @{
    candidate_id = $candidateA.id; interviewer_id = $interviewer.id; duration_minutes = 30
} $headers
$suggestionB = Invoke-HireFlowJson "/v1/scheduling/suggestions" @{
    candidate_id = $candidateB.id; interviewer_id = $interviewer.id; duration_minutes = 30
} $headers
if ($suggestionA.starts_at -ne $suggestionB.starts_at) {
    throw "expected both candidates to be recommended the same last slot"
}

$startInterview = {
    param($url, $json, $authorization)
    try {
        $response = Invoke-WebRequest -Method Post -Uri "$url/v1/interviews" `
            -ContentType "application/json" -Headers @{ Authorization = $authorization } `
            -Body $json -ErrorAction Stop
        [PSCustomObject]@{ status = [int]$response.StatusCode; body = $response.Content }
    }
    catch {
        if ($null -eq $_.Exception.Response) { throw }
        [PSCustomObject]@{ status = [int]$_.Exception.Response.StatusCode; body = "" }
    }
}

$bodyA = @{ candidate_id = $candidateA.id; interviewer_id = $interviewer.id; job_id = $job.id;
    starts_at = $suggestionA.starts_at; ends_at = $suggestionA.ends_at } | ConvertTo-Json
$bodyB = @{ candidate_id = $candidateB.id; interviewer_id = $interviewer.id; job_id = $job.id;
    starts_at = $suggestionB.starts_at; ends_at = $suggestionB.ends_at } | ConvertTo-Json

$jobA = Start-Job -ScriptBlock $startInterview -ArgumentList $baseUrl, $bodyA, $headers.Authorization
$jobB = Start-Job -ScriptBlock $startInterview -ArgumentList $baseUrl, $bodyB, $headers.Authorization
$null = Wait-Job -Job $jobA, $jobB
$results = @((Receive-Job -Job $jobA), (Receive-Job -Job $jobB))
Remove-Job $jobA, $jobB -Force
$statuses = @($results | ForEach-Object { $_.status } | Sort-Object)

if (($statuses -join ",") -ne "201,409") {
    throw "expected one 201 and one 409, received: $($statuses -join ',')"
}

[PSCustomObject]@{
    shared_last_slot = $suggestionA.starts_at
    first_request = $statuses[0]
    second_request = $statuses[1]
    result = "one interview created; competing request rejected by conflict protection"
} | Format-List
