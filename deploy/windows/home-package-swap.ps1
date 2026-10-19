<#
Package-only Hermes Home swap: cut over to the reviewed PR #109 wheel, or roll back to 3a0eec0.
The current database is kept and never restored. The installer, firewall,
Tailscale, Prometheus, binds and secrets are not touched. The only machine
setting this script changes is HERMES_HOME_DEPLOYMENT_REVISION.

Run elevated on CaticornQueen. Stage each wheel under its canonical basename in
its own directory:
  Cutover : -Wheel <dir>\stage-4c3f621\hermes_relay_home-0.2.0-py3-none-any.whl
            -ExpectedSha256 91a3a8f3ff01cfa6c3a7545e7948f5c7d2cb0ba3a4bf75e34320d694afdfca96
            -Revision 4c3f62122fbb771f5ee20bacba8566274194143e
  Rollback: -Wheel <dir>\stage-3a0eec0\hermes_relay_home-0.1.0-py3-none-any.whl
            -ExpectedSha256 <full sha of the 3a0eec0 wheel>
            -Revision 3a0eec0b9739524e2a8c54fd5a3308b3c3abb90b
  -BackupDir must not already exist; the script creates it with a private ACL.

Before stopping the task, the script runs these checks and stops if any fails:
  - wheel hash
  - canonical wheel name
  - `uv pip install --dry-run --force-reinstall --no-deps`
  - private backup directory created and its ACL verified
After the stop, a failed DB backup, failed install or RECORD mismatch leaves
the task STOPPED. A partly installed package never starts.
#>
param(
  [Parameter(Mandatory)] [string] $Wheel,
  [Parameter(Mandatory)] [string] $ExpectedSha256,
  [Parameter(Mandatory)] [string] $Revision,
  [Parameter(Mandatory)] [string] $BackupDir
)
$ErrorActionPreference = 'Stop'
$root   = 'C:\ProgramData\HermesHome'
$python = Join-Path $root 'venv\Scripts\python.exe'
$site   = Join-Path $root 'venv\Lib\site-packages'
$task   = 'Hermes Home'
$ports  = 8780, 8766

function Invoke-PythonScript([string] $Code, [string[]] $Arguments) {
  $dir = Join-Path ([IO.Path]::GetTempPath()) ('hermes-swap-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $dir | Out-Null
  try {
    $file = Join-Path $dir 'step.py'
    Set-Content -LiteralPath $file -Value $Code -Encoding UTF8
    & $python $file @Arguments | Out-Host
    return [int] $LASTEXITCODE
  } finally {
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Test-Listening([int] $Port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) }

# ---------- Preflight (no change to the running service) ----------
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Revision must be a full 40-character commit SHA' }
if ((Split-Path -Leaf $Wheel) -notmatch '^hermes_relay_home-[0-9][0-9A-Za-z.]*-py3-none-any\.whl$') { throw 'Wheel must keep its canonical basename' }
$actual = (Get-FileHash -LiteralPath $Wheel -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $ExpectedSha256.ToLower()) { throw "Wheel hash mismatch: $actual" }
$uv = (Get-Command uv -ErrorAction Stop).Source
& $uv pip install --python $python --dry-run --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { throw 'uv dry-run failed; nothing changed' }

if (Test-Path -LiteralPath $BackupDir) { throw "BackupDir already exists: $BackupDir" }
New-Item -ItemType Directory -Path $BackupDir | Out-Null
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
foreach ($sid in 'S-1-5-18', 'S-1-5-32-544') {
  $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)), 'FullControl', $inherit, 'None', 'Allow')))
}
Set-Acl -LiteralPath $BackupDir -AclObject $acl
$granted = (Get-Acl -LiteralPath $BackupDir).Access | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } | Sort-Object -Unique
if ((Compare-Object $granted @('S-1-5-18', 'S-1-5-32-544')) -or -not (Get-Acl -LiteralPath $BackupDir).AreAccessRulesProtected) { throw 'Backup directory ACL is not private (SYSTEM/Administrators only)' }

# ---------- Stop ----------
Stop-ScheduledTask -TaskName $task
$alive = $null; $listen = $null
for ($i = 0; $i -lt 60; $i++) {
  $alive = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'hermes_home\.runtime' -and $_.ExecutablePath -like "$root*" }
  $listen = Get-NetTCPConnection -State Listen -LocalPort $ports -ErrorAction SilentlyContinue
  if (-not $alive -and -not $listen) { break }
  Start-Sleep -Seconds 1
}
if ($alive -or $listen) { throw 'Home runtime or its listeners are still present; not swapping (task left stopped for operator review)' }

# ---------- Online backup of the CURRENT database (never restored here) ----------
$backupCode = @'
import sqlite3, sys
src = sqlite3.connect('file:C:/ProgramData/HermesHome/home.sqlite3?mode=ro', uri=True)
dst = sqlite3.connect(sys.argv[1])
src.backup(dst); dst.close(); src.close()
chk = sqlite3.connect(sys.argv[1]); ok = chk.execute('pragma integrity_check').fetchone()[0]; chk.close()
print('backup integrity', ok); sys.exit(0 if ok == 'ok' else 1)
'@
if ((Invoke-PythonScript $backupCode @((Join-Path $BackupDir 'home-before-swap.sqlite3'))) -ne 0) { throw 'Database backup failed; task left STOPPED' }

# ---------- Package-only reinstall ----------
& $uv pip install --python $python --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { throw 'Package install failed; task left STOPPED. Re-run with the 3a0eec0 wheel to recover.' }

$verifyCode = @'
import base64, csv, hashlib, io, pathlib, sys, zipfile
wheel, site = zipfile.ZipFile(sys.argv[1]), pathlib.Path(sys.argv[2])
record = [n for n in wheel.namelist() if n.endswith('.dist-info/RECORD')][0]
bad = []
for path, digest, _ in csv.reader(io.StringIO(wheel.read(record).decode())):
    if not digest:
        continue
    target = site / path
    if not target.is_file():
        bad.append(path); continue
    algo, expected = digest.split('=', 1)
    got = base64.urlsafe_b64encode(hashlib.new(algo, target.read_bytes()).digest()).rstrip(b'=').decode()
    if got != expected:
        bad.append(path)
print('record_mismatches', len(bad), bad[:5]); sys.exit(1 if bad else 0)
'@
if ((Invoke-PythonScript $verifyCode @($Wheel, $site)) -ne 0) { throw 'Installed package does not match the wheel RECORD; task left STOPPED' }

# ---------- Record revision and start ----------
[Environment]::SetEnvironmentVariable('HERMES_HOME_DEPLOYMENT_REVISION', $Revision, 'Machine')
Start-ScheduledTask -TaskName $task
for ($i = 0; $i -lt 90; $i++) {
  if ((Test-Listening 8780) -and (Test-Listening 8766)) { break }
  Start-Sleep -Seconds 1
}
if (-not ((Test-Listening 8780) -and (Test-Listening 8766))) { throw 'Home did not listen on BOTH 8780 and 8766 after swap' }

$token = (Get-Content -LiteralPath (Join-Path $root 'secrets\admin-token') -Raw).Trim()
try {
  $cfg = Invoke-RestMethod -Uri 'http://127.0.0.1:8780/api/v1/configuration' -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
} finally { $token = $null }
if (-not $cfg.snapshot.revision) { throw 'Authenticated configuration readback failed after swap' }
"swapped to $Revision; listeners 8780+8766 up; configuration revision $($cfg.snapshot.revision); backup $BackupDir"
