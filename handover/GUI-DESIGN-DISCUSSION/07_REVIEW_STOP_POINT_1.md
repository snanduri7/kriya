# 07 — Review of stop point 1 (Phase A) — Claude, reviewer

**Date:** 2026-10-04.
**Reviewed:** the implementing agent's stop-point-1 report, `ui/docs/PHASE_A_REPORT.md`, `ui/spikes/a1_zero_write/A1_REPORT.md`.
**HEAD:** `efef852`.

## Verified independently (MEASURED, read-only)

- Four local commits above `61a867f`: `5159cb5` GUI-A1; `aa67320`, `43327dd`, `efef852` GUI-A2.
- No changes under `kriya/`.
- No tracked uncommitted changes.
- `origin` is still the local AntiGravity path; no remote was added.
- The A1 report's results match the summary. Its method is sound:
  - writes are detected through the sandbox's denial log, attributed to the reading process and classified by path;
  - a before/after file inventory is compared without the sandbox;
  - no denials hit unrelated paths;
  - `immutable=1` was correctly **not** tried.

## Verdict

**Phase A is ACCEPTED as evidence, and the agent's discipline was exemplary.** It applied D-4 exactly as written and loosened nothing.

**Phase C is BLOCKED** on one owner decision (§1). Phases B and D may continue.

## 1. The D-4 outcome: the inspector cannot read the measured WAL fixture states under D-4 as written (MEASURED on fixtures; real stores INFERRED)

**The result:**
- Kriya always opens its stores in WAL mode (`kriya/core/db.py:9-20`), so a cleanly closed `traces.db` has no `-wal`/`-shm` side files (case C03).
- In that state, a read-only connection **cannot open the store without creating those side files** (`SQLITE_CANTOPEN` when writes are denied; `-wal` and `-shm` created when they are allowed).
- With side files present (C04/C05), a read-only connection **writes to `-shm`**.
- So under D-4, the adapter refuses every **fixture** WAL state. For **real** stores this is a prediction (INFERRED from `db.py` and standard SQLite close behaviour), not yet a measurement. It is checked on a copy of the real store after D-9 is lifted (D4 topic, acceptance step 4).

This is the risk flagged in `04_REVIEW_2.md` G-2. The owner fixed the fallback *before* measuring, which is correct. Per D-4 ("revisit only in a new topic"), the fix is a new topic, not an edit to this gate.

**Opened:** `handover/GUI-D4-READ-STRATEGY/` with a short proposal. My recommendation:
- a **guarded immutable read for the cleanly closed state**: zero writes, with staleness detected rather than risked;
- typed `STORE_BUSY` whenever side files exist, which is exactly when a run is active.

**Why a stricter reading of D-4 does not help:** a `-shm`-only exception would not fix C03 either, because the store cannot be opened at all without *creating* `-wal`.

## 2. Phase A2 (prototype): accepted, with notes

| Item | Assessment |
|---|---|
| Electron 44.5.1, A-1 hardening tested as data, six-message preload, spawning only in main, a fake `kriya` while D-9 holds | Meets A-1 and P-R1 to P-R3. Moving the hardening test into Phase A is a beneficial deviation. Accepted. |
| Idle memory 393 MB across 4 processes; 475 MB steady after 100 cycles plus GC; peak 604 MB before GC | Above the gate's *estimate* of about 100–300 MB. That was an estimate, not a target, so this is **not a gate failure**. **Record 475 MB steady-state as the baseline.** Add a regression check in Phase E: fail if steady-state after GC exceeds 550 MB on the fixture cycle test. Re-measure with a model loaded after D-9 is lifted. |
| No long task; worst frame gap 106 ms; populate time 76 ms median / 169 ms max on a 6.37 MiB payload | Meets P-35. A fixture larger than 4 MiB is a stricter test. Accepted. |
| Keyboard, focus, unnamed controls (0), contrast ≥ 4.5:1, 800 px layout | Meets P-35, MEASURED. |
| VoiceOver | **Owner action:** a 5-minute check at the machine (§3). |
| `code -g file:7` exits 0 on VS Code 1.140.0 | **Owner action:** confirm by eye that the cursor lands on line 7. |
| Two real defects caught by tests (a cursor starting with `-`; stand-in stdout truncation) | Good. The cursor case is exactly the argument-injection class P-25 requires tests for. Keep both as regression tests. |
| 74 tests green; `npm run check` exits 0 | Accepted (as reported; I did not re-run it, to avoid load during the matrix). |

## 3. Owner actions

1. **The decision in `GUI-D4-READ-STRATEGY/`.** This unblocks Phase C.
2. **VoiceOver check** on the standalone app:
   - Cmd-F5 to turn VoiceOver on;
   - Tab through the Runs list, Timeline and Inspector tabs;
   - each control should be announced with a meaningful name;
   - report pass/fail to the agent.
3. **Confirm by eye** that `code -g` opened line 7.
4. **Optional:** run the `fs_usage` command printed in the A1 report with sudo, to confirm independently that there are no writes.

## 4. Instruction to the agent (until the D-4 topic is gated)

- **Continue:**
  - **Phase B** (the KUP schema). Include `STORE_BUSY` and `READ_ONLY_UNAVAILABLE`, plus the availability field. The `consistency` values and the store-state reasons stay provisional until the D4 gate (see the addendum).
  - **Phase D** (the shared panels), on fixtures.
- **Do not start Phase C** (the Kriya adapter) until the owner gates `GUI-D4-READ-STRATEGY`.
- **Add the Phase E memory regression check:** fail if steady-state after GC exceeds 550 MB.
- **All other rules unchanged:** D-9, no push, nothing under `kriya/`.

## Addendum: after ChatGPT's comments on stop point 1 (2026-10-04)

**Accepted corrections:**

1. **Wording:** §1 overstated "every real store" as MEASURED. That is now corrected to: measured on fixtures, inferred for real stores.
2. **Memory:** flat memory after a *forced* GC shows that the memory can be reclaimed. It does not rule out leaks in a normal long session.
   - **New acceptance check (Phase E): a soak test without forced GC.** 2 hours, or 1,000 selection cycles with periodic refreshes. Record memory across all processes every 5 minutes.
   - **Pass:** no upward trend after the first 15 minutes, and the 550 MB ceiling holds.
   - **The owner must explicitly accept the 393 MB idle footprint** against the earlier 100–300 MB estimate. The reviewer setting a baseline is not the owner's acceptance.
3. **Open in IDE:** exit code 0 shows that the command launched, not that the cursor landed on the right line. Keep it reported as "launch verified; cursor placement unverified" until the owner confirms by eye.
4. **Phase B and the read strategy:** the envelope's `consistency` field depends on the read strategy chosen. For example, P-24's `sqlite_transaction_snapshot` would become `quiescent_snapshot_verified` under the D4 proposal's Q-1. **Phase B may proceed on everything else,** but keep the `consistency` values and the store-state availability reasons **marked provisional** until `GUI-D4-READ-STRATEGY/03_GATE.md`.

**Unchanged:**
- **Phase C stays blocked** until the D4 topic is gated.
- **D-4 stays in force** until then. Both reviewers agree on this.

## Addendum 2: soak verification (2026-10-04, after commit `15c36b7`)

**Verified independently (MEASURED, read-only)** from `ui/standalone/measurements/soak-2026-10-04T04-50-09-515Z.json`:
- `forced_gc: false`; Electron 44.5.1; 26 samples over 120 minutes; 1,177 selections; 0 errors.
- **Using the criterion as written (from minute 15 onward):** 22 samples, maximum 401 MB, linear slope **−7.1 MB/h**. The agent's 10-minute warm-up gives −7.3 MB/h; either way the result is the same.
- **PASS:**
  - no upward trend after minute 15;
  - the 550 MB ceiling held;
  - the range from minute 15 on was 354–401 MB.

**Notes:**
- The soak ran 10:20–12:20 IST, **during the CAGC matrix**, alongside the agent's own test runs in its first 30 minutes.
- That is allowed under D-9: it involved no Kriya, Ollama or `~/.kriya`. But it added about 400 MB of memory load and some CPU to the matrix host.
- Future long measurements should wait until the matrix finishes.

**Reviewer recommendation to the owner:** accept the 393 MB idle footprint. It is stable (354–401 MB over 2 hours, no growth) and typical for Electron. This is the cost already accepted with gate A-1.

**Still open:**
- VoiceOver (owner).
- Confirming by eye that VS Code opened at the right line (owner).
- The D4 read-strategy gate. ChatGPT's `02_REVIEW.md` of `GUI-D4-READ-STRATEGY/01_PROPOSAL.md` is still pending.
- Phase C stays held.
