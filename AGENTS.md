# Hermes Home agent instructions

This repository owns the Hermes Home service: household configuration,
credential-bound wake claims, arbitration, and the service's local HTTP
contract. It is a BMAD delivery surface, not the product-planning authority.

## Ticket and BMAD changes

- Story identity and scope live in
  `_bmad-output/implementation-artifacts/story-index.yaml`.
- Formal delivery status lives in
  `_bmad-output/implementation-artifacts/sprint-status.yaml`.
- The local specification and validation artifacts are the evidence for
  closure. Do not infer Home completion from another repository's status.
- A Home-only ticket changes these local records and this repository's code;
  it does not require an edit to the TUI, iOS, Android, or agent repository.
- A shared household rule, protocol contract, or cross-surface dependency is
  recorded in the private product hub. The TUI coverage index is read-only
  context for applicability, ownership, evidence, and dependencies; it is not
  Home's status authority.

The external Hermes Home GitHub Issues board is active for Home story work.
Use the issue URLs recorded in `story-index.yaml`; agents may inspect and update
those Home-owned issues, including labels, comments, and status. Create an issue
only when the local story record has no existing issue URL. Keep changes scoped
to Hermes Home; do not reconcile tickets in other repositories. The local
`sprint-status.yaml` remains the delivery-status authority.

Home-owned BMAD workflow sources live in
`_bmad/custom/home-issue-tracking/workflows/`. After refreshing the shared
issue-tracking setup files, run `scripts/apply_home_issue_tracking_overrides.sh`
to copy those sources into BMAD's generated workflow directory. Do not edit the
shared skill for Home-specific changes.

Keep changes in a clean, focused branch or worktree, preserve unrelated local
edits, and review staged paths before committing. Never commit credentials,
tokens, `.env` files, or generated local state.

## Worktrees

All linked feature and agent worktrees for this repository belong under
`.worktrees/<name>` inside the repository's main checkout. Keep `.worktrees/`
ignored and do not create sibling `*-worktrees` directories or use a global
tool-specific worktree location. BMAD loop-managed run worktrees under
`.bmad-loop/runs/<run>/worktrees/` are engine-owned and remain there.

## Verification

Use the supported Python 3.14 environment. Before handing off a change, run
the focused tests, `ruff check src tests`, and `ruff format --check src tests`;
record the observed result in the local validation artifact when the story
workflow requires it.
