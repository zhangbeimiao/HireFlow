$ErrorActionPreference = "Stop"

$baseUrl = "http://localhost:8000"
$attempts = 11
$statuses = @()
$body = @{
    tenant_id = [guid]::NewGuid().ToString()
    email = "rate.limit.test@example.com"
    password = "invalid-password"
} | ConvertTo-Json

for ($i = 1; $i -le $attempts; $i++) {
    # Login failures deliberately still count, preventing credential-stuffing probes.
    try {
        $response = Invoke-WebRequest -Method Post -Uri "$baseUrl/v1/auth/tokens" `
            -ContentType "application/json" -Body $body -ErrorAction Stop
        $statuses += [int]$response.StatusCode
    }
    catch {
        # Windows PowerShell 5.1 throws for 4xx responses, whereas PowerShell 7 can opt out.
        if ($null -eq $_.Exception.Response) {
            throw
        }
        $statuses += [int]$_.Exception.Response.StatusCode
    }
}

if ($statuses[0] -ne 401) {
    throw "expected an invalid login to return 401, got $($statuses[0])"
}
if ($statuses[-1] -ne 429) {
    throw "expected request $attempts to return 429, got $($statuses[-1])"
}

[PSCustomObject]@{
    attempts = $attempts
    initial_status = $statuses[0]
    blocked_status = $statuses[-1]
    result = "rate limit verified"
} | Format-List
