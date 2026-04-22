[CmdletBinding()]
param(
    [string]$DatabaseUrl,
    [switch]$UseDocker,
    [string]$PostgresImage = $env:M8FLOW_EXAMPLE_POSTGRES_IMAGE,
    [switch]$KeepContainer
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Main {
    param(
        [string]$DatabaseUrl,
        [switch]$UseDocker,
        [string]$PostgresImage,
        [switch]$KeepContainer
    )

    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
    Set-Location $repoRoot

    $exampleScript = Join-Path $PSScriptRoot "conditional_approval_poc.py"
    $pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $defaultDatabaseUrl = "postgresql+psycopg://postgres:postgres@localhost:5432/m8flow_bpmn_core_example?connect_timeout=1"

    if (-not (Test-Path $pythonExe)) {
        throw "Could not find the project virtual environment at $pythonExe"
    }

    if (-not $PostgresImage) {
        $PostgresImage = "postgres:16"
    }

    $previousExampleDatabaseUrl = $env:M8FLOW_EXAMPLE_DATABASE_URL
    $startedContainerName = $null
    $exitCode = 0

    try {
        $selection = Resolve-ExampleDatabaseUrl `
            -DatabaseUrl $DatabaseUrl `
            -UseDocker:$UseDocker `
            -PostgresImage $PostgresImage `
            -DefaultDatabaseUrl $defaultDatabaseUrl `
            -PythonExe $pythonExe

        $exampleDatabaseUrl = $selection.DatabaseUrl
        $startedContainerName = $selection.ContainerName
        $env:M8FLOW_EXAMPLE_DATABASE_URL = $exampleDatabaseUrl

        Write-Host ""
        Write-Host "Status: launching the interactive conditional-approval example..."
        Write-Host "Status: using database URL $($exampleDatabaseUrl -replace ':[^:@/]+@', ':***@')"
        & $pythonExe $exampleScript
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            Write-Host "Status: the example exited with code $exitCode."
        }
    }
    finally {
        if ($null -ne $previousExampleDatabaseUrl) {
            $env:M8FLOW_EXAMPLE_DATABASE_URL = $previousExampleDatabaseUrl
        }
        else {
            Remove-Item Env:M8FLOW_EXAMPLE_DATABASE_URL -ErrorAction SilentlyContinue
        }

        if ($startedContainerName -and -not $KeepContainer) {
            Write-Host "Status: removing temporary Docker container $startedContainerName..."
            & docker rm -f $startedContainerName | Out-Null
        }
    }

    if ($exitCode -ne 0) {
        exit $exitCode
    }
}

function Resolve-ExampleDatabaseUrl {
    param(
        [string]$DatabaseUrl,
        [switch]$UseDocker,
        [string]$PostgresImage,
        [string]$DefaultDatabaseUrl,
        [string]$PythonExe
    )

    if ($DatabaseUrl) {
        Write-Host "Status: using the database URL passed to the launcher."
        return [pscustomobject]@{
            DatabaseUrl   = $DatabaseUrl
            ContainerName = $null
        }
    }

    if ($UseDocker) {
        return Start-TemporaryPostgresContainer -PostgresImage $PostgresImage
    }

    if ($env:M8FLOW_EXAMPLE_DATABASE_URL) {
        Write-Host "Status: using M8FLOW_EXAMPLE_DATABASE_URL from the environment."
        return [pscustomobject]@{
            DatabaseUrl   = $env:M8FLOW_EXAMPLE_DATABASE_URL
            ContainerName = $null
        }
    }

    Write-Host "Status: checking whether the default local Postgres database is reachable..."
    if (Test-DatabaseUrlReachable -DatabaseUrl $DefaultDatabaseUrl -PythonExe $PythonExe) {
        Write-Host "Status: default local Postgres database is reachable."
        return [pscustomobject]@{
            DatabaseUrl   = $DefaultDatabaseUrl
            ContainerName = $null
        }
    }

    Write-Host "Status: default local database is not reachable, starting Docker fallback..."
    return Start-TemporaryPostgresContainer -PostgresImage $PostgresImage
}

function Start-TemporaryPostgresContainer {
    param(
        [string]$PostgresImage
    )

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is not available, so the example cannot start a fallback Postgres container."
    }

    $containerName = "m8flow-bpmn-core-example-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
    Write-Host "Status: starting temporary Docker Postgres container $containerName..."
    & docker run -d --rm `
        --name $containerName `
        -e "POSTGRES_USER=postgres" `
        -e "POSTGRES_HOST_AUTH_METHOD=trust" `
        -e "POSTGRES_DB=m8flow_bpmn_core_example" `
        -P $PostgresImage | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start the temporary Docker Postgres container."
    }

    $mappedPort = Get-ContainerHostPort -ContainerName $containerName
    $databaseUrl = "postgresql+psycopg://postgres@127.0.0.1:$mappedPort/m8flow_bpmn_core_example"
    Write-Host "Status: temporary container is available on host port $mappedPort."
    return [pscustomobject]@{
        DatabaseUrl   = $databaseUrl
        ContainerName = $containerName
    }
}

function Get-ContainerHostPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ContainerName
    )

    $output = & docker port $ContainerName 5432/tcp
    if ($LASTEXITCODE -ne 0) {
        throw "Could not determine the mapped port for container $ContainerName."
    }

    foreach ($line in $output) {
        if ($line -match ":(\d+)$") {
            return [int]$Matches[1]
        }
    }

    throw "Could not parse the mapped port for container $ContainerName."
}

function Test-DatabaseUrlReachable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseUrl,
        [Parameter(Mandatory = $true)]
        [string]$PythonExe
    )

    $checkScript = @'
import sys

from sqlalchemy import create_engine, text


url = sys.argv[1]
engine = create_engine(url)
try:
    with engine.connect() as connection:
        connection.execute(text("select 1"))
finally:
    engine.dispose()
'@

    & $PythonExe -c $checkScript $DatabaseUrl | Out-Null
    return $LASTEXITCODE -eq 0
}

Main @PSBoundParameters
