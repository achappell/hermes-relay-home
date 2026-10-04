---
title: 'HOME-NW-06 connection failure diagnostics supplement'
type: 'feature'
created: '2026-10-04'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '144466aa8a1daaed37eb59f7da8bfabfdd4f3897'
context:
  - '_bmad-output/specs/spec-connection-failure-diagnostics/SPEC.md'
  - '_bmad-output/specs/spec-connection-failure-diagnostics/event-contract.md'
  - '_bmad-output/specs/spec-connection-failure-diagnostics/implementation-acceptance.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Existing connection-failure evidence cannot reliably join requests, local writes, transport closures, and generating builds.

**Approach:** Implement the approved Home-owned service/proxy diagnostics and server-first negotiated correlation/schema-2 report support from the three referenced intent documents, preserving their exact field, privacy, compatibility, and acceptance contracts. This supplements HOME-NW-06/issue 16; it does not close that story's broader acceptance or promote Epic 6.

## Boundaries & Constraints

**Always:** Home owns correlation/redaction. Use content-free random IDs, explicit unknown/loss states, nonblocking bounded diagnostics, unchanged authorization and report consent, 14-day events and separate seven-day reports/associations. Legacy socket/report shapes remain unchanged without opt-in. A returned write is not receipt; ready is not recovery. Preserve Standard interfaces and existing conversation behavior.

**Never:** iOS edits, production deployment, Standard changes, retries/replay/probes, timeout tuning, uncertainty changes, content capture, private identifiers in operational records, raw exception/reason logging, new public review routes, or whole-epic closure. iOS/device and real-Standard acceptance remain explicitly pending.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Legacy | No exact negotiation header | Original ready/result/error and schema-1 semantics | Missing joins unavailable |
| Correlated submit | Opted-in socket and valid request token | Home socket ID before send; one correlation through refusal/upstream/write | Invalid metadata count-only; duplicate ambiguous; cap unavailable |
| Delayed upload | Schema 2 after close/restart | Exact authenticated Device/socket/token lookup; generating origins retained | Missing/expired/corrupt unavailable; cross-device never joined |
| Close/recovery | Either close order, cancellation, next explicit request | Numeric frame/status distinction, pending state, typed write/terminal outcomes | Unknown cause stays unknown; no replay |
| Diagnostics failure | Full queue, disk/formatter/store failure, crash | Live turn unaffected; bounded loss visible | No raw fallback; incomplete last line is a gap |

</frozen-after-approval>

## Code Map

- `src/hermes_home/observability/diagnostics.py`: strict `DiagnosticEvent`, `DiagnosticsRecorder`, status, correlation authority; `record()` currently commits synchronously. Preserve direct APIs and incident capture; share imported operational validators.
- `src/hermes_home/storage/diagnostics.py`: bounded SQLite event/expiry transactions; extend versioned decoding and protected association storage without replacing existing events.
- `src/hermes_home/bridge/{endpoint,standard,production}.py`, `api/bridge_server.py`: request gates, deferred/actual writes, upstream socket factory, authentication and socket lifecycle.
- `src/hermes_home/observability/client_reports.py`, `api/{application,pairing_page}.py`: strict schema-1 intake, Device identity, latest-50 signed-in textContent viewer; preserve rate/size/auth bounds.
- `src/hermes_home/runtime.py`: resource ownership and production dispatch; `pyproject.toml`: package discovery.
- `deploy/ops/hermes-standard-home-pilot-proxy.py`: standalone byte relay using agent interpreter; shell wrappers and Windows runners currently append logs without bounds.

## Tasks & Acceptance

**Execution:**
- [ ] `src/hermes_home_diagnostics.py`, `pyproject.toml` — one stdlib-only operational projection/sink, packaged as py-module and reusable beside proxy; UTC/sequence/provenance, typed warning/close adapter, 2KiB records, 1024 queue, 10MiB plus four backups, expiry, counters, bounded drain.
- [ ] `observability/diagnostics.py`, `storage/diagnostics.py`, `runtime.py` under `src/hermes_home/` — version extended safe events; runtime nonblocking dispatch wrapping existing recorder APIs and protected associations; keep disk work off transport paths and expose loss status.
- [ ] `src/hermes_home/bridge/{endpoint,standard,production}.py`, `src/hermes_home/api/bridge_server.py` — allocate correlation before refusal, negotiate metadata, instrument actual submission/write/close/recovery, track bounded tokens and pending states, migrate socket factories/fakes; proxy header only on explicitly configured proxy hop.
- [ ] `src/hermes_home/observability/client_reports.py`, `src/hermes_home/api/{application,pairing_page}.py` — strict schema 2/origins alongside schema 1, conflicting-event rejection, bounded Device-scoped association lookup and honest schema-aware review.
- [ ] `deploy/ops/hermes-standard-home-pilot-proxy.py`, existing ops shell/plist and Windows scripts — paired legs/link ambiguity, safe logging, canonical helper deployment, one rotating owner; no agent environment changes.
- [ ] Existing `tests/test_{diagnostics,diagnostics_store,diagnostics_api,client_reports,bridge_endpoint,standard_bridge,bridge_server,runtime,observability_artifacts,windows_deployment}.py` and focused behavioral integration tests — cover matrix, real loopback/proxy close orders, canaries, load/failure/retention and mixed versions. Delete source/wording-pinning tests rather than repinning them.
- [ ] Existing ops/Windows/observability READMEs and local validation artifact — safe retrieval/permissions/copy recipe, exact exercised provenance, workspace base and pending external acceptance; preserve unrelated trackers/evidence.

**Acceptance Criteria:**
- Given actual Home/proxy fixtures, when acceptance A1–A10's Home-owned cases run, then safe IDs reconstruct request, write and both transport legs without asserting client receipt or agent interior facts.
- Given 100,000 lifecycle records and fixed 1,000-submit logging-on/off harness, when queues/rotation/retention/failures are exercised, then contract bounds hold, no disk wait reaches transport, and measured p95 added latency is at most 5ms.
- Given legacy clients/reports and malicious cross-device hints, when upgraded Home handles them, then existing outcomes/auth remain unchanged and only exact scoped associations link.
- Given Home-only completion, when evidence is recorded, then iOS receive/resolve, device acceptance, live Standard and A11 remain pending rather than fabricated passes.

## Implementation Notes

Approval provenance: the user's “do the home changes now” authorizes this unchanged Home-owned scope; parent explicitly directed approval/continue and retention of all planned Home tasks. No iOS work or deployment is authorized. Planning measured 1,842 tokens (cl100k_base); the retained cohesive cross-layer spec exceeds the 1,600-token recommendation and requires careful context discipline. This records actual request and parent routing, not additional user words.

## Spec Change Log

## Review Triage Log

## Design Notes

Dispatch footprint includes additive SQLite tables and local retention mutation; deployment wrapper/installers/docs are updated only to ship the standalone helper and configure local log roots, with no production deployment. No unresolved intent gaps for the approved Home subset.

Core owns `src/**`, the deploy proxy runtime, and the real-socket/privacy integration tests. Fast worker owns only deployment wrappers/installers/docs and removes affected source-pinning assertions from `tests/test_observability_artifacts.py` and `tests/test_windows_deployment.py`. Canonical `hermes_home_diagnostics` is stdlib-only and copied beside proxy at the same revision; no full Home dependency or new agent environment. Home imports its allowlists/projection rather than duplicating policy. Runtime dispatch preserves direct recorder behavior/tests while production records and association writes are queued; diagnostic reads do not control transport.

Log roots: Home defaults to `<data_dir>/logs`, overridable with `HERMES_HOME_DIAGNOSTICS_DIR`; pilot proxy defaults to `<pilot_dir>/logs`, overridable with `HERMES_HOME_PROXY_LOG_DIR`. No log payload contains link metadata or raw transport exception formatting.

Shared API: `OperationalDiagnostics(component=, directory=, source_files=(), app_version=None, source_revision=None, websocket_version=None, settings=None)`; `emit(event, **fields)->bool`, `status()->dict`, `close(timeout=0.1)`; `new_connection_id()`, `exception_fields(error)`, `close_fields(connection=None, error=None)`, `SafeTransportLogHandler(diagnostics, leg=, connection_id=None)`. Constructors/sinks fail open; no raw formatter. Loaded source digest is distinct from an unverified configured revision. Proxy never forwards link metadata to Standard.

Workspace: `.worktrees/home-connection-diagnostics`, branch `feat/home-nw-06-connection-diagnostics`, base `144466aa8a1daaed37eb59f7da8bfabfdd4f3897`. Only four authored intent files were copied from the main checkout. Preexisting NW-18 validation and other worktrees remain untouched.

## Verification

Parent/integration owner runs once after all edits: supported Python 3.14 focused tests above, `uv run pytest`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, and explicit real-socket/load/privacy harnesses. Production deployment and iOS tests are not authorized. Record only observed results and exact pending boundaries.

Local observed results and pending acceptance are recorded in
`_bmad-output/implementation-artifacts/validation-home-nw-06-connection-failure-diagnostics.md`.
The supplement is in review; this does not close HOME-NW-06's broader acceptance
or promote Epic 6.
