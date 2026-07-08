param(
    [switch]$UseActiveEnvironment,
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 5010
)

$ErrorActionPreference = "Stop"

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

Invoke-Step -Description "Building the library wheel" -Action {
    Push-Location $repoRoot
    try {
        try {
            uv build --wheel
        }
        catch {
            Write-Warning (
                "uv build --wheel failed. Falling back to " +
                "'python -m build --wheel --no-isolation'."
            )
            python -m pip install build hatchling
            python -m build --wheel --no-isolation
        }
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
        & uv @syncArgs
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
        & uv @runArgs "m8flow-sample-app"
    }
    finally {
        Pop-Location
    }
}
