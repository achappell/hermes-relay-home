$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
$logDirectory = Join-Path $root 'logs'
$logPath = Join-Path $logDirectory 'hermes-home.log'

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Hermes Home virtual environment is missing: $python"
}

# Windows PowerShell 5.1 turns a native process's first stderr line into a
# terminating NativeCommandError under ErrorActionPreference Stop, which ended
# this runner (and Home) on the first logged warning. Let cmd.exe own the
# redirection so stdout and stderr append to the log as bytes, and keep Python
# unbuffered so the log is current when the process exits.
$env:PYTHONUNBUFFERED = '1'
$command = '"{0}" -m hermes_home.runtime >> "{1}" 2>&1' -f $python, $logPath
$process = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"$command`"" -NoNewWindow -Wait -PassThru
exit $process.ExitCode
