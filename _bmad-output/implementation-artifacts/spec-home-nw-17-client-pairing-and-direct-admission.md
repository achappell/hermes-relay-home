---
title: 'HOME-NW-17 — Pair personal clients from a Home page and admit their direct conversations'
type: 'feature'
created: '2026-09-23'
status: 'draft'
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

**Compatibility decision:** Evolve `/api/v1/*` in place. `client_claim` is a new value in `SUPPORTED_CREDENTIAL_CAPABILITIES`. A scope without `client_grants` grants no client claim, matching the empty-grant precedents of HOME-NW-05 and HOME-NW-16. Existing credentials are unaffected.

## Resolved Direction

- **Approval lives on a Home page for now.** The page is the first trusted surface for FR-28. It is built on public API routes, so the iOS and Android control-plane stories (3-I, 3-A) can later approve through the same calls without a second pairing model.
- **Profile owners approve access to their own Profile.** Each Profile is configured as `shared` (for example Spark or a family Profile) or owned by one person. The admin may grant a shared Profile on the pairing page. A grant to an owned Profile becomes `pending_owner` until the owner approves it from a client already paired to that Profile; the grant appears there as an approval request showing the device label, type, and requester. Bootstrap exception: when an owned Profile has no paired client yet, the admin may approve the first grant on the page, and that approval is recorded. Every client of a Profile can list the devices currently holding it, so no grant is silent. Revocation by the owner or the admin is immediate.
- **Home authentication does not depend on the network path.** Clients currently reach Home over Tailscale, and the pairing link carries the tailnet address, so a client without Tailscale cannot pair yet; home-network routes remain HOME-NW-04 roaming work. Home never uses Tailscale identity headers for authorization. The pairing page signs in with the admin credential, and owner approval uses the owner's paired Device credential. Either works unchanged behind a different route or proxy.
- **A pairing link carries everything the client needs.** The QR code and the copyable text encode one link, `hermes-home://pair?home=<https base URL>&code=<enrollment code>`. A client needs nothing else: no URL typing, no pasted credential, no handle. The short code (for example `K7Q-4MX`) is shown for typing when a camera is not available, with the Home base URL shown beside it.
- **Profiles are granted per client, not per Room.** A client credential carries a list of `client_grants`, one per approved Profile, each with an opaque `grant_id` and a display label. Device configuration returns the list of `{grant_id, label}`; Profile IDs stay on Home. The TUI's Profile switcher and the mobile Profile pickers choose by `grant_id`.
- **No Room, no arbitration, no preemption.** A personal client is not a household room device. Its claims are keyed by device and grant. Several terminal windows on one laptop are several clients of the same device, so a device may hold several active claims per grant, bounded by `HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE` (default 8); excess claims are denied `claim_limit`.
- **The client owns the session lifecycle, Home owns authority.** A client claim has no idle timer and no first-turn session binding. It stays active while the client holds its bridge connection, through the existing in-process reconnect path, and closes when the client sends `conversation.close`, when its connection is gone past the reconnect grace (`HERMES_HOME_CLIENT_RECONNECT_GRACE_SECONDS`, default 120), or on revocation. Home never decides that a conversation has ended because the user paused; the Room idle tail and first-open expiry remain wake/tap-claim rules only.
- **Session operations are stock Standard operations.** After `conversation.open`, a client-claim connection may call `session.list`, `session.create`, `session.resume`, `session.most_recent`, `session.title`, and `session.history` with their stock Standard semantics. Home forwards each to the claim's Profile and nowhere else, and rewrites Standard Session IDs to opaque, Home-issued `session_ref` values that are valid only for that grant. A `session_ref` from another grant or device is `session_unavailable`. `session.delete`, `session.branch`, `session.redirect`, and control/configuration operations are not exposed.
- **Sessions are per Profile, as in a CLI.** `session.list` returns the Profile's Standard sessions, the same view a Hermes CLI signed in to that Profile would see, including conversations started by Room devices (Puck, Touch panel, W/K) and by other paired clients, so a conversation started anywhere can be resumed from any client paired to the same Profile. The list is bounded (default 50, newest first) and carries only `session_ref`, title, last-active time, message count, and whether another active claim currently holds it.
- **Resume never replays.** Resuming a session restores Hermes's stored history; any turn left uncertain by a disconnect stays unresolved and is reported, never re-sent.
- **Long-lived pairing through automatic renewal.** The 90-day credential and 14-day renewal window stay as implemented. Clients renew on connect when inside the window, and the contract documents that obligation. A client that is unused for 90 days must pair again; the page offers "pair again" for an expired device with its previous Profiles preselected.
- **Pending approval is a typed state.** Consuming a request that is still pending returns `approval_pending` instead of the generic `conflict`, so a client can poll plainly and show "waiting for approval on the Home page".

## Proposed Contract

### Pairing link

```text
hermes-home://pair?home=https://caticornqueen.example.ts.net&code=K7Q4MX
```

The client submits the existing `POST /api/v1/enrollment/requests` body, with `type` one of `tui`, `ios`, or `android`, and `requested_capabilities` including `client_claim`. It shows the returned confirmation code and polls `POST /api/v1/enrollment/requests/{request_id}/consume` until it receives credential material, `approval_pending`, or a terminal error.

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

### Session operations on a client-claim connection

The client opens the bridge with `conversation.open` and its handle, then manages sessions itself:

```json
{"jsonrpc": "2.0", "id": 7, "method": "session.list", "params": {"limit": 20}}
```

```json
{"jsonrpc": "2.0", "id": 7, "result": {"sessions": [
  {"session_ref": "sref-01J...", "title": "Grocery plan", "last_active_at": "2026-09-23T19:02:11Z", "message_count": 14}
]}}
```

`session.create` returns a new `session_ref`. `session.resume` takes a `session_ref` and returns the stock resume result with the reference in place of the Standard ID. A turn may be submitted only after the connection has an active session. `conversation.close` ends the claim; the Standard session stays stored in Hermes for later resume.

### Denial reasons

| Code | Meaning |
| --- | --- |
| `unauthorized` | Missing, revoked, expired, or superseded credential |
| `client_claim_unavailable` | Scope lacks `client_claim`, or the `grant_id` is not in the current scope |
| `profile_unavailable` | The grant's Profile is currently unavailable |
| `stale_configuration` | Claim names a revision older than current; refresh device configuration |
| `claim_limit` | This device already holds the maximum number of active client claims |

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
| `session.list` | This Profile's sessions only, bounded, opaque refs | No Standard IDs, no other Profile |
| `session.resume` with another grant's ref | `session_unavailable` | Refs are grant-scoped |
| `session.resume` for a session bound to another active claim (a live Puck turn, another window) | `session_busy`; the list marks it `active: true` | Never two claims driving one Standard session; retry once the other claim closes |
| `session.delete` or `session.branch` | `method_unavailable` | Not exposed to clients |
| Profile made unavailable mid-claim | Claim closes `profile_revoked` | Same path as wake and tap claims |
| Credential inside the renewal window | Client renews on connect | Idempotent renewal unchanged |
| Credential expired | `unauthorized`; client asks to pair again | Page preselects previous Profiles |
| Room busy with a Puck conversation | Client claim unaffected | Client claims never touch Room claims |

## Code Map

- `docs/contracts/v1/configuration.schema.json` and configuration storage: add Profile `ownership` (`shared` or an owner person ID).
- `src/hermes_home/domain/credentials.py`: grant states `active` and `pending_owner`, bootstrap rule, owner decisions, and holder listing; add `client_claim` to `SUPPORTED_CREDENTIAL_CAPABILITIES`; add `client_grants` (Profile ID and opaque grant ID) to `CredentialScope` with bounded validation, serialization, and "absent grants nothing" semantics; validate grants against available Profiles at approval; return `approval_pending` from `consume_request` for pending requests; accept endpoint types `tui`, `ios`, and `android`.
- `src/hermes_home/bridge/production.py`: migrate `conversation_claims` to allow client claims without a Room or wake mapping (nullable `room_id` and `wake_mapping_id`, plus `grant_id` and `claim_kind`); exempt client claims from the Room conflict query, idle timer, and first-open binding; enforce the per-device claim limit; close client claims on `conversation.close` or after the reconnect grace.
- `src/hermes_home/bridge/standard.py` and `src/hermes_home/bridge/endpoint.py`: forward the allowed session operations for client-claim connections to the claim's Profile; maintain the grant-scoped `session_ref` mapping; reject other session methods with `method_unavailable`.
- `src/hermes_home/api/application.py`: add the `/api/v1/client-claims` branch mirroring the tap-claim durable-context checks and `_discard_new_claim` recovery; add `client_grants` to device configuration; add the `/pair` page and `/pair/api/*` cookie-authenticated admin equivalents.
- `src/hermes_home/runtime.py`: read `HERMES_HOME_CLIENT_CLAIMS_PER_DEVICE` and `HERMES_HOME_CLIENT_RECONNECT_GRACE_SECONDS`.
- `docs/contracts/v1/README.md` and `docs/contracts/v1/client-claim.schema.json`: document the pairing link, client claim, grants, client session operations, and the client renewal obligation.
- `deploy/windows/README.md` and `install.ps1`: document the Tailscale Serve paths to publish (`/pair`, `/pair/api`, `/api/v1/enrollment/requests`, `/api/v1/client-claims`, `/api/v1/devices`, `/api/v1/bridge/ws`) and the client claim limit and reconnect grace parameters.

## Tasks & Acceptance

**Execution:**

1. Credential scope, capability, endpoint types, and `approval_pending`.
2. Claim-store migration, client claim route, claim limit, close, and reconnect grace.
3. Client-claim session operations and grant-scoped `session_ref` mapping.
4. Device configuration `client_grants`.
5. Profile ownership, owner-approval routes, and holder listing.
6. Pairing page with admin session, QR/code offer, pending approval with Profile selection, device list, revoke, and pair again.
7. Contract documentation, schema fixture, and deployment notes.

**Acceptance Criteria:**

- A new client pairs using only the pairing link or the short code and Home URL, plus one approval on the page.
- The page shows the device label, type, requested capabilities, and confirmation code before approval.
- A client credential with two grants can open independent conversations for both Profiles; it can never name a Profile ID.
- A client claim never changes, blocks, or supersedes a Room claim, and a Room claim never blocks a client claim.
- A client can create, list, resume, and title sessions for its Profile through the bridge, and a session started on one paired client can be resumed from another client paired to the same Profile.
- A client claim is never closed by inactivity; it closes on `conversation.close`, after the reconnect grace, or on revocation.
- No Standard Session ID or Profile ID reaches a client.
- Revoking the device or making a Profile unavailable closes active client claims.
- Renewal inside the window keeps a client paired past the original 90 days without re-pairing.
- A grant to an owned Profile does not become active until the owner approves it from a paired client, except for the recorded first-device bootstrap; a shared Profile can be granted by the admin directly.
- No Home authorization decision reads Tailscale identity or any other network-path identity.
- Admin routes remain unreachable from the tailnet except through the authenticated page.
- Focused tests, `ruff check src tests`, and `ruff format --check src tests` pass.

## Open Decisions

- Per-device claim limit (8 proposed) and reconnect grace (120 seconds proposed).
- Whether the page also offers "approve from a paired phone" once 3-I and 3-A exist; out of scope here.

## Consuming Stories

- TUI: pair from a link (`hermes-relay pair <link>`), store the credential in the platform secure store (macOS Keychain; Secret Service on Linux) and refuse to pair when none is available, keep pairings keyed per Home so one client can pair with more than one household, show and decide pending owner grants (`/approvals`) and Profile holders, choose Profiles by grant, claim on launch, and own the session lifecycle like a regular CLI: a new session by default, `--continue` for the most recent, `--resume <ref>`, and in-app `/sessions`, `/new`, `/resume`, and `/title`. Close the claim on quit, renew the credential automatically, and keep the legacy voice-session path as rollback.
- iOS and Android: scan the QR code, store the credential in platform secure storage keyed per Home, show and decide pending owner grants and Profile holders, and replace the operator-supplied disposable handles with client claims.

## Spec Change Log

- 2026-09-23: Drafted from the TUI Home migration investigation.
- 2026-09-23: Replaced Home-decided continuity (client idle timeout and automatic resume) with a client-owned session lifecycle, per Amanda: the TUI manages sessions like a regular CLI.
- 2026-09-23: Resolved per Amanda: owned Profiles require owner approval from a paired client (first-device bootstrap by admin); clients store credentials in the platform secure store; Tailscale is required for client reachability for now, but Home authorization never depends on Tailscale identity; clients keep pairings per Home.
- 2026-09-23: Resolved per Amanda: `session.list` shows the Profile's conversations from every surface, including Room devices and other paired clients.

## Verification

Pending implementation.
