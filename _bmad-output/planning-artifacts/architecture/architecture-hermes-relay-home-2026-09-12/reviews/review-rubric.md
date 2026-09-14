# Reviewer Gate — Rubric

Date: 2026-09-13

## Verdict

PASS. The Home spine now covers the foundation and the next-wave Home-owned
boundaries at feature altitude. Slice C and Slice D now have formal load-bearing
specifications; their implementation details remain deferred rather than being
left for independent clients to invent.

## Coverage

- AD-1 through AD-7 preserve the existing Home authority, contract, revision,
  credential, arbitration, dependency, and enrollment decisions.
- AD-8 covers all six Profile-mapping and conversation-claim capabilities:
  household-wide phrase meaning, aliasing, snapshot grants, Room-local
  arbitration, fixed active binding, independent Sessions, freshness, closure,
  and the 8-second initial idle default.
- AD-9 makes the supported Hermes session boundary the bridge boundary and
  keeps endpoint capture, route changes, and uncertain-turn recovery honest.
- AD-10 through AD-11 cover per-device permissions, sensitive replacement,
  choices/artifact authorization, Watch, health, and notifications without
  turning Home into an assistant or transcript store.
- AD-12 makes Household Diagnostics a distinct content-safe path with one
  correlation ID, explicit incident capture, the 60-second ring buffer, and
  30-day/14-day/7-day retention targets.
- AD-13 keeps choices and artifacts explicit, revision/freshness-bound, and
  separate from arbitrary file transport.
- The operational envelope names bridge behavior, mapping refresh, route
  identity, and best-effort diagnostics. The capability map points to owners
  and decisions rather than duplicating acceptance criteria.
- Every AD has Binds, Prevents, and an enforceable Rule; deferred decisions
  include the concrete stores, cryptographic primitives, delivery mechanics,
  health probe vocabulary, and native choice details that can wait for their
  owning specifications.

## Evidence

- `lint_spine.py`: zero findings.
- The Standard migration baseline pins the stock release and separates the
  `/api/ws` JSON-RPC gateway from the `/api/audio/speak-stream` response-audio
  sidecar; the fork route remains rollback-only.
- Home architecture memlog records the adopted Slice B, bridge, grant,
  observation, health, diagnostics, retention, and choice boundaries.
- No source-code files were changed by this pass; existing implementation
  work and generated artifacts remain outside this architecture change.

## Follow-up

The formal Slice C and Slice D specifications are now written and linked. Their
next gate is implementation planning and story decomposition, including the
remaining identity-proof, bridge-envelope, diagnostics-store, and delivery
mechanics choices. That is an intentional delivery gate, not a missing Home
ownership decision.
