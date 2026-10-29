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
$relayMetrics = Invoke-WebRequest -Uri "http://localhost:9101/metrics" -UseBasicParsing
$consumerMetrics = Invoke-WebRequest -Uri "http://localhost:9102/metrics" -UseBasicParsing

if ($health.Headers["X-Request-ID"] -ne $requestId) {
    throw "request ID was not returned by the API"
}
if ($metrics.Content -notmatch "hireflow_http_requests_total") {
    throw "Prometheus HTTP counter was not exposed"
}
foreach ($metric in @("hireflow_scheduling_conflicts_total", "hireflow_db_pool_connections")) {
    if ($metrics.Content -notmatch $metric) { throw "API metric was not exposed: $metric" }
}
foreach ($metric in @("hireflow_outbox_relay_events_total", "hireflow_outbox_backlog")) {
    if ($relayMetrics.Content -notmatch $metric) { throw "Relay metric was not exposed: $metric" }
}
foreach ($metric in @("hireflow_notification_deliveries_total", "hireflow_notification_failure_backlog")) {
    if ($consumerMetrics.Content -notmatch $metric) { throw "Consumer metric was not exposed: $metric" }
}

[PSCustomObject]@{
    request_id = $health.Headers["X-Request-ID"]
    health_status = $health.StatusCode
    metrics = "API, DB pool, scheduling, Outbox and notification metrics exposed"
    result = "observability verified"
} | Format-List
