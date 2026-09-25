# PRD-011 Pytest Verification

## Verdict
PYTEST_VERIFIED @ 99c2340 (reopen closure + final-review correction 6568f11).

## Result (user's terminal)
- Focused suite (the batch-2 list, including `tests/test_prd011_toolchain_migration.py`): green. One earlier
  focused run had a single failure in `test_enforce_loop_dispatches_tool_subtasks_only_through_subtask_executor`;
  it was a stale-source artifact (that pytest process imported `workflow_controller.py` before 6568f11 added 8
  lines above `_run_structured_enforce`, so `inspect.getsource` read the new file at the old line number). It
  passes against the committed code and in the full run below. No code change.
- Full suite `.venv/bin/pytest`: **5399 passed, 0 failed, 9 deselected, 148 warnings** in 1126.32s (0:18:46).

## Superseded record (pre-reopen revision)

### Verdict (superseded)
PYTEST_VERIFIED

## Independent full-suite result
- Initial full-suite result: **4759 passed, 1 failed, 8 deselected, 142 warnings**.
- Duration: **962.88s (0:16:02)**.
- Sole failure: `tests/test_sec002_fail_closed_evidence.py::test_python_compile_check_never_touches_containment` asserted the pre-PRD-011 contract that Python syntax compilation never uses containment.

## Compatibility correction verification
- The stale test now asserts the PRD-011 fail-closed contract: when contained Python compilation cannot establish containment, `ContainmentSetupError` propagates and no host-side syntax PASS is possible.
- Post-fix command: `.newvenv/bin/python -m pytest -q tests/test_sec002_fail_closed_evidence.py tests/test_prd011_toolchain_identity.py`
- Post-fix result: **24 passed, 0 failed, 0 skipped** in **0.53s**.
- Static check: Ruff passed for the corrected regression file; `git diff --check` passed.

## Incremental-verification basis
The full run proved all 4759 unchanged tests passed. The compatibility follow-up changed only the stale regression test, and the focused post-fix run proved that changed test plus the PRD-011 resolver suite passes on the current tree. The user directed the task to continue on this combined evidence rather than repeat the unchanged 16-minute suite.

## Next gate
Run the required PRD-011 Java 17 and Python production-containment live case and record the exact image digests in `handover/PRD-011_LIVE_VERIFICATION.md`.
