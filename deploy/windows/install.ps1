#requires -RunAsAdministrator

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string] $WheelPath,

    [string] $InstallRoot = 'C:\ProgramData\HermesHome',
    [string] $PrometheusConfigPath = 'C:\Program Files\Prometheus\prometheus.yml',
    [string] $BindHost,
    [ValidateRange(1, 65535)]
    [int] $Port = 8780,
    [switch] $AllowTailnetMetricsScrape,
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
    [ValidateRange(1, 600)]
    [double] $ConversationIdleTimeoutSeconds = 8,
    [ValidateRange(1, 64)]
    [int] $ClientClaimsPerDevice = 8,
    [ValidateRange(1, 600)]
    [double] $ClientReconnectGraceSeconds = 120
)

$ErrorActionPreference = 'Stop'
$tailnetMetricsRuleName = 'Hermes Home metrics from Tailscale'
$tailnetMetricsRemoteAddress = '100.64.0.0/10'

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

function Set-DiagnosticsDirectoryAcl {
    param([string] $Path)

    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) {
        [void] $acl.RemoveAccessRule($rule)
    }
    $inheritance = (
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
    $systemRule = New-Object -TypeName System.Security.AccessControl.FileSystemAccessRule -ArgumentList @(
        'NT AUTHORITY\SYSTEM',
        [System.Security.AccessControl.FileSystemRights]::Modify,
        $inheritance,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $adminRule = New-Object -TypeName System.Security.AccessControl.FileSystemAccessRule -ArgumentList @(
        'BUILTIN\Administrators',
        [System.Security.AccessControl.FileSystemRights]::Read,
        $inheritance,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void] $acl.AddAccessRule($systemRule)
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

function Test-WildcardBindHost {
    param([string] $Address)

    $parsed = $null
    $candidate = $Address.Trim().TrimStart('[').TrimEnd(']')
    if (-not [System.Net.IPAddress]::TryParse($candidate, [ref] $parsed)) {
        return $false
    }
    return (
        $parsed.Equals([System.Net.IPAddress]::Any) -or
        $parsed.Equals([System.Net.IPAddress]::IPv6Any)
    )
}

function Test-TailnetAddress {
    # Tailscale assigns 100.64.0.0/10 (IPv4) and fd7a:115c:a1e0::/48 (IPv6).
    param([string] $Address)

    $parsed = $null
    $candidate = $Address.Trim().TrimStart('[').TrimEnd(']')
    if (-not [System.Net.IPAddress]::TryParse($candidate, [ref] $parsed)) {
        return $false
    }
    $bytes = $parsed.GetAddressBytes()
    if ($bytes.Length -eq 4) {
        return ($bytes[0] -eq 100 -and ($bytes[1] -band 0xC0) -eq 64)
    }
    $prefix = @(0xFD, 0x7A, 0x11, 0x5C, 0xA1, 0xE0)
    for ($index = 0; $index -lt $prefix.Count; $index++) {
        if ($bytes[$index] -ne $prefix[$index]) {
            return $false
        }
    }
    return $true
}

function Resolve-BindHostPlan {
    # Decides the machine HERMES_HOME_BIND_HOST before any state changes.
    # An omitted -BindHost preserves the existing machine value; the loopback
    # default applies only when nothing is set (first install).
    param(
        [string] $Requested,
        [bool] $RequestedSupplied,
        [string] $Existing
    )

    $previous = if ([string]::IsNullOrWhiteSpace($Existing)) { $null } else { $Existing.Trim() }
    if ($RequestedSupplied) {
        if ([string]::IsNullOrWhiteSpace($Requested)) {
            throw 'BindHost must not be blank'
        }
        $resolved = $Requested.Trim()
        $source = 'explicit'
    }
    elseif ($null -ne $previous) {
        $resolved = $previous
        $source = 'preserved'
    }
    else {
        $resolved = '127.0.0.1'
        $source = 'default'
    }

    if (Test-TailnetAddress -Address $resolved) {
        throw "HERMES_HOME_BIND_HOST '$resolved' is a Tailscale address. Tailscale Serve proxies the pairing paths to 127.0.0.1, so a tailnet-address-only bind breaks pairing. Rerun with -BindHost 0.0.0.0 and -AllowTailnetMetricsScrape (tailnet-scoped firewall rule) for remote metrics scraping, or -BindHost 127.0.0.1 to stay on loopback."
    }

    return [pscustomobject] @{
        Host     = $resolved
        Source   = $source
        Previous = $previous
        Changed  = ($source -eq 'explicit' -and $previous -ne $resolved)
    }
}

function Get-LocalScrapeHost {
    # The local Prometheus job always dials loopback when Home listens on
    # loopback or a wildcard; only a specific address is used as given.
    param([string] $BindHost)

    $address = $BindHost.Trim()
    if ($address -eq '127.0.0.1' -or (Test-WildcardBindHost -Address $address)) {
        return '127.0.0.1'
    }
    $parsed = $null
    $candidate = $address.TrimStart('[').TrimEnd(']')
    if ([System.Net.IPAddress]::TryParse($candidate, [ref] $parsed) -and
        $parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6) {
        return "[$candidate]"
    }
    return $address
}

function Test-TailnetMetricsRuleShape {
    param(
        [string] $Direction,
        [string] $Action,
        [string] $Enabled,
        [string] $Profile,
        [string] $Protocol,
        [string[]] $LocalPort,
        [string[]] $RemoteAddress,
        [int] $ExpectedPort
    )

    return (
        $Direction -eq 'Inbound' -and
        $Action -eq 'Allow' -and
        $Enabled -eq 'True' -and
        $Profile -eq 'Private' -and
        $Protocol -eq 'TCP' -and
        @($LocalPort).Count -eq 1 -and [string] $LocalPort[0] -eq [string] $ExpectedPort -and
        @($RemoteAddress).Count -eq 1 -and
        $RemoteAddress[0] -in @($tailnetMetricsRemoteAddress, '100.64.0.0/255.192.0.0')
    )
}

function Set-TailnetMetricsFirewallRule {
    # Opt-in only. Creates or converges the single inbound allow that lets the
    # household ops collector scrape /metrics over the tailnet. The rule is
    # scoped to the Tailscale range and the Private profile, never to any
    # address, and is read back before the installer continues.
    param([int] $LocalPort)

    $rules = @(Get-NetFirewallRule -DisplayName $tailnetMetricsRuleName -ErrorAction SilentlyContinue)
    if ($rules.Count -eq 0) {
        New-NetFirewallRule -DisplayName $tailnetMetricsRuleName `
            -Description 'Allow the household ops collector to scrape Hermes Home /metrics over Tailscale only.' `
            -Direction Inbound -Action Allow -Enabled True -Profile Private `
            -Protocol TCP -LocalPort $LocalPort -RemoteAddress $tailnetMetricsRemoteAddress | Out-Null
        $outcome = 'created'
    }
    else {
        $rules | Set-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -Profile Private
        $rules | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $LocalPort
        $rules | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $tailnetMetricsRemoteAddress
        $outcome = 'updated'
    }

    $applied = @(Get-NetFirewallRule -DisplayName $tailnetMetricsRuleName)
    if ($applied.Count -eq 0) {
        throw "Firewall rule '$tailnetMetricsRuleName' was not found after it was applied"
    }
    foreach ($rule in $applied) {
        $portFilter = $rule | Get-NetFirewallPortFilter
        $addressFilter = $rule | Get-NetFirewallAddressFilter
        $shapeOk = Test-TailnetMetricsRuleShape `
            -Direction ([string] $rule.Direction) -Action ([string] $rule.Action) `
            -Enabled ([string] $rule.Enabled) -Profile ([string] $rule.Profile) `
            -Protocol ([string] $portFilter.Protocol) `
            -LocalPort @($portFilter.LocalPort | ForEach-Object { [string] $_ }) `
            -RemoteAddress @($addressFilter.RemoteAddress | ForEach-Object { [string] $_ }) `
            -ExpectedPort $LocalPort
        if (-not $shapeOk) {
            throw "Firewall rule '$tailnetMetricsRuleName' does not match TCP $LocalPort from $tailnetMetricsRemoteAddress on the Private profile after it was applied"
        }
    }
    Write-Output "Firewall rule '$tailnetMetricsRuleName' $outcome"
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
$logRoot = Join-Path $root 'logs'
$diagnosticsRoot = Join-Path $root 'diagnostics'
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
    -not [string]::IsNullOrWhiteSpace($StandardTokenFile)
)
if ($standardConfigured -and (
        [string]::IsNullOrWhiteSpace($StandardGatewayUrl) -or
        [string]::IsNullOrWhiteSpace($StandardTokenFile)
    )) {
    throw 'Standard bridge settings require StandardGatewayUrl and StandardTokenFile'
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

$existingBindHost = [Environment]::GetEnvironmentVariable('HERMES_HOME_BIND_HOST', 'Machine')
$bindPlan = Resolve-BindHostPlan -Requested $BindHost -RequestedSupplied $PSBoundParameters.ContainsKey('BindHost') -Existing $existingBindHost
$localScrapeHost = Get-LocalScrapeHost -BindHost $bindPlan.Host
switch ($bindPlan.Source) {
    'default' { Write-Output "HERMES_HOME_BIND_HOST not set; first install defaults to $($bindPlan.Host)" }
    'preserved' { Write-Output "HERMES_HOME_BIND_HOST preserved: $($bindPlan.Host) (pass -BindHost to change it)" }
    'explicit' {
        if ($bindPlan.Changed) {
            Write-Warning "HERMES_HOME_BIND_HOST changes from '$($bindPlan.Previous)' to '$($bindPlan.Host)' because -BindHost was supplied"
        }
        else {
            Write-Output "HERMES_HOME_BIND_HOST already $($bindPlan.Host)"
        }
    }
}
if ($AllowTailnetMetricsScrape -and -not (Test-WildcardBindHost -Address $bindPlan.Host)) {
    Write-Warning "-AllowTailnetMetricsScrape creates the tailnet-scoped firewall rule, but Home is bound to '$($bindPlan.Host)', so a tailnet collector cannot reach it. Remote scraping needs HERMES_HOME_BIND_HOST 0.0.0.0 (pass -BindHost 0.0.0.0)."
}
elseif (-not $AllowTailnetMetricsScrape -and (Test-WildcardBindHost -Address $bindPlan.Host)) {
    Write-Output "NOTE: Home listens on every interface; inbound reachability of TCP $Port is decided by Windows Firewall. This installer opens no firewall port unless -AllowTailnetMetricsScrape is passed."
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$taskWasRunning = $null -ne $existingTask -and $existingTask.State -eq 'Running'
try {
    $null = Stop-ExistingHermesHomeTask -Name $TaskName
    New-Item -ItemType Directory -Path $root, $appRoot, $secretRoot, $logRoot, $diagnosticsRoot -Force | Out-Null
    Set-DiagnosticsDirectoryAcl -Path $diagnosticsRoot
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
    [Environment]::SetEnvironmentVariable('HERMES_HOME_DIAGNOSTICS_DIR', $diagnosticsRoot, 'Machine')
    [Environment]::SetEnvironmentVariable('HERMES_HOME_BIND_HOST', $bindPlan.Host, 'Machine')
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

        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_GATEWAY_URL', $StandardGatewayUrl.Trim(), 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_TOKEN_FILE', $standardTokenPath, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CONVERSATION_IDLE_TIMEOUT_SECONDS', $ConversationIdleTimeoutSeconds.ToString([System.Globalization.CultureInfo]::InvariantCulture), 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE', $ClientClaimsPerDevice.ToString([System.Globalization.CultureInfo]::InvariantCulture), 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CLIENT_RECONNECT_GRACE_SECONDS', $ClientReconnectGraceSeconds.ToString([System.Globalization.CultureInfo]::InvariantCulture), 'Machine')
    }
    else {
        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_GATEWAY_URL', $null, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_STANDARD_TOKEN_FILE', $null, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CONVERSATION_IDLE_TIMEOUT_SECONDS', $null, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE', $null, 'Machine')
        [Environment]::SetEnvironmentVariable('HERMES_HOME_CLIENT_RECONNECT_GRACE_SECONDS', $null, 'Machine')
    }

    if ($AllowTailnetMetricsScrape) {
        Set-TailnetMetricsFirewallRule -LocalPort $Port
    }
    $backup = Update-PrometheusConfig -ConfigPath $PrometheusConfigPath -PrometheusTokenPath $tokenPath -TargetHost $localScrapeHost -TargetPort $Port
    Register-HermesHomeTask -Name $TaskName -RunnerPath $runnerPath -WorkingDirectory $root
    Start-ScheduledTask -TaskName $TaskName
    Wait-ForHomeMetrics -HostName $localScrapeHost -TargetPort $Port -Token $token
    Wait-ForPrometheusTarget

    Write-Output "Hermes Home installed under $root"
    Write-Output "Prometheus configuration backup: $backup"
    Write-Output "Scheduled task: $TaskName"
    Write-Output "Home listener: $($bindPlan.Host)`:$Port ($($bindPlan.Source)); local scrape target: $localScrapeHost`:$Port"
    if ($AllowTailnetMetricsScrape) {
        Write-Output "Firewall rule '$tailnetMetricsRuleName': TCP $Port from $tailnetMetricsRemoteAddress, Private profile"
    }
    Write-Output "Bridge listener: $BridgeBindHost`:$BridgePort"
    Write-Output "Bridge route ID: $($BridgeRouteId.Trim())"
    if ($standardConfigured) {
        Write-Output "Standard pilot target: $($StandardGatewayUrl.Trim())"
        Write-Output "Conversation idle timeout: $ConversationIdleTimeoutSeconds seconds"
        Write-Output "Client claims per device: $ClientClaimsPerDevice"
        Write-Output "Client reconnect grace: $ClientReconnectGraceSeconds seconds"
    }
}
catch {
    if ($taskWasRunning) {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    throw
}
