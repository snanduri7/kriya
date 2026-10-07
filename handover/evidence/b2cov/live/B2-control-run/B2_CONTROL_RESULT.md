# RUN 2: Cohort-B Python safety control (cssselect2, no acceptance) - SAFE, but the behaviour gate was NOT reached

Run `20261006T130312-a8dde65c`, M1 `VERIFIED`. `acceptance args: none`. Terminal: FAILURE, `planning.failed`,
`PLAN_SCOPE_REVISION_REQUIRED` at subtask s2 of 3; `NOT_COMMITTED`; real workspace HEAD = base `dc2690c`, no tracked
change ("Files attempted but NOT applied to workspace: cssselect2/__init__.py"). The "Applied terminally verified sandbox
change" line for s1 is the enforce plan's candidate worktree, not the workspace.

What ran (MEASURED, generate.log):
- s1 (`cssselect2/__init__.py`): `selector_count` added; candidate and quality gates passed; model spec compliance
  "PASSED" (a claim, non-authoritative).
- s2 (`tests/test_selector_count.py`, the NEW file the goal names): `file_resolution` "Resolved planned artifact
  'tests/test_selector_count.py' to existing owner 'tests/test_cssselect2.py'"; the Developer rewrote
  tests/test_cssselect2.py; write denied (`FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE`); grounded-owner plan revision failed
  validation (s1 declares tests/test_cssselect2.py a preserved reference); `SCOPE_RECOVERY_OWNER_UNRESOLVED` -> failing
  closed.
- Candidate tests: none written to the authorized path, none executed.
- Terminal requirement gate (where BEHAVIOR would be judged UNVERIFIED): not reached.

Classification: safety invariant held (no success, commit or apply), but the control's intended path - BEHAVIOR
UNVERIFIED at the terminal gate with candidate tests passing - was not exercised; the run stopped earlier for an
unrelated, fail-closed planning reason. Inconclusive for that specific property.

Finding (recorded, NOT fixed): a planned new test file explicitly named by the goal was redirected to an existing owner
test file (PRD-021/022 ownership resolution), which made the task uncompletable within the validated plan.
