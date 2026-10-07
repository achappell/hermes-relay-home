# Qwen3 streaming server logging

Persistent, rotating, content-free logging for the Qwen3 speech server on
CaticornQueen, added 2026-10-06 and versioned here because the live files are
untracked local edits. Before this change a restart truncated the redirected
stdout/stderr logs and nothing survived a stall.

## Provenance

`D:\Qwen3-TTS` **is** a git checkout, but of upstream
[`QwenLM/Qwen3-TTS`](https://github.com/QwenLM/Qwen3-TTS) (HEAD `022e286`,
clean tracked tree). The server files are not part of it: `git ls-files` lists
only upstream's `qwen_tts/`, `examples/`, `finetuning/` etc., and
`qwen3_streaming_server.py`, `start_qwen3_streaming_optimized.ps1` and the new
`qwen3_server_logging.py` are all *untracked* local files. They are Amanda's own
service code with no other repository, so the full modified copies are shipped
here, along with a patch against the pre-change originals. Nothing from
upstream's tree is copied.

## Files

| File | Purpose |
| --- | --- |
| `qwen3_server_logging.py` | New module. Installs the rotating log file, content-free tracebacks and the health monitor. |
| `qwen3_streaming_server.py` | Full modified server: two guarded hooks in `main()`. |
| `start_qwen3_streaming_optimized.ps1` | Full modified start script: archives the previous stdout/stderr logs instead of truncating them. |
| `qwen3-streaming-logging.patch` | `git apply` / `patch -p1` diff of the last two against the originals. |
| `install.ps1` | Backup + install; opt-in idle-guarded restart. |
| `rollback.ps1` | The host's saved rollback, verbatim. |
| `.gitattributes` | Keeps these files byte-exact on Windows checkouts (no line-ending conversion). |

SHA-256, recorded 2026-10-06 (compared against the live files over SSH with
`Get-FileHash`; the checked-in files byte-match):

```text
f7b5aea7b955ff9a59f92ebf2b7f7d9a6fcab8b72c859bb1c0ef318f64b9dae9  qwen3_server_logging.py                (live, new)
a57265073311bce48f9863f3736b451934aab3f5cb4026e65d656338a66cb821  qwen3_streaming_server.py             (live, modified)
74042877aed2f65c0309691640439cb86902ae1a535f99e21cc2e0f53519bbc3  start_qwen3_streaming_optimized.ps1   (live, modified)
30676cf861af34700024cc815df3cd885f720882332f098ff7315dc33a2008f6  qwen3_streaming_server.py             (original, backups\qwen3-logging-20261006)
e4fe09a3a6cfc5dbdcf3272cf6a098f91fb8c1fa59511e3af742e8df905657aa  start_qwen3_streaming_optimized.ps1   (original, backups\qwen3-logging-20261006)
98aaf52e60a8204d749666e89c958b458dcccdda7b89351fcbf98b2d98086182  rollback.ps1                          (live backups\qwen3-logging-20261006)
```

The patch round-trips: applying it to the originals yields the modified hashes
and reverse-applying it to the modified files yields the original hashes.

## What changed

`qwen3_streaming_server.py` `main()`, both calls wrapped so a logging failure
cannot stop speech:

```python
    logging.basicConfig(...)
    try:
        import qwen3_server_logging
        qwen3_server_logging.install_logging()
    except Exception:
        LOGGER.exception("could not enable rotating file logging; continuing on stderr only")
    ...
    try:
        qwen3_server_logging.start_health_monitor(service, args)
    except Exception:
        LOGGER.exception("could not start health logging; continuing without it")
```

`start_qwen3_streaming_optimized.ps1`: before `Start-Process`, moves a non-empty
`qwen3-streaming-optimized.{stdout,stderr}.log` into `logs\archive\<name>.<yyyyMMdd-HHmmss>.log`
and keeps the newest 5 of each. A failure only warns.

## The log

- File: `D:\Qwen3-TTS\logs\qwen3-streaming.log` (directory override: `QWEN3_LOG_DIR`).
  It survives restarts.
- Rotation: `RotatingFileHandler`, **10 MB x 5** (`.log`, `.log.1` ... `.log.5`, 60 MB
  at most).
- Everything the server already logged (INFO and above) is now also in the file.

### Fields (content-free)

No request text, prompt or audio is ever written. Per-request lines carry
lengths and ids only (`text_chars`, byte counts, seconds, voice names, client
address). Tracebacks keep frames and the exception type but replace the message:
`module.Type: <message omitted>`, because a provider error can echo the
synthesized text. Successful `GET /healthz` access lines (polled every ~10 s)
are dropped and counted into `healthz_ok_total`; non-200 healthz responses are
still logged.

| Event | Fields |
| --- | --- |
| `event=process_start` | `pid python log_file rotation` |
| `event=server_ready` | `pid torch cuda model default_voice device dtype compile_mode optimizations port` |
| `event=health` | `pid model uptime_s busy active_request active_age_s since_chunk_s since_write_s requests_total healthz_ok_total proc_cpu_pct sys_cpu_pct rss_mb gpu_util_pct gpu_mem_mb gpu_temp_c gpu_power_w` (or `gpu=unavailable(<ExceptionType>)`) |
| `event=server_exit` | `pid` (atexit only; absent after a hard kill or crash) |
| `timing request=<id> event=...` | emitted by the server itself: `request_start`, `first_audio`, `synthesis_complete`/`synthesis_aborted`, `alignment_complete`, `response_complete` |

`event=health` is written every 60 s while idle and every 10 s while a request
holds the generation lock. `active_age_s`, `since_chunk_s` and `since_write_s`
appear only while a request is active.

Known limit: the server's own `response_complete status=bad_request` line
includes `error=<message>` for `ValueError`/`TypeError`/`JSONDecodeError`. That
line predates this change and the logging module does not rewrite it; those
messages describe the request shape, not the synthesized text, but this is not
covered by the traceback redaction.

### Stall-triage field guide

1. **Find the stuck request.** A `request_start` with the same `request=<id>` but no
   `response_complete` is in flight, or the process died mid-request: look for a later
   `event=process_start` with a different `pid` (and whether `event=server_exit`
   appeared). `event=health` carries the same id as `active_request` and
   `active_age_s` says how long it has been running.
2. **Is audio being produced?** While `busy=1`:
   - `since_chunk_s=none`: nothing generated yet for this request (stuck before
     first audio: compile/warmup, GPU, lock).
   - `since_chunk_s` small and moving: generation is healthy.
   - `since_chunk_s` large: generation stalled.
3. **Generation or delivery?** Compare `since_write_s` with `since_chunk_s`
   (`since_write_s` is the time since the last HTTP chunk finished writing; it is
   absent until the first write finishes).
   - `since_write_s` **greater** than `since_chunk_s`: a chunk was generated but its
     write has not finished. The client or socket is blocked (slow reader, dropped
     network), not the model.
   - `since_write_s` <= `since_chunk_s`, both large and growing: the model stopped
     producing; the writes were keeping up.
4. **Is the GPU doing anything?** On a generation stall, `gpu_util_pct` near 0 with
   large `active_age_s` points at a hang (lock, CUDA graph, stuck thread); high
   utilization with slow chunks points at a slow or contended GPU. Also check
   `gpu_temp_c`, `gpu_power_w` and `gpu_mem_mb` for throttling and memory pressure.
5. **Process health.** `proc_cpu_pct`, `sys_cpu_pct`, `rss_mb` and `uptime_s` (a reset
   means a restart) round out the picture. `healthz_ok_total` stops growing if the
   HTTP loop itself is wedged.

A healthy idle line, for shape:

```text
... event=health pid=36648 model=Qwen/Qwen3-TTS-12Hz-1.7B-Base uptime_s=8684 busy=0 active_request=none requests_total=0 healthz_ok_total=997 proc_cpu_pct=0 sys_cpu_pct=16 rss_mb=66 gpu_util_pct=0 gpu_mem_mb=11371/16376 gpu_temp_c=48 gpu_power_w=50.45
```

## Install

The 2026-10-06 edit is already live; `install.ps1` reports `already installed`.
To reinstall or deploy elsewhere, copy this directory to the host and, from an
elevated PowerShell on CaticornQueen:

```powershell
scp -r deploy/ops/qwen3-streaming-logging caticornqueen:C:/Users/achap/qwen3-logging-install   # from the Mac
cd C:\Users\achap\qwen3-logging-install
.\install.ps1                  # backup + copy; does not restart
.\install.ps1 -Restart         # also restart, only when idle
```

`install.ps1`:

1. rejects shipped files whose SHA-256 differs from the table above (for example a
   Windows checkout that converted line endings);
2. requires each target file to be the known original or already the shipped
   version; anything else is local drift and aborts unless `-Force`;
3. copies the current target files to
   `D:\Qwen3-TTS\backups\qwen3-logging-<yyyyMMdd-HHmmss>\` and writes a
   `rollback.ps1` there;
4. copies the three files.

### Restart

The running server keeps the old code until it is restarted. Restart **only when
idle** (newest `event=health` shows `busy=0`; `-Restart` refuses on `busy=1`
unless `-Force`). Only the `Qwen3-TTS-Streaming-Optimized` scheduled task and its
`qwen3_streaming_server.py` python process are touched:

```powershell
Stop-ScheduledTask -TaskName "Qwen3-TTS-Streaming-Optimized"; Start-Sleep 3
Get-CimInstance Win32_Process | ? { $_.CommandLine -match "qwen3_streaming_server\.py" } | % { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 4; Start-ScheduledTask -TaskName "Qwen3-TTS-Streaming-Optimized"
```

After the restart the model reloads and warms up (several minutes). Confirm
`event=process_start` then `event=server_ready` in the log.

## Rollback

On the host, `rollback.ps1` (shipped verbatim) restores the pre-change originals
from `D:\Qwen3-TTS\backups\qwen3-logging-20261006\`, removes
`qwen3_server_logging.py`, and restarts only the streaming task, so run it when
idle:

```powershell
D:\Qwen3-TTS\backups\qwen3-logging-20261006\rollback.ps1
```

A later `install.ps1` run writes its own `rollback.ps1` into its backup directory.
Rolling back leaves `logs\` and `logs\archive\` in place; delete them by hand if
unwanted.

## Verifying a deployment

```powershell
Get-FileHash D:\Qwen3-TTS\qwen3_server_logging.py, D:\Qwen3-TTS\qwen3_streaming_server.py, D:\Qwen3-TTS\start_qwen3_streaming_optimized.ps1 -Algorithm SHA256
Get-Content D:\Qwen3-TTS\logs\qwen3-streaming.log -Tail 5
```

compare the hashes with the table above.
