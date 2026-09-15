# HOME-NW-03 Validation

Validated 2026-09-14 on Python 3.14 in the isolated
`feat/home-nw-03-approved-local-bridge-route` worktree.

## Observed checks

| Check | Command | Result |
| --- | --- | --- |
| Focused adapter/runtime suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q tests/test_bridge_endpoint.py tests/test_bridge_server.py tests/test_runtime.py` | `41 passed in 1.55s` |
| Full Home suite | `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography --with 'websockets>=17,<18' -- python -m pytest -q` | `203 passed in 3.47s` |
| Ruff lint | `uvx --from ruff ruff check src tests` | `All checks passed!` |
| Ruff format | `uvx --from ruff ruff format --check src tests` | `38 files already formatted` |
| Package build | `uv run --no-cache --no-project --python 3.14 --with build -- python -m build --sdist --wheel` | Successfully built the sdist and wheel |

The live local WebSocket smoke is included in the full suite as
`test_live_bridge_server_accepts_only_the_versioned_route_and_closes_bridges`.
It binds a real loopback listener, performs a real WebSocket upgrade and
schema-1 open, verifies the configured route and Device-header pass-through,
and confirms bridge cleanup. Its bridge is an injected deterministic fixture;
this is live listener evidence, not a live Hermes gateway or physical-device
claim.

The first no-project test attempt omitted the newly selected `websockets`
dependency and failed collection with `ModuleNotFoundError`. The corrected
command above supplies `websockets>=17,<18`, and the README and companion
transport contract now document that exact environment.

## Boundary evidence

- The sibling listener accepts only `/api/v1/bridge/ws`, requires exactly one
  non-empty `Authorization: Device ...` header, and enforces a one-MiB bound.
- Endpoint frames retain only opaque Home conversation/turn handles and safe
  route/status/event/audio fields. Hermes bearer, Profile, runtime Session, and
  credential-shaped values are not emitted.
- Prompt, control, event, reconnect, and audio failure paths preserve typed
  known/uncertain outcomes and do not retry uncertain input.
- Route-roaming, Household Identity proof, browser tickets, live Hermes
  integration, and physical hardware remain outside this story's evidence.
