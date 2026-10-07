# A1 — Zero-write SQLite measurement (06 §A1, D-4, P-29)

Date: 2026-10-04. Author: implementing agent (Claude). Fixtures only; nothing under `~/.kriya`
or `~/kriya-cagc-v2` was touched; no `kriya` command was run (D-9).
Raw data: `results/results.json`, generated table: `results/results.md`. Reproduce with:

```
PYTHONDONTWRITEBYTECODE=1 .newvenv/bin/python -B ui/spikes/a1_zero_write/harness.py \
  --python "$PWD/.newvenv/bin/python" --work-dir <scratch>/a1work/runN --keep
```

## Environment (MEASURED)

| item | value |
|---|---|
| macOS | 26.7 (build 25G229), kernel 25.6.0, arm64 |
| reader interpreter | `.newvenv/bin/python`, Python 3.14.6, `sqlite3.sqlite_version` 3.53.4 |
| `kriya` console script interpreter | `#!…/.newvenv/bin/python3.14` (same interpreter → same SQLite 3.53.4; no mismatch finding) |
| `sandbox-exec` | present, man page says DEPRECATED; still enforces `(deny file-write*)` (EPERM on the probe write, denial lines in the unified log) |
| unified log | `/usr/bin/log show --predicate 'eventMessage CONTAINS "Sandbox:" AND eventMessage CONTAINS "deny"'` is readable WITHOUT sudo and reports `Sandbox: <proc>(<pid>) deny(1) file-write-<kind> <path>` per attempt |
| `fs_usage` / `opensnoop` | present, need root; NOT run (owner adjustment 3). Command for the owner is at the end. |

## Method

- Read candidate (`reader.py`, never imports `kriya`): `sqlite3.connect("file:<abs>?mode=ro", uri=True, timeout=2.0)`,
  then `PRAGMA temp_store=MEMORY` (connection-local; keeps SQLite scratch storage off the filesystem),
  then the Phase C query shapes (P-25): `PRAGMA journal_mode` (read), `sqlite_master` table list, `COUNT(*)`,
  history.list page 1 (`ORDER BY timestamp DESC, run_id DESC LIMIT 50`), page 2 by `(timestamp, run_id)` cursor,
  history.detail by `run_id` (all columns except `prompt_rendered`), history.prompt by `run_id`, close.
  No journal_mode/WAL pragma, no `immutable=1`, no `db.wal_connect`.
- Environment of the reader: `PYTHONDONTWRITEBYTECODE=1`, `-B`, `HOME` = a harness-owned fake home,
  `TMPDIR` = a harness-owned directory (so any SQLite scratch write would show up as a classified `RUN_TMPDIR` path).
- Each case runs twice on a fresh fixture: **sandboxed** (`/usr/bin/sandbox-exec -f` `(version 1)(allow default)(deny file-write*)`)
  and **unsandboxed** (shows what SQLite WOULD write when allowed, via a before/after inventory: size, mtime_ns, sha256 per file).
- Write detectors: (1) the sandbox denial itself (the syscall fails), (2) the unified-log denial lines attributed to the
  reader PID (`sandbox-exec` execs the reader, so the PID is the same; cross-checked against the PID the reader prints),
  (3) the inventory diff. Every denial is classified by path: `STORE` (store or -wal/-shm/-journal), `CASE_DIR`,
  `RUN_TMPDIR`, `DEVICE` (`/dev/*`), `UNRELATED` (= harness bug, exit 2). Nothing is silently ignored.
- The fixture writer processes (`fixtures.py create` / `active-writer`) run OUTSIDE the sandbox.
- Fixture schema = the baseline `runs`/`milestone_plans` DDL (TRACED `kriya/core/trace.py:32-99`), 120 synthetic rows
  (~3 MB), JSON payloads with escaping and non-ASCII, equal timestamps across rows.

## Results (MEASURED, run of 2026-10-04 09:14:25; two full runs, identical outcomes except the C02 fixture fix noted below)

| case | fixture state | sandboxed read | store write attempted (sandboxed, by path) | unsandboxed read / inventory change | **D-4 verdict** |
|---|---|---|---|---|---|
| C01 | rollback journal, clean | READ_OK, all 10 steps | none | READ_OK / none | **READ_OK_ZERO_WRITE** |
| C02 | rollback journal, HOT `-journal` (writer crashed mid-transaction after a cache spill) | `SQLITE_READONLY_ROLLBACK` on first query ("attempt to write a readonly database"); connect succeeds | none (SQLite refuses before writing) | same error / none | **READ_ONLY_UNAVAILABLE** (typed, from SQLite itself) |
| C03 | WAL, clean close, no `-wal`/`-shm` | `SQLITE_CANTOPEN` on first query | `file-write-create traces.db-wal` | READ_OK / **`-wal` and `-shm` CREATED** | **READ_ONLY_UNAVAILABLE** |
| C04 | WAL, orphaned `-wal`+`-shm` (writer exited without checkpoint) | READ_OK, all steps | `file-write-data traces.db-wal`, `file-write-data traces.db-shm` (open-for-write attempts, denied; SQLite then fell back to a read-only heap wal-index) | READ_OK / **`-shm` MODIFIED** (sha256 changed) | **READ_ONLY_UNAVAILABLE** (a write is attempted; it is only prevented by the sandbox) |
| C05 | WAL, active fixture writer committing every 20 ms | READ_OK, all steps (no lock error, no wait) | same two `file-write-data` attempts as C04 | READ_OK / inventory changes are the WRITER's (not attributable to the reader - this is why the PID-attributed log is the primary detector) | **READ_ONLY_UNAVAILABLE** (same reason as C04) |
| C06 | rollback journal in a 0555 directory | READ_OK | none | READ_OK / none | **READ_OK_ZERO_WRITE** |
| C07 | clean WAL in a 0555 directory | `SQLITE_CANTOPEN` | `file-write-create traces.db-wal` (twice) | `SQLITE_READONLY_DIRECTORY` / none | **READ_ONLY_UNAVAILABLE** |
| C08 | rollback store deleted after the first list row was fetched | READ_OK, all steps (pages 1-2, detail, prompt served from the still-open fd) | none | READ_OK / file gone | **READ_OK_ZERO_WRITE** (data is of the deleted file; the adapter must label it by observation time, P-24) |
| C09 | store missing | `SQLITE_CANTOPEN` at connect | none (mode=ro never creates) | same / no file created | **READ_ONLY_UNAVAILABLE** (typed "no store", P-29) |

Noise, classified and reported, never counted: `file-write-data /dev/dtracehelper` at process start (dyld's dtrace
helper registration) appears for most sandboxed processes - class `DEVICE`. Zero `UNRELATED` and zero `RUN_TMPDIR`
denials in both runs, so `PYTHONDONTWRITEBYTECODE`, `-B` and `temp_store=MEMORY` left no harness false positive to fix.
Reader wall time per case: 80-123 ms (C08: 1.6 s, by design, the deliberate pause).

Fixture fix between run 1 and run 2 (reported, not hidden): run 1's "hot journal" left a journal whose header was
still zeroed (no cache spill before the crash); SQLite treats that as not hot and read the store fine with no write.
The fixture now spills the page cache (`PRAGMA cache_size=10`, 300 uncommitted rows) so the journal header is real
(`d9d5 05f9 20a1 63d7`, nRec=3, verified with `xxd`) and the main file holds partially written pages.

## Findings

1. **MEASURED:** a WAL-mode database with no sidecars (C03) cannot be opened at all with `mode=ro` under write denial
   (`SQLITE_CANTOPEN`, after an attempt to CREATE `-wal`); when writes are allowed SQLite CREATES both `-wal` and
   `-shm` even for a `mode=ro` connection. A WAL store with sidecars present (C04, C05) is readable, but a plain
   `mode=ro` open WRITES to `-shm` (and tries to open `-wal` for writing); only an external write-denying boundary
   turns that into a zero-write read (SQLite's read-only wal-index fallback).
2. **TRACED:** Kriya opens every store through `kriya/core/db.py:9-20` (`wal_connect`, `PRAGMA journal_mode=WAL`,
   monkey-patched onto `sqlite3.connect`), so a real `traces.db` is a WAL-mode file. After the last Kriya connection
   closes, SQLite checkpoints and removes `-wal`/`-shm` (standard SQLite behaviour; the fixture C03 was produced the
   same way), i.e. C03 is the expected resting state of a real history store. **INFERRED** (check: `ls -la` of the real
   state directory, only after D-9 is lifted): the owner's real `traces.db` currently sits in state C03 or C04.
3. **Consequence under D-4 as written (no `immutable=1`, no `-shm` exception, typed refusal):** with an in-process
   `sqlite3` read-only connection the adapter would return `READ_ONLY_UNAVAILABLE` for EVERY WAL state (C03, C04,
   C05, C07) and read only rollback-journal stores (C01, C06), which Kriya never produces. D-4 anticipated "even if
   that happens often"; this measurement says it would happen for essentially every real store. The rule is applied
   as written here (verdict column) and nothing is loosened. Options exist but each is a design change for the owner,
   not for this phase: (a) run the adapter's read inside an OS write-denying boundary (`sandbox-exec` on macOS,
   deprecated; no portable equivalent) - C04/C05 then read with zero writes, C03 still fails; (b) revisit D-4 in a new
   topic (C03 needs sidecar creation or `immutable=1` to be readable at all); (c) accept the refusals.
4. **MEASURED:** a hot rollback journal (C02) is refused by SQLite itself with the typed `SQLITE_READONLY_ROLLBACK`
   and no write attempt - maps cleanly to `STORE_BUSY`. A missing store (C09) is `SQLITE_CANTOPEN` with no file
   created. A store deleted mid-read (C08) keeps serving the open connection - the response is correct for the
   observation time but the file no longer exists; the adapter should re-stat and label it.
5. **MEASURED:** no lock wait or `SQLITE_BUSY` occurred against an active WAL writer (WAL readers never block on
   writers). Rollback-journal + active writer was not measured (Kriya does not produce rollback-journal stores).

## Not done / for the owner

- `fs_usage` confirmation (root). If wanted, run (two terminals, or `&`):
  ```
  sudo fs_usage -w -f filesys 2>/dev/null | grep --line-buffered 'a1work/run_fs' > /tmp/a1_fs_usage.log &
  PYTHONDONTWRITEBYTECODE=1 .newvenv/bin/python -B ui/spikes/a1_zero_write/harness.py \
    --python "$PWD/.newvenv/bin/python" --work-dir <scratch>/a1work/run_fs --keep --out-dir ui/spikes/a1_zero_write/results/fs_usage_run
  sudo kill %1
  ```
  Expected: for sandboxed readers no `WrData`/`open` with write flags on `traces.db*` succeeds; for the unsandboxed
  C03/C04 readers `-wal`/`-shm` opens with write access appear.
- `immutable=1` was NOT measured (D-4 forbids it for the adapter; measuring it belongs to the new topic D-4 names).
