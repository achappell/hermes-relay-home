"""Checks for the Windows installer's bind-host and firewall handling.

The installer cannot run here (it needs Windows, admin rights and uv), so the
tests take two routes:

* Static checks of ``deploy/windows/install.ps1`` and the deployment docs.
* When PowerShell (``pwsh``) is on PATH, the installer's helper functions are
  extracted from the script's AST and executed against inputs, with the
  Windows Firewall cmdlets replaced by an in-memory fake. These are skipped
  when ``pwsh`` is missing.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
INSTALLER = ROOT / "deploy" / "windows" / "install.ps1"
WINDOWS_README = ROOT / "deploy" / "windows" / "README.md"
OPS_README = ROOT / "deploy" / "ops" / "README.md"
ALLOY = ROOT / "deploy" / "ops" / "hermes-home.alloy"

RULE_NAME = "Hermes Home metrics from Tailscale"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")


# --- static checks -----------------------------------------------------------


def test_bind_host_is_optional_and_has_no_hardcoded_default() -> None:
    installer = INSTALLER.read_text()

    assert re.search(r"^\s*\[string\] \$BindHost,$", installer, re.MULTILINE)
    assert "$BindHost = " not in installer
    assert "[switch] $AllowTailnetMetricsScrape" in installer
    assert "$PSBoundParameters.ContainsKey('BindHost')" in installer, (
        "explicit -BindHost must be distinguished from an omitted one"
    )


def test_installer_writes_only_the_resolved_bind_and_scrapes_the_derived_host() -> None:
    installer = INSTALLER.read_text()

    assert (
        "SetEnvironmentVariable('HERMES_HOME_BIND_HOST', $bindPlan.Host, 'Machine')"
        in installer
    )
    assert "SetEnvironmentVariable('HERMES_HOME_BIND_HOST', $BindHost" not in installer
    assert "-TargetHost $localScrapeHost" in installer
    assert "-HostName $localScrapeHost" in installer
    # The raw parameter must never reach the Prometheus target or the probe.
    assert "-TargetHost $BindHost" not in installer
    assert "-HostName $BindHost" not in installer


def test_bind_plan_is_resolved_before_any_state_changes() -> None:
    installer = INSTALLER.read_text()

    plan = installer.index("$bindPlan = Resolve-BindHostPlan")
    assert plan < installer.index("$null = Stop-ExistingHermesHomeTask")
    assert plan < installer.index("& $uv python install")


def test_firewall_is_opt_in_exact_and_never_open_to_any_address() -> None:
    installer = INSTALLER.read_text()

    assert "$tailnetMetricsRuleName = 'Hermes Home metrics from Tailscale'" in installer
    assert "$tailnetMetricsRemoteAddress = '100.64.0.0/10'" in installer
    assert installer.count("New-NetFirewallRule") == 1
    create = installer[installer.index("New-NetFirewallRule") :]
    create = create[: create.index("| Out-Null")]
    for fragment in (
        "-Direction Inbound",
        "-Action Allow",
        "-Profile Private",
        "-Protocol TCP",
        "-LocalPort $LocalPort",
        "-RemoteAddress $tailnetMetricsRemoteAddress",
    ):
        assert fragment in create
    assert "Any" not in create
    assert "-RemoteAddress Any" not in installer
    assert "LocalSubnet" not in installer
    # The only caller sits behind the opt-in switch.
    calls = [
        m.start()
        for m in re.finditer(
            r"^\s*Set-TailnetMetricsFirewallRule ", installer, re.MULTILINE
        )
    ]
    assert len(calls) == 1
    guard = installer[: calls[0]].rindex("if (")
    assert installer[guard : calls[0]].startswith("if ($AllowTailnetMetricsScrape)")


def test_installer_warns_when_scraping_is_requested_without_a_wildcard_bind() -> None:
    installer = INSTALLER.read_text()

    assert "$AllowTailnetMetricsScrape -and -not (Test-WildcardBindHost" in installer
    assert "Remote scraping needs HERMES_HOME_BIND_HOST 0.0.0.0" in installer


def test_windows_readme_documents_the_durable_bind_and_firewall_setup() -> None:
    readme = WINDOWS_README.read_text()

    assert "-AllowTailnetMetricsScrape" in readme
    assert RULE_NAME in readme
    assert "100.64.0.0/10" in readme
    assert "HERMES_HOME_BIND_HOST" in readme
    assert "Package-only" in readme or "package-only" in readme
    assert "Remove-NetFirewallRule" in readme  # rollback
    assert "-BindHost 100.78.105.19" not in readme


def test_ops_readme_no_longer_tells_operators_to_bind_the_tailnet_address() -> None:
    readme = OPS_README.read_text()

    assert "-BindHost 100.78.105.19" not in readme
    assert "-BindHost 0.0.0.0 -AllowTailnetMetricsScrape" in readme
    assert RULE_NAME in readme
    assert "127.0.0.1:8780" in readme  # Tailscale Serve and local Prometheus
    assert "Test-NetConnection" in readme
    assert "Remove-NetFirewallRule" in readme


def test_ops_alloy_still_scrapes_the_tailnet_address() -> None:
    assert "100.78.105.19:8780" in ALLOY.read_text()


# --- executed checks (pwsh) --------------------------------------------------

HELPERS = (
    "Test-WildcardBindHost",
    "Test-TailnetAddress",
    "Resolve-BindHostPlan",
    "Get-LocalScrapeHost",
    "Test-TailnetMetricsRuleShape",
    "Set-TailnetMetricsFirewallRule",
)

PRELUDE = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:INSTALLER_PATH, [ref] $tokens, [ref] $errors)
if ($errors.Count -gt 0) { throw ($errors | Out-String) }
$tailnetMetricsRuleName = 'Hermes Home metrics from Tailscale'
$tailnetMetricsRemoteAddress = '100.64.0.0/10'
$wanted = $env:INSTALLER_FUNCTIONS -split ','
$definitions = $ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($definition in $definitions) {
    if ($wanted -contains $definition.Name) { Invoke-Expression $definition.Extent.Text }
}

function Invoke-Plan {
    param([string] $Requested, [bool] $Supplied, [string] $Existing)
    try {
        $plan = Resolve-BindHostPlan -Requested $Requested -RequestedSupplied $Supplied -Existing $Existing
        return [ordered] @{
            bind = $plan.Host; source = $plan.Source; changed = $plan.Changed
            previous = $plan.Previous; scrape = (Get-LocalScrapeHost -BindHost $plan.Host)
            error = $null
        }
    }
    catch {
        return [ordered] @{ bind = $null; source = $null; changed = $null
            previous = $null; scrape = $null; error = $_.Exception.Message }
    }
}
"""

PLAN_CASES = [
    # id, requested (None = omitted), existing
    ("first-install-default", None, None),
    ("first-install-blank-machine-value", None, "  "),
    ("omitted-preserves-wildcard", None, "0.0.0.0"),
    ("omitted-preserves-loopback", None, "127.0.0.1"),
    ("omitted-preserves-lan-address", None, "192.168.0.4"),
    ("omitted-preserves-ipv6-wildcard", None, "::"),
    ("explicit-wildcard-over-loopback", "0.0.0.0", "127.0.0.1"),
    ("explicit-same-as-existing", "0.0.0.0", "0.0.0.0"),
    ("explicit-loopback-over-wildcard", "127.0.0.1", "0.0.0.0"),
    ("explicit-first-install", "0.0.0.0", None),
    ("explicit-lan-address", "192.168.0.4", "0.0.0.0"),
    ("explicit-ipv6-loopback", "::1", None),
    ("explicit-hostname", "localhost", None),
    ("explicit-tailnet-ip-rejected", "100.78.105.19", "0.0.0.0"),
    ("explicit-tailnet-range-edge-rejected", "100.127.255.254", None),
    ("explicit-tailnet-ipv6-rejected", "fd7a:115c:a1e0::1", None),
    ("omitted-stale-tailnet-ip-rejected", None, "100.78.105.19"),
    ("outside-tailnet-range-100.128-allowed", "100.128.0.1", None),
    ("outside-tailnet-range-100.63-allowed", "100.63.255.255", None),
    ("explicit-blank-rejected", "", "0.0.0.0"),
]

PLAN_EXPECTED = {
    "first-install-default": ("127.0.0.1", "default", False, "127.0.0.1"),
    "first-install-blank-machine-value": ("127.0.0.1", "default", False, "127.0.0.1"),
    "omitted-preserves-wildcard": ("0.0.0.0", "preserved", False, "127.0.0.1"),
    "omitted-preserves-loopback": ("127.0.0.1", "preserved", False, "127.0.0.1"),
    "omitted-preserves-lan-address": ("192.168.0.4", "preserved", False, "192.168.0.4"),
    "omitted-preserves-ipv6-wildcard": ("::", "preserved", False, "127.0.0.1"),
    "explicit-wildcard-over-loopback": ("0.0.0.0", "explicit", True, "127.0.0.1"),
    "explicit-same-as-existing": ("0.0.0.0", "explicit", False, "127.0.0.1"),
    "explicit-loopback-over-wildcard": ("127.0.0.1", "explicit", True, "127.0.0.1"),
    "explicit-first-install": ("0.0.0.0", "explicit", True, "127.0.0.1"),
    "explicit-lan-address": ("192.168.0.4", "explicit", True, "192.168.0.4"),
    "explicit-ipv6-loopback": ("::1", "explicit", True, "[::1]"),
    "explicit-hostname": ("localhost", "explicit", True, "localhost"),
    "outside-tailnet-range-100.128-allowed": (
        "100.128.0.1",
        "explicit",
        True,
        "100.128.0.1",
    ),
    "outside-tailnet-range-100.63-allowed": (
        "100.63.255.255",
        "explicit",
        True,
        "100.63.255.255",
    ),
}

PLAN_REJECTED = {
    "explicit-tailnet-ip-rejected": "Tailscale address",
    "explicit-tailnet-range-edge-rejected": "Tailscale address",
    "explicit-tailnet-ipv6-rejected": "Tailscale address",
    "omitted-stale-tailnet-ip-rejected": "Tailscale address",
    "explicit-blank-rejected": "must not be blank",
}


def run_pwsh(body: str, functions: tuple[str, ...]) -> object:
    assert PWSH is not None
    script = PRELUDE + body
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
        env={
            "PATH": str(Path(PWSH).parent),
            "HOME": str(Path.home()),
            "INSTALLER_PATH": str(INSTALLER),
            "INSTALLER_FUNCTIONS": ",".join(functions),
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def _ps_string(value: str | None) -> str:
    return "$null" if value is None else "'" + value.replace("'", "''") + "'"


@pytest.fixture(scope="module")
def plan_results() -> dict[str, dict]:
    if PWSH is None:
        pytest.skip("pwsh is not installed")
    lines = []
    for case_id, requested, existing in PLAN_CASES:
        supplied = "$false" if requested is None else "$true"
        lines.append(
            f"$results['{case_id}'] = Invoke-Plan -Requested {_ps_string(requested)} "
            f"-Supplied {supplied} -Existing {_ps_string(existing)}"
        )
    body = (
        "$results = [ordered] @{}\n"
        + "\n".join(lines)
        + "\n$results | ConvertTo-Json -Depth 4 -Compress\n"
    )
    return run_pwsh(body, HELPERS)  # type: ignore[return-value]


@needs_pwsh
def test_installer_script_parses_without_errors() -> None:
    assert run_pwsh("'true'", ()) is True  # PRELUDE throws on any parse error


@needs_pwsh
@pytest.mark.parametrize("case_id", sorted(PLAN_EXPECTED))
def test_bind_plan_and_local_scrape_target(plan_results, case_id) -> None:
    bind, source, changed, scrape = PLAN_EXPECTED[case_id]
    actual = plan_results[case_id]

    assert actual["error"] is None
    assert actual["bind"] == bind
    assert actual["source"] == source
    assert actual["changed"] is changed
    assert actual["scrape"] == scrape


@needs_pwsh
@pytest.mark.parametrize("case_id", sorted(PLAN_REJECTED))
def test_bind_plan_rejections(plan_results, case_id) -> None:
    actual = plan_results[case_id]

    assert actual["bind"] is None
    assert PLAN_REJECTED[case_id] in actual["error"]


FIREWALL_FAKE = r"""
$global:rules = @{}
$global:calls = New-Object System.Collections.ArrayList
$global:narrowAddress = $true

function Get-NetFirewallRule {
    [CmdletBinding()] param([string] $DisplayName)
    if ($global:rules.ContainsKey($DisplayName)) { return $global:rules[$DisplayName] }
}
function New-NetFirewallRule {
    [CmdletBinding()]
    param([string] $DisplayName, [string] $Description, [string] $Direction, [string] $Action,
          [string] $Enabled, [string] $Profile, [string] $Protocol, $LocalPort, [string] $RemoteAddress)
    [void] $global:calls.Add("New:$RemoteAddress")
    $remote = if ($RemoteAddress -eq '100.64.0.0/10') { '100.64.0.0/255.192.0.0' } else { $RemoteAddress }
    $global:rules[$DisplayName] = [pscustomobject] @{
        DisplayName = $DisplayName; Direction = $Direction; Action = $Action; Enabled = $Enabled
        Profile = $Profile; Protocol = $Protocol; LocalPort = [string] $LocalPort; RemoteAddress = $remote }
}
function Set-NetFirewallRule {
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline)] $InputObject, [string] $Direction, [string] $Action,
          [string] $Enabled, [string] $Profile)
    process {
        [void] $global:calls.Add('SetRule')
        $InputObject.Direction = $Direction; $InputObject.Action = $Action
        $InputObject.Enabled = $Enabled; $InputObject.Profile = $Profile
    }
}
function Get-NetFirewallPortFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline)] $InputObject)
    process { $InputObject }
}
function Get-NetFirewallAddressFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline)] $InputObject)
    process { $InputObject }
}
function Set-NetFirewallPortFilter {
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline)] $InputObject, [string] $Protocol, $LocalPort)
    process { $InputObject.Protocol = $Protocol; $InputObject.LocalPort = [string] $LocalPort }
}
function Set-NetFirewallAddressFilter {
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline)] $InputObject, [string] $RemoteAddress)
    process {
        if ($global:narrowAddress) {
            $InputObject.RemoteAddress = if ($RemoteAddress -eq '100.64.0.0/10') { '100.64.0.0/255.192.0.0' } else { $RemoteAddress }
        }
    }
}
function Snapshot {
    [ordered] @{ rules = @($global:rules.Values | ForEach-Object { $_.PSObject.Copy() }); calls = @($global:calls) }
}
"""

FIREWALL_BODY = r"""
$out = [ordered] @{}

$first = (Set-TailnetMetricsFirewallRule -LocalPort 8780) -join '|'
$out['first'] = [ordered] @{ message = $first; state = (Snapshot) }

$global:calls.Clear()
$second = (Set-TailnetMetricsFirewallRule -LocalPort 8780) -join '|'
$out['second'] = [ordered] @{ message = $second; state = (Snapshot) }

# A pre-existing, too-broad rule of the same name is narrowed in place.
$global:calls.Clear()
$global:rules[$tailnetMetricsRuleName] = [pscustomobject] @{
    DisplayName = $tailnetMetricsRuleName; Direction = 'Inbound'; Action = 'Allow'; Enabled = 'False'
    Profile = 'Any'; Protocol = 'TCP'; LocalPort = '9999'; RemoteAddress = 'Any' }
$third = (Set-TailnetMetricsFirewallRule -LocalPort 8780) -join '|'
$out['broad'] = [ordered] @{ message = $third; state = (Snapshot) }

# If the system does not apply the address scope, the installer must fail.
$global:rules[$tailnetMetricsRuleName].RemoteAddress = 'Any'
$global:narrowAddress = $false
try { Set-TailnetMetricsFirewallRule -LocalPort 8780 | Out-Null; $failure = $null }
catch { $failure = $_.Exception.Message }
$out['unscoped'] = [ordered] @{ error = $failure }

# A different port converges to the requested one.
$global:narrowAddress = $true
$global:calls.Clear()
$fifth = (Set-TailnetMetricsFirewallRule -LocalPort 18780) -join '|'
$out['port'] = [ordered] @{ message = $fifth; state = (Snapshot) }

$out | ConvertTo-Json -Depth 6 -Compress
"""


@pytest.fixture(scope="module")
def firewall_results() -> dict:
    if PWSH is None:
        pytest.skip("pwsh is not installed")
    return run_pwsh(FIREWALL_FAKE + FIREWALL_BODY, HELPERS)  # type: ignore[return-value]


def _single_rule(step: dict) -> dict:
    rules = step["state"]["rules"]
    assert len(rules) == 1
    return rules[0]


@needs_pwsh
def test_firewall_rule_is_created_with_the_exact_tailnet_scope(
    firewall_results,
) -> None:
    step = firewall_results["first"]
    rule = _single_rule(step)

    assert step["state"]["calls"] == ["New:100.64.0.0/10"]
    assert "created" in step["message"]
    assert rule["DisplayName"] == RULE_NAME
    assert (rule["Direction"], rule["Action"], rule["Enabled"]) == (
        "Inbound",
        "Allow",
        "True",
    )
    assert rule["Profile"] == "Private"
    assert (rule["Protocol"], rule["LocalPort"]) == ("TCP", "8780")
    assert rule["RemoteAddress"] == "100.64.0.0/255.192.0.0"


@needs_pwsh
def test_firewall_rerun_updates_in_place_without_creating_a_second_rule(
    firewall_results,
) -> None:
    step = firewall_results["second"]

    assert "updated" in step["message"]
    assert "New:" not in " ".join(step["state"]["calls"])
    assert _single_rule(step)["RemoteAddress"] == "100.64.0.0/255.192.0.0"


@needs_pwsh
def test_firewall_rerun_narrows_an_overbroad_existing_rule(firewall_results) -> None:
    rule = _single_rule(firewall_results["broad"])

    assert (rule["Enabled"], rule["Profile"]) == ("True", "Private")
    assert (rule["Protocol"], rule["LocalPort"]) == ("TCP", "8780")
    assert rule["RemoteAddress"] == "100.64.0.0/255.192.0.0"


@needs_pwsh
def test_firewall_fails_when_the_scope_is_not_applied(firewall_results) -> None:
    assert (
        "does not match TCP 8780 from 100.64.0.0/10"
        in (firewall_results["unscoped"]["error"])
    )


@needs_pwsh
def test_firewall_port_follows_the_installer_port(firewall_results) -> None:
    rule = _single_rule(firewall_results["port"])

    assert rule["LocalPort"] == "18780"
    assert rule["RemoteAddress"] == "100.64.0.0/255.192.0.0"
