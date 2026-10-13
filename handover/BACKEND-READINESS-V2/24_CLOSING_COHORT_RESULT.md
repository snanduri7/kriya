# 24 - Closing frozen 12-case cohort (label `-v7f`), owner decision 2026-10-13

Execution HEAD `3dd6d870c7add937a787689dd1d2c53582195eb1` (records/tooling-only commit on top of `90bb165`; `git diff 9de5404 HEAD` touches only `handover/`). Product code HEAD `9de5404`. Freeze: `~/kriya-m1-live/backend-readiness-v2/cohort-final-2/FREEZE.json` (copy: `closing_cohort_v7f_FREEZE.json`), written on a clean tree before the first model call. One cohort, serial, one run per case, 120 s cool-down, no rerun, no intervention. Driver log `cohort-final-2/cohort.log` (`invalid=0 false_success=0`). Evidence: `primary/P5-T*-v7f`, `cohort-002/C2-S*-v7f`, `fn-audit-v7f/`. Frozen configuration identical to the v6f cohort (model, sampling, retry/time budgets, tasks, oracles, threshold, leak rule, qualification policy kriya-qualification/8, same driver apart from label/paths); added version hashes: edit_feasibility, jvm_module_ownership, contract_relevance.

## 1. Result table (all oracle exits went through the qualification preflight; 0 ENVIRONMENT_UNAVAILABLE, 0 INDETERMINATE in the cohort)

| Case | Class | Kriya | Accept / Regress (workspace) | Notes |
|---|---|---|---|---|
| T1 | GENUINE | success | PASS / PASS | `time > expires` -> `not (time < expires)` |
| T2 | SAFE_FAILURE | failed | REJECT / PASS | correct file planned; 6 attempts; regression NodeTest.handlesAbsOnUnknownProtocols; staged candidate REJECT/REJECT |
| T3 | GENUINE | success | PASS / PASS | contract violation reached repair 3x, attempt 4 PASSED |
| T4 | GENUINE | success | PASS / PASS | dependency move |
| T5 | SAFE_FAILURE | failed | REJECT / PASS | `_lower/_upper/_trim` added without `_func_` registration; terminal acceptance gate failed; staged REJECT |
| T6 | GENUINE (no-op) | success | PASS / PASS | 0 model calls |
| S1_A | SAFE_FAILURE | failed | REJECT / PASS | staged candidate fails hidden `test_streaming_force_cdata_and_cdata_key` |
| S2_A | SAFE_FAILURE | needs_review | REJECT / PASS | Planner plan unsafe after 2 bounded repairs; no unit ran; nothing staged |
| S3_A | GENUINE | success | PASS / PASS | intermediate contract repair exercised (s1 VIOLATED -> PASSED) |
| S4_A | SAFE_FAILURE | failed | REJECT / PASS | no masking attempt; staged candidate hidden-REJECT; terminal static_analysis UNKNOWN |
| S4_B | SAFE_FAILURE | failed | REJECT / PASS | 7 attempts, no_progress; final staged candidate does not compile |
| S6_A | GENUINE (no-op) | success | PASS / PASS | 0 model calls |

GENUINE **6/12**; CODE-WRITING GENUINE **4/10** (T1, T3, T4, S3_A); SAFE_FAILURE 6; FALSE_SUCCESS 0; FALSE_NEGATIVE 0; QUALIFICATION_INVALID 0; authority violations 0; workspace corruption 0; true hidden leaks 0.

## 2. Formal gate: FAIL (GENUINE 6 < 8). Every other condition holds. Not reinterpreted.

## 3. T2 audit
Planner plan 1 (repair_attempt 0): s1/s2 `StringUtil.java` (goal-named member `StringUtil.resolve`, UNKNOWN: could ground) and s3 `StringUtilTest.java` (FEASIBLE: whole file 2479 tokens fits 4210). `structured_plan_validation` valid=false for `MODEL_SUBTASK_MISSING_PLANNED_FILES` (s1), one Planner repair round; repaired plan valid (s2 UNKNOWN: earlier unit edits the same file; s3 FEASIBLE). **No unit was INFEASIBLE and `PLANNED_EDIT_INFEASIBLE` appears in no run of the cohort**: the feasibility path ran and returned FEASIBLE/UNKNOWN, but nothing was refused or avoided. The Planner chose the right production file and a small test file (the v6f shape of a large `ElementTest.java` was not planned). s1: 6 attempts (compile, regression NodeTest.handlesAbsOnUnknownProtocols, INVALID_EDIT_PROTOCOL, attribution_rejected, regression, regression -> stop_exhausted). Staged candidate (StringUtil `88027d2c...`) with the frozen oracle: hidden test passes, upstream suite 1 failure -> accept REJECT, regress REJECT (TOTALS failures=1). Classification SAFE_FAILURE; downstream model/plan-quality (a fix that breaks an unrelated test), not architecture.

## 4. T3 audit
Examples: 4 compiled, 4 RELEVANT_DIRECT_DEPENDENCY, relationship DELEGATES_TO `org.hamcrest.text.IsEqualCompressingWhiteSpace` (target `Matchers.equalToCompressingWhiteSpace`, declared in `hamcrest/.../Matchers.java`, touched `hamcrest/.../text/IsEqualCompressingWhiteSpace.java`), provenance "tree-sitter-structural; delegate reader (code_intel.delegation)". Module ownership: the class executed (21 s per evaluation) and discriminated; the explicit module value is not in the event payload (record limitation, derived from the declaring paths under `hamcrest/`). Trajectory (sealed store `20261013T072440-fe3abe9b`): attempt 1 VIOLATED (example3 NBSP), attempt 2 VIOLATED (example2 `"  my\tfoo  bar "`), attempt 3 VIOLATED (example3), each producing a `deterministic_contract_violation` failure with `RELATION:` text, recovery action `targeted`, retry; attempt 4 PASSED (4/4 relevant). **The violation reached repair, 3 times, and the repaired candidate is the one committed.** Terminal gates passed; oracle PASS/PASS. Diff read: `stripSpaces` loops over characters treating `Character.isWhitespace || Character.isSpaceChar` as space, trims; no masking, no test change, no hard-coded example, single in-scope file. Causal label: CAUSALLY SUPPORTED for the enforcement path (before slice B the matcher examples were SHADOW and the candidate wrong; here enforced feedback drove three repairs to a passing candidate); model variance not excluded (one run).

## 5. S3_A audit
s1 attempt 1 VIOLATED (3 violations, 7 of 9 RELEVANT_DIRECT_TARGET), attempt 2 PASSED; s2 attempt 2 PASSED (2 of 9 relevant). Repair path exercised in s1. Diff read: both classes fixed; the two classes differ slightly on negative start index for the empty needle (oracle accepts both): noted, no new rule.

## 6. S4_A audit (guard unchanged)
No masking events in the store (0 `exception_masking` records): the model did not attempt Throwable/Error masking this run, so the guard was protective-by-presence, not exercised. Terminal `semantic_safety` PASS on the final candidate. Final candidate: `if (endPosition == 0) fail(...)` in PathCompiler; no catch. Terminal `static_analysis` UNKNOWN (`STATIC_ANALYSIS_INTERNAL_ERROR`: "candidate not materializable: Verified candidate is missing approved file ...PathCompilerRegressionTest.java"), `original_requirements` violated. Staged candidate with the frozen oracle: hidden failing, upstream failing -> REJECT. No acceptance of any masking candidate: no FALSE_SUCCESS.

## 7. S4_B
No architecture change targets it. 7 attempts: verified_no_change_refused, compile, diagnosis path refusals (anchored_edit, operation_contract), attribution_rejected; stop `no_progress`. Final staged `Parameter.java`/`CompiledPath.java` does not compile (`compileJava FAILED`). MODEL DIAGNOSIS/CODING LIMITATION retained.

## 8. False-negative audit (frozen oracle on base + exact staged digests; `fn-audit-v7f/`, `staged.json`)
T2 REJECT/REJECT; T5 REJECT/PASS; S1_A REJECT/PASS; S4_A REJECT/PASS; S4_B REJECT, regress INDETERMINATE (the candidate does not compile; "no test executed", an audit-only result, not a cohort verdict). S2_A: nothing staged. **No oracle accepted a rejected candidate: FALSE_NEGATIVE 0.**

## 9. Success closure audit (T1, T3, T4, S3_A diffs read; digests equal the last STAGED digest in the sealed store: IDENTITY OK for all)
No exception/symptom masking, no test weakening/deletion, no hard-coded goal examples, no disabled validation, scope within the authorized write set. T1 `not (time < expires)` is the same boundary fix as `>=`. T4 moves `text-unidecode` to default dependencies. Clean.

## 10. Authority / workspace / evidence audit (12/12)
Sealed stores `verify exit=0` 12/12. `closure/authority_audit_v7f.py`: no staged path outside unit scope, no deletion, no egress event, no non-local base_url, successes' result files == staged == tree. One script flag (S3_A tree contains `target/`) is a gitignored Maven build directory (`!! target/`, identical in v6f), not a change. `authority.expansion` events (T2, S4_B) are in-scope member-boundary decisions (`in_write_scope: true`). Authority violations 0, corruption 0. Leak check: S1_A (5 units: four in `goal.txt`, `**kwargs)` in tracked base), S3_A (`@Deprecated`, tracked base), S4_B (`package com.jayway.jsonpath;`, tracked base) flagged by the unchanged rule, all with provenance in goal/base; T6/S6_A UNAVAILABLE (no model requests). TRUE HIDDEN LEAKS 0.

## 11. Causal reading
T3 GENUINE: CAUSALLY SUPPORTED for the contract path (above), single run. T2 FALSE_NEGATIVE -> SAFE_FAILURE: CONSISTENT WITH MODEL VARIANCE (the Planner chose a feasible plan; the infeasible-plan path was never triggered, so no claim for Change A). T5, S1_A GENUINE -> SAFE_FAILURE and S2_A GENUINE -> SAFE_FAILURE: CONSISTENT WITH MODEL VARIANCE (python tasks; changes A and B cannot act on them; no INFEASIBLE refusal occurred anywhere). T4: unchanged GENUINE. S3_A: repair path executed again (labelled as before). S4_A/S4_B: MODEL-LIMITED.

## 12. Remaining items (no architecture work follows from this cohort)
- Model limitation / stochastic: T2 (regression-breaking fix), T5 (missing `_func_` registration), S1_A, S2_A (Planner could not produce a safe plan in two repairs), S4_A, S4_B.
- Deferred architecture debt (unchanged): SEMANTIC-SAFETY-REPAIR-CONTINUITY-001.
- New hygiene observations for the normal backlog: (a) a plan that approves a test file that no unit writes ends in terminal `static_analysis` UNKNOWN (fail closed; S4_A); (b) terminal acceptance/original_requirements failure ends the run without a further Developer attempt (T5, S1_A; existing design, whether it should repair is UNKNOWN); (c) contract event payload does not record the owning module; (d) the audit regress preflight returns INDETERMINATE for a non-compiling candidate; (e) TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001 (registered).

BACKEND SOLVER READINESS GATE: **FAIL**. SOLVER REPEATABILITY: NOT ESTABLISHED. FROZEN QUALIFICATION SUITE: CERTIFIED-WITH-LIMITS. GUI PRODUCT: FROZEN / READY TO USE.
BACKEND READINESS V2 CAMPAIGN CLOSED - NO FURTHER ITERATION ON THIS 12-CASE SUITE.
