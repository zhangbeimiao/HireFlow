param(
    [string]$ProjectName = "hireflow-tls-acceptance"
)

$ErrorActionPreference = "Stop"

if ($ProjectName -notmatch '^hireflow-tls-acceptance(?:-[a-z0-9-]+)?$') {
    throw "Refusing unsafe Compose project name: $ProjectName"
}

$docker = Get-Command docker -ErrorAction Stop
$compose = @("compose", "--project-name", $ProjectName, "-f", "docker-compose.tls-acceptance.yml")

try {
    & $docker @compose up --force-recreate --abort-on-container-exit --exit-code-from rabbitmq-verify rabbitmq-verify
    if ($LASTEXITCODE -ne 0) {
        throw "RabbitMQ TLS verification failed with exit code $LASTEXITCODE"
    }

    & $docker @compose up --force-recreate --abort-on-container-exit --exit-code-from postgres-verify postgres-verify
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL TLS verification failed with exit code $LASTEXITCODE"
    }

    [pscustomobject]@{
        postgresql  = "verify-full query succeeded"
        rabbitmq    = "mutual TLS handshake succeeded"
        certificates = "ephemeral CA, server and client certificates"
        result      = "TLS integration verified"
    }
}
finally {
    # This project name is validated above and owns only ephemeral acceptance data.
    & $docker @compose down --volumes --remove-orphans | Out-Null
}

