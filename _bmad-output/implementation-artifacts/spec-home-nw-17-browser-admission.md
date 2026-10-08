---
id: HOME-NW-17-browser-admission
parent: HOME-NW-17
status: draft
product_epic: 3
created: 2026-10-08
github_issue: https://github.com/achappell/hermes-relay-home/issues/101
---

# Admit the production W/K browser appliance as a Home client

**Status: draft stub.** Nothing here is approved beyond the owner decisions dated 2026-10-08 below; no implementation is claimed. Remaining design choices are `PROPOSED`. Tracker: `home-nw-17-browser-admission`, `backlog`. This is a Home-only record (`AGENTS.md`); it changes no TUI, iOS or Android file. The consuming story is the TUI repository's `WK-HOME-01` ("Migrate the production W/K browser and iPad appliance to HomeBridge"), whose draft spec is in the TUI docs PR for the same change; its status stays with the TUI tracker.

Evidence tags: **[FACT]** cites a file and line on `origin/main` (`71539fc`). **[INFERENCE]** is my conclusion. **UNKNOWN** is not established by any artifact I could read.

## Why Home needs a story

The production browser appliance is a server-side process on the Ops host that serves several browsers reachable only over the household Tailscale network, not the public Internet. The TUI repository wants it to use Home like any other client: pair the appliance once, hold grants for every Home-authorized Profile, and make one independent client claim per browser tab/connection. No browser sign-in is provided; anyone reaching the page can use those authorized Profiles. Home has the mechanism but not the vocabulary:

- **No browser or display kind.** `CLIENT_ENDPOINT_TYPES = {"tui","ios","macos","android"}` (`src/hermes_home/domain/credentials.py:39`). `client_claim` is refused for any other type (`credentials.py:604-611`) and the pairing page refuses to approve any other type (`api/pairing.py:345`). A grep of `src/hermes_home` for `browser`/`kiosk` finds only an Origin check (`api/pairing.py:427`).
- **Every grant holder is an owner approver.** `pending_owner_grants`, `decide_owner_grant` and `profile_holders` select approvers by holding an active grant for the Profile and never read the device type (`credentials.py:1011-1075`; rule at `HOME-NW-17` spec `:48`). A shared kitchen display holding the Amanda grant could therefore approve other devices' grants to that Profile if its credential were used for that call.
- **Storage attestation is one fixed value.** Enrollment requires `secure_storage == "platform_secure_store"` (`credentials.py:38,407,678`). A headless Linux service has no platform store; the TUI's own client refuses to run without macOS Keychain or Linux Secret Service (TUI `home_client.py:145-160`). An appliance would have to assert something false or Home must define an honest value.
- **Self-health is Room-bound.** `GET /api/v1/devices/{id}/health` (HOME-NW-09) returns route, authorization, bridge and Standard readiness without opening a conversation, but needs `health_view` and a target whose `room_id` is in the caller's scope rooms (`api/application.py:695-736`). A client device has `rooms: []` (`HOME-NW-17` spec `:78`). **UNKNOWN** whether a client device appears in `configuration["devices"]` at all.

- **No post-pair grant-add operation.** The existing Profile-grant API lists and approves/rejects/revokes grants (`src/hermes_home/api/application.py:240-243,277-280`) but has no operation to append one exact Profile grant to an already-paired Device. Re-enrollment is not a safe substitute because the enrollment grant-issuance path replaces that Device's grants (`src/hermes_home/domain/credentials.py:772-778,1163-1213`). H6 proposes a minimal append-only Home-admin contract.

## Proposed contract change (minimal)

- **H1. `browser` endpoint kind.** Add `browser` to `CLIENT_ENDPOINT_TYPES` so a browser appliance can be enrolled, approved on `/pair`, and hold `client_claim` grants. It follows the personal-client rules: no Room, no wake mapping, opaque `grant_id`s, per-device claim limit. The pairing page shows the type; the existing `personal_client` flag (`api/pairing.py:231-232`) needs a wording check because a shared display is not personal.
- **H2. Not an owner approver (APPROVED 2026-10-08).** A browser device may hold grants and claims but cannot approve or reject other devices' access; approvals remain in existing Home administration tools. **PROPOSED enforcement:** reject both `approve` and `reject` for browser devices at the owner-approval mutation/authorization boundary (`decide_owner_grant`), not merely when listing pending grants; direct API requests and the domain mutation path must be denied. Holder-list visibility remains undecided. Its own owned-Profile grants follow existing `pending_owner` authorization, and the recorded first-device bootstrap is unchanged (`credentials.py:1180-1215`).
- **H3. Storage attestation and credential protection (APPROVED 2026-10-08).** Accept `secure_storage: service_private_file` for the browser appliance only; all other device kinds retain `platform_secure_store`. The approved appliance policy is a private server-side credential file readable only by the service account and administrators, surviving restarts, and never exposed to browser storage, logs, or the repository. **PROPOSED Home enforcement:** bind the attestation to the browser endpoint type at both enrollment-request creation and credential consumption; reject type/attestation substitution and reject `service_private_file` for every non-browser kind. The appliance's actual file and restart behavior are downstream TUI acceptance, not Home acceptance. File location, exact permissions/atomic-write mechanics, renewal, and implementation remain unapproved.
- **H4. Capabilities.** A browser credential is issued `client_claim` only; `sensitive_entry` and `consequence_confirm` stay omitted, so structured secret/sudo prompts are unavailable to the shared display, matching the browser UI's own limit (TUI `docs/ops-web-deployment.md:35-38`). Add a test, not new behaviour.
- **H5 (optional, separable). Self-health for a roomless client device.** **PROPOSED:** let a browser device with `health_view` read only its own four-stage health projection, without creating a Standard session. H5 is not approved. Without it, this Home contract grants no self-health capability. The checked-in Home metrics and dashboards do not expose idle Standard-readiness evidence; do not claim they provide an Hermes outage alert.

## Owner decisions

**Approved 2026-10-08:** pair the appliance once, not once per browser; the one appliance Device holds grants for all Home-authorized Profiles, including new Profiles once their grants are approved; a browser device cannot approve or reject other devices' access and approvals remain in existing Home administration tools; `service_private_file` is an approved browser-appliance-only attestation and credential-storage policy, while other device kinds retain `platform_secure_store`; no separate browser sign-in, so anyone reaching the page can use those authorized Profiles; household Tailscale access only, not public, including WebSocket/backend bypass protection; each reload or reconnect starts a fresh conversation without automatic replay, and tabs are independent. These policy approvals do not approve H1, H5, or implementation/deployment.

Every remaining design choice below is **PROPOSED**.

1. **H1. `browser` kind.** Add the `browser` kind rather than pair the appliance as `tui`. **PROPOSED:** add `browser`; the `tui` route works today but mislabels a shared display. H1 is not approved.
2. **H2 enforcement.** **PROPOSED:** enforce owner-approver exclusion at the authorization/mutation boundary for both approval and rejection; deny direct API and domain mutation by a browser device. Whether holder-list visibility is restricted remains undecided.
3. **H3 implementation.** The attestation and private-file policy are approved as above; request/consume binding tests and implementation mechanics remain **PROPOSED**, not implementation or deployment approval.
4. **H4 capabilities.** **PROPOSED:** browser credentials carry `client_claim` only, with no `sensitive_entry`, `consequence_confirm`, `health_view`, `wake_claim`, or `touch_claim`.
5. **H5 self-health.** **PROPOSED:** consider only as a separate, optional capability requiring an explicit approval; do not infer it from H3 or baseline admission.
6. **H6 post-pair grant addition.** **PROPOSED minimal contract:** a Home-admin-only operation targets one already-paired Device and one exact Profile, appends rather than replaces grants, and never re-pairs the device or accepts a wildcard. It creates a grant only after Home authorization: shared Profiles follow existing shared-grant rules; an owned Profile with another active holder enters `pending_owner` for approval through existing Home administration tools; with no active holder, only the existing explicit Home owner/administrator first-holder bootstrap may issue it and must record that authorization—otherwise it remains pending. Browser devices can neither request nor decide approvals. Repeated identical requests return the same current grant rather than duplicating it; every grant remains individually revocable. Current per-device labels must be unique among active grants; reject a colliding new/renamed Profile and advance configuration revision on a valid rename. A grant's opaque `grant_id` is stable across that grant's label rename and is never reassigned; revocation/re-issuance creates a distinct ID. A former label may be reused without redirecting a stale ID-bound shortcut. **PROPOSED interface shape:** an authenticated Home-admin operation such as `POST /api/v1/devices/{device_id}/profile-grants` with one `profile_id`, a distinct label if needed, and idempotency key; exact route and persistence mechanics are unapproved.
7. **Naming.** **PROPOSED:** keep `HOME-NW-17-browser-admission` (child of the personal-client admission story, epic 3) instead of reserving a new `NW-19`.

## Acceptance (Home service contract only; for when the owner approves)

The downstream TUI spec owns appliance-file persistence, page/Origin behavior, tailnet/proxy exposure, and real-browser/deployment evidence (`WK-HOME-01` AC-1, AC-9, AC-10). Home contract completion is a prerequisite, not dependent on that deployment.

- If H1 is later approved, a `browser` enrollment is approved on the pairing page with only `client_claim`; any other capability or an attempt to carry `wake_claim`/`touch_claim` is refused.
- The pairing request and consume steps both enforce the `(browser, service_private_file)` pairing. A changed endpoint type or attestation between request and consume is rejected; `service_private_file` is rejected for `tui`, `ios`, `macos`, and `android`, whose existing `platform_secure_store` behavior remains unchanged.
- A browser device cannot approve or reject any other device's grant. Direct authenticated `approve` and `reject` requests, including calls reaching the domain mutation boundary, are denied; an authorized non-browser holder can still use the existing Home approval tools. Whether browser devices can view pending-grant or holder-list information remains undecided.
- The proposed H6 Home-admin operation appends exactly one requested Profile grant to one named, already-paired Device; it never re-pairs/re-enrolls, replaces/revokes prior grants, or accepts a wildcard. Shared Profiles follow the existing shared-grant rule. An owned Profile with another active holder stays `pending_owner` until an eligible non-browser holder approves via existing Home administration tools; with no active holder, only the existing explicit Home owner/administrator first-holder bootstrap may issue it and must record that authorization, otherwise it remains pending. The browser device cannot request, approve, or reject grants. Repeating the same idempotency key returns the same result without duplicate grants. A grant retains its `grant_id` across label rename, while revocation/re-issuance creates a distinct ID; reusing a former label cannot resolve an old ID-bound shortcut. A new/approved grant appears in that Device's configuration with a new revision; duplicate current per-device labels are rejected, while a valid rename advances the revision. Revoking the grant closes its claims; no stale/ambiguous label may select another Profile.
- Revoking the Device or an owned grant closes its claims, as for other clients.
- A browser device receives `forbidden`/unavailable for protected prompts (`sensitive_entry`, `consequence_confirm`); it has no `health_view` unless H5 is separately approved.
- Existing `tui`, `ios`, `macos` and `android` enrollment, attestation, capability, grant, and claim behavior and tests are unchanged.

## Code map (expected, from existing files)

`src/hermes_home/domain/credentials.py` (type set, approver selection, attestation); `src/hermes_home/api/pairing.py` and `pairing_page.py` (type check, labels); `src/hermes_home/api/application.py` (approver routes, optional self-health); `docs/contracts/v1/README.md` (kind, attestation); tests in `tests/test_credentials*.py`, `tests/test_client_grants.py`, `tests/test_pairing_page.py`, `tests/test_client_claims_api.py`, `tests/test_health.py`.

## Size and blockers

- **H1-H4 and H6:** Home contract/domain/API changes and tests; implementation size remains **UNKNOWN** until independently reviewed. **H5:** a separable optional change requiring a separate approval.
- **Hardware / host access:** none to build. Deploying to CaticornQueen needs Windows host access; the Ops → Home tailnet route and Serve publication of the existing client routes are Ops/host facts not recorded in this repository (**UNKNOWN**).

## Risks

- A shared display holding an owned-Profile grant is a standing credential usable by anyone reaching its page; H2 removes the approver power but not the conversation access. Tailscale-only household reachability is the access boundary, not per-user browser authentication; public access and proxy/backend bypass are prohibited.
- Adding a `browser` kind and attestation widens the contract; keep them browser-only. H6 must append one explicitly authorized grant without disturbing existing grants or owner approval. Unique active labels keep the selector readable; stable per-grant IDs, rather than labels, bind shortcuts across rename/reuse and prevent stale routing.
- Appliance concurrency (default 8 browser sockets) shares Home's per-device claim limit (default 8, `bridge/production.py:53`; `runtime.py:232`); Home does not expose that limit to a device (**UNKNOWN**), so sizing is a deploy-time check in the TUI story.

## Spec Change Log

- 2026-10-08: Drafted as a stub for the TUI `WK-HOME-01` dependency. Status `draft`; tracker `backlog`; no code.
- 2026-10-08: Addressed browser-plan review findings in the draft: required direct mutation denial, proposed exact post-pair grants and authorization, request/consume attestation binding, and Home-only acceptance ownership. Preserved approved H2/H3/Q5/Q6 policy; H1/H5 and implementation/deployment remain unapproved; status `draft`, tracker `backlog`.
- 2026-10-08: Independent recheck clarified stable per-grant identity across rename/re-issue and safe label reuse; H1/H5 and implementation/deployment remain unapproved. Status stays `draft`; tracker stays `backlog`.
