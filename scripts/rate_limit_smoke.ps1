$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$baseUrl = "http://localhost:8000"
$maxAttempts = 30
$statuses = @()
$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidateDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidateDocker)) { throw "Docker CLI was not found" }
    $docker = $candidateDocker
}

# Make the black-box assertion repeatable without touching business cache data.
$loginKeys = @(
    & $docker compose exec -T redis redis-cli --scan `
        --pattern "hireflow:rate-limit:ip:*:POST:/v1/auth/tokens"
)
foreach ($key in $loginKeys) {
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        & $docker compose exec -T redis redis-cli UNLINK $key | Out-Null
    }
}
$body = @{
    tenant_id = [guid]::NewGuid().ToString()
    email = "rate.limit.test@example.com"
    password = "invalid-password"
} | ConvertTo-Json

for ($i = 1; $i -le $maxAttempts; $i++) {
    # Login failures deliberately still count, preventing credential-stuffing probes.
    try {
        $response = Invoke-WebRequest -Method Post -Uri "$baseUrl/v1/auth/tokens" `
            -ContentType "application/json" -Body $body -UseBasicParsing -ErrorAction Stop
        $statuses += [int]$response.StatusCode
    }
    catch {
        # Windows PowerShell 5.1 throws for 4xx responses, whereas PowerShell 7 can opt out.
        if ($null -eq $_.Exception.Response) {
            throw
        }
        $statuses += [int]$_.Exception.Response.StatusCode
    }
    if ($statuses[-1] -eq 429) { break }
}

if ($statuses[0] -ne 401) {
    throw "expected an invalid login to return 401, got $($statuses[0])"
}
if ($statuses[-1] -ne 429) {
    throw "expected a token-bucket rejection within $maxAttempts requests, got $($statuses[-1])"
}

[PSCustomObject]@{
    attempts = $statuses.Count
    initial_status = $statuses[0]
    blocked_status = $statuses[-1]
    result = "rate limit verified"
} | Format-List
