---
title: 'Add a bounded single-device health check'
type: 'feature'
created: '2026-09-20'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e5dc94c489deecb40fc6d7accacda878972ed638'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-next-wave-planned.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md'
  - '{project-root}/docs/contracts/v1/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Hermes Home has paired endpoints, approved routes, bridge state, and safe diagnostics, but no one-device read that explains which boundary is unavailable. A generic green dot would be misleading and could encourage a client to reopen or replay an uncertain Session.

**Approach:** Add a read-only v1 health projection for one authorized endpoint. It runs only bounded, safe checks, emits one opaque correlation ID and typed per-stage outcomes with safe next actions, and reports current health separately from any existing delivery state. Device-local microphone, speaker, wake-listener, and display checks remain explicitly unsupported until a client adapter owns them.

## Boundaries & Constraints

**Always:** Check route, Home authorization, Home bridge readiness, and Standard Hermes readiness as separate stages; use stable bounded status and failure values; cap probe time and the serialized response; preserve credential, content, and precise-household-identity redaction; make the operation read-only and best-effort for diagnostics.

**Never:** Open, resume, close, or mutate a conversation; submit a prompt, capture audio, open a microphone or speaker, replay an uncertain request, change configuration or Profile mappings, expose credentials or URLs, create a fleet dashboard, or modify Android, iOS, TUI, or Standard Hermes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Verified endpoint | Caller and target are authorized; bounded probes pass | `200` with four distinct verified stages, safe overall status, one correlation ID, and no content | N/A |
| Boundary failure | One probe reports route, authorization, bridge, or Hermes failure | `200` safe projection naming the failed stage, stable reason, and next safe action; later probes follow the frozen policy | No raw exception, credential, URL, or probe payload |
| Active or uncertain delivery | Target has an active or unresolved turn | Health result includes delivery state separately and does not touch the claim, bridge, or turn | No replay, reconnect, or mutation |
| Timeout or oversized result | A probe exceeds the deadline or returns more than the response bound | Bounded unavailable result with correlation ID and safe timeout/size reason | Close or discard the probe; never serialize the oversized value |
| Unsupported device-local check | Target has no Home-owned microphone, speaker, wake-listener, or display probe | Stage is `unsupported`, never `verified`, with a safe client-side next action | No hardware access |

## Approved Decisions

- Health authority is a separate `health_view` capability, and a caller may target one endpoint within its authorized Room scope.
- Standard readiness uses a fresh bounded `gateway.ready` plus read-only `gateway.ping` connection; it creates no Hermes Session and closes after the probe.
- The request may target one endpoint within the caller's authorized Room scope; cross-Room admin health is out of scope.

</frozen-after-approval>

## Code Map

- `src/hermes_home/api/application.py` -- dispatches versioned device reads, authenticates durable device context, creates request correlation IDs, and currently implements the Watch projection; add the health boundary without reusing Watch response semantics.
- `src/hermes_home/domain/credentials.py` -- owns the allowlisted credential capabilities and scoped device authorization.
- `src/hermes_home/bridge/routes.py` -- provides bounded route selection, identity proof, typed route failures, and deadline propagation; reuse its safe descriptors rather than returning URLs.
- `src/hermes_home/bridge/standard.py` -- `StandardGatewayClient.connect()` waits for `gateway.ready`, while `ping()` exercises `gateway.ping`; `HomeBridge.open()` is conversation-bound and must not be used by health.
- `src/hermes_home/bridge/production.py` and `src/hermes_home/runtime.py` -- wire production Standard sockets, Home bridge state, credentials, and injected providers; add a non-conversation probe seam here if option 2A is approved.
- `src/hermes_home/observability/diagnostics.py` -- validates safe health phases, outcomes, correlation IDs, route descriptors, fingerprints, and failure codes; health diagnostics must remain content-free.
- `docs/contracts/v1/README.md` -- documents the versioned HTTP contract, safe response fields, and unavailable vocabulary.
- `tests/test_api_application.py`, `tests/test_bridge_routes.py`, `tests/test_standard_bridge.py`, `tests/test_diagnostics.py`, and new `tests/test_health.py` -- cover authorization, route bounds, Standard readiness, redaction, correlation, timeouts, and non-interference.

## Tasks & Acceptance

**Execution:**
- [x] `src/hermes_home/domain/health.py` -- define bounded stage/status/reason models and a provider protocol -- keep the health result deterministic and independent of HTTP or socket details.
- [x] `src/hermes_home/api/application.py` and `src/hermes_home/domain/credentials.py` -- add the authenticated device health route and approved capability/scope policy -- fail closed before any target probe.
- [x] `src/hermes_home/bridge/routes.py`, `src/hermes_home/bridge/standard.py`, `src/hermes_home/bridge/production.py`, and `src/hermes_home/runtime.py` -- reuse route bounds and add the approved non-conversation readiness seam -- never call `HomeBridge.open()` or conversation-bound `bridge.ping`.
- [x] `src/hermes_home/observability/diagnostics.py` and `docs/contracts/v1/README.md` -- record and document only bounded safe health fields -- keep correlation useful without content or credential leakage.
- [x] `tests/test_health.py` plus focused existing adapter tests -- prove each stage, failure mapping, authorization, timeout, response-size bound, redaction, unsupported hardware, and active/uncertain-turn non-interference.

**Acceptance Criteria:**
- Given a permitted caller and one target endpoint, when the health route runs, then route, Home authorization, bridge, and Standard readiness appear as distinct bounded stages with one safe correlation ID.
- Given any injected boundary failure, when the health route completes, then it identifies the failing boundary and safe next action without returning raw exception text or probe data.
- Given an active or uncertain delivery, when health runs, then the existing claim, bridge, and turn state are unchanged and delivery state is reported separately.
- Given a timeout, oversized probe result, revoked credential, stale target, or unsupported hardware check, when health runs, then it fails closed with a stable safe result and no credential, content, URL, or precise household identifier.
- Given a healthy result, when a client receives it, then it represents the bounded check just completed and never claims that an untested microphone, speaker, wake listener, display, or cached Session is healthy.

## Implementation Notes

## Spec Change Log

## Review Triage Log

- `medium` — `patch` — blind-hunter: The default production bridge result could be verified from configuration alone. Runtime wiring now supplies a live factory/listener probe, and the standalone provider defaults to unavailable without an explicit readiness seam.
- `medium` — `patch` — blind-hunter: A failed authorization result could still allow target probes. `run_health_check` now short-circuits all target and delivery probes when authorization is not verified.
- `false` — blind-hunter: The no-provider route default does not claim an external route; it records the authenticated Home HTTP handler as the local route proof, while bridge and Standard remain unavailable.
- `medium` — `patch` — blind-hunter: The initial unknown or out-of-scope target correctly remains a fail-closed 404, but a target removed or moved during the bounded check was not revalidated. The post-probe configuration read now returns a `stale_target` projection for that race.
- `maybe-false` — `defer` — blind-hunter: The daemon worker can outlive an injected provider that ignores its deadline. Production adapters pass bounded timeouts and the wrapper discards late results; a production provider that mutates or holds resources after timeout would settle the remaining risk.
- `maybe-false` — `defer` — blind-hunter: A custom route selector can continue after the outer deadline because its selector API has no cancellation hook. The built-in selector has its own bound and the outer wrapper bounds the HTTP result; a production selector that ignores that bound would settle the risk.
- `medium` — `patch` — blind-hunter: Unexpected provider or delivery exceptions such as `KeyError` could escape the safe boundary. Both paths now catch `Exception` and return allowlisted unavailable results.
- `false` — blind-hunter: Missing delivery storage means this Home instance has no Home-owned claim source to report; the default idle result is not a probe of an external conversation, and configured production storage is used when present.
- `medium` — `defer` — blind-hunter: Production delivery projection cannot currently see `HomeBridge`'s per-connection `turn_uncertain` state. That pre-existing state split is recorded in deferred work because joining it requires a bridge-state registry or durable uncertainty handoff.
- `false` — blind-hunter: The in-memory claim store is a deterministic test adapter and intentionally has no production idle timer; production SQLite delivery reads enforce the idle deadline.
- `false` — blind-hunter: Providers return typed, allowlisted results rather than raw payloads, and `bounded_health_body` caps the complete serialized document. The response-size test now uses a `HealthResult` subclass with an oversized endpoint projection.
- `medium` — `patch` — blind-hunter: The oversized fallback omitted the optional device-local stage, making the bounded response shape inconsistent. The fallback now contains all five stage names.
- `low` — `patch` — blind-hunter: A successfully completed but degraded health check was recorded as diagnostic `unavailable`. Degraded results now use the completed outcome while unavailable results retain unavailable.
- `low` — `patch` — verification-gap: Runtime auto-wiring lacked a regression assertion. The runtime test now verifies that the production `StandardHealthProbeProvider` reaches the application.
- `low` — `patch` — verification-gap: Production route, bridge, and Standard failure mapping lacked direct tests. Focused tests now cover safe route/bridge reasons, Standard timeout/protocol/transport mapping, and client closure.
- `low` — `patch` — verification-gap: The health route had no production SQLite delivery-store coverage. A health request now exercises `ConversationGrantStore.delivery_state` and asserts the active claim is unchanged.
- `low` — `patch` — verification-gap: Durable credentials had no `health_view` pairing test. The focused suite now enrolls, approves, consumes, and authenticates a paired health capability.
- `low` — `patch` — verification-gap: The new `device_health` metric route label lacked an assertion. The health API test now checks the emitted counter label.
- `medium` — `patch` — verification-gap: The stale-target race was not verified. The new test mutates configuration during the probe and asserts the stable stale projection.
- `medium` — `defer` — verification-gap: The production uncertain-delivery gap is the same pre-existing per-connection versus durable-state split recorded above; no second deferred entry is needed.
- `medium` — `patch` — edge-case-hunter: Unexpected `KeyError`, `AttributeError`, or custom exceptions could escape `_get_health`. The broad safe-boundary catches now map them without exposing exception text.
- `maybe-false` — `defer` — edge-case-hunter: The worker-cancellation concern is the same uncooperative-provider risk recorded above; current production adapters honor their supplied bounds.
- `medium` — `patch` — edge-case-hunter: Failed authorization could still invoke route, bridge, Standard, and local probes. The domain check now returns before invoking any target or delivery provider.
- `false` — edge-case-hunter: A missing route selector is an intentional local-route proof for the authenticated Home handler; external route selection is only used when a selector is injected.
- `medium` — `patch` — edge-case-hunter: Runtime previously inferred bridge readiness from `bridge_configured=True`. It now checks the configured factory and live listener through the existing bridge probe seam.
- `medium` — `patch` — edge-case-hunter: Configuration could change after target lookup. The health route now re-reads the target after probing and emits `stale_target` when the endpoint is removed or moved.
- `medium` — `defer` — edge-case-hunter: Durable delivery still cannot observe a live per-connection unresolved turn; this is the deferred state-handoff item above.
- `low` — `patch` — edge-case-hunter: `HealthResult` accepted an overall status inconsistent with its stages. Model validation now derives and enforces the overall status.
- `low` — `patch` — edge-case-hunter: `HealthDeliveryState` accepted idle-with-activity and active-without-activity combinations. Its invariants now reject both contradictory shapes.
- `low` — `patch` — edge-case-hunter: The size-bound test used a structurally compatible non-`HealthResult` fixture. It now uses a real `HealthResult` subclass, preserving the oversized projection needed by the test.

## Design Notes

The existing `bridge.ping` is deliberately excluded because it requires an already-bound conversation and can alter readiness state on failure. A separate provider keeps the health operation a read-only projection and allows deterministic fakes to prove total timeout and response-size behavior without a live Standard server.

## Verification

**Commands:**
- `uv run --extra dev --with ruff pytest -q tests/test_health.py tests/test_api_application.py tests/test_bridge_routes.py tests/test_standard_bridge.py tests/test_diagnostics.py tests/test_diagnostics_api.py` -- expected: focused health, auth, route, Standard, and diagnostics tests pass.
- `uv run --with ruff ruff check src tests` -- expected: no lint findings.
- `uv run --with ruff ruff format --check src tests` -- expected: formatting is clean.
- `uv run python -m compileall -q src && git diff --check` -- expected: compilation and whitespace checks pass.
