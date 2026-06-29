[CmdletBinding()]
param(
    [string]$BrokerUrl,
    [string]$ResultBackend,
    [string]$DatabaseUrl,
    [string]$QueueName,
    [string]$TenantId,
    [double]$PollSeconds = 1,
    [string]$LogLevel = "info",
    [switch]$SkipBeat
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$celeryExe = Join-Path $repoRoot ".venv\Scripts\celery.exe"
if (-not (Test-Path $celeryExe)) {
    throw "Could not find celery.exe in the project virtual environment at $celeryExe"
}

if ($BrokerUrl) {
    $env:M8FLOW_BPMN_CORE_CELERY_BROKER_URL = $BrokerUrl
}
elseif (-not $env:M8FLOW_BPMN_CORE_CELERY_BROKER_URL -and -not $env:M8FLOW_BACKEND_CELERY_BROKER_URL) {
    $env:M8FLOW_BPMN_CORE_CELERY_BROKER_URL = "redis://localhost:6848/0"
}

if ($ResultBackend) {
    $env:M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND = $ResultBackend
}
elseif (-not $env:M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND -and -not $env:M8FLOW_BACKEND_CELERY_RESULT_BACKEND) {
    if ($env:M8FLOW_BPMN_CORE_CELERY_BROKER_URL) {
        $env:M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND = $env:M8FLOW_BPMN_CORE_CELERY_BROKER_URL
    }
}

if ($DatabaseUrl) {
    $env:M8FLOW_BPMN_CORE_CELERY_DATABASE_URL = $DatabaseUrl
}
elseif (-not $env:M8FLOW_BPMN_CORE_CELERY_DATABASE_URL -and -not $env:M8FLOW_EXAMPLE_DATABASE_URL -and -not $env:M8FLOW_DATABASE_URL) {
    $env:M8FLOW_BPMN_CORE_CELERY_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:6843/postgres?connect_timeout=1"
}

if ($QueueName) {
    $env:M8FLOW_BPMN_CORE_CELERY_QUEUE = $QueueName
}
elseif (-not $env:M8FLOW_BPMN_CORE_CELERY_QUEUE) {
    $env:M8FLOW_BPMN_CORE_CELERY_QUEUE = "m8flow-bpmn-core-poc"
}

if ($TenantId) {
    $env:M8FLOW_BPMN_CORE_CELERY_TENANT_ID = $TenantId
}

$env:M8FLOW_BPMN_CORE_CELERY_POLL_SECONDS = $PollSeconds.ToString([System.Globalization.CultureInfo]::InvariantCulture)

function Get-MaskedDatabaseUrl {
    param(
        [string]$DatabaseUrl
    )

    return ($DatabaseUrl -replace ':[^:@/]+@', ':***@')
}

$beatProcess = $null
$beatStdOutPath = $null
$beatStdErrPath = $null

Write-Host "Status: starting the Celery scheduler worker helper..."
Write-Host "Status: this script only runs the scheduler poller. Use celery_timer_poc.ps1 for the full workflow POC."
Write-Host "Status: broker URL $($env:M8FLOW_BPMN_CORE_CELERY_BROKER_URL)"
Write-Host "Status: result backend $($env:M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND)"
if ($env:M8FLOW_BPMN_CORE_CELERY_DATABASE_URL) {
    Write-Host "Status: database URL $(Get-MaskedDatabaseUrl -DatabaseUrl $env:M8FLOW_BPMN_CORE_CELERY_DATABASE_URL)"
}
else {
    Write-Host "Status: database URL inherited from M8FLOW_EXAMPLE_DATABASE_URL or M8FLOW_DATABASE_URL"
}
Write-Host "Status: queue $($env:M8FLOW_BPMN_CORE_CELERY_QUEUE)"
if ($env:M8FLOW_BPMN_CORE_CELERY_TENANT_ID) {
    Write-Host "Status: tenant filter $($env:M8FLOW_BPMN_CORE_CELERY_TENANT_ID)"
}
Write-Host "Status: poll interval $($env:M8FLOW_BPMN_CORE_CELERY_POLL_SECONDS)s"

try {
    if (-not $SkipBeat) {
        $beatStdOutPath = Join-Path ([System.IO.Path]::GetTempPath()) (
            "m8flow-bpmn-core-celery-beat-$([Guid]::NewGuid().ToString('N')).out.log"
        )
        $beatStdErrPath = Join-Path ([System.IO.Path]::GetTempPath()) (
            "m8flow-bpmn-core-celery-beat-$([Guid]::NewGuid().ToString('N')).err.log"
        )
        $beatSchedulePath = Join-Path ([System.IO.Path]::GetTempPath()) (
            "m8flow-bpmn-core-celery-beat-$([Guid]::NewGuid().ToString('N')).schedule"
        )
        $beatArgs = @(
            "-A",
            "examples.celery_scheduler_poc:celery_app",
            "beat",
            "--loglevel",
            $LogLevel,
            "--schedule",
            $beatSchedulePath
        )

        Write-Host "Status: starting a hidden Celery beat helper for Windows compatibility..."
        $beatProcess = Start-Process `
            -FilePath $celeryExe `
            -ArgumentList $beatArgs `
            -WindowStyle Hidden `
            -RedirectStandardOutput $beatStdOutPath `
            -RedirectStandardError $beatStdErrPath `
            -PassThru
        Start-Sleep -Seconds 2

        if ($beatProcess.HasExited) {
            $beatErrorOutput = ""
            if (Test-Path $beatStdErrPath) {
                $beatErrorOutput = (Get-Content -LiteralPath $beatStdErrPath -Raw)
            }
            $beatStdOut = ""
            if (Test-Path $beatStdOutPath) {
                $beatStdOut = (Get-Content -LiteralPath $beatStdOutPath -Raw)
            }

            $beatFailureMessage = "The Celery beat helper exited immediately with code $($beatProcess.ExitCode). Stdout:`n$beatStdOut`nStderr:`n$beatErrorOutput"
            throw $beatFailureMessage
        }

        Write-Host "Status: Celery beat helper PID $($beatProcess.Id) is running."
        Write-Host "Status: Celery beat stdout log $beatStdOutPath"
        Write-Host "Status: Celery beat stderr log $beatStdErrPath"
    }

    & $celeryExe -A examples.celery_scheduler_poc:celery_app worker --pool solo --loglevel $LogLevel -Q $env:M8FLOW_BPMN_CORE_CELERY_QUEUE
    $workerExitCode = $LASTEXITCODE
    if ($workerExitCode -ne 0) {
        exit $workerExitCode
    }
}
finally {
    if ($beatProcess -and -not $beatProcess.HasExited) {
        Write-Host "Status: stopping Celery beat helper PID $($beatProcess.Id)..."
        Stop-Process -Id $beatProcess.Id -Force
    }
}
