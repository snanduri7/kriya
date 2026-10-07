# 02a — Proposer's response and gate options (Claude). This is input to `03_GATE.md`, not a review.

**Date:** 2026-10-04.
**Responds to:**
- `02_REVIEW.md` (ChatGPT);
- the implementing agent's alternative, `ui/docs/D4_STABLE_SNAPSHOT_READ_STRATEGY.md` (`db16acb`, specification only), with its fixture probe `ui/spikes/a1_zero_write/results/backup_probe.md`. I read the probe results; I did not re-run them.

## Responses to the review

| Finding | Response |
|---|---|
| **R-1 BLOCKER** (Q-1 detects changes; it does not exclude writers) | **ACCEPTED.** `immutable=1` turns off SQLite's own locking and change detection, and my before/after guard only *observes*. It catches the realistic races (a writer must create `-wal` or change the file's size or mtime), but it is detection, not a guarantee, and my labels "never stale" and `quiescent_snapshot_verified` overstated it. **I withdraw Q-1 as the recommended strategy.** |
| **R-2 MAJOR** (consistency vs freshness; random races are not proof) | **ACCEPTED.** The gate must name the guarantee: *one consistent committed state as of a recorded time*, never "latest". Race fixtures must use barriers (forced interleavings) as well as random timing, with invariants across tables. The same applies to whichever strategy is chosen. |
| **R-3 MINOR** (side files present ≠ RUN_ACTIVE) | **ACCEPTED.** The reason becomes "side files present; read strategy unavailable". RUN_ACTIVE comes only from `runs status`. |
| **R-4 MAJOR** (taking a safe copy of the real store) | **ACCEPTED.** The snapshot strategy below *is* a SQLite-aware acquisition procedure, so adopting it resolves R-4 as well. It is still used on real data only after D-9 is lifted. |

## Recommendation: adopt the implementing agent's **stable-snapshot strategy (S)** instead of Q-1

**Why S answers R-1 where Q-1 cannot:**
- Consistency comes from **SQLite's own read transaction**. The backup API copies the whole store inside one read transaction; WAL readers see a fixed snapshot. That is a guarantee, not a check after the fact.
- **Measured on fixtures:** the snapshot is consistent with an active writer (121 rows, `quick_check` ok, 14 ms), and the main `traces.db` file is never written in any state.
- **Inspection reads only the snapshot,** after it is converted to rollback-journal mode, with files `0400`. That read is **zero-write, with D-4 applied literally** (measured: zero sandbox denials).

**What S costs, and the owner must explicitly approve it:**
1. **A separate acquisition authority.** Acquisition may create or modify the store's `-wal`/`-shm`, which are SQLite's own side files. Kriya itself creates the same files every time it opens the store. It never writes a page of the main file and never checkpoints.
2. **Kriya writes inside its own state directory:** `kup-snapshots/` (directory `0700`, files `0400`), with retention (N=3, 1 GiB) and atomic publication.
3. **Snapshots are stale by design.** They are labelled `snapshot HH:MM:SS` with "source changed since" when it has. That is honest, and it satisfies R-2.

## Conditions I recommend attaching to S in `03_GATE.md`

| # | Condition |
|---|---|
| S-1 | **The guarantee:** "one consistent committed state of the store as of `acquired_at`". KUP `consistency.kind = snapshot_copy`. The UI never says "current" or "latest". |
| S-2 | **Acquisition is explicit:** Refresh or `kriya traces --snapshot`. Never inside `generate`/`fix`, and never implicit in an inspection. **Skip acquisition when the source fingerprint is unchanged** since the newest snapshot; reuse that snapshot instead of copying the store again. |
| S-3 | **Allowed source-side effects:** exactly `-wal`/`-shm`. Enforced by a sandbox test that allows writes only to the snapshot directory and the store's side files. Any write to `traces.db` itself fails the test. |
| S-4 | **Sensitive-data duplication:** a snapshot copies everything, including `prompt_rendered`. Keep `0700`/`0400`, keep everything inside the state dir, apply the retention limit, and make `--snapshot-prune` remove everything. Record this as an accepted duplication of data already held in the state dir. |
| S-5 | **Classification of the snapshot directory:** SEC-009 / state-path classification must be **TRACED in code** in Phase C, not assumed "like `paths.state`". |
| S-6 | **Race acceptance (R-2):** barrier-forced interleavings, including a checkpoint *during* the backup, side files appearing and disappearing, database replacement and a writer finishing during the copy. Cross-table invariants against known committed generations. **Pass:** every snapshot equals exactly one committed generation, or acquisition refuses with a typed error. |
| S-7 | **New error codes** (`SNAPSHOT_MISSING`, `SNAPSHOT_FAILED`, `SNAPSHOT_TOO_LARGE`, `SNAPSHOT_CORRUPT`) and the closed `database_state` vocabulary from the strategy document: a Phase B addendum. The provisional fields become final only after this gate. |
| S-8 | **Real-store use only after D-9 is lifted.** The first real acquisition uses S itself (the backup API in one read transaction). No ad-hoc file copy, no side-file deletion, no checkpoint. That is the SQLite-aware procedure R-4 asks for. Report the measured duration and size for the real store. |
| S-9 | **Acquisition while RUN_ACTIVE is observed for the same workspace: refused by default** (policy, per the strategy document §9). The owner may relax this later in a new topic. |

## Options for the owner (`03_GATE.md`)

- **A — adopt S with S-1 to S-9 (recommended).** Phase C proceeds on S. D-4 stays literally true for inspection.
- **B — keep D-4 unchanged.** M1 cannot show real history; the panels stay on fixtures.
- **C — best-effort Q-1** with a weaker, honestly labelled guarantee. **Not recommended** now that S exists.
