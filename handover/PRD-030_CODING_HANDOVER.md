# PRD-030 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: cd13d39 (Backlog 6.6 closed; pushed; user full suite 6419 passed / 0 failed at f378c40, tests-only delta to cd13d39)
- Final revision: 26e65c8
- Branch: milestone-decomposition (local, unpushed)

## Scope implemented
- Instruction file: `tasks/PRD-030_Extract_TerminalGateService_and_CommitService.md`
- Requirements completed:
  1. `TerminalGateService` (`kriya/workflow/terminal_gate_service.py`) receives the candidate workspace, the immutable baseline identities (migration resolution, ledger baselines), the obligation/requirement inputs and the validators, and returns a typed `TerminalGateReport`. It makes no real-workspace writes: it only reads the artifact registry and the run's committed history.
  2. `commit_service.commit_verified_candidate` (`kriya/workflow/commit_service.py`) accepts only a commit-eligible report (anything else raises `TerminalCommitNotEligibleError`) plus a `TerminalCommitRequest`, i.e. the RunContext: the plan, the candidate and workspace, the base revisions, the plan hash, the ledger and the run id. It performs the revision-grounded transaction through `terminal_commit.commit_terminal_candidate`, the single commit implementation already shared with the generation workflow's terminal apply (PRD-007).
  3. `WorkflowController._run_structured_enforce` stays the lifecycle orchestrator: VERIFYING stage, `terminal_gates_started`, `commit_eligible`, `workspace_commit_*` events, the post-commit control-state save, the approved-plan save, sandbox removal and retention, and result assembly. It contains no gate or commit implementation.
  4. Event names, their order and payloads, result keys, gap messages and reason codes are all unchanged; no migration is needed.
  5. Architecture tests were added, since the repo already has layering tests (inf001, knowledge_readpath).
- Requirements deliberately not implemented:
  - The spec names a "CommitService"; it is a module (`commit_service`) with one entry point, `commit_verified_candidate`. A class would carry no state, whereas `TerminalGateService` is a class because it holds its validators.
  - The enforce/STRUCTURED convergence into `execute_plan()`, which the PRD-008A handover deferred to "PRD-030/031", is not done. It changes orchestration behaviour, not structure, and this batch is extraction only, preserving current semantics (user directive, 2026-09-27). It stays deferred.

## Files changed
- Production:
  - `kriya/workflow/terminal_gate_service.py` (new): the gates, `TerminalGateValidators`, `TerminalGateRequest`, `TerminalGateReport`, `TERMINAL_GATES_NOT_RUN`. It also holds `_terminal_candidate_paths`, `_verify_original_requirements` and `enforce_preserved_reference_terminal_integrity`, moved verbatim, with their persisted `source=` strings unchanged.
  - `kriya/workflow/commit_service.py` (new).
  - `kriya/workflow/workflow_controller.py`: the terminal block is replaced by the two calls. The moved helpers are imported back, because they are bound into the validators and tests import them from here. Unused imports were removed after checking that none is a re-export any test uses. 7022 -> 6666 lines.
- Tests: `tests/test_prd030_terminal_services.py` (new, 26 tests).
- Docs: `docs/design.md` §2.9b (new) plus the §2.10 module list; `CLAUDE.md` (Unified execution plans paragraph).

## Pre-change reproduction
A refactor, so there is no defect. The characterization baseline is the user's green full suite at the base. The PRD-004 gate matrix patches `kriya.workflow.workflow_controller.{find_migration_incomplete, validate_stack_contract_artifacts, enforce_preserved_reference_terminal_integrity, blocking_requirements}`, and test_prd020 patches `wc.find_migration_incomplete`. Moving the gates naively would silently bypass those patches. The injected `TerminalGateValidators`, bound from the controller's own names on every run, keep them effective with the tests unchanged.

## Implementation summary
- Every gate is copied, not rewritten: the same try/except per gate, the same messages, the same ledger records (including `migration.identity_resolution` at INDETERMINATE), and the same lazy imports (`kriya.workflow.workflow` closures, `attempt._close_requirements_by_migration_gate`).
- The six `terminal_gate_outcome` events are emitted through the controller's own `_emit_gate_outcome`, interleaved exactly as before (the verifier's calls happen between them).
- `TerminalGateReport.commit_eligible` = gates ran and no gap. `TERMINAL_GATES_NOT_RUN` is the report on the incomplete-subtask path, so every gap is defined there, with no unbound variable.
- The in-place candidate (candidate root == workspace) returns completed with no transaction, as before.
- `CANDIDATE_MATERIALIZATION_FAILED` is still a structured UNCHANGED failure.
- The PRD-029 transition builder (`_enforce_contract_transition`) stays in the controller and is passed in as `contract_transition_for(writes, transaction_id)`, so the CORR-016 call-site count is unchanged.
- One unavoidable difference: the two INFO log lines of the requirement gate ("mutation-scope evidence", "closure by named tests") now log under `kriya.workflow.terminal_gate_service` instead of `kriya.workflow.workflow_controller`. No test or consumer reads them by logger name.

## Tests run by coding agent
Small targeted runs only; the full suite is for the user.

| Command | Passed | Failed | Notes |
|---|---:|---:|---|
| `pytest tests/test_prd004_commit_failure.py tests/test_workflow_controller_enforce.py -k "test_enforce_terminal_gate_failure_discards_candidate_before_commit or test_prd004_commit_failure or commit"` | 17 | 0 | PRD-004 gate matrix + commit failures, tests unchanged |
| `pytest tests/test_prd005_commit_transactions.py tests/test_prd020_requirement_lineage.py tests/test_prd020_mutation_scope.py tests/test_model_evidence_hardening_001.py tests/test_prd029_contract_lifecycle.py tests/test_corr016_planner_authority_gate.py tests/test_state001_checkpoint_workspace_identity.py tests/test_knowledge_readpath_001.py` | 252 | 0 | every test touching the moved code or the controller's source structure |
| `pytest tests/test_prd030_terminal_services.py` | 26 | 0 | new |

Mutation check: 10 mutations in the new logic, all killed. They cover the eligibility guard, `ran and`, the in-place shortcut, a gate exception turned into a pass, the closure-attempt union, a dropped gate, the verifier-findings suffix, the preserved-reference filter, the INDETERMINATE ledger record, and record_errors propagation. The last two survived at first, and the tests for them were added.

## Static/lint/architecture checks
- `.venv/bin/ruff check .`: all checks passed.
- `.venv/bin/pylint kriya plugins/core_tools tests`: exit 0.
- Architecture tests in `test_prd030_terminal_services.py`:
  - neither service imports the controller;
  - the gate service has no commit path;
  - each gate message and `CANDIDATE_MATERIALIZATION_FAILED` has exactly one owner module;
  - the controller names no commit primitive and has exactly one `TerminalGateService(` and one `commit_verified_candidate(`.

## Live test additions
- Required by instruction: NO.

## Known limitations / residual risks
- The validators are resolved from `workflow_controller`'s module globals at each run. Anyone moving that binding must keep it, or the characterization patches go stale (noted in CLAUDE.md).
- The requirement gate still reads `kriya.workflow.workflow` lazily (unchanged cycle avoidance). PRD-031 is the place to revisit it, if at all.

## Verification-agent handoff
Focused (PRD-004/005 characterization, controller, control-plane, edit safety, new tests):
```
.venv/bin/pytest tests/test_prd030_terminal_services.py tests/test_prd004_commit_failure.py tests/test_prd005_commit_transactions.py tests/test_workflow_controller_enforce.py tests/test_workflow_controller.py tests/test_edit_safety_policy_audit.py tests/test_control_plane_end_to_end.py tests/test_checkpoint_control_plane_hashes.py tests/test_prd020_requirement_lineage.py tests/test_prd020_mutation_scope.py tests/test_prd029_contract_lifecycle.py tests/test_model_evidence_hardening_001.py tests/test_tool001_autonomous_tool_execution.py
```
Full: `.venv/bin/pytest`
