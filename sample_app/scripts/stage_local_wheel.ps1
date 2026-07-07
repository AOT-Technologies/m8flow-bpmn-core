param(
    [string]$DistDirectory = (Join-Path $PSScriptRoot "..\..\dist"),
    [string]$VendorDirectory = (Join-Path $PSScriptRoot "..\vendor"),
    [string]$PyprojectPath = (Join-Path $PSScriptRoot "..\pyproject.toml")
)

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

$pyproject = Get-Content -Path $PyprojectPath -Raw -ErrorAction Stop
$relativeWheelPath = "vendor/$($wheel.Name)"
$pattern = 'm8flow-bpmn-core = \{ path = "vendor/[^"]+" \}'
if ($pyproject -notmatch $pattern) {
    throw "Could not update '$PyprojectPath' with the staged wheel path."
}

$updatedPyproject = $pyproject -replace $pattern, "m8flow-bpmn-core = { path = `"$relativeWheelPath`" }"

Set-Content -Path $PyprojectPath -Value $updatedPyproject -NoNewline

Write-Host "Staged wheel:" $wheel.FullName
Write-Host "Destination :" $destination
Write-Host "Updated source:" $relativeWheelPath
