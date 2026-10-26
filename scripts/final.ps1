param(
    [switch]$Start,
    [switch]$Rehearsal,
    [ValidateSet('strategic', 'strategic-policy')]
    [string]$Agent = 'strategic',
    [ValidateRange(1, 10)]
    [int]$Parallel = 10
)

$ErrorActionPreference = 'Stop'
$finalProject = Split-Path -Parent $PSScriptRoot
$finalPython = Join-Path $finalProject '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $finalPython -PathType Leaf)) {
    throw 'Project Python is missing. Install Python 3.12/uv and run uv sync --frozen --dev first.'
}
if ($Rehearsal -and -not $PSBoundParameters.ContainsKey('Agent')) {
    $Agent = 'strategic-policy'
}
$finalArgs = @('-m', 'aeiou', 'final', '--agent', $Agent, '--parallel', "$Parallel")
if ($Rehearsal) {
    $finalArgs += @('--rehearsal', '--waves', '2')
} else {
    $finalArgs += @('--waves', '5')
}
if ($Start) {
    $finalArgs += '--start'
    Write-Host 'Explicit START: autonomous wave execution. Stop all practice loops first.'
} else {
    $finalArgs += '--check'
    Write-Host 'Offline inspection only. No API calls and no sessions. Use -Start only when ready.'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
Push-Location -LiteralPath $finalProject
try {
    & $finalPython @finalArgs
    if ($LASTEXITCODE -ne 0) { throw "Final command exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
