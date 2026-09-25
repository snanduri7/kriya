# PRD-011 Pytest Verification

## Verdict
PENDING: superseded by the PRD-011 reopen (see `PRD-011_CODING_HANDOVER.md`, "Reopen closure"). The record below
verified the pre-reopen revision (54ad798/d6b775e), which `PRD-001_011_INDEPENDENT_REVIEW.md` found BLOCKING. It is
kept for provenance and is not evidence for the reopened code.

## Superseded record

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
