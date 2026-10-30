$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

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
        try { return [int] $response.StatusCode } finally { $response.Close() }
    }
    catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $status = [int] $_.Exception.Response.StatusCode
            $_.Exception.Response.Close()
            return $status
        }
        throw
    }
}

function Invoke-Scalar {
    param([Parameter(Mandatory)] [string] $Sql)
    $value = & $docker compose exec -T postgres psql -U hireflow -d hireflow -Atc $Sql
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL query failed" }
    return "$value".Trim()
}

try {
    & $docker compose stop rabbitmq | Out-Null
    $unreadyStatus = 200
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $unreadyStatus = Get-HttpStatus "http://localhost:9101/ready"
        if ($unreadyStatus -eq 503) { break }
        Start-Sleep -Seconds 1
    }
    if ($unreadyStatus -ne 503) { throw "Go relay readiness should be 503 during broker outage, got $unreadyStatus" }

    $outageMarker = Invoke-Scalar "SELECT clock_timestamp()"

    # The synchronous API must still commit its transaction and Outbox rows while RabbitMQ is down.
    & "$PSScriptRoot\demo.ps1"
    if ($LASTEXITCODE -ne 0) { throw "demo failed while RabbitMQ was unavailable" }
    $event = ""
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $event = Invoke-Scalar "SELECT id::text || '|' || status || '|' || attempts::text || '|' || (next_attempt_at IS NOT NULL)::text FROM outbox_events WHERE created_at >= '$outageMarker' AND attempts > 0 ORDER BY created_at, id LIMIT 1"
        if ($event) { break }
        Start-Sleep -Seconds 1
    }
    $parts = $event -split '\|'
    if ($parts.Count -ne 4) { throw "unexpected Outbox probe: $event" }
    $eventId = $parts[0]
    if ($parts[1] -ne "pending" -or [int] $parts[2] -lt 1 -or $parts[3] -ne "true") {
        throw "broker failure was not persisted as a retry: $event"
    }

    & $docker compose start rabbitmq | Out-Null
    & $docker compose up -d --wait --wait-timeout 90 outbox-relay notification-consumer | Out-Null

    $published = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ((Invoke-Scalar "SELECT status FROM outbox_events WHERE id = '$eventId'") -eq "published") {
            $published = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $published) { throw "Outbox event was not published after RabbitMQ recovery" }

    $processed = 0
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $processed = [int] (Invoke-Scalar "SELECT count(*) FROM notification_inbox WHERE event_id = '$eventId' AND status = 'processed'")
        if ($processed -eq 1) { break }
        Start-Sleep -Seconds 1
    }
    if ($processed -ne 1) { throw "recovered event was not consumed exactly once" }
    $readyStatus = Get-HttpStatus "http://localhost:9101/ready"
    if ($readyStatus -ne 200) { throw "Go relay did not become ready after recovery" }

    [PSCustomObject]@{
        broker_outage_readiness = "503 verified"
        persisted_retry = "pending with durable next_attempt_at"
        recovered_event = $eventId
        consumer_inbox = "processed exactly once"
        result = "Go relay broker-outage recovery verified"
    } | Format-List
}
finally {
    & $docker compose start rabbitmq | Out-Null
    & $docker compose up -d outbox-relay notification-consumer | Out-Null
}
