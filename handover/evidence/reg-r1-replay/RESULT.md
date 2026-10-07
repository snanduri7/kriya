# REG-R1 deterministic replay of the preserved qwen3.8 candidate (no model, no generation)

Live gate shape and runtime of run 20261006T231821-fb31caad, certified code 6d242e7: full-suite baseline on the frozen
Graphify base 67f99bd, POST with staged engine.py e47eacba..., production `classify_with_baseline_stability` with real
untouched-baseline replays (OCI). 211 s.

| | a049c02 (whole output) | REG-R1 6d242e7 (per test) |
|---|---|---|
| Authority | whole output | pytest_per_test (both reports COMPLETE, 5370 cases) |
| Verdict | blocking: `level1:CHANGED_FAILURE` | blocking: 1 x STABILITY_UNRESOLVED (unattributed) |
| Pre-existing failures excused | n/a | 216 / 217 |
| `test_built_at_commit_comes_from_the_target_repo` | - | body VOLATILE on the untouched baseline (env repr) -> PRE_EXISTING |
| `test_ts_normalizer_scales_linearly_on_large_files` | - | STABILITY_UNRESOLVED: BASELINE_REPLAY_OUTCOME_DIFFERS |

**DOES THE PRESERVED 3/5 CANDIDATE NOW SURVIVE THE ERRONEOUS REGRESSION STOP? NO.**

MEASURED: the timing test FAILS in every full-suite run of the untouched baseline (6/6 observations: the live baseline,
the live POST and 4 REG-R1 reproduction runs, all with a different ratio) but PASSES when replayed alone (2/2 isolated
replays, identical). Its outcome depends on execution context (suite load), so the isolated replays cannot observe the
disputed message/body; 6d242e7 treats that as INDETERMINATE (fail closed), and the workflow stops unattributed
(REGRESSION_UNATTRIBUTED) - no blame, no false success, but still the premature stop for this case. The deterministic
workflow therefore does not proceed past subtask s1.

Owner decision needed (the specification does not settle it): how a baseline whose disputed test changes OUTCOME between
the original full-suite observation and the isolated replays is treated -
(a) keep INDETERMINATE (current, strictest);
(b) treat the disputed fields as VOLATILE (they demonstrably do not reproduce on the untouched baseline) - resolves this
    case, but would also excuse a candidate's change to the failure text of a context-flaky test;
(c) take the replay observations in the original execution context (re-run the untouched baseline's FULL suite twice,
    once per run, cached) - resolves this case from the same context as the original observation (6/6 full-suite runs
    fail with a varying message -> message VOLATILE), keeps (a)'s strictness for genuinely context-free tests; costs two
    full-suite runs per run when any dispute exists.
