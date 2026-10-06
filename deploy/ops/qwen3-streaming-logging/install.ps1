<#
.SYNOPSIS
Installs the Qwen3 streaming server's content-free rotating logging on CaticornQueen.

.DESCRIPTION
Copies qwen3_server_logging.py, the patched qwen3_streaming_server.py and the patched
start_qwen3_streaming_optimized.ps1 from this directory into -TargetDir. It is
idempotent and does not touch the running server unless -Restart is given.

Before writing anything it:
  * checks the shipped files against their recorded SHA-256 (a checkout that
    converted line endings is rejected);
  * checks the target's server and start script are either the known originals or
    already the shipped versions (anything else is local drift and aborts unless
    -Force);
  * copies the current target files to <TargetDir>\backups\qwen3-logging-<stamp>\
    and writes a rollback.ps1 there.

-Restart restarts ONLY the Qwen3-TTS-Streaming-Optimized scheduled task and its
python process, and refuses while the newest log health line reports busy=1
(override with -Force). Restart only when no one is using speech.

.PARAMETER TargetDir
Qwen3 server directory. Default D:\Qwen3-TTS.

.PARAMETER Restart
Restart the streaming task after installing so the new code loads.

.PARAMETER Force
Install over unrecognised local changes, or restart while the log reports busy.
#>
[CmdletBinding()]
param(
    [string]$TargetDir = "D:\Qwen3-TTS",
    [switch]$Restart,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$TaskName = "Qwen3-TTS-Streaming-Optimized"
$Source = $PSScriptRoot

# SHA-256 of every file this installer knows about. "Original" = before the logging
# change (2026-10-06); "New" = the shipped files.
$Files = @(
    @{ Name = "qwen3_streaming_server.py";
       Original = "30676cf861af34700024cc815df3cd885f720882332f098ff7315dc33a2008f6";
       New = "a57265073311bce48f9863f3736b451934aab3f5cb4026e65d656338a66cb821"; Backup = $true },
    @{ Name = "start_qwen3_streaming_optimized.ps1";
       Original = "e4fe09a3a6cfc5dbdcf3272cf6a098f91fb8c1fa59511e3af742e8df905657aa";
       New = "74042877aed2f65c0309691640439cb86902ae1a535f99e21cc2e0f53519bbc3"; Backup = $true },
    @{ Name = "qwen3_server_logging.py"; Original = $null;
       New = "f7b5aea7b955ff9a59f92ebf2b7f7d9a6fcab8b72c859bb1c0ef318f64b9dae9"; Backup = $false }
)

function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath $TargetDir -PathType Container)) {
    throw "Target directory not found: $TargetDir"
}

# 1. Shipped files must be byte-exact.
foreach ($File in $Files) {
    $Path = Join-Path $Source $File.Name
    if (-not (Test-Path -LiteralPath $Path)) { throw "Missing shipped file: $Path" }
    $Actual = Get-Sha256 $Path
    if ($Actual -ne $File.New) {
        throw "$($File.Name) differs from the recorded SHA-256 ($Actual). Re-fetch it without line-ending conversion (git -c core.autocrlf=false)."
    }
}

# 2. Classify what is on the target.
$ToCopy = @()
foreach ($File in $Files) {
    $Dest = Join-Path $TargetDir $File.Name
    if (-not (Test-Path -LiteralPath $Dest)) {
        if ($File.Original) { throw "Target is missing $($File.Name): $Dest" }
        $ToCopy += $File
        continue
    }
    $Current = Get-Sha256 $Dest
    if ($Current -eq $File.New) {
        Write-Host "already installed: $($File.Name)"
    } elseif ($File.Original -and $Current -ne $File.Original -and -not $Force) {
        throw "$($File.Name) is neither the known original nor the shipped version (sha256 $Current). It has local changes; review them or re-run with -Force."
    } elseif (-not $File.Original -and -not $Force) {
        throw "$($File.Name) exists with unrecognised contents (sha256 $Current). Re-run with -Force to replace it."
    } else {
        $ToCopy += $File
    }
}

$Changed = $false
if ($ToCopy.Count -eq 0) {
    Write-Host "Nothing to install: the logging change is already in $TargetDir."
} else {
    # 3. Back up, then copy.
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BackupDir = Join-Path $TargetDir "backups\qwen3-logging-$Stamp"
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $Restore = @()
    foreach ($File in $ToCopy) {
        $Dest = Join-Path $TargetDir $File.Name
        if (Test-Path -LiteralPath $Dest) {
            Copy-Item -LiteralPath $Dest -Destination (Join-Path $BackupDir $File.Name)
            if ($File.Original) { $Restore += $File.Name }
        }
    }

    $RollbackLines = @(
        "# Rollback for the Qwen3 logging install of $Stamp. Restores the backed-up files,",
        "# removes the logging module, restarts ONLY the streaming task (run when idle).",
        "`$R = `"$TargetDir`"; `$B = `"$BackupDir`""
    )
    foreach ($Name in $Restore) { $RollbackLines += "Copy-Item `"`$B\$Name`" `"`$R\$Name`" -Force" }
    $RollbackLines += "Remove-Item `"`$R\qwen3_server_logging.py`" -ErrorAction SilentlyContinue"
    $RollbackLines += "Stop-ScheduledTask -TaskName `"$TaskName`"; Start-Sleep 3"
    $RollbackLines += "Get-CimInstance Win32_Process | Where-Object { `$_.CommandLine -match `"qwen3_streaming_server\.py`" } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }"
    $RollbackLines += "Start-Sleep 4; Start-ScheduledTask -TaskName `"$TaskName`""
    Set-Content -LiteralPath (Join-Path $BackupDir "rollback.ps1") -Value $RollbackLines -Encoding ASCII

    foreach ($File in $ToCopy) {
        Copy-Item -LiteralPath (Join-Path $Source $File.Name) -Destination (Join-Path $TargetDir $File.Name) -Force
        Write-Host "installed: $($File.Name)"
    }
    $Changed = $true
    Write-Host "originals backed up in $BackupDir (rollback.ps1 alongside)"
}

# 4. Restart is opt-in and idle-guarded.
if ($Restart) {
    $LogPath = Join-Path $TargetDir "logs\qwen3-streaming.log"
    if ((Test-Path -LiteralPath $LogPath) -and -not $Force) {
        $Health = Get-Content -LiteralPath $LogPath -Tail 200 | Where-Object { $_ -match "event=health" } | Select-Object -Last 1
        if ($Health -and $Health -match "busy=1") {
            throw "The newest health line reports busy=1 (a request is generating). Retry when idle, or pass -Force."
        }
    }
    Write-Host "restarting $TaskName ..."
    Stop-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match "qwen3_streaming_server\.py" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 4
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "restarted. The model reload and warmup take a few minutes; watch $LogPath for event=server_ready."
} elseif ($Changed) {
    Write-Host "NOT restarted: the running server still has the old code. When idle, re-run with -Restart."
}
