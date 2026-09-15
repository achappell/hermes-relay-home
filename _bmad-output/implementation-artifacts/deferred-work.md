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
