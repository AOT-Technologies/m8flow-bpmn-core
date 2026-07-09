[CmdletBinding()]
param(
    [string]$DatabaseUrl,
    [switch]$UseDocker,
    [string]$PostgresImage = $env:M8FLOW_EXAMPLE_POSTGRES_IMAGE,
    [switch]$KeepContainer,
    [switch]$UseExistingWorker,
    [string]$BrokerUrl,
    [string]$ResultBackend,
    [string]$QueueName,
    [string]$TenantId,
    [double]$PollSeconds = 1,
    [string]$LogLevel = "info"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Main {
    param(
        [string]$DatabaseUrl,
        [switch]$UseDocker,
        [string]$PostgresImage,
        [switch]$KeepContainer,
        [switch]$UseExistingWorker,
        [string]$BrokerUrl,
        [string]$ResultBackend,
        [string]$QueueName,
        [string]$TenantId,
        [double]$PollSeconds,
        [string]$LogLevel
    )

    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
    Set-Location $repoRoot

    $exampleScript = Join-Path $PSScriptRoot "celery_timer_poc.py"
    $workerScript = Join-Path $PSScriptRoot "celery_scheduler_worker.ps1"
    $pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $defaultDatabaseUrl = "postgresql+psycopg://postgres:postgres@localhost:6843/postgres?connect_timeout=1"

    if (-not (Test-Path $pythonExe)) {
        throw "Could not find the project virtual environment at $pythonExe"
    }
    if (-not (Test-Path $workerScript)) {
        throw "Could not find the Celery scheduler worker helper at $workerScript"
    }

    if (-not $PostgresImage) {
        $PostgresImage = "postgres:16"
    }

    $previousExampleDatabaseUrl = $env:M8FLOW_EXAMPLE_DATABASE_URL
    $startedContainerName = $null
    $workerHostProcess = $null
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
        Write-Host "Status: launching the Celery timer POC..."
        Write-Host "Status: using database URL $($exampleDatabaseUrl -replace ':[^:@/]+@', ':***@')"

        if ($UseExistingWorker) {
            Write-Host "Status: using an already-running Celery scheduler worker."
        }
        else {
            $workerLaunch = Start-CelerySchedulerWorker `
                -WorkerScriptPath $workerScript `
                -DatabaseUrl $exampleDatabaseUrl `
                -BrokerUrl $BrokerUrl `
                -ResultBackend $ResultBackend `
                -QueueName $QueueName `
                -TenantId $TenantId `
                -PollSeconds $PollSeconds `
                -LogLevel $LogLevel
            $workerHostProcess = $workerLaunch.Process
            Write-Host "Status: started the temporary Celery scheduler worker helper."
            Write-Host "Status: worker helper PID $($workerHostProcess.Id)"
            Write-Host "Status: worker stdout log $($workerLaunch.StdOutPath)"
            Write-Host "Status: worker stderr log $($workerLaunch.StdErrPath)"
        }

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

        if ($workerHostProcess) {
            try {
                $workerHostProcess.Refresh()
                if (-not $workerHostProcess.HasExited) {
                    Write-Host "Status: stopping the temporary Celery scheduler worker tree rooted at PID $($workerHostProcess.Id)..."
                    & taskkill /PID $workerHostProcess.Id /T /F | Out-Null
                }
            }
            catch {
                Write-Host "Warning: failed to stop the temporary Celery scheduler worker cleanly. $_"
            }
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

    Write-Host "Status: checking whether the shared local Postgres database is reachable..."
    if (Test-DatabaseUrlReachable -DatabaseUrl $DefaultDatabaseUrl -PythonExe $PythonExe) {
        Write-Host "Status: found a reachable shared local Postgres database."
        return [pscustomobject]@{
            DatabaseUrl   = $DefaultDatabaseUrl
            ContainerName = $null
        }
    }

    Write-Host "Status: shared local Postgres database is not reachable, starting Docker fallback..."
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

    $checkScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        "m8flow-db-check-$([Guid]::NewGuid().ToString('N')).py"
    )
$checkScript = @'
import sys

from sqlalchemy import create_engine, text


url = sys.argv[1]
engine = create_engine(url)
try:
    with engine.connect() as connection:
        connection.execute(text("select 1"))
except Exception:
    sys.exit(1)
finally:
    engine.dispose()
'@

    try {
        Set-Content -LiteralPath $checkScriptPath -Value $checkScript -Encoding UTF8
        & $PythonExe $checkScriptPath $DatabaseUrl | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        Remove-Item -LiteralPath $checkScriptPath -ErrorAction SilentlyContinue
    }
}

function Start-CelerySchedulerWorker {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkerScriptPath,
        [Parameter(Mandatory = $true)]
        [string]$DatabaseUrl,
        [string]$BrokerUrl,
        [string]$ResultBackend,
        [string]$QueueName,
        [string]$TenantId,
        [double]$PollSeconds,
        [string]$LogLevel
    )

    $powershellExe = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path $powershellExe)) {
        throw "Could not find powershell.exe at $powershellExe"
    }

    $stdOutPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        "m8flow-bpmn-core-celery-worker-$([Guid]::NewGuid().ToString('N')).out.log"
    )
    $stdErrPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        "m8flow-bpmn-core-celery-worker-$([Guid]::NewGuid().ToString('N')).err.log"
    )

    $argumentList = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $WorkerScriptPath,
        "-DatabaseUrl",
        $DatabaseUrl,
        "-PollSeconds",
        $PollSeconds.ToString([System.Globalization.CultureInfo]::InvariantCulture),
        "-LogLevel",
        $LogLevel
    )

    if ($BrokerUrl) {
        $argumentList += @("-BrokerUrl", $BrokerUrl)
    }
    if ($ResultBackend) {
        $argumentList += @("-ResultBackend", $ResultBackend)
    }
    if ($QueueName) {
        $argumentList += @("-QueueName", $QueueName)
    }
    if ($TenantId) {
        $argumentList += @("-TenantId", $TenantId)
    }

    $process = Start-Process `
        -FilePath $powershellExe `
        -ArgumentList $argumentList `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdOutPath `
        -RedirectStandardError $stdErrPath `
        -PassThru

    Start-Sleep -Seconds 5
    $process.Refresh()
    if ($process.HasExited) {
        $stdout = ""
        $stderr = ""
        if (Test-Path $stdOutPath) {
            $stdout = Get-Content -LiteralPath $stdOutPath -Raw
        }
        if (Test-Path $stdErrPath) {
            $stderr = Get-Content -LiteralPath $stdErrPath -Raw
        }
        $failureMessage = "The temporary Celery scheduler worker exited immediately with code $($process.ExitCode). Stdout:`n$stdout`nStderr:`n$stderr"
        throw $failureMessage
    }

    return [pscustomobject]@{
        Process    = $process
        StdOutPath = $stdOutPath
        StdErrPath = $stdErrPath
    }
}

Main @PSBoundParameters
