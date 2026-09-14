# Credential lifecycle

This companion defines the behavior of the pairing and endpoint-credential
slice. It does not choose the HTTP framework, QR library, at-rest digest or
encryption primitive, or route-discovery mechanism; those choices remain
implementation details or open questions in `SPEC.md`.

## Enrollment state

| State | Meaning | Allowed next state | Access granted? |
| --- | --- | --- | --- |
| `offered` | Home has created a five-minute enrollment code. | `pending`, `expired`, `cancelled` | No |
| `pending` | An endpoint has presented the code and awaits trusted approval. | `approved`, `rejected`, `expired`, `cancelled` | No |
| `approved` | The trusted control plane accepted the endpoint identity and requested scope. | `consumed` | Not until credential issuance completes |
| `consumed` | Home issued the endpoint credential and invalidated the enrollment code. | terminal | Yes, through the issued credential |
| `expired` / `cancelled` / `rejected` | The code or request can no longer be approved. | terminal | No |

Rules:

- A code is single-use. A second scan after `consumed` is denied, even if the
  code has not reached its five-minute time limit.
- Scanning creates a pending request; it is not approval and cannot start a
  Hermes session, claim a wake, or read household configuration.
- Approval records the human-approved endpoint identity and requested scope.
  The first pilot approval surface is the existing TUI; its exact wording and
  accessibility behavior remain open questions.
- Credential issuance and code consumption succeed or fail together. A failed
  issuance leaves no usable credential and cannot silently consume a request.

## Endpoint credential state

| State | Accepted for new requests? | Notes |
| --- | --- | --- |
| `active` | Yes | Expires 90 days after issue; renewal is available in the final 14 days. |
| `expired` | No | Reconnection requires re-enrollment or an approved rotation. |
| `revoked` | No | Home hard-stops new activity and rejects late actions. |
| `replaced` | Only during a bounded overlap | Rotation has issued a different credential; the old one is accepted for at most 10 minutes to recover a lost rotation response. |

The credential is externally opaque and endpoint-scoped. Its representation,
at-rest protection and revocation storage are not yet fixed. The lifetime,
overlap, and retry-idempotency policy is fixed. Regardless of mechanism:

- it is distinct from the Home admin credential and any Hermes bearer token;
- it never appears in configuration snapshots, URLs, ordinary logs,
  diagnostics, prompts, transcripts, Watch content, or audio;
- it cannot carry an arbitrary Profile ID or grant capabilities by naming
  them; Home resolves scope from its own state;
- re-enrollment issues a different credential and invalidates the old one;
- an issued credential expires after 90 days; Home offers automatic renewal
  during the final 14 days;
- a successful rotation activates the new credential and permits the old one
  for at most 10 minutes only to recover a lost rotation response; a repeated
  request with the same request ID returns the same replacement result, while a
  different request ID cannot create another replacement for that credential
  generation;
- revocation is authoritative at Home and does not depend on the endpoint
  being online.

The endpoint creates a rotation request ID and binds it to the credential
generation it is presenting. The request ID is not a credential and cannot be
reused for a later generation. Home must make the same request safe to retry
without issuing multiple valid replacements. Explicit revocation invalidates
the retry path immediately.

## Wire representation and migration boundary

Production Home credentials are 32 random bytes encoded as unpadded base64url.
The resulting 43-character ASCII value is opaque: it contains no endpoint ID,
Profile ID, capability, expiry, or route information. Deterministic tests may
use readable fixture values, but a fixture value is not a production format.

The only ordinary endpoint authentication form is:

```http
Authorization: Device <base64url-device-credential>
```

Credential issuance and rotation may return the credential exactly once in an
authenticated, TLS-protected control-plane response:

```json
{
  "schema": 1,
  "device_id": "device-opaque-id",
  "credential": "<base64url-device-credential>",
  "generation": 1,
  "issued_at": "2026-09-14T19:00:00Z",
  "expires_at": "2026-12-13T19:00:00Z"
}
```

The response is not a configuration snapshot and must not be logged, cached,
placed in a URL, or forwarded through an ordinary bridge event. An endpoint
stores only the credential value in its platform secure store; it does not
parse or derive authority from the value.

There is deliberately no endpoint API that converts a personal Hermes bearer
or the fork-only `/voice-session` credential into a Home credential. A surface
migrating from the fork performs this local, idempotent transition:

1. preserve its local Profile ID, device label, and Profile-scoped history;
2. retain the old credential only in a rollback-only secure-store slot until
   the migration retirement gate;
3. complete the approved Home pairing flow and store the newly issued Home
   credential in a separate secure-store slot;
4. mark the target binding usable only after a Home bridge authorization
   succeeds for the same endpoint; and
5. leave the target unavailable, preserve the source, and submit no turn if
   pairing, secure storage, or authorization fails.

Repeating the transition for the same local Profile updates the existing Home
binding; it never creates another Profile or history, derives a credential from
the old token, or replays a turn whose delivery was uncertain. The old slot is
removed only by the explicit post-migration retirement decision.

## Storage boundary

Home must not persist a bearer credential in SQLite. For each credential it
stores the endpoint binding, generation, status, timestamps, and a keyed digest
calculated with a root secret that is held outside the database. The exact
digest primitive and secret-store adapter remain implementation choices.

The replacement needed for a lost rotation response is different: Home may
retain it only as an authenticated-encryption record keyed by the root secret,
bound to the endpoint, credential generation, and rotation request ID, and
only until the 10-minute overlap ends. It is deleted or rendered unusable on
expiry, replacement completion, or revocation. Loss of the root secret fails
closed and requires endpoint re-enrollment.

An endpoint stores its Home credential in the platform secure store or OS
keyring. A plaintext environment file, configuration snapshot, URL, ordinary
history, or log is not an acceptable paired-credential store. If the endpoint
cannot provide secure storage, pairing stops before Home issues the credential.

If an endpoint misses expiry, its old credential is rejected and the endpoint
must complete pairing again. Explicit revocation ends any rotation overlap
immediately.

## Approval surface

The first pilot trusted control plane is the existing TUI. A pending request is
not access. The review must show:

- Home identity or route context and the request deadline;
- endpoint label or type and a short confirmation code that the candidate
  endpoint also displays;
- requested Room, capabilities, and Profile mappings.

The approver uses the trusted Home admin boundary to choose bounded scope or
reject the request. Home commits approval and credential issuance atomically.
No credential exists for rejected, cancelled, expired, or unapproved requests.
The endpoint cannot self-approve or grant itself capabilities, a Room, or
Profile mappings. A display may show the enrollment QR but cannot approve
itself. If no trusted TUI is available, the request stays pending until it
expires. A phone is a later adapter of this same review, approve, and reject
contract; it does not create a second policy.

## Revocation effects

After Home commits revocation, the endpoint may not start or continue new
capture, follow-up, Watch, or notification activity. If a bridge or session is
reachable, Home asks it to interrupt the active work and stop endpoint capture
or playback. Any late frame or action carrying the revoked endpoint identity
is ignored or rejected without mutating household state. Reconnection must
perform fresh authorization; an uncertain turn is never replayed.

Profile mapping, route roaming, and the transparent Hermes session bridge are
separate slices. This companion gives those slices the credential boundary;
it does not make the credential an assistant, Profile selector, or session
authority.
