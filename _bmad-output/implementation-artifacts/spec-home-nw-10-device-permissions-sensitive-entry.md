---
title: 'HOME-NW-10 Per-device permissions and masked Sensitive Entry'
type: 'feature'
created: '2026-09-20'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '0484f3a51e4d3806ccd8b2f244e4e01ce9db5be6'
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/docs/contracts/v1/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-next-wave-planned.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Home has explicit per-device authority for wake claims, Watch, health, and typed choices, but no Home-owned policy for protected input or consequence-bearing actions. Existing Standard structured prompts can reach a bound endpoint without a dedicated Sensitive Entry boundary, so a shared display and an approved phone/TUI cannot be distinguished safely for this work.

**Approach:** Extend Home's device and credential capability model, conversation grant, and bridge prompt projection so protected input and consequence-bearing actions are checked against the current endpoint, credential generation, configuration revision, active claim, and prompt correlation. Keep Sensitive Entry values out of ordinary endpoint payloads, transcript/history paths, Watch, and automatic diagnostics while preserving the existing Standard session authority. This slice covers the existing structured-prompt boundary only; it does not add configuration read/propose/apply or shared-artifact operations.

## Boundaries & Constraints

**Always:** Permissions are explicit per endpoint and omitted capabilities deny access. For a protected request, effective authority is the intersection of the current authenticated credential scope and the current configured capabilities of that same endpoint; both must grant the requested category. Every protected request is revalidated at use time; stale, expired, revoked, malformed, or out-of-scope requests fail closed. Sensitive values are one-shot protected data, limited to 4,096 UTF-8 bytes: never log, persist, echo as ordinary transcript, expose to Watch, or include in automatic diagnostics. A capability-only configuration change must deny later protected requests or make their prompt stale without closing, retargeting, or replaying the active Standard conversation. Risky actions require an explicit, current confirmation boundary.

**Never:** Add a new Hermes authority or bearer-token path; infer permission from discovery, Room membership, route, Profile, or a broad unrelated capability; persist a secret for retry; fall back to `prompt.submit` or an ordinary event path for protected values; implement configuration read/propose/apply or shared-artifact mutation from NW-12; change iOS, Android, TUI, or Hermes code in this Home slice.

**Boundary dependency:** The existing Standard `secret.respond` and `sudo.respond` operations must treat submitted values as non-transcript and non-history input. Home uses only those existing operations and cannot enforce Hermes storage internals; if the Standard peer cannot provide that privacy guarantee, Home returns a safe unavailable result and does not claim the protected path is complete.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Authorized protected input | Current paired endpoint and credential both grant `sensitive_entry`; one matching `secret.request` or `sudo.request` is current under the active claim and configuration revision | Deliver only an allowlisted masked/safe prompt projection; accept one current replacement and forward it through the existing Standard response operation | No value in Home response, diagnostics, Watch, or ordinary transcript |
| Missing or stale authority | Either capability is omitted, credential generation/configuration revision changed, claim revoked, or prompt expired/replaced | No Standard request and no action; leave the active conversation identity and turn unchanged | Typed unavailable/forbidden result with a safe reason |
| Consequence-bearing action | Current `approval.request` requires confirmation and the endpoint and credential both grant `consequence_confirm` | Commit only after the approved endpoint presents the required explicit confirmation through the current correlated approval flow | Denial, expiry, mismatch, or replay produces no commit |
| Malformed or oversized protected data | Unknown fields, wrong types, invalid correlation, or value beyond 4,096 UTF-8 bytes | No forwarding or persistence | Typed invalid/protocol failure; no raw input in logs or error text |

## Resolved Decisions

- Both `secret.request` and `sudo.request` are Sensitive Entry. A `sudo.request` password is protected input, but `sensitive_entry` never authorizes the consequence-bearing action by itself.
- NW-10 publishes separate per-device capabilities in both the credential scope and device configuration: `sensitive_entry` and `consequence_confirm`. Omission from either side denies its category; Home carries only the effective intersection into the conversation grant.
- Every `approval.request` is treated as consequence-bearing for this slice. Home never infers risk from arbitrary prompt text or an untrusted payload field, so each approval response requires `consequence_confirm`.
- Protected values are memory-only and receive one forward attempt. After transport uncertainty, Home discards the value and requires a fresh prompt; it does not retain encrypted retry material.
- Consequence confirmation reuses the current correlated `approval.request`/`approval.respond` operation and is gated by Home against the endpoint, claim, generation, effective capability revision, and prompt freshness. NW-10 adds no Home-issued confirmation receipt or API.
- Protected prompt events use an explicit allowlist of bounded non-secret metadata; unknown payload fields are dropped. Protected response results use a fixed safe envelope containing status and operation only, and protected failures use safe codes rather than peer error text.
- Home enforces a 4,096-byte UTF-8 input bound before forwarding. It keeps no protected value at rest and does not retry after known or uncertain delivery.
- Configuration read/propose/apply and shared-artifact mutation remain deferred to their own stories; NW-10 delivers only endpoint-scoped structured prompt entry and confirmation.

</frozen-after-approval>

## Code Map

- `src/hermes_home/domain/credentials.py` -- `SUPPORTED_CREDENTIAL_CAPABILITIES`, `CredentialScope`, and enrollment approval; add only the approved bounded categories and preserve subset/revocation behavior.
- `src/hermes_home/domain/configuration.py` and `docs/contracts/v1/configuration.schema.json` -- `_validate_devices` and the versioned Device capability shape; omission must remain deny-by-default and revision publication must stay atomic.
- `src/hermes_home/api/application.py` -- current device authentication, durable credential scope, configuration revision, diagnostics, and redacted HTTP error boundaries; expose no artifact read/propose/apply route for this slice.
- `src/hermes_home/bridge/production.py` -- `ConversationGrantStore.resolve` and `ConversationGrant`; resolve the current credential/configuration intersection and effective capability revision without invalidating an active Standard session solely because a permission flag changed.
- `src/hermes_home/bridge/standard.py` -- `_STRUCTURED_PROMPT_OPERATIONS`, `_revalidate_ready_binding`, `HomeBridge._respond_prompt`, and `_endpoint_safe_payload`; reuse `secret.respond`/`sudo.respond`/`approval.respond` only after Home authorization, and use allowlisted protected projections/results.
- `src/hermes_home/bridge/endpoint.py` and `src/hermes_home/observability/diagnostics.py` -- `_validate_prompt_response`, `_result_payload`, pending prompt correlation, safe error mapping, and content-free diagnostics; protect response values before they reach any generic boundary.
- `docs/contracts/v1/README.md` and the existing Standard bridge contract -- document the capability intersection, 4,096-byte bound, no-transcript prerequisite, and safe protected-operation envelope.
- `tests/test_credentials*.py`, `tests/test_configuration*.py`, `tests/test_standard_bridge.py`, `tests/test_bridge_endpoint.py`, and `tests/test_production_bridge.py` -- existing scope, prompt, claim, revocation, active-session, and redaction seams for focused coverage.

## Tasks & Acceptance

**Execution:**
- [x] `domain/credentials.py`, `domain/configuration.py`, and the v1 schema/docs -- implement `sensitive_entry` and `consequence_confirm` in both bounded capability surfaces with atomic revision semantics -- preserve deny-by-default behavior.
- [x] `bridge/production.py` -- resolve the current credential/configuration intersection and effective capability revision; reject stale protected prompts without closing, retargeting, or replaying the active Standard conversation.
- [x] `bridge/standard.py`, `bridge/endpoint.py`, and `observability/diagnostics.py` -- enforce current claim/generation/revision/prompt authority, allowlisted masked projections, fixed safe results, the 4,096-byte bound, one-shot delivery, and safe failure codes.
- [x] `docs/contracts/v1/README.md` and the existing Standard bridge contract -- record the no-transcript/no-history dependency for `secret.respond` and `sudo.respond`; do not add a Hermes or client implementation here.
- [x] Focused credential, configuration, Standard, endpoint, production, and diagnostics tests -- cover authorized, omitted on either side, stale, revoked, expired, malformed, oversized, replayed, active-session-preservation, confirmation, upstream rejection, and redaction cases -- prove no protected value is persisted or emitted.

**Acceptance Criteria:**
- Given a paired endpoint without the approved protected capability, when it receives or answers a protected prompt, then Home performs no Standard response and returns a safe unavailable result.
- Given a current authorized `secret.request` or `sudo.request`, when the approved endpoint submits one valid protected value, then Home forwards it only through the selected existing response operation and exposes no value in ordinary output, diagnostics, Watch, or the protected response result.
- Given a stale, revoked, expired, replaced, or replayed claim/prompt, when a protected response arrives, then no action occurs, the active conversation identity and turn are unchanged, and no response is retried or retargeted.
- Given a capability-only configuration change during an active conversation, when the endpoint answers a protected prompt, then Home rechecks the current intersection, rejects a no-longer-authorized or stale prompt safely, and leaves the Standard conversation bound without replay.
- Given any `approval.request`, when confirmation is missing, denied, expired, from the wrong endpoint, or lacks the effective `consequence_confirm` capability, then no commit reaches Standard.
- Given malformed or oversized protected input, when Home validates it, then it fails closed before forwarding or persistence without logging or returning the raw value.
- Given a Standard peer that rejects or cannot guarantee the privacy-preserving protected operation, when Home attempts the response, then Home returns a safe unavailable/rejected result and never falls back to ordinary text submission or exposes peer error text.

## Implementation Notes

- Added `sensitive_entry` and `consequence_confirm` to credential scope and Device configuration with omission-as-deny normalization. Production grants now carry only the intersection of the current credential generation and current Device flags, plus the live capability revision; configuration changes do not close the active claim.
- Existing Standard structured prompt operations remain the only Hermes path. `approval.request` requires `consequence_confirm`; `secret.request` and `sudo.request` require `sensitive_entry`. Protected prompt metadata is allowlisted before endpoint projection, response values are bounded at 4,096 UTF-8 bytes, results are fixed safe envelopes, and pending protected responses are consumed before transport delivery to prevent retry.
- The endpoint and Standard bridge use existing content-free diagnostic/error boundaries. No protected value is written to Home storage or included in ordinary event/result projections; the contract documents the upstream Standard transcript/history privacy prerequisite.
- Review follow-up clears a protected pending prompt when Standard presents a
  mismatched replacement, supersedes older protected endpoint entries, rejects
  wrong-typed protected metadata, validates protected bridge resolution before
  returning the fixed envelope, and covers both known and uncertain one-shot
  delivery. The NW-02 and next-wave planning notes now match this approved
  boundary.
- Focused suite: `338 passed`. Full suite: `558 passed`.

## Spec Change Log

- 2026-09-20 — Review follow-up: implementation scope remains frozen; clarified the protected replacement, metadata typing, fixed-result, and upstream privacy boundaries and refreshed verification counts.

## Review Triage Log

- B1 — Verdict: false. Home cannot inspect or negotiate Hermes storage internals in this slice; the boundary dependency documents the upstream non-transcript/non-history prerequisite and Home never claims to enforce it locally. Route: documentation prerequisite, no Home code change.
- B2 — Verdict: false. Protected failures already use the existing safe JSON-RPC error mapper, while protected successes use a fixed envelope and hide peer error text; the approved vocabulary permits safe rejected/unavailable outcomes. Route: retain existing error boundary.
- B3 — Verdict: patch. `sensitivity` is now an explicit allowlisted protected metadata field and remains bounded before endpoint projection. Route: `bridge/standard.py` and projection regression test.
- B4 — Verdict: false. Local prompt timers are not part of the approved Standard boundary; existing Standard expiry events are the authority, and protected expiry events now use the same safe projection. Route: retain upstream expiry contract.
- B5 — Verdict: patch. A mismatched protected Standard prompt now clears the older protected pending identity before discarding the replacement, so the old correlation cannot be answered. Route: `bridge/standard.py` and replacement regression test.
- B6 — Verdict: patch. `approval.expire`, `secret.expire`, and `sudo.expire` now use the protected allowlist instead of the generic sanitizer, preventing value/password leakage. Route: `bridge/standard.py` and endpoint expiry test.
- B7 — Verdict: false. Runtime wiring supplies the credential scope resolver in paired mode; legacy/no-service operation resolves no protected credential authority and therefore fails closed. Route: runtime integration test documents the intended omission behavior.
- B8 — Verdict: patch. Protected metadata now enforces string, numeric, and list field classes before projection; unknown fields remain dropped. Route: `bridge/standard.py` and wrong-type regression test.
- B9 — Verdict: patch. Endpoint coverage now includes both Sensitive Entry operations, protected rejection, expiry redaction, replacement, fixed results, and privacy assertions. Route: `test_bridge_endpoint.py`.
- B10 — Verdict: patch. NW-02 now distinguishes its wake-claim rules from the later approved `sensitive_entry` and `consequence_confirm` scope categories. Route: NW-02 spec clarification.
- B11 — Verdict: patch. The next-wave roadmap now keeps configuration/artifact read/propose/apply in NW-12 and describes NW-10 as the existing protected structured-prompt boundary. Route: roadmap clarification.
- B12 — Verdict: patch. The exact focused command now records `338 passed`; the full suite records `558 passed`. Route: implementation notes.
- B13 — Verdict: patch. This review triage log and the spec change log are now populated. Route: story artifact.
- E1 — Verdict: patch. Same root cause and fix as B6: all protected expiry event types use the protected projection. Route: expiry regression test.
- E2 — Verdict: patch. The endpoint validates a protected bridge result as an accepted/resolved terminal result before returning the fixed accepted envelope; rejected or ambiguous results become safe errors. Route: endpoint implementation and regression test.
- E3 — Verdict: maybe-false/defer. Protected use-time revalidation occurs immediately before Standard delivery, but no atomic lock spans the independent configuration store and transport; closing that window would require a broader authority transaction. Route: retain the defined revalidation boundary and defer cross-store atomicity.
- E4 — Verdict: patch. Endpoint pending protected prompts are superseded per turn, and Standard clears a protected pending identity when it observes a mismatched structured replacement. Same-correlation delivery remains the current correlated prompt, not a separate Home authority. Route: endpoint/Standard replacement tests.
- E5 — Verdict: false. Protected projection forwards no arbitrary maps, bounds strings and collection counts, and limits option records to three allowlisted fields; a scalar timeout is not an unbounded container path. Route: retain bounded projection.
- E6 — Verdict: false. Credential or claim revocation is intentionally binding-invalidating; the frozen no-close guarantee applies to capability-only configuration changes, which are covered separately. Route: retain revocation semantics.
- E7 — Verdict: false. Same boundary dependency as B1: Standard privacy is an upstream prerequisite outside Home's authority in this slice, and the contract records it explicitly. Route: documentation prerequisite.
- V1 — Verdict: patch. Standard coverage is parameterized across `secret.request`/`secret.respond` and `sudo.request`/`sudo.respond`. Route: Standard regression test.
- V2 — Verdict: patch. Production coverage now exercises each protected capability with the credential granting only that capability, proving the intersection is not an all-or-nothing shortcut. Route: production regression test.
- V3 — Verdict: patch. Runtime integration now creates a paired credential, publishes the endpoint capabilities, resolves a live conversation grant, and asserts both the effective capabilities and revision. Route: runtime regression test.
- V4 — Verdict: patch. Endpoint success coverage is parameterized for both secret and sudo protected operations. Route: endpoint regression test.
- V5 — Verdict: patch. The same parameterized endpoint path covers the 4,096-byte bound for sudo as well as secret input. Route: endpoint/Standard regression tests.
- V6 — Verdict: patch. A transport-uncertain secret delivery is now asserted to leave the turn uncertain and emit exactly one `secret.respond`, with no retry path. Route: Standard regression test.

## Verification

**Commands:**
- `uv run --python 3.14 --locked --extra dev pytest -q tests/test_credentials.py tests/test_credentials_api.py tests/test_configuration_validation.py tests/test_configuration_schema.py tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_production_bridge.py tests/test_diagnostics.py` -- expected: focused permission and protected-prompt tests pass.
- `uv run --python 3.14 --locked --extra dev pytest -q` -- expected: full Home suite passes.
- `uvx ruff check src tests` and `uvx ruff format --check src tests` -- expected: clean.
- `git diff --check` -- expected: no whitespace errors.
