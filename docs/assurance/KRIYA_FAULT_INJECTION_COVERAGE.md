# Kriya Fault-Injection Coverage

R1 Deliverable 4. Tests Kriya's deterministic control plane under deliberately introduced faults from trusted-but-fallible dependencies and intermediate stages — not whether the LLM writes good code, and not new topology support. Every fault family below was derived from the mechanisms `KRIYA_INVARIANT_CATALOG.md` and `KRIYA_TOPOLOGY_COVERAGE.md` already established as critical, not from a generic chaos-engineering checklist. All 10 required scenarios (FI-01–FI-10) were audited for existing coverage before any test was written; only one (FI-06) had a genuine gap, and exactly one test was added to close it.

## Governing properties checked per fault (as applicable)

1. Detected. 2. Correctly classified/routed. 3. No false success. 4. No unauthorized write. 5. No baseline corruption. 6. No loss of already-satisfied obligations. 7. Terminates/retries per existing bounded behavior. 8. No unnecessary LLM churn where architecture already prevents it.

## Detection vs. classification (Step 9)

For every `PROTECTED` entry below, the note explicitly states whether **detection alone** is the protected property, or **detection + correct classification/routing** is required (because a misclassification could itself cause unsafe mutation, false success, wrong recovery, or retry amplification). Six of the ten scenarios require the stronger bar; each is marked.

---

## Coverage matrix

| Fault ID | Fault Family | Tier | Invariants | Injection Boundary | Test Evidence | Expected Safe Outcome | Status |
|---|---|---:|---|---|---|---|---|
| FI-01 | Write authorization | 1 | INV-RECOVERY-001, INV-PLAN-002 | `AuthorizedFileWriter` at the write-commit boundary | `test_authorize_denies_file_outside_validated_subtask_scope`, `test_commit_batch_raises_and_writes_nothing_when_one_target_is_denied` (`tests/test_policy_filesystem_authorized_writer.py`); `test_workflow_stops_retrying_immediately_on_unrecoverable_scope_denial` (`tests/test_workflow.py`) | Denied before mutation; batch all-or-nothing; terminal success impossible | PROTECTED |
| FI-02 | Obligation lifecycle (semantic contract) | 1 | INV-OBL-001, INV-PLAN-004 | `_semantic_contract_regression_subtasks()` / `ObligationLedger` | `test_enforce_reproduces_p7_oscillation_and_rejects_both_silent_drops` (`tests/test_workflow_controller_enforce.py`) | Regressed candidate rejected; retained baseline seeds next round and terminal report | PROTECTED |
| FI-03 | Obligation lifecycle (preserved reference) | 1 | INV-PRESERVE-001, INV-PRESERVE-004 | `_preserved_reference_regressions()` / `ObligationLedger` | `test_enforce_p5_preserved_reference_oscillation_is_not_silently_accepted`, `_inverse_ordering`, `test_enforce_preserved_reference_legitimate_correction_is_not_rejected` (`tests/test_workflow_controller_enforce.py`) | SATISFIED→VIOLATED regression rejected; legitimate correction still converges; no over-freezing | PROTECTED |
| FI-04 | Deterministic build validation | 1 | INV-BUILD-001 | `PolymorphicValidator.run_compile_check()` | `test_reactor_compile_check_fails_when_owning_module_produced_no_classes`, `test_java_compile_check_catches_maven_false_positive_when_nothing_actually_compiled` (`tests/test_polymorphic_validation.py`) | Process exit status alone never accepted as compile success; owning module's real output required | PROTECTED |
| FI-05 | Candidate-independent failure diagnosis | 2 | INV-RETRY-001 | `evaluate_candidate_independent_failure()` wired into `handle_attempt_failure()`, real `WorkflowEngine.run_generation_workflow()` | `test_real_retry_loop_stops_before_a_third_developer_call` (`tests/test_deterministic_failure_diagnostic.py`) | Baseline replay reproduces failure → classified `candidate_independent_deterministic_failure`; 3rd Developer call never made | PROTECTED |
| FI-06 | Candidate-independent failure diagnosis (negative control) | 2 | INV-RETRY-001 | Same as FI-05, baseline replay outcome inverted | `test_real_retry_loop_continues_past_a_third_developer_call_when_baseline_reproduces_nothing` (`tests/test_deterministic_failure_diagnostic.py`, **new**) | Baseline replay reproduces nothing → classification stays candidate-correctable; 3rd Developer call happens; run can still succeed | PROTECTED (was PARTIALLY_PROTECTED before this task — see Gap Closed below) |
| FI-07 | Runtime verification (process lifecycle) | 2 | INV-RUNTIME-001 | `run_managed_service_verification()`, real spawned subprocess | `test_service_early_exit_is_captured_with_returncode`, `test_unexpected_exception_during_readiness_still_cleans_up_process` (`tests/test_service_runtime.py`) | `SERVICE_EXITED_BEFORE_READY` (never a false pass); cleanup still runs; distinct internal-error category never misrouted to a probe result | PROTECTED |
| FI-08 | Runtime verification (evidence producer/consumer) | 2 | INV-RUNTIME-002 | `_build_required_verification_evidence()` vs. real `_execute_runtime_verification_directly()`/`run_tests()` producers | `test_real_run_app_sequence_test_outcome_satisfies_judgment_runtime_requirement` (positive), `test_real_ordinary_test_gate_outcome_does_not_satisfy_judgment_runtime_requirement` (negative, `tests/test_workflow.py`) | Real runtime-verification evidence satisfies the requirement; an unrelated ordinary test-gate outcome, from a genuinely different real producer, does not | PROTECTED |
| FI-09 | Recovery / coordinated repair scope | 1 | INV-RECOVERY-001 | `AuthorizedFileWriter` re-checked inside coordinated repair (`RepairContract`), real `run_attempt()` | `test_run_attempt_coordinated_repair_denies_unauthorized_participant_atomically`, `test_repair_contract_authorized_scope_stays_separate_from_participation` (`tests/test_workflow.py`) | Naming a file as a repair "participant" grants no write authority by itself; denial is atomic; baseline files confirmed unchanged on disk | PROTECTED |
| FI-10 | Failure attribution | 2 | INV-ATTR-001 | `attribute_failure()` via real `handle_attempt_failure()` | `test_handle_attempt_failure_redirects_fixture_precondition_to_test_not_production` (`tests/test_workflow.py`) | A test's own fixture/precondition failure attributes to the test, never authorizing a production-code recovery it could never satisfy | PROTECTED |

**Totals:** 10/10 scenarios PROTECTED, 0 PARTIALLY_PROTECTED, 0 UNPROTECTED. 1 new test added (FI-06); 9 scenarios fully covered by existing tests, reused rather than duplicated.

---

## Per-scenario disposition

### FI-01 — Unauthorized Write Attempt
**Tier:** 1. **Detection vs. classification:** detection alone is the protected property here — a denied write has exactly one safe outcome (reject), no classification branch to get wrong.
**Disposition:** EXISTING, reused. `test_commit_batch_raises_and_writes_nothing_when_one_target_is_denied` proves the batch is all-or-nothing (an otherwise-valid sibling write is confirmed, via `os.path.exists`, never created when one target in the same batch is denied) — this is the baseline-integrity check Step 7 asks for, already present. `test_workflow_stops_retrying_immediately_on_unrecoverable_scope_denial` is the VERTICAL layer: real `run_generation_workflow()`, confirms `quality_gates_passed=False`, `files=[]`, and `failure_category="unauthorized_generation_target"` — terminal success is structurally impossible after this denial.
**No new test added.**

### FI-02 — Previously-Satisfied Semantic Contract Dropped
**Tier:** 1. **Detection vs. classification:** both — a detected-but-misclassified drop (e.g. accepted as ordinary non-strict-regression noise) would let the regressed plan through, exactly the historical P7 incident.
**Disposition:** EXISTING, marked so per the task's own instruction ("If existing P7 vertical coverage already proves this sufficiently, mark EXISTING"). `test_enforce_reproduces_p7_oscillation_and_rejects_both_silent_drops` reproduces the real 3-attempt P7 sequence through `WorkflowController.execute()`.
**No new test added.**

### FI-03 — Previously-Satisfied Preserved Reference Dropped
**Tier:** 1. **Detection vs. classification:** both — this is `INV-PRESERVE-001`, the live defect this R1 audit itself found and fixed (`ddcc1ab`).
**Disposition:** EXISTING, marked so per the task's own instruction. The three Deliverable 3 vertical tests cover the drop (both orderings) and the legitimate-correction/no-over-freezing counter-case together.
**No new test added.**

### FI-04 — Compiler/Validator Claims Success But Required Output Is Missing
**Tier:** 1. **Detection vs. classification:** detection alone — a genuine missing-output finding has one safe outcome (fail the gate); no further classification branch exists inside this specific check.
**Disposition:** EXISTING, reused, restricted to already-validated topology (`TOP-MVN-002`/`TOP-MVN-003` — flat, directly-declared reactor modules) per the task's explicit instruction not to extend nested/profile reactor support.
**No new test added.**

### FI-05 — Candidate Changes But Deterministic Failure Is Candidate-Independent
**Tier:** 2. **Detection vs. classification:** both — this is the mechanism whose own first implementation had a real wrapping-comparison bug (found by exactly this class of vertical test, before this audit began). Misclassification here either wastes a Developer budget slot (under-detection) or, worse, mislabels a genuinely different candidate defect as environment-level and stops repair prematurely (over-detection — see FI-06).
**Disposition:** EXISTING, reused. `test_real_retry_loop_stops_before_a_third_developer_call` drives the real `WorkflowEngine.run_generation_workflow()`, asserting `developer.run_generation.call_count == 2` (never a 3rd call) and the correct `failure_category`. This is explicitly a control-behavior proof, not a wall-clock performance claim — no timing assertion is made or implied.
**No new test added.**

### FI-06 — Similar Candidate Failure But Baseline Does NOT Reproduce It
**Tier:** 2 (negative control for a Tier 2 mechanism). **Detection vs. classification:** both, and this is precisely the direction where a classification error is most dangerous — a false `NON_CANDIDATE_CORRECTABLE` verdict here would terminate a legitimately continuing repair, discarding real, recoverable work.
**Gap found and closed:** Two existing unit tests (`test_baseline_succeeds_classifies_candidate_correctable_and_retry_continues`, `test_different_failure_signatures_produce_no_candidate_independent_conclusion`, `tests/test_deterministic_failure_diagnostic.py`) prove `evaluate_candidate_independent_failure()` itself returns the correct classification — but neither drives the real retry loop. This is the exact class of gap the whole R1 audit exists to catch (a helper-level test cannot prove the real caller wires things correctly — see `feedback_mocked_units_hide_wrapping_mismatches`). **New test added:** `test_real_retry_loop_continues_past_a_third_developer_call_when_baseline_reproduces_nothing` — same recurring-signature shape as FI-05, but the baseline replay returns `success: True`. Asserts `developer.run_generation.call_count == 3` (the loop was NOT short-circuited) and `failure_category != "candidate_independent_deterministic_failure"`, with the 3rd candidate allowed to succeed normally. **Result: no live defect. The mechanism correctly declines to terminate.** Verified directly and under real `pytest` (both passed identically — no autouse-fixture or interpreter-basename divergence in this file).

### FI-07 — Runtime Process Fails Before Readiness
**Tier:** 2. **Detection vs. classification:** both — `SERVICE_EXITED_BEFORE_READY` must be distinguished from `READINESS_TIMEOUT` and from an internal Kriya exception, because each implies a different, existing recovery/repair routing.
**Disposition:** EXISTING, reused. `test_service_early_exit_is_captured_with_returncode` drives a real spawned subprocess (a real Python HTTP-service script configured to exit immediately) through the real `ProcessController`; `test_unexpected_exception_during_readiness_still_cleans_up_process` confirms cleanup and correct category on an unrelated internal-error path, so the category boundary is exercised from both sides.
**No new test added.**

### FI-08 — Runtime Verification Evidence Producer/Consumer Mismatch
**Tier:** 2. **Detection vs. classification:** classification is the entire property under test — both the matching and non-matching outcomes are already "detected" outcomes of the same lookup; the question is only whether the right one fires.
**Disposition:** EXISTING, reused — this is `INV-RUNTIME-002`, itself built this R1 audit from two genuinely different real producers (`_execute_runtime_verification_directly()`/`run_app_sequence()` for the positive case, `run_tests()` for the negative). Current behavior already fails safe in the direction that matters (an unrelated outcome cannot manufacture satisfied evidence); explicitly asserted, not assumed, by `test_real_ordinary_test_gate_outcome_does_not_satisfy_judgment_runtime_requirement`.
**No new test added.**

### FI-09 — Recovery Attempts Illegal Mutation
**Tier:** 1. **Detection vs. classification:** detection alone — `AuthorizedFileWriter`'s check does not depend on which caller (ordinary generation vs. coordinated repair) requested the write.
**Disposition:** EXISTING, reused. `test_run_attempt_coordinated_repair_denies_unauthorized_participant_atomically` drives the real `run_attempt()` with a real `RepairContract` naming an unauthorized file as a "participant" and confirms (a) `PolicyDeniedError` with `FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE`, and (b) both files' on-disk content unchanged from the pre-attempt baseline — the baseline-integrity check Step 7 requires, already present and load-bearing in this exact test. `test_repair_contract_authorized_scope_stays_separate_from_participation` confirms the structural precondition (authorized scope and participation are tracked independently, never conflated) that makes this guarantee meaningful rather than accidental.
**No new test added.**

### FI-10 — Failure Attribution Points at Test Fixture Rather Than Production Cause
**Tier:** 2. **Detection vs. classification:** both — misattributing to production would authorize a repair that can never converge (the historical P1 incident); misattributing to the test on a *genuinely new* production defect would suppress a real fix (the adjacent counter-case already covered in the same test file).
**Disposition:** EXISTING, marked so per the task's own instruction. `test_handle_attempt_failure_redirects_fixture_precondition_to_test_not_production` drives the real `handle_attempt_failure()`, not `attribute_failure()` in isolation.
**No new test added.**

---

## Baseline integrity (Step 7)

Two of the ten scenarios (FI-01, FI-09) are capable of touching workspace state and were checked for baseline integrity specifically; both already assert it using this codebase's own existing revision-hash/content-comparison mechanisms — no new baseline subsystem was needed or added:

- FI-01: `test_commit_batch_raises_and_writes_nothing_when_one_target_is_denied` — `os.path.exists()` confirms the sibling, otherwise-valid write in the same batch was never created.
- FI-09: `test_run_attempt_coordinated_repair_denies_unauthorized_participant_atomically` — both the authorized and unauthorized files' real on-disk content confirmed unchanged from the pre-attempt baseline after the denial.

The remaining scenarios either do not touch workspace files at all (FI-02, FI-03, FI-05, FI-06, FI-08 are pure planning/classification-loop decisions with no candidate write in scope) or their write boundary is already covered transitively by FI-01/FI-09's own mechanism (FI-04's compile-check failure and FI-07's service failure both terminate before any write/apply step is reached).

---

## Scope discipline confirmation

No fault from `KRIYA_TOPOLOGY_COVERAGE.md`'s negative-space list (Maven profile-activated modules, nested reactors, Gradle, mixed-language workspaces) was injected — those are unvalidated topology boundaries, not failures inside validated topology, and are explicitly out of scope for this deliverable. No new correctness mechanism was designed. No performance telemetry was added. MA8 and MA9 were read, not modified.
