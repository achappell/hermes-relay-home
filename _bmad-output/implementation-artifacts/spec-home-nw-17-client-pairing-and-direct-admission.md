---
title: 'HOME-NW-17 — Pair personal clients from a Home page and admit their direct conversations'
type: 'feature'
created: '2026-09-23'
status: 'review'
baseline_commit: '97dc3aa'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/docs/contracts/v1/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-02-pairing-credentials.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-05-profile-mappings-conversation-claims.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-home-nw-16-touch-admission-tap-claim.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-pilot-session-persist-and-runner-log.md'
---

> **Approved correction (2026-09-23):** Apply the [delivery contract](course-correction-2026-09-23.md). Home personal admission includes macOS. Preserve existing owner-approval and client-owned session decisions. The draft is not proof that each named operation exists on the selected unmodified upstream baseline. Legacy rollback is retired at the migration gate.

# Implementation Spec: HOME-NW-17

## Intent

**Problem:** The TUI, iOS, and Android clients are Paired Endpoints in the architecture, but none of them can use Home on their own. Two gaps block them.

1. **Pairing is not usable.** HOME-NW-02 implemented offers, requests, approval, consumption, renewal, and revocation, but approval requires raw HTTP calls with the Home admin token, and the device-facing routes are not published on the tailnet. The PRD's FR-28 QR flow has no surface that implements it.
2. **Direct-use clients have no admission route.** A conversation handle is created only by a granted wake claim (HOME-NW-05, acoustic, Puck) or a tap claim (HOME-NW-16, one fixed Room/Profile, Touch panel). A personal client belongs to no Room, may serve several Profiles, and has no acoustic evidence. The Android 5-A-4 live gate and the TUI's `HOME_CONVERSATION_HANDLE` both worked around this with operator-minted disposable handles, which expire within 90 seconds of issue if unopened and 8 seconds after playback.

**Approach:** Add three pieces on top of the existing credential and claim machinery.

- **A Home pairing page.** A tailnet-only page served by Home creates an offer and shows it as a QR code and a short typed code. It then lists the pending request with its label, type, and confirmation code, and approves it with ticked Profiles. It uses the same admin authority as the existing admin routes.
- **A `client_claim` capability and route.** `POST /api/v1/client-claims` admits a deliberate conversation start from a paired personal client for one of its approved Profiles. It is not Room-bound and does not arbitrate.
- **Client-owned session lifecycle.** A client claim authorizes a connection for one Profile grant; it does not choose a session. Like a regular CLI, the client decides: start a new session, list its Profile's sessions, resume one, rename it, and end the connection when the user quits. Home passes a bounded set of stock Standard session operations through the bridge, scoped to the claim's Profile, and keeps Standard Session IDs behind opaque session references.

Once paired, a client stays paired: it renews its own credential automatically inside the existing renewal window, and only revocation, expiry after 90 days without any connection, or an explicit unpair ends the pairing.

## Boundaries & Constraints

**Always:** Keep Home the only authority for credentials, Profile grants, and conversation claims. Pairing codes stay short-lived and single-use. Show the device's label, type, requested capabilities, and confirmation code before approval. Validate device authentication, credential generation, `client_claim` capability, grant currency, configuration revision, and Profile availability on every claim. Reuse the conversation claim store, opaque handle, first-open expiry, bridge open, activity events, close, and revocation paths. Revoking the device, a Profile, or a grant closes its active claims exactly as for wake and tap claims. A turn that may have reached Hermes is never replayed.

**Never:** Expose Profile IDs, Session IDs, Hermes tokens, prompts, transcripts, or audio to the client. Accept acoustic evidence, a wake mapping, a Room, or a raw Profile ID on the client-claim route. Let a client claim affect a Room's claim, arbitration, or idle tail. Let `touch_claim` or `wake_claim` credentials submit client claims, or the reverse. Publish admin routes (offer creation, request listing, approval, rejection, rotation, revocation, configuration writes, metrics, diagnostics) on the tailnet except through the authenticated pairing page. Store the admin token in the browser beyond the page session. Add a sibling-repository change.

**Compatibility decision:** Evolve `/api/v1/*` in place. `client_claim` is a new value in `SUPPORTED_CREDENTIAL_CAPABILITIES`. A device without `client_grants` records grants no client claim, matching the empty-grant precedents of HOME-NW-05 and HOME-NW-16. Existing credentials are unaffected.

## Resolved Direction

- **Approval lives on a Home page for now.** The page is the first trusted surface for FR-28. It is built on public API routes, so the iOS and Android control-plane stories (3-I, 3-A) can later approve through the same calls without a second pairing model.
- **Profile owners approve access to their own Profile.** Each Profile carries an optional `shared` flag in configuration (for example Spark or a family Profile); a Profile without it is owned. Owner identity is proved by holding an active grant for that Profile, so Home needs no separate person record. The admin may grant a shared Profile on the pairing page. A grant to an owned Profile becomes `pending_owner` until the owner approves it from a client already paired to that Profile; the grant appears there as an approval request showing the device label, type, and requester. Bootstrap exception: when an owned Profile has no paired client yet, the admin may approve the first grant on the page, and that approval is recorded. Every client of a Profile can list the devices currently holding it, so no grant is silent. Revocation by the owner or the admin is immediate.
- **Home authentication does not depend on the network path.** Clients currently reach Home over Tailscale, and the pairing link carries the tailnet address, so a client without Tailscale cannot pair yet; home-network routes remain HOME-NW-04 roaming work. Home never uses Tailscale identity headers for authorization. The pairing page signs in with the admin credential, and owner approval uses the owner's paired Device credential. Either works unchanged behind a different route or proxy.
- **A pairing link carries everything the client needs.** The QR code and the copyable text encode one link, `hermes-home://pair?home=<https base URL>&code=<enrollment code>`. A client needs nothing else: no URL typing, no pasted credential, no handle. The short code (for example `K7Q-4MX`) is shown for typing when a camera is not available, with the Home base URL shown beside it.
- **Profiles are granted per client, not per Room.** A client device holds `client_grants`, one per approved Profile, each with an opaque `grant_id`, a display label, and a state (`active` or `pending_owner`). Grants live in their own record keyed by device, not inside the frozen credential scope, so owner decisions, renewal, and rotation never rewrite a credential; the scope carries only the `client_claim` capability. `client_claim` may be approved only for endpoint types `tui`, `ios`, `macos`, and `android`. Device configuration returns the list of `{grant_id, label}`; Profile IDs stay on Home. The TUI's Profile switcher and the mobile Profile pickers choose by `grant_id`.
- **No Room, no arbitration, no preemption.** A personal client is not a household room device. Its claims are keyed by device and grant. Several terminal windows on one laptop are several clients of the same device, so a device may hold several active claims per grant, bounded by `HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE` (default 8); excess claims are denied `claim_limit`.
- **The client owns the session lifecycle, Home owns authority.** A client claim has no idle timer. It stays active while the client holds its bridge connection, through the existing in-process reconnect path, and closes when the client sends `conversation.close`, when its connection is gone past the reconnect grace (`HERMES_HOME_CLIENT_RECONNECT_GRACE_SECONDS`, default 120), or on revocation. Home never decides that a conversation has ended because the user paused; the Room idle tail remains a wake/tap-claim rule. A client claim that is never opened still closes after the 90-second first-open expiry, so a client that crashes between claiming and connecting cannot hold a slot of the per-device limit until Home restarts.
- **The client chooses the session when it claims.** The bridge keeps its invariant of one claim bound to one Standard Session. A client claim names the session it wants: `new` (default), `most_recent` (Home asks Standard `session.most_recent` for the Profile), or `resume` with a `session_ref`. Switching sessions in the client (`/new`, `/resume`) closes the current claim and makes a new one, as a CLI reconnects. Listing uses `POST /api/v1/client-sessions/list`, which asks Standard `session.list` for the grant's Profile over a short-lived gateway connection. Renaming uses Hermes's own advertised `title` command through the existing bridge command dispatch. Home rewrites Standard Session IDs to opaque `session_ref` values that are valid only for that grant; a reference from another grant is `session_unavailable`. No other Standard session operation is exposed.
- **Sessions are per Profile, as in a CLI.** `session.list` returns the Profile's Standard sessions, the same view a Hermes CLI signed in to that Profile would see, including conversations started by Room devices (Puck, Touch panel, W/K) and by other paired clients, so a conversation started anywhere can be resumed from any client paired to the same Profile. The list is bounded (default 50, newest first) and carries only `session_ref`, title, last-active time, message count, and whether another active claim currently holds it.
- **Resume never replays.** Resuming a session restores Hermes's stored history; any turn left uncertain by a disconnect stays unresolved and is reported, never re-sent.
- **Long-lived pairing through automatic renewal.** The 90-day credential and 14-day renewal window stay as implemented. Clients renew on connect when inside the window, and the contract documents that obligation. A client that is unused for 90 days must pair again; the page offers "pair again" for an expired device with its previous Profiles preselected.
- **Pending approval is a typed state.** Consuming a request that is still pending returns `approval_pending` instead of the generic `conflict`, so a client can poll plainly and show "waiting for approval on the Home page".

## Proposed Contract

### Pairing link

```text
hermes-home://pair?home=https://caticornqueen.example.ts.net&code=K7Q4MX
```

The client submits the existing `POST /api/v1/enrollment/requests` body, with `type` one of `tui`, `ios`, `macos`, or `android`, and `requested_capabilities` including `client_claim`. It shows the returned confirmation code and polls `POST /api/v1/enrollment/requests/{request_id}/consume` until it receives credential material, `approval_pending`, or a terminal error.

### Approval scope

```json
{
  "schema": 1,
  "scope": {
    "rooms": [],
    "capabilities": ["client_claim"],
    "wake_mapping_grant": {"mode": "selected", "ids": []},
    "client_grants": [{"profile_id": "amanda"}, {"profile_id": "jensen"}]
  }
}
```

Home assigns each grant an opaque `grant_id`. A grant is valid only while its Profile is available.

### `GET /api/v1/devices/{device_id}/configuration` addition

```json
{
  "client_grants": [
    {"grant_id": "grant-01J...", "label": "Amanda"},
    {"grant_id": "grant-01K...", "label": "Jensen"}
  ]
}
```

### `POST /api/v1/client-claims`

```json
{
  "schema": 1,
  "claim_id": "client-01J...",
  "device_id": "tui-amanda-macbook",
  "configuration_revision": 13,
  "grant_id": "grant-01J..."
}
```

A success response mirrors the tap-claim envelope:

```json
{
  "schema": 1,
  "claim_id": "client-01J...",
  "decision": "granted",
  "configuration_revision": 13,
  "conversation_handle": "opaque-home-claim-01J..."
}
```

The request may name a session: `"session": {"mode": "new"}` (the default when absent), `{"mode": "most_recent"}`, or `{"mode": "resume", "session_ref": "sref-01J..."}`. The response adds `"session": {"mode": "new"}` or `{"mode": "resumed", "session_ref": "sref-01J..."}`. `most_recent` with no stored session for the Profile falls back to `new`.

### `POST /api/v1/client-sessions/list`

```json
{"schema": 1, "grant_id": "grant-01J...", "limit": 20}
```

```json
{"schema": 1, "sessions": [
  {"session_ref": "sref-01J...", "title": "Grocery plan", "started_at": 1727120000, "message_count": 14, "active": false}
]}
```

Device-authenticated and limited to an active grant held by the caller. `limit` is 1–50 (default 50), newest first. `active` is true when another active claim is bound to that session. `conversation.close` ends a claim; its Standard session stays stored in Hermes for later resume.

### Denial reasons

| Code | Meaning |
| --- | --- |
| `unauthorized` | Missing, revoked, expired, or superseded credential |
| `client_claim_unavailable` | Scope lacks `client_claim`, or the `grant_id` is not in the current scope |
| `profile_unavailable` | The grant's Profile is currently unavailable |
| `stale_configuration` | Claim names a revision older than current; refresh device configuration |
| `claim_limit` | This device already holds the maximum number of active client claims |
| `session_unavailable` | The `session_ref` is unknown for this grant |
| `session_busy` | Another active claim is bound to the requested session |
| `grant_pending` | The grant is waiting for the Profile owner's approval |

### Pairing page

`GET /pair` serves a static page. Admin sign-in exchanges the admin token for an `HttpOnly`, `Secure`, `SameSite=Strict` session cookie with a 12-hour lifetime; every state-changing page call also checks `Origin`. The page calls the existing offer, request-list, approve, reject, and revoke operations through cookie-authenticated equivalents under `/pair/api/*`. The page shows paired devices with type, label, Profiles, grant state (`active` or `pending_owner`), expiry, and last renewal, and can revoke one or start "pair again".

### Owner approval on a paired client

A client with a grant for an owned Profile can list and decide pending grants for that Profile:

- `GET /api/v1/profile-grants/pending`: pending grants for Profiles this device is granted, with an opaque `pending_id`, device label, type, requested Profile label, and requested time.
- `POST /api/v1/profile-grants/{pending_id}/approve` and `/reject`: Device-authenticated; allowed only for a device holding an active grant for the same owned Profile.
- `GET /api/v1/profile-grants/holders`: the devices currently holding each of this device's Profiles.

A pending grant expires after 24 hours. A grant never becomes active without an owner decision, except under the bootstrap rule.

## I/O & Edge-Case Matrix

| Situation | Behavior | Guardrail |
| --- | --- | --- |
| Admin grants a shared Profile | Grant active at approval | Profile configured `shared` |
| Admin grants an owned Profile that already has a paired client | Grant `pending_owner`; shown on the owner's clients | No session access until the owner approves |
| Owner approves or rejects from a paired client | Grant becomes active or is removed | Only a device holding that Profile may decide |
| Admin grants the first device for an owned Profile | Grant active under the bootstrap rule; recorded | Visible in every later holder list |
| Pending owner grant not decided in 24 hours | Expires | Admin may request again |
| Client submits a valid pairing link | Pending request with confirmation code; page shows it | Code single-use; 5-minute expiry unchanged |
| Client polls before approval | `approval_pending` | No credential material before approval |
| Admin rejects or code expires | Client shows the terminal reason and offers a new pairing | No partial credential |
| Same endpoint pairs again | Existing NW-02 replacement semantics; old generation revoked | Active claims of the old generation close |
| Client claim, valid grant | Granted; handle returned | No Room, arbitration window, or preemption |
| Second terminal window on the same device and grant | Second claim granted, up to the device limit | Each window owns its own session |
| Claims for two grants on one device | Both allowed | Independent Sessions per Profile |
| User pauses for an hour with the TUI open | Claim stays active | No idle timer on client claims |
| Network drop shorter than the reconnect grace | `conversation.reconnect` restores the claim and session | Uncertain turn reported, never replayed |
| Client quits | `conversation.close`; claim closed | Standard session remains resumable |
| Client crashes | Claim closes after the reconnect grace | No orphaned claim |
| Session list | This Profile's sessions only, bounded, opaque refs | No Standard IDs, no other Profile |
| Resume claim with another grant's ref | `session_unavailable` | Refs are grant-scoped |
| Resume claim for a session bound to another active claim (a live Puck turn, another window) | `session_busy`; the list marks it `active: true` | Never two claims driving one Standard session; retry once the other claim closes |
| Client switches session with `/new` or `/resume` | Current claim closed, new claim made | One claim, one session; the bridge core is unchanged |
| Profile made unavailable mid-claim | Claim closes `profile_revoked` | Same path as wake and tap claims |
| Credential inside the renewal window | Client renews on connect | Idempotent renewal unchanged |
| Credential expired | `unauthorized`; client asks to pair again | Page preselects previous Profiles |
| Room busy with a Puck conversation | Client claim unaffected | Client claims never touch Room claims |

## Code Map

- `docs/contracts/v1/configuration.schema.json` and configuration validation: add the optional Profile `shared` boolean.
- `src/hermes_home/domain/credentials.py`: grant states `active` and `pending_owner`, bootstrap rule, owner decisions, and holder listing; add `client_claim` to `SUPPORTED_CREDENTIAL_CAPABILITIES`; add `client_grants` (Profile ID and opaque grant ID) to `CredentialScope` with bounded validation, serialization, and "absent grants nothing" semantics; validate grants against available Profiles at approval; return `approval_pending` from `consume_request` for pending requests; accept endpoint types `tui`, `ios`, `macos`, and `android`.
- `src/hermes_home/bridge/production.py`: migrate `conversation_claims` to allow client claims without a Room or wake mapping (nullable `room_id` and `wake_mapping_id`, plus `grant_id` and `claim_kind`); exempt client claims from the Room conflict query, idle timer, and first-open binding; enforce the per-device claim limit; close client claims on `conversation.close` or after the reconnect grace.
- `src/hermes_home/bridge/production.py`: a grant-scoped `session_ref` table; client claims created with a preset Session ID for `most_recent` and `resume`; a short-lived Standard client for `session.list` and `session.most_recent`. `standard.py` and `endpoint.py` need no session-operation changes.
- `src/hermes_home/api/application.py`: add the `/api/v1/client-claims` branch mirroring the tap-claim durable-context checks and `_discard_new_claim` recovery; add `client_grants` to device configuration; add the `/pair` page and `/pair/api/*` cookie-authenticated admin equivalents.
- `src/hermes_home/runtime.py`: read `HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE` and `HERMES_HOME_CLIENT_RECONNECT_GRACE_SECONDS`.
- `docs/contracts/v1/README.md` and `docs/contracts/v1/client-claim.schema.json`: document the pairing link, client claim, grants, client session operations, and the client renewal obligation.
- `deploy/windows/README.md` and `install.ps1`: document the Tailscale Serve paths to publish (`/pair`, `/pair/api`, `/api/v1/enrollment/requests`, `/api/v1/client-claims`, `/api/v1/devices`, `/api/v1/bridge/ws`) and the client claim limit and reconnect grace parameters.

## Tasks & Acceptance

**Execution:**

1. Credential scope, capability, endpoint types, and `approval_pending`.
2. Claim-store migration, client claim route, claim limit, close, and reconnect grace.
3. Session choice at claim time, session listing, and grant-scoped `session_ref` mapping.
4. Device configuration `client_grants`.
5. Profile ownership, owner-approval routes, and holder listing.
6. Pairing page with admin session, QR/code offer, pending approval with Profile selection, device list, revoke, and pair again.
7. Contract documentation, schema fixture, and deployment notes.

**Acceptance Criteria:**

- A new client pairs using only the pairing link or the short code and Home URL, plus one approval on the page.
- The page shows the device label, type, requested capabilities, and confirmation code before approval.
- A client credential with two grants can open independent conversations for both Profiles; it can never name a Profile ID.
- A client claim never changes, blocks, or supersedes a Room claim, and a Room claim never blocks a client claim.
- A client can start, list, and resume sessions for its Profile, and rename the current one through Hermes's `title` command, and a session started on one paired client can be resumed from another client paired to the same Profile.
- An opened client claim is never closed by inactivity; it closes on `conversation.close`, after the reconnect grace, or on revocation. A claim that is never opened closes after the 90-second first-open expiry.
- No Standard Session ID or Profile ID reaches a client.
- Revoking the device or making a Profile unavailable closes active client claims.
- Renewal inside the window keeps a client paired past the original 90 days without re-pairing.
- A grant to an owned Profile does not become active until the owner approves it from a paired client, except for the recorded first-device bootstrap; a shared Profile can be granted by the admin directly.
- No Home authorization decision reads Tailscale identity or any other network-path identity.
- Admin routes remain unreachable from the tailnet except through the authenticated page.
- Focused tests, `ruff check src tests`, and `ruff format --check src tests` pass.

### Review Findings

Code review 2026-09-24 of PR #54 (source-only diff; Blind Hunter, Edge Case Hunter, Verification Gap, Acceptance Auditor).

- [x] [Review][Decision] Client claims still expire 90 s after issue if never opened — resolved: keep the expiry as intended (Amanda, 2026-09-24); spec amended.
- [x] [Review][Decision] "Pair again" with previous Profiles preselected is not implemented on the page — resolved: build it now (Amanda, 2026-09-24); patched.
- [x] [Review][Patch] Page poll rebuilds waiting requests every 2 s and wipes ticked Profiles [src/hermes_home/api/pairing_page.py render]
- [x] [Review][Patch] Client claims never re-check grant currency: revoke-then-close is two commits and a revoke racing a new claim leaves a live claim on a revoked grant [src/hermes_home/bridge/production.py resolve; src/hermes_home/api/application.py _post_client_claim]
- [x] [Review][Patch] Any holder of a shared Profile can revoke other devices' grants to it [src/hermes_home/domain/credentials.py revoke_client_grant]
- [x] [Review][Patch] One credential can carry client_claim together with wake_claim or touch_claim [src/hermes_home/domain/credentials.py approve_request]
- [x] [Review][Patch] Holders whose credentials expired still count as owners, stranding new devices in pending_owner [src/hermes_home/domain/credentials.py _issue_client_grants]
- [x] [Review][Patch] Page never shows requested capabilities before approval [src/hermes_home/api/pairing.py _state]
- [x] [Review][Patch] Pairing link can carry a plain-http or loopback Home address [src/hermes_home/api/pairing.py _offer]
- [x] [Review][Patch] Consume now requires a configuration read even for Room devices [src/hermes_home/api/application.py _consume_enrollment_request]
- [x] [Review][Patch] Page does not show last renewal [src/hermes_home/api/pairing_page.py]
- [x] [Review][Patch] Session list ignores Profile availability [src/hermes_home/api/application.py _post_client_session_list]
- [x] [Review][Patch] Page actions map unauthorized/expired errors to 409 [src/hermes_home/api/pairing.py _action]
- [x] [Review][Patch] Non-finite started_at from Standard serializes as NaN [src/hermes_home/bridge/production.py list_sessions]
- [x] [Review][Patch] Admin-guard comment overstates loopback enforcement [src/hermes_home/api/application.py _dispatch]
- [x] [Review][Patch] No real-server test that Set-Cookie and CSP reach the browser [tests/test_http_server.py]
- [x] [Review][Patch] No runtime test for the client settings and session-directory wiring [tests/test_runtime.py]
- [x] [Review][Patch] Page grant removal is never tested to close live claims [tests/test_pairing_page.py]
- [x] [Review][Patch] Legacy credential state without client_grants is never loaded in a test [tests/test_credentials_api.py]
- [x] [Review][Patch] Admin-route guard tests miss rotate, reject, diagnostics, and the Forwarded header [tests/test_pairing_page.py]
- [x] [Review][Patch] Pairing session expiry and eviction are untested [tests/test_pairing_page.py]
- [x] [Review][Defer] Origin check assumes Tailscale Serve passes the browser Host through [src/hermes_home/api/pairing.py _same_origin] — deferred: unverified high; settle with a live sign-in through Serve on deploy.
- [x] [Review][Defer] Admin guard assumes Tailscale Serve adds X-Forwarded-For [src/hermes_home/api/application.py _is_proxied] — deferred: unverified high; settle with a live proxied admin call that must return admin_local_only.

Rejected:

- Reconnect does not clear the grace (false): `mark_open`, called on open and reconnect, clears the deadline; covered by a store test.
- Device revocation leaves client claims open (false): `on_revoked` closes every claim for the device; re-enrollment emits the same event.
- Owned-Profile holders are peers (false against spec): owner identity is holding an active grant.
- Sign-in brute force (false): the admin token is 32 cryptographically random bytes; page sessions reset on restart, which rotation requires.
- Secure cookie over plain HTTP (low): Home binds loopback and is published only over HTTPS.
- Page actions miss configuration exceptions (false): both subclass RuntimeError and are caught.
- No tests or docs (false): excluded from this pass by scope; present on the branch.
- Installer/runtime mismatch (false): both cap grace at 600 and share defaults.
- Dead code and style nits (low): cosmetic.
- Duplicate or non-string Profile IDs (false): `_bounded_values` rejects both.
- Orphan claim when discard fails (low): needs a database failure after a successful insert.
- Holder list exposes shared-Profile holders (false against spec): holder visibility is required.
- Admin approval of pending grants (rejected): contradicts owner approval; P5 removes the stranding cause.
- Owner-route naming differs from spec, and the AC wording on network identity (rejected): fixes would edit the spec.
- Installer does not configure Serve paths (false): documented in the deployment README, including session and grant routes.
- `session.most_recent` is not stock (false): present in Hermes `tui_gateway/methods_session.py`.
- New websocket per session call (low): bounded 5 s, rare calls.

## Open Decisions

- Whether the page also offers "approve from a paired phone" once 3-I and 3-A exist; out of scope here.

## Consuming Stories

- TUI: pair from a link (`hermes-relay pair <link>`), store the credential in the platform secure store (macOS Keychain; Secret Service on Linux) and refuse to pair when none is available, keep pairings keyed per Home so one client can pair with more than one household, show and decide pending owner grants (`/approvals`) and Profile holders, choose Profiles by grant, claim on launch, and own the session lifecycle like a regular CLI: a new session by default, `--continue` for the most recent, `--resume <ref>`, and in-app `/sessions`, `/new`, `/resume`, and `/title`. Close the claim on quit, renew the credential automatically, and retain supported direct Standard as an explicit setup mode; retire the legacy voice-session path under HOME-MIG-09.
- iOS, macOS and Android: open the pairing link or enter the code and Home address (scan QR where supported), store the credential in platform secure storage keyed per Home, show and decide pending owner grants and Profile holders, and replace the operator-supplied disposable handles with client claims.

## Spec Change Log

- 2026-09-23: Drafted from the TUI Home migration investigation.
- 2026-09-23: Replaced Home-decided continuity (client idle timeout and automatic resume) with a client-owned session lifecycle, per Amanda: the TUI manages sessions like a regular CLI.
- 2026-09-23: Resolved per Amanda: per-device client claim limit 8 and reconnect grace 120 seconds, both Home settings.
- 2026-09-23: Resolved per Amanda: owned Profiles require owner approval from a paired client (first-device bootstrap by admin); clients store credentials in the platform secure store; Tailscale is required for client reachability for now, but Home authorization never depends on Tailscale identity; clients keep pairings per Home.
- 2026-09-23: Resolved per Amanda: `session.list` shows the Profile's conversations from every surface, including Room devices and other paired clients.
- 2026-09-24: Merged the approved 2026-09-23 course correction: `macos` is a personal client type; the legacy voice-session rollback belongs to HOME-MIG-09 retirement, not this story.
- 2026-09-24: Code review decisions: unopened client claims keep the 90-second first-open expiry; "Pair again" pre-ticks an expired device's Profiles. Client claims re-check their grant on every open; shared-Profile holders may revoke only their own grant; client claims cannot share a credential with wake or touch claims; only holders with live credentials count as owners; offers require an https Home address.
- 2026-09-24: Admin-token routes refuse proxied requests (`admin_local_only`), because Tailscale Serve publishes by path prefix and the device enrollment prefix also carries admin approval; the header is used only to deny.
- 2026-09-24: Implementation refinements: session choice moves to claim time (new, most_recent, resume) with an HTTP session list, keeping the bridge's one-claim-one-session invariant; `/title` uses Hermes's advertised command; client grants live in their own device-keyed record; Profile ownership is an optional `shared` flag.
- 2026-09-25: Added `POST /api/v1/client-claims/session`, per Amanda, for Android "continue last conversation" (ANDROID-HOME-02 slice 2). A new claim's response carries no `session_ref`, and the Standard session is bound only after the first accepted turn, so a client could not otherwise learn which session to resume. The route answers only for the caller's own active client claim.

## Verification

See [`validation-home-nw-17.md`](validation-home-nw-17.md). Local implementation and tests pass; deployment and live Standard checks are pending.
