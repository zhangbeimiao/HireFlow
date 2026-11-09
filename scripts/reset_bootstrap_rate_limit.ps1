$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$docker = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $candidateDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path $candidateDocker)) {
        throw "Docker CLI was not found. Start Docker Desktop first."
    }
    $docker = $candidateDocker
}

# Black-box suites intentionally share one Docker gateway IP. Reset only the
# ephemeral bootstrap counters between independent suites; business cache keys
# and production rate-limit settings remain untouched.
$keys = @(
    & $docker compose exec -T redis redis-cli --scan `
        --pattern "hireflow:rate-limit:POST:/v1/auth/bootstrap:*"
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not enumerate bootstrap rate-limit fixture keys"
}

$removed = 0
foreach ($key in $keys) {
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        & $docker compose exec -T redis redis-cli UNLINK $key | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not remove bootstrap rate-limit fixture key"
        }
        $removed += 1
    }
}

[PSCustomObject]@{
    removed = $removed
    scope = "bootstrap test fixtures only"
} | Format-List
