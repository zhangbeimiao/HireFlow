$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$baseUrl = "http://localhost:8000"
$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidate)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidate
}

function Invoke-HireFlowJson {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [object] $Body,
        [hashtable] $Headers = @{}
    )
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 6)
}

function Get-Deliveries {
    param([Parameter(Mandatory)] [hashtable] $Headers)
    return @(Invoke-RestMethod -Uri "$baseUrl/v1/admin/notification-deliveries" -Headers $Headers)
}

$runId = "email-failure-$(Get-Date -Format 'yyyyMMddHHmmss')"
$password = "HireFlowEmailFailure!2026"
$headers = @{}
$deliveryId = $null

try {
    & $docker compose stop email-dispatcher | Out-Null
    & $docker compose start mailpit | Out-Null

    $bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
        tenant_name = "Email Failure Drill $runId"
        owner = @{
            email = "owner.$runId@hireflow.demo"
            display_name = "Email Failure Owner"
            role = "owner"
            password = $password
        }
    }
    $headers = @{ Authorization = "Bearer $($bootstrap.access_token)" }
    $job = Invoke-HireFlowJson "/v1/jobs" @{ title = "Email Reliability Engineer" } $headers
    $candidate = Invoke-HireFlowJson "/v1/candidates" @{
        email = "candidate.$runId@hireflow.demo"
        full_name = "Email Failure Candidate"
    } $headers
    $application = Invoke-HireFlowJson "/v1/applications" @{
        job_id = $job.id
        candidate_id = $candidate.id
    } $headers

    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $delivery = Get-Deliveries $headers | Where-Object { $_.recipient -eq $candidate.email } `
            | Select-Object -First 1
        if ($null -ne $delivery) {
            $deliveryId = $delivery.id
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $deliveryId) { throw "notification consumer did not persist an email delivery" }

    & $docker compose run --rm --no-deps `
        -e HIREFLOW_SMTP_HOST=unreachable.invalid `
        -e HIREFLOW_SMTP_PORT=1 `
        -e HIREFLOW_SMTP_TIMEOUT_SECONDS=1 `
        -e HIREFLOW_EMAIL_RUN_ONCE=true `
        email-dispatcher | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "one-shot failure provider did not exit cleanly" }
    $retry = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $retry = Get-Deliveries $headers | Where-Object { $_.id -eq $deliveryId } | Select-Object -First 1
        if ($null -ne $retry -and $retry.attempts -ge 1 -and $retry.status -in @("retry", "failed")) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $retry -or $retry.attempts -lt 1) {
        throw "SMTP outage was not persisted as a delivery failure"
    }

    & $docker compose start email-dispatcher | Out-Null
    $sent = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $sent = Get-Deliveries $headers | Where-Object { $_.id -eq $deliveryId } | Select-Object -First 1
        if ($null -ne $sent -and $sent.status -eq "sent") { break }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $sent -or $sent.status -ne "sent") {
        throw "email delivery did not recover after Mailpit returned"
    }

    $count = & $docker compose exec -T postgres psql -U hireflow -d hireflow -Atc `
        "SELECT count(*) FROM notification_deliveries WHERE id = '$deliveryId'"
    if ($LASTEXITCODE -ne 0 -or [int]("$count".Trim()) -ne 1) {
        throw "provider retry created a duplicate delivery row"
    }

    [PSCustomObject]@{
        application_id = $application.id
        delivery_id = $deliveryId
        failed_attempts = $sent.attempts
        final_status = $sent.status
        duplicate_rows = 0
        result = "SMTP outage persisted, retried, and recovered"
    } | Format-List
}
finally {
    & $docker compose start mailpit email-dispatcher | Out-Null
    & $docker compose up -d --wait --wait-timeout 90 | Out-Null
}
