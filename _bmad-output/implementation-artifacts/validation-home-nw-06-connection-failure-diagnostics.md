# HOME-NW-06 connection diagnostics — local validation

Date: 2026-10-04. Status: review, not broader story or Epic 6 closure.
Specification: `spec-home-nw-06-connection-failure-diagnostics.md`.
Workspace: `.worktrees/home-connection-diagnostics`; branch `feat/home-nw-06-connection-diagnostics`; baseline `144466aa8a1daaed37eb59f7da8bfabfdd4f3897`. At the time this local validation was recorded, no host deployment had been performed; the later deployment is documented in the supplement below.

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

At the time of this earlier validation, PowerShell execution was unavailable.
No host deployment, production restart, real-Standard acceptance, iOS/device
receive/resolve acceptance, or A11 acceptance was performed. Loopback fixtures
establish local transport behavior only. Returned writes do not prove peer
receipt. External acceptance remains pending. The remote parser-only check
below validates Windows script syntax only; it does not establish deployment or
device acceptance.

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

## Windows deployment diagnostics directory correction (2026-10-04)

Source correction in `deploy/windows/install.ps1` creates
`<InstallRoot>\diagnostics`, applies its protected DACL (SYSTEM Modify and
BUILTIN\Administrators Read), and persists that exact path as the machine
`HERMES_HOME_DIAGNOSTICS_DIR`. `deploy/windows/run.ps1` applies the same path
as a per-process override. The existing `logs` directory is kept separate and
is not the diagnostics sink or the target of the restrictive ACL. The README
example reads `C:\ProgramData\HermesHome\diagnostics\home.jsonl`.

PowerShell 5.1 parser-only validation ran remotely on CaticornQueen against the
exact branch files `deploy/windows/run.ps1` and `deploy/windows/install.ps1`.
Both full source files were UTF-8/base64-encoded locally and decoded in-memory
by Windows PowerShell before calling
`[System.Management.Automation.Language.Parser]::ParseInput(...)`. Neither
script was invoked; no host files were written and no host state was changed.

Exact remote command (PowerShell script was streamed over stdin; each full file
was represented by local base64 literal(s), with long literals split into
short assignments):

```sh
ssh -T -i ~/.ssh/id_ed25519_caticornqueen \
  -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes \
  -o ConnectTimeout=10 caticornqueen \
  'powershell.exe -NoProfile -NonInteractive -Command -'
```

The stdin script decoded each base64 source via
`[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($encoded))`
and parsed it using
`[System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)`.
Observed output:

```text
run.ps1 error count=0 PowerShell=5.1.26100.9444
install.ps1 error count=0 PowerShell=5.1.26100.9444
```

This is syntax validation only; installer/runner execution and ACL behavior
were not tested. No production host was changed or restarted.


Local source gates previously recorded for the diagnostics feature were:

- `uv run --python 3.14 --extra dev pytest` — 913 passed, 4 existing
  `websockets.connect()` deprecation warnings in 24.39s.
- `uvx ruff check src tests` — all checks passed.
- `uvx ruff format --check src tests` — 74 files already formatted.

Those source gates predate this documentation-only correction. Remote parser
validation above supersedes the prior note that PowerShell was unavailable.
No production deployment or restart, installer/runner execution, ACL
acceptance, real-device acceptance, or live Standard/proxy acceptance was
performed.

## Mid-request disconnect and duplicate-token gap tests (2026-10-04)

- `uv run --python 3.14 --extra dev pytest` — **915 passed, 4 warnings** in 23.27s.
- `uvx ruff check src tests` — all checks passed.
- `uvx ruff format --check src tests` — 74 files already formatted.
- Added `test_client_close_during_submit_records_failed_write_and_finalizes_association` and
  `test_duplicate_diagnostic_request_token_is_ambiguous_without_suppressing_prompts`.
- Fixed a diagnostics gap exposed by the first test: a failed response send detached the
  server-managed endpoint before `run()` could observe the close. The endpoint now records
  `request_transport_lost` and `transport_observed` at the failed-write boundary.
- The disconnect test verifies one Home connection ID across request/upstream/close evidence,
  pending state, no claimed successful response write, redaction, and linked association
  finalization. The duplicate-token test verifies both upstream submissions, omitted
  diagnostics on the duplicate response, ambiguous association, and the
  `correlation_conflicts` counter.
- Known limit: real Standard/phone network drop during a pending request is not exercised on device (owner accepted).

## CaticornQueen deployment supplement — 2026-10-04

The merged revision `a45f7efe318e91bde7edf4c23c62973d7fb86e96` was deployed
to CaticornQueen from an isolated `.worktrees/` checkout. This supplements the
local validation above; it does not complete real-device acceptance.

- Wheel: `hermes_relay_home-0.1.0-py3-none-any.whl`, SHA-256
  `dc28b3c1e71a763fa9eb6ae1ffb698c2a2c217e6bbf592cd25d0c5f11e0af016`.
  The 39-member archive contains 33 `hermes_home` Python files and top-level
  `hermes_home_diagnostics.py`.
- Host: CPython 3.14.7. `Hermes Home` task Running as SYSTEM
  (last result `267009`, current-running code); loopback listeners on
  `127.0.0.1:8780` and `127.0.0.1:8766` belonged to one Home process.
  `/pair`, authenticated `/api/v1/diagnostics/status`, and authenticated
  `/api/v1/configuration` returned 200. Prometheus
  `up{job="hermes-home"}` returned `1`.
- Dedicated operational JSONL directory:
  `C:\ProgramData\HermesHome\diagnostics`. Its protected ACL has exactly two
  entries: SYSTEM Modify and BUILTIN\Administrators Read; no Users or
  deployment-account write access. The old user-writable legacy log directory
  was preserved as `C:\ProgramData\HermesHome\diagnostics-legacy`.
- Benign `/pair` requests and the queued writer produced two parseable
  `home.jsonl` JSON records: `provenance_header` and `process_started`, both
  `component=home`, `phase=startup`. Zero invalid records; scans found no
  authorization or credential markers, prompt, transcript, or other inspected
  secret/content indicators. No synthetic client report or incident was
  submitted.
- The final installed runner SHA-256 is
  `52b9316aac157fcc3095373cd8231083841b1c864c0eb7b6e88135484985cc55`; it
  directs `HERMES_HOME_DIAGNOSTICS_DIR` to the dedicated directory. Task
  action, SYSTEM principal, working directory, and task settings were
  preserved. No Prometheus or Standard setting was changed.
- **Runner provenance:** the deployed `run.ps1` intentionally differs from the
  current merged main runner (`logs` → `diagnostics`, with the comment updated).
  This deployment-specific adjustment was necessary because the old `logs`
  ACL granted BUILTIN\Users Write. It is not represented in the merged source
  and remains pending a source PR; do not treat repo main's runner as identical
  to production.
- Rollback assets are in
  `C:\ProgramData\HermesHome\backups\home-nw06-a45f7ef-20261004`. The prior
  package archive SHA-256 is
  `2493dabeee2464dd978d0d2abfc64190395a213dc18a8004552006525726ebd7`;
  its 43-file manifest was checked against archive bytes. A task-specific
  rollback script was parsed with Windows PowerShell 5.1; it restores the
  verified old package, runner, and task XML without reverting live SQLite or
  machine settings. No rollback was needed.
- No proxy process was running. The disabled
  `\Microsoft\Windows\Autochk\Proxy` task is an unrelated Windows Autochk task;
  it was not changed and no pilot proxy was started.

Real phone schema-2 upload and end-to-end phone/Home correlation remain
unverified and pending. This deployment supplement is operational evidence,
not those missing acceptance observations.

## Read-only deployed acceptance supplement — 2026-10-08 UTC

Disposition: **review, not done**. This is bounded deployed verification, not
a deployment, configuration repair, stress run, client-control action or
broader HOME-NW-06/Epic 6 closure. No connection kill, revocation, unpair,
consent change or CI watch was performed.

### Installed source, operational journal and protected access — pass

- All 34 installed Python sources matched
  `d803994d1d47c63bb1b3c92cff42695de19a4434`, with zero mismatches.
  Recomputing the source aggregate by the installed provenance algorithm
  matched the running journal's startup artifact SHA-256
  `a367de2ec24a3f0ce0cd9a5d1824a49897fb36750d1414c9aa6929ebf6d5b93b`.
  The `Hermes Home` scheduled task was running. The separately stored
  deployment revision remained stale `376583d7273e08a090d7ec33c416e0b32880a343`;
  it was not substituted for loaded-source proof. The journal's own
  provenance label remained `unverified`, not silently upgraded.
- Five retained operational JSONL files contained 1,551 strictly valid
  records, zero invalid records and 1,122,092 bytes at the bounded snapshot.
  Every file started with provenance and ended in newline; the largest
  record was 999 bytes. These are observed bounds, not a fresh load,
  retention-expiry or crash test.
- Latest operational loss snapshot: queue drops 0, schema rejects 0,
  sink failures 0, rotation evictions 1, correlation conflicts 0.
  This operational JSONL sink is distinct from the SQLite safe-event
  export queue below.
- The diagnostics directory DACL was protected, with only SYSTEM
  Modify/Synchronize and BUILTIN Administrators Read/Synchronize, both
  non-inherited. Credentials, report content and private identifiers were
  not printed or copied.
- Existing-admin authenticated `/api/v1/diagnostics/status` and `/metrics`
  returned 200. The client-report intake/review access checks and retained
  receipt counts are recorded in
  [the client-report supplement](validation-home-client-diagnostics.md#read-only-deployed-acceptance-supplement--2026-10-08-utc).

### Safe-event export — not wired, with observed durable evidence loss

The precise finding is **exporter not wired: no `EventCollector` injection
or flush wiring**. It is not a demonstrated collector network/authentication
outage. The initial authenticated status snapshot showed:

| Field | Observed value |
| --- | --- |
| `enabled` | `true` |
| `collector_reachable` | `false` |
| `last_successful_upload_at` | `null` |
| `queued_event_count` | `4096` |
| `dropped_event_count` | `2060` |
| `rejected_event_count` | `0` |

Bounded root-cause proof, using the installed sources rather than assuming
the operator checkout matched production:

1. Installed `runtime.py:358` constructs
   `DiagnosticsRecorder(store=diagnostics_store, metrics=metrics)` without
   `collector`; the installed constructor's default is `None`.
   The fallback construction in `api/application.py:133` also omits it.
   An AST inspection of the 34 installed Home Python files found no
   `.flush()` call.
2. `DiagnosticsRecorder.flush` in
   `src/hermes_home/observability/diagnostics.py` uses the injected
   `EventCollector`; its missing-collector branch returns zero uploaded
   and false reachability. Runtime supplies no concrete collector or
   automatic drain lifecycle. No collector destination/transport is
   configured by this runtime contract, so there was no collector URL to
   health-check safely.
3. `SQLiteDiagnosticsStore` initializes reachability false and changes it
   after upload outcomes. Status reads that stored state; it is **not**
   a live network probe. Its bounded append path evicts old rows and
   increments the persistent dropped-event count.
4. A later read-only SQLite snapshot found all 4,096 retained diagnostic
   events with `uploaded=0`, persistent drops **2,062**, zero rejects,
   null last-success timestamp and reachability 0. The increase from
   2,060 is observed activity between snapshots, not a stress test.
   Authenticated metrics exposed queue depth 4,096 and reachability 0;
   no upload-attempt metric series was present. There is no observed
   exporter transport error to attribute to network, TLS or credentials.

Operational export therefore cannot pass as a deployed capability in this
review. Local recording, bounded loss reporting, the operational JSONL sink
and automatic **client-report intake** must not be described as that export.
Client intake separately retained 53 valid reports, including schema-2
client receive/resolve assertions and exact scoped associations; it was not
disabled by this missing exporter.

### Contract-scope question and precise implementation prerequisite

The [canonical parent SPEC](../specs/spec-household-diagnostics-incident-review/SPEC.md#capabilities)
CAP-3 requires honest enabled/last-upload/queue/reachability status, and
CAP-6 requires bounded retention and isolation from live turns. Its
**Non-goals**, **Assumptions** and **Open Questions** deliberately leave the
concrete log backend and transport protection undecided.
The [historical implementation contract](spec-home-nw-06-diagnostics-incident-review.md#boundaries--constraints)
explicitly selects an **injected collector**; its **UPLOAD** acceptance row
requires delivery when one is supplied and honest queue/reachability when
one is missing or failing. The two child specs do not select that collector:
the client-report AC defines Device-authenticated intake, and connection
diagnostics AC 4 expressly keeps broader Home/iOS/Standard/A11 acceptance
pending. This observation does not silently reopen the historically
completed injected-port slice.

**Owner scope decision required:** Is a real deployed safe-event exporter a
current NW-06 acceptance requirement to address within existing scope, or
should its concrete integration be a separately authorized follow-up while
retaining the historical injected-port boundary? Either choice must retain
this installed-runtime limitation; neither is authorization from this
evidence-only PR to implement or deploy transport.

If export is authorized, first approve the missing collector contract:
receiver/backend ownership and destination, transport and encoding,
authentication/authorization and TLS trust, failure/timeout/size semantics,
and remote retention/review/deletion responsibilities. Reuse the existing
in-process `EventCollector.upload(events, idempotency_key)` and exact
`UploadAcknowledgement(idempotency_key, event_ids)` rules; they are not an
already defined network protocol. The proposed implementation is then a
concrete collector adapter plus runtime injection and bounded, independently
scheduled flush/shutdown lifecycle, preserving the existing queue limits,
exact acknowledgements, privacy and no live-turn retry/replay. No such code
or configuration change was made here.

### Remaining integrated acceptance — unverified

Current evidence does not establish the intended real client's consent and
build attribution, controlled failure/restart and delayed schema-2 upload,
or independently observed receive/resolve. Android DIAG-01 manual sharing is
not automatic-upload proof. Historical stored assertions do not substitute
for that controlled journey.

The canonical [connection acceptance matrix](../specs/spec-connection-failure-diagnostics/implementation-acceptance.md#acceptance-matrix)
still requires the separately authorized real-Standard gate in A2, intended
iOS-device receive/resolve and close-order proof in A3, live proxy-leg
attribution in A4, client/restart/consent attribution for integrated A10,
and the final ID-joined Home/proxy/iOS timeline in A11. Earlier local fixture
results retain their recorded scope; none was relabeled as live acceptance.
Both child tracker entries remain `review`; no status was promoted.
