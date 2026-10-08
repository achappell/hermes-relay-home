# Validation — Windows installer bind and metrics-scrape durability

Date: 2026-10-08. Status: review. Source change with local tests; **not deployed, no host verification.** No Home story ID applies (deployment hardening of `deploy/windows/install.ps1` found while deploying PR #91); no story-index or sprint-status entry was added.

## Problem

On CaticornQueen the deployed state is Home listening on `0.0.0.0:8780` (`HERMES_HOME_BIND_HOST=0.0.0.0`), the local Prometheus job targeting `127.0.0.1:8780`, Tailscale Serve proxying the pairing paths to `127.0.0.1:8780`, and the inbound firewall allow `Hermes Home metrics from Tailscale` (TCP 8780, remote `100.64.0.0/10`, Private) so ops Alloy can scrape `100.78.105.19:8780` (`deploy/ops/hermes-home.alloy`). `install.ps1` reset the machine bind to its `-BindHost` parameter (default `127.0.0.1`) on every run and wrote the same value into the local Prometheus job, so a rerun would silently revert the bind and, with `-BindHost 0.0.0.0`, point Prometheus at `0.0.0.0:8780`. The ops README also told operators to pass `-BindHost 100.78.105.19`, which breaks pairing through Serve.

## Change

- `-BindHost` is optional. Omitted: the stored machine value is preserved (default `127.0.0.1` only when nothing is stored). Supplied: applied, with a warning when it changes the stored value. The plan is resolved before any state change.
- A Tailscale address (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) as the resolved bind, supplied or already stored, is rejected with the reason and the supported alternative.
- The local Prometheus job target and the installer's readiness probe use a derived host: `0.0.0.0`, `::`, `127.0.0.1` give `127.0.0.1`; any other specific address is used as given (IPv6 bracketed).
- New opt-in `-AllowTailnetMetricsScrape` creates or converges the single rule `Hermes Home metrics from Tailscale` (Inbound, Allow, TCP `-Port`, remote `100.64.0.0/10`, Private), reads it back, and fails on any mismatch. Without the switch the installer never touches the firewall. With the switch and a non-wildcard bind it warns that scraping needs `0.0.0.0`.
- `deploy/windows/README.md` and `deploy/ops/README.md` document the setup, verification, rollback, and that package-only cutovers do not touch these settings; the `-BindHost 100.78.105.19` instruction is removed.

## Behavior table

| `-BindHost` | Stored bind before | Machine bind after | Local scrape target |
| --- | --- | --- | --- |
| omitted | unset or blank | `127.0.0.1` | `127.0.0.1:<Port>` |
| omitted | `0.0.0.0` | `0.0.0.0` | `127.0.0.1:<Port>` |
| omitted | `127.0.0.1` | `127.0.0.1` | `127.0.0.1:<Port>` |
| omitted | `192.168.0.4` | `192.168.0.4` | `192.168.0.4:<Port>` |
| omitted | `::` | `::` | `127.0.0.1:<Port>` |
| omitted | `100.78.105.19` | rejected | none |
| `0.0.0.0` | any | `0.0.0.0` (warns if changed) | `127.0.0.1:<Port>` |
| `127.0.0.1` | any | `127.0.0.1` (warns if changed) | `127.0.0.1:<Port>` |
| `::1` | any | `::1` | `[::1]:<Port>` |
| `192.168.0.4` | any | `192.168.0.4` | `192.168.0.4:<Port>` |
| `100.78.105.19`, `fd7a:115c:a1e0::1` | any | rejected | none |
| blank | any | rejected | none |

| `-AllowTailnetMetricsScrape` | Firewall |
| --- | --- |
| absent | untouched (a note is printed when the bind is a wildcard) |
| present | rule created or converged to TCP `<Port>` from `100.64.0.0/10`, Private, then verified; extra warning if the bind is not a wildcard |

## Tests (observed locally)

`tests/test_windows_installer.py`:

- Static (always run): `-BindHost` has no default and is detected with `PSBoundParameters`; the machine variable is written from the resolved plan; Prometheus and the readiness probe use the derived host; the plan is resolved before the task is stopped or anything is installed; exactly one `New-NetFirewallRule`, with the exact name, remote address, profile and port and no `Any`, called only behind `$AllowTailnetMetricsScrape`; both READMEs updated and the old instruction gone; the Alloy target unchanged.
- Executed with PowerShell 7.6.6 (`pwsh`, `osx-arm64`, downloaded to `/tmp` for this session; the tests skip when `pwsh` is not on PATH): the installer parses without errors; the helper functions are extracted from the script AST and run against 20 bind cases (above, plus edge addresses `100.63.255.255`, `100.128.0.1`, `100.127.255.254`, IPv6 and hostname); `Set-TailnetMetricsFirewallRule` runs against an in-memory fake of the NetSecurity cmdlets for create, idempotent rerun without a second rule, narrowing an over-broad existing rule (Any/Any profile/other port/disabled), failing when the scope is not applied, and port convergence. A deliberate break of the scrape-host derivation made five tests fail, then was reverted.

Gates: `uv run --python 3.14 --extra dev pytest -q` — 1122 passed, 7 existing warnings (with `pwsh` on PATH; 26 of the 34 new tests skip without it, the other 8 static tests run); `uvx ruff check src tests` — all checks passed; `uvx ruff format --check src tests` — 78 files already formatted; `git diff --check` — clean. After the final lint fixes, the new tests plus the issue-tracking and observability-artifact tests were rerun: 62 passed.

## Not verified

- The installer was not executed end to end. Windows PowerShell 5.1 parsing was not repeated for this change (the tests parse with PowerShell 7). The real `NetSecurity` cmdlets (`Get-/Set-/New-NetFirewallRule`, port and address filters), including the read-back format `100.64.0.0/255.192.0.0` and filter pipeline behavior, were exercised only through a fake modeled on the 2026-10-08 readback.
- No CaticornQueen, ops, Alloy, Prometheus or firewall state was inspected or changed; no deployment. The verification and rollback commands in the READMEs are untested on the host.
- Whether the existing manually created rule on CaticornQueen is selected by `-DisplayName` (rather than only a `-Name`) is assumed from the recorded display name.
