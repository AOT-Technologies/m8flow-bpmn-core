param(
    [string]$DistDirectory = (Join-Path $PSScriptRoot "..\..\dist"),
    [string]$VendorDirectory = (Join-Path $PSScriptRoot "..\vendor"),
    [string]$PyprojectPath = (Join-Path $PSScriptRoot "..\pyproject.toml")
)

$ErrorActionPreference = "Stop"

function Resolve-PythonExecutable {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return $pythonCommand.Source
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $pyCommand) {
        return @($pyCommand.Source, "-3")
    }

    throw "Could not find 'python' or 'py'. Install Python before staging the sample-app wheel."
}

function Resolve-UvExecutable {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uvCommand) {
        throw "Could not find 'uv'. Install uv before staging the sample-app wheel."
    }
    return $uvCommand.Source
}

$resolvedDist = Resolve-Path -Path $DistDirectory -ErrorAction Stop
$wheel = Get-ChildItem -Path $resolvedDist -Filter "m8flow_bpmn_core-*.whl" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $wheel) {
    throw "No m8flow_bpmn_core wheel was found in '$resolvedDist'. Run 'uv build' from the repo root first."
}

if (-not (Test-Path -LiteralPath $VendorDirectory)) {
    New-Item -ItemType Directory -Path $VendorDirectory | Out-Null
}

$destination = Join-Path $VendorDirectory $wheel.Name

Get-ChildItem -Path $VendorDirectory -Filter "m8flow_bpmn_core-*.whl" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

$legacyWheelPath = Join-Path $VendorDirectory "m8flow_bpmn_core.whl"
if (Test-Path -LiteralPath $legacyWheelPath) {
    Remove-Item -LiteralPath $legacyWheelPath -Force
}

Copy-Item -LiteralPath $wheel.FullName -Destination $destination -Force

$pythonExecutable = Resolve-PythonExecutable
$uvExecutable = Resolve-UvExecutable
$uvLockPath = Join-Path $PSScriptRoot "..\uv.lock"
$metadataScript = Join-Path $PSScriptRoot "update_local_wheel_metadata.py"
if ($pythonExecutable -is [array]) {
    & $pythonExecutable[0] $pythonExecutable[1] $metadataScript `
        --pyproject-path $PyprojectPath `
        --uv-lock-path $uvLockPath `
        --wheel-path $destination `
        --uv-executable $uvExecutable
}
else {
    & $pythonExecutable $metadataScript `
        --pyproject-path $PyprojectPath `
        --uv-lock-path $uvLockPath `
        --wheel-path $destination `
        --uv-executable $uvExecutable
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to refresh sample_app/pyproject.toml and sample_app/uv.lock for the staged wheel."
}

Write-Host "Staged wheel:" $wheel.FullName
Write-Host "Destination :" $destination
Write-Host "Updated source:" "vendor/$($wheel.Name)"
Write-Host "Refreshed lock: sample_app/uv.lock"
