# Rollback for qwen3 content-free logging change (2026-10-06).
# Restores the original server + start script, removes the logging module, restarts ONLY the streaming task/process.
$R="D:\Qwen3-TTS"; $B="D:\Qwen3-TTS\backups\qwen3-logging-20261006"
Copy-Item "$B\qwen3_streaming_server.py" "$R\qwen3_streaming_server.py" -Force
Copy-Item "$B\start_qwen3_streaming_optimized.ps1" "$R\start_qwen3_streaming_optimized.ps1" -Force
Remove-Item "$R\qwen3_server_logging.py" -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName "Qwen3-TTS-Streaming-Optimized"; Start-Sleep 3
Get-CimInstance Win32_Process | ? { $_.CommandLine -match "qwen3_streaming_server\.py" } | % { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 4; Start-ScheduledTask -TaskName "Qwen3-TTS-Streaming-Optimized"
