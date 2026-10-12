---
title: 'HOME-TC-B1 (placeholder ID; owner assigns) — Let a Room-less paired client opt in to typed choices'
type: 'feature'
created: '2026-10-11'
status: 'in-review'
baseline_commit: '1f7052409b7bcf22d016eaa91b838bd9c2dd389c'
route: 'dispatch'
review_loop_iteration: 0
source_story: 'HOME-TC-B1 (placeholder; no story ID, issue, or tracker row exists yet)'
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/docs/contracts/v1/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-07-freshness-bound-typed-choice-authority.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-17-browser-admission.md'
  - '{project-root}/_bmad-output/implementation-artifacts/home-tc-b3-live-proof-and-sequencing.md'
---

> **Placeholder IDs.** `HOME-TC-B1`, `HOME-TC-B2`, and the B3 live proof are working names. The owner assigns real story IDs, issues, and `story-index.yaml` / `sprint-status.yaml` rows. This spec adds none of them. Slices are B1 -> B2 -> B3; this is B1. **Approval:** director review of the spec in place of the owner checkpoint (the owner authorized it); owner to correct.

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Home grants a conversation `interactive_choice` only from a Room Device entry in `devices[]` (`production.py` `resolve`). A paired client such as the browser appliance holds exactly `client_claim`, has no Room, and so can never be interactive. A dummy Room entry would put a Room-less client in the wake registry. Separately, the existing revision check cannot be reused for client claims as written (see Design Notes).

**Approach:** Add an optional, default-off, admin-set allowance for paired client Devices in Home configuration, keyed by Device ID. A client claim's `ConversationGrant.interactive_choice` is true only for an opted-in Device whose claim was made under the Device's current configuration projection. The browser credential scope stays exactly `client_claim`. B1 changes no event projection, no Standard operation, and no capability that is advertised today.

**Owner decisions recorded (2026-10-11):** D2 = A (per-client-device opt-in in Home configuration, default off, set by admin `PUT /api/v1/configuration`; a client claim can be interactive without a Room; browser credential scope unchanged; additive contract amendment; a capability or revision change leaves the claim open but unadvertised, as for Room Devices). D5 = A (browser appliance first; the mechanism is per Device ID, so TUI, iOS, and Android can follow without a schema change).

## Boundaries & Constraints

**Always:**
- Default off: no `client_devices` key, no entry, or `interactive_choice: false` yields `interactive_choice=False` for every client claim, byte-identical to today.
- Keep Room-claim resolution untouched. A Room claim reads only `devices[]` for `interactive_choice`. A client claim reads only `client_devices` for `interactive_choice`.
- The resolver fails closed to `interactive_choice=False`. It never returns `None` (which closes the claim as `stale_conversation`) and never raises out of `resolve` because of this lookup.
- `client_devices` is administered only through `PUT /api/v1/configuration` (admin credential). It is never returned by a Device-authenticated route.
- Omit `client_devices` from a normalized snapshot when it is empty, so an unchanged Home round-trips GET -> PUT with identical bytes and `validate_snapshot(fixture) == fixture` still holds.
- Protected capabilities (`sensitive_entry`, `consequence_confirm`) derivation is unchanged for every claim; `client_devices` grants none.

**Never:**
- Widen the browser credential scope, add a credential-scope capability, or touch pairing/admission code.
- Allow an ID in both `devices[]` and `client_devices`.
- Accept any capability key other than `interactive_choice` in a `client_devices` entry.
- Add a route, a SQLite column or migration, a per-claim opt-in, or a client-visible disclosure of the allowance.
- Advertise `prompt.choose` / `prompt.explore` differently from today. Standard still supplies those flags; projection is B2.
- Touch the TUI, iOS, Android, Pixel, hardware, or any running service.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| DEFAULT_OFF | Existing snapshot with no `client_devices` | Reads, normalizes, and round-trips unchanged; every client claim `interactive_choice=False` | N/A |
| OPTED_IN | `client_devices` has `{id: D, interactive_choice: true}`; claim of D made under D's current projection | `resolve` returns `interactive_choice=True`; open/reconnect handshake advertises the Standard-supplied choice flags (none today) | N/A |
| EXPLICIT_FALSE | Entry with `interactive_choice: false` | Same as default off; entry is kept in the snapshot | N/A |
| OPT_IN_REMOVED | Admin PUT drops D's entry (or sets false) while claim is open | Claim stays open; next `resolve` returns False; typed-choice responses are refused `capability_unavailable`; next open/reconnect is unadvertised | N/A |
| REVISION_CHANGED | Snapshot publish, or a grant change for D, after the claim was made | Claim stays open; `resolve` returns False until D refetches configuration and makes a new claim | N/A |
| GRANT_ADDED_DRIFT | D's projected revision is ahead of the global revision (grant change after first fetch) and D is opted in | Still True: comparison uses D's projection, not the global revision | Regression test required |
| PROJECTION_UNREADABLE | Credential store read fails, or no resolver is wired | `interactive_choice=False`; claim unaffected | Fail closed; never `None` |
| ROOM_CLAIM | Room claim whose Device ID also appears in `client_devices` | Ignored; Room path only, from `devices[]` | N/A |
| OVERLAP | ID present in both `devices[]` and `client_devices` | PUT rejected `400 invalid_request`; nothing stored | `ConfigurationValidationError` |
| BAD_ENTRY | Extra key, non-bool flag, duplicate ID, ID not 1-128 chars, `client_devices` not an array | PUT rejected `400 invalid_request` | Same |
| UNKNOWN_DEVICE | Entry for an ID that never paired | Accepted; no effect | N/A |
| OLD_HOME | `client_devices` PUT to a pre-B1 Home | `400 invalid_request` (unknown key); no state change | Safe failure |

</frozen-after-approval>

## Open Questions

All four were adopted at their provisional answers under the director review (see Implementation Notes); none blocks.

- **Revision gate for client claims. DECIDED (A):** compare the claim's `configuration_revision` with the Device's current projected revision through a read-only credential-service peek.
- **Retain explicit `false` entries? DECIDED (A):** keep as given.
- **Entry shape. DECIDED (A):** `{id, capabilities: {interactive_choice}}`.
- **Display name. DECIDED (A):** none.

## Code Map

Anchors verified against `1f70524` (the readiness note's line numbers still match).

- `src/hermes_home/bridge/production.py` L586-618 (`ConversationGrantStore.resolve`): the only place `interactive_choice` is computed. L589 `devices.get(device_id)`; L614-618 `snapshot.get("revision") == row[9] and device ... .get("interactive_choice") is True`. `row[10]` is `claim_kind` (`'client'` for client claims); `row[9]` is the claim's `configuration_revision`. L590-613 protected capabilities (keep). L631 broad `except KeyError, RuntimeError, TypeError, ValueError: return None` — the new lookup must sit inside its own guard so it cannot reach this handler.
- `src/hermes_home/bridge/production.py` L535 `set_client_grant_checker` — pattern for the new `set_client_revision_resolver`. `src/hermes_home/runtime.py` L405-429 wires the store and the checker; add the resolver beside it.
- `src/hermes_home/domain/credentials.py` L1134-1161 `CredentialService.client_configuration(device_id, revision)`: a `mutate()` that records `fingerprint=[revision, [[grant_id, status], ...]]` and returns `revision` or `max(revision, previous + 1)`. This is the revision a client sends in `POST /api/v1/client-claims` (`application.py` L1478-1497: `configuration_revision != current_revision` -> `409 stale_configuration`) and the value stored as the claim's `configuration_revision`. Add a read-only sibling `client_configuration_revision(device_id, revision) -> int | None`: return the stored projection revision only when its stored fingerprint equals the current one, else `None`; never write. `read_state()` exists on both stores (`storage/credentials.py` L51, L102).
- `src/hermes_home/domain/configuration.py`: L8-9 `CONFIGURATION_KEYS` / `SNAPSHOT_KEYS` (exact-key checks at L24-40, L42-61); optional-field precedent `_validate_profiles` `shared` (L82-119); `_validate_devices` L179-271 (capability allowlist L205-222). Add optional `client_devices` handling and `_validate_client_devices(value, devices)` for uniqueness, the `devices[]` overlap rule, and the capability allowlist.
- `src/hermes_home/storage/sqlite.py` L62-84 (`read`) and L86-124 (`replace`): persist whatever `validate_candidate` returns; no change expected beyond the domain change. L73-80 legacy-shape check is unaffected.
- `src/hermes_home/api/application.py` L1143-1210 (`_put_configuration_authenticated`; `validate_candidate` L1163 when a credential service exists), L1010-1028 (`_get_configuration`), L549-607 (`_get_device_configuration`: must NOT expose `client_devices`; it builds its own dict, so no change).
- `docs/contracts/v1/configuration.schema.json`: top-level `additionalProperties: false`; add `client_devices` (not in `required`) and a `client_device` `$def`.
- `docs/contracts/v1/README.md` L28-67 (Configuration, `interactive_choice` paragraph L64-67) and L357-416 (Client admission, scope sentence L410-415).
- `_bmad-output/specs/spec-home-bridge-route-roaming/bridge-contract.md` L302-309: capability exposure paragraph.
- Not changed: `standard.py` (`ConversationGrant` L172-187, `_capabilities` L3430-3459, open L1086, reconnect L1369, `_respond_prompt` gate L2005-2027, `choice_capabilities` L2527-2540): B1 only changes the grant flag that these already consume.

## Tasks & Acceptance

**Execution:**
- [ ] `src/hermes_home/domain/configuration.py` -- accept optional `client_devices`; validate shape, uniqueness, `devices[]` disjointness; omit the key from normalized output when empty -- additive, default off
- [ ] `docs/contracts/v1/configuration.schema.json` -- add `client_devices` and `$defs/client_device` with the description text below -- published contract parity
- [ ] `src/hermes_home/domain/credentials.py` -- add read-only `client_configuration_revision` -- lets the grant resolver compare against the projection clients actually use, without a write per `resolve`
- [ ] `src/hermes_home/bridge/production.py`, `src/hermes_home/runtime.py` -- add `set_client_revision_resolver`; compute client-claim `interactive_choice` from `client_devices` + projection match inside a fail-closed guard; leave Room path and protected capabilities untouched -- D2
- [ ] `docs/contracts/v1/README.md`, `bridge-contract.md` -- apply the amendments below -- contract
- [ ] Tests (list below) -- I/O matrix coverage

**Contract amendments (exact text):**

1. `configuration.schema.json` property `client_devices`, description: `"Paired personal-client Devices (credential scope client_claim) that an administrator allows to answer typed choices. Omission denies. An ID must not also appear in devices."` `$defs/client_device`: object, `required: ["id", "capabilities"]`, `additionalProperties: false`; `id` is `$ref #/$defs/id`; `capabilities` is an object, `required: ["interactive_choice"]`, `additionalProperties: false`, `interactive_choice` boolean with description `"Explicitly permits this paired client to answer typed choice prompts. Omission or false denies."` The schema cannot express the `devices[]` disjointness; the README states it and validation enforces it.
2. `README.md`, after the `interactive_choice` paragraph (L64-67): see the merged `docs/contracts/v1/README.md` for the final wording. Final points: entries are Devices paired with `client_claim`; `interactive_choice` is required in an entry and the only permitted capability; `false`, an omitted entry, or an omitted array denies; an ID is not checked against paired Devices at publish; the ID must not also be a Room Device and `devices` never confers `interactive_choice` on a paired client's claim; no Room, wake or arbitration role, credential scope, `sensitive_entry`, or `consequence_confirm`; an empty array is not stored; a claim stops advertising, without closing, while its entry is absent or false or while the Device's configuration projection (snapshot revision plus the Device's grants, including a pending grant that expires) differs from the one the claim was made under.
3. `README.md` Client admission, append to the scope paragraph (after L415): `A browser appliance's scope is unaffected by client_devices: the allowance is Home configuration, not a credential capability, and Home still rejects broader browser requests and approvals.`
4. `bridge-contract.md` L302-305: replace `the bound Home Device has capabilities.interactive_choice: true. That optional Device capability defaults to false` with `the bound Home Device has capabilities.interactive_choice: true, or, for a paired client claim, has a client_devices entry with capabilities.interactive_choice: true whose claim was made under the Device's current configuration projection. Both capabilities default to false`. Leave L306-309 for B2.

**Acceptance Criteria:**
- Given no `client_devices` anywhere, when any claim resolves, then every advertised capability is identical to `1f70524`, except that a paired client's claim no longer takes `interactive_choice` from a `devices[]` entry (only `client_devices` can opt a client in).
- Given an opted-in client Device whose projection has drifted ahead of the global revision, when its claim resolves, then `interactive_choice` is True.
- Given an open claim, when the entry is removed or the projection changes, then the claim stays open and `resolve` reports False.
- Given a resolver or store failure, when `resolve` runs, then the grant is returned with `interactive_choice=False` (not `None`) and no exception escapes.
- Given a Room claim and a client claim for IDs listed in the other registry, when each resolves, then each ignores the other registry.
- Given the PUT bodies in the matrix, when published, then BAD_ENTRY and OVERLAP return `400 invalid_request` and the stored revision is unchanged.

## Design Notes

**The readiness note's mechanism, applied literally, never fires for a real browser.** `resolve` requires `snapshot["revision"] == row[9]`. For a client claim `row[9]` is not the snapshot revision; it is the Device's projected revision from `CredentialService.client_configuration`. Reproduced on 2026-10-11 with the real in-memory `CredentialService` (scratch script, not committed): first fetch at snapshot 5 returns 5; after one grant is added, still at snapshot 5, it returns 6; after a snapshot publish to 6 it returns 7; to 7, it returns 8. The projection stays one or more ahead permanently, so `snapshot.revision == row[9]` is never true again for that Device. A browser appliance gains grants over time (README "Add an exact Profile to an already-paired Device"), so the capability would be silently dead. B1 therefore compares against the projection (Open Question 1).

**Why not reuse `devices[]` with a dummy Room.** `devices[]` requires `room_id` and a unique per-Room `priority` and feeds wake arbitration (`arbitration.py` L159, L323). A dummy entry works mechanically but registers a Room-less client in the wake registry.

**Rollback hazard (flag for the owner).** A pre-B1 Home rejects a stored snapshot that contains `client_devices` (`validate_snapshot` -> `_require_keys` unknown field), which makes `GET /api/v1/configuration`, device configuration, and every wake claim answer 503. Never set an opt-in before B2 and B3 are proven, and publish a configuration without `client_devices` before any rollback of the Home build. The deploy checklist is in the sequencing note.

## Verification

**Commands:**
- `uv run pytest tests/test_configuration_validation.py tests/test_configuration_schema.py tests/test_contract_fixtures.py tests/test_sqlite_configuration_store.py tests/test_api_application.py tests/test_production_bridge.py tests/test_client_grants.py tests/test_runtime.py tests/test_standard_bridge.py -q` -- expected: all pass
- `uv run ruff check src tests` and `uv run ruff format --check src tests` -- expected: clean

**Tests to add or extend (names are proposals):**
- `tests/test_configuration_validation.py`: `test_client_devices_are_optional_and_omitted_when_empty`, `..._reject_overlap_with_room_devices`, `..._reject_duplicate_ids_extra_capabilities_and_non_bool_flags`, `..._keep_explicit_false`.
- `tests/test_configuration_schema.py`: parity cases for each validation case; the schema stays valid Draft 2020-12.
- `tests/test_contract_fixtures.py`: the existing canonical fixture still equals `validate_snapshot(fixture)`; add an inline `client_devices` case.
- `tests/test_sqlite_configuration_store.py`: publish -> reopen -> read round-trips `client_devices`; a legacy snapshot without the key still reads.
- `tests/test_api_application.py`: admin PUT/GET round-trip; non-admin 401; invalid body 400 with revision unchanged; `GET /api/v1/devices/{id}/configuration` does not contain `client_devices`.
- `tests/test_client_grants.py`: peek returns `None` before the first projection, equals the `mutate` result when the fingerprint matches, returns `None` after a grant or snapshot change until refetch, and does not mutate stored state.
- `tests/test_production_bridge.py`: client claim carries the capability when opted in; loses it on opt-in removal, projection change, resolver error, and absent resolver; the drift regression above; Room claim ignores `client_devices`; client claim ignores `devices[].interactive_choice`; protected capabilities stay empty.
- `tests/test_standard_bridge.py`: a client grant with `interactive_choice=True` advertises exactly the Standard-supplied flags; default off hides them (existing `test_bridge_withholds_choices_from_devices_without_interactive_opt_in` stays green).
- `tests/test_runtime.py`: the resolver is wired when a credential service exists.

**Manual checks:** none for B1. Live behavior is observable only after B2; see the sequencing note.

## Implementation Notes

Implemented 2026-10-11 against `1f70524`. Approval: director review of the spec in place of the owner checkpoint (the owner authorized it); the owner is to correct. The four open questions below were adopted at their provisional answers.

- Decisions adopted: revision gate = compare the claim's stored revision with the Device's current projection through the read-only `CredentialService.client_configuration_revision` (option A); explicit `false` entries are kept; entry shape `{id, capabilities: {interactive_choice}}`; no display name.
- Files: `domain/configuration.py` (optional `client_devices`, `_validate_client_devices`, `_require_keys(optional=...)`), `docs/contracts/v1/configuration.schema.json`, `domain/credentials.py` (`client_configuration_revision` and the shared `_projection_fingerprint`), `bridge/production.py` (`set_client_revision_resolver`, `client_revision_resolver=`, `_client_interactive_choice`), `runtime.py` (wiring), `docs/contracts/v1/README.md`, `bridge-contract.md`.
- The default-off path does not touch the credential service: a Device with no `client_devices` entry returns before the resolver is called.
- Protected-capability derivation for client claims is deliberately unchanged (the first draft of the code set `device = None` for client claims, which removed it; review caught that and it was reverted and pinned by tests). Only `interactive_choice` changes: a paired client's claim no longer takes `interactive_choice` from a `devices[]` entry. No deployed behavior depended on that: pinned Standard advertises no typed choice, and it needed a dummy Room entry.
- A projection that changes and then reverts (a grant added, then revoked before the Device refetches) makes the fingerprint match again, so an older claim is advertised again. This is the same authority the claim was made under; the contract text says "differs from the one the claim was made under" rather than "only through a new claim".
- `resolve` now reads credential state twice for an opted-in client (grant check, then the projection peek). Accepted: it already read it once per resolve, the default-off path adds none, and each read is one small SELECT.
- Verification (2026-10-11): full suite `1213 passed, 26 skipped` (baseline `1119 passed, 26 skipped`); `ruff check` and `ruff format --check` clean; new and changed tests run 20 times with no failure and once in reverse file order; the new tests fail on the unchanged source (41 failed, 14 errors before the change).

## Review Triage Log

Three layers (Blind Hunter, Edge Case Hunter, Verification Gap) reviewed the diff on 2026-10-11.

| Finding | Verdict | Evidence and action |
| --- | --- | --- |
| Client claims lose protected capabilities from `devices[]` (`device = None`), raised by all three layers | high | Real: it removed a path a non-browser client with `client_claim` plus `sensitive_entry` could use. Reverted to `devices.get(device_id)`; two tests pin both facts (client_devices grants none; registry still grants as before). |
| README says `interactive_choice` "defaults to false" but the key is required | medium | Real doc contradiction with schema and validator. README reworded: required, `false`/omitted denies. |
| README says entries "hold only" `client_claim`; the ID is not checked at publish | low | Reworded to "paired with the `client_claim` capability" and states an unpaired ID has no effect. |
| Fingerprint duplicated in two functions | low | Real drift risk. Moved to `_projection_fingerprint`, used by both. |
| Two credential-state reads per opted-in resolve under the lock | low | Accepted (see notes); no change. |
| Pending grant expiry drops eligibility, undocumented and untested | low | Real. README names it; `test_projection_peek_drops_when_a_pending_owner_grant_expires` added. |
| Drift regression asserts a truthy value only | low | Real. Now asserts the stored revision equals the projected one. |
| Tracker IDs in source comments | low | Removed from code; they remain in spec and PR text only. |
| Empty `client_devices` is not stored, undocumented | low | README states it. |
| One malformed entry denies the rest | low | Unreachable from a validated snapshot; made per-entry anyway and the test now puts a valid entry after malformed ones. |
| No positive end-to-end test of flags through the bridge | medium | Real. `test_an_opted_in_client_claim_answers_a_typed_choice_through_the_bridge` and the not-opted-in counterpart added (real store, real claim, `HomeBridge`). |
| Peek re-advertises an older claim when the grant set reverts | low | Real but same authority; contract text narrowed (see notes). |
| Pin protected capabilities empty for client claims in `devices[]` (verification gap) | medium | Folded into the first row. |
