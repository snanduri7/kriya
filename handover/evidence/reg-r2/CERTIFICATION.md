# REG-R2 — flaky baseline attribution authority: certification of 56ae8d3

Implementation **56ae8d3** (executable). This commit is evidence only. OBS-4 / REG-R1 stays FIXED (5c55630 + 6d242e7,
history not rewritten); REG-R2 is a separate policy correction.

## Root cause (CONFIRMED)

Observation (MEASURED, sealed M1 evidence of POST-REG-R1 Arm A, run 20261007T071630-e21ca9b2, Kriya 69465d3): the
pre-existing test `test_ts_normalizer_scales_linearly_on_large_files` was FAIL / PASS / FAIL across the untouched
baseline (seq 23) and its two same-context replays (seq 94, 95); the correct candidate's run (seq 93) was FAIL /
AssertionError with another message.

Producer (TRACED, 69465d3): `pytest_stability.classify_baseline_observations` set BASELINE_OUTCOME_UNSTABLE for any
outcome/type variation and made message/body UNRESOLVED; `validation_baseline.classify_pytest_per_test_delta` then
returned STABILITY_UNRESOLVED (blocking, never attributed) → REGRESSION_UNATTRIBUTED. Baseline outcome instability was
treated as universal blocking uncertainty, rejecting every candidate. A second, related property: a blocking state
change was judged against the original baseline observation only (the replays were never consulted for it).

Discriminating check: the sealed observations re-decided by 69465d3 reproduce the live verdict exactly (below); the
generic real-pipeline reproducer `tests/test_reg_r2_workflow_reproducer.py` (a pre-existing test passing on exactly the
third suite execution) fails on 69465d3 with `regression_unattributed`.

## Design (owner's modified Option B)

Execution state = (outcome, exception type). Envelope S = the states of every untouched-baseline observation of the
verification context (original + replays, none privileged), with per-state message/body STABLE (seen 2+ times,
identical), VOLATILE, or STABILITY_NOT_ESTABLISHED (seen once). Candidate state ∉ S → block. ∈ S → only a STABLE
same-state field the candidate changed blocks (CHANGED_FAILURE); otherwise FLAKY_PREEXISTING (baseline showed 2+
states; non-blocking; flake-rate regression NOT_ASSESSED, recorded per test) or PRE_EXISTING_FAILURE. Never enveloped:
missing tests, new failing tests, incomplete/invalid evidence; no envelope (absent from a replay, failed/incomplete
replay, revision drift) keeps the provisional verdict. Any blocking difference of an existing test requests the
envelope (cost: at most the two same-context full-suite replays per context, cached and checkpointed as before);
a non-blocking FAIL→PASS never triggers replays. Stability policy 3 (a REG-R1 v2 cache entry is another context). No
global normalization, no test identities hard-coded.

## Predicted effect → measured

Prediction: the sealed Arm-A evidence re-decided by the fix gives the timing test FLAKY_PREEXISTING and a non-blocking
gate, every other test unchanged (216 PRE_EXISTING).

| Original symptom, re-measured | 69465d3 (pre-fix) | 56ae8d3 (installed build, venv-postreg2) |
|---|---|---|
| Frozen Arm-A evidence re-decided (`frozen_replay_*.json`; rebuilt through the production collector and asserted equal to the sealed baseline_cases / post_cases / replay observations) | blocking; 216 PRE_EXISTING + 1 STABILITY_UNRESOLVED (= the live sealed verdict) | **not blocking; 216 PRE_EXISTING + 1 FLAKY_PREEXISTING** |
| Timing-test envelope | — | FAIL/AssertionError ×2 (message, body VOLATILE) + PASS ×1 |
| Real-pipeline reproducer (`before_reproducer_69465d3.txt`, `after_reproducer_56ae8d3.txt`) | FAILED: regression_unattributed | passed |

Exact candidate bytes (engine.py 1adbc4ba…, from the sealed blob, digest-checked) on a fresh clone of 67f99bd
(`arm_a_candidate_score.json`): **external 5/5** (A–E PASS), **reviewed acceptance 5/5**, **regression 76/76**.

**ARM-A FROZEN CORRECT CANDIDATE REPLAY: PASS** (REG-R2 decision for the timing test FLAKY_PREEXISTING; regression
gate PASS). Not run: a fresh re-execution of the candidate's regression gate — fresh runs cannot deterministically
reproduce the load-dependent flip (13/13 earlier no-model full-suite runs failed it), so they would test a different
input; the deterministic replay is the sealed one above.

## Verification

| Gate | Result | Evidence |
|---|---|---|
| Focused (REG-R2 controls 01–15 + extras, REG-R2 reproducer, REG-R1, validation baseline, Surefire) | 141/141 | — |
| Adjacent (validation baseline, regression/P2 attribution, Surefire, PRD-024, FS-1*, B2/B3, checkpoint/resume, LR-R1-M1 incl. P1/P4/P5, capability adapters, file integrity, REG-R1/R2) | 1279/1279 | `adjacent.txt` |
| Full suite (`-n 8 --dist loadgroup`, worktree = 56ae8d3 content) | 9292 passed / 0 failed | `full_suite.txt.gz` |
| Mutation (owner's 10 + 11 REG-R2 + 20 carried REG-R1) | 41/41 killed, tree restored | `reg_r2_mutants.py`, `mutants_run1.*` |
| ruff / pylint | 0 / 0 | — |

REG-R1 tests that encoded the superseded rule were rewritten to the REG-R2 rule (c04/c05 consolidated into
`test_c04_c05_superseded_by_reg_r2`; test_06 no longer asserts "no replay" — a type change is now judged against the
envelope); every other REG-R1 assertion is kept, reading the envelope API.

## Own defect found and fixed (reported separately)

`evaluate_postreg_candidates.py` (POST-REG-R1 scorer, outside the repository) parsed per-site results with
`dict(re.findall(...))` keyed by PASS/FAIL, so only the last site survived (`{'E': 'PASS'}` for a 5/5 candidate —
MEASURED in `postreg/run-a/candidates/trajectory.json`). Scores and the 5/5 verdict were unaffected (they come from the
evaluator's summary line). Fixed in the POST-REG-R2 copy `evaluate_postreg2_candidates.py` (verified on the same output:
A–E PASS); the historical POST-REG-R1 evidence is left as is.

## Status

REG-R2: FIXED (CLOSED in handover/BACKLOG_REGISTRY.csv). OBS-4: FIXED. Fresh paired comparison: see
`handover/evidence/postreg2/`.
