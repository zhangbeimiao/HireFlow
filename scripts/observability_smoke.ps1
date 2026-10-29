$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$requestId = [guid]::NewGuid().ToString()
$health = $null
for ($attempt = 1; $attempt -le 10; $attempt++) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:8000/health" `
            -Headers @{ "X-Request-ID" = $requestId } -UseBasicParsing -ErrorAction Stop
        break
    }
    catch {
        if ($attempt -eq 10) {
            throw
        }
        Start-Sleep -Seconds 1
    }
}
$metrics = Invoke-WebRequest -Uri "http://localhost:8000/metrics" -UseBasicParsing

if ($health.Headers["X-Request-ID"] -ne $requestId) {
    throw "request ID was not returned by the API"
}
if ($metrics.Content -notmatch "hireflow_http_requests_total") {
    throw "Prometheus HTTP counter was not exposed"
}

[PSCustomObject]@{
    request_id = $health.Headers["X-Request-ID"]
    health_status = $health.StatusCode
    metrics = "hireflow_http_requests_total exposed"
    result = "observability verified"
} | Format-List
