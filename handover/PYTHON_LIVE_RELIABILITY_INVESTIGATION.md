# Python live reliability investigation (opened 2026-10-03)

Registry: PYTHON-LIVE-RELIABILITY-001. Causality relative to CAGC: **UNKNOWN / not attributable from the historical
data.** Python live reliability was 1/16 valid runs (A 1/8, B 0/8). The Python requests carried no capability
guidance in either arm, but Arm B also changed the base role prompts, so "no guidance rendered" does not mean
"same prompt"; the data do not establish whether the difference is attributable to CAGC, the base-prompt changes,
ordinary model behaviour or another cause. PROTOCOL_v2 classifies failures prospectively. *(Corrected 2026-10-03;
the first version said "not a CAGC finding".)* No retry, early-stop, fallback or localization policy changes before
the CAGC v2 experiment: the findings below are classifications, not implementation authorization.

Valid Python sample of matrix-40: maxsplit, ichunked, zipb, invalidurl x (A r1, B r2, B r3, A r4) = 16 runs
(python-symbol-one excluded: wrong goal for its workspace). Kriya SUCCESS **1/16** (ichunked A r4).
Source: each run's `traces.json` run events (`~/kriya-cagc-ab/{A,B}/evidence/<task>.<rep>/`), read-only.

## First incorrect state per run (MEASURED)

| task | run | attempt sequence (failure types) | terminal | first incorrect state |
|---|---|---|---|---|
| maxsplit | A r1 | regression_test, regression_test (fallback, full set), no_op_edit, goal_spec_compliance, no_op_edit, no_op_edit, fallback_incompatible | fallback_model_incompatible | DEVELOPER_OUTPUT (candidate broke SplitAfter/Before/When::test_max_split; replay-confirmed) |
| maxsplit | B r2 | regression_test x3, operation_contract, fallback_incompatible | fallback_model_incompatible | DEVELOPER_OUTPUT (same confirmed regression) |
| maxsplit | B r3 | verified_no_change_refused, no_op_edit x3, fallback_incompatible | fallback_model_incompatible | DEVELOPER_OUTPUT (no change proposed) |
| maxsplit | A r4 | diagnosis_mismatch, test, test (fallback), test, operation_contract, fallback_incompatible | fallback_model_incompatible | DEVELOPER_OUTPUT (diagnosis mismatch) |
| ichunked | A r1 | regression_unattributed | regression_unattributed | VERIFICATION: level-1 CHANGED_FAILURE, level 2 = test_negative RESOLVED, test_zero_nonempty PRE_EXISTING |
| ichunked | B r2 | regression_unattributed | regression_unattributed | VERIFICATION (identical delta) |
| ichunked | B r3 | regression_unattributed | regression_unattributed | VERIFICATION (identical delta) |
| ichunked | A r4 | diagnosis_mismatch, then pass (both target tests RESOLVED) | SUCCESS | - |
| zipb | A r1 | regression_unattributed | regression_unattributed | VERIFICATION: level-1 CHANGED_FAILURE, level 2 {} |
| zipb | B r2 | diagnosis_mismatch, regression_unattributed | regression_unattributed | DEVELOPER_OUTPUT, then VERIFICATION stop |
| zipb | B r3 | diagnosis_mismatch, regression_unattributed | regression_unattributed | DEVELOPER_OUTPUT, then VERIFICATION stop |
| zipb | A r4 | s1: diagnosis_mismatch, goal_spec_compliance, pass; s2: test x4 | no_progress | DEVELOPER_OUTPUT (s2) |
| invalidurl | A r1 | anchored_edit, no_progress_retry x2, fallback_incompatible | fallback_model_incompatible | PLANNING (gold `_urlparse.py` never planned) |
| invalidurl | B r2 | same | fallback_model_incompatible | PLANNING |
| invalidurl | B r3 | context_edit_protocol_unsatisfiable | context_edit_protocol_unsatisfiable | PLANNING, then CONTEXT (starvation shape; capacity after the P1 fix) |
| invalidurl | A r4 | same as A r1 | fallback_model_incompatible | PLANNING |

## The five questions

**1. Does `regression_unattributed` terminate recovery too early?** Likely yes - INFERRED, not CONFIRMED.
TRACED: `validation_baseline.classify_baseline_delta` makes any level-1 classification in
`TERMINAL_BLOCKING_CLASSIFICATIONS` (here `CHANGED_FAILURE`) blocking on its own; `workflow.py` then replays the
ambiguous per-test entries, and when nothing is confirmed candidate-caused it raises `regression_unattributed`
as an environment stop (VAL-001 G1-DEVINV2) - no Developer retry. MEASURED: 6/16 runs ended this way, 4 of them
at attempt 1; in ichunked the candidate had *resolved* one target test and the only remaining failure was the
other, pre-existing target test - i.e. partial progress on the goal itself. ichunked A r4 reached the same partial
state via an ordinary targeted retry and passed. In zipb level 2 is empty while level 1 changed: consistent with
the pre-existing target tests still failing with a *different* failure output (the level-1 fingerprint covers
the output), which is exactly what a repair attempt produces - not yet checked against the stored outputs.
Discriminating check before any change: a deterministic reproducer (pre-existing failing target test; candidate
changes how it fails, or fixes one of two) through the real regression gate; expected today: terminal
`regression_unattributed` with no retry.

**2. Does the retrying Developer receive the complete relevant failing-test evidence?** UNKNOWN. The traces store
only the subtask's first-attempt task prompt (`prompt_rendered`), not retry prompts. TRACED: on a confirmed
regression the retry gets `render_blocking_regression_evidence` (the confirmed test ids' blocks only). To answer:
persist each Developer request's section composition with the retry evidence ids, or reconstruct one retry
deterministically from a scripted replay of a preserved run.

**3. Does the retry contain materially new causal information?** UNKNOWN for content (same reason).
MEASURED signal: `retry.progress_vector` reported PROGRESS for every retry (several dimensions changed each
time) while the Developer answered no_op_edit three times in a row in maxsplit A r1 and B r3 - the progress
vector's dimensions changed, the Developer's output did not.

**4. Is fallback exhaustion fundamentally a model-qualification coverage problem?** Yes - MEASURED. All 7
`fallback_model_incompatible` stops carry the same reason: the only fallback,
`qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a`, has capability profile `unverified_conservative_default` (whole files
only, edit protocol `full_file`), and the targeted retry may only patch (D1: no complete current source shown).
The fallback was usable for full-set retries and rejected exactly when a patch was required. Remedy is
qualification/profile coverage (a measured edit-protocol profile for the fallback, or a patch-capable qualified
fallback), not retry policy.

**5. Are Python candidates targeting the correct file/member before Developer generation?** Mostly - MEASURED.
maxsplit (split_before/after/when or split_at), ichunked (ichunked, chunked) and zipb (zip_broadcast): the
localization decision and the T0 members are the goal's own functions in all 12 runs. invalidurl: 0/4 - the
Planner and localization target `_exceptions.InvalidURL`, never `_urlparse.urlparse` (the gold).

## Next steps (no retry-policy change before a deterministic reproducer shows a harness defect)
1. Reproducer for question 1 through `classify_baseline_delta` + the workflow's regression gate (scripted
   Developer), on the two measured shapes (one of two target tests resolved; same failing ids, different output).
2. Capture per-request retry evidence (questions 2-3) in the run events, content-free (ids, section tokens).
3. Fallback capability coverage (question 4): measure the fallback's edit protocol under qualification, or
   configure a patch-capable fallback; a config/qualification change, recorded through PRD-014.
4. invalidurl localization (question 5) is the same planning gap recorded in CAGC-0 CORRECTIONS.md section 2.
