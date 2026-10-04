# Stable-snapshot read strategy for the D-4 gate decision

Status: **approved with six conditions in `handover/GUI-D4-READ-STRATEGY/03_GATE.md` (2026-10-04) and implemented on
fixtures in `kriya/kup/` (commits `db96b12`, `f4bd13f`) - see `ui/docs/PHASE_C_HANDOFF.md` for the as-built decisions
(the gate wins where this text differs: e.g. §4's 2 GiB and §8's 1 GiB were resolved as 512 MiB per snapshot, newest 3
retained, 2 GiB directory ceiling; history reads require a snapshot id; metadata equality never skips an acquisition).**
D-4 stands as written for inspection: no `immutable=1`, no `-shm` exception.

Evidence this rests on: `ui/spikes/a1_zero_write/A1_REPORT.md` (the read candidate, nine store states) and
`ui/spikes/a1_zero_write/results/backup_probe.md` (the acquisition candidate, measured 2026-10-04 on fixtures with
`.newvenv`: Python 3.14.6, SQLite 3.53.4). Every claim below is tagged MEASURED, TRACED or INFERRED.

## 1. The problem in one paragraph

Kriya's stores are WAL databases (TRACED `kriya/core/db.py:9-20`). MEASURED (A1): SQLite cannot open a WAL store
read-only without creating `-wal`/`-shm` when they are absent (C03, the resting state after a clean close) and writes
`-shm` when they are present (C04/C05). So a write-free `mode=ro` inspection of the LIVE store is impossible for every
real store state; under D-4 the KUP adapter must refuse them all. A rollback-journal database, by contrast, reads with
zero write attempts under `(deny file-write*)` (A1 C01, C06, C08).

## 2. The strategy: two operations with two different authorities

| | **Acquisition** (new, explicit, Kriya-owned) | **Inspection** (the KUP read path, D-4 as written) |
|---|---|---|
| what it touches | the live store (read) and its `-wal`/`-shm` sidecars (SQLite's own side effects); writes ONE new file tree in a Kriya-owned snapshot directory | the snapshot file only |
| authority | the same authority Kriya already exercises every time `TraceLogger`/`get_connection` opens the store; declared, logged, never implicit | strictly write-free: `mode=ro`, no pragma that writes, no `immutable=1`, no `-shm` exception; typed refusal otherwise |
| consistency | one SQLite read transaction (backup in one step) | a rollback-journal file nobody writes |
| invoked by | the host's "Refresh" (acquire, then inspect) or an explicit `kriya traces --snapshot` | every KUP query |

The two are never merged: inspection never falls back to the live store, and acquisition never answers a query.

## 3. Acquisition - how consistency is guaranteed

MEASURED (`backup_probe.py`), the candidate sequence:

1. `sqlite3.connect("file:<store>?mode=ro", uri=True, timeout=2.0)`; `setconfig(SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)`;
   `PRAGMA wal_autocheckpoint=0` (a read-only connection cannot checkpoint anyway; both settings make that explicit).
2. `Connection.backup(dest, pages=-1)`: the whole copy inside ONE read transaction, so the image is the store exactly
   as that transaction saw it (WAL readers see a fixed snapshot of the WAL; a writer committing meanwhile - C05 -
   does not disturb it: 121 rows copied, `quick_check` ok, 14 ms).
3. On the DESTINATION only: `PRAGMA journal_mode=DELETE` (the snapshot becomes the A1-C01 state), `PRAGMA quick_check`,
   `COUNT(*)` for the manifest, close.
4. Write the manifest, rename the temporary snapshot directory to its final name (atomic publish). A partial snapshot
   is never visible.

What acquisition changes on the SOURCE side (MEASURED, inventory before/after, main file never touched):

| store state | source-side effect of acquisition | after |
|---|---|---|
| WAL, clean (C03) | SQLite CREATES `-wal` (empty) and `-shm` and leaves them behind (a read-only connection cannot delete them) | the store is in the C04 state; the next Kriya writer uses them normally (TRACED: SQLite semantics; INFERRED for Kriya's next run - check: run the harness writer after an acquisition) |
| WAL with sidecars (C04) / active writer (C05) | `-shm` modified (the wal-index), nothing else | unchanged otherwise |
| hot rollback journal (C02) | REFUSED `SQLITE_READONLY_ROLLBACK` before any write - acquisition never recovers another process's transaction | untouched |
| missing (C09) | REFUSED `SQLITE_CANTOPEN`, nothing created | untouched |
| locked beyond the busy timeout | REFUSED `SQLITE_BUSY` after 2 s (INFERRED: WAL readers do not wait on writers, so this is expected only for a rollback-journal store under an exclusive lock; check: an A1 case with `BEGIN EXCLUSIVE` held by the fixture writer) | untouched |

The main database file is never written by acquisition in any measured state. This is the honest statement the gate
would approve: **acquisition may create or modify the source's `-wal`/`-shm`; it never writes a row, a page of the
main file, a checkpoint, or anything under the workspace.**

## 4. What acquisition reads and writes, and with which permissions

- Reads: `<state dir>/traces.db` (+ `-wal`/`-shm` as SQLite needs), its `stat` before and after (size, mtime_ns,
  inode) and the sizes of the sidecars, for the manifest. Nothing in the workspace. No log file is written (the KUP
  path keeps the "no logging bootstrap" rule of 06 §C2).
- Writes, all inside `<state dir>/kup-snapshots/` (owned by Kriya's state directory authority, SEC-009-classified
  like `paths.state`; never inside the workspace, never the operator's home outside the state dir):
  - `tmp-<snapshot_id>/traces.snapshot.db` then the directory renamed to `<snapshot_id>/`;
  - `<snapshot_id>/manifest.json`: `snapshot_id` (UTC time + 8 hex random), `acquired_at` (UTC), `source`
    (absolute path), `source_stat_before`/`source_stat_after` (size, mtime_ns, inode; `wal_size`, `shm_present`),
    `source_journal_mode`, `sqlite_version`, `kriya_version`/`commit` (from `kriya version --json`'s source),
    `rows`, `snapshot_sha256`, `quick_check`, `duration_ms`.
  - Permissions: directory `0700`, snapshot and manifest `0400` once published (read-only; the inspector then cannot
    write it even by mistake, and a tampered snapshot shows as a digest mismatch).
- Size bound: refuse when the source is larger than a configured ceiling (proposal 2 GiB; `SNAPSHOT_TOO_LARGE`),
  and when the free space in the snapshot directory is below source size + 10 % (`SNAPSHOT_FAILED` with reason).
- Timing (MEASURED on a 3 MB fixture): 12-14 ms. INFERRED for a real store: proportional to size, disk-bound; the
  gate can require a measured number on a copy of a real store once D-9 is lifted.

## 5. Inspection - what changes versus today's plan

- The KUP commands read `<state dir>/kup-snapshots/<current>/traces.snapshot.db` with the A1 read candidate (`mode=ro`,
  `temp_store=MEMORY`). MEASURED (`backup_probe`): zero denials under `(deny file-write*)`, no file in the snapshot
  directory changed. The rollback-journal snapshot is the C01 state; D-4 is satisfied literally, not by exception.
- "Current" snapshot = the newest published directory whose manifest `source` equals the resolved store path and whose
  digest verifies. Inspection NEVER acquires; with no snapshot it returns the typed `SNAPSHOT_MISSING` and the host
  offers acquisition.

## 6. Freshness labels (KUP envelope, provisional fields made concrete)

`consistency.kind = "snapshot_copy"` (today's provisional string becomes this value), plus:

```json
"consistency": {
  "kind": "snapshot_copy", "live_stream": false,
  "snapshot_id": "...", "acquired_at": "UTC",
  "age_seconds": 12,
  "source_fingerprint_at_acquisition": {"size": 1, "mtime_ns": 2, "inode": 3, "wal_size": 4},
  "source_fingerprint_now": {"size": 1, "mtime_ns": 2, "inode": 3, "wal_size": 4},
  "source_changed_since_acquisition": false
}
```

- `source_fingerprint_now` comes from `stat` only (no open; `stat` is not a write and needs no lock).
- Labels the trust strip shows, derived only from these fields: **fresh** (acquired by this Refresh),
  **snapshot HH:MM:SS, source unchanged**, **snapshot HH:MM:SS, source changed since** (a new acquisition is offered),
  **source unavailable now** (stat failed; the snapshot is still shown, labelled). The word "current" is never shown for
  a snapshot older than the Refresh that produced the view.
- `observed_at` stays the inspection time; `acquired_at` is the data time. Both are displayed (P-22).

## 7. Failure behaviour (typed, never a silent fallback)

| condition | acquisition result | inspection result |
|---|---|---|
| hot rollback journal / lock timeout | `STORE_BUSY` (+ `database_state`) | unaffected: the previous snapshot is still readable, labelled stale |
| store missing | `READ_ONLY_UNAVAILABLE` with `database_state: "missing"` | `SNAPSHOT_MISSING` if none exists |
| source too large / disk full / backup error | `SNAPSHOT_TOO_LARGE` / `SNAPSHOT_FAILED`; temp directory removed | previous snapshot stays |
| digest or `quick_check` failure on the published snapshot | - | `SNAPSHOT_CORRUPT`; that snapshot is never served again and is deleted at the next acquisition |
| acquisition interrupted (crash, SIGKILL) | `tmp-*` left behind; removed by the next acquisition | previous snapshot unaffected |
| any refusal | nothing read from the live store by inspection, ever | typed error envelope; the host clears "current" (P-32) |

Error codes `SNAPSHOT_MISSING`, `SNAPSHOT_FAILED`, `SNAPSHOT_TOO_LARGE`, `SNAPSHOT_CORRUPT` would join the six in
`common.schema.json` (schema change, Phase B addendum) and `database_state` would become a closed vocabulary
(`wal_clean`, `wal_sidecars`, `wal_active_writer`, `hot_journal`, `missing`, `readonly_directory`, `locked`) - the
provisional fields were left open for exactly this.

## 8. Cleanup and retention

- Retention: keep the newest N published snapshots (proposal N = 3) and at most a total size (proposal 1 GiB); the
  oldest are deleted by the next acquisition, never by inspection. `kriya traces --snapshot-prune` for an explicit
  prune. The live store and its sidecars are never deleted, truncated or checkpointed by either operation.
- Crash safety: `tmp-*` directories are removed at the start of the next acquisition; a published directory is only
  ever added or removed whole.
- Operator view: `kriya traces --snapshots --json` lists snapshots with manifests; `doctor --production` could report
  the snapshot directory size and the newest age.

## 9. What the gate would authorize (and what it would not)

Authorize: one new acquisition command (`kriya traces --snapshot --json`, plus `--snapshot-prune`/`--snapshots`), the
snapshot directory under the state dir, the four error codes, and the inspection path reading snapshots only. The
P-25 inspection grammar is otherwise unchanged; `capabilities.features.snapshot = true` advertises it.

Not authorized by this strategy: reading the live store during inspection, `immutable=1`, any `-shm` exception,
checkpointing, any write under the workspace, acquisition inside a `generate`/`fix` run (acquisition is operator- or
host-triggered only), and acquisition while `RUN_ACTIVE` is observed for the SAME workspace unless the owner says the
`-shm` touch is acceptable then (INFERRED: SQLite allows it - a reader's `-shm` write does not block a writer; the
question is policy, not correctness; check: C05 shows the writer kept committing, 121 rows copied).

## 10. Acceptance evidence the implementation would have to produce (if approved)

1. The A1 harness extended with an acquisition step per store state, under `sandbox-exec` with writes allowed ONLY
   to the snapshot directory: every denial classified; the allowed source-side set is exactly `-wal`/`-shm`
   (MEASURED prediction from `backup_probe`: C03 create both; C04/C05 modify `-shm`; C02/C09 refuse with nothing
   written). A denial on `traces.db` itself fails the test.
2. Inspection of every snapshot under full `(deny file-write*)`: zero denials (prediction: as measured).
3. Mutation: remove `PRAGMA journal_mode=DELETE` on the destination -> the snapshot is a WAL file -> inspection under
   deny-write must fail (A1 C03) -> the test must fail. Remove the atomic rename -> a crashed acquisition must leave a
   visible partial -> the test must fail.
4. Consistency: an active writer during acquisition; the snapshot's row count and digest equal a `BEGIN; SELECT`
   taken in the same transaction window (the backup API's guarantee, re-measured rather than trusted).
5. Byte-identical existing CLI output when no snapshot flag is given (06 §C4), and the full suite at `-n 2`.
