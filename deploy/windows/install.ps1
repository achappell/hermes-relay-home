#requires -RunAsAdministrator

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string] $WheelPath,

    [string] $InstallRoot = 'C:\ProgramData\HermesHome',
    [string] $PrometheusConfigPath = 'C:\Program Files\Prometheus\prometheus.yml',
    [string] $BindHost = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int] $Port = 8780,
    [string] $BridgeBindHost = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int] $BridgePort = 8766,
    [string] $BridgeRouteId = 'local',
    [string] $TaskName = 'Hermes Home',
    [string] $UvPath = '',
    [string] $DeviceCredentialsFile = '',
    [string] $CredentialRootSecretFile = '',
    [string] $StandardGatewayUrl = '',
    [string] $StandardTokenFile = '',
    [string] $ConversationGrantsFile = ''
)

$ErrorActionPreference = 'Stop'

function Resolve-UvPath {
    param([string] $RequestedPath)

    if ($RequestedPath) {
        if (-not (Test-Path -LiteralPath $RequestedPath -PathType Leaf)) {
            throw "uv was not found at $RequestedPath"
        }
        return (Resolve-Path -LiteralPath $RequestedPath).Path
    }

    $command = Get-Command uv.exe, uv -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $command) {
        throw 'uv is required to install the Python 3.14 runtime; install uv and rerun this script'
    }
    return $command.Source
}

function Set-SecretFileAcl {
    param([string] $Path)

    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) {
        [void] $acl.RemoveAccessRule($rule)
    }
    $readRule = New-Object -TypeName System.Security.AccessControl.FileSystemAccessRule -ArgumentList @(
        'NT AUTHORITY\SYSTEM', 'Read', 'Allow'
    )
    $adminRule = New-Object -TypeName System.Security.AccessControl.FileSystemAccessRule -ArgumentList @(
        'BUILTIN\Administrators', 'FullControl', 'Allow'
    )
    [void] $acl.AddAccessRule($readRule)
    [void] $acl.AddAccessRule($adminRule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Ensure-AdminToken {
    param([string] $Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $bytes = New-Object byte[] 32
        $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $random.GetBytes($bytes)
        }
        finally {
            $random.Dispose()
        }
        $token = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
        $utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
        [System.IO.File]::WriteAllText($Path, $token, $utf8)
    }

    $value = (Get-Content -LiteralPath $Path -Raw).Trim()
    if (-not $value) {
        throw "Admin token file is blank: $Path"
    }
    Set-SecretFileAcl -Path $Path
    return $value
}

function Update-PrometheusConfig {
    param(
        [string] $ConfigPath,
        [string] $PrometheusTokenPath,
        [string] $TargetHost,
        [int] $TargetPort
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "Prometheus configuration was not found: $ConfigPath"
    }

    $promtool = Join-Path (Split-Path -Parent $ConfigPath) 'promtool.exe'
    if (-not (Test-Path -LiteralPath $promtool -PathType Leaf)) {
        throw "promtool was not found beside Prometheus: $promtool"
    }

    $existing = [System.IO.File]::ReadAllText($ConfigPath)
    $jobBlock = @"
  # BEGIN HERMES HOME
  - job_name: 'hermes-home'
    scrape_timeout: 5s
    static_configs:
    - targets: ['$TargetHost`:$TargetPort']
      labels:
        host: 'caticornqueen'
    bearer_token_file: '$PrometheusTokenPath'
  # END HERMES HOME
"@
    $markerPattern = '(?ms)^[ \t]*# BEGIN HERMES HOME\r?\n.*?^[ \t]*# END HERMES HOME[ \t]*(?:\r?\n|$)'
    if ([regex]::IsMatch($existing, $markerPattern)) {
        $updated = [regex]::Replace(
            $existing,
            $markerPattern,
            $jobBlock.TrimEnd() + [Environment]::NewLine,
            1
        )
    }
    else {
        $updated = $existing.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $jobBlock
    }

    $temporaryPath = "$ConfigPath.hermes-home.tmp"
    $utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($temporaryPath, $updated, $utf8)
    $checkOutput = & $promtool check config $temporaryPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        throw "Prometheus configuration validation failed: $checkOutput"
    }

    $backupPath = "$ConfigPath.bak-hermes-home-$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Copy-Item -LiteralPath $ConfigPath -Destination $backupPath
    Move-Item -LiteralPath $temporaryPath -Destination $ConfigPath -Force
    Restart-Service -Name prometheus -Force
    return $backupPath
}

function Register-HermesHomeTask {
    param(
        [string] $Name,
        [string] $RunnerPath,
        [string] $WorkingDirectory
    )

    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $RunnerPath + '"'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Stop-ExistingHermesHomeTask {
    param([string] $Name)

    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $existing -or $existing.State -ne 'Running') {
        return $false
    }
    Stop-ScheduledTask -TaskName $Name
    for ($attempt = 1; $attempt -le 15; $attempt++) {
        if ((Get-ScheduledTask -TaskName $Name).State -ne 'Running') {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    throw "Scheduled task did not stop: $Name"
}

function Wait-ForHomeMetrics {
    param(
        [string] $HostName,
        [int] $TargetPort,
        [string] $Token
    )

    $uri = "http://$HostName`:$TargetPort/metrics"
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $uri -Headers @{ Authorization = "Bearer $Token" } -UseBasicParsing
            if ($response.StatusCode -eq 200 -and $response.Content -match 'hermes_home_http_requests_total') {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "Hermes Home did not expose authenticated metrics at $uri"
}

function Wait-ForPrometheusTarget {
    $query = 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22hermes-home%22%7D'
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $result = Invoke-RestMethod -Uri $query -UseBasicParsing
            $series = @($result.data.result)
            if ($result.status -eq 'success' -and $series.Count -gt 0 -and $series[0].value[1] -eq '1') {
                return
            }
        }
        catch {
        }
        Start-Sleep -Seconds 1
    }
    throw 'Prometheus did not report an up hermes-home target within 30 seconds'
}

$root = [System.IO.Path]::GetFullPath($InstallRoot)
$appRoot = Join-Path $root 'app'
$pythonRoot = Join-Path $root 'python'
$venvRoot = Join-Path $root 'venv'
$secretRoot = Join-Path $root 'secrets'
$tokenPath = Join-Path $secretRoot 'admin-token'
$runnerSource = Join-Path $PSScriptRoot 'run.ps1'
$runnerPath = Join-Path $root 'run.ps1'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'

if ($DeviceCredentialsFile -and $CredentialRootSecretFile) {
    throw 'DeviceCredentialsFile and CredentialRootSecretFile cannot be configured together'
}
if ([string]::IsNullOrWhiteSpace($BridgeBindHost)) {
    throw 'BridgeBindHost must not be blank'
}
if ([string]::IsNullOrWhiteSpace($BridgeRouteId)) {
    throw 'BridgeRouteId must not be blank'
}
if ($BridgeRouteId -match '[\r\n]') {
    throw 'BridgeRouteId must not contain line breaks'
}
$standardConfigured = (
    -not [string]::IsNullOrWhiteSpace($StandardGatewayUrl) -or
    -not [string]::IsNullOrWhiteSpace($StandardTokenFile) -or
    -not [string]::IsNullOrWhiteSpace($ConversationGrantsFile)
)
if ($standardConfigured -and (
        [string]::IsNullOrWhiteSpace($StandardGatewayUrl) -or
        [string]::IsNullOrWhiteSpace($StandardTokenFile) -or
        [string]::IsNullOrWhiteSpace($ConversationGrantsFile)
    )) {
    throw 'Standard bridge settings require StandardGatewayUrl, StandardTokenFile, and ConversationGrantsFile'
}
if ($standardConfigured -and -not ($DeviceCredentialsFile -or $CredentialRootSecretFile)) {
    throw 'Standard bridge settings require DeviceCredentialsFile or CredentialRootSecretFile'
}
if ($standardConfigured) {
    try {
        $standardUri = [System.Uri] $StandardGatewayUrl
    }
    catch {
        throw 'StandardGatewayUrl must be a valid ws or wss URL ending in /api/ws'
    }
    if ($standardUri.Scheme -notin @('ws', 'wss') -or
        [string]::IsNullOrWhiteSpace($standardUri.Host) -or
        $standardUri.AbsolutePath.TrimEnd('/') -ne '/api/ws' -or
        $standardUri.Fragment) {
        throw 'StandardGatewayUrl must be a ws or wss URL ending in /api/ws without a fragment'
    }
}

if (-not (Test-Path -LiteralPath $runnerSource -PathType Leaf)) {
    throw "The deployment bundle is missing run.ps1 beside install.ps1"
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$taskWasRunning = $null -ne $existingTask -and $existingTask.State -eq 'Running'
try {
    $null = Stop-ExistingHermesHomeTask -Name $TaskName
    New-Item -ItemType Directory -Path $root, $appRoot, $secretRoot -Force | Out-Null
    Copy-Item -LiteralPath $runnerSource -Destination $runnerPath -Force

    $uv = Resolve-UvPath -RequestedPath $UvPath
    $env:UV_PYTHON_INSTALL_DIR = $pythonRoot
    & $uv python install 3.14 --no-bin
    if ($LASTEXITCODE -ne 0) {
        throw 'uv could not install Python 3.14'
    }
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        & $uv venv --python 3.14 --allow-existing --link-mode copy $venvRoot
    }
    else {
        & $uv venv --python 3.14 --link-mode copy $venvRoot
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'uv could not create the Hermes Home virtual environment'
    }
    & $uv pip install --python $venvPython --force-reinstall $WheelPath
    if ($LASTEXITCODE -ne 0) {
        throw 'uv could not install the Hermes Home wheel'
    }

    $token = Ensure-AdminToken -Path $tokenPath
    [Environment]::SetEnvironmentVariable('HERMES_HOME_DATA_DIR', $root, 'Machine')
    [Environment]::SetEnvironmentVariable('HERMES_HOME_BIND_HOST', $BindHost, 'Machine')
    [Environment]::SetEnvironmentVariable('HERMES_HOME_PORT', [string] $Port, 'Machine')
    [Environment]::SetEnvironmentVariable('HERMES_HOME_BRIDGE_BIND_HOST', $BridgeBindHost, 'Machine')
    [Environment]::SetEnvironmentVariable('HERMES_HOME_BRIDGE_PORT', [string] $BridgePort, 'Machine')
    [Environment]::SetEnvironmentVariable('HERMES_HOME_BRIDGE_ROUTE_ID', $BridgeRouteId.Trim(), 'Machine')
    [Environment]::SetEnvironmentVariable('HERMES_HOME_ADMIN_TOKEN_FILE', $tokenPath, 'Machine')
    if ($CredentialRootSecretFile) {
        if (-not (Test-Path -LiteralPath $CredentialRootSecretFile -PathType Leaf)) {
            throw "Credential root secret file was not found: $CredentialRootSecretFile"
        }
        $credentialRootPath = (Resolve-Path -LiteralPath $CredentialRootSecretFile).Path
        $credentialRoot = (Get-Content -LiteralPath $credentialRootPath -Raw).Trim()
        if ($credentialRoot -notmatch '^[0-9a-fA-F]{64}$') {
            throw 'Credential root secret must contain exactly 64 hexadecimal characters'
        }
        Set-SecretFileAcl -Path $credentialRootPath
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE', $credentialRootPath, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_DEVICE_CREDENTIALS_FILE', $null, 'Machine')
    }
    else {
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE', $null, 'Machine')
    }
    if ($DeviceCredentialsFile) {
        if (-not (Test-Path -LiteralPath $DeviceCredentialsFile -PathType Leaf)) {
            throw "Device credentials file was not found: $DeviceCredentialsFile"
        }
        [Environment]::SetEnvironmentVariable('HERMES_HOME_DEVICE_CREDENTIALS_FILE', $DeviceCredentialsFile, 'Machine')
    }
    else {
        [Environment]::SetEnvironmentVariable('HERMES_HOME_DEVICE_CREDENTIALS_FILE', $null, 'Machine')
    }

    if ($standardConfigured) {
        if (-not (Test-Path -LiteralPath $StandardTokenFile -PathType Leaf)) {
            throw "Standard token file was not found: $StandardTokenFile"
        }
        $standardTokenPath = (Resolve-Path -LiteralPath $StandardTokenFile).Path
        $standardToken = (Get-Content -LiteralPath $standardTokenPath -Raw).Trim()
        if (-not $standardToken) {
            throw "Standard token file is blank: $standardTokenPath"
        }
        Set-SecretFileAcl -Path $standardTokenPath

        if (-not (Test-Path -LiteralPath $ConversationGrantsFile -PathType Leaf)) {
            $grantsParent = Split-Path -Parent $ConversationGrantsFile
            if ($grantsParent) {
                New-Item -ItemType Directory -Path $grantsParent -Force | Out-Null
            }
            $emptyGrants = '{"schema":1,"grants":[]}'
            [System.IO.File]::WriteAllText($ConversationGrantsFile, $emptyGrants)
        }
        $conversationGrantsPath = (Resolve-Path -LiteralPath $ConversationGrantsFile).Path
        Set-SecretFileAcl -Path $conversationGrantsPath
        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_GATEWAY_URL', $StandardGatewayUrl.Trim(), 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_TOKEN_FILE', $standardTokenPath, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CONVERSATION_GRANTS_FILE', $conversationGrantsPath, 'Machine')
    }
    else {
        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_GATEWAY_URL', $null, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_TOKEN_FILE', $null, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CONVERSATION_GRANTS_FILE', $null, 'Machine')
    }

    $backup = Update-PrometheusConfig -ConfigPath $PrometheusConfigPath -PrometheusTokenPath $tokenPath -TargetHost $BindHost -TargetPort $Port
    Register-HermesHomeTask -Name $TaskName -RunnerPath $runnerPath -WorkingDirectory $root
    Start-ScheduledTask -TaskName $TaskName
    Wait-ForHomeMetrics -HostName $BindHost -TargetPort $Port -Token $token
    Wait-ForPrometheusTarget

    Write-Output "Hermes Home installed under $root"
    Write-Output "Prometheus configuration backup: $backup"
    Write-Output "Scheduled task: $TaskName"
    Write-Output "Bridge listener: $BridgeBindHost`:$BridgePort"
    Write-Output "Bridge route ID: $($BridgeRouteId.Trim())"
    if ($standardConfigured) {
        Write-Output "Standard pilot target: $($StandardGatewayUrl.Trim())"
        Write-Output "Conversation grants: $conversationGrantsPath"
    }
}
catch {
    if ($taskWasRunning) {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    throw
}
