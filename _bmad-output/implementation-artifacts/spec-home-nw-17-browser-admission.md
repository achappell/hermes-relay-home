---
id: HOME-NW-17-browser-admission
parent: HOME-NW-17
status: in-review
baseline_commit: 3614fde02b557b76deb0aca4fad517e922f44489
route: dispatch
product_epic: 3
created: 2026-10-08
github_issue: https://github.com/achappell/hermes-relay-home/issues/101
---

# Admit the production W/K browser appliance as a Home client

**Home implementation under review; not deployed acceptance.** The owner approved required H1/H2/H3/H4/H6 on 2026-10-08 (“ok lets do it”) after the corrected-spec review. H5 self-health/monitoring, downstream TUI changes, hosts, production grants, deployment and browser WK acceptance are excluded. This implementation stacks on the reviewed specification PR #102, head `3614fde02b557b76deb0aca4fad517e922f44489`. The consuming TUI spec remains PR #226, head `760da1865991009b57bb1e73784525b34afdd486`; its tracker is not changed here.

## Intent

<frozen-after-approval>
Pair one browser appliance Device once, not each browser or tab. Its credential holds grants for every explicitly Home-authorized Profile, including later approved grants without re-pairing. Each browser connection can independently claim an authorized grant under the existing per-device limit. No wildcard or browser identity login is introduced.

Admit endpoint kind `browser`, with exactly `client_claim`, no Room, wake mapping or touch binding. Deny protected-input, consequence-confirmation, health, wake and touch capabilities. Preserve native TUI/iOS/macOS/Android contracts.

A browser device must not request new Profile access or approve/reject another Device's grants. Direct approve/reject HTTP calls and the credential domain mutation boundary must deny browser authority. Existing non-browser owner approval remains available. Holder visibility is not new approval authority.

Accept `service_private_file` only for browser appliances, at enrollment request and consumption, bound to the stored endpoint type/attestation. Other endpoint kinds retain `platform_secure_store`. The appliance policy is a persistent private server-side credential file readable only by its service account and administrators, never browser storage, logs or the repository. Actual appliance-file persistence is TUI acceptance, not proof supplied by Home.

Provide a minimal exact-Profile Home-admin post-pair grant-add API/domain/admin-tool path. Append, never re-pair or replace/revoke existing grants. Shared Profiles follow existing authorization. Owned Profiles with another live holder remain pending until an eligible non-browser holder approves. Without a holder, explicit recorded Home administrator first-holder bootstrap may activate the grant; otherwise it remains pending. Requests are idempotent and individually revocable.

Grant IDs are opaque, stable across label rename, never reassigned, and distinct after revocation/re-issuance. Current per-device labels must not collide. A valid rename or grant change produces a new configuration revision; former-label reuse cannot redirect an ID-bound shortcut. Revoking a grant or Device closes its claims.
</frozen-after-approval>

## Code Map and design decisions

- `src/hermes_home/domain/credentials.py`: reuse enrollment, credential storage, owner authorization, current-grant filtering and revocation. Browser request/approval scope policy and endpoint-specific attestation validation are enforced here. `decide_owner_grant` denies both browser decisions directly.
- `add_client_grant` appends one exact grant transactionally. Durable per-device idempotency records bind key, Profile, bootstrap argument and grant ID; retries return its current status rather than restoring a revoked grant. Current grants are deduplicated, and the existing 16-grant limit applies. Label collision validation is inside the append transaction as well as existing approval/configuration boundaries.
- `client_configuration` returns grants and their durable per-device revision atomically. Its fingerprint includes household configuration revision and current grant IDs/status. Client claims compare that projection, so a grant approval, removal or rename can make a stale selector fail closed; concurrent addition cannot attach a new revision to an old grant list. Room-device revision behavior is unchanged.
- `src/hermes_home/api/application.py`: `POST /api/v1/devices/{device_id}/profile-grants`, admin Bearer on loopback; classified as an admin route for proxy denial. Body: `schema`, exact `profile_id`, `idempotency_key`, optional `authorize_bootstrap` (default false). Returns current grant ID/status. Existing configuration publication checks per-device name collisions.
- `src/hermes_home/api/pairing.py` and `pairing_page.py`: existing signed-in same-origin `/pair` administration offers Add Profile with explicit bootstrap authorization confirmation. The browser appliance credential cannot access this administrator session. The existing `personal_client` internal page flag also identifies roomless browser clients; no misleading personal-device wording is shown.
- Labels reuse canonical Profile names rather than add a second alias convention. Rename uses the existing revision-checked configuration API. Holder/pending-list visibility is intentionally unchanged; only the mutation authority is denied.
- `tests/test_browser_admission.py`: behavioral regressions and actual localhost HTTP/SQLite smoke, using isolated temporary state. Existing native fixtures gain browser-only attestation selection without changing native behavior.
- `docs/contracts/v1/README.md`: published route, authorization, label, identity and per-device revision contract.

## Tasks and acceptance

- [x] H1/H3/H4: Given browser enrollment, when request and approval/consume run, then only browser private-file attestation and baseline client scope are accepted. Substituted consume fields fail without consuming the approval. Native secure-store behavior remains tested.
- [x] H2: Given a browser holding an owned Profile, when either approve or reject reaches HTTP/domain mutation, then it is denied and the pending grant remains pending. Native owner decisions remain tested.
- [x] H6: Given a paired Device, when an administrator adds one exact Profile, then other grants remain, shared/owned/bootstrap rules apply, retries do not duplicate, and browser add authority/wildcards are denied.
- [x] Identity/discovery: Given a grant or canonical Profile rename, when configuration is fetched, then current grants have unique labels and a fresh device projection revision; rename preserves ID, revocation/re-issuance does not reuse it.
- [x] Local HTTP acceptance: Given temporary SQLite state and a real loopback Home server, when browser enrollment, grant additions, denials, claim admission and revocation run, then HTTP statuses and durable effects are asserted. No live Home or Standard deployment is contacted.
- [ ] Merge/deploy acceptance: implementation PR must be reviewed and dependencies resolved; deployment and downstream browser WK acceptance remain separately owned and open.

## Verification and gates

See the colocated `validation-home-nw-17-browser-admission.md` for exercised commands/output and independent-review disposition. Implemented does not mean deployed. The immutable baseline preserves the original corrected proposal and owner-decision history in PR #102.

## Spec Change Log

- 2026-10-08: Original draft/backlog stub and independent corrective/recheck updates published in PR #102.
- 2026-10-08: Owner approved H1/H2/H3/H4/H6 implementation; H5 and deployment remain excluded. Implemented Home contract, added behavioral and real HTTP smoke coverage, and documented the exact administrator API and revision/identity semantics. Status is in-review, not done/deployed.
