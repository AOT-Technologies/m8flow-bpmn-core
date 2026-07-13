param(
    [switch]$UseActiveEnvironment,
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 5010
)

$ErrorActionPreference = "Stop"

function Resolve-CommandExecutable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandName
    )

    $command = Get-Command $CommandName -ErrorAction Stop
    return $command.Source
}

function Invoke-CapturedNativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-Location).Path
    )

    $stdoutPath = Join-Path $env:TEMP (
        "m8flow-bpmn-core-sample-app-" +
        [Guid]::NewGuid().ToString("N") +
        ".stdout.log"
    )
    $stderrPath = Join-Path $env:TEMP (
        "m8flow-bpmn-core-sample-app-" +
        [Guid]::NewGuid().ToString("N") +
        ".stderr.log"
    )

    try {
        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        $stdoutText = if (Test-Path -LiteralPath $stdoutPath) {
            Get-Content -LiteralPath $stdoutPath -Raw -Encoding utf8
        }
        else {
            ""
        }
        $stderrText = if (Test-Path -LiteralPath $stderrPath) {
            Get-Content -LiteralPath $stderrPath -Raw -Encoding utf8
        }
        else {
            ""
        }

        $outputParts = @()
        foreach ($text in @($stdoutText, $stderrText)) {
            if ([string]::IsNullOrWhiteSpace($text)) {
                continue
            }
            $outputParts += $text.TrimEnd()
        }

        return [pscustomobject]@{
            ExitCode   = $process.ExitCode
            Succeeded  = ($process.ExitCode -eq 0)
            OutputText = ($outputParts -join [Environment]::NewLine).Trim()
        }
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Test-IsKnownUvTempLockFailure {
    param(
        [string]$OutputText
    )

    if ([string]::IsNullOrWhiteSpace($OutputText)) {
        return $false
    }

    $hasUvTrampolinePath = $OutputText -match "uv-trampoline-[^\\\s]+\.exe"
    $hasKnownWindowsUvFailure = (
        $OutputText -match "os error 32" -or
        $OutputText -match "Failed to update Windows PE resources" -or
        $OutputText -match "The system cannot open the device or file specified"
    )

    return ($hasUvTrampolinePath -and $hasKnownWindowsUvFailure)
}

function Test-IsWindowsFileInUseFailure {
    param(
        [string]$OutputText
    )

    if ([string]::IsNullOrWhiteSpace($OutputText)) {
        return $false
    }

    return (
        $OutputText -match "failed to remove file" -and
        $OutputText -match "os error 32"
    )
}

function Write-CapturedOutput {
    param(
        [string]$OutputText
    )

    if ([string]::IsNullOrWhiteSpace($OutputText)) {
        return
    }

    Write-Host $OutputText
}

function Invoke-UvCommandWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvExecutable,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [int]$MaxAttempts = 3,
        [int]$RetryDelaySeconds = 1
    )

    $knownLockRetries = 0
    $lastResult = $null

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $result = Invoke-CapturedNativeCommand `
            -FilePath $UvExecutable `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory
        if ($result.Succeeded) {
            return [pscustomobject]@{
                Succeeded               = $true
                OutputText              = $result.OutputText
                AttemptsUsed            = $attempt
                RecoveredFromKnownLock  = ($knownLockRetries -gt 0)
            }
        }

        $lastResult = $result
        if (
            (Test-IsKnownUvTempLockFailure -OutputText $result.OutputText) -and
            $attempt -lt $MaxAttempts
        ) {
            $knownLockRetries += 1
            Start-Sleep -Seconds $RetryDelaySeconds
            continue
        }

        break
    }

    return [pscustomobject]@{
        Succeeded              = $false
        OutputText             = if ($null -ne $lastResult) { $lastResult.OutputText } else { "" }
        AttemptsUsed           = $MaxAttempts
        RecoveredFromKnownLock = $false
    }
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "==> $Description"
    & $Action
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sampleAppRoot = Resolve-Path (Join-Path $scriptRoot "..")
$repoRoot = Resolve-Path (Join-Path $scriptRoot "..\..")
$repoVenv = Join-Path $repoRoot.Path ".venv"
$uvCacheDir = Join-Path $sampleAppRoot.Path ".uv-cache"

if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = $uvCacheDir
}

if (-not $UseActiveEnvironment.IsPresent -and $env:VIRTUAL_ENV) {
    try {
        $resolvedActiveVenv = (Resolve-Path $env:VIRTUAL_ENV).Path
        if ((Test-Path -LiteralPath $repoVenv) -and $resolvedActiveVenv -eq (Resolve-Path $repoVenv).Path) {
            $UseActiveEnvironment = $true
        }
    }
    catch {
        # Ignore broken VIRTUAL_ENV values and fall back to regular uv behavior.
    }
}

$syncArgs = @("sync")
$runArgs = @("run")
if ($UseActiveEnvironment.IsPresent) {
    $syncArgs += "--active"
    $runArgs += "--active"
}

$uvExecutable = Resolve-CommandExecutable -CommandName "uv"
$pythonExecutable = Resolve-CommandExecutable -CommandName "python"
$effectiveSyncArgs = @($syncArgs)
$effectiveRunArgs = @($runArgs)

Invoke-Step -Description "Building the library wheel" -Action {
    Push-Location $repoRoot
    try {
        $uvBuildResult = Invoke-UvCommandWithRetry `
            -UvExecutable $uvExecutable `
            -ArgumentList @("build", "--wheel") `
            -WorkingDirectory $repoRoot.Path `
            -MaxAttempts 2
        if ($uvBuildResult.Succeeded) {
            if ($uvBuildResult.RecoveredFromKnownLock) {
                Write-Host "Status: recovered from a transient Windows uv-helper issue while building the wheel."
            }
            else {
                Write-Host "Status: built the library wheel with uv."
            }
            return
        }

        if (-not (Test-IsKnownUvTempLockFailure -OutputText $uvBuildResult.OutputText)) {
            Write-CapturedOutput -OutputText $uvBuildResult.OutputText
            throw "Failed to build the library wheel with uv."
        }

        $pipInstallResult = Invoke-CapturedNativeCommand `
            -FilePath $pythonExecutable `
            -ArgumentList @("-m", "pip", "install", "build", "hatchling") `
            -WorkingDirectory $repoRoot.Path
        if (-not $pipInstallResult.Succeeded) {
            Write-CapturedOutput -OutputText $uvBuildResult.OutputText
            Write-CapturedOutput -OutputText $pipInstallResult.OutputText
            throw "Failed to install build dependencies for the Python wheel fallback."
        }

        $pythonBuildResult = Invoke-CapturedNativeCommand `
            -FilePath $pythonExecutable `
            -ArgumentList @("-m", "build", "--wheel", "--no-isolation") `
            -WorkingDirectory $repoRoot.Path
        if (-not $pythonBuildResult.Succeeded) {
            Write-CapturedOutput -OutputText $uvBuildResult.OutputText
            Write-CapturedOutput -OutputText $pythonBuildResult.OutputText
            throw "Failed to build the library wheel with the Python fallback."
        }

        Write-Host (
            "Status: uv build hit a known Windows uv-helper issue; " +
            "built the wheel with the Python fallback."
        )
    }
    finally {
        Pop-Location
    }
}

Invoke-Step -Description "Staging the newest wheel into sample_app/vendor" -Action {
    Push-Location $repoRoot
    try {
        & (Join-Path $scriptRoot "stage_local_wheel.ps1")
    }
    finally {
        Pop-Location
    }
}

Invoke-Step -Description "Syncing the sample app environment" -Action {
    Push-Location $sampleAppRoot
    try {
        $uvSyncResult = Invoke-UvCommandWithRetry `
            -UvExecutable $uvExecutable `
            -ArgumentList $effectiveSyncArgs `
            -WorkingDirectory $sampleAppRoot.Path `
            -MaxAttempts 3
        if (-not $uvSyncResult.Succeeded) {
            $canFallbackFromActiveEnvironment = (
                $UseActiveEnvironment.IsPresent -and
                ($effectiveSyncArgs -contains "--active") -and
                (Test-IsWindowsFileInUseFailure -OutputText $uvSyncResult.OutputText)
            )

            if ($canFallbackFromActiveEnvironment) {
                $effectiveSyncArgs = @("sync")
                $effectiveRunArgs = @("run")
                $fallbackSyncResult = Invoke-UvCommandWithRetry `
                    -UvExecutable $uvExecutable `
                    -ArgumentList $effectiveSyncArgs `
                    -WorkingDirectory $sampleAppRoot.Path `
                    -MaxAttempts 3
                if ($fallbackSyncResult.Succeeded) {
                    Write-Host (
                        "Status: the active repo environment has locked files " +
                        "in use, so the sample app was synced into sample_app/.venv instead."
                    )
                    return
                }

                Write-CapturedOutput -OutputText $uvSyncResult.OutputText
                Write-CapturedOutput -OutputText $fallbackSyncResult.OutputText
                throw (
                    "Failed to sync the sample app environment in both the " +
                    "active repo environment and sample_app/.venv."
                )
            }

            Write-CapturedOutput -OutputText $uvSyncResult.OutputText
            throw "Failed to sync the sample app environment."
        }
        if ($uvSyncResult.RecoveredFromKnownLock) {
            Write-Host "Status: recovered from a transient Windows uv-helper issue while syncing the sample app environment."
        }
        else {
            Write-Host "Status: synced the sample app environment."
        }
    }
    finally {
        Pop-Location
    }
}

Invoke-Step -Description "Starting the sample app" -Action {
    Push-Location $sampleAppRoot
    try {
        $env:M8FLOW_SAMPLE_APP_HOST = $BindHost
        $env:M8FLOW_SAMPLE_APP_PORT = "$Port"
        Write-Host "Sample app URL: http://$BindHost`:$Port"
        & $uvExecutable @effectiveRunArgs "m8flow-sample-app"
        if ($LASTEXITCODE -ne 0) {
            throw "The sample app exited with code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}
