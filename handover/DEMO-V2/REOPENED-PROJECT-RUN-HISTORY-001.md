# REOPENED-PROJECT-RUN-HISTORY-001 (2026-10-15)

Start: `a743d9f` == origin/demo/v2. The owner's canonical suite at that HEAD: **11269 passed, 0 failed** (pre-fix baseline; not re-run). No live model; the owner's `~/delete/2/HelloWorld` was not modified (read-only inspection; the only write was a state COPY under the scratchpad).

## Phase 1 answers (MEASURED / TRACED)
1. **Authoritative source of Recent runs:** a published SNAPSHOT of the trace store (`traces.db`) in the STATE DIRECTORY the app reads (`KRIYA_STATE_DIR`, else `~/.kriya/state`), scoped to the folder by each run's evidence manifest `workspace_identity` (`kriya/kup/workspace_ops.py: scoped_history_list`). Not `<project>/.kriya/control/runs` (those are control records; the result projection needs the trace row and the evidence store, which live in the state directory).
2-4. Each window's session pins the NEWEST ALREADY-PUBLISHED snapshot at start (`core/context.tsx` SessionProvider); a new snapshot is acquired only after a run finishes in that window or on a manual refresh - opening a folder does not acquire one. `history.list --workspace` then reads that snapshot, in new `kriya` processes; nothing else is cached across restarts. (Correction after the independent review: an earlier draft said every open acquires.) Consequence: a run recorded by another process AFTER the last published snapshot is invisible on reopen until a refresh (`HISTORY-SNAPSHOT-STALE-ON-REOPEN-001`; not the owner's case - the newest snapshot, 20:09, contains the run).
5. Identity canonicalization (symlinks) is handled and tested (`test_kup_workspace`).
6. No: the GUI knows nothing but what the state directory's snapshot holds.
7. No filter on closed, committed, greenfield or direct-plan runs (tests below).
8. `.kriya/run.lock`: see Phase 7.
9-10. **Here is the defect.** The state directory is a property of the app PROCESS (`child_env.ts` passes `KRIYA_STATE_DIR` only if the operator set it). `demo-v2/scripts/launch_gui.sh --scenario 00` starts the app with the scenario's own state (`~/kriya-demo-v2-work/scenario-00/state`).

## Cause (CONFIRMED by discriminating runs)
- The owner's live run was recorded in the DEFAULT state: its trace row `43c4c2a0` and evidence store are in `~/.kriya/state`; `~/kriya-demo-v2-work/scenario-00/state` is EMPTY (created 20:01, never written).
- Same fresh-process sequence the host runs, over a COPY of the owner's state: `snapshot --workspace HelloWorld` -> `history --workspace HelloWorld` => `['43c4c2a0']` (matched 1).
- Same sequence with an empty state directory (what the launch_gui window reads): `READ_ONLY_UNAVAILABLE ... no trace database exists at the resolved store path` => the UI's quiet "No runs yet.". Opening HelloWorld inside that window changes the folder, not the state directory, so it still showed nothing.
- **First wrong decision:** none in the history projection. The window was pointed at a state directory that does not hold the run, and the empty state was presented as "No runs yet." with nothing saying which store it read. The brief's premise (history discovered from `<project>/.kriya/control/runs`) does not match the architecture and could not work: a listing from control records alone cannot open a result (no evidence store, no trace row).

## Second finding: which folder was opened
The scenario-00 window's persisted settings (`~/kriya-demo-v2-work/scenario-00/gui/userdata/kriya-ui-settings.json`) record `workspacePath: /Users/sriramnanduri/delete/2` - the PARENT of HelloWorld - as the opened folder. MEASURED: `history --workspace ~/delete/2` returns no runs even with the correct state directory (the run belongs to the HelloWorld identity), and `~/delete/2` has no `.kriya`, so no note appears either. If that is what the window showed, it is a folder-choice slip (the macOS dialog opens the folder you are inside when nothing is selected), not a history defect. The owner should confirm the project title in the window reads `HelloWorld`.

## Fix (bounded; the backend history projection is unchanged and correct)
- **UI honesty (`RecentRuns.tsx`):** when a folder has a `.kriya` directory (Kriya has worked there) and the history store shows no run, the Project page says which state directory this window reads and from which source, and that runs made with another state directory need Kriya started with that one. Only recorded facts (`workspace.describe`: `kriya_directory_present`, `state.directory`, `state.source`) - no guess about what happened to the runs. Renderer access unchanged (typed host operations only; no filesystem access, no contract change).
- **Launcher:** `launch_gui.sh --state ABS_DIR` to open a scenario window on the state directory where the runs were recorded (default unchanged: the scenario's own).
- Not done (design): per-project history that follows the folder across state directories (`HISTORY-STATE-DIR-PER-PROJECT-001`).

## Tests
- `tests/test_reopened_project_history.py` (6, every read a fresh `kriya traces --json` process): restart + two reopens list the completed run; project isolation (A's run absent from B); latest three by recorded time (written out of order); success/failure/in-progress retained; malformed/unattributed siblings never suppress valid ones; another state directory yields the typed unavailable error and the project's own control records are NOT a substitute. Mutation: scoping always "match" fails the isolation test.
- UI `project_home.test.tsx` L (3): the note appears only for a folder with a `.kriya` directory and no run, never for a folder with runs or an untouched folder; mutants (note unconditional, note removed) killed.
- Existing coverage: `test_kup_workspace.py` (scope, symlink, paging, bounded scan).

## Phase 7 - run.lock (not involved)
`<project>/.kriya/run.lock` is an `flock` file whose content is retained metadata; a non-blocking shared flock succeeds (nobody holds it), PID 25560 is dead, and `workspace.status` for HelloWorld reports `CLEAN / run_active false`. Acquisition is refused only while a run is ACTIVE. Classification: A (expected retained metadata) and C (unrelated to history). Not deleted.

## Owner visual check
Quit any Kriya window, then open (check that the title of the project page reads `HelloWorld`, not `2`) the app WITHOUT a scenario state: double-click `ui/standalone/out/Kriya.app` (or `demo-v2/scripts/launch_gui.sh --scenario 00 --state $HOME/.kriya/state`), Open Folder -> `~/delete/2/HelloWorld`. Recent runs shows `20261015T200748-08db1057`; selecting it shows Build passed / 1 / 1 tests passed / Runtime verification passed / Ran successfully / output / the collapsed formal requirement disclosure. In a window started with an empty state, the same folder now shows the note naming that state directory.
