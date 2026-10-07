# REG-R1 context-stability certification - implementation 5c55630 (no model calls)

Design: context-matched stability-aware pytest authority. Tested tree = commit 5c55630.

| Gate | Result | Evidence |
|---|---|---|
| BEFORE (6d242e7, isolated replays): generic workflow reproducer with a suite-dependent pre-existing failure | `stop_environment`; `test_z_fails_only_under_the_full_suite` STABILITY_UNRESOLVED (`BASELINE_REPLAY_OUTCOME_DIFFERS`) - the Graphify mechanism | `before_context_fix_workflow_reproducer.txt` |
| AFTER: same reproducer | passes 3/3; two full-suite replays; suite-dependent failure outcome/type STABLE, message VOLATILE -> PRE_EXISTING | `tests/test_reg_r1_workflow_reproducer.py` |
| Context controls c01-c12 | all pass (full-suite context replay, isolated pass not used, FAIL/PASS/FAIL and FAIL/ERROR/FAIL -> BASELINE_OUTCOME_UNSTABLE, type instability, stable changed blocks, volatile ignored with stronger fields binding, context/revision/selection mismatch re-establishes, one replay pair per context, candidate never an input) | `tests/test_reg_r1_pytest_regression_authority.py` |
| Focused | 61/61 | |
| Adjacent | 1133/1133 | |
| Full suite | 9271 passed / 0 failed | `full_suite.txt.gz` |
| Mutation | run 1 34/36 (selection-only mutant EQUIVALENT: target_test stays bound - redefined as both lines; post-replay revision check unpinned - test added); survivors re-run 2/2; final 36/36 | `context_mutants_*.json` |
| ruff / pylint | 0 / 0 | |

Implementation 6d242e7 and certification 12cc872 remain valid evidence for the first mechanism (history not rewritten).
WORKTREE-SYNC-BYTECODE-CACHE-001 stays separately DEFERRED (not folded in; it did not prevent certification).
