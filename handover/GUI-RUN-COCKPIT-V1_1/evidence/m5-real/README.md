# M5 real-data run (2026-10-10): Open Folder + persistent read transport

Safe copy only: `~/kriya-gui-m2` (HOME, `KRIYA_STATE_DIR`, content-ON configuration approved earlier with `kriya authority approve --confirm`). No live model. The packaged production bundle (`npm run build:cockpit`, check-bundle OK) under real Electron; `run-capture.sh <spawn|service> <dir>` drives it with `capture-m5.json`.
Folders opened (throw-away directories): the two paths the real runs recorded as their workspaces (`pin.V0ZC/ws` made a Git project; `p3dpin/ws` a plain non-Git folder with a file), a new empty folder, a dirty Git repo (`feature/clamp`, 1 modified + 1 untracked), a missing path, a mode-000 folder; plus a stale recent entry. The only stand-in is the scripted chooser (`KRIYA_UI_CAPTURE_CHOOSE`, reachable only while the screenshot driver runs): it replaces the native `dialog.showOpenDialog` call and nothing after it. **The native dialog itself was not automated and needs one manual click-through by the owner.**

## Before / after (ms, this machine, content ON; `spawn/results.json` vs `service/results.json`, same build)
| Step | spawn (before) | persistent (after) |
|---|---|---|
| Cold backend: first request (child start + ready + op) | 879 (capabilities, per request) | 707-712 once |
| Open Folder validation (workspace.describe, warm) | 831-914 | 7-124 (14 first, git probes 80-124) |
| Open folder -> first Runs (describe + runs list) | 1817 | 182 |
| Run -> Timeline | 2433 | 61 |
| Validation / Context / Plan / Evidence / Changes first render (warm) | 16-20 | 16-18 |
| Lazy authorized diff (change_content) | 796 | 16 |
| Switch workspace A -> B | 1713 | 153 |
| Switch back to A | 2401 | 183 |
Per request: spawn 0.8-0.9 s (interpreter + imports, MEASURED ~451 ms import alone); persistent warm 7-40 ms. Prediction made before the change (repeated start-up removed, operation time remains) confirmed. M4 baseline (2467 / 2461 / 812) reproduced by the spawn column.

## What the screens show (`service/`)
01 welcome (recents) - 02 stale recent fails cleanly (Remove from recent) - 03 Workspace of the Git project (identity, branch, revision, clean, state) - 04/05 that workspace's single run and its authorized diff; Runs says "4 run(s) in this state store belong to other workspaces; 349 have no recorded workspace and are not listed" - 06/07 switch to the non-Git folder: a different run, selection cleared, "Not a Git repository" - 08/09 new empty folder: "No run is recorded for this workspace" - 10 dirty repo "2 changed or untracked entries reported by Git" - 11/12 missing / unreadable folder: typed notice, the open workspace stays open - 13 cancel changes nothing - 14 back to the first workspace.
Nothing is classified greenfield or brownfield anywhere; the same fields describe every folder.

## Not demonstrable on real data
No run is bound to the empty/dirty folders (only the two recorded paths have runs). 349 of 354 trace rows have no evidence store and appear in no workspace (GUI-RUN-ATTRIBUTION-001).
