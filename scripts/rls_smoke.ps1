$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$baseUrl = "http://localhost:8000"
$runId = Get-Date -Format "yyyyMMddHHmmss"
$password = "HireFlowRlsPassword!2026"

function Invoke-HireFlowJson {
    param([string] $Path, [object] $Body, [hashtable] $Headers = @{})
    Invoke-RestMethod -Method Post -Uri "$baseUrl$Path" -ContentType "application/json" `
        -Headers $Headers -Body ($Body | ConvertTo-Json -Depth 5)
}

function New-TenantCandidate {
    param([string] $Suffix)
    $bootstrap = Invoke-HireFlowJson "/v1/auth/bootstrap" @{
        tenant_name = "RLS Smoke $Suffix"
        owner = @{ email = "owner.$Suffix@hireflow.demo"; display_name = "RLS Owner"; role = "owner"; password = $password }
    }
    $candidate = Invoke-HireFlowJson "/v1/candidates" @{
        email = "candidate.$Suffix@hireflow.demo"; full_name = "RLS Candidate $Suffix"
    } @{ Authorization = "Bearer $($bootstrap.access_token)" }
    return $candidate
}

$candidateA = New-TenantCandidate "$runId-a"
$candidateB = New-TenantCandidate "$runId-b"
$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidateDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidateDocker)) { throw "Docker CLI was not found. Start Docker Desktop first." }
    $docker = $candidateDocker
}

function Invoke-RlsSql {
    param([string] $Sql)
    $scopedSql = "SET ROLE hireflow_app; $Sql"
    $result = & $docker compose exec -T postgres psql -U hireflow -d hireflow -At -c $scopedSql
    if ($LASTEXITCODE -ne 0) { throw "RLS SQL failed: $Sql" }
    return @($result | Where-Object { $_ -ne "" })
}

$withoutContext = Invoke-RlsSql "SELECT count(*) FROM candidates"
if ($withoutContext[-1] -ne "0") { throw "API role read rows without a tenant context" }

foreach ($table in @("application_audits", "interview_rounds", "interview_scorecards", "offers", "offer_audits")) {
    $newTableWithoutContext = Invoke-RlsSql "SELECT count(*) FROM $table"
    if ($newTableWithoutContext[-1] -ne "0") {
        throw "API role read $table rows without a tenant context"
    }
}

$withinTenant = Invoke-RlsSql "SELECT set_config('app.tenant_id', '$($candidateA.tenant_id)', false); SELECT count(*) FROM candidates WHERE id = '$($candidateA.id)'"
if ($withinTenant[-1] -ne "1") { throw "API role could not read its own tenant row" }

$crossTenant = Invoke-RlsSql "SELECT set_config('app.tenant_id', '$($candidateA.tenant_id)', false); SELECT count(*) FROM candidates WHERE id = '$($candidateB.id)'"
if ($crossTenant[-1] -ne "0") { throw "RLS allowed tenant A to read tenant B" }

$crossTenantCursor = Invoke-RlsSql "SELECT set_config('app.tenant_id', '$($candidateA.tenant_id)', false); SELECT count(*) FROM candidates WHERE tenant_id = '$($candidateB.tenant_id)' AND (created_at, id) <= ('$($candidateB.created_at)'::timestamptz, '$($candidateB.id)'::uuid)"
if ($crossTenantCursor[-1] -ne "0") { throw "RLS allowed a tenant B cursor boundary to expose tenant B rows" }

[PSCustomObject]@{
    no_context_read = "0 rows verified"
    same_tenant_read = "1 row verified"
    cross_tenant_read = "0 rows verified"
    cross_tenant_cursor = "0 rows verified"
    recruiting_pipeline_tables = "no-context RLS verified"
    result = "PostgreSQL RLS enforced for the API database role"
} | Format-List
