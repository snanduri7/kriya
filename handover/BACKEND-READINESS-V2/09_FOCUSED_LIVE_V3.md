# 09 - Focused live validation of this cycle's changes (label -v3a, one run per task, serial, stop on a false success)

Executable: branch `backend/readiness-v2` @ 5531396 (clean tree). Tasks: S4_B, S3_A, T2 (the three cases whose causes were changed). No S4_A live run: the exported candidate is the proof for fix 1.
Evidence root `~/kriya-m1-live/backend-readiness-v2/{primary,cohort-002}/*-v3a`; earlier labels untouched. A green run in which the new path did not execute is NOT evidence for the change.

## Predictions, written before any run
| task | path that must fire | prediction | would count as evidence for the change |
|---|---|---|---|
| S4_B | `structured_plan_normalized.immutable_test_redirects` in decisions.jsonl; plan valid at repair round 0 | IF the Planner again plans an edit of a frozen test (it did in 3 of 3 JsonPath drafts), the plan is valid without a repair round and the run reaches the Developer. Outcome GENUINE or SAFE_FAILURE is not predicted; "nothing written after 2 repairs" must not recur | the redirect record AND no PLAN_EDITS_IMMUTABLE_TEST repair |
| S3_A | `regression.decision` with `blocking:false` and the DNS tests PRE_EXISTING | certain to fire (same environment). Prediction: SAFE_FAILURE remains unless the Developer also edits `StrBuilder`; the number of attempts spent on `regression_test` for DNS tests is 0 (was 3 of 7) | blocking false on the first full-suite gate, no DNS-driven retry |
| T2 | the delegate line in the Planner's request (read from the sealed `model.request` blob; the planning-diagnostics request is cut at ~20,000 chars) | the line is certain to be present. Planner targeting `StringUtil.java` is uncertain (~50%); GENUINE only if `StringUtil.resolve` is fixed completely | the line in the request AND a changed target |
Any false success, authority violation, workspace corruption or leak stops the evaluation.

## Results
(filled after the runs)
