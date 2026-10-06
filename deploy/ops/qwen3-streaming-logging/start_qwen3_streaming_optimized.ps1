[CmdletBinding()]
param(
    [int]$Port = 8767,
    [string]$HostAddress = "0.0.0.0",
    [string]$Model = "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    [string]$Voice = "capaldi-calm",
    [string]$ForcedAlignerModel = ""
)

$ErrorActionPreference = "Stop"
# Listen on all interfaces; Windows Firewall scopes reachability to trusted networks.
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Server = Join-Path $Root "qwen3_streaming_server.py"
$VoicesDir = $Root
$TritonRoot = Join-Path $Root "triton-test-site"
$ForkRoot = Join-Path $Root "streaming-fork"
$TritonCompiler = Join-Path $TritonRoot "triton\runtime\tcc\tcc.exe"
$StdoutLogPath = Join-Path $Root "qwen3-streaming-optimized.stdout.log"
$StderrLogPath = Join-Path $Root "qwen3-streaming-optimized.stderr.log"

$RequiredPaths = @(
    $Python,
    $Server,
    $TritonCompiler,
    (Join-Path $VoicesDir "capaldi-calm-reference.wav"),
    (Join-Path $VoicesDir "capaldi-calm-reference.txt"),
    (Join-Path $VoicesDir "skippy-reference.wav"),
    (Join-Path $VoicesDir "skippy-reference.txt")
)
foreach ($RequiredPath in $RequiredPaths) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required file is missing: $RequiredPath"
    }
}

# Keep the experimental Triton path isolated from the production Qwen venv.
$env:PYTHONPATH = $TritonRoot
$env:QWEN_TTS_FORK_PATH = $ForkRoot
$env:CC = $TritonCompiler
$env:TORCHINDUCTOR_CACHE_DIR = Join-Path $Root "torchinductor-streaming-cache"

$Arguments = @(
    $Server,
    "--model", $Model,
    "--voices-dir", $VoicesDir,
    "--voice-name", $Voice,
    "--host", $HostAddress,
    "--port", $Port,
    "--device", "cuda:0",
    "--dtype", "bfloat16",
    "--threads", "8",
    "--streaming-optimizations",
    "--decode-window-frames", "80",
    "--compile-mode", "reduce-overhead",
    "--warmup-text", "Warm-up."
)

if ($ForcedAlignerModel) {
    $Arguments += @("--forced-aligner-model", $ForcedAlignerModel)
}

# Start-Process truncates the redirect targets, which used to discard the previous
# run's stderr/stdout on every restart. Keep the last 5 of each. The server also
# writes a rotating content-free log to logs\qwen3-streaming.log (qwen3_server_logging.py).
try {
    $ArchiveDir = Join-Path $Root "logs\archive"
    New-Item -ItemType Directory -Force -Path $ArchiveDir | Out-Null
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    foreach ($LogPath in @($StdoutLogPath, $StderrLogPath)) {
        if ((Test-Path -LiteralPath $LogPath) -and ((Get-Item -LiteralPath $LogPath).Length -gt 0)) {
            $Leaf = [IO.Path]::GetFileNameWithoutExtension($LogPath)
            Move-Item -LiteralPath $LogPath -Destination (Join-Path $ArchiveDir "$Leaf.$Stamp.log")
            Get-ChildItem -LiteralPath $ArchiveDir -Filter "$Leaf.*.log" |
                Sort-Object LastWriteTime -Descending | Select-Object -Skip 5 | Remove-Item -Force
        }
    }
} catch {
    Write-Warning "log archive step failed: $($_.Exception.Message)"
}

Set-Location -LiteralPath $Root
$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $StdoutLogPath `
    -RedirectStandardError $StderrLogPath `
    -PassThru `
    -Wait `
    -WindowStyle Hidden
exit $Process.ExitCode
