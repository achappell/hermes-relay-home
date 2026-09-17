---
title: 'HOME-NW-05 — Add household Profile mappings and conversation claims'
type: 'feature'
created: '2026-09-16'
status: 'done'
baseline_commit: 'eb950026ce591179c0d2c4c6069c01c09cfc3fee'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/_bmad-output/specs/spec-profile-mapping-conversation-claims/SPEC.md'
  - '{project-root}/_bmad-output/specs/spec-profile-mapping-conversation-claims/state-machine.md'
  - '{project-root}/docs/contracts/v1/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-04-route-roaming-identity-proof.md'
---

# Implementation Spec: HOME-NW-05

## Intent

**Problem:** Home has mapping labels but no phrase-to-Profile authority. Each Device has one Profile, paired credentials authorize Rooms and capabilities only, arbitration shares one pending round, and the bridge uses operator-managed grants.

**Approach:** Put available Profiles and household-wide phrase mappings in the revisioned Home configuration. Add exact Wake Mapping IDs to paired-device scope; expand “all current Profiles” at approval time. Arbitrate independently per Room, then bind each winner to one Profile, Standard Session, and opaque conversation handle. Keep wake claims content-free and Profile-free.

## Boundaries & Constraints

**Always:** One normalized phrase resolves to one available Profile across the household. Validate device, current mapping, exact credential grant, Room, and capability before arbitration. Keep the 250 ms monotonic window, acoustic ranking, per-Room priority, and no-loser-promotion rule. Active conversations retain their original Device, Room, Mapping, Profile, and Session through edits. Follow-ups reuse that Session. Start the configurable 8-second idle timer after playback acknowledgement, reset after non-empty follow-ups, and suspend it during capture, playback, and in-flight turns. Stop, disconnect, expiry, or explicit revocation closes reachable work and ignores late frames. Return only safe ready/unavailable state and an opaque handle.

**Never:** Guess or fall back to another mapping/Profile; persist an all-mappings wildcard; promote a loser; join Rooms; retarget an active conversation; replay an uncertain turn; expose Profile/Session IDs, Hermes credentials, prompts, transcripts, or audio; add a wake detector, new Hermes channel, UI, or sibling-repository change.

**Compatibility decision:** Evolve /api/v1/configuration in place. There are no deployed clients. Legacy SQLite snapshots cannot be converted safely; require a trusted publish in the new shape and never infer a Profile from a Device or mapping label.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior | Failure handling |
|----------|--------------------|------------------|
| Duplicate phrase targets | Reject publication if a normalized phrase could select different Profiles; keep the prior revision | Stable invalid-configuration response |
| Authorized wake | Admit only a current mapping in the device’s exact grant, Room, capability, and availability boundary | Stale/unauthorized mapping is denied and requires snapshot refresh |
| Two Rooms wake | Arbitrate independently; each Room may grant one claim | Winner failure never promotes a loser |
| Session unavailable | Fail this wake without joining another Session or retrying | Safe unavailable reason; no capture/turn |
| Active follow-up or edit | Reuse the bound Session; edits affect future wakes only | Other mappings cannot retarget the claim |
| Close or idle deadline | Close on stop, disconnect, revocation, or safe expiry | Do not expire during capture, playback, or a turn; ignore late frames |

## Code Map

- src/hermes_home/domain/configuration.py and docs/contracts/v1/configuration.schema.json — Profile availability, phrase targets, Device Room/priority, and reference validation.
- src/hermes_home/domain/credentials.py and src/hermes_home/api/application.py — exact mapping grants, selected/all-current approval, and authorized revisioned device snapshots.
- src/hermes_home/domain/arbitration.py — pending rounds partitioned by Room; retain timing, acoustic ranking, priority, and terminal loser behavior.
- src/hermes_home/bridge/standard.py, endpoint.py, production.py, and runtime.py — replace static grants with claim-bound Standard Sessions and lifecycle handling.
- tests/test_configuration_validation.py, test_credentials*.py, test_arbitration.py, test_api_application.py, test_bridge_endpoint.py, test_production_bridge.py, test_standard_bridge.py, and test_runtime.py — deterministic authorization, isolation, lifecycle, and redaction coverage.
- Canonical SPEC.md and state-machine.md remain authoritative for intent.

## Tasks & Acceptance

**Execution:**
- [x] Evolve the revisioned config for available Profiles, household phrases, and Device Room/priority; reject ambiguous phrases and invalid references atomically.
- [x] Store exact mapping grants in credential scope; expand all-current approval to IDs at that revision. Old scopes with no mappings grant none.
- [x] Serve paired devices only their authorized, revisioned mappings; require refresh after stale rejection.
- [x] Partition arbitration by Room without changing the 250 ms window, evidence ranking, priority, or no-promotion behavior.
- [x] Bind each winner to an independent Standard Session and opaque handle; remove operator-managed grant authority.
- [x] Add follow-up, capture/playback activity, idle expiry, stop, disconnect, revocation, and late-frame safety.
- [x] Update Home contracts, tests, story records, and validation. Do not touch sibling repositories or the paused external board.

**Acceptance Criteria:**
- Aliases resolve to one available Profile; ambiguous phrase publication is rejected.
- A selected grant authorizes only its mapping IDs. All-current approval stores a finite snapshot and does not grant later mappings.
- Same-Room arbitration grants at most one claim; different Rooms proceed independently; failure never promotes a loser.
- A winner receives an independent Session bound to its resolved Profile and only an opaque handle.
- Follow-ups retain their binding; new wakes use current config. Close/revocation/expiry never replays or crosses Rooms.
- Stale, removed, unauthorized, unavailable, or malformed state fails closed without fallback, capture, turn, or content leakage.

## Implementation Notes

- Configuration is one revisioned SQLite snapshot with atomic compare-and-swap. Credential scopes contain exact mapping IDs; an old scope without that field grants no mappings.
- The all-current approval option expands to the active mappings of currently available Profiles at approval time. Device configuration reads return only authorized active mapping IDs and phrases.
- Legacy snapshots return `configuration_migration_required` and require a trusted publish in the new shape. No Profile is inferred from an old Device `profile_id` or mapping label.
- The persistent conversation store binds each winning device, Room, mapping, Profile, credential generation, and Standard Session. It closes active claims immediately after endpoint, Profile, or mapping revocation; mapping remaps and removals preserve existing bindings.
- A granted claim that is not opened expires after 90 seconds and releases its Room. This first-open deadline is cleared on open and is separate from disconnected-turn reconnect grace.
- Content-free activity events cover capture, turn, playback, and playback completion. The configurable idle timer defaults to 8 seconds after playback completion. Route reconnect resumes the same claim and Session; process restart closes active claims.
- Windows deployment no longer creates an operator-managed grants file; claims and Session bindings live in Home SQLite.

## Spec Change Log

- 2026-09-16 — Drafted from the canonical HOME-NW-05 specification; v1 evolves in place because no clients are deployed.
- 2026-09-16 — Implemented Profile mappings, exact grants, per-Room arbitration, durable conversation claims, bridge lifecycle handling, v1 contract updates, and validation.
- 2026-09-16 — Applied all fourteen code-review patches and reran the focused and full test suites plus required lint and format checks.

## Review Triage Log

### Review Findings

#### Decision resolved

- [x] [Review][Decision] Define expiry before a claim's first open — A granted handle is stored as active with no idle_deadline and no timer, so a handle the endpoint never opens can reserve its Room indefinitely. The separate pilot-session worktree has a 90-second recovery grace after a live turn loses its socket; that window does not expire unopened claims. **Decision:** expire a claim 90 seconds after grant if it has not opened, releasing its Room reservation.

#### Patch

- [x] [Review][Patch] Expire and release unopened claims 90 seconds after grant; keep this deadline separate from the pilot's disconnected-turn reconnect grace
- [x] [Review][Patch] Return configuration_migration_required with the current revision from wake arbitration on legacy snapshots [src/hermes_home/domain/arbitration.py:121]
- [x] [Review][Patch] Close a newly inserted claim if credentials were revoked after the last pre-insert check [src/hermes_home/api/application.py:872]
- [x] [Review][Patch] Interrupt live Standard work when revocation closes its durable claim [src/hermes_home/bridge/production.py:391]
- [x] [Review][Patch] Prevent post-deadline activity from clearing an expired idle deadline [src/hermes_home/bridge/production.py:250]
- [x] [Review][Patch] Interrupt a created Standard Session when persisting its claim binding fails [src/hermes_home/bridge/standard.py:911]
- [x] [Review][Patch] Make configuration publication and claim-revocation cleanup recoverable after a committed write [src/hermes_home/api/application.py:727]
- [x] [Review][Patch] Enforce the installer's 600-second idle-timeout maximum in runtime settings [src/hermes_home/runtime.py:386]
- [x] [Review][Patch] Return not_found for unknown mappings and reserve stale_mapping for inactive mappings; document and test both outcomes [src/hermes_home/domain/arbitration.py:329]
- [x] [Review][Patch] Close the local claim when explicit close arrives while Standard is unavailable [src/hermes_home/bridge/endpoint.py:457]
- [x] [Review][Patch] Reject wake phrases that normalize to an empty string [src/hermes_home/domain/configuration.py:127]
- [x] [Review][Patch] Add a full-width/ASCII NFKC wake-phrase collision test [tests/test_configuration_validation.py:64]
- [x] [Review][Patch] Test that an unavailable Profile is denied at arbitration [tests/test_arbitration.py:264]
- [x] [Review][Patch] Test that a device without wake_claim scope cannot read its configuration snapshot [src/hermes_home/api/application.py:416]

#### Rejected

- false — Temporary diff headers used an absolute path for untracked files, but the actual source, spec, and validation files are at their correct repository-relative paths.
- false — Static credentials have no exact Wake Mapping grant, so denying their wake claims is the required fail-closed result; no mapping permission is implied.
- false — The enrollment grant parser catches TypeError and ValueError and returns 400 invalid_request; malformed selection does not escape as an unhandled error.
- false — A claim admitted before a config edit uses its admission snapshot, and opening it rechecks Profile availability and mapping activity before creating a Session. Revoked Profiles or inactive mappings therefore do not start a Session.
- false — The verification-gap report's static-scope finding is the same intentional empty-grant behavior; no unintended claim is authorized.
- low — The broad HTTP metric prefix can mislabel credential routes, but only skews telemetry labels and fixing it requires extra route classification.
- low — Closed conversation rows accumulate, but no retention policy is specified and safe cleanup needs an explicit replay/history policy.
- low — The edge-case retention report repeats the same gradual SQLite growth; no retention policy is specified and safe cleanup needs an explicit replay/history policy.

## Design Notes

Config owns Profile availability and phrase meaning; credential scope owns per-device mapping grants. Active conversations copy their resolved binding so later edits affect only new wakes. Legacy configuration has no safe semantic conversion.

## Verification

See `_bmad-output/implementation-artifacts/validation-home-nw-05.md` for the
exact focused/full test, Ruff, lockfile, and whitespace-check results.
