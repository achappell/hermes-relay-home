---
story: HOME-NW-02
status: passed
validated: 2026-09-14
baseline_commit: 0519bb7f30aba5f5042b0459b285a4444c7c0f8e
---

# HOME-NW-02 validation

The Home pairing and credential lifecycle was exercised on Python 3.14 with
the repository's complete test suite and the focused runtime/API/domain set.

## Checks

- `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography -- python -m pytest -q tests/test_credentials.py tests/test_credentials_api.py tests/test_runtime.py` — 44 passed.
- `uv run --no-cache --no-project --python 3.14 --with pytest --with cryptography -- python -m pytest -q` — 169 passed.
- `uv run --no-cache --no-project --python 3.14 --with ruff --with cryptography -- ruff check src tests` — all checks passed.
- `uv run --no-cache --no-project --python 3.14 --with ruff --with cryptography -- ruff format --check src tests` — 34 files already formatted.
- `uv lock --check` — resolved 10 packages.
- `git diff --check` — passed.

## Covered behaviors

- Single-use five-minute offers, pending-before-approval, bounded approval,
  confirmation display data, secure-storage gating, and redacted listings.
- Durable keyed credential digests, AES-GCM replacement retry material,
  restart recovery, ninety-day expiry, final-fourteen-day renewal, bounded
  overlap, metadata tamper failure, re-enrollment, rotation, and revocation.
- Paired/legacy/disabled runtime modes, no static fallback, admin route
  authentication, request-size rejection, scope enforcement inside the
  arbitration snapshot, revocation during arbitration, and stable errors.

## Review outcome

All three independent review layers ran against the complete baseline diff.
Their patch findings were applied and rechecked. Bridge-adapter coverage,
enrollment cancellation, configuration-device binding, and rotation request-ID
entropy remain explicitly deferred to the owning future slices; see the story
spec's Review Triage Log and `deferred-work.md`.
