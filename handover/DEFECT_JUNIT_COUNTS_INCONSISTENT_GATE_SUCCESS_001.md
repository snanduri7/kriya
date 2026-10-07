# JUNIT-COUNTS-INCONSISTENT-GATE-SUCCESS-001: a pytest gate reported PASS while its own structured evidence was inconsistent

## Status
**FIXED** (2026-10-07) on `fix/backend-reliability-closure` from main f757e6b. Severity P2 (load-bearing: with
`unittest.subTest` suites the per-test regression authority refused attribution on both sides, so a complete, green
133-case run could never be attributed - cohort T4 rerun at 9ec7a14 stopped REGRESSION_UNATTRIBUTED). Registry row
CLOSED. Discovered by the BACKEND-READINESS-001 blind reliability cohort; fixed in BACKEND-RELIABILITY-CLOSURE-002
(evidence `~/kriya-m1-live/backend-reliability-closure-002/defects/junit-evidence/`).

## Observation (MEASURED)
Pre-fix, through Kriya's own production test gate (`PolymorphicValidator.run_tests`, host mode, pytest 9.1.1):
- T4 workspace copy: `test_execution.completeness = COMPLETE` while `pytest_evidence.evidence.integrity.ok = false`,
  reason JUNIT_COUNTS_INCONSISTENT, declared `{tests 267, failures 20, errors 0, skipped 8}` vs parsed
  `{tests 133, failures 20, errors 0, skipped 8}` (`pre_fix_measurement.txt`).
- A 2-test project with three passing subtests: gate `success = true` with integrity false, declared tests 5 vs parsed 2.

## Producer (TRACED, measured on pytest's own writer)
`_pytest/junitxml.py` (pytest 9.1.1): `tests=` is the sum of its passed/failure/skipped/error stats, incremented once
per call-phase report - a passed subtest is a report of its own - while a `<testcase>` element is written once per
test id; a failed or skipped subtest appends a `<failure>`/`<skipped>` child to the parent's element (and to the
matching count). `kriya/tools/test_execution.py::parse_pytest_case_evidence` required `parsed == declared` for all
four counts, so any suite with a passed subtest was JUNIT_COUNTS_INCONSISTENT. `collect()` set COMPLETE regardless of
integrity, and `validate.py::run_tests` reported `success = rc == 0` regardless of the evidence.
JVM writers (Surefire, Gradle) write one element per executed test: MEASURED on 220 real reports from the first
cohort's builds (108 Surefire, 112 Gradle), 0 mismatches between declared counts and elements.

## Fix
1. `test_execution._integrity`: failures/errors/skipped must match exactly; `tests` may exceed the elements only by
   the uncounted passed reports (`surplus_reports`, recorded) for pytest; a deficit, any other mismatch or a repeated
   case id stays inconsistent. JVM documents (`junit_report_integrity`, new) are exact; they must declare `tests`
   and the other counts are compared when declared. Nothing is reconciled from console text.
2. `test_execution.collect`: a document whose integrity fails is not a COMPLETE record (reason = the integrity reason)
   for every runner - completeness and integrity can no longer disagree inside one report.
3. `validate.run_tests`: a process that exited 0 whose structured evidence is present but contradicts itself,
   cannot be read, or is complete and names a failing case, is a typed non-PASS (`reason_code
   TEST_EVIDENCE_INCONSISTENT`, output prefixed). Absent evidence (no report for the runner, no destination, no
   report written, process not run) stays INDETERMINATE under the whole-output authority exactly as before (REG-R1).
Out of scope, recorded: a pytest process that exits 0 and writes no JUnit report at all is still accepted under the
whole-output authority (`test_11b` of REG-R1); tightening that is a separate decision (many fixtures stub the process).

## Verification
- Original symptom re-measured post-fix: T4 copy integrity ok, surplus_reports 134, evidence complete; tiny project
  success true, integrity ok, surplus 3 (`post_fix_measurement.txt`). Prediction held.
- Regression: `tests/test_junit_counts_inconsistent_gate_success_001.py` (22 tests: the real subtest mechanism
  through the production gate, a failing subtest failing its parent with matching counts, the measured cohort
  numbers, consistent zero/nonzero/parameterized/multi-suite documents, every inconsistent shape, JVM exactness, the
  gate verdict on deficit/truncated/missing reports, console text never deciding, a failing process keeping its own
  verdict, JVM collection folding integrity into completeness). `test_reg_r1_pytest_regression_authority.py::test_13`
  fixture changed from a surplus (pytest's legitimate shape, MEASURED) to a deficit.
- Mutations (`mutations.txt`): m1 exact-match rule restored -> 3 failed; m2 gate verdict flip removed -> 3 failed;
  m3 integrity not folded into completeness -> 2 failed; m4 failing case no longer refuses PASS -> 1 failed.
- Adjacent: REG-R1, FS-1A, FS-1C0, acceptance (Python + JVM), validation baseline, polymorphic validation,
  capability adapters, PRD-011, D8, SEC-002, contained Python gate: 271 passed. ruff + pylint: 0 findings.
