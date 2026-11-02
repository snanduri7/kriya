# PRD-034 pre-fix evidence (preserved, not overwritten)

All measured on the PRE-FIX tree: HEAD `78fddee10b95af72ba61a314b0a30b1ffe5b92e6` (branch arch/containment-rebuild-v2), macOS arm64, Python 3.14.6, 2026-11-02.

| File | What it shows | Status |
|---|---|---|
| `deselected-node-ids-at-78fddee.txt` | the 46 tests `-o addopts= -m "not live_model"` deselected (19,682 collected, 19,636 selected); equal to the live_model set | MEASURED |
| `live_model-node-ids-at-78fddee.txt` | the live_model set; contains `tests/test_deterministic_failure_diagnostic.py::test_real_baseline_replay_reproduces_failure_against_real_maven` (real Maven, NO model) | MEASURED |
| `real_toolchain-node-ids-at-78fddee.txt` | the 4 real_toolchain tests before the marker correction (expected 5 after) | MEASURED |
| `live_static_analysis-node-ids-at-78fddee.txt` | the 11 live_static_analysis tests | MEASURED |
| `timing-family-node-ids.txt`, `timing-family-under-load-PREFIX-run1.txt` | the 9-node timing family run under 24 busy loops + 8 xdist workers pre-fix: `1 failed, 8 passed` (`test_e_an_attempt_that_made_no_model_call_is_not_applicable`: `INFERENCE_DEADLINE_EXHAUSTED ... left 0 ms`, no attempt record) | MEASURED |
| `timing-family-under-load-POSTFIX-run{1,2}.txt` | the same command after the fix: `9 passed` twice | MEASURED |
| `tools-command-reproducer.py.txt` (run as a script: `python <file> repo|tmp`) | the repo-cwd vs clean-git-workspace driver: repo cwd exit 1 `[Recovery Required] ... handover/SPRING-IGNITE-PROTOCOL/evidence/live-failure-20261016T094912-e7b4804c/...`; tmp git workspace exit 0 | MEASURED |

WAVE5 test pre-fix, run by exact node id with `-o addopts=`: FAILED `BoundTreeError: BOUND_TREE_UNAVAILABLE ... is not the root of a Git work tree` (the test had never run since the R5/R6 bound-tree copy; hidden by the wrong marker). After: `_init_git_repo` + `real_toolchain` marker: 1 passed in 18 s against real contained Maven.
