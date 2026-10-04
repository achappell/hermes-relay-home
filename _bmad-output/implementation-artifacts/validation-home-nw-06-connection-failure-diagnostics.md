# HOME-NW-06 connection diagnostics — local validation

Date: 2026-10-04. Status: review, not broader story or Epic 6 closure.
Specification: `spec-home-nw-06-connection-failure-diagnostics.md`.
Workspace: `.worktrees/home-connection-diagnostics`; branch `feat/home-nw-06-connection-diagnostics`; baseline `144466aa8a1daaed37eb59f7da8bfabfdd4f3897`. No commit, push, or host deployment was performed.

## Observed delivery gate

Commands below ran from the feature worktree. Ruff is not in the project's dev dependencies, so the executable was supplied by `uvx` rather than claiming that `uv run ruff` succeeded.

| Exact command | Observed result |
| --- | --- |
| `uv run --python 3.14 --extra dev pytest` | Final post-format run: 912 passed, 4 warnings in 23.18s. Prior pre-format full run: 912 passed, 4 warnings in 23.60s. |
| `uvx ruff check src tests` | All checks passed. Initial run found one intake exception-convention warning; preserved the existing ValueError API and documented its intentional use. |
| `uvx ruff format --check src tests` | 74 files already formatted. Initial run identified 14 changed files; only those files were formatted. |

The four warnings are websockets `connect()` deprecations in the real relay tests, not failed assertions. The standalone helper has a per-file Ruff Python 3.9 target, preventing the formatter from introducing Python 3.14-only exception syntax into the separately deployed helper.

Focused pre-gate command: `uv run --python 3.14 --extra dev pytest tests/test_home_operational_diagnostics.py tests/test_bridge_server.py tests/test_bridge_endpoint.py tests/test_large_session_resume_transport.py tests/test_production_bridge.py tests/test_runtime.py` — 181 passed, 4 warnings before the additional truncation regression test. The final full suite includes all eight helper tests.

## Crash-tail regression

`test_restart_preserves_truncated_tail_but_evicts_interior_corruption` writes a complete process log followed by an unterminated JSON fragment and a separate backup with interior corruption. Restart retains the complete prior evidence (including its untouched trailing bytes in the rotated file), evicts the corrupt interior backup, records eviction loss, and writes the new process provenance. Retention ignores only the unterminated final line; malformed complete/interior records still trigger eviction. The full 912-pass run exercises this test.

## Additional fast-worker evidence

The integration owner supplied the following observed fast-worker results; these were not re-executed or relabeled as host acceptance by this worker:

- Standalone helper smoke passed on CPython 3.9.6 and 3.14.8, without the Home application dependency environment.
- Deployment wrapper smoke passed.
- Report/viewer focused run: 139 passed.
- Local HTTP `/pair` browser smoke: signed-out report access returned 401; schema-2 report details displayed; a hostile label rendered as text rather than executing markup.

## Scope and remaining limits

No PowerShell execution was available. No host deployment, production restart, real-Standard acceptance, iOS/device receive/resolve acceptance, or A11 acceptance was performed. Loopback fixtures establish local transport behavior only. Returned writes do not prove peer receipt. External acceptance remains pending.

The supplement is recorded as review in the local tracker, preserving the parent story's existing historical status and Epic 6's in-progress status.

## Local bounds and submission latency acceptance

Exact command: `uv run --python 3.14 --extra dev python /tmp/home_diagnostics_acceptance.py`.
The throwaway harness and its temporary log directory were removed after execution.
Machine: macOS 27.0.1, arm64; CPython 3.14.8 built with Clang 21.0.0.

The harness used the real `OperationalDiagnostics` writer and real Home
`create_bridge_server`/`BridgeEndpoint` over loopback WebSockets. Four concurrent
clients each sent 250 valid explicit `prompt.submit` requests in each mode,
with a deterministic injected upstream returning known rejection. Exactly
1,000 upstream calls occurred per mode; no latch shortcut or replay.
Latency measured client send through decoded matching response using
`perf_counter_ns`; p95 is sorted sample 950 of 1,000 (nearest rank).
Logging-on ran concurrently with the 100,000-record lifecycle producer.
The real sink writer was delayed by 0.1ms per write on its worker, not on
the transport thread. Producer batches drained every 1,000 records to
exercise rotation rather than merely discard the whole load.

| Measurement | Observed |
| --- | --- |
| Logging off p95 / median | 0.881791ms / 0.4616875ms |
| Logging on p95 / median | 1.713958ms / 1.0565ms |
| Added p95 | **0.832167ms**, below the unchanged **5ms** budget |
| Lifecycle attempts / accepted / queue-dropped | 100,000 / 99,997 / 3 |
| All-source queue drops | 4,893; matched independently counted failed emissions, including bridge events |
| Schema rejects / sink failures | 0 / 0 |
| Maximum observed queue depth | 1,024 |
| Retained file byte sizes | 2,011,982; 10,485,747; 10,485,502; 10,485,535; 10,485,233 |
| Retained parseable records / largest record | 80,826 / 545 bytes |
| Completed writer calls | 100,128 |
| Rotation eviction counter before expiry | 1 |
| Eviction counter after advancing clock by 14 days + 1s | 80,822; all owned files removed |

Every retained line ended in newline, parsed as JSON, and passed the actual
strict event validator; every file began with provenance. All five files
were individually at most 10MiB, combined below 50MiB. Only the
`home-diagnostics` worker executed the delayed disk writer. Queue drops were
not counted as durable evidence, and retention/rotation losses remained
visible. These are local fixture measurements, not a production benchmark
or live Standard/proxy acceptance.

An initial harness assertion exposed 1,000 rejected diagnostic schemas:
the endpoint passed `cause_category` to an upstream outcome whose contract
allows only `exception_category`. The endpoint now projects only that
approved field; the passing run above has zero schema rejects. Two earlier
harness setup runs corrected response-key and required-handle assumptions,
not implementation behavior or thresholds.

Post-fix delivery recheck:
`uv run --python 3.14 --extra dev pytest && uvx ruff check src tests && uvx ruff format --check src tests`
— 912 passed, four existing warnings in 22.77s; all lint checks passed;
74 files already formatted.

## Rebase and D3 recheck (2026-10-04)

The branch was rebased with `git rebase --onto origin/main 144466a`, replaying only
the NW-06 commit onto squash-merged NW-18 (`a83d478`); it applied without conflicts
and the PR diff no longer carries NW-18 files. Review item D3 was fixed: opted-in
ready/reconnect results add diagnostics capability keys only to the existing
capabilities mapping, with a new endpoint regression test.

`uv run --python 3.14 --extra dev pytest` — 913 passed, four existing warnings in
22.84s. `uvx ruff check src tests` — all checks passed.
`uvx ruff format --check src tests` — 74 files already formatted.
