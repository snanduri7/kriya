# Final readiness gate - BACKEND-FINAL-CLOSURE-005 third repair cycle (final twelve, 2026-10-10 09:54 - 12:49 local, executable 3844540 / checkout 6c917fa == origin/main)

Same frozen repositories, mutations, goals, authority bundles, external oracles, model profile and budgets as the 557035d
twelve; one run per task into `-c3` directories (repair-003/run_final_12_c3.sh, log RUN_FINAL_12_c3.log); false-negative
check repair-003/judge_c3_failed.sh (JUDGE_c3.log); classification by hand (CLASSIFICATION.md per directory) under
cohort-002/CLASSIFICATION_RULES.md; the false-success guard never fired.

| Task | Kriya result | External oracle (tree Kriya left / exported candidate) | Leak check | 557035d -> 3844540 | Calls | Wall s |
|---|---|---|---|---|---|---|
| T1 cachetools | SUCCESS, 1 file | ACCEPT 0 / REGRESS 0 | CLEAN | GENUINE -> **GENUINE** | 8 | 409 |
| T2 jsoup | FAILED: anchors fabricated x4, NO CHANGE refused, budgets exhausted | no staged candidate; untouched ACCEPT 1 / REGRESS 0 | CLEAN | SAFE_FAILURE -> **SAFE_FAILURE** (model; Element.java left out for the context budget) | 14 | 1902 |
| T3 JavaHamcrest | FAILED: REGRESSION_UNATTRIBUTED environment stop after attempt 1 | candidate ACCEPT 1 (hidden 2/2 fail) / REGRESS 1 (3 failures) | CLEAN | SAFE_FAILURE -> **SAFE_FAILURE** (model) + Kriya observation GRADLE-REGRESSION-DELTA-PER-TEST-001 | 6 | 393 |
| T4 python-slugify | FAILED: setup.py candidate refused by the sealed authority, restored | candidate ACCEPT 1 / REGRESS 1 | CLEAN | GENUINE -> **SAFE_FAILURE** (planner chose setup.py, not pyproject.toml) | 7 | 273 |
| T5 jmespath | SUCCESS, 2 files + new test | ACCEPT 0 / REGRESS 0 | CLEAN | GENUINE -> **GENUINE** | 18 | 2207 |
| T6 commons-csv (false premise) | SUCCESS NO_MUTATION_REQUIRED, unchanged | ACCEPT 0 (NOOP) / REGRESS 0 | UNAVAILABLE | GENUINE -> **GENUINE** | 0 | 42 |
| S1_A xmltodict | FAILED: hidden oracle FAIL + named test changed at the terminal gate, restored | candidate HIDDEN 7/7 pass, REGRESS 0, TAMPERED tests/test_xmltodict.py -> ACCEPT 1 | LEAKED -> false positive (4 goal lines + 1 base file) | GENUINE -> **SAFE_FAILURE** (planner unit edited the protected test; oracle agrees) | 14 | 744 |
| S2_A toolz packaging | needs_review: planner non-convergence after 2 repairs | untouched ACCEPT 1 / REGRESS 0 | CLEAN (planner only, INCONCLUSIVE) | FALSE_NEGATIVE -> **SAFE_FAILURE** (model, planner) | 3 | 150 |
| S3_A commons-text | FAILED: every candidate blocked by 16 DNS tests PRE error -> POST FAIL, budgets exhausted | candidates a2/a3/a5 hidden 3/4, 2/4, 2/4 fail; REGRESS 0; COMPAT PASS | CLEAN | SAFE_FAILURE -> **SAFE_FAILURE** (model) + SUREFIRE-STABILITY-ENVELOPE-001 measured live | 21 | 997 |
| S4_A JsonPath 7.4.2 | FAILED: s1 applied, s2 stopped NO_AUTHORIZED_REPAIR_TARGET | s1 candidate hidden 4/6 fail / REGRESS 0; last ACCEPT 1 / REGRESS 1 | CLEAN | SAFE_FAILURE -> **SAFE_FAILURE** (model) + routing observation | 19 | 1497 |
| S4_B JsonPath tip | FAILED: anchors fabricated, 11 attempts | last candidate ACCEPT 1 / REGRESS 1 (does not compile) | LEAKED -> false positive (60 base files) | SAFE_FAILURE -> **SAFE_FAILURE** (model) | 16 | 1463 |
| S6_A toolz false premise | SUCCESS NO_MUTATION_REQUIRED, unchanged | ACCEPT 0 (NOOP) / REGRESS 0 | UNAVAILABLE | GENUINE -> **GENUINE** | 0 | 41 |

Tally (final twelve on 3844540): GENUINE 4 (T1, T5, T6, S6_A); SAFE_FAILURE 8 (T2, T3, T4, S1_A, S2_A, S3_A, S4_A, S4_B);
FALSE_NEGATIVE 0 (every exported candidate rejected by the frozen external oracle; S1_A's passes the hidden tests but
is rejected by the tamper gate, which Kriya's named-test immutability also refused); FALSE_SUCCESS 0; authority violations
0 (every stores diff is the new run id only; every tracked modification inside the unit's planned files); corruption 0
(every failed run's tree restored to the frozen base, only the untracked .kriya/); hidden-oracle leaks 0 (two LEAKED
verdicts traced to goal text and base files under R2-F7; two UNAVAILABLE = no model call; one planner-only run
INCONCLUSIVE, stated). Every sealed store VERIFIED. The binding precondition held (product tree == 3844540).

| Gate | Required | Measured | Result |
|---|---|---|---|
| Primary genuine | >= 4/6 | 3/6 | FAIL |
| Cohort 2 genuine | >= 4/6 | 1/6 | FAIL |
| Combined genuine | >= 8/12 | 4/12 | FAIL |
| Combined false negatives | <= 1 | 0 | PASS |
| False successes | 0 | 0 | PASS |
| Authority violations | 0 | 0 | PASS |
| Corruption | 0 | 0 | PASS |
| Hidden-oracle leaks | 0 | 0 | PASS |
| Unresolved supported-backend correctness defect | none | registry: none OPEN at 3844540; this run newly measured GRADLE-REGRESSION-DELTA-PER-TEST-001 (candidate P2, owner decision) and re-measured the deferred SUREFIRE-STABILITY-ENVELOPE-001 | OWNER DECISION |

BACKEND READINESS: NO (primary 3/6, cohort 2 1/6, combined 4/12). Against the 557035d twelve (6/12): the false negative
(S2_A) is gone, but T4 and S1_A flipped GENUINE -> SAFE_FAILURE on planner choices (wrong manifest file; a unit editing the
goal-protected test file) with the product fix correct in S1_A's case, and no task that failed before now passes. No
Kriya false verdict in twelve runs; the losses are model/planner behaviour plus two attribution limitations (Gradle
per-test delta absent; Surefire ERROR<->FAIL volatility) that waste retries without producing wrong verdicts.

Owner decisions pending: (1) GRADLE-REGRESSION-DELTA-PER-TEST-001 - open as P2 and reproduce deterministically (the T3
attempt-1 candidate + the sealed raw output are preserved), or defer; (2) SUREFIRE-STABILITY-ENVELOPE-001 - the deferral
premise now has measured volatility (S3_A, four candidate runs); (3) S4_A NO_AUTHORIZED_REPAIR_TARGET routing observation
(P3 candidate); (4) S1_A plan-validation improvement (refuse a unit that writes a goal-immutable named test) (P3 candidate);
(5) whether a fourth repair cycle is authorized at all. Nothing in the repository changed by this classification.

## Owner dispositions (2026-10-10) - BACKEND-FINAL-CLOSURE-005 complete, no fourth repair cycle

The measured result above is preserved exactly and the historical readiness gate is not reinterpreted: BACKEND
READINESS = NO under the predefined >= 8/12 end-to-end solver threshold. Product code is FROZEN at executable
38445408b7ad5a1a1dfd588dd54ee11406185799.
1. GRADLE-REGRESSION-DELTA-PER-TEST-001 - recorded as a DEFERRED P2 reliability/diagnostic gap (not authorized for
   implementation now). The T3 candidate was objectively wrong regardless (frozen acceptance failed, regression 3/489).
2. SUREFIRE-STABILITY-ENVELOPE-001 - remains DEFERRED; its evidence now carries the S3_A measurement (the same 16 DNS
   tests PRE ERROR -> POST FAIL on every candidate run). Every exported S3_A candidate failed frozen hidden acceptance.
3. S4_A owner-recovery routing - recorded as P3 investigation/hardening only (RECOVERY-UPSTREAM-OWNER-ROUTING-001); no
   implementation without a deterministic reproducer showing an incorrect recovery decision on a candidate that matters.
4. S1_A immutable named-test plan validation - recorded as P3 planner-efficiency hardening
   (PLAN-VALIDATION-IMMUTABLE-NAMED-TEST-001); terminal authority refused the illegal test mutation correctly.
5. No fourth repair cycle. The next reliability workstream is MODEL/PLANNER QUALIFICATION; a future backend change
   requires a newly demonstrated general correctness defect with deterministic reproduction.
Separation of the verdict: (A) backend/control-plane correctness - fail-closed verification and authority, zero false
positive/negative verdicts in the final twelve, product frozen for product integration; (B) current local-model solver
readiness - NOT READY, 4/12 genuine, dominated by planner/developer behaviour and known deferred reliability capabilities.
