# CONTAINED-PYTHON-TEST-GATE-001: the contained Python test gate ran zero tests for dependency-free projects

## Status
**FIXED** (2026-10-07) on `fix/reliability-cohort-001` from main 2b82ca6, commit 48279db. Severity P1 (verification
gate executed no test on the baseline or on any candidate under the production profile, while reporting a typed
failure; fail-closed, so never a false success, but every correct candidate was rejected). Registry row CLOSED.
Discovered by the BACKEND-READINESS-001 blind reliability cohort (evidence `~/kriya-m1-live/backend-readiness-001`,
tasks T1 cachetools, T4 python-slugify baseline, T5 jmespath; classifications and two independent reviews there).

## Observation (MEASURED)
T1: both tests gates (PRE baseline, POST candidate) `STRUCTURED_REPORT_MISSING`, 0 cases, stderr
`ModuleNotFoundError: No module named 'pytest'`; stop REGRESSION_UNATTRIBUTED; candidate (the correct one-line fix)
rejected. T4: PRE baseline the same; POST ran 133 tests only because the candidate added a dependency. T5: pytest ran
(requirements.txt declares it) but collected 992 items / 1 error (`extra/test_hypothesis.py`: `No module named
'jmespath'`), session incomplete on PRE and POST; correct candidate rejected.

## Producer (TRACED, reviews confirmed)
1. `kriya/tools/validate.py::_resolve_python_interpreter`: no requirements.txt and no pyproject dependencies ->
   `default_interpreter = "python3" if contained else sys.executable`; the OCI image (`python:3.12-slim`) has no
   pytest; `kriya/capabilities/pip.py::run_tests` builds `[interpreter, "-c", "... import pytest ..."]`.
2. `kriya/capabilities/pip.py::run_tests`: `extra_roots=[workspace_path, workspace_path/src, ...]` - HOST absolute
   paths embedded in the bootstrap; the OCI backend mounts the tree at `/kriya/workspace` and passes argv verbatim
   (no path mapping), so the roots are dead in the container; `tests/` imported only through pytest's package walk.

## Fix (commit 48279db)
1. Under containment a dependency-free project gets the project venv with pytest only (`_ensure_project_venv([])`:
   same `DEPENDENCY_REGISTRY_ONLY` acquisition, memoized); creation failure keeps the bare token (fail closed).
   Host mode unchanged (sys.executable). The resolver is shared with runtime verification.
2. The pytest roots are cwd-relative names resolved inside the child (`os.path.abspath` in the bootstrap): realpath-
   equal on the host, valid at the container mount; shadowing protection unchanged.

## Verification
- Regression: `tests/test_contained_python_test_gate_001.py` (10 tests: every dependency-free manifest shape, host
  mode zero commands, pip failure fails the gate with pip's output, venv-creation failure keeps the bare token,
  contained argv carries no host path, host roots realpath-equal, real host src-layout run); real Docker:
  `tests/test_validate_oci.py::test_contained_python_validation_without_manifest_runs_tests_from_a_pytest_venv`
  (T1's mechanism end to end: COMPLETE report, 1 passed); `tests/test_capability_adapters.py` pins the bootstrap.
- Mutation 1 (revert change 1): 7 failed incl. the Docker reproducer (`remediation/mutation1_*.txt`).
- Mutation 2 (revert change 2): 4 failed incl. the Docker reproducer with `No module named 'pkg'` - change 2 is
  needed even with change 1 (`remediation/mutation2_*.txt`).
- Original symptom re-measured: cohort reruns of T1, T5, T4 (+ T2 control) at the merged revision - see the batch
  report (`~/kriya-m1-live/backend-readiness-001/FINAL_REPORT.md`).
