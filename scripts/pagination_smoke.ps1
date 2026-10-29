$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowPaginationPassword!2026"

function Invoke-HireFlowJson {
    param([string] $Method, [string] $Path, [object] $Body, [hashtable] $Headers = @{})
    Invoke-RestMethod -Method $Method -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

function Invoke-Page {
    param([string] $Path, [hashtable] $Headers)
    $response = Invoke-WebRequest -Method Get -Uri "$baseUrl$Path" -Headers $Headers -UseBasicParsing
    return @{
        Items = @($response.Content | ConvertFrom-Json)
        Cache = [string]@($response.Headers["X-Cache"])[0]
        Limit = [int]@($response.Headers["X-Page-Limit"])[0]
        NextCursor = [string]@($response.Headers["X-Next-Cursor"])[0]
    }
}

function Assert-UniquePages {
    param([hashtable] $First, [hashtable] $Second, [string] $Resource)
    if ($First.Items.Count -ne 20 -or $Second.Items.Count -ne 3) {
        throw "$Resource expected page sizes 20 and 3, got $($First.Items.Count) and $($Second.Items.Count)"
    }
    $ids = @($First.Items.id) + @($Second.Items.id)
    if (@($ids | Select-Object -Unique).Count -ne 23) {
        throw "$Resource pagination returned duplicate or missing records"
    }
    if ($First.Cache -ne "MISS" -or $Second.Cache -ne "BYPASS") {
        throw "$Resource expected first-page MISS and cursor-page BYPASS"
    }
    if ($First.Limit -ne 20 -or [string]::IsNullOrWhiteSpace($First.NextCursor)) {
        throw "$Resource did not expose the default limit and next cursor"
    }
    if (-not [string]::IsNullOrWhiteSpace($Second.NextCursor)) {
        throw "$Resource emitted a cursor after the final page"
    }
}

$bootstrap = Invoke-HireFlowJson Post "/v1/auth/bootstrap" @{
    tenant_name = "Pagination Smoke $runId"
    owner = @{
        email = "owner.$runId@hireflow.demo"
        display_name = "Pagination Owner"
        role = "owner"
        password = $password
    }
}
$headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }

foreach ($number in 1..23) {
    Invoke-HireFlowJson Post "/v1/jobs" @{ title = "Paginated Job $number" } $headers | Out-Null
    Invoke-HireFlowJson Post "/v1/candidates" @{
        email = "pagination-$number-$runId@hireflow.demo"
        full_name = "Paginated Candidate $number"
    } $headers | Out-Null
}

$jobsFirst = Invoke-Page "/v1/jobs" $headers
$jobsCursor = [uri]::EscapeDataString($jobsFirst.NextCursor)
$jobsSecond = Invoke-Page "/v1/jobs?cursor=$jobsCursor" $headers
Assert-UniquePages $jobsFirst $jobsSecond "jobs"

$candidatesFirst = Invoke-Page "/v1/candidates" $headers
$candidatesCursor = [uri]::EscapeDataString($candidatesFirst.NextCursor)
$candidatesSecond = Invoke-Page "/v1/candidates?cursor=$candidatesCursor" $headers
Assert-UniquePages $candidatesFirst $candidatesSecond "candidates"

try {
    Invoke-WebRequest -Uri "$baseUrl/v1/jobs?cursor=invalid" -Headers $headers -UseBasicParsing | Out-Null
    throw "invalid cursor unexpectedly succeeded"
} catch {
    if ($null -eq $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 422) {
        throw "invalid cursor expected HTTP 422: $($_.Exception.Message)"
    }
}

[PSCustomObject]@{
    page_size = 20
    jobs = "20 + 3, no duplicates"
    candidates = "20 + 3, no duplicates"
    cache_policy = "default first page cached; cursor pages bypass cache"
    invalid_cursor = "422 verified"
    result = "keyset pagination verified"
} | Format-List
