---
title: 'HOME-NW-01: Pin and prove the Standard Hermes compatibility boundary'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_revision: 'f220265eb1fe98326728930bedece37726fbbf05'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - _bmad-output/specs/spec-standard-hermes-compatibility-migration/SPEC.md
  - _bmad-output/specs/spec-standard-hermes-compatibility-migration/standard-baseline.md
  - _bmad-output/specs/spec-standard-hermes-compatibility-migration/surface-migration-matrix.md
  - _bmad-output/specs/spec-standard-bridge/SPEC.md
  - _bmad-output/specs/spec-standard-bridge/transport-contract.md
  - src/hermes_home/bridge/standard.py
warnings: []
deferred:
  - summary: >-
      Decide whether Home should consume Standard event sequence/cursor replay after reconnect.
    evidence: |-
      The pinned Standard release exposes session.events.since and monotonic event sequences, but this story forbids replaying uncertain prompts and prior responses. The existing bridge deliberately discards stale sequenced events until a later route-level contract chooses whether missed non-response events should be replayed.
    location: >-
      _bmad-output/specs/spec-standard-bridge/SPEC.md:176; _bmad-output/implementation-artifacts/deferred-work.md:1-3
    severity: medium
---

<intent-contract>

## Intent

**Problem:** Home has a substantial Standard Hermes bridge and deterministic
fixtures, but the compatibility record is not formally closed and some
positive fixtures model a command list in `gateway.ready` that the pinned
Standard release does not send. That makes the bridge appear more compatible
than the wire contract proves.

**Approach:** Align command capability discovery and its tests with the pinned
Standard gateway, add the missing pin-shaped audio/prompt edge evidence, and
record a reviewable validation artifact. Preserve the existing Home bridge,
opaque endpoint boundary, and deterministic fake-port strategy.

## Boundaries & Constraints

**Always:** Use Standard Hermes `0.21.1` at commit
`2237be355906fbe6065ce1815711eee52b2d646e`; keep the Hermes credential
server-side; preserve JSON event identity/order, separate signed-16
little-endian PCM, structured-prompt correlation, confirmed interruption,
bounded waits, explicit timing absence, and no replay after uncertainty.

**Never:** Add a Hermes channel, fork-only acceptance behavior, network-arrival
timing, endpoint Profile/session authority, live deployment or hardware tests,
front-end migrations, route roaming, or automatic replay. The existing
reconnect cursor/replay policy remains an explicitly recorded later decision.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|-----------------------------|----------------|
| COMMAND_CATALOG | Ready event has no command list; Standard catalog advertises one command | Home exposes only the catalog-advertised command and dispatches the explicit Standard operation | Missing or rejected catalog leaves commands unavailable and rejects dispatch by capability; never guess a command |
| AUDIO_FALLBACK | Text completes while the separate audio socket returns `fallback` | Text remains usable and audio is reported as unavailable/ended without fabricated PCM | Invalid metadata or bytes close the sidecar with a typed protocol failure |
| STRUCTURED_PROMPT | Correlated Standard prompt carries sensitivity and active-turn identity | Home preserves correlation, sensitivity, and terminal resolution at its bridge boundary | Expired, stale, or wrong-type responses are rejected without changing the turn |
| TRANSPORT_LOSS | Gateway or audio transport ends during or after a turn | Home reports bounded transport state and resumes only the existing conversation | Uncertain input is never resent; a fresh user action is required |

</intent-contract>

## Code Map

- `src/hermes_home/bridge/standard.py` — `StandardGatewayClient` owns the one
  JSON-RPC reader and bounded request/event waits; `HomeBridge` maps grants,
  text, audio, prompts, commands, interruption, and reconnect. `_capabilities`,
  `dispatch_command`, `_open_audio`, and `_next_audio` are the compatibility
  seams to adjust or verify.
- `tests/test_standard_bridge.py` — deterministic JSON/audio socket fakes and
  the existing coverage for session, text, PCM, prompts, commands, interrupt,
  reconnect, and failure semantics. Extend these fixtures with the pinned
  catalog and fallback shapes rather than adding a second transport harness.
- `tests/test_standard_compatibility_artifacts.py` — executable checks for the
  pinned release, endpoint split, surface matrix, and rollback boundary; add
  checks for the validation record and provenance without duplicating bridge
  behavior tests.
- `_bmad-output/specs/spec-standard-hermes-compatibility-migration/` —
  canonical release pin, surface requirements, and rollout/rollback rules;
  these remain read-only planning authority for this implementation.
- `_bmad-output/specs/spec-standard-bridge/transport-contract.md` — current
  Home bridge fixture table and failure vocabulary; correct its command
  readiness example to match the pinned catalog boundary.
- `_bmad-output/implementation-artifacts/validation-home-nw-01.md` — record
  observed focused/full tests, lint/format results, the immutable baseline,
  covered capabilities, and explicit non-claims.

## Tasks & Acceptance

**Execution:**
- `src/hermes_home/bridge/standard.py` — derive command availability from the
  pinned Standard catalog/advertisement boundary and keep dispatch explicit and
  fail-closed — avoid accepting a fixture-only `gateway.ready` shape.
- `tests/test_standard_bridge.py` — add pin-shaped command-catalog, audio
  fallback, and prompt/terminal edge fixtures — prove the corrected behavior at
  the bridge seam.
- `tests/test_standard_compatibility_artifacts.py` — assert immutable source
  provenance and validation-artifact coverage — keep the closure evidence
  executable.
- `_bmad-output/specs/spec-standard-bridge/transport-contract.md` — document
  the pinned command-catalog shape and normalized Home capability result — keep
  the implementation and contract from drifting apart.
- `_bmad-output/implementation-artifacts/validation-home-nw-01.md` — record
  observed verification and non-claims — make completion auditable.
- `_bmad-output/implementation-artifacts/story-index.yaml` and
  `sprint-status.yaml` — link the validation artifact and mark HOME-NW-01 done
  only after implementation and review evidence pass — keep local status
  authoritative.

**Acceptance Criteria:**
- Given the pinned Standard ready and catalog responses, when Home opens and a
  command is requested, then only an explicitly catalog-advertised command is
  exposed and dispatched through `command.dispatch`.
- Given a separate audio socket returns valid start/PCM/end or fallback frames,
  when Home consumes the turn, then PCM metadata and bytes remain ordered and a
  fallback never removes usable text or invents audio.
- Given a correlated structured prompt, interrupt, reconnect, or transport
  failure, when the bridge maps it, then identity, sensitivity, terminal state,
  bounded readiness, and no-replay semantics remain observable and typed.
- Given the repository verification commands complete, when the validation
  artifact is reviewed, then it names the immutable Standard commit, records
  the observed results, and distinguishes deterministic bridge proof from live
  Hermes, route-roaming, hardware, and front-end evidence.
- Given all acceptance checks pass, when local delivery status is updated, then
  HOME-NW-01 is `done` and no unrelated worktree edits or secrets are staged.

## Design Notes

The existing bridge already keeps runtime Hermes Session IDs and bearer tokens
out of endpoint payloads. The correction must preserve that boundary. Standard
command discovery is a capability lookup, not a reason to expose arbitrary
Hermes methods; an unavailable or rejected catalog is safer than a guessed
command list. The pinned bridge story explicitly accepts deterministic fake
ports and excludes live deployment validation.

## Verification

**Commands:**
- `PYTHONPATH="$PWD/src" uv run --isolated --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_standard_bridge.py tests/test_standard_compatibility_artifacts.py` — expected: all focused compatibility tests pass.
- `PYTHONPATH="$PWD/src" uv run --isolated --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` — expected: full Home suite passes.
- `uvx --from ruff ruff check src tests` — expected: all checks pass.
- `uvx --from ruff ruff format --check src tests` — expected: all files formatted.
- `uv lock --check` — expected: lockfile is current.
- `git diff --check` — expected: no whitespace errors.

## Spec Change Log

### 2026-09-15

- Added isolated, worktree-source verification commands so recorded checks do
  not import an editable package from another checkout.
- Recorded the Standard event sequence/cursor replay decision as deferred work;
  the current story continues to forbid replay of uncertain prompts and prior
  responses.

## Review Triage Log

### 2026-09-15 — Review pass

- verdicts: 19 findings — high 0, medium 0, low 10, false 9, maybe-false 0
- findings:
  - `[low]` `[patch]` Duplicate closing `</intent-contract>` marker — removed the second marker so the intent block has one unambiguous closing boundary.
  - `[false]` `[reject]` Story index still points HOME-NW-01 at the broad migration spec — the canonical migration spec remains the story scope authority, and the new validation artifact is now linked from the story entry.
  - `[false]` `[reject]` Sprint status remains `ready-for-dev` during the review snapshot — delivery status is intentionally updated only after implementation and review evidence pass.
  - `[false]` `[reject]` Catalog lookup before Profile binding could select the wrong command set — the pinned `commands.catalog` handler reads registry/configuration state without a session or Profile parameter, so the cited non-default grant outcome is not demonstrated.
  - `[false]` `[reject]` Malformed catalog data makes a healthy commandless connection — malformed pairs are converted to an empty capability set, and the dispatch gate rejects the command; the optional command capability remains fail-closed as specified.
  - `[low]` `[patch]` No regression for a command present only in `gateway.ready` — added an empty-catalog fixture and asserted that the stale command is neither exposed nor dispatched.
  - `[low]` `[patch]` Pinned baseline prose omitted the `commands.catalog` wire shape — documented the `pairs` arrays and Home normalization rule in `standard-baseline.md`.
  - `[low]` `[patch]` Validation record omitted exact commands and tested-source provenance — added the command table and stated the baseline-plus-working-tree relationship without claiming a live Hermes run.
  - `[low]` `[patch]` Compatibility-artifact test only checked loose substrings — added checks for status, revisions, required sections/results, story-index linkage, and the route contract.
  - `[low]` `[patch]` Deferred replay policy was not represented in the spec metadata — added one structured deferred item with evidence, location, and severity.
  - `[false]` `[reject]` Review logs were empty in the captured diff — the capture preceded this required triage entry; this pass now records the findings and actions.
  - `[low]` `[patch]` Reconnect catalog adoption lacked consumer proof — extended the reconnect fixture to assert the resumed catalog capability and successful dispatch.
  - `[low]` `[patch]` Malformed catalog pairs lacked explicit coverage — added parameterized short-pair, missing-slash, and non-text-description fixtures that fail closed.
  - `[low]` `[patch]` Route contract retained stale `gateway.ready` command wording — updated both the operation list and dispatch description to name `commands.catalog`.
  - `[false]` `[reject]` Deterministic fixtures do not bind runtime code to an upstream checkout — the captured intent explicitly requires deterministic fake-port evidence and excludes live deployment validation; the artifact names the immutable source used for wire comparison.
  - `[false]` `[reject]` Changed tests do not exercise the public opaque endpoint — the intent preserves the existing endpoint boundary and explicitly excludes front-end/route work from this story; HOME-NW-03 owns the live local route.
  - `[false]` `[reject]` The diff does not mark the story done — the review workflow requires `in-review` until this pass finishes, after which finalization updates local status.
  - `[false]` `[reject]` Newly added hunks do not duplicate every existing invalid prompt/audio case — the intent asks for missing pin-shaped evidence, while the pre-existing bridge suite already covers the listed invalid, stale, interrupt, reconnect, and transport cases.

## Auto Run Result

### Summary

HOME-NW-01 now derives Home command capabilities from the pinned Standard
`commands.catalog` `pairs` response, rejects stale or malformed command
advertisements, and proves the boundary with deterministic bridge fixtures.
The pinned audio fallback and structured-prompt evidence are recorded without
changing the existing opaque Home boundary or no-replay recovery policy.

### Files changed

- `src/hermes_home/bridge/standard.py` — discover and normalize Standard command capabilities.
- `tests/test_standard_bridge.py` — add catalog, reconnect, malformed-pair, fallback, and prompt evidence.
- `tests/test_standard_compatibility_artifacts.py` — verify provenance and cross-document evidence links.
- `_bmad-output/specs/spec-standard-hermes-compatibility-migration/standard-baseline.md` — record the pinned catalog wire shape.
- `_bmad-output/specs/spec-standard-bridge/transport-contract.md` — align the fixture contract with Standard.
- `_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md` — align the future route wording.
- `_bmad-output/implementation-artifacts/validation-home-nw-01.md` — record commands, results, provenance, and non-claims.
- `_bmad-output/implementation-artifacts/story-index.yaml` — link the validation artifact.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — mark HOME-NW-01 done.

### Review findings breakdown

- 19 findings were triaged: 10 low direct patches, 9 false/rejected findings,
  and no high, medium, or maybe-false findings.
- The 10 patches corrected the intent-block marker, catalog negative and
  reconnect coverage, malformed-pair coverage, baseline and route prose,
  validation provenance, artifact assertions, and deferred metadata.
- One residual decision remains deferred: whether Home should consume Standard
  event sequence/cursor replay after reconnect. It is recorded in frontmatter
  with evidence and a follow-up location.
- Follow-up review recommendation: `false`; no high or medium patch was found.

### Verification performed

- Focused bridge and compatibility-artifact tests: `82 passed in 0.20s`.
- Full Home test suite: `214 passed in 3.23s`.
- Ruff lint: `All checks passed!`.
- Ruff format: `38 files already formatted`.
- Lockfile: current; `uv` resolved 11 packages.
- `git diff --check`: passed with no output.

### Residual risks

This story does not claim a live Home-to-Hermes run, front-end migration,
route roaming/browser bootstrap, or hardware validation. Those remain later
surface-owned gates. Standard event replay after reconnect remains explicitly
undecided; uncertain prompts and prior responses are still never replayed.
