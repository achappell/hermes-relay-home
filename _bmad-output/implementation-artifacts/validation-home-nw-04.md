---
story: HOME-NW-04
status: verified
validated: 2026-09-16
baseline_commit: 750c0e1b3278bd3a2718d95b43f9f081c2ce79f1
---

# HOME-NW-04 validation record

## Boundary proved

The Home bridge now has a pure approved-route policy seam. It validates
`home`, `tailscale`, and `public` route candidates, snapshots them immutably,
evaluates them in deterministic priority order, requires an injected
Household Identity proof to match the expected opaque identity, and permits
public routes only when explicitly enabled.

Attempts retain only route class, route ID, typed outcome, and stable failure
reason. Selected results contain only the class/ID descriptor. URLs, proof
material, credentials, Profile IDs, and Standard Session IDs do not enter the
selection result. The policy ports create no bridge, Hermes Session, turn, or
retry state; existing `HomeBridge` and endpoint no-replay behavior remain
unchanged.

## Verification boundary

Evidence is from deterministic injected probe and identity ports in the Home
repository. It proves route ordering, public opt-in, identity rejection,
typed failures, timeout boundaries, immutable snapshots, validation, and
redaction. It does not claim production route discovery, a final cryptographic
proof mechanism, endpoint reconnect orchestration, TLS/public deployment, a
live Hermes turn, or physical-device behavior.

## Tested source revision

The HOME-NW-04 implementation was rebased onto `main` commit
`726ba4f9ecb986691c42c84e7ff03bccf77296fa`; the focused and full checks below
were rerun on that rebased branch after the sprint status was finalized. The
story's approved original baseline remains
`750c0e1b3278bd3a2718d95b43f9f081c2ce79f1`. No credentials or generated local
state were added.

## Observed checks

| Check | Exact command | Recorded result |
| --- | --- | --- |
| Focused route/bridge/runtime suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_bridge_routes.py tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_runtime.py` | `94 passed in 1.79s` |
| Full Home test suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` | `318 passed in 3.40s` |
| Ruff lint | `uvx --from ruff ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx --from ruff ruff format --check src tests` | `48 files already formatted` |
| Lockfile check | `uv lock --check` | Passed; `uv` resolved 11 packages with CPython 3.14.7 |
| Diff whitespace check | `git diff --check` | Passed; no output |

## Review result

The BMAD review ran blind, edge-case, and verification-gap lenses. It produced
27 findings; all were classified as patchable test or boundary corrections.
The corrections were applied, and the focused and full suites above were rerun
successfully. No deferred-work entry was required. Production roaming remains
explicitly deferred by the approved policy-only scope.
