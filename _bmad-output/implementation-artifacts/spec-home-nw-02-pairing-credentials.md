---
id: HOME-NW-02
title: Pair endpoints with QR enrollment and limited credentials
status: in-progress
route: dispatch
baseline_commit: 0519bb7f30aba5f5042b0459b285a4444c7c0f8e
context:
  - _bmad-output/specs/spec-home-service-foundation/SPEC.md
  - _bmad-output/specs/spec-home-service-foundation/credential-lifecycle.md
  - src/hermes_home/api/application.py
  - src/hermes_home/auth/static.py
  - src/hermes_home/domain/arbitration.py
  - src/hermes_home/domain/configuration.py
  - src/hermes_home/runtime.py
  - src/hermes_home/storage/sqlite.py
  - src/hermes_home/bridge/standard.py
created: 2026-09-14
updated: 2026-09-14
source: _bmad-output/specs/spec-home-service-foundation/SPEC.md
---

# Implementation Spec: HOME-NW-02

## Problem

Home currently authenticates devices from a static credential map. It cannot
enroll an endpoint, wait for trusted-admin approval of a bounded scope, persist
credential state across restart, or rotate and revoke credentials. NW-03 owns
the public bridge route; this story supplies the pairing and credential
authority that route will consume.

## Approach

Implement a framework-independent credential lifecycle, then attach it to the
existing application and runtime seams:

- `domain/credentials.py` owns enrollment and credential state transitions,
  endpoint identity, expiry, renewal overlap, bounded scopes, and idempotent
  rotation decisions. It generates a Home-owned `device_id`; the endpoint's
  stable `endpoint_id` is identity data, not a credential.
- `storage/credentials.py` persists endpoint binding, generation, status,
  timestamps, keyed digests, and short-lived encrypted replacement material
  in SQLite. The operator-provided root secret is 32 bytes encoded as 64 hex
  characters in `HERMES_HOME_CREDENTIAL_ROOT_SECRET_FILE`; Home never creates
  it. HMAC-SHA256 with domain-separated input provides keyed digests. A key
  derived as `HMAC-SHA256(root, "hermes-home/replacement-key/v1")` drives
  AES-256-GCM for retry material; each record has a fresh 12-byte nonce and
  associated data binding its device, generation, and request ID. Credential
  digests use `HMAC-SHA256(root, "hermes-home/credential-digest/v1\\0" +
  token)`. Missing or malformed key material fails closed.
- `auth/credentials.py` validates opaque `Device` credentials with
  `hmac.compare_digest` and returns the endpoint binding, generation, and
  scope. Authentication has two explicit modes: `paired` when the root-secret
  file is configured, or `legacy` when only the existing static credential
  file is configured. If neither source is configured, endpoint authentication
  and pairing are disabled. The modes never fall back to one another;
  configuring both sources is a startup error so revocation cannot be bypassed
  by a static credential.
- `api/application.py` exposes the exact Home-owned v1 control plane below.
  Admin actions use the existing Home bearer token; endpoint self-actions use
  `Device` auth bound to the path's `device_id`. Credentials appear only in
  the one-time offer, consume, or replacement response and never in URLs,
  snapshots, logs, diagnostics, metrics, or approval listings.
- `runtime.py` wires the durable store, root-secret file, and paired-mode
  authenticator. Existing configuration-only deployments remain unchanged in
  explicit legacy mode.

The QR payload is only a five-minute `enrollment_code` generated with at least
128 bits of randomness (the implementation uses `secrets.token_urlsafe(24)`)
and submitted in a request body. The endpoint also receives an eight-character
uppercase `confirmation_code` from an unambiguous display alphabet; it is
display-only and never authorizes anything. Home stores only a keyed digest of
the enrollment code. Scanning creates a pending request and cannot grant
access. Approval and initial credential issuance/consumption are atomic.

An issued credential is valid for 90 days. Endpoint renewal is available only
in the final 14 days; an expired credential must re-enroll. A successful
replacement permits the old credential for at most 600 seconds solely to
retry a lost response. The same request ID returns the same replacement for
that generation; a different request ID cannot mint another replacement.
Explicit revocation invalidates both generations and retry material
immediately. Re-enrollment creates a new credential generation and invalidates
the prior one.

### v1 control-plane routes

All JSON requests require `Content-Type: application/json`. Responses use the
existing `{"schema": 1, ...}` envelope. No route accepts a credential in a
path or query string.

| Route | Caller | Contract |
|---|---|---|
| `POST /api/v1/enrollment/offers` | Admin | Body contains `schema` and optional `expires_in_seconds` (fixed to at most 300). Response returns the one-time `enrollment_code` and `expires_at` for QR presentation. |
| `POST /api/v1/enrollment/requests` | Unpaired endpoint | Body contains `schema`, `enrollment_code`, `endpoint_id`, `label`, `type`, `requested_rooms`, `requested_capabilities`, `requested_profile_mappings`, and `secure_storage: "platform_secure_store"`. Response returns `request_id`, `confirmation_code`, and `expires_at`. |
| `GET /api/v1/enrollment/requests` | Admin | Returns pending or terminal request metadata, including endpoint identity, confirmation code, deadline, requested scope, and display-only profile mappings; never the enrollment code or credential. |
| `POST /api/v1/enrollment/requests/{request_id}/approve` | Admin | Body contains `schema` and `scope: {rooms, capabilities}`. The approved rooms and capabilities must be subsets of the request and current Home configuration. Response is redacted request state. |
| `POST /api/v1/enrollment/requests/{request_id}/reject` | Admin | Body contains `schema` and an optional bounded `reason`. Response is redacted terminal state. |
| `POST /api/v1/enrollment/requests/{request_id}/consume` | Unpaired endpoint | Body repeats `schema`, `enrollment_code`, and `secure_storage`. Only an approved request with a ready platform secure store returns the one-time opaque credential and generated `device_id`. |
| `POST /api/v1/devices/{device_id}/credentials/renew` | That endpoint | Body contains `schema`, client `request_id`, and expected `generation`. It is accepted only during the final 14 days and returns one replacement material. |
| `POST /api/v1/devices/{device_id}/credentials/rotate` | Admin | Body contains `schema`, client `request_id`, and expected `generation`. It may replace an active credential early for recovery or administration. |
| `POST /api/v1/devices/{device_id}/revoke` | Admin | Body contains `schema` and an optional bounded `reason`. Revocation is committed before notifying active-work observers. |

Identifiers and labels are non-empty UTF-8 strings of at most 128 characters.
`endpoint_id` is generated and retained by the endpoint in its secure store;
it is not a secret. `requested_rooms` and `scope.rooms` are duplicate-free
arrays of configured room IDs. `requested_capabilities` and
`scope.capabilities` are duplicate-free arrays; the only supported capability
is the literal `wake_claim`, and unknown capabilities are invalid.
`requested_profile_mappings` is an optional list of at most 16 records shaped
as `{"profile_id": string, "label": string}` with the same length bound;
Home displays it but never interprets it as authorization. Client
`request_id` values are non-empty strings of at most 128 characters and are
bound to one credential generation. Reasons are optional strings of at most
256 characters. `expires_in_seconds` is an integer from 1 through 300.

Malformed bodies return `400 invalid_request`; missing or invalid credentials
return `401 unauthorized`; a caller with valid identity but insufficient
authority returns `403 forbidden`; an unknown resource returns `404 not_found`;
an already-transitioned or conflicting request returns `409 conflict`; an
expired or consumed code returns `410 expired_or_consumed`; and unavailable
durable storage returns `503 service_unavailable`. Every error uses the
existing redacted schema envelope and contains no secret or submitted value.

The only initially supported approved capability is `wake_claim`. Its scope
is enforced before arbitration: the authenticated device must have that
capability and the requested wake mapping must resolve to an approved room.
Future capabilities are rejected until explicitly added to the policy.
Requested Profile mappings are retained only as display data for approval;
they never become credential authority and are owned by NW-05.

## Boundaries

In scope: endpoint enrollment, trusted-admin approval, limited endpoint
credentials, explicit paired/legacy auth modes, persistence, expiry, rotation,
revocation, restart recovery, scope enforcement for wake claims, a revocation
observer port, secret-safe errors, and focused domain/API/runtime tests.

Out of scope: Hermes bearer credentials, Profile mapping authority and
conversation claims, public bridge/WebSocket routes, route roaming, QR image
rendering, cryptographic hardware attestation, prompts, transcripts, audio,
and client UI changes. The TUI remains the first approval client but is not
changed in this Home-only story. NW-03 will consume the revocation observer to
interrupt reachable bridge work; this story makes authorization fail closed
immediately.

## Code Map

- `src/hermes_home/auth/static.py` — current static auth seam to preserve and
  replace for durable credentials.
- `src/hermes_home/api/application.py` — versioned HTTP dispatch and stable
  error envelope and the route/schema contract above.
- `src/hermes_home/runtime.py` — settings and production wiring.
- `src/hermes_home/storage/sqlite.py` — SQLite ownership and locking pattern.
- `src/hermes_home/domain/configuration.py` — room and device validation used
  when bounding requested scope.
- `src/hermes_home/domain/arbitration.py` — wake-mapping admission and the
  boundary where credential scope must be enforced.
- `src/hermes_home/bridge/standard.py` — existing device-auth consumer that
  must accept the durable authenticator without gaining bridge-route scope.
- `_bmad-output/specs/spec-home-service-foundation/credential-lifecycle.md` —
  normative lifecycle, storage, and approval rules.

## Tasks & Acceptance

- [ ] Add pure lifecycle types and transitions for offer, request, approval,
  consumption, expiry, re-enrollment, rotation, replacement, and revocation,
  including confirmation-code and secure-storage semantics.
- [ ] Add SQLite persistence with schema migration, atomic transitions, keyed
  digests, encrypted short-lived retry material, and restart recovery.
- [ ] Add durable `Device` authentication with scope and constant-time
  comparison; enforce mutually exclusive paired and legacy modes so revoked
  credentials cannot reach a static fallback.
- [ ] Add the exact admin/endpoint control-plane handlers and stable error
  mappings listed above, without changing the legacy configuration contract.
- [ ] Enforce `wake_claim` capability and approved-room scope before the
  arbitration engine admits a durable-credential claim.
- [ ] Add a redacted `RevocationObserver`/event port, committing revocation
  before best-effort active-work notification.
- [ ] Wire settings and secret loading; keep credentials out of repr, logs,
  errors, metrics, and serialized snapshots; reject invalid root-secret files.
- [ ] Cover single-use codes, approval boundaries, idempotent rotation,
  overlap expiry, re-enrollment, secure-storage rejection, scope enforcement,
  revocation observers, restart recovery, and HTTP error behavior with focused
  tests.
- [ ] Run focused tests, `ruff check src tests`, and `ruff format --check src tests`; record results in
  `_bmad-output/implementation-artifacts/validation-home-nw-02.md`.

Acceptance requires that an unapproved scan cannot grant access; only a
trusted admin can approve a bounded endpoint scope; missing secure-storage
readiness is rejected before issuance; a paired-mode restart preserves
approved/revoked state without retaining plaintext credentials; static mode
cannot bypass paired-mode revocation; wake claims cannot exceed approved
scope; old, expired, revoked, and replayed material is rejected; the
revocation observer receives a redacted event; and the full test/lint/format
checks pass without changing NW-03 or sibling repositories.

## Implementation Notes

Pending implementation.

## Open Questions

None. The control-plane paths and field shapes, credential modes, scope
policy, code split, secret format, error mapping, and revocation boundary are
now explicit Home-owned decisions. NW-03 will define the separate approved
bridge route and envelope.

## Spec Change Log

- 2026-09-14 — Drafted after codebase and canonical lifecycle review.
- 2026-09-14 — Revised after review: separated codes, fixed auth-mode
  fallback, specified routes and scope enforcement, added secure-storage and
  revocation boundaries, and resolved crypto/storage details.
- 2026-09-14 — Final review pass: named request fields, error statuses, and
  display-only Profile mapping data.
- 2026-09-14 — Owner approved the implementation scope; status moved to
  `ready-for-dev`.

## Review Triage

Owner-approved; implementation may proceed.
