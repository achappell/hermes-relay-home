$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
$logDirectory = Join-Path $root 'logs'
$logPath = Join-Path $logDirectory 'hermes-home.log'

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Hermes Home virtual environment is missing: $python"
}

& $python -m hermes_home.runtime >> $logPath 2>&1
exit $LASTEXITCODE
