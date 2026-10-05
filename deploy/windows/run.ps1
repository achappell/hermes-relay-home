$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
$diagnosticsDirectory = Join-Path $root 'diagnostics'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Hermes Home virtual environment is missing: $python"
}

# Set this per process to the dedicated SYSTEM-writable, Administrator-readable
# diagnostics directory without changing machine-wide settings.

$env:HERMES_HOME_DIAGNOSTICS_DIR = $diagnosticsDirectory
$env:PYTHONUNBUFFERED = '1'
$process = Start-Process -FilePath $python -ArgumentList '-m hermes_home.runtime' -NoNewWindow -Wait -PassThru
exit $process.ExitCode
