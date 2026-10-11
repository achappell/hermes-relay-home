---
title: 'HOME-TC-B2 (placeholder ID; owner assigns) — Project a single-select Standard clarify as a typed choice'
type: 'feature'
created: '2026-10-11'
status: 'draft'
baseline_commit: '1f7052409b7bcf22d016eaa91b838bd9c2dd389c'
route: 'dispatch'
review_loop_iteration: 0
source_story: 'HOME-TC-B2 (placeholder; no story ID, issue, or tracker row exists yet)'
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-tc-b1-client-device-interactive-choice.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-07-freshness-bound-typed-choice-authority.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md'
  - '{project-root}/_bmad-output/specs/spec-standard-hermes-compatibility-migration/standard-baseline.md'
  - '{project-root}/_bmad-output/implementation-artifacts/home-tc-b3-live-proof-and-sequencing.md'
---

> **Placeholder IDs.** `HOME-TC-B1`, `HOME-TC-B2`, and the B3 live proof are working names; the owner assigns real IDs and tracker rows. This spec depends on B1 (it consumes `ConversationGrant.interactive_choice` for client claims) and **cannot leave `draft` until the Standard capture in Open Question 1 exists.**

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Pinned Standard Hermes `0.21.1` (`2237be3`) emits approval, clarify, secret, and sudo prompts only. It has no `prompt.request` event and no `prompt.choose` / `prompt.explore` operations, so Home never has a typed choice to forward and never has a capability to advertise. Typed choices therefore cannot be proven against a real Home and a pinned Standard (readiness note Q2).

**Approach:** Home adapts a supported Standard interface without changing Hermes. Inside `StandardHermesBridge`, a `clarify.request` that has exactly one single-select question with listed text choices, on a conversation whose grant is interactive, is rewritten before the existing typed-choice pipeline sees it as a native `prompt.request` (`prompt_kind: "choice"`, operations `["choose"]`). The client's `choose` is mapped back to one `clarify.respond {request_id, answer, question_id}`. `ChoiceAuthority`, `_choice_respond`, and the public payload shape are reused unchanged. Every other clarify, and every other prompt type, keeps today's path.

**Owner decisions recorded (2026-10-11):** D1 = P1 as above; `explore` is NOT advertised; everything else keeps today's clarify path unchanged. D3 = A (`choose`-only is accepted; FR-45 / Story 2.6 wording is amended outside this repo to "Explore is advertised only when Standard supports it"). D4 = A (clarify-derived choices are allowed on the household browser only, harmless-only, with no consequence-confirm path; approvals unchanged). D5 = A (browser appliance first; mechanism per Device ID).

## Boundaries & Constraints

**Always:**
- Project only when all hold at event time: the grant is interactive (B1 for client claims), `prompt.choose` is in the bridge's advertised capabilities, Standard's `gateway.ready` does not itself advertise `prompt.choose`, and the clarify passes the eligibility rules below. Otherwise forward the raw `clarify.request` exactly as today.
- Emit one representation only: a projected clarify is never also forwarded as `clarify.request`.
- Take the answer from Home's stored mapping (option ID -> the original choice string). Never from client-supplied text.
- Mint option IDs (`opt-1`..`opt-N`), freshness, and public object IDs inside Home. Standard's `request_id` and `qid` never leave the bridge as typed-choice identity (`ChoiceAuthority` mints the public object/freshness).
- Consume each choice once, before delivery (existing `authorize`). A rejected, stale, expired, uncertain, or duplicate action is never resent and never becomes `prompt.submit` or a new turn.
- Record only content-free diagnostics (existing `_record_choice_diagnostic`). Never log or put into diagnostics a question, choice text, `request_id`, or `qid`.

**Never:**
- Advertise `prompt.explore` from projection, or call Standard for an `explore`.
- Project `approval.request`, `secret.request`, `sudo.request`, a native `prompt.request`, or any clarify with free text, `multi_select`, more than one question, or a non-text choice.
- Add a consequence-confirm or sensitive path for projected choices, or classify a clarify as harmless.
- Modify Hermes, add a Standard operation, or route a choice through `prompt.submit`.
- Change `choice_authority.py` limits or semantics, the endpoint `prompt.respond` shape, or any client-visible field name.
- Touch the TUI, iOS, Android, Pixel, hardware, or any running service.

## I/O & Edge-Case Matrix

Terms: *projected* = rewritten to `prompt.request`; *legacy* = forwarded as `clarify.request`, byte-identical to `1f70524`.

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY | Opted-in client claim; eligible clarify | One `prompt.request` with `options[{id:"opt-n",label}]`, `choice.operations: ["choose"]`; no `clarify.request` forwarded. `choose` once -> one `clarify.respond {session_id, request_id, answer, question_id}` -> endpoint result `{status:"accepted", operation:"choose"}` | N/A |
| DEFAULT_OFF | Not opted in, or `prompt.choose` not advertised | Legacy; no `prompt.choose` in handshake | N/A |
| INELIGIBLE_SHAPE | `choices: null`, empty, non-string, `multi_select: true`, 0 or 2+ questions, flat `{question,choices}`, text over 1024, label over 256, over 32 choices, duplicate labels, invalid `request_id` or `qid` | Legacy | None |
| EXPLORE | Client sends `explore` | `{status:"unavailable", reason:"unsupported"}` from `ChoiceAuthority`; no Standard call; object stays current until replaced or expired | N/A |
| STALE | Wrong `object_id`/`freshness`, or option not listed | `unavailable` (`stale` / `unknown`); no Standard call | N/A |
| DUPLICATE | Second `choose` after accept, or two concurrent | Exactly one `clarify.respond`; the other `unavailable/duplicate` | N/A |
| REPLACED | A second eligible clarify (new `request_id`) while the first is pending | First -> `replaced`; second current | Existing revision cap (128/turn) applies |
| EXPIRED_HOME | 300 s after first delivery | `unavailable/expired`; no Standard call | N/A |
| EXPIRED_UPSTREAM | Standard `clarify.expire` for the projected `request_id` | Rewritten to `prompt.expire` (same correlation); object `expired` | Non-matching `request_id` ignored |
| REEMITTED | Same `request_id` seen again in the turn (no `seq`, or after resume) | Dropped; never re-offered | No protocol error |
| RECONNECT | Client reconnects or parked endpoint adopted | Pending choices revoked (existing `revoke_all`); response -> `unavailable/revoked`; no resend; parked delivery starts its 300 s window at first send | N/A |
| CAPABILITY_LOST | Opt-in removed or revision changed after projection | Response refused (`unsupported`); one-shot; Standard not called; clarify stays pending upstream until Standard times out or the user interrupts | Existing `BridgeCapabilityUnavailable` path |
| UPSTREAM_REJECT | `clarify.respond` rejected (`GatewayRPCError`, `accepted:false`) | `unavailable/stale`; turn's choices revoked; no retry | N/A |
| UNCERTAIN | Timeout or transport loss during `clarify.respond` | Endpoint marked unavailable, no retry | Existing `_raise_bridge_error` |
| UNEXPECTED_REMAINING | Result has non-empty `remaining` | Protocol error; transport loss; no retry | One question was sent, so remaining must be empty |
| TURN_TERMINAL | `message.complete` while pending | Revoked | N/A |
| OTHER_PROMPTS | approval, secret, sudo, native `prompt.request` | Unchanged | N/A |

</frozen-after-approval>

## Open Questions

- **1. Answer value: label, ID, or index (BLOCKING).** Provisional: `answer` is the choice string verbatim (`choices[i]`). Evidence is partial and none of it is a capture of a choice-bearing clarify. In the repo: (a) `tests/test_standard_bridge.py` L4425-4455 asserts `choices: ["one","two"]` with `answer: "one"`, but that is an authored test, not a capture; (b) the only captured clarify is free-text: `{"questions":[{"qid":"q0","question":"What name?","choices":null,"multi_select":false}],"request_id":"f968a59f"}` (live STD-4 audit, commit `a460f17`; `tests/test_standard_bridge.py` L5872-5902); (c) the Event Compatibility Matrix records `choices: null | [..]` without element type, and keeps raw frames "only in a session scratch directory" (not in the repo); (d) `_validate_structured_response` accepts `answer` as a string or a non-empty list of strings (standard.py L3833-3839), consistent with, but not proof of, answers being choice text. The TUI (#241/#243) makes the same untested assumption. **Capture needed before `ready-for-dev`:** on a loopback Standard Hermes `0.21.1` / `2237be3` with a throwaway Profile, record raw `/api/ws` frames for (i) a single-select clarify: exact `choices` element type and any extra keys; (ii) `clarify.respond` with the choice string, with a 0- and 1-based index, and with a string not in `choices`: the result frame and what text the agent actually receives; (iii) a clarify left unanswered: whether `clarify.expire` is emitted and after how long; (iv) a gateway reconnect with `session.resume` while a clarify is pending: is it re-emitted, with which `request_id`, and is it still answerable; (v) `session.interrupt` while pending. Commit sanitized frames as `tests/fixtures/standard/clarify-*.json` and add an evidence row to `standard-baseline.md`. The `hermes-agent` source is not on this machine, so the owner or an ops session must run the capture.
- **2. Duplicate labels.** Options: (A, provisional) ineligible, legacy path; (B) project with distinct IDs. The user would see two identical buttons. Provisional: A.
- **3. Standard natively advertises `prompt.choose`.** Options: (A, provisional) projection is off and the native path wins (a re-pin is its own story); (B) both coexist. Provisional: A.
- **4. `prompt.explore` key.** Options: (A, provisional) omit it, matching "omission denies" and the TUI `main` appliance, which offers `prompt.explore` only when `"prompt.explore"` is in the session capabilities; (B) emit explicit `false`. Provisional: A.
- **5. Choice TTL versus Standard's clarify timeout.** Home expires after 300 s; Standard's clarify may time out sooner or later (unknown until capture iii). Provisional: keep 300 s and let Standard's `clarify.expire` shorten it; a user choosing after an unseen upstream timeout gets `stale`.
- **6. Flat `{question, choices}` shape.** The TUI reads it, but real Standard always sends a batch. Provisional: not eligible.

## Code Map

Anchors verified against `1f70524`.

- `src/hermes_home/bridge/standard.py`
  - L32-42 `_STRUCTURED_PROMPT_OPERATIONS`; L75-81 `_PROMPT_EXPIRY_TYPES` (`clarify.expire` is keyed to `clarify.request`, so it would be dropped for a projected prompt).
  - L2377-2440 `_next_event` structured-prompt branch: the rewrite point is just before `_validate_structured_event_payload` (L2377-2380), so pending/revision/cap logic (L2391-2440) treats the projection as a native typed choice. `pending_mismatch` / `is_choice_revision` (L2408-2434) give REPLACED for free. `clarify.expire` handling L2441-2458. Pending is cleared at L2051-2054, 2102-2105, 2430-2433, 2455-2458, 2483-2486: any new projection record must be cleared at each.
  - L2511-2541 `choice_context`: `choice_capabilities` derive from `grant.interactive_choice` and the advertised map, so advertising `prompt.choose` is sufficient.
  - L290-315 `_ActiveTurn`: add the per-turn projection record (`request_id`, `qid`, ordered choice strings) and a bounded `projected_request_ids` set.
  - L1986-2106 `_respond_prompt`: after the capability and `grant.interactive_choice` checks (L2005-2027) and `_validate_typed_choice_response` (L3733-3777), branch for a projected correlation: send `clarify.respond` instead of `prompt.choose` (L2045-2056). Validate the result with clarify-batch rules, not `prompt.request` rules: `_require_prompt_resolution_result` (L3882-3928) raises on `remaining` unless `_is_batch_clarify_event` (L3790-3793), which is false for the synthetic event. Needs a unit test.
  - L3430-3459 `_capabilities`: add a `clarify_choice` input; when true and Standard did not advertise it, set `prompt.choose: True` and leave `prompt.explore` out. Called at L1086 (open) and L1369 (reconnect).
  - L3653-3680 `_validate_structured_event_payload`: unchanged for legacy batch clarify; L3683-3730 validates the synthetic payload.
  - `BridgeEvent.to_endpoint` (L254-258) refuses `prompt.request`; the endpoint always projects it through `ChoiceAuthority`. Keep.
- `src/hermes_home/bridge/choice_authority.py`: `project` L87-207, `authorize` L233-312, `complete` L314-325, `expire_correlation` L327-337, limits L19-25. Reused unchanged; a synthetic event is an ordinary typed-choice input.
- `src/hermes_home/bridge/endpoint.py`: `_choice_respond` L1396-1508 (unchanged), `_event_payload` L1958-2059 (`prompt.request` -> `ChoiceAuthority.project`), `_prompt_respond` L1320-1349 routes `prompt.request` to `_choice_respond`, `_safe_capabilities` L3022-3056 passes `prompt.choose` through, reconnect clears pending and revokes choices L1683-1694, parked delivery L2696-2713.
- `src/hermes_home/bridge/production.py` L586-618: B1 supplies the grant flag.
- `_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md` L252-308 (response-key table, typed-choice section, capability paragraph).
- `_bmad-output/specs/spec-standard-hermes-compatibility-migration/standard-baseline.md` L38 (Prompts row): add the capture evidence row.

## Tasks & Acceptance

**Execution:**
- [ ] (Prerequisite, not code) Complete the Open Question 1 capture and commit sanitized fixtures; resolve Open Questions 1 and 5 in the frozen block
- [ ] `src/hermes_home/bridge/standard.py` -- eligibility function; early rewrite to `prompt.request`; per-turn projection record, dedupe set, and clears; `clarify.expire` -> `prompt.expire`; `_respond_prompt` branch to `clarify.respond` with clarify-batch result validation; `_capabilities` `clarify_choice` at open and reconnect -- D1/D3/D4
- [ ] `bridge-contract.md`, `standard-baseline.md` -- apply the amendments below
- [ ] Tests (below) -- matrix coverage; existing clarify tests stay unmodified and green as the default-off proof

**Contract amendments (exact text):**

1. `bridge-contract.md` after the `prompt.request` row of the response-key table (L255-259) add a note: `A prompt.request with prompt_kind "choice" is either emitted by Standard or projected by Home from a Standard clarify.request (see Clarify projection). The projected form accepts only operation "choose", and maps to clarify.respond.`
2. `bridge-contract.md` replace the paragraph at L302-309 with: `The prompt.choose capability flag is exposed when the bound Home Device may answer typed choices (see Device eligibility) and either Standard advertises prompt.choose, or Home projects Standard clarify.request prompts as typed choices. The prompt.explore flag is exposed only when Standard advertises it. Home never advertises an operation that has no Standard operation behind it. Passive Room Displays and audio-only Pucks, which have no eligibility, stay read-only.` (Device eligibility = the B1 text.)
3. `bridge-contract.md` new subsection `### Clarify projection` after the typed-choice section: `When the Device is eligible and Standard does not advertise typed choices, Home projects a clarify.request that has exactly one question, multi_select false, and 1-32 non-empty text choices as a typed choice. The question text is the explanation, each choice is an option with a Home-minted ID, and operations is ["choose"]. A choose maps to one clarify.respond carrying the original choice text and the question_id. Home sends the answer exactly once; stale, expired, replaced, revoked, duplicate, unknown, and unsupported attempts, including explore, return the typed unavailable result and do not reach Standard. Any other clarify.request is forwarded unchanged. Projected choices are Hermes questions: Home cannot classify them harmless, so they never carry a consequence-confirm path, and approval.request is never projected.`
4. HOME-NW-07's `Always` clause ("advertise only when the upstream supports the matching operation") is satisfied for `prompt.explore` and amended for `prompt.choose` by item 2. NW-07 is `done`; record the amendment here and in the B2 validation record, not by editing NW-07.

**Acceptance Criteria:**
- Given an opted-in client claim and an eligible clarify, when the user chooses once, then Standard receives exactly one `clarify.respond` with the stored answer and `question_id`, the client sees no `clarify.request` for it, and a second choose is `duplicate`.
- Given default off, or any ineligible shape, when a clarify arrives, then the endpoint receives the same bytes as at `1f70524` and the handshake carries no `prompt.choose`.
- Given an `explore`, stale, replaced, expired, reconnected, or capability-lost attempt, when sent, then the typed unavailable result is returned and no Standard request is made.
- Given an uncertain delivery, when it occurs, then nothing is resent and no `prompt.submit` or new turn exists.
- Given a re-emitted `request_id`, when it arrives, then it is not re-offered.
- Given diagnostics and logs for a whole run, when scanned, then they contain no question, choice, `request_id`, or `qid` text.

## Design Notes

**Why rewrite inside the bridge, not the endpoint.** The endpoint already treats `prompt.request` as Home-authorized typed-choice input, the bridge owns the active turn, runtime session, and pending-prompt state, and `_respond_prompt` is the only place that talks to Standard. Rewriting early reuses revision/replace/cap/expiry logic and keeps `choice_authority.py` untouched.

**Interaction with TUI B2b/B2c.** TUI `main` already renders single-select clarify through the legacy path (#241, #243, newer than the readiness note). P1 therefore adds no user capability; its purpose is to exercise the typed-choice contract end to end so 2-WK-5/6 can close (Q2). A client that ignores typed choices still gets a working legacy clarify whenever the grant is not interactive.

**A subtle sequencing trap.** Projecting when `interactive_choice` is False would swallow the clarify (the choice would carry no operations). The projection gate therefore checks the grant at event time and falls back to legacy.

## Verification

**Commands:**
- `uv run pytest tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_diagnostics.py tests/test_production_bridge.py -q` -- expected: all pass
- `uv run ruff check src tests` and `uv run ruff format --check src tests` -- expected: clean

**Tests to add or extend (names are proposals):**
- `tests/test_standard_bridge.py`: `test_eligible_single_select_clarify_is_projected_as_typed_choice`; `..._choose_maps_to_clarify_respond_with_question_id`; one parameterized `test_ineligible_clarify_shapes_stay_legacy`; `test_projection_is_off_without_interactive_grant_or_advertised_choose`; `test_native_prompt_choose_advertisement_disables_projection`; `test_clarify_expire_expires_the_projected_choice`; `test_reemitted_request_id_is_never_reoffered`; `test_projection_clears_with_every_pending_prompt_reset`; `test_projected_resolution_tolerates_empty_remaining_and_rejects_nonempty`; `test_capabilities_advertise_choose_only_at_open_and_reconnect`; capture-fixture tests once fixtures exist. Existing clarify tests (L4425-4455, L5872-5902) stay unmodified.
- `tests/test_bridge_endpoint.py`: projected choice through the real `ChoiceAuthority`: `choose` once, `explore` unsupported, stale freshness, duplicate (sequential and concurrent), replaced, Home expiry, upstream expiry, reconnect revoke, capability loss, upstream reject, uncertain timeout never retried.
- `tests/test_bridge_server.py`: one live-websocket pass of choose, then duplicate.
- `tests/test_diagnostics.py`: content-free outcomes for a projected choice.
- `tests/test_production_bridge.py`: end-to-end claim -> grant -> advertised flag (needs B1).

**Manual checks:** none for B2. Live proof is B3 (sequencing note).
