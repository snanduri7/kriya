# Wave 5 / Gate E reconciliation - DRAFT for ledger section 33 (2026-11-01). NOT yet written to any tracked file.

## Revision binding
Kriya HEAD dd9ba00282ee54492e9430bba91edbb402e0a952, tree 45533c31e77c74cf66023c8e07ec2e765afcca00, status 0 lines before AND after, refs/kriya/verified 7eddc5d6 unmoved, nothing pushed/tagged. Python 3.14.6, pytest 9.1.1.

## A. Graphify product run (live, current revision) - preserved truthfully
Run 20261101T174848-978ebaa3, config graphify-qwen3coder-native-production.yaml (076e48ac...), qwen3-coder v5 native (Wave-4 records byte-identical). Outcome: **FAILURE / quality_gates_exhausted**, NOT_COMMITTED, workspace unchanged, evaluator 2/5 = PRE, regression 76 passed, evidence VERIFIED + sealed (481 records). Not rerun, not edited. Classification: MODEL CAPABILITY primary (see OWNERSHIP_TRACE.md).

## B. Bounded ownership/recovery trace (OWNERSHIP_TRACE.md)
- H1 "Kriya had sufficient deterministic evidence and wrongly failed to reopen s1/engine.py": **DISPROVED as a contract defect** (TRACED).
- Cross-unit gap O1 and test-weakening gap O2: **CONFIRMED code behaviour, outside every signed PRD; not a contract violation.** Owner decision.
- Retraction: the earlier claim "the model never weakened the test" was wrong (appended to GATE_E_RESULT.md).

## C. Deterministic union at dd9ba00 (RUN ONCE)
- Command: `cd Kriya-rebuild-v2; ulimit -n 256; .venv/bin/pytest -q -n 8 --dist loadgroup -p no:cacheprovider -rA --junitxml=<H>/junit.xml $(cat union_targets.txt)` (script RUN_COMMAND.sh; targets union_targets.txt, provenance union_provenance.json).
- Sources: the ten PRD-020..029 CODING_HANDOVER "Tests"/verification sections + the three recorded commands in BATCH6_SUMMARY.md (focused, 001C subset, PRD027 subset). The Batch-5 focused command text is NOT recorded in the repo (only its count 1962), so per-PRD "Tests" file lists stand in for it.
- Excluded (live tier): tests/test_live_prd020_024_batch5.py, tests/test_live_prd025_029_batch6.py. Not a target: tests/test_pricing.py (fixture-repo prose in the PRD-022 handover). `tests/test_runtime_verification.py` (in my earlier inventory) is in no handover/Batch-6 command, so it is NOT in the union.
- **Result: 3315 passed, 0 failed, 0 errors, 0 skipped, 14 warnings, 2137 s (35:37), exit 0.** 3316 collected, 1 deselected by pyproject addopts (identity not enumerated). No registered timing/non-hermetic failure appeared. 76 files = ~17% of the full suite; full suite NOT run (not needed: nothing failed).
- Artifacts: junit.xml, pytest_output.txt, per_file_counts.json, exit_code.txt, pre_run_state.txt.

## D. Per-PRD reconciliation (labels: LIVE EXERCISED / DETERMINISTICALLY VERIFIED / DISPROVED HYPOTHESIS / OPEN DEFECT / INTENTIONAL)
Per-file passes at dd9ba00 are from per_file_counts.json. "Live" = Graphify run on the current revision unless stated.

| PRD | Acceptance area (source: handover Scope/Tests) | Proving tests (all PASS at dd9ba00) | Status |
|---|---|---|---|
| 020 | requirement derivation, ledger/policy, verifier precedence, plan-validation of invented ids, direct+enforce e2e, terminal gate | test_prd020_requirement_lineage 42, test_prd020_milestone_requirements 5, test_workflow_controller_enforce 276, test_failure_reporting | DETERMINISTICALLY VERIFIED. Lineage/obligation tracking also LIVE EXERCISED (obligations.snapshot x12, STAGE_OBLIGATION_SCOPE) |
| 020 | mutation-scope / acceptance-oracle closure | test_prd020_mutation_scope 34 | DETERMINISTICALLY VERIFIED only (the sealed Graphify run did not reach closure). Greenfield "prints X" cannot reach VERIFIED = GREENFIELD-DETERMINISTIC-OUTPUT-CLOSER-001: INTENTIONAL (registered, owner-gated capability, not signed-PRD acceptance) |
| 021 | grounded duplicate-owner candidates/findings, justification only acknowledges, Planner/Dev/enforce injection | test_prd021_grounded_ownership 11 | DETERMINISTICALLY VERIFIED. **Correction to my earlier report:** the live run showed NO `ownership.finding` event, so the PRD-021 finding mechanism was NOT live exercised; what the run exercised was the separate plan-time `MISSING_GROUNDED_PRODUCTION_ARTIFACT` validation + planner repair |
| 022 | corrective retry targets the grounded owner after a `brownfield_ownership_redirect`, restoration, post-generation findings, reviewer evidence advisory | test_prd022_ownership_recovery 6 | DETERMINISTICALLY VERIFIED. NOT live exercised (no redirect violation occurred). H1 about this PRD: DISPROVED HYPOTHESIS. Attempt-7 attribution-rejected widening is generic attribution, not PRD-022 |
| 023 | contract-change classification statuses, escalation only by policy, approval record | test_prd023_contract_classification 38 | DETERMINISTICALLY VERIFIED. Not live exercised on this revision |
| 024 | auto baseline trigger, environment identity, per-test honesty, REG-R1/R2 | test_prd024_baseline_auto_policy 35 (+ baseline/stability files in the Batch-6 commands) | DETERMINISTICALLY VERIFIED. LIVE EXERCISED (regression.decision, PRE_EXISTING_FAILURE attribution, 76 passed) |
| 025 | bounded verifier evidence package, UNKNOWN not false PASS, exit/timeout authority | test_prd025_verifier_evidence 56 | DETERMINISTICALLY VERIFIED. Live: a run_verifier call occurred in s1 but its package was not examined -> not claimed. Older LIVE_EXERCISED (Batch 6, e34e0ee) is HISTORY, not current |
| 026 | progress vector, seen-vector cycle detection, SAMPLING_RESAMPLE typed, API recovery under the rule, terminal reason | test_prd026_retry_progress 37, test_retry_policy 23 | DETERMINISTICALLY VERIFIED. LIVE EXERCISED: retry.delta x10, 11 distinct vectors, max consecutive no-progress 1 vs 3, 2 SAMPLING_RESAMPLE events, ended on the global bound |
| 027 | fixtures/golden items, measured before any LLM, fixed thresholds, identity-bound record, doctor check | test_prd027_context_certification 36, test_prd027_retrieval_defects 8, test_prd027_precision_expansion_seeds 10, test_production_doctor 62 | DETERMINISTICALLY VERIFIED. LIVE EXERCISED (current): real-embedder certification CERTIFIED precision 0.5581 (target 0.5); 7/7 golden files in the real Developer prompt. Stale stock live test (non-git fixture): known test-evidence issue, not fixed |
| 028 | adapter contract, Kriya-decided read-only escalation, records, fail closed (no mutation-scope expansion) | test_prd028_authority_escalation 21 | DETERMINISTICALLY VERIFIED. LIVE EXERCISED: anchor guard x2, authority.snapshot x12 and **`authority.expansion` MEMBER_AUTHORITY_GRANTED at attempt 9** (seq 364). **Correction:** my earlier report listed member escalation as NOT EXERCISED; it was exercised |
| 029 | registry transitions only at the terminal commit, consumer invalidation, resume fingerprint, crash recovery | test_prd029_contract_lifecycle 38, test_prd029_registry_integrity 9 | DETERMINISTICALLY VERIFIED. Not live exercised (no commit) |

Adjacent suites in the same run (all pass): test_workflow 871, test_workflow_controller_enforce 276, plus the other 55 adjacent files named in the Batch-6 commands.

## E. Open items (owner)
- **O1** no deterministic consumer->producer recovery route; candidate owners plan_validation (decomposition) and cross-unit recovery. **O2** no guard against a scope-forced weakened run-authored test; false-green NOT demonstrated. Neither violates a signed PRD; no registry row exists. NOT an OPEN DEFECT under the labels (no contract owner/reproducer proven); if opened, reproducer = scripted replay of attempts 1/3/5.
- Registered, not new: GREENFIELD-DETERMINISTIC-OUTPUT-CLOSER-001 (P3, owner-gated), stale stock live PRD-027 test (to register).
- Live properties not reached by the failed Graphify run on this revision: PRD-022 redirect, 023, 025 package, 029, resume -> deterministic proof only.

## F. Gate E verdict (recommendation, owner decides)
Gate E text: "Requirements, source authority, regression baselines, ownership findings and contract state are revision-bound and terminally auditable." On the evidence: requirements/baselines/authority/retry are live-exercised and sealed-auditable at dd9ba00; ownership findings and contract state are deterministically verified at dd9ba00 (3315/0); the failed product run is terminally auditable (FAILURE/NOT_COMMITTED, VERIFIED seal). **Recommendation: READY_TO_CLOSE**, conditional on two owner decisions: (1) O1/O2 are registered as P2 design-gap rows and do not hold Wave 5 open; (2) deterministic-only proof is accepted for PRD-022 redirect/023/025/029/resume because the live run failed before reaching them. If the owner answers "no" to either, Wave 5 stays OPEN and the single smallest critical path is: for (1) a four-section-reviewed fix for O1/O2 with the scripted replay reproducer; for (2) one new live Graphify (or synthetic brownfield) run on fresh state/homes after a justified correction (no rerun on unchanged inputs).
