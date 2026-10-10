# M4 real-evidence run (2026-10-10)

Same safe copy as M3: `~/kriya-gui-m2` (isolated HOME, `state`, `config` content ON approved via `kriya authority approve --confirm`, `config-off` default). Owner's `~/.kriya/state` and frozen experiment stores untouched; no live model.
Command shape: `HOME=~/kriya-gui-m2/home KRIYA_STATE_DIR=~/kriya-gui-m2/state KRIYA_UI_ALLOW_REAL_KRIYA=1 KRIYA_UI_CAPTURE=<dir> KRIYA_UI_CAPTURE_STEPS=ui/standalone/capture-m4-{on,off}.json npx electron . --user-data-dir=...` (production bundle, `check-bundle` OK).

Real runs: f8eeec67 -> store 20261006T164431-9d216cb0 (sealed, VERIFIED, 44 records: 2 model.result, 2 prompt.sections, 1 candidate.change; one attempt); a5f13320 -> 20261006T163143-1f1002d3 (unsealed failed, diagnosis); 030f1894 pruned.
Screens (`on/`, `off/`): 01 Context, 02 Plan, 03 Attempts, 04 Evidence, 05 record detail, 06 grouped by kind, 07-09 failed unsealed run, 10 pruned.

Measured (ms, this machine, content ON; OFF within noise): acquire to first Runs 2467 (2433 off); select run to Timeline 2461; Context first render 16 (18 off); Plan 17; Attempts 17; Evidence 18; select unsealed run to Timeline 2446.
Context authorized-content retrieval: NOT measurable on the real copy (no `model.request` record exists in any real store); the same spawn path measured 812 ms for `change_content` in M3 (one process: verify, class/size checks, read, re-hash). Covered functionally by provider contract and `real_kriya` classes test.
All four new modules read the already-loaded run evidence, hence ~16 ms. Spawn latency (about 0.78 s per kriya process, three serial per run open) is unchanged: `GUI-KRIYA-PROCESS-STARTUP-LATENCY-001` stays P2 DEFERRED (not closed, no server/cache added).

Real-data honesty: Attempts shows one attempt and no comparison; Plan shows two direct units, authority targets, requirements, lineage, obligations, "No plan validation or repair event was recorded", and "Not recorded" for dependencies and immutable targets; Context shows recorded sections/budget/omissions with DERIVED context use, and no prompt text (no model.request).
