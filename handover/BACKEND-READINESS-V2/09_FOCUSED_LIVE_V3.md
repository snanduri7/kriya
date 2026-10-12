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
Executable: c6b845a (product tree identical to 5531396; the "@ 5531396" wording above refers to the same product tree). One run per task, serial, label -v3a, evidence `~/kriya-m1-live/backend-readiness-v2/{cohort-002/C2-S4_B-v3a,cohort-002/C2-S3_A-v3a,primary/P5-T2-v3a}`.
All three: Kriya reported failure, the external oracle rejected the tree (accept_exit=1). SAFE_FAILURE x3; no false success, authority 0, workspace restored. Leak flags on S4_B ("package com.jayway.jsonpath;") and S3_A ("@Deprecated") are R2-F7 false positives (MEASURED: both units occur in tracked frozen-base files; T2 CLEAN).

| Case | Predicted | MEASURED | New code executed? |
|---|---|---|---|
| S4_B | redirect fires if the Planner edits the frozen test | Plan valid on the first draft (1 structured_plan_validation, no repair, `immutable_test_redirects` not recorded). Developer then failed: 12 Gradle compile failures, anchored-edit ANCHOR_NOT_IN_FILE on FunctionPathToken.java, attempts 8-9 failed. | NO. The Planner did not repeat the frozen-test edit this time (2 of 3 JsonPath plans did, 1 did not), so the redirect was not exercised live; it stays proven only by replay + deterministic tests. |
| S3_A | first full-suite gate non-blocking; stays SAFE_FAILURE | CONFIRMED: suite_attribution logged "every failure is classified PRE_EXISTING_FAILURE ... not blocking" for the verification-unit, full-regression (x2) and suite-preservation gates. Run still failed on behaviour: oracle 4/1891 failures. | YES (fingerprint fix causally changed the gate from blocking to non-blocking). It did not change the outcome: the Developer's candidate still fails the behaviour. |
| T2 | delegate fact present; target change ~50% | The Planner again targeted Node.java (not StringUtil). Developer's own analysis says "absUrl delegates directly to StringUtil.resolve, which doesn't properly handle query-only..." yet it patched Node.absUrl. Stopped CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE on a test file; oracle: NodeTest.absHandlesRelativeQuery + StringUtilTest.resolvesRelativeUrls fail. | Delegate-fact line NOT found in the log (planner request is only in sealed blobs; not verified there) - treated as unproven. Target did not change. |

Causal improvement of a previously persistent failure: S3_A gate behaviour only (not outcome). S4_B/T2: none.
