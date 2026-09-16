---
story: HOME-NW-06
status: passed
validated: 2026-09-16
baseline_commit: fd16784d264615b6df11e02fe117871cc69a2041
---

# HOME-NW-06 validation

Validated in the isolated `feat/home-nw-06-diagnostics` worktree on Python
3.14. The worktree started from the merged HOME-NW-03 deployment-wiring
mainline.

## Checks

| Check | Command | Result |
| --- | --- | --- |
| Focused diagnostics/integration suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_diagnostics.py tests/test_diagnostics_store.py tests/test_diagnostics_api.py tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_runtime.py tests/test_observability_artifacts.py` | `94 passed in 1.11s` |
| Full Home suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` | `264 passed in 3.04s` |
| Ruff lint | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff check src tests` | `All checks passed!` |
| Ruff format | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff format --check src tests` | `44 files already formatted` |
| Lockfile | `uv lock --check` | `Resolved 11 packages` |
| Whitespace/diff | `git diff --check` | Passed |
| Package build | `uv run --no-cache --no-project --python 3.14 --with build -- python -m build --sdist --wheel` | Successfully built the sdist and wheel |

## Boundary evidence

- `DiagnosticEvent` accepts an explicit safe-field allowlist only. Prompts,
  transcripts, raw audio, credentials, keys, Sensitive Entry values, private
  notification content, and reversible identifiers are rejected or omitted;
  endpoint, session, turn, and custom route values used in events are opaque
  fingerprints or Home-approved safe identities, and failure codes are typed.
- `SQLiteDiagnosticsStore` durably retains the safe event timeline across a
  Home restart, honors per-event retention deadlines, purges before reads and
  writes, bounds the local queue, and keeps upload/rejection/loss state visible.
  Prometheus metrics remain low-cardinality and carry no content; ring eviction,
  expiry, and stale out-of-order drops are visible in status and metrics.
- Home exposes authenticated status and admin-only correlation timelines. The
  diagnostics status and metrics reads do not record themselves, so a review
  read cannot mutate the state it reports.
- The bridge records one correlation across endpoint, Home, and Hermes turn
  facts. Audio records include lifecycle and PCM byte-count facts only; raw PCM
  remains a bridge payload and never enters the automatic event store. Timeout,
  failed, interrupted, and unavailable fixtures preserve typed outcomes without
  turn retries.
- The explicit capture service requires an injected authorizer and current-task
  resolver, previews stable selectable evidence descriptors from the bounded
  local ring before approval, seals and uploads through separate injected
  ports, exposes a seven-day deadline, and audits preserve/delete and expiry
  transitions. Capture metadata and audit history persist in bounded SQLite
  tables; untouched captures can be reaped through the runtime scheduler.
  Automatic telemetry never feeds this bundle path.
- Recorder upload claims a bounded batch under its lock, performs collector I/O
  outside the lock, and finalizes only after an exact acknowledgement carrying
  the stable idempotency key and event IDs. A failed finalization can therefore
  retry the same batch without changing the live turn path.

## Limits

The final structured-event collector, encrypted incident-bundle store,
cryptographic primitive, trusted-surface role model, endpoint-native detailed
evidence/ring adapter, production runtime capture route, and production upload
retry policy remain deferred by the canonical specification. The runtime
therefore owns a durable local queue and an injected collector boundary; it does
not claim a live remote collector, live capture deployment, or live
Hermes/physical-endpoint validation.

## Follow-up review validation

The follow-up BMAD review was run on 2026-09-16 against the narrowed core
diagnostics/storage implementation group. The focused core suite was rerun
after the fixes and the new negative, restart, bounds, lifecycle, clock, and
rendered metrics checks.

| Check | Command | Result |
| --- | --- | --- |
| Focused core suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_diagnostics.py tests/test_diagnostics_store.py tests/test_metrics.py` | `55 passed in 0.16s` |
| Ruff lint | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff check src tests` | `All checks passed!` |
| Ruff format | `uv run --no-cache --no-project --python 3.14 --with ruff -- ruff format --check src tests` | `46 files already formatted` |
| Whitespace/diff | `git diff --check` | Passed |

The full suite was attempted but collection remains blocked by the preserved,
pre-existing `src/hermes_home/bridge/__init__.py` edit importing
`hermes_home.bridge.routes`, while that module is absent from this NW-06
worktree. This is an unrelated HOME-NW-04 worktree change; no route module was
copied into the diagnostics branch. API, bridge, and runtime tests therefore
remain unverified in this worktree.
