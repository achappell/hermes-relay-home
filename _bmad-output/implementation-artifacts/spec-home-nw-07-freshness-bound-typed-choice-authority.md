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
