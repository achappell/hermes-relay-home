---
title: 'HOME-NW-16 — Admit Touch panels and grant bound tap-to-talk claims'
type: 'feature'
created: '2026-09-21'
status: 'done'
baseline_commit: 'ebbbe01'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/docs/contracts/v1/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-05-profile-mappings-conversation-claims.md'
---

# Implementation Spec: HOME-NW-16

## Intent

**Problem:** Home grants conversation claims only through wake arbitration. HOME-NW-05 binds a winning `wake_claim` to a Profile, Standard Session, and opaque handle, but every path into that binding requires a wake mapping and acoustic evidence. A Touch panel has no wake phrase and no microphone of its own — its audio peripheral is a separate controller — so it cannot obtain a ready binding by any current route. The consuming story (TUI `STD-7`) is blocked on exactly this: it must not open a microphone before Home returns a ready, admitted binding.

**Approach:** Add a second, non-acoustic route to the same conversation-claim machinery. A paired Touch device holds a `touch_claim` capability and exactly one approved Room/Profile binding in its credential scope. `POST /api/v1/touch-claims` validates device, credential generation, Room, capability, binding currency, and Profile availability, then grants immediately with no arbitration window and no acoustic comparison. Everything downstream of the grant — opaque handle, first-open expiry, bridge open, activity events, idle timer, close, revocation — is unchanged and shared with wake claims.

## Boundaries & Constraints

**Always:** One admitted Touch device resolves to exactly one Home-approved Room and Profile, fixed at approval time and read from credential scope. Validate device authentication, credential generation, exact Room grant, `touch_claim` capability, configuration revision currency, and Profile availability before granting. Honour the per-Room single-active-claim rule. Reuse the existing conversation claim store, the opaque handle, the 90-second first-open expiry, the content-free activity events, and the configurable 8-second idle timer. Revocation of the endpoint, Profile, or binding closes active claims exactly as it does for wake claims. Return only safe ready/unavailable state and an opaque handle.

**Never:** Accept acoustic evidence, a wake mapping ID, a wake phrase, a Profile ID, or a Session ID on this route. Expose Profile or Session identifiers, Hermes credentials, prompts, transcripts, or audio to the device. Infer a Profile from a Device label or from an old scope. Let a tap claim preempt, join, retarget, or promote itself over an active claim in its Room. Open a 250 ms window or rank claimants. Grant to a credential whose scope carries no explicit Touch binding. Add a microphone path, STT, a new Hermes channel, or any sibling-repository change.

**Compatibility decision:** Evolve `/api/v1/*` in place; no clients are deployed. `touch_claim` is a new value in `SUPPORTED_CREDENTIAL_CAPABILITIES`, not a widening of `wake_claim` — a panel must not be able to submit wake claims, and a Puck must not be able to submit tap claims. A credential scope with no `touch_binding` field grants no Touch claim, matching the empty-grant precedent set for wake mappings in HOME-NW-05.

## Resolved Direction

- **Separate route, shared grant.** `POST /api/v1/touch-claims` is its own endpoint rather than a mode flag on `/api/v1/wake-claims`. The wake route's contract requires `wake_mapping_id`, `observation`, and `acoustic_evidence`; a tap has none of those, and overloading the schema would make the required-field validation conditional and weaker. The two routes converge at `ConversationClaimStore.create_from_decision`.

- **No arbitration window.** A tap claim is decided synchronously. There is no competitor to rank — a tap is one deliberate act by one person at one panel — so the 250 ms window would add latency to a user-visible interaction and buy nothing.

- **Room contention: deny while live, take over the idle tail.** "Busy" is two states, and they deserve different answers.

  A claim is **live** while capture, an in-flight turn, or playback is outstanding. A tap into a live Room is denied `room_busy`. It does not wait and it does not preempt: the panel would otherwise seize a Room mid-turn from a conversation already in progress, and someone talking to the kitchen Puck could be cut off by a passing tap on the kitchen display. Denial is immediately visible to the person tapping, who can retry; cutting off speech is not recoverable.

  A claim is in its **idle tail** once it has reported `playback_complete` and its configurable 8-second idle deadline has not yet expired. Nobody is speaking; the claim is held open only in case a follow-up arrives. A tap into an idle-tail Room closes that claim with reason `superseded_by_touch` and grants the tap. Denying here would fail for a reason no person in the room can perceive — the conversation sounded finished because it was — and would force a second tap that succeeds only because the clock ran out.

  The tradeoff accepted: the previous speaker loses their follow-up window to a tap. That is a deliberate exchange of a speculative continuation for a deliberate, present request.

  Home can already distinguish these without new state. The idle timer starts on the content-free `playback_complete` activity event, so the idle tail is exactly *last activity was `playback_complete` and the idle deadline has not expired*. Never treat a claim with no recorded activity as idle — a granted-but-unopened claim is not an idle tail, and is covered by the separate 90-second first-open expiry.

- **Binding lives in credential scope, not configuration.** The Room/Profile pair is fixed when the device is approved, alongside the existing exact wake-mapping grants. The panel has no Profile picker and never learns a Profile ID. Later configuration edits affect only future approvals, matching the HOME-NW-05 rule that active conversations retain their original binding.

## Proposed Contract

### `POST /api/v1/touch-claims`

Request:

```json
{
  "schema": 1,
  "claim_id": "claim-01J...",
  "device_id": "touch-kitchen",
  "configuration_revision": 13,
  "initiation": {
    "kind": "tap",
    "observed_at_ms": 1720000000000
  }
}
```

`initiation.kind` is `tap`; the field is reserved so a later deliberate-initiation variant does not need a new route. `observed_at_ms` is Device wall-clock observation metadata only — the server uses its own monotonic receive clock, as on the wake route. No wake mapping, no acoustic evidence, no Profile, no content.

Response, granted:

```json
{
  "schema": 1,
  "claim_id": "claim-01J...",
  "decision": "granted",
  "configuration_revision": 13,
  "conversation_handle": "opaque-home-claim-01J..."
}
```

`decision` is `granted` or `denied`. Denied responses carry a safe reason and no handle. The granted handle behaves exactly as a wake-granted handle: the endpoint opens it on the Home bridge route, begins capture only after `ready`, and the grant expires if its first successful open does not occur within 90 seconds.

### Denial reasons

| Reason | Cause |
|---|---|
| `unauthorized` | Unauthenticated, unknown device, or changed credential generation |
| `touch_claim_unavailable` | Scope lacks the `touch_claim` capability or carries no `touch_binding` |
| `stale_configuration` | Claim names a revision older than current; device must refresh |
| `room_busy` | The bound Room holds a live claim — capture, turn, or playback outstanding |
| `profile_unavailable` | Bound Profile is unavailable or revoked at grant time |
| `conversation_active` | This device already holds an active claim |
| `configuration_migration_required` | Legacy snapshot shape, as on the wake route |

## I/O & Edge-Case Matrix

| Scenario | Expected behavior | Failure handling |
|----------|--------------------|------------------|
| Valid tap, free Room | Grant immediately; return opaque handle bound to the scope's Room/Profile | — |
| Room holds a live claim (capture, turn, or playback) | Deny `room_busy`; the active conversation is untouched | No preemption, no queueing, no loser promotion |
| Room holds a claim in its idle tail | Close it `superseded_by_touch` and grant the tap | Closure follows the ordinary close path; no replay, no Session reuse |
| `playback_complete` arrives after the idle deadline passed | Treat the claim as expired, not idle | Post-deadline activity must not revive an expired deadline (HOME-NW-05 precedent) |
| Room holds a granted-but-unopened claim | Deny `room_busy`; this is not an idle tail | The 90-second first-open expiry releases it on its own |
| Device taps twice | Second tap denied `conversation_active` while the first claim lives | Terminal turn event frees the binding for the next tap |
| Scope has capability but no binding | Deny `touch_claim_unavailable` | Empty grant is the fail-closed result, not an error |
| Profile revoked between approval and tap | Deny `profile_unavailable`; no Session created | Re-check availability at grant, not only at approval |
| Credential revoked mid-claim | Close the active claim and interrupt live Standard work | Same path as wake-claim revocation |
| Handle never opened | Expire and release the Room after 90 seconds | Shared first-open deadline |
| Stale revision | Deny `stale_configuration` | Device must refresh its snapshot before retry |

## Code Map

- `src/hermes_home/domain/credentials.py` — add `touch_claim` to `SUPPORTED_CREDENTIAL_CAPABILITIES`; add a `touch_binding` (Room + Profile) field to `CredentialScope` with the same bounded-value validation and same "absent field grants nothing" semantics as `wake_mappings`; extend scope serialization at both ends (`~:934`, `~:944`).
- `src/hermes_home/domain/arbitration.py` — no change to `ArbitrationEngine`. The tap path does not enter a `_PendingRound`. It needs only the Room-occupancy question, which should be exposed as a narrow read rather than by reaching into round state.
- `src/hermes_home/domain/conversations.py` — `create_from_decision` currently takes a `WakeDecision`. Either widen it to a shared granted-claim shape or add a sibling constructor; the close/revocation methods (`close_device_claims`, `close_profile_claims`, `close_claim`, `close_all_claims`) are reused unchanged.
- `src/hermes_home/api/application.py` — add the `/api/v1/touch-claims` POST branch beside the wake branch (`~:154`); the handler mirrors `_post_wake_claim`'s durable-context re-checks and `_discard_new_claim` recovery, minus the window sleep and finalize/decision steps.
- `src/hermes_home/bridge/endpoint.py`, `standard.py`, `production.py` — no change expected; a tap-granted handle is indistinguishable downstream. Confirm this with a test rather than by assertion.
- `docs/contracts/v1/` — add `touch-claim.schema.json`; add a "Touch admission" section to `README.md` beside "Wake arbitration".
- Tests: `tests/test_credentials*.py`, `test_api_application.py`, `test_conversations.py`, `test_bridge_endpoint.py` — authorization, binding absence, Room contention, double-tap, revocation, expiry, and redaction coverage.

## Tasks & Acceptance

**Execution:**
- [x] Add the `touch_claim` capability and the `touch_binding` Room/Profile scope field, with approval-time validation against available Profiles and an empty-grant default.
- [x] Add `POST /api/v1/touch-claims` with synchronous decision, full credential/generation/revision/availability validation, and the denial-reason set above.
- [x] Share the conversation-claim grant path so a tap-granted handle carries the same expiry, activity, idle, close, and revocation behavior as a wake-granted one.
- [x] Enforce the per-Room single-active-claim rule: deny into a live Room; close an idle-tail claim `superseded_by_touch` and grant. No queueing and no loser promotion in either case.
- [x] Add the contract schema and README section; update `story-index.yaml` and `sprint-status.yaml`.
- [x] Verify with deterministic tests; record results in `validation-home-nw-16.md`.

**Acceptance Criteria:**
- An admitted Touch device receives a ready opaque handle bound to its approved Room and Profile, with no Profile or Session identifier exposed.
- A device without the capability, without a binding, with a stale revision, or with an unavailable Profile is denied and creates no Session.
- A tap into a live Room is denied and leaves the active conversation entirely undisturbed; capture, turn, and playback are each proven to deny.
- A tap into a Room whose only claim is in its idle tail is granted, and the superseded claim is closed without replay or Session reuse.
- A granted-but-unopened claim and an expired-deadline claim are both distinguished from an idle tail.
- Tap-granted and wake-granted handles are indistinguishable to the bridge, including expiry, idle, close, and revocation paths.
- No acoustic evidence, wake mapping, phrase, prompt, transcript, or audio appears anywhere on this route.

## Open Decisions

- **`create_from_decision` shape.** Widening the existing signature versus adding a sibling constructor is an implementation-time call; both preserve the shared close/revocation surface.

## Design Notes

This story exists to unblock TUI `STD-7`, which states the dependency directly: *"No Home story ID for this Touch predecessor has been assigned; Home owns that record and status."* This spec is that record. Home owns admission, binding, and session authority; the Touch firmware never holds Hermes credentials or parses Hermes events, and the host-side STT path in the consuming repository submits through the bound session rather than opening one.

Per `AGENTS.md`, this is a Home-only ticket: it changes Home's local records and Home's code. The TUI coverage index is read-only context here, and STD-7's own status stays with the TUI tracker.

## Spec Change Log

- 2026-09-21 — Drafted as the Home-owned Touch admission predecessor named in TUI `STD-7`, reusing the HOME-NW-05 conversation-claim machinery through a separate non-acoustic route.
- 2026-09-21 — Room contention decided: deny into a live Room, supersede an idle-tail claim. Replaces the drafted deny-always rule; removes the corresponding open decision.

## Verification

Focused tests, then `ruff check src tests` and `ruff format --check src tests`, per `AGENTS.md`. Record observed results in `_bmad-output/implementation-artifacts/validation-home-nw-16.md` at implementation time.
