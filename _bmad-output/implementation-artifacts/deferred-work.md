## Deferred from: code review of SPEC.md (2026-09-14)

- Decide whether Home consumes Standard event sequence/cursor replay after reconnect. The pinned Standard release supports `session.events.since` and monotonic event sequence numbers, while Story 2 forbids replaying uncertain prompts and old responses; the route-level contract must settle whether missed non-response events are intentionally discarded or replayed.

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md`
  summary: Define and implement an explicit enrollment-cancellation transition and control-plane route.
  evidence: The canonical lifecycle names `cancelled`, but the approved NW-02 route table and task list do not include a cancellation operation.

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md`
  summary: Define how approved enrollment binds a newly generated Home device identity to a canonical configuration Device.
  evidence: NW-02 issues the credential and enforces scope against the existing configuration authority, while the request lacks the profile and priority fields needed to create a Device record.

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md`
  summary: Specify the entropy or actor-binding requirements for endpoint rotation request IDs.
  evidence: The retry contract accepts a client-generated request ID and an old credential during overlap; a guessable ID could let that credential recover the replacement, but the approved spec does not make request IDs secret or prescribe their generation.

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md`
  summary: Exercise the durable authenticator through the Standard bridge consumer.
  evidence: The persistent adapter now preserves the existing ID-returning seam, but bridge integration is owned by the later NW-03 route/bridge slice.

## Deferred from: code review of spec-home-nw-06-diagnostics-incident-review.md (2026-09-15)

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-06-diagnostics-incident-review.md`
  summary: Expose `IncidentCaptureService` through the Home runtime/API.
  evidence: The endpoint-native evidence contract and trusted-surface role model are open decisions in the canonical diagnostics specification; no Home capture route is defined in this slice.

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-06-diagnostics-incident-review.md`
  summary: Define whether storage failures and duplicate IDs belong in `rejected_event_count` or a separate loss/availability counter.
  evidence: The current diagnostics status vocabulary does not distinguish these cases, so changing the counter without a contract would make status interpretation ambiguous.

## Deferred from: code review of spec-home-nw-06-diagnostics-incident-review.md (2026-09-16)

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-06-diagnostics-incident-review.md`
  summary: Make the runtime own automatic capture-service wiring and reaper construction.
  evidence: The endpoint-native evidence adapter and trusted-surface role model remain open decisions already recorded under W1.

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-06-diagnostics-incident-review.md`
  summary: Define remote bundle lifecycle semantics when no lifecycle adapter is injected.
  evidence: D6 deliberately leaves the final remote bundle backend injected.

- source_spec: `_bmad-output/implementation-artifacts/spec-home-nw-06-diagnostics-incident-review.md`
  summary: Add a second durable upload receipt/transaction protocol spanning the remote bundle store and local SQLite.
  evidence: D9 selects stable idempotency keys and acknowledgements until the final backend contract exists.
- source_spec: `_bmad-output/implementation-artifacts/spec-pilot-session-persist-and-runner-log.md`
  summary: Distinguish a Standard `session.resume` "not found" rejection for a never-stored Session from other rejections, so a stale grant session ID can heal instead of requiring manual cleanup.
  evidence: Maybe-false until Standard exposes a distinguishing error code; today a reaped empty Session and a genuine rejection both surface as GatewayRPCError -> request_rejected.

## Deferred from: code review of spec-home-nw-07-freshness-bound-typed-choice-authority (2026-09-17)

- Confirm whether Standard guarantees correlation IDs are globally unique across all structured prompt types. Home currently routes a response to the choice path when any retained choice correlation matches, but the local contract does not promise global uniqueness; if Standard can reuse an ID for a different prompt type, that prompt's valid response is diverted. Evidence to settle it: the pinned Standard protocol contract or a producer implementation guarantee.
- Audit how endpoint clients render bidirectional and formatting controls in choice text and labels. Home accepts bounded Unicode strings containing these controls; they may visually reorder or spoof options if the client does not neutralize them. Evidence to settle it: a client rendering audit that preserves legitimate right-to-left text.
