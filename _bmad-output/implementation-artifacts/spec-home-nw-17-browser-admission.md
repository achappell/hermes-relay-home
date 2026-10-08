---
id: HOME-NW-17-browser-admission
parent: HOME-NW-17
status: draft
product_epic: 3
created: 2026-10-08
github_issue: https://github.com/achappell/hermes-relay-home/issues/101
---

# Admit the production W/K browser appliance as a Home client

**Status: draft stub. Nothing here is approved and no implementation is claimed.** Every item below is a `PROPOSED` default for the owner. Tracker: `home-nw-17-browser-admission`, `backlog`. This is a Home-only record (`AGENTS.md`); it changes no TUI, iOS or Android file. The consuming story is the TUI repository's `WK-HOME-01` ("Migrate the production W/K browser and iPad appliance to HomeBridge"), whose draft spec is in the TUI docs PR for the same change; its status stays with the TUI tracker.

Evidence tags: **[FACT]** cites a file and line on `origin/main` (`71539fc`). **[INFERENCE]** is my conclusion. **UNKNOWN** is not established by any artifact I could read.

## Why Home needs a story

The production browser appliance is a server-side process on the Ops host that serves several unauthenticated browsers. The TUI repository wants it to use Home like any other client: pair once, hold Profile grants, and make one client claim per browser connection. Home has the mechanism but not the vocabulary:

- **No browser or display kind.** `CLIENT_ENDPOINT_TYPES = {"tui","ios","macos","android"}` (`src/hermes_home/domain/credentials.py:39`). `client_claim` is refused for any other type (`credentials.py:604-611`) and the pairing page refuses to approve any other type (`api/pairing.py:345`). A grep of `src/hermes_home` for `browser`/`kiosk` finds only an Origin check (`api/pairing.py:427`).
- **Every grant holder is an owner approver.** `pending_owner_grants`, `decide_owner_grant` and `profile_holders` select approvers by holding an active grant for the Profile and never read the device type (`credentials.py:1011-1075`; rule at `HOME-NW-17` spec `:48`). A shared kitchen display holding the Amanda grant could therefore approve other devices' grants to that Profile if its credential were used for that call.
- **Storage attestation is one fixed value.** Enrollment requires `secure_storage == "platform_secure_store"` (`credentials.py:38,407,678`). A headless Linux service has no platform store; the TUI's own client refuses to run without macOS Keychain or Linux Secret Service (TUI `home_client.py:145-160`). An appliance would have to assert something false or Home must define an honest value.
- **Self-health is Room-bound.** `GET /api/v1/devices/{id}/health` (HOME-NW-09) returns route, authorization, bridge and Standard readiness without opening a conversation, but needs `health_view` and a target whose `room_id` is in the caller's scope rooms (`api/application.py:695-736`). A client device has `rooms: []` (`HOME-NW-17` spec `:78`). **UNKNOWN** whether a client device appears in `configuration["devices"]` at all.

What already works and needs no change: `POST /api/v1/client-claims` (new session, no Room, per-device limit 8, 120 s reconnect grace, close on revoke; `HOME-NW-17` spec `:51-57,97-148`; `bridge/production.py:246-348`), `GET /api/v1/client-claims` and `POST /api/v1/client-claims/close` (`HOME-NW-18`), the Serve paths for those routes (`deploy/windows/README.md:316-319`), and deny-by-default `sensitive_entry`/`consequence_confirm` (`HOME-NW-10` spec `:20,44`).

## Proposed contract change (minimal)

- **H1. `browser` endpoint kind.** Add `browser` to `CLIENT_ENDPOINT_TYPES` so a browser appliance can be enrolled, approved on `/pair`, and hold `client_claim` grants. It follows the personal-client rules: no Room, no wake mapping, opaque `grant_id`s, per-device claim limit. The pairing page shows the type; the existing `personal_client` flag (`api/pairing.py:231-232`) needs a wording check because a shared display is not personal.
- **H2. Not an owner approver.** A `browser` device holds grants and claims but is excluded from `pending_owner_grants`, `decide_owner_grant`, and the approver side of `profile_holders` (it may still appear in other holders' lists). Its own grants to owned Profiles follow the existing `pending_owner` flow, approved by a non-browser holder, with the recorded first-device bootstrap unchanged (`credentials.py:1180-1215`).
- **H3. Storage attestation for a service-hosted appliance.** Accept one additional `secure_storage` value, for example `service_private_file`, only when `type == "browser"`. The pairing page shows it before approval. All other kinds keep `platform_secure_store`. Alternative: the appliance uses Secret Service on its host if it exists (**UNKNOWN**), which would need no Home change.
- **H4. Capabilities.** A `browser` credential is issued `client_claim` only; `sensitive_entry` and `consequence_confirm` stay omitted, so structured secret/sudo prompts are unavailable to the shared display, matching the browser UI's own limit (TUI `docs/ops-web-deployment.md:35-38`). Add a test, not new behaviour.
- **H5 (optional, separable). Self-health for a roomless client device.** Let a `browser` device with `health_view` read its own health (the four-stage projection), so an appliance can show Standard-readiness without creating a Standard session. If H5 is not approved, the appliance reports Home reachability and credential state only, and Standard outages are visible through Home's existing diagnostics and metrics.

## Owner decisions

1. **Add the `browser` kind (H1), or pair the appliance as `tui` with no Home change?** **PROPOSED:** add `browser`. The `tui` route works today but mislabels a shared display and makes it an owner approver (H2).
2. **H2 owner-approver exclusion.** **PROPOSED:** yes, for `browser` only.
3. **H3 attestation.** **PROPOSED:** add `service_private_file`, `browser` only. Needs an owner decision because it weakens the wording of the pairing rule "platform secure store" for one kind (`HOME-NW-17` spec `:294`).
4. **H5 self-health.** **PROPOSED:** include as a separate slice after H1-H4; defer if cost is high.
5. **Naming.** **PROPOSED:** keep `HOME-NW-17-browser-admission` (child of the personal-client admission story, epic 3) instead of reserving a new `NW-19`.

## Acceptance (for when the owner approves)

- A `browser` enrollment is approved on the pairing page with only `client_claim`; any other capability or an attempt to carry `wake_claim`/`touch_claim` is refused.
- A `browser` device can list grants, make client claims (new session), close them, and list and close its own claims; nothing returns a Profile ID, Standard Session ID or handle other than the claim's own handle.
- A `browser` device cannot list pending owner grants or approve/reject one.
- Revoking the device or an owned grant closes its claims, as for other clients.
- A `browser` device receives `forbidden`/unavailable for protected prompts (`sensitive_entry`, `consequence_confirm`).
- The attestation value is shown on the page and rejected for every other kind.
- Existing `tui`, `ios`, `macos` and `android` behaviour and tests are unchanged.

## Code map (expected, from existing files)

`src/hermes_home/domain/credentials.py` (type set, approver selection, attestation); `src/hermes_home/api/pairing.py` and `pairing_page.py` (type check, labels); `src/hermes_home/api/application.py` (approver routes, optional self-health); `docs/contracts/v1/README.md` (kind, attestation); tests in `tests/test_credentials*.py`, `tests/test_client_grants.py`, `tests/test_pairing_page.py`, `tests/test_client_claims_api.py`, `tests/test_health.py`.

## Size and blockers

- **H1-H4:** small code change plus tests; the review of H2 and H3 is the real cost. **H5:** small-to-medium.
- **Hardware / host access:** none to build. Deploying to CaticornQueen needs Windows host access; the Ops → Home tailnet route and Serve publication of the existing client routes are Ops/host facts not recorded in this repository (**UNKNOWN**).

## Risks

- A shared display holding an owned-Profile grant is a standing credential on an unauthenticated browser surface; H2 removes the approver power but not the conversation access.
- Adding a kind and an attestation value widens the contract; both stay `browser`-only to avoid changing personal-client rules.
- The appliance's concurrency (default 8 browser sockets) shares Home's per-device claim limit (default 8, `bridge/production.py:53`; `runtime.py:232`); Home does not expose that limit to a device (**UNKNOWN**), so sizing is a deploy-time check in the TUI story.

## Spec Change Log

- 2026-10-08: Drafted as a stub for the TUI `WK-HOME-01` dependency. Status `draft`; tracker `backlog`; no code.
