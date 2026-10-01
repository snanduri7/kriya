# PRD-002/004/005/006/007 - user verification report (2026-09-24)

Run by the user in their own terminal on `milestone-decomposition` at `51d1ca7` (local, unpushed).

- Full non-live suite: `.venv/bin/pytest` -> **4830 passed, 8 deselected (live_model), 149 warnings, 0 failed** (15m43s).
- Release: `KRIYA_PYTHON=.venv/bin/python bash scripts/verify_release.sh` -> wheel + sdist integrity
  (`--source-root`, exact tracked-file match) passed, clean-venv install, `pip check` clean, smoke PASS incl.
  `BUNDLED SKILLS: ['activemq-artemis', 'binary-wire-protocol', 'ignite-java17', 'qpid']` and doctor reporting
  the installed skills directory `[EXISTS]`.

Failures found by the user's runs and fixed before this green run (all test-side or check-order):
- f02ba8a: PRD-005 fault-injection tests also broke the rollback's own restore (code was correct); rollback temp
  file now cleaned on a failed restore.
- f3f356f: new pre-planning gate reason codes classified; `begin_commit` reports an unsettled cycle first.
- 51d1ca7: distribution-test fixture repo now tracks the required files that live in release trees.

Live-model verification: NOT REQUIRED for these tasks. Status: VERIFIED (see TASK_STATUS_TRACKER.csv).
