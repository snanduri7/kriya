# REG-R1 certification - implementation 6d242e7 (no model calls)

Tested tree = commit 6d242e7 (no file changed between the final full suite, the mutation run and the commit).

| Gate | Result | Evidence |
|---|---|---|
| BEFORE (unmodified product, `kriya/` == a049c02): untouched base vs itself | level1 CHANGED_FAILURE, blocking; level2 3/3 PRE_EXISTING | `before_fix.json` |
| BEFORE workflow reproducer (real direct pipeline) | `stop_environment` (REGRESSION_UNATTRIBUTED) - the OBS-4 stop | `before_fix_workflow_reproducer.txt` |
| AFTER: same pair, production entry point, real replays | per-test authority, blocking false, volatile: message+body / body | `after_fix.json` |
| Focused (REG-R1 unit+real+workflow) | 47/47 | `tests/test_reg_r1_*.py` |
| Adjacent (validation baseline, regression/P2 attribution, Surefire, PRD-024, FS-1*, B2/B3, checkpoint/resume, LR-R1-M1 recorder, capability adapters, file integrity) | 1133/1133 | - |
| Full suite run 1 | 9255 passed / 2 failed - both required contract updates (FILE-INTEGRITY write-site audit for the replay copy; an exact-dict adapter assertion now also checks the new INDETERMINATE pytest evidence) | `full_suite_1.txt.gz` |
| Full suite run 2 (after that repair) | 9257 passed / 0 failed | `full_suite_2.txt.gz` |
| Mutation | 26/27 first run (pre-replay revision check survived -> test added, 1/1 killed), final run 27/27 | `mutants*.json` |
| ruff / pylint | 0 / 0 | - |

Changed existing assertions (never weakened): `test_validation_baseline.py::test_20` now asserts the stronger REG-R1
facts (the newly failing test is NEW_FAILURE, previously the disclosed NOT_COMPARABLE limitation; a pre-existing failure
whose body the candidate changed is STABILITY_UNRESOLVED until the baseline is measured, CHANGED_FAILURE if stable,
PRE_EXISTING if volatile), with companion `test_20c` keeping the original intent (only the new failure attributed);
`test_capability_adapters.py` pops and asserts the new evidence.

Residual (recorded, not fixed - outside REG-R1 scope): `worktree._sync_uncommitted_changes_into_worktree` copies
untracked non-ignored files into the candidate worktree; in a repository that does not ignore `__pycache__`, the
baseline run's pytest assertion-rewrite bytecode (compiled at the workspace path) is then reused there and a failure's
crash line names the workspace file (measured 2/5 runs; 0/5 with `__pycache__/` ignored). Under REG-R1 such a repository
cannot have a stable baseline measured (its own run changes the pristine revision), so it fails closed unattributed,
never blamed (`test_reg_r1_workflow_reproducer.py` second test). A repository with no Graphify-like ignore rules and
pre-existing valid bytecode is the narrow remaining case. Suites with a pre-existing COLLECTION error have an
incomplete baseline (pytest exit 2) and keep fail-closed behaviour.
