---
id: SPEC-profile-mapping-conversation-claims
updated: 2026-09-13
companions:
  - state-machine.md
  - ../spec-home-service-foundation/SPEC.md
  - ../../../docs/contracts/v1/README.md
  - ../../../docs/contracts/v1/configuration.schema.json
  - ../../../docs/contracts/v1/wake-claim.schema.json
  - ../../planning-artifacts/architecture/architecture-hermes-relay-home-2026-09-12/ARCHITECTURE-SPINE.md
sources:
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/sources/prds/prd-hermes-home-next-wave-2026-09-13/prd.md'
  - '~/Documents/Vaults/Personal Vault/projects/hermes-home/slices/next-feature-slate-2026-09-13.md'
---

> **Canonical contract.** This SPEC and `state-machine.md` define the Home-owned
> Profile routing and conversation-claim slice. The foundation spec and v1
> contract companions remain authoritative for the service and claim primitive.

# Profile Mapping and Conversation Claims

## Why

Shared displays and other paired endpoints need to hear several household wake
phrases without receiving personal Hermes tokens or accidentally crossing
Profiles. This slice gives Home one authoritative mapping and claim boundary:
the right device wins locally, the selected Profile stays fixed through
follow-ups, and a second Room cannot steal or merge into an existing
conversation.

## Capabilities

- **CAP-1**
  - **intent:** Home resolves household-wide wake phrases to authorized Hermes
    Profiles, including multiple distinct aliases for one Profile.
  - **success:** A valid configuration accepts several aliases for one Profile,
    rejects a phrase assigned to two Profiles, and returns the same Profile
    meaning regardless of Room or endpoint.

- **CAP-2**
  - **intent:** A trusted approver can grant a device one or more Wake Mappings,
    including all mappings for currently available Profiles as one explicit
    pairing choice.
  - **success:** Approval produces visible per-device grants that can be
    narrowed or revoked; the all-current choice is a snapshot, and a Profile
    created later is not added without another approval.

- **CAP-3**
  - **intent:** Home can choose at most one eligible winner for a wake within
    each Room.
  - **success:** Deterministic fixtures prove the existing bounded arbitration
    window, acoustic evidence and availability checks, per-Room priority,
    independent arbitration in two Rooms, and no loser promotion after failure.

- **CAP-4**
  - **intent:** Home can bind a winning device and Wake Mapping to one Profile
    and Hermes Session for an Active Conversation.
  - **success:** Every non-empty follow-up uses the original Profile and
    Session without a new wake; a different phrase cannot retarget the active
    binding, and closure creates fresh state for the next wake.

- **CAP-5**
  - **intent:** Home can keep independent conversations for the same Profile in
    different Rooms.
  - **success:** Two Rooms can hold separate Sessions for the same Profile
    without joining, stealing, or retargeting; if the second Session cannot be
    created, its wake fails closed and does not attach to the first.

- **CAP-6**
  - **intent:** Home can close an Active Conversation safely after inactivity
    or an explicit termination event.
  - **success:** The idle timer starts after response and playback, resets after
    each non-empty follow-up, never expires an in-flight turn, capture, or
    playback, and closes on `stop`, disconnect, revocation, or expiry.

## Constraints

- Home is authoritative. Endpoints submit an opaque Wake Mapping ID with their
  authenticated device identity; they never submit an arbitrary Profile ID,
  phrase, prompt, transcript, audio, or Hermes bearer token.
- After a claim is granted, Home opens the ordinary Hermes Session behind the
  bridge and returns only `ready` or `unavailable` with a safe reason. Capture
  begins only after `ready`; the endpoint receives an opaque conversation handle
  and never a Hermes token.
- Wake phrase meaning is household-wide. Duplicate or ambiguous phrases across
  Profiles are rejected before publication; the same phrase may be enabled in
  multiple approved Rooms or endpoints only for its one Profile.
- Room-local arbitration uses Home's monotonic receive clock and the existing
  250 ms claim window. A failed winner never promotes a loser and requires a
  new wake.
- Arbitration state is partitioned by Room. Unrelated Rooms may have
  simultaneous claim windows; one Room's pending round must never suppress or
  serialize another Room's wake.
- Mapping edits are atomic for new wakes. A remap cannot change an Active
  Conversation's Profile or Session; explicit endpoint, Profile, or mapping
  revocation is the hard-stop path.
- Stale, removed, or unauthorized claims fail closed until the endpoint
  refreshes. Home never guesses a Profile or falls back to another mapping.
- Devices fetch the authorized, revisioned mapping snapshot on pairing, startup,
  and reconnect; poll every 30 seconds while active; and refresh immediately
  after a stale-claim rejection. Snapshot replacement is atomic, with no new
  Hermes WebSocket channel and no stale fallback.
- An unavailable or revoked Profile or mapping fails closed and cannot fall
  through to another Profile. An endpoint may show the active Profile state
  without exposing a personal credential.
- The all-current-Profiles approval choice is a snapshot, not a standing
  wildcard. Later Profiles and Wake Mappings require explicit authorization.
- A different Room may arbitrate the same phrase independently, but a Room's
  winner, failure, or active conversation cannot suppress, promote, or steal
  another Room's claim.
- Room reassignment is an explicit Home configuration change for new wakes. An
  active conversation remains bound to its original Room, device, Profile, and
  Session; there is no automatic handoff.
- Session startup failure does not promote another device, retry automatically,
  or attach to an existing conversation; the user repeats the wake.
- The claim payload remains content-free and Profile-free even as the
  configuration model evolves from the foundation's single device Profile
  reference to explicit Wake Mapping grants.
- The exact idle duration and final acoustic evidence encoding remain
  configuration or hardware-contract choices; the initial idle default is 8
  seconds after playback, and neither choice changes routing isolation.

## Non-goals

- Implementing the final custom wake detector or unrestricted open-microphone
  inference.
- Choosing the final Hermes bridge transport or replacing the ordinary Hermes
  session protocol.
- Building endpoint presentation, pairing UI, Watch View, notification, or
  cross-device conversation handoff behavior.
- Defining the final acoustic calibration and evidence encoding for every
  hardware model.
- Allowing a stale endpoint to continue locally when Home cannot verify its
  mapping or Session.

## Success signal

A deterministic Home test fixture can configure household-wide aliases, grant
them to selected devices, resolve simultaneous claims independently per Room,
bind each winner to the correct Profile and Session, carry multiple follow-ups
without cross-routing, close the conversation safely, and reject stale,
revoked, removed, or unsupported claims without creating a Hermes turn.

## Assumptions

- Endpoints perform local wake detection and hold an authorized mapping
  snapshot; Home verifies the mapping, device grant, Room, and active
  configuration before capture or a Hermes turn proceeds.
- The foundation service can represent or create a Hermes Session through a
  later Home bridge adapter; this slice defines the binding and policy rather
  than the final bridge transport.
- A device has one current Room assignment for arbitration. An explicit move
  changes that assignment for future wakes only.
