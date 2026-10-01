# PRD-031 Coding Agent Handover

## Status
VERIFIED_BY_PYTEST (2026-09-27): focused 1700/0 and full 6477/0, run by the user at 04b6bd4; evidence in handover/evidence/BATCH7/pytest.md. Live test: NOT_REQUIRED.

## Source identity
- Base revision: 48a1a28 (PRD-030 handover; PRD-030 code 26e65c8)
- Final revision: 543aec8
- Branch: milestone-decomposition (local, unpushed)

## Scope implemented
- Instruction file: `tasks/PRD-031_Extract_Verification_and_Recovery_Coordination_Services.md`
- Requirements completed:
  1. **`VerificationCoordinator`** (`kriya/workflow/verification_coordinator.py`). It sequences a verification-only subtask's existing verifiers: PolymorphicValidator compile/test, then the runtime verifier (injected). It owns no mutation and no retry policy.
  2. **`RecoveryCoordinator`** (`kriya/workflow/recovery_coordinator.py`). It consumes a typed `ClassifiedAttemptFailure` and the recorded state, and delegates the decision to `retry_policy.decide_for_state`. The repair and recording machinery is the existing `_record_attempt_failure`, injected.
  3. Only cohesive boundaries were extracted, with typed request/result objects:
     - `ClassifiedAttemptFailure`, `RecoveryDecision`;
     - `VerificationRequest`, `VerificationResult`.
  4. There are no cycles. Neither coordinator imports attempt, retry_strategy, workflow or workflow_controller; the architecture tests enforce this direction.
  5. The compatibility entry points are unchanged:
     - `retry_strategy.handle_attempt_failure(state, ctx, e) -> bool`;
     - `attempt._run_verification_only_attempt`;
     - `attempt._directly_executable_verifiers` / `_directly_executable_runtime_verifiers`, re-imported;
     - callers (workflow.py, best_of_n.py) untouched.
- Requirements deliberately not implemented, with reasons:
  - **`run_attempt`'s inline verification sequence is not migrated.** That is the implementation-subtask compile → targeted/full tests → runtime verification → spec compliance sequence, around 1600 lines. It interleaves self-correction, which writes files, so it is not a mutation-free boundary. The spec also limits this task to one slice ("Do not move all helper functions at once").
  - **The ~900-line recording step stays whole in retry_strategy** (as `_record_attempt_failure`). Its values flow through live lookup, the failure signature, the budgets and attribution, so splitting it further would mean re-deriving them, which the module docstring warns against.
  - **The enforce/STRUCTURED convergence stays deferred**, for the same reason as in PRD-030: it would change behaviour.

## Files changed
- Production:
  - `recovery_coordinator.py` (new, 248 lines).
  - `verification_coordinator.py` (new, 156 lines). The two predicate helpers moved into it verbatim.
  - `retry_strategy.py` (1529 → 1352 lines):
    - the head (classification) and the tail (decision) moved to the coordinator;
    - `_abandon_active_repair_contract_if_any` moved verbatim;
    - the middle is now `_record_attempt_failure`;
    - the docstring records the cut.
  - `attempt.py` (9842 → 9757 lines).
- Tests: `tests/test_prd031_coordinators.py` (new, 32 tests).
- Docs: `docs/design.md` §2.9c (new) and the §2.10 module list; `CLAUDE.md`.

## Pre-change reproduction
This is a refactor. The baseline is the green full suite at cd13d39, plus the PRD-030 targeted runs.

Patch targets checked before moving anything:
- The tests patch `retry_strategy.{classify_environment_failure, attribute_failure, handle_attempt_failure}`. All three are still bound in retry_strategy. `classify_environment_failure` and `attribute_failure` are used by the recording step, which did not move.
- The class-level patches on `PolymorphicValidator.run_*` work wherever they are called from.
- The verification-only path still builds its validator from the lazy `kriya.tools.validate` import in attempt.py.

## Implementation summary
- **Classification.** It is the same expressions: attached failure, then grounded scope denial, then budget failure, then containment / internal / general. Grounding is consulted only when no failure is attached, as before.
- **The unrecoverable-scope-denial counter** now increments at the start of the recording step. The classification never read it, so the value at every read is unchanged.
- **The decision is `conclude_attempt_failure`,** the old tail verbatim: plan-scope conflict / no progress → abandon the contract, log, capture final contents, remove the worktree, stop; else the retry policy, with the same exhaustion handling and the same explicit break only for STOP_ENVIRONMENT. The two duplicated capture-and-remove blocks share one helper, and each keeps its own debug wording.
- **Verification** keeps the same order, outcome dicts and failure messages. Each outcome is recorded when it happens, through a sink, so a verifier that raises leaves the same `gate_outcomes` as before.

## Tests run by coding agent
Targeted runs only; the full suite is for the user.

| Command | Passed | Failed | Notes |
|---|---:|---:|---|
| Recovery/verification characterization set (see handoff), plus `tests/test_plan_schema.py`, `test_prd030_*`, `test_prd031_*` | 267 | 0 | tests unchanged |
| `pytest tests/test_workflow.py -k "handle_attempt_failure or scope_denial or internal_framework or containment or budget_unsatisfiable or repair_contract or no_progress or verification_only or directly_executable"` | 39 | 0 | subset only; the full file belongs to the user |
| `pytest tests/test_prd031_coordinators.py` | 32 | 0 | new |

Mutation check: 15 mutations across the two coordinators, all killed. They cover:
- classification: the grounding guard, attempt-mode wording, the ValueError exclusion, the scope-denial reason code (it survived at first, so a test was added), the containment reason code;
- decision: the plan-scope stop, the environment-only break, the exhaustion cleanup, the in-place shortcut;
- the recorder call;
- verification: the compile and test failure checks, runtime gating, the failure outcome record, and the compile file order.

Structural-test sweep (source-reading and file-set tests):
- `test_architecture_regression_index`, `test_corr016`, `test_corr018`, `test_knowledge_readpath_001`, `test_auth_goal_contamination_001`, `test_prd012_network_inventory`, `test_inf001_runtime_port`, `test_prd008_commit_state_gate` and `test_strict_doubles` all pass (176).
- No test patches `attempt._directly_executable_*` or the moved abandon helper.
- No test asserts on the moved log lines by logger name.

## Static/lint/architecture checks
- `.venv/bin/ruff check .`: all checks passed.
- `.venv/bin/pylint kriya plugins/core_tools tests`: exit 0.
- Architecture tests:
  - no upward imports from either coordinator, including lazy imports;
  - retry_strategy → recovery_coordinator, and attempt → verification_coordinator;
  - the recovery decision goes through `decide_for_state`;
  - the verification coordinator names no write path.

## Live test additions
- Required by instruction: NO.

## Known limitations / residual risks
The deferred items are tracked in the canonical registry `handover/BACKLOG_REGISTRY.csv`, which is authoritative for their status: ENFORCE-EXECUTE-PLAN-CONVERGENCE-001 and RUN-ATTEMPT-GATE-EXTRACTION-001.
- The recovery coordinator's `_abandon_active_repair_contract_if_any` keeps its persisted event source string `retry_strategy.handle_attempt_failure`. It is kept verbatim for trace compatibility.
- Implementation-subtask verification inside `run_attempt` is still inline (see above). The coordinator is the extension point if it is ever migrated.

## Verification-agent handoff
Focused (attempts, retries, gates, architecture index, plus both batch-7 tasks):
```
.venv/bin/pytest tests/test_prd031_coordinators.py tests/test_prd030_terminal_services.py tests/test_workflow.py tests/test_prv17_preflight.py tests/test_deterministic_failure_diagnostic.py tests/test_prd017_fallback_transition.py tests/test_best_of_n.py tests/test_prd026_retry_progress.py tests/test_prv17_stage_contract_architecture.py tests/test_failure_reporting.py tests/test_sec002_fail_closed_evidence.py tests/test_plan_schema.py tests/test_architecture_regression_index.py tests/test_prd004_commit_failure.py tests/test_prd005_commit_transactions.py tests/test_workflow_controller_enforce.py tests/test_workflow_controller.py
```
Full: `.venv/bin/pytest`
