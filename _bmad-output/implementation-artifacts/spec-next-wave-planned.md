---
id: SPEC-hermes-home-next-wave-planned
updated: 2026-09-17
kind: next-wave
canonical_sources:
  - ../specs/spec-home-service-foundation/SPEC.md
  - ../specs/spec-home-bridge-route-roaming/SPEC.md
  - ../specs/spec-profile-mapping-conversation-claims/SPEC.md
  - ../specs/spec-household-diagnostics-incident-review/SPEC.md
  - ../specs/spec-standard-hermes-compatibility-migration/SPEC.md
---

# Hermes Home next-wave stories

This artifact holds the next Home-owned stories that do not yet have a
dedicated contract directory. The existing foundation, bridge, Profile, and
diagnostics specifications remain authoritative for the earlier slices. These
stories are deliberately bounded: Home owns household authority and policy;
clients own presentation and device-specific evidence.

## Delivery order

Use the active nine-epic parents in `story-index.yaml` and the [approved delivery contract](course-correction-2026-09-23.md). NW is historical provenance, not a separate active epic. September 25 and October 9 targets are unscheduled pending estimation. HOME-NW-15 is broader later distribution; actual-household deployment is HOME-MIG-09.

## HOME-NW-07 — Freshness-bound typed-choice authority

### Outcome

Household surfaces can render a bounded choice supplied by Home and return one
authorized choice without inventing a command or silently acting on stale UI.

### Acceptance criteria

- Home issues choices with an opaque choice ID, source/session binding,
  revision, expiry, and a bounded action kind.
- A selection is accepted only from the authorized endpoint and active
  conversation that received it.
- A stale, duplicated, revoked, or unknown choice returns a typed unavailable
  result and creates no new turn or side effect.
- Choice labels and values obey the safe-content policy; credentials and
  Sensitive Entry values never enter the choice payload.
- The action is auditable with safe correlation and outcome fields, without
  recording private content.

### Dependencies

- HOME-NW-05 Profile and conversation claims.
- HOME-NW-03 bridge/session readiness.
- Existing TUI typed-choice rendering remains a client-owned adapter.

## HOME-NW-08 — Read-only Watch Safe Preview

### Outcome

A paired Watch can show one safe, read-only household snapshot without becoming
another conversation, configuration, or credential authority.

### Acceptance criteria

- Home returns only the authorized endpoint's safe current state, selected
  Profile label, route/health state, and bounded current-task summary.
- The preview cannot start, continue, stop, retarget, or replay a conversation.
- Revoked, expired, stale, or disconnected state is visible as unavailable and
  does not fall back to another endpoint or Profile.
- Sensitive Entry values, credentials, transcripts, audio, and private
  notification content are excluded or explicitly masked.
- The request is bounded to one endpoint and one current snapshot.

### Dependencies

- HOME-NW-02 endpoint credentials and HOME-NW-05 conversation claims.
- HOME-NW-06 safe diagnostics policy.
- iOS and Android Watch adapters remain separate implementation stories.

## HOME-NW-09 — Bounded single-device health check

### Outcome

Home can explain whether one authorized endpoint is reachable and which
boundary is unavailable without opening or changing a conversation.

### Acceptance criteria

- The check reports route, Home authorization, bridge readiness, and Standard
  Hermes readiness as distinct bounded stages.
- It never captures audio, creates a turn, changes a Profile, or replays an
  uncertain request.
- Each failure is typed, safe to display, and correlated without content,
  credentials, or precise household identifiers.
- A timeout and response-size bound prevent the check becoming a hidden
  streaming or diagnostics channel.
- The result distinguishes current health from the delivery state of any
  existing turn.

### Dependencies

- HOME-NW-03 approved bridge route.
- HOME-NW-06 diagnostics and safe-outcome vocabulary.

## HOME-NW-10 — Per-device permissions and masked Sensitive Entry

### Outcome

Home can grant each endpoint a bounded capability set and route protected
structured prompts without leaking their values.

### Acceptance criteria

- Permissions are explicit per endpoint and capability; discovery never grants
  access.
- `sensitive_entry` and `consequence_confirm` are explicit per-device
  capabilities, intersected with the current credential scope.
- Protected prompt values are memory-only, bounded, and excluded from ordinary
  responses, logs, diagnostics, previews, and notifications.
- Revocation and expiry remove access on the next authorized request and do
  not mutate an active conversation.
- Unauthorized, stale, and malformed requests fail closed with safe reasons.
- Configuration read/propose/apply and shared-artifact mutation belong to
  HOME-NW-12 and are not part of this story.

### Dependencies

- HOME-NW-02 credential lifecycle.
- HOME-NW-05 Profile/mapping ownership.
- HOME-NW-06 safe diagnostics and retention rules.

## HOME-NW-11 — Endpoint-scoped notifications and quiet hours

### Outcome

Home can deliver opt-in, safe notifications to an endpoint without turning a
notification into a conversation or exposing household content.

### Acceptance criteria

- Notification categories, endpoint scope, opt-in state, and quiet hours are
  explicit and revisioned.
- Delivery never starts a turn, changes a Profile, interrupts capture/playback,
  or becomes a hidden wake path.
- Revocation, expiry, and quiet hours prevent future delivery while preserving
  safe audit outcomes.
- Notification content is redacted or masked according to endpoint capability
  and Sensitive Entry policy.
- Clients can show unavailable or suppressed delivery without retrying a live
  turn.

### Dependencies

- HOME-NW-10 endpoint permissions.
- HOME-NW-06 diagnostics policy.
- iOS and Android notification adapters are separate client stories.

## HOME-NW-12 — Shared artifacts as propose, diff, and Apply

### Outcome

An authorized household surface can propose a shared artifact change, inspect a
safe diff, and apply it only after explicit approval against the expected
revision.

### Acceptance criteria

- A proposal is scoped to one artifact, endpoint, and expected revision.
- Home produces a deterministic, content-safe diff before Apply.
- Apply is explicit, atomic, revision-checked, and returns a typed conflict
  without overwriting a newer change.
- Proposals, diffs, and approvals exclude credentials and mask Sensitive Entry
  values.
- Rejected, expired, revoked, and superseded proposals cannot apply later.

### Dependencies

- HOME-NW-05 Profile and household identity.
- HOME-NW-10 permissions.
- HOME-NW-06 review and retention policy.

## HOME-NW-13 — Custom wake phrases after built-in mappings

### Outcome

The household can add explicit custom wake aliases while preserving built-in
mapping precedence and one authoritative Profile meaning.

### Acceptance criteria

- A custom alias is explicitly mapped to one existing authorized Profile and
  revision.
- Duplicate or ambiguous aliases are rejected before publication.
- Built-in mappings retain their documented precedence; custom matching never
  creates a second authority or arbitrary Profile selection.
- Endpoint grants, revocation, refresh, and stale revisions behave like other
  Wake Mappings.
- The alias path remains content-free with respect to credentials,
  transcripts, and audio.

### Dependencies

- HOME-NW-05 Profile mappings and conversation claims.
- Existing wake arbitration contract and client refresh behavior.

## HOME-NW-14 — Separate arbitrary-attachment transport

### Outcome

The product has an explicit decision and bounded transport for arbitrary
attachments instead of smuggling files through the ordinary Hermes channel.

### Acceptance criteria

- The attachment capability, endpoint scope, approval, size, media type,
  retention, and revocation rules are explicit before implementation.
- Ordinary text/session messages reject arbitrary binary payloads rather than
  silently forwarding them.
- A proposed attachment is inspectable as safe metadata and cannot apply
  without explicit authorization.
- Unsupported, expired, revoked, oversized, and unsafe attachments fail closed
  with no turn replay or hidden retry.
- Diagnostics and retention do not retain attachment content unless a separate
  approved incident policy allows it.

### Dependencies

- HOME-NW-10 permissions and Sensitive Entry handling.
- HOME-NW-06 diagnostics/retention policy.
- A separately reviewed attachment contract; this story does not invent a
  general file-sharing channel.

## HOME-NW-15 — Safe Linux and macOS deployment for other homes

### Outcome

An operator can run Hermes Home in another household on Linux or macOS and
follow a household-neutral guide without relying on Amanda's hosts, paths,
network, or credentials.

### Acceptance criteria

- Supported Linux and macOS versions and the required Python runtime are named;
  Linux uses a systemd service and macOS uses a launchd job with documented
  service-user behavior.
- Home runs with least privilege. Private data and operator-supplied secrets
  have restrictive ownership and permissions; secrets never appear in command
  arguments, service definitions, checked-in configuration, or logs.
- The listener stays loopback-bound by default. Installation does not open
  firewall ports or publish a public route. Any private-LAN exposure is a
  deliberate operator choice with authentication and network controls
  explained.
- Repeated install and upgrade preserve the SQLite database, configuration,
  and credentials, and provide a recoverable path for service/config rollback.
  Uninstall preserves operator data unless removal is explicitly requested.
- Household-neutral setup and deployment instructions cover prerequisites,
  installation, initial configuration and pairing, service control, health
  verification, backup and restore, upgrade and rollback, troubleshooting, and
  uninstall.
- Examples contain no personal hostnames, usernames, home paths, IP addresses,
  tokens, or tailnet-specific assumptions. Each supported install path is
  exercised from a clean Linux or macOS setup.

### Dependencies

- HOME-NW-02 credential lifecycle and HOME-NW-03 Home runtime.
- Existing Windows deployment is a reference, not a requirement to change
  that installer.
