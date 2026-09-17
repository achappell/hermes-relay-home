---
title: 'HOME-NW-07 — Add freshness-bound typed-choice authority'
type: 'feature'
created: '2026-09-17'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '73d58e5092e6a24796463120382f495842ea0750'
source_story: 'HOME-NW-07'
story_key: 'home-nw-07-typed-choice-authority'
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-next-wave-planned.md'
  - '{project-root}/_bmad-output/specs/spec-standard-bridge/transport-contract.md'
  - '{project-root}/_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Home forwards correlated approval and clarification prompts, but it has no typed-choice authority. A client cannot return `choose` or `explore` bound to the choice object, active claim, turn, and current freshness without treating the action as text or an arbitrary command.

**Approach:** Extend the existing Home v1 bridge prompt path with bounded choice identity and server-owned freshness. Bind each issued choice to the authorized endpoint, Home conversation claim, Standard Session, active turn, and option IDs. Keep Home authoritative for expiry, replacement, replay rejection, and the typed unavailable result.

## Boundaries & Constraints

**Always:** Keep Standard Session IDs and private values inside Home. Project only bounded explanation text, opaque object/freshness IDs, the advertised `choose`/`explore` operations, and bounded option IDs and labels. Match the TUI limits: 32 options, 64 characters per choice ID/freshness/option ID, and 256 characters per label. A choice action is one structured response in the owning turn; it never creates a turn. Home expires each object 300 seconds after first delivery using its monotonic clock; exploration does not extend that deadline. Advertise `prompt.choose`/`prompt.explore` only when the upstream supports the matching operation, and forward only through that explicit capability. Record only safe correlation and outcome fields.

**Never:** Route a choice through free-text `prompt.submit` or arbitrary `command.dispatch`; accept an unadvertised operation or option; replay an uncertain action; expose credentials, Sensitive Entry values, or arbitrary option values; allow passive displays or audio-only Pucks to act; execute consequence-bearing actions in this first harmless proof; log choice text; change another repository in this Home story.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CURRENT_CHOICE | Authorized endpoint, active claim/Session/turn, current object and listed option | Accept exactly one advertised `choose` or `explore` as a structured action | None |
| EXPLORE | Current object, listed option, advertised `explore` | Deliver one non-committing action; keep the original object usable until replacement or expiry, without extending its deadline | A failed or uncertain write is never replayed |
| STALE_OR_REPLAYED | Expired, replaced, revoked, unknown, wrong-turn, wrong-endpoint, or already-consumed choice | Return `{"status":"unavailable","reason":"..."}`; create no Hermes turn or action | Reason is one of `stale`, `expired`, `replaced`, `revoked`, `duplicate`, `unknown`, or `unsupported`; no private content |
| UNSAFE_OBJECT | Unknown fields, raw option values, sensitive-key fields, malformed or oversized text/options | Project only allowlisted fields; reject malformed required fields or limit violations | Do not log or echo rejected content |

</frozen-after-approval>

## Code Map

- `src/hermes_home/bridge/standard.py` — owns the active claim/Session/turn binding, Standard event validation, and correlated response path. Preserve its revalidation and never expose the Standard Session ID.
- `src/hermes_home/bridge/endpoint.py` — owns Home v1 RPC dispatch, endpoint authorization, pending prompt correlation, event projection, and public-field filtering. Add exact choice allowlists and typed unavailable outcomes here.
- `src/hermes_home/bridge/production.py` — durable claim lifecycle and monotonic timing patterns; choice authority itself must die with its active turn/claim and need no SQLite migration.
- `src/hermes_home/domain/configuration.py` — optional Device eligibility for an interactive choice surface; old configuration snapshots remain ineligible by default.
- `tests/test_standard_bridge.py`, `tests/test_bridge_endpoint.py`, `tests/test_diagnostics.py` — prove binding, expiry, replacement, duplicate rejection, side-effect-free unavailable results, bounds, and content-free audit.
- `tests/test_configuration_validation.py`, `tests/test_production_bridge.py` — prove the eligibility opt-in and its grant binding.
- `_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md` — document the Home WebSocket request/result shape and safety rules.
- `_bmad-output/implementation-artifacts/story-index.yaml`, `sprint-status.yaml`, and a new NW-07 validation record — keep Home-local delivery state and evidence authoritative.

## Tasks & Acceptance

**Execution:**
- [x] Add Home-owned, monotonic, per-choice freshness and lifecycle state to the existing correlated bridge path; keep it tied to the active claim, endpoint, Standard Session, turn, and configuration revision.
- [x] Validate only the normalized choice shape and listed options; require the advertised operation; consume each pending correlation once; return the stable typed unavailable result without a new turn or side effect.
- [x] Add safe, content-free outcome diagnostics and update the v1 bridge contract; do not edit TUI, iOS, Android, Coordinator, or Hermes Agent in this Home story.
- [x] Add focused lifecycle, privacy, malformed-input, and replay tests; record focused/full tests, Ruff, format, and `git diff --check` in `validation-home-nw-07.md`.

**Acceptance Criteria:**
- Given a current choice on the bound endpoint and turn, when a listed option uses an advertised operation, then Home delivers one structured response to the owning Session.
- Given `explore`, when Home accepts the response, then it requests detail without committing the option or invalidating the original object.
- Given stale, duplicate, revoked, replaced, unknown, or unauthorized context, when a response arrives, then Home returns typed unavailability and produces no new turn or action.
- Given malformed or unsafe choice content, when Home projects the object, then no credential, Sensitive Entry value, raw option value, or unknown field reaches the endpoint or diagnostics.
- Given an action attempt, when Home records its outcome, then diagnostics contain safe correlation and outcome only.

### Review Findings

Full review covered four diff chunks with four independent layers per chunk. All 42 findings were adjudicated individually before grouping. Final counts: 0 decision-needed, 17 patch groups, 2 deferred, 10 rejected. F22 was resolved as a patch after the user selected preserving the last valid choice on per-turn revision overflow.

#### Patch

- [x] [Review][Patch] Start the 300-second choice window on first delivery [src/hermes_home/bridge/choice_authority.py:174] — Members F15, F18, F37 (acceptance-auditor and blind-hunter, chunks 1, 2, and 4; medium). Expiry is set while projecting the event, before the endpoint sends it. Events can be parked while the WebSocket is disconnected, so reconnect can deliver a shortened or already expired choice. Align the monotonic deadline and advertised timeout with the first actual send, and test a delayed parked event.
- [x] [Review][Patch] Keep safe diagnostics available after turn release [src/hermes_home/bridge/endpoint.py:923] — Member F16 (acceptance-auditor, chunk 1; medium). Turn release removes the turn-to-correlation mapping before a late choice response is rejected, so the action attempt is not recorded. Preserve a bounded safe correlation for this diagnostic and test a response after terminal release.
- [x] [Review][Patch] Reject same-freshness choice content changes before they leave old authority live [src/hermes_home/bridge/standard.py:2088] — Members F03, F13, F24 (blind-hunter and verification-gap, chunks 1 and 3; medium). Standard identifies a choice revision by object ID and freshness, then silently drops a different correlation when those IDs are unchanged. If options or operations changed, Home's later normalizer cannot revoke the old choice because it never receives the event. The authority's direct check also lacks a regression test. Route the mutation to Home's fail-closed check and cover the Standard-to-endpoint path.
- [x] [Review][Patch] Serialize choice response admission with newer revisions [src/hermes_home/bridge/standard.py:1762] — Member F38 (blind-hunter, chunk 4; medium). The pending-prompt check releases the state lock before gateway.request; a newer revision can replace the pending correlation between the check and the write. Prevent a stale response from reaching the gateway in that race and add a deterministic concurrent test.
- [x] [Review][Patch] Prevent replay after correlation eviction [src/hermes_home/bridge/choice_authority.py:179] — Members F05, F26, F39 (blind-hunter and verification-gap, chunks 1, 3, and 4; medium). The 256-entry cache drops old correlations without tombstones. I reproduced 257 correlations for one still-active object, then replayed the first correlation; projection reused the public choice and authorization returned no rejection reason. Retain bounded replay protection for the live choice/turn and add this sequence as a regression test.
- [x] [Review][Patch] Keep the integer timeout positive until the monotonic deadline [src/hermes_home/bridge/choice_authority.py:187] — Member F08 (blind-hunter, chunk 1; low). Flooring a positive remainder under one second advertises timeout_s: 0 even though authorization still accepts until the exact deadline. Align integer countdown rounding and test the final fractional second.
- [x] [Review][Patch] Clarify that the public IDs are option IDs [_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md:283] — Member F19 (blind-hunter, chunk 2; low). The current sentence says only IDs and labels reach the endpoint even though the same documented payload also includes text, prompt identity, timeout, and operations. Say “option IDs and labels” so the privacy boundary is precise.
- [x] [Review][Patch] Validate the configuration schema semantically [_bmad-output/implementation-artifacts/validation-home-nw-07.md:30] — Members F21, F42 (verification-gap, chunks 2 and 4; medium). The recorded command checks JSON syntax only; it does not exercise Draft 2020-12 schema rules. Add schema-level tests for omission, boolean values, invalid types, and unknown fields.
- [x] [Review][Patch] Test the endpoint's resolved-true success result [tests/test_bridge_endpoint.py:571] — Member F12 (verification-gap, chunk 1; medium). Standard accepts resolved: true and the endpoint maps it to accepted, but current endpoint choice tests use accepted: true. Add an endpoint regression test for the supported resolved result shape.
- [x] [Review][Patch] Assert typed-choice capability flags in endpoint readiness [tests/test_bridge_endpoint.py:1456] — Member F14 (verification-gap, chunk 1; medium). Add readiness assertions proving prompt.choose and prompt.explore reach the endpoint only when both upstream support and Device eligibility allow them.
- [x] [Review][Patch] Cover unknown options and extra response fields [tests/test_bridge_endpoint.py:1038] — Members F25, F32 (blind-hunter and verification-gap, chunk 3; medium). The response validator rejects an unlisted option as unknown and extra keys as unsupported, but the endpoint tests do not prove either typed result or the absence of a bridge action.
- [x] [Review][Patch] Cover capability loss and uncertain choice delivery [src/hermes_home/bridge/endpoint.py:862] — Members F27, F31, F35 (blind-hunter, verification-gap, and acceptance-auditor, chunk 3; medium). Exercise BridgeCapabilityUnavailable after authorization and a transport timeout followed by a retry of the same correlation. Verify the unavailable/failure outcome and that no second gateway action is sent.
- [x] [Review][Patch] Complete the malformed-choice rejection matrix [tests/test_bridge_endpoint.py:204] — Members F29, F30 (blind-hunter and edge-case-hunter, chunk 3; medium). The event helper replaces options=[] with defaults, making empty options untestable. Add that case plus top-level and nested sensitive flags and non-boolean privacy flags to the rejection matrix.
- [x] [Review][Patch] Test all published text and identifier limits [src/hermes_home/bridge/choice_authority.py:18] — Member F33 (verification-gap, chunk 3; medium). Add boundary and over-limit cases for 64-character identifiers, 1024-character explanation text, and 256-character option labels.
- [x] [Review][Patch] Exercise typed-choice notifications through the event pump [src/hermes_home/bridge/endpoint.py:1181] — Member F28 (blind-hunter, chunk 3; medium). Current choice projection tests call the private _event_payload method directly. Add a public event-pump test proving a valid typed choice is serialized and delivered, and a malformed event follows the production failure path.
- [x] [Review][Patch] Link the validation record from the story index [_bmad-output/implementation-artifacts/story-index.yaml:52] — Members F36, F41 (blind-hunter and edge-case-hunter, chunk 4; medium). The validation file exists, but HOME-NW-07 has no validation entry in the local delivery index, so indexed closure review cannot discover its evidence.
- [x] [Review][Patch] Preserve the last valid choice when the per-turn revision limit is reached [tests/test_bridge_endpoint.py:965] — Member F22 (blind-hunter, chunk 3; medium). User decision: the previous valid choice remains usable after overflow. Handle the over-limit event without closing the endpoint or consuming/replacing the Standard correlation for the last valid choice; cover the production event-pump path as well as the limit test.

#### Defer

- [x] [Review][Defer] Confirm whether Standard correlation IDs are globally unique across prompt types [src/hermes_home/bridge/endpoint.py:763] — deferred: unverified medium; the dispatcher routes by retained correlation ID alone, but the local contract does not state global uniqueness. Confirm the upstream guarantee; if absent, scope routing by event identity. Members F04 and F10 (blind-hunter and edge-case-hunter, chunk 1).
- [x] [Review][Defer] Audit client rendering of bidi and formatting controls [src/hermes_home/bridge/choice_authority.py:378] — deferred: unverified medium; Home permits bounded Unicode text and labels containing bidi controls, but this repository does not establish how clients render them. Audit the client renderer and confirm controls are neutralized without breaking legitimate right-to-left text. Member F06 (blind-hunter, chunk 1).

#### Rejected

- F01 (false) — The claim that the v1 schema rejects interactive_choice is contradicted by docs/contracts/v1/configuration.schema.json:74-78, which declares the optional boolean property under additionalProperties: false.
- F02 (false) — The claim that choice text must remain unchanged is contradicted by the frozen Explore behavior and tests/test_bridge_endpoint.py:753-760, which change the explanation to “More detail.” while keeping the same choice object usable.
- F07 (false) — Bounded option IDs are explicitly part of the public typed-choice contract and are required for response mapping; raw option values and unknown fields are dropped by the projection at src/hermes_home/bridge/choice_authority.py:189-201.
- F09 (false) — The claim that the full change adds no tests for the authority is contradicted by the projection, replay, expiry, replacement, and unavailable-path tests in tests/test_bridge_endpoint.py and tests/test_standard_bridge.py.
- F11 (false) — Unknown event names with a prompt. prefix are not forwarded; the endpoint event pump forwards only _ENDPOINT_EVENT_TYPES, declared at src/hermes_home/bridge/endpoint.py:113.
- F17 (false) — The full diff adds focused and standard-bridge tests, the typed-choice contract at _bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md:262-299, and the validation record at _bmad-output/implementation-artifacts/validation-home-nw-07.md.
- F20 (false) — The contract permits bounded option IDs in the public payload; it drops source option values and unknown fields. The choice projection contains only each option's id and label at src/hermes_home/bridge/choice_authority.py:189-201.
- F23 (false) — A supported configuration write increments the configuration revision, so changing interactive_choice without changing the revision is not reachable through the normal storage path; see src/hermes_home/storage/sqlite.py:110.
- F34 (false) — Unsupported explore is covered by tests/test_bridge_endpoint.py:1108-1128: the event advertises only prompt.choose, the response attempts explore, and the endpoint returns unsupported without a bridge call.
- F40 (false) — Changed explanation text does not alter action authority by itself; Explore explicitly requests more detail while preserving the current choice and deadline, as shown in the frozen intent and tests/test_bridge_endpoint.py:753-760.

## Implementation Notes

Typed choices also require the Home Device configuration to opt in with
`capabilities.interactive_choice: true`. The field is optional and defaults to
false, preserving existing snapshots and denying passive displays and
audio-only Pucks by default. `ConversationGrant` carries the resolved flag and
the claim's existing configuration revision into each choice event; response
delivery rechecks both. This adds no SQLite column or migration.

## Spec Change Log

## Review Triage Log

| Finding | Verdict and evidence | Route |
| --- | --- | --- |
| BH-01 — wrong-handle response echoes the bound handle | `low` — the mismatched-handle branch returns `bound_handle` in the result, disclosing the active opaque handle; echoing the request handle is a direct correction. | P-01 patch |
| BH-02 — concurrent correlations can authorize two choices | `false` — authorization consumes a correlation under the endpoint state lock; Standard forwards only its single pending prompt type/ID, so distinct correlations for one object cannot be concurrently pending and a replay of one correlation is rejected. | Rejected |
| BH-03 — explanation text is omitted from source identity checks | `false` — source object ID and freshness define the revision identity, while options and operations define the allowed action; prose changing without a new freshness identity does not change that authority. | Rejected |
| BH-04 — expiry of one correlation expires another live correlation | `false` — Standard holds one pending prompt correlation; an old expiry is ignored after a newer correlation becomes pending, and a still-pending correlation prevents another from being issued. | Rejected |
| BH-05 — correlation eviction leaves a live public projection unknown | `false` — older correlations must have been consumed, expired, or replaced before 256 later prompts can issue; eviction returns the contract's typed no-effect `unknown` reason and cannot authorize an action. | Rejected |
| BH-06 — pending prompt storage grows without bound | `false` — Standard admits one pending prompt at a time, endpoint response/expiry paths remove it, turn release clears leftovers, and Home also caps choice objects per turn. | Rejected |
| BH-07 — revision-limit failure replaces the last usable choice | `medium` — after admitting newer source revisions, a 129th distinct revision can reach the authority while the current correlation is still open; the capacity error currently marks that current object replaced first, making the last usable choice unavailable. | P-07 patch |
| BH-08 — upstream rejected result becomes an RPC error and leaves authority live | `false` — `HomeBridge` validates rejection results before returning; `BridgeRequestRejected` is mapped to typed `stale` unavailability and the endpoint revokes/removes the prompt. | Rejected |
| BH-09 — nested sensitivity is not checked at the Standard boundary | `false` — the Home choice normalizer rejects sensitive choice and option flags before projection, so nested-sensitive content cannot reach the client; the later fail-closed rejection has no demonstrated harmful effect. | Rejected |
| BH-10 — malformed bridge result has no outcome diagnostic | `false` — Standard rejects malformed results before returning, and endpoint bridge exceptions record a content-free `failed` diagnostic; the endpoint result validator only receives accepted results in production. | Rejected |
| EC-01 — Standard silently drops a newer choice revision with a new correlation | `medium` — the active-prompt guard drops a different correlation before Home can see its changed source freshness, leaving the prior choice current even though the frozen intent says a new revision replaces it. | P-02 patch |
| EC-02 — Explore clears the pending correlation needed for another response | `false` — each correlation permits one response; after Explore, clearing that consumed upstream correlation allows a newly issued correlation to continue the same still-fresh Home object. | Rejected |
| EC-03 — a rejection becomes JSON-RPC after consuming authority | `false` — explicit Standard rejections raise `BridgeRequestRejected`, which the endpoint converts to typed unavailability and cleans up; accepted-false results are stopped by Standard validation first. | Rejected |
| VG-01 — late response without an intervening event lacks a test | `medium` — existing expiry tests can pass if the authorization-time clock check is removed; no test currently submits directly after the 300-second deadline. | P-03 patch |
| VG-02 — mismatched public object/freshness IDs lack rejection tests | `medium` — existing response tests use matching IDs, so removal of either equality check would go undetected despite permitting a stale or foreign choice identity. | P-04 patch |
| VG-03 — successful Standard Explore dispatch lacks a gateway test | `medium` — endpoint coverage uses a fake bridge and the Standard gateway test covers only `prompt.choose`, leaving the `prompt.explore` method mapping unverified. | P-05 patch |
| VG-04 — claim revision change after issue lacks a response test | `medium` — existing coverage checks eligibility resolution but does not change the grant revision between event delivery and response to prove no action reaches the gateway. | P-06 patch |

Patch groups after individual verdicts:

- **P-01 — echo the caller's handle for mismatched-handle unavailability.** Member: BH-01.
- **P-02 — admit a new typed-choice revision when its source identity changes.** Member: EC-01.
- **P-03 — test direct response after Home's freshness deadline.** Member: VG-01.
- **P-04 — test mismatched public object and freshness IDs.** Member: VG-02.
- **P-05 — test successful `prompt.explore` gateway dispatch.** Member: VG-03.
- **P-06 — test response revalidation after a claim revision change.** Member: VG-04.
- **P-07 — check the per-turn revision cap before replacing current authority.** Member: BH-07.

## Design Notes

Use the existing TUI shape: `prompt_kind: "choice"`, `choice.object_id`, opaque `choice.freshness`, `option_id`, and explicit `choose`/`explore` operations. Home assigns freshness per object revision, keeps one current object per active turn, invalidates the previous revision when replaced, and preserves the object across exploration until replacement or expiry. Use each pending prompt correlation as the single-response identity; another Explore needs a newly issued correlation. The 300-second TTL is the selected safe default: it matches the TUI prompt wait and leaves Home as the clock authority.

The pinned Standard contract supports approval, clarify, secret, and sudo prompts only; it has no typed-choice event or response method. This Home story will add the authority and capability gate but will not edit Hermes Agent. Home must fail closed when the upstream capability is absent. The integration test proves Home behavior against a capable fake; it does not claim a live end-to-end action against the current Standard server. A separate protocol delivery remains necessary for production use.

Planning facts: the only user-visible policy selected from the unattended recommendations is a 300-second, non-renewing freshness window. No irreversible data migration or deletion is planned; choice state is ephemeral and tied to the live turn. The footprint is the Home Standard/endpoint bridge, focused tests, the Home WebSocket contract, and local story/validation records.

## Verification

**Commands:**
- `uv run --python 3.14 --locked --extra dev pytest -q tests/test_standard_bridge.py tests/test_bridge_endpoint.py tests/test_diagnostics.py` — expected: all focused choice and regression tests pass.
- `uv run --python 3.14 --locked --extra dev pytest -q` — expected: full Home suite passes.
- `uvx ruff check src tests` and `uvx ruff format --check src tests` — expected: clean.
- `git diff --check` — expected: no whitespace errors.
