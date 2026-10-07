# REGRESSION-UNATTRIBUTED-PROSE-001: the unattributed-regression stop claimed an aggregate change the evidence did not show

## Status
**FIXED** (2026-10-07) on `fix/backend-reliability-closure` from main f757e6b. Severity P3 (reporting: the decision was
correct and fail-closed; its sentence was not). Registry row CLOSED. Discovered by the BACKEND-READINESS-001 blind
cohort (T1, T5); fixed in BACKEND-RELIABILITY-CLOSURE-002 (evidence
`~/kriya-m1-live/backend-reliability-closure-002/defects/regression-attribution/`).

## Observation (MEASURED, cohort T1/T5)
PRE and POST pytest evidence both incomplete, whole-output fingerprints equal (level1 PRE_EXISTING_FAILURE), POST suite
failing: the stop said "the full-regression suite's aggregate outcome changed relative to the captured PRE-mutation
baseline (level1=PRE_EXISTING_FAILURE)".

## Producer (TRACED)
`kriya/workflow/workflow.py` built one fixed message for every `_full_regression_unattributed` stop; the only blocking
reason in that shape is `pytest_evidence_incomplete:<status>` (`validation_baseline._pytest_delta`), not a level-1 change.
No path in Kriya derives regression PASS from prose: `_regression_should_block` is assigned only from the gate's
`success` and the structured delta's `blocking` (now pinned by a structural test).

## Fix
`validation_baseline.regression_unattributed_diagnosis(delta)` returns (attribution state, message) from the delta's
own blocking reasons: `evidence_incomplete` (states the incomplete sides and whether the fingerprints are equal or
different), `aggregate_delta` (the previous wording, now only when level 1 blocks), `per_test_unattributable` (names the
entries). The workflow uses it and records `attribution`, `blocking_reasons` and `pytest_evidence_status` in the
failure diagnostics. The `REGRESSION_UNATTRIBUTED:` prefix, the failure type and the stop decision are unchanged.

## Verification
- `tests/test_regression_unattributed_prose_001.py` (15): the three attribution shapes, the cohort shape through the
  real comparator, structured failure beating console text, console success without evidence not verified, mixed
  partial evidence bounded, a real pytest run deciding from its report, the structural tripwire (regression decisions
  read only `<gate>["success"]` / `<delta>.blocking`; no membership test, string constant or call), its negative
  control, the regression modules importing no model/agent, the stop carrying its structured diagnostics.
- Mutations (`mutations.txt`): m1 incomplete case reports an aggregate change -> 3 failed; m2 planted prose decision
  in workflow.py -> 1 failed (the first tripwire version let it survive and was strengthened); m3 diagnostics dropped
  -> 1 failed.
- Adjacent: LR-R1-M1 reason codes, failure reporting, validation baseline, REG-R1/R2 reproducers, P2 attribution,
  workflow regression tests: all passed. ruff + pylint 0.
