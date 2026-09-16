---
title: 'HOME-NW-04 — Add approved-route roaming and same-household identity proof'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_commit: '750c0e1b3278bd3a2718d95b43f9f081c2ce79f1'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/route-session-state.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Home has a static route descriptor but no reusable policy for
choosing among approved home, Tailscale, and optional public routes or for
checking that a reachable path proves the expected Household Server. A route
that is reachable but belongs to another authority must not be accepted for
future Home or Hermes work.

**Approach:** Add a framework-independent approved-route model and selector with
explicit, injected reachability and Household Identity proof ports. The selector
will validate one immutable route snapshot, try eligible routes in the
contract's fixed order, skip disabled public routes, compare each proof with an
already established expected identity, and return ordered typed attempts plus a
safe selected descriptor. This slice is a policy seam: `HomeBridge` remains the
owner of authorization, Standard session binding, and uncertain-turn recovery.

## Boundaries & Constraints

**Always:** An `ApprovedRoute` has class `home`, `tailscale`, or `public`, a
non-empty 1–128-character label, a `ws`/`wss` URL ending at the versioned Home
bridge path, and a boolean `enabled` flag. URLs contain no userinfo, query,
fragment, or credential. Selection requires a non-empty expected opaque
Household Identity and compares the injected proof value to it before accepting
a route. It tries `home`, then `tailscale`, then `public` only when the caller
explicitly enables public use; it records ordered outcomes from the contract's
five-value attempt set and continues after safe failures. Selected descriptors
contain only class/id. Credentials, URLs, proof material, Profile IDs, and
Standard Session IDs never enter descriptors or diagnostics. The ports do not
create bridges, Sessions, turns, or external state.

**Never:** Choose the final cryptographic proof, implement route discovery,
persist or refresh a production route registry, deploy TLS/reverse proxies,
add a browser ticket, alter the Standard Hermes protocol, create another
Profile/Session authority, or automatically resubmit an uncertain prompt.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|----------------------------|----------------|
| HOME_WINS | Home and Tailscale routes are reachable and prove the expected identity | Select Home and report only its class/id; do not probe lower-priority routes | None |
| TAILSCALE_FALLBACK | Home is unavailable; Tailscale proves the expected identity | Select Tailscale and retain the failed Home attempt | Typed `route_unavailable` attempt |
| PUBLIC_OPT_IN | Higher-priority routes fail; public use and the public route are enabled | Select public only after the approved higher-priority attempts | Disabled public route is never probed |
| IDENTITY_MISMATCH | A reachable route proves a different identity | Reject it and try the next approved route; no bridge/session port is called | Typed `route_identity_mismatch` attempt |
| NO_SAFE_ROUTE | Every eligible route is unavailable, unauthorized, timed out, or mismatched | Return unavailable with ordered redacted attempts and no selected route | Result reason is `route_unavailable`; attempts retain exact boundaries |
| INVALID_SETUP | Route fields, expected identity, or injected results are malformed | Reject before accepting a route or invoking later ports | Typed validation failure; no partial selection |

</frozen-after-approval>

## Code Map

- `src/hermes_home/bridge/routes.py` — new pure `ApprovedRoute`, probe/result
  protocols, typed `RouteAttempt`/`RouteSelection`, identity comparison, and
  ordered selector; keep route policy independent of WebSocket and Standard
  session code.
- `src/hermes_home/bridge/standard.py:705-900` — existing `HomeBridge` open,
  reconnect, and uncertain-turn state; reuse unchanged and do not move route
  policy into the Standard adapter.
- `src/hermes_home/bridge/__init__.py` — public bridge exports for the new
  route-policy types.
- `tests/test_bridge_routes.py` — deterministic route ordering, identity
  rejection, public opt-in, typed failures, validation, and safe descriptors;
  existing bridge tests remain regression evidence rather than implementation
  targets.
- `_bmad-output/specs/spec-home-bridge-route-roaming/{SPEC.md,bridge-contract.md,route-session-state.md}`
  — authoritative priorities, failure vocabulary, and no-replay boundary.

## Tasks & Acceptance

**Execution:**
- [x] `src/hermes_home/bridge/routes.py` — add the exact validated route model,
  injected `RouteProbe` and `HouseholdIdentityVerifier` contracts, deterministic
  sequential selection, five typed attempt outcomes, aggregate unavailable
  results, and safe class/id descriptors. Reject malformed ports and never
  invoke bridge/session work from this policy layer.
- [x] `src/hermes_home/bridge/__init__.py` and `tests/test_bridge_routes.py` —
  export the policy and cover empty/invalid route sets, duplicate IDs and
  stable ordering, public opt-in, probe exceptions/timeouts, missing or
  mismatched proof, all-failure evidence, and descriptor redaction.
- [x] `_bmad-output/specs/spec-home-bridge-route-roaming/{bridge-contract.md,route-session-state.md}`
  and `README.md` — record that the deterministic policy seam is live while
  production discovery, cryptographic proof, endpoint reconnect orchestration,
  and deployment remain deferred.
- [x] `_bmad-output/implementation-artifacts/validation-home-nw-04.md` and
  `sprint-status.yaml` — record observed focused/full tests, Ruff, formatting,
  package checks, and formal HOME-NW-04 completion status.

**Acceptance Criteria:**
- Given a validated immutable route snapshot and an expected Household Identity,
  when probes are evaluated, then the highest-priority reachable identity-valid
  route wins and disabled public routes are not attempted.
- Given two reachable approved routes, when their proof resolves to one
  expected Household Identity, then both are eligible without creating another
  authority or credential/session scope.
- Given a reachable route with a mismatched proof, when selection evaluates it,
  then Home records `route_identity_mismatch`, may try the next approved route,
  and calls no bridge/session port through the rejected route.
- Given no safe route, when selection completes, then the result is unavailable
  with ordered redacted typed attempts and makes no Hermes turn or retry.
- Given a selected route, when a future adapter consumes its result, then the
  available descriptor is limited to class/id and the existing endpoint and
  HomeBridge readiness/no-replay contracts remain unchanged.

## Implementation Notes

- Implemented the policy-only selector with validated frozen route candidates,
  explicit reachability and Household Identity ports, deterministic priority,
  bounded timeout propagation, typed failures, and endpoint-safe descriptors.
- Kept route selection separate from bridge/Hermes readiness by naming a
  successful selection `selected`; `HomeBridge`, endpoint session handling, and
  no-replay behavior remain outside this module.
- Added the missing proof, redaction, snapshot, ordering, timeout, malformed
  port, and overflow regression coverage identified during implementation
  review.

## Spec Change Log

- 2026-09-15 — Implemented the approved policy slice and clarified the
  selected-route result boundary; canonical route documentation now separates
  the live policy seam from deferred production roaming.

## Review Triage Log

### Verification-gap layer

- [patch] Ready selection serialization was not directly asserted; added an
  exact safe ready-result serialization test.
- [patch] Missing `None` Household Identity proof lacked coverage; added a
  rejection-and-fallback test.
- [patch] Source-list mutation after selector construction lacked coverage;
  added an immutable-snapshot test.
- [patch] Verifier timeout propagation lacked coverage; added a finite-bound
  assertion.
- [patch] Direct non-reachable `RouteProbeResult` values lacked coverage;
  added typed failure cases that never invoke identity verification.
- [patch] No-safe-route count and ordering lacked coverage; added an exact
  priority-ordered attempt assertion.

### Blind layer

- [patch] A `ready` selection status could be mistaken for bridge/Hermes
  readiness; changed the policy result status to `selected`.
- [patch] A mutable `RouteProbeError.outcome` could bypass identity proof;
  made the public outcome read-only and revalidated it at consumption.
- [patch] Empty `?` and `#` URL delimiters were accepted; URL validation now
  rejects both delimiters.
- [patch] Late operational failures were reported with their original outcome;
  deadline checks now classify late failures as `timed_out`.
- [patch] Synchronous ports that ignore their bound had no explicit contract;
  the port protocols now require implementations to honor the passed timeout
  rather than introducing unsafe un-cancellable workers in the policy core.
- [patch] Catch-all exception handling could hide malformed port signatures;
  only declared operational failures are normalized and incompatible calls
  become typed validation errors.
- [patch] Huge integer timeout values could leak `OverflowError`; numeric
  timeout conversion now produces `RouteValidationError`.
- [patch] Unordered route collections could make equal-priority selection
  hash-order dependent; set and frozenset inputs are rejected.
- [patch] The versioned bridge path was duplicated; endpoint validation now
  imports the canonical constant from the pure route-policy module.
- [patch] Formal validation/status records were absent at review time; the
  validation artifact, story-index link, and sprint record are now added.
- [patch] Missing-proof coverage was also reported by the blind lens; the new
  `None` proof fallback test closes that same verified gap.

### Edge-case layer

- [patch] Empty query/fragment delimiters were also reported; the shared URL
  guard rejects them.
- [patch] Incompatible port call signatures could be treated as unavailable;
  the selector now returns typed validation failure for that setup defect.
- [patch] Mutable probe-failure outcomes were also reported; the outcome is
  read-only and validated before it can affect selection.
- [patch] Late probe or verifier failures could evade timeout classification;
  operational exception paths now check the route deadline.
- [patch] A port could be invoked after its remaining time reached zero; the
  selector now records `timed_out` before calling it.
- [patch] Finite timing values could overflow deadline arithmetic; deadline
  and remaining-time calculations now validate finite results.
- [patch] Huge timeout integers could leak a raw overflow; conversion is now
  guarded by typed numeric validation.
- [patch] Huge clock integers could leak a raw overflow; clock conversion now
  uses the same finite-number guard.
- [patch] The edge lens also found missing-proof coverage; the fallback test
  now verifies that `None` cannot win.
- [patch] Formal validation/status records were absent at review time; they
  are now part of the local delivery evidence.

## Design Notes

This is deliberately a policy-only delivery. The canonical contract leaves
cryptographic proof, route discovery, TLS, deployment, and endpoint reconnect
orchestration open. The selector therefore consumes an established expected
identity and injected ports, and returns a safe descriptor for a later adapter;
it does not claim that the public endpoint already roams or that a selected
route is bridge-ready. Route readiness and turn delivery remain independent.

## Verification

**Commands:**
- `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_bridge_routes.py tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_runtime.py` — expected: focused tests pass.
- `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` — expected: full suite passes.
- `uvx --from ruff ruff check src tests` — expected: no diagnostics.
- `uvx --from ruff ruff format --check src tests` — expected: formatted.
- `uv lock --check` — expected: lockfile is current.
- `git diff --check` — expected: no whitespace errors.
