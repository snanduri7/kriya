# 01 — Proposal: a guarded immutable read for a quiescent store; typed STORE_BUSY otherwise (Claude)

## Facts (MEASURED in A1, unless marked)

- **F-1 (C03):** a cleanly closed WAL store has no side files. `mode=ro` cannot open it without **creating** `-wal`/`-shm`.
- **F-2 (C04/C05):** with side files present, `mode=ro` **writes** to `-shm`. That state means a writer is active, or one exited without a checkpoint.
- **F-3 (TRACED, `kriya/core/db.py:9-20`):** every Kriya connection uses WAL mode.
- **F-4 (INFERRED; check in step 1 below):** `immutable=1` performs no locking and no side-file access, so it writes nothing. Its only risk is reading while a writer changes the file.

## Proposal

**Q-1. Quiescent store** (no `-wal`, `-shm` or `-journal` present):
1. Record `stat(db)` (inode, size, mtime in ns) and confirm there are no side files.
2. Open `file:…?mode=ro&immutable=1`. Run all queries in **one** read.
3. Afterwards, check `stat(db)` is identical and there are still no side files.
4. If anything changed, **discard the result** and return `STORE_BUSY`. Never return partial or stale data.

**Why this is safe:** in WAL mode a new writer first creates `-wal` and writes there. The database file changes only on checkpoint, which changes mtime/size. So "no side files before and after, plus an unchanged stat" detects any writer that started during the read.

**Q-2. Side files present:** return typed `STORE_BUSY` (a run is active, or the store was not cleanly closed). No read is attempted, so no `-shm` write happens. Live progress is the separate, later `ui-server` topic.

**Q-3. Missing store:** `READ_ONLY_UNAVAILABLE`, creating nothing (as measured).

**Q-4. Rollback-journal stores:** as measured: a plain `mode=ro` read is zero-write; a hot journal gives `READ_ONLY_UNAVAILABLE`.

## What this changes in D-4

- It allows `immutable=1` **only** in state Q-1, and only with the before/after guard.
- Everything else in D-4 stands:
  - typed refusal in every other state;
  - no `-shm` exception;
  - zero writes, enforced by the sandbox test.

## Acceptance (fixtures, in the A1 harness, allowed under D-9)

1. Under `deny file-write*`: Q-1 reads with **zero** attributed write attempts. **This verifies F-4.**
2. A race test: a fixture writer starts during a Q-1 read, repeated 200 times, with randomized timing. Every result is either complete and correct, or `STORE_BUSY`. **There are 0 torn or stale results.**
3. Q-2: side files present → `STORE_BUSY`, with zero writes.
4. After the owner lifts D-9: a **copy** of the real `~/.kriya` `traces.db` taken after a clean close confirms it is in state Q-1. The original is never opened.

## Residual risk (INFERRED)

- A writer that creates a store and has fully checkpointed and closed it between our two checks leaves no side files, but changes size and mtime. The stat check catches that.
- A clock-free mtime collision with an identical size is theoretically possible on a coarse-timestamp filesystem. APFS has nanosecond timestamps, so it is negligible. Including the inode in the check also covers file replacement.

## Owner question for `03_GATE.md`

Adopt Q-1 to Q-4, replacing D-4's "no `immutable=1`" with the guarded Q-1 read? Or keep D-4 and accept that M1 cannot show real history?
