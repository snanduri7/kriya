# REG-R2 replay of run 20261007T093122-df65c5d3 on the integrated code (be3cbb2)

Method (milestone_replay.py): every sealed `regression.decision` of the run is re-decided by the integrated
`pytest_stability.classify_with_baseline_stability` (stability policy 3). Its inputs are the run's own sealed
`gate.result` observations — raw pytest stdout/stderr/JUnit rebuilt through Kriya's production collector
(`test_execution.prepare/observe/collect`) — and are asserted equal to what the live decision sealed: baseline evidence
== sealed `baseline_cases`, POST evidence == sealed `post_cases`, and each disputed test's observation identities ==
the sealed stability observations. The replays are served from the sealed same-context replay observations.

| Decision | Subtask | baseline / POST / replays (M1 seq) | Live verdict | Replayed verdict (be3cbb2) | level2 identical to live |
|---|---|---|---|---|---|
| seq 72 | s1 | 29 / 69 / [70, 71] | non-blocking | non-blocking — 216 PRE_EXISTING_FAILURE + 1 **FLAKY_PREEXISTING** (`tests/test_ts_import_type_arguments.py::test_ts_normalizer_scales_linearly_on_large_files`; flake-rate regression NOT_ASSESSED) | YES |
| seq 138 | s2 | 69 / 135 / [136, 137] | non-blocking | non-blocking — 216 PRE_EXISTING_FAILURE + 1 **RESOLVED_FAILURE** (same timing test passed on the candidate) | YES |

Notes:
- s2's baseline is s1's applied-candidate full-suite run (seq 69), the run-level prior-full-suite reuse; the faithfulness
  assertions held for it.
- The s1 decision is the exact shape that rejected the correct POST-REG-R1 Arm-A candidate under REG-R1 (baseline
  FAIL, candidate FAIL/AssertionError, same-context replays FAIL then PASS); REG-R2 classifies it FLAKY_PREEXISTING.

Raw output: milestone_replay.json.
