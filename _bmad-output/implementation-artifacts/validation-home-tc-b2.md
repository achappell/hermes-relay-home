---
story: HOME-TC-B2 (placeholder; owner assigns an ID)
status: passed
validated: 2026-10-11
baseline_commit: '0b14f3de72af98d8a6bfd5f428e0367493fa456a'
---

# HOME-TC-B2 validation record

## Verification boundary

The Home bridge and endpoint were exercised against captured Standard Hermes
0.21.1 (`2237be3`) frames and local test doubles. No live Home, media server,
production configuration, service, Pixel, hardware, or Tailscale access was used.
No production configuration or device was changed. B3 live proof is **NOT
DONE**.

## Observed checks

| Check | Command | Result |
| --- | --- | --- |
| B2 regression flake gate | Five runs of `uv run pytest -q tests/test_clarify_choice_projection.py tests/test_clarify_choice_endpoint.py` | Five consecutive passes; 109 passed each (545 test executions) |
| Full Home suite | `uv run pytest` | 1322 passed, 26 skipped, 7 warnings in 32.64s |
| Ruff lint | `uv run --with ruff ruff check src tests` | All checks passed |
| Ruff format | `uv run --with ruff ruff format --check src tests` | 82 files already formatted |
| Wheel | `uv run --with build python -m build --wheel` | Built `hermes_relay_home-0.2.0-py3-none-any.whl` |
| Whitespace | `git diff --check` and `git diff --cached --check` | Passed; no output |

The seven full-suite warnings are WebSocket API deprecations; the suite passed.
At initial PR #106 read, GitHub's `statusCheckRollup` was empty; no CI checks
were reported.

## Review

Five review layers returned 14 findings: four were patched, ten were rejected
with evidence in the B2 spec's Review Triage Log, and none were deferred. An
additional boundary check ensures projected `clarify.expire` events preserve
their correlation but do not expose Standard's raw request ID in the public
event payload.

## Deployment and rollback boundary

Merge is **not** deployment. Do not deploy before **2026-10-12 17:08 CDT** or
before the B3 live proof and safety checks are complete. Before any opt-in, B3-7
must verify no Device ID overlap between `devices[]` and `client_devices`, no
Room Device has `interactive_choice: true`, and the media-server profile's
`agent.clarify_timeout` (or legacy `clarify.timeout`) is unset or at most 3600
seconds. Those live checks remain outstanding.

Before rolling back the Home build, publish configuration without
`client_devices`; the older build rejects that key and returns 503. The
trailing `(Recommended)` display-label stripping and fixed 3600-second
projected-choice TTL remain provisional and owner-correctable. No production
configuration or device was touched.
