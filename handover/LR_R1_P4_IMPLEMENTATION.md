# LR-R1-P4: no equivalent retry of a verification-only unit that cannot change its inputs

**Branch:** `feature/lr-r1-p4`, based on the accepted P1 lineage (`f33d0be`: M1 certified + P1 certified + LV-2).
**Commits:** `976c5a3` (implementation and tests); `7aab9fc` (reproducer: assert never converted to success).
**Not pushed, not merged.**
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED.

## 1. Phase 1: the defect re-established on the accepted lineage (unchanged code, `f33d0be`)

- **Reproducer.** `tests/test_lr_r1_p4_verification_only_retry_reproducer.py` from `5576bc3`, unmodified: **1 passed**
  (MEASURED). It still pins the defect after M1, P1 and LV-2.
- **Measurement harness.** `handover/evidence/lr-r1-p4/p4_measure_harness.py` (evidence only, not in `tests/`) wraps
  `VerificationCoordinator.verify`. At each verification it records the workspace hash, using
  `compute_effective_workspace_hash`, the PRD-026 workspace identity. A first harness version perturbed the run (its
  hook raised inside verify): its numbers were discarded and the hook was made non-raising before measuring.
- **Measured facts** (`phase1_before.json`, MEASURED):

| Fact | Value |
|---|---|
| unit kind | `s2`: `execution_role: verification`, `planned_files: []`: DENY_ALL write scope, a directly executable `test` verifier |
| Developer callable | No: `run_attempt` takes the verification-only branch (`_run_verification_only_attempt`) for every attempt (TRACED) |
| mutation authority | none (DENY_ALL) |
| workspace digest before each verification (attempts 1–4) | `32dac9f0511a09af…` ×4, identical; service file sha256 `5d50733b…` ×4 |
| verification inputs | the same declared verifier ×4 |
| verification result | `test` failure ×4 |
| retry decision | after attempts 1–3: `full_set`, `retry: true` (M1 Q6); after 4: stop |
| retry count | 3 equivalent retries |
| progress | PROGRESS, then REPEATED_VECTOR ×3 with **no changed dimension** |
| model calls in s2 | 0 (2 in the run, both s1's) |
| terminal | s2 failed, s1 completed, nothing applied; `failure_category no_progress`, RETRY_NO_PROGRESS_EXHAUSTED |
| new deterministic input to any retry | **none** (MEASURED: identical digests and inputs; TRACED: nothing in the loop writes a DENY_ALL unit's worktree) |

**Root cause: CONFIRMED, unchanged from the investigation.** `handle_attempt_failure` keeps a DENY_ALL unit's loop
going by design (`should_break=False`; the `NO_AUTHORIZED_REPAIR_TARGET` stop is ALLOWLIST-only), and the retry policy
has no input saying the next attempt cannot change anything.

## 2. Phase 2: retryability from the capability to change state

**Transient versus stable verifier failures (TRACED; no new heuristic added).**
- Kriya has **no attempt-level typed "transient, retry it" classification** for verifier failures.
- Every infrastructure class it models is a deterministic stop set *before* any retry decision:
  - `verification_infrastructure_failure`, `time_budget_exhausted`, `internal_framework_error` and
    `containment_setup_failed` → `environment_failure` (`retry_strategy.py`);
  - `classify_environment_failure` (JVM startup, missing tools, …) → stop;
  - `INFRASTRUCTURE_DEFECT` attribution exists only for `verification_infrastructure_failure`, which already stops.
- P4 leaves all of them deciding first. Limitation: Kriya cannot today recognise a flaky verifier, so a genuinely
  transient test failure in a verification-only unit is now reported after one run rather than re-run. This is the
  same fail-closed outcome the run reached before, after 3 identical re-runs.

**Decision** (`retry_strategy._admit_verification_only_retry`, at the end of failure recording, after attribution).
- **Applies only to** a failure of a verification-only attempt in this invocation: `state.verification_only_inputs`
  is recorded by `_run_verification_only_attempt` with that attempt's number.
- **Existing typed routes decide first, untouched:** an environment/infrastructure stop; a plan-scope conflict (the
  controller reopens the owner, a recovery that can change the workspace); the no-progress terminal.
- **Change mechanisms** for the next attempt:
  - **mutation authority:** the next attempt is not verification-only (`verification_coordinator.is_verification_only_unit`,
    the one predicate `run_attempt` now uses too);
  - **changed inputs:** `verification_inputs_digest` now differs from the digest at the failed verification. The
    digest covers the effective workspace content (every file the run wrote or the unit established) and the
    declared verifiers. It is content only: no attempt number, run id or time.
- **Not mechanisms:** remaining budget, time, another attempt number, a new process or a resume.
- **No mechanism:** the attempt ends on the existing PRD-026 no-progress terminal, typed
  `VERIFICATION_RETRY_NO_CHANGE_POSSIBLE`. It is the same truthful failure (`failure_category no_progress`,
  `quality_gates_passed false`), with no new category and no success.
- **Evidence.**
  - A `retry.verification_admission` run event (mirrored into M1 as an ordinary mirror record) carries: unit kind,
    `verification_only`, `write_scope_mode`, `mutation_possible`, both input digests, `workspace_changed`,
    `recovery_route`, `failure_type`, `retryable`, `reason_code`.
  - The M1 `recovery.decision` (Q6) and Q9's last recovery decision show `no_progress_reason =
    VERIFICATION_RETRY_NO_CHANGE_POSSIBLE` through **existing fields**.
- **No M1 recorder schema change.**

## 3. Before / after (the original reproducer, same harness; `phase1_before.json` / `phase3_after.json`)

| | Before (`f33d0be`) | After (`976c5a3`) |
|---|---|---|
| model calls | 2 (s1: Developer + Reviewer); 0 in s2 | 2 (same); 0 in s2 |
| verification executions (s2) | 4 | **1** |
| retry count (s2) | 3 | **0** |
| wall time | 1.31 s | 1.33 s |
| terminal | s2 failed; `no_progress` / RETRY_NO_PROGRESS_EXHAUSTED; nothing applied | s2 failed; `no_progress` / **VERIFICATION_RETRY_NO_CHANGE_POSSIBLE**; nothing applied |

- **Wall time is not the measure here.** The reproducer's gates are stubs (about 1 ms each), so it cannot show the
  saving.
- **The saving is the 3 removed verification executions.** In the frozen CAGC evidence these took **68–98 s per run**
  (MEASURED there: 6 runs, about 546 s) plus up to 3 model calls in 4 runs. The predicted live saving is that much per
  affected run (INFERRED; a live P4 confirmation is not authorized).

## 4. Tests

- **Reproducer** (Case 1), flipped before the fix:
  - fails on `f33d0be` (MEASURED: `['s1','s2','s2','s2','s2'] != ['s1','s2']`);
  - passes after it;
  - asserts one verification, zero s2 model calls, the admission evidence, the typed terminal, Q6/Q9, and
    `quality_gates_passed false` / FAILURE.
- **`tests/test_lr_r1_p4_verification_retry_admission.py`** (12 tests):

| Case | Test |
|---|---|
| 2 mutable recovery: decision | `test_a_unit_with_mutation_authority_is_retryable` |
| 2 mutable recovery: end to end (s2 may write the file; Developer repairs; retries kept) | `test_case2_a_unit_that_can_repair_keeps_its_retries` |
| 3 workspace changed → reverify allowed | `test_changed_verification_inputs_are_retryable` |
| 4 existing typed infrastructure/environment stop, plan-scope reopen, no-progress decide first | `test_an_existing_typed_route_decides_first` (×3) |
| 5 budget alone is not a mechanism | `test_unchanged_inputs_are_not_retryable_whatever_budget_remains` |
| 6 verification-only success unchanged (`['s1','s2','s2']`, identical pre-fix, MEASURED) | `test_case6_verification_only_success_is_unchanged` |
| 7 attempt number / process identity / resume are not changes | `test_attempt_number_and_process_identity_are_not_changes`, `test_the_reproducer_still_stops_typed_when_the_run_resumes_from_its_inputs` |
| guards | `test_a_failure_that_was_not_a_verification_only_attempt_is_untouched`, `test_the_verification_only_predicate_is_the_run_attempt_branch` |

## 5. Mutation

`p4_mutation_campaign.py` → `p4_mutation_results.json` / `.txt`, at `976c5a3` over the P4 test set. **14 targets, 14
killed:**

| Mutant | Result |
|---|---|
| retry whenever budget remains | KILLED |
| verification-only flag ignored | KILLED |
| verification-only predicate ignores executable verifiers | KILLED |
| workspace-change check ignored | KILLED |
| new-recovery-input (plan-scope) check ignored | KILLED |
| mutable recovery suppressed | KILLED |
| transient/infrastructure stop overridden | KILLED |
| attempt number treated as new information | KILLED |
| resume treated as state change | KILLED |
| typed failure converted to success | **first form SURVIVED ×2; corrected form KILLED** (below) |
| stop without typed reason | KILLED |
| non-verification-attempt guard removed | KILLED |
| admission never called | KILLED |
| admission evidence unrecorded | KILLED |

**The survivor, traced.** The first success-conversion mutant set three of the four flags that
`GenerationState.final_workflow_quality_passed()` requires and never `quality_gates_succeeded`, so it could not change
the outcome. That was a campaign-mutant defect.
- It survived first in the campaign, and again after the reproducer was strengthened (`7aab9fc`; result
  `p4_mutation_rerun_success_conversion.json`). Both SURVIVED results are kept.
- The faithful form, setting all four flags (`p4_mutation_rerun_success_conversion.py` →
  `p4_mutation_rerun_success_conversion_v2.json`), is **KILLED**.

## 6. Gates

| Gate | Result |
|---|---|
| P4 reproducer | fail-before / pass-after |
| focused P4 tests | 13 passed (reproducer + 12) |
| adjacent (PRD-026, retry policy/package, PRD-031 coordinators, enforce controller, verified-no-change, recovery handback, PRD-008, all M1, all P1, PRD-017, CONTEXT-EDIT-PROTOCOL, state-machine tier, `test_workflow.py`, best-of-N, plan schema) | **1785 passed** |
| I-2 | 12 passed |
| P1 regression (all `test_lr_r1_p1_*`, PRD-017, P1 reproducer) | PASS (inside the 1785) |
| Ruff | All checks passed |
| Pylint | exit 0 |
| full suite | **8720 passed, 0 failed, 0 errors** (14 warnings), 531 s, at `7aab9fc`, clean tracked tree (`full_suite.txt`) |

**Own defect found and fixed before the commit.** My `ruff --fix` removed `_directly_executable_verifiers` and
`_directly_executable_runtime_verifiers` from `attempt.py` as unused. Tests import them from there as re-exports
(`test_workflow.py`, `test_plan_schema.py`, `test_prd031_coordinators.py`, `test_lr_r1_m1_reason_codes.py`): 3
collection errors (MEASURED). They are restored as explicit `name as name` re-exports; all four modules pass.

## 7. Non-interference

- **What changed in production:** only the verification-only branch predicate (the same logic, moved into one shared
  function), the digest recorded by a verification-only attempt, and the admission check.
- **Unchanged by construction:** ordinary Developer retries, fallback routing, P1 compatibility routing and derived
  capabilities, D1 authority, RunRecord, the DecisionLedger, verification success and the existing infrastructure
  stops. The admission returns immediately for any failure that is not a verification-only attempt's. Shown by:
  - the guard tests;
  - Case 2 end to end;
  - Case 6;
  - the 1785-test adjacent batch and I-2.
- **P1 code is untouched:** `git diff f33d0be..HEAD` touches none of `model_capabilities.py`, `model_transition.py` or
  the fallback functions.

## 8. Out of scope

P5 not started. P2/P3 deferred. P1-L1, P1-L2 and LV-1 untouched. No CAGC work. No live run.
