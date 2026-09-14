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

The external ticket board is paused. Until the maintainer explicitly reopens
it, do not inspect, create, edit, move, delete, or reconcile board tickets.

Keep changes in a clean, focused branch or worktree, preserve unrelated local
edits, and review staged paths before committing. Never commit credentials,
tokens, `.env` files, or generated local state.

## Verification

Use the supported Python 3.14 environment. Before handing off a change, run
the focused tests, `ruff check src tests`, and `ruff format --check src tests`;
record the observed result in the local validation artifact when the story
workflow requires it.
