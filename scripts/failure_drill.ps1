$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$baseUrl = "http://localhost:8000"
$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}

function Get-HttpStatus {
    param([Parameter(Mandatory)] [string] $Uri)
    try {
        $request = [System.Net.HttpWebRequest]::Create($Uri)
        $request.Method = "GET"
        $request.Timeout = 3000
        $response = $request.GetResponse()
        try { return [int]$response.StatusCode } finally { $response.Close() }
    }
    catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            $_.Exception.Response.Close()
            return $status
        }
        throw
    }
}

function Get-ResponseHeader {
    param([object] $Response, [string] $Name)
    foreach ($entry in $Response.Headers.GetEnumerator()) {
        $hasKey = $entry.PSObject.Properties.Name -contains "Key"
        $headerName = if ($hasKey) { [string]$entry.Key } else { [string]$entry }
        if (-not [string]::Equals($headerName, $Name, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $hasValue = $entry.PSObject.Properties.Name -contains "Value"
        $value = if ($hasValue) { $entry.Value } else { $Response.Headers[$headerName] }
        return [string](@($value) -join ",")
    }
    return ""
}

function Invoke-Scalar {
    param([Parameter(Mandatory)] [string] $Sql)
    $value = & $docker compose exec -T postgres psql -U hireflow -d hireflow -Atc $Sql
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL query failed" }
    return "$value".Trim()
}

function Wait-ForHttpStatus {
    param(
        [Parameter(Mandatory)] [string] $Uri,
        [Parameter(Mandatory)] [int] $Expected,
        [int] $Attempts = 30
    )
    $actual = 0
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        try { $actual = Get-HttpStatus $Uri } catch { $actual = 0 }
        if ($actual -eq $Expected) { return }
        Start-Sleep -Seconds 1
    }
    throw "$Uri expected HTTP $Expected after $Attempts attempts, received $actual"
}

function Wait-ForScalar {
    param(
        [Parameter(Mandatory)] [string] $Sql,
        [Parameter(Mandatory)] [string] $Expected,
        [int] $Attempts = 30
    )
    $actual = ""
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        $actual = Invoke-Scalar $Sql
        if ($actual -eq $Expected) { return }
        Start-Sleep -Seconds 1
    }
    throw "database state did not converge to '$Expected'; last value was '$actual'"
}

$results = [ordered]@{}

try {
    & $docker compose up -d --wait --wait-timeout 90 | Out-Null

    # Relay and consumer are both stopped while the API commits a real business
    # transaction. The event must remain durable, publish after relay recovery,
    # and create exactly one Inbox side effect after the consumer recovers.
    & $docker compose stop outbox-relay notification-consumer | Out-Null
    $eventMarker = Invoke-Scalar "SELECT clock_timestamp()"
    & "$PSScriptRoot\demo.ps1" | Out-Null
    $eventId = Invoke-Scalar "SELECT id::text FROM outbox_events WHERE created_at >= '$eventMarker' AND event_type = 'interview.scheduled' ORDER BY created_at, id LIMIT 1"
    if (-not $eventId) { throw "no interview Outbox event was committed during the relay outage" }
    $pendingStatus = Invoke-Scalar "SELECT status FROM outbox_events WHERE id = '$eventId'"
    if ($pendingStatus -ne "pending") { throw "stopped relay should leave the event pending, got $pendingStatus" }

    & $docker compose start outbox-relay | Out-Null
    Wait-ForScalar "SELECT status FROM outbox_events WHERE id = '$eventId'" "published"
    $beforeConsumer = Invoke-Scalar "SELECT count(*) FROM notification_inbox WHERE event_id = '$eventId'"
    if ([int]$beforeConsumer -ne 0) { throw "stopped consumer unexpectedly processed the recovered event" }

    & $docker compose start notification-consumer | Out-Null
    Wait-ForScalar "SELECT count(*) FROM notification_inbox WHERE event_id = '$eventId' AND status = 'processed'" "1"
    $results.relay_consumer_recovery = "pending -> published -> processed exactly once"

    # Exercise duplicate delivery, persisted retry evidence and manual replay.
    & "$PSScriptRoot\reliability_smoke.ps1" | Out-Null
    $results.duplicate_delivery = "Inbox idempotency and manual replay verified"

    # Stop the actual broker and verify Relay readiness, durable retry state and
    # automatic publication after RabbitMQ returns.
    & "$PSScriptRoot\go_relay_recovery_smoke.ps1" | Out-Null
    $results.rabbitmq_outage = "Relay 503, durable Outbox retry, recovery verified"

    # Warm a tenant list, stop Redis, then prove the API returns the PostgreSQL
    # result with X-Cache: ERROR while readiness reports degraded but remains 200.
    $runId = "failure-$(Get-Date -Format 'yyyyMMddHHmmss')"
    $password = "HireFlowFailureDrill!2026"
    $bootstrapBody = @{
        tenant_name = "Failure Drill $runId"
        owner = @{
            email = "owner.$runId@hireflow.demo"
            display_name = "Failure Drill Owner"
            role = "owner"
            password = $password
        }
    } | ConvertTo-Json -Depth 5
    $bootstrap = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/auth/bootstrap" `
        -ContentType "application/json" -Body $bootstrapBody
    $headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
    $job = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/jobs" -ContentType "application/json" `
        -Headers $headers -Body (@{ title = "Failure Recovery Engineer" } | ConvertTo-Json)
    Invoke-WebRequest -Uri "$baseUrl/v1/jobs" -Headers $headers -UseBasicParsing | Out-Null

    & $docker compose stop redis | Out-Null
    $fallback = Invoke-WebRequest -Uri "$baseUrl/v1/jobs" -Headers $headers -UseBasicParsing
    $fallbackItems = @($fallback.Content | ConvertFrom-Json)
    $cacheOutcome = Get-ResponseHeader $fallback "X-Cache"
    if ($fallback.StatusCode -ne 200 -or $cacheOutcome -ne "ERROR") {
        throw "Redis outage should fail open with HTTP 200 and X-Cache ERROR, got $($fallback.StatusCode)/$cacheOutcome"
    }
    if ($job.id -notin $fallbackItems.id) { throw "PostgreSQL fallback omitted the committed job" }
    $degraded = Invoke-RestMethod -Uri "$baseUrl/health/ready"
    if ($degraded.status -ne "degraded" -or $degraded.dependencies.redis -ne "degraded") {
        throw "Redis outage was not represented as degraded readiness"
    }
    if ((Get-HttpStatus "$baseUrl/health/live") -ne 200) { throw "Redis outage affected liveness" }
    $results.redis_outage = "cache failed open to PostgreSQL; readiness degraded"

    & $docker compose start redis | Out-Null
    & $docker compose up -d --wait --wait-timeout 90 redis api | Out-Null
    Wait-ForHttpStatus "$baseUrl/health/ready" 200

    # PostgreSQL is a critical synchronous dependency: readiness must fail while
    # liveness continues to answer, then recover without restarting the API.
    & $docker compose stop postgres | Out-Null
    Wait-ForHttpStatus "$baseUrl/health/ready" 503
    if ((Get-HttpStatus "$baseUrl/health/live") -ne 200) { throw "PostgreSQL outage affected liveness" }
    $results.postgres_outage = "readiness 503 while liveness remained 200"

    & $docker compose start postgres | Out-Null
    & $docker compose up -d --wait --wait-timeout 90 postgres api | Out-Null
    Wait-ForHttpStatus "$baseUrl/health/ready" 200
    $results.recovery = "all critical services returned ready"

    [PSCustomObject]$results | Format-List
}
finally {
    & $docker compose start postgres redis rabbitmq outbox-relay notification-consumer | Out-Null
    & $docker compose up -d --wait --wait-timeout 90 | Out-Null
}
