---
title: 'HOME-NW-08 Watch Safe Preview'
type: 'feature'
created: '2026-09-20'
status: 'done'
route: 'dispatch'
baseline_commit: '413ffadabc6c65c90c360d4f1b4263e9099cb0d3'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md'
  - '{project-root}/docs/contracts/v1/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-next-wave-planned.md'
  - '{project-root}/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A paired endpoint cannot see what an authorized display is doing without receiving a conversation-control channel. The existing Home claim and bridge state are live but have no bounded, redacted observation projection.

**Approach:** Add a Home-owned Watch View that reads one authorized endpoint's current task/session state through the literal `watch_view` credential capability and returns one bounded `GET` snapshot containing only an allowlisted Safe Preview, configured display-only Profile label, route/health state, and bounded task summary. Ongoing transcript/status fan-out is deferred to a later contract. It never creates, retargets, interrupts, or replays a conversation.

## Boundaries & Constraints

**Always:** Bind every read to the requested endpoint, `watch_view` scope, current credential generation, active grant, and current Home configuration. Return one bounded snapshot only; do not create a Watch subscription. Fail closed as unavailable for revoked, expired, stale, disconnected, or absent state. Use an explicit safe-field allowlist; a Profile is display label only, never an internal ID or authority. Do not persist Watch transcript, raw preview/audio, credentials, Sensitive Entry values, private notifications, or unrelated room content.

**Never:** Add remote desktop, continuous screen capture, microphone, response audio, prompt submission, choices, interrupt, Profile/configuration mutation, fallback to another endpoint/Profile, or a new Hermes authority. Watch must not reuse arbitrary bridge event payloads or incident-capture preview state.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Authorized current state | Paired endpoint has `watch_view` authority and one eligible current task | One bounded `GET` snapshot identifying only the authorized endpoint and current state | `200` with schema envelope; no secret or private content |
| No eligible state | No current task/session, or the task ends before read | No preview and no session mutation | Honest unavailable result |
| Revoked or stale state | Credential generation, endpoint, claim, or configuration no longer matches | No data from the old state | Unauthorized/unavailable; fail closed |
| Control attempt | Watch caller submits a prompt, choice, interrupt, or retarget request | No Hermes action and no claim mutation | Rejected as forbidden/not supported |

</frozen-after-approval>

## Code Map

- `src/hermes_home/api/application.py` -- existing v1 dispatch, exact-device authentication, redacted response envelopes, and device-configuration filtering; add the Watch boundary here after the contract is fixed.
- `src/hermes_home/domain/credentials.py` -- current bounded capability validation; only `wake_claim` is supported today, so observation authority must be explicit rather than inferred.
- `src/hermes_home/bridge/production.py` -- `ConversationGrantStore` validates claim, generation, revision, expiry, and activity, but currently exposes no read-only snapshot.
- `src/hermes_home/bridge/standard.py` -- `ConversationGrant`, `HomeBridge`, and safe coarse readiness/turn state; do not expose rendered text or arbitrary Standard payloads.
- `src/hermes_home/observability/diagnostics.py` -- safe-field allowlists and typed status vocabulary; incident-capture preview is a separate state machine and is not reusable as Watch state.
- `tests/test_api_application.py`, `tests/test_credentials_api.py`, `tests/test_production_bridge.py`, `tests/test_standard_bridge.py`, `tests/test_watch.py` -- authorization, revocation, lifecycle, and redaction seams for contract tests.

## Tasks & Acceptance

**Execution:**
- [x] Add the chosen observation authorization and versioned Watch request/response contract without widening conversation authority.
- [x] Add a bounded read-only current-state projection that revalidates endpoint identity, generation, claim/configuration freshness, and disconnect/revocation state.
- [x] Cover authorized, unavailable, revoked/stale, redaction, and attempted-control cases with focused tests.

**Acceptance Criteria:**
- Given an authorized paired endpoint and an eligible current task, when it requests Watch, then Home returns exactly one bounded safe view for that endpoint.
- Given no eligible task or a stale, expired, revoked, or disconnected state, when Watch is requested, then Home returns unavailable and does not fall back or mutate the live session.
- Given sensitive, credential, audio, private-notification, unrelated-room, or hidden transcript content, when a Watch response is built, then none is returned or persisted.
- Given a Watch caller attempts prompt, choice, interrupt, retarget, or configuration control, when the request is handled, then no Hermes action or conversation mutation occurs.
- Given the active conversation is being observed, when Watch disconnects or is revoked, then the conversation continues unaffected while future Watch reads fail closed.

## Implementation Notes

- `watch_view` is checked on the authenticated observer, while the provider validates the requested endpoint's active claim and preserves that target claim's credential generation internally; observer and target generations are intentionally independent.
- Watch state uses an explicit in-process connected marker cleared by bridge transport loss, revocation, close, and replacement. Production reads filter expired claim deadlines without mutating the claim or interrupting the live conversation.
- The response emits only the versioned safe-state envelope, configured Profile label, bounded route class/id, health, and content-free activity summary. Transcript/status fan-out remains deferred; no findings were deferred from review.

## Spec Change Log

## Review Triage Log

- B1 — false — The comma-separated `except` syntax is valid on the repository's Python 3.14 runtime; `py_compile`, Ruff, and the focused tests all import these modules successfully.
- B2 — medium — patch — The requester's credential generation is being compared with the observed endpoint's claim generation, so a durable watcher can fail closed when the target uses a different generation; the provider contract must validate the target claim independently.
- B3 — medium — patch — The new connection marker expires after 30 seconds without activity even when the bridge remains connected; the marker needs explicit connection ownership rather than an idle-time lease.
- B4 — false — Transport-loss handling checks gateway identity before clearing state, and the lifecycle lock serializes replacement; the cited old-transport clear does not occur at the cited path.
- B5 — medium — patch — `WatchSnapshot` accepts extra route keys and the response currently serializes the whole mapping, violating the route allowlist; emit only `class` and `id`.
- B6 — false — The in-memory test store has no deadline state from which it could report an expired claim; production expiry is owned by `ConversationGrantStore`, so this cited bad outcome is not reachable in the cited implementation.
- B7 — medium — patch — Watch coverage is static-auth-only; a durable credential with `watch_view` and a different target generation needs an end-to-end authorization test.
- B8 — medium — patch — The current out-of-scope test uses a missing endpoint, so it does not prove an existing endpoint in another Room is denied.
- B9 — medium — patch — The tests call the store's disconnect method directly and do not prove that `HomeBridge` transport loss clears the marker.
- B10 — false — The documented command omission does not create a runtime failure, and the actual focused command already runs `tests/test_watch.py`; the verification list will be corrected as an implementation-note cleanup.
- B11 — low — patch — The unavailable envelope and stable reason values are returned by code but not described in the contract, leaving clients without a documented response shape.
- V1 — medium — patch — The production SQLite Watch projection has no direct test for fresh state, target revision, deadline, and disconnect behavior.
- V2 — medium — patch — Production factory wiring and the Standard bridge disconnect callback are not exercised by the current tests.
- V3 — medium — patch — Durable credential authentication is absent from Watch tests, so the real paired-device path could regress independently of static-auth tests.
- V4 — medium — patch — Room authorization lacks a test against an existing endpoint outside the observer's Room scope.
- V5 — low — patch — Only the default `open` summary is covered; activity-specific summaries can regress without a response-level assertion.
- V6 — medium — patch — This is the same generation-binding defect as B2: observer generation is incorrectly used as the target claim generation.
- E1 — medium — patch — A Watch read calls `_expire_due_locked`, which can close an expired claim without notifying its registered bridge; the read should filter expired state without mutating the live claim.
- E2 — medium — patch — This is the same route-allowlist defect as B5; provider-supplied extra route fields must never enter the response.
- E3 — high — patch — `mark_open` runs before the final bridge binding checks, and the `turn_active`/`reconnect_required` returns do not clear the newly opened marker, so a never-bound endpoint can appear available.
- E4 — medium — patch — Replacing an existing binding closes the old gateway but does not clear the old claim's Watch marker, so the old endpoint can remain visible until another lifecycle event.
- E5 — medium — patch — `record_activity` can re-add a marker after `mark_disconnected`; a late activity callback can therefore make a disconnected claim available again.
- E6 — false — `_mark_transport_loss` rejects callbacks for a gateway that is no longer current, so the cited stale transport-loss clear is guarded.
- E7 — false — The production disconnector raises on store failure before clearing its marker, but the same store failure makes the subsequent Watch read unavailable; the cited production path does not serve the stale snapshot.
- E8 — false — The response is a consistent snapshot for the configuration revision read at request start; a concurrent publish after that read is a normal request boundary, not evidence of an incorrect fallback or mutation.
- E9 — false — Durable authentication is performed before the read, and revocation closes target claims; rotation after request authentication is the normal authorization race boundary shared by the existing API, not a Watch-specific stale read.

## Verification

**Commands:**
- `uv run --extra dev --with ruff pytest -q tests/test_watch.py tests/test_api_application.py tests/test_credentials_api.py tests/test_credentials.py tests/test_production_bridge.py tests/test_standard_bridge.py tests/test_runtime.py tests/test_configuration_validation.py` -- expected: focused Home tests pass.
- `uv run --with ruff ruff check src tests` -- expected: no lint findings.
- `uv run --with ruff ruff format --check src tests` -- expected: formatting is clean.
