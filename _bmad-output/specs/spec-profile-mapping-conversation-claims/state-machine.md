# Conversation Claim Lifecycle

This companion carries the load-bearing records, transitions, and acceptance
cases that would make the kernel unwieldy. Home owns the decisions; endpoints
only detect locally, submit opaque mapping IDs, and act after a grant.

## Records

| Record | Required meaning |
| --- | --- |
| Wake Mapping | An opaque mapping ID, one household-wide phrase or alias, one authorized Hermes Profile, and an active/revoked state. Several mappings may point to the same Profile; one phrase may not point to two Profiles. |
| Device Grant | An explicit device-to-mapping authorization, Room, and capability boundary. The “all current Profiles” pairing choice expands to the mappings present at approval time and is not a future wildcard. |
| Wake Claim | The existing v1 claim shape: authenticated device identity plus opaque mapping ID, observation, acoustic evidence, and `ready` availability. It contains no Profile ID, prompt, transcript, or audio. |
| Conversation Claim | Home-owned binding of winning device, Room, mapping, Profile, Hermes Session, configuration revision, and lifecycle timestamps/close reason. |

## Main flow

1. An endpoint recognizes a locally configured phrase and resolves it to an
   authorized mapping ID from its current Home snapshot.
2. The endpoint submits a Wake Claim. Home authenticates the device, checks
   the grant, mapping, Room, availability, and current configuration, then
   admits it to the existing bounded arbitration round.
3. Home arbitrates only among eligible claims in that Room. It uses the Home
   monotonic receive clock, the existing 250 ms window, acoustic evidence, and
   per-Room priority. Each Room has its own pending round, so unrelated Rooms
   may arbitrate at the same time. At most one device is granted per Room.
4. The winner receives a claim-specific grant and Home creates or binds an
   independent Hermes Session for the resolved Profile. Home returns only
   `ready` or `unavailable` with a safe reason and an opaque conversation
   handle; the endpoint proceeds to capture only after `ready`.
5. While the conversation is active, every non-empty follow-up uses the same
   Conversation Claim, Profile, and Session. A different wake phrase cannot
   retarget it.
6. After response playback, the bounded idle timer begins. Each non-empty
   follow-up resets it. An in-flight turn, capture, or playback cannot be
   expired by that timer. The initial default is 8 seconds, and the duration
   remains configurable.
7. `stop`, disconnect, revocation, or idle expiry closes the Conversation
   Claim. A later wake creates fresh Profile and Session state.

## Room and Profile isolation

- Two Rooms hearing the same phrase arbitrate independently. Each Room may
  create one winner and one independent Session for the same Profile.
- A winner or failed winner in one Room never promotes, suppresses, or steals a
  claim in another Room.
- If a second independent Session cannot be created, the later wake fails
  closed rather than joining the existing conversation.
- Session startup failure does not promote another device or retry automatically;
  the user repeats the wake.
- A Watch surface may observe permitted state later, but it does not own,
  interrupt, or retarget this Conversation Claim.

## Configuration and freshness

- A mapping remap is atomic for new wakes. An existing Conversation Claim keeps
  its bound Profile and Session.
- Removing a mapping rejects new claims. It does not retarget an active
  conversation; explicit mapping, Profile, or endpoint revocation is required
  to hard-stop active work.
- A stale, removed, or unauthorized mapping ID is rejected. The endpoint must
  refresh its mapping snapshot before retrying. Devices fetch on pairing,
  startup, and reconnect, poll every 30 seconds while active, and refresh
  immediately after this rejection. Snapshot replacement is atomic; Home never
  substitutes a different Profile or phrase.
- A new Profile or Wake Mapping is not added to devices that used the
  all-current pairing choice. It requires a new explicit approval.
- An explicit Room move applies to new wakes only. An active Conversation Claim
  keeps its original Room, device, Profile, and Session; there is no automatic
  handoff.

## State table

| State | Entry | Allowed next states | Key rule |
| --- | --- | --- | --- |
| `detected` | Local detector recognizes an authorized phrase | `claiming`, `rejected` | No capture or prompt yet. |
| `claiming` | Device submits a Wake Claim | `granted`, `denied`, `refresh_required`, `unavailable` | The claim is checked against Home's current authority. |
| `granted` | This device wins its Room's round | `binding`, `denied` | Losers do not retry or get promoted for the same utterance. |
| `binding` | Home resolves Profile and creates/binds a Session | `active`, `unavailable` | `ready` permits capture; `unavailable` is safe and terminal for this wake. |
| `active` | Session is ready and the endpoint is conversing | `active`, `closing`, `revoked`, `unavailable` | Follow-ups keep the original binding. |
| `closing` | `stop`, idle expiry, or normal conversation close | `closed` | No later prompt reuses the closed claim. |
| `revoked` | Endpoint, Profile, or mapping authorization is revoked | `closed` | Interrupt reachable work and ignore late frames. |
| `closed` | Conversation ended | `detected` for a future fresh wake | A new wake creates fresh Profile/Session state. |

## Acceptance matrix

| Case | Expected result |
| --- | --- |
| Two aliases point to one Profile | Both resolve to that Profile; each may be granted independently. |
| One phrase points to two Profiles | Configuration publication is rejected. |
| Two eligible devices claim in one Room | Exactly one grant; the loser stays silent and is not promoted. |
| Same phrase is heard in two Rooms | Each Room arbitrates independently and may create its own Session. |
| Second same-Profile Session unavailable | Later wake fails closed; it does not join the first. |
| Non-empty follow-up | Same Profile and Session; idle window resets. |
| Different wake during active conversation | Cannot retarget the active claim. |
| Mapping remapped while active | Existing claim is unchanged; new wakes use the new mapping. |
| Stale or removed mapping claim | Rejected; device refreshes; no Profile fallback. |
| Unavailable or revoked Profile/mapping | Rejected; no fallback Profile, capture, or Hermes turn. |
| Hermes Session unavailable after claim | Safe `unavailable`; no capture, promotion, retry, or attachment to another Session. |
| New Profile after all-current approval | Not granted silently; explicit approval is required. |
| Explicit revocation during active work | Close/interrupt the claim, stop reachable local work, and ignore late messages. |
