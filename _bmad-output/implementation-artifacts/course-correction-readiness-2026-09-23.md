# Course-correction planning readiness — 2026-09-23

## Verdict: CONCERNS for implementation; approved backlog organization applied

The product direction, epic parents, ownership, release boundaries and acceptance overlay are approved. This is not authorization to skip detailed specification or promote new backlog stories to ready-for-dev. The user explicitly approved applying the backlog with those readiness gates retained.

## Remaining specification or evidence gates

- HOME-NW-17: verify each named session operation against the selected unmodified baseline; the draft is not upstream evidence.
- HOME-NW-17: reconcile the broad prohibition on exposing prompts/transcripts/audio with authorized conversation/history delivery; specify which sensitive/internal fields are actually withheld.
- HOME-NW-17: finalize owner authorization semantics before implementation; possession of a Profile grant must not silently broaden who may approve another device.
- HOME-MIG-09: record the actual deployment inventory and compatible recovery procedure before cutover.
- HOME-NW-13: custom wake phrase release inclusion remains an explicit scope decision.

## Structural review

All existing tracker identities and story states were preserved; the duplicate TUI STD-3 key was consolidated. Every tracked story resolves through the local index and manifest. Story specification references resolve. The read-only coordinator renderer joins all four course-correction worktrees. No runtime tests or live acceptance were performed for this documentation-only change.
