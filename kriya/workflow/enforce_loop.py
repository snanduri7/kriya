"""The structured enforce run (full modularization W4, E12).

Moved verbatim from ``kriya/workflow/workflow_controller.py``: ``WorkflowController._run_structured_enforce`` is
``EnforceLoop.run`` here - the sealed verification contract and its admission/baseline stops, the authoritative
Planner request and the bounded PLAN_REPAIR loop (``_UnsafeStructuredPlan`` on exhaustion), the subtask-spanning
resume decision and the abandoned-plan quarantine, the approved-plan document, the plan-level worktree sandbox, the
per-subtask loop (bounded subtask invocation, TOOL subtasks, planned-prerequisite and runtime-plan-gap revisions, the
PLAN_SCOPE_DEFECT revision loop, owner recovery, the verification-owner reopen, per-subtask acceptance and stop), the
terminal gates and commit (``TerminalGateService``/``commit_verified_candidate``), the worktree removal on every exit
and the aggregated result. The nested closures (``_persist_control_state``, ``planner_prompts``,
``_invoke_bounded_subtask``, ``_emit_terminal_event``, ``_emit_gate_outcome``) stay nested in it. Every decision,
record, event and log line is unchanged, in the same order; it was moved whole (one body, not phases: the run's
state - ``plan``, ``order``, ``control_state``, ``execution_context``, ``approved_stage_states``, ``call_result`` - is
rebound across every plausible cut and captured by the closures).

``WorkflowController`` keeps the method name and signature (a forwarder): ``execute`` calls it and catches the
pre-execution exceptions it raises (``_StructuredPlanUnavailable``, ``_UnsafeStructuredPlan``, the admission and
contract stops), which stay defined in the controller. What arrives in ``EnforceDeps``, resolved by
``workflow_controller._enforce_deps(controller)`` once, when the forwarder is entered (never per loop iteration): the
controller instance's ``workflow_engine`` and bound ``_build_context``; EVERY ``kriya.workflow.workflow_controller``
module-level name the body reads (the suite patches ``parse_planner_structured_output``,
``build_engineering_plan_from_planner_output``, ``validate_plan``, ``model_capacities`` and ``collect_unit_grounding``
(through ``evaluate_plan_edit_feasibility``), ``subtask_executor``, ``create_git_worktree``, ``remove_git_worktree``,
``StaticAnalysisService``, ``enforce_preserved_reference_terminal_integrity``, ``save_artifact_registry``,
``save_control_state`` and more there; the rest are the controller's own helpers and forwarders, all-import-cycle
members, or names the controller imports through allowlisted edges); the controller's ``logger``; and loaders doing
the body's call-time imports of ``kriya.workflow.workflow`` (the facade) and ``kriya.workflow.goal_facts`` (all-import
cycle). Annotations that would need such an import (``ControlState``, ``Decision``, ``CandidatePrompts``) are spelled
``Any``. The body's other call-time imports (``authority_request``, ``contract_compilation``, ``contract_baseline``,
``run_events``) stay verbatim. This module never imports ``kriya.workflow.workflow_controller``, ``workflow``,
``attempt`` or ``kriya.cli``, nor any all-import-cycle module.
"""
from __future__ import annotations

import functools
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from kriya.repository.git import RepositoryContentFilterRefused
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.plan_schema import EngineeringPlan, Subtask, VerificationMethodType
from kriya.workflow.subtask_executor import plannable_tool_names
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus, VerificationReport


@dataclass(frozen=True)
class EnforceDeps:
    """What the moved enforce loop may use, resolved through ``kriya.workflow.workflow_controller`` (its module names and
    the controller instance) by ``workflow_controller._enforce_deps(controller)`` once, when the forwarder is entered
    (never per loop iteration). Every field is required: a builder that omits one fails at construction, never at
    the first use.
    """

    # the controller instance's collaborators (its engine; its bound _build_context)
    workflow_engine: Any
    _build_context: Any
    # defined in kriya.workflow.workflow_controller
    EstablishedProvenance: Any
    _ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE: Any
    _MAX_PLAN_SCOPE_REVISION_ATTEMPTS: Any
    _StructuredPlanUnavailable: Any
    _UnsafeStructuredPlan: Any
    _attempt_owner_recovery_self_correction: Callable[..., Any]
    _authoritative_planner_extension_candidates: Callable[..., Any]
    _build_owner_recovery_context: Callable[..., Any]
    _cross_owner_obligation_id: Callable[..., Any]
    _enforce_contract_transition: Callable[..., Any]
    _evaluate_integration_obligations: Callable[..., Any]
    _evaluate_recovery_acceptance_precheck: Callable[..., Any]
    _evaluate_subtask_verification: Callable[..., Any]
    _get_or_create_cross_owner_obligation: Callable[..., Any]
    _is_strict_regression: Callable[..., Any]
    _plan_scope_conflict_files: Callable[..., Any]
    _preserved_reference_must_preserve_lines: Callable[..., Any]
    _preserved_reference_pairs_mentioned: Callable[..., Any]
    _preserved_reference_regressions: Callable[..., Any]
    _semantic_contract_must_preserve_lines: Callable[..., Any]
    _semantic_contract_regression_subtasks: Callable[..., Any]
    _subtask_ids_mentioned: Callable[..., Any]
    all_subtasks_completed: Callable[..., Any]
    build_approved_plan_document: Callable[..., Any]
    build_authoritative_planner_request: Callable[..., Any]
    build_planning_structural_evidence: Callable[..., Any]
    build_recovery_execution_plan: Callable[..., Any]
    build_subtask_constraint_context: Callable[..., Any]
    build_subtask_goal_text: Callable[..., Any]
    build_subtask_plan_text: Callable[..., Any]
    build_subtask_semantic_context: Callable[..., Any]
    compute_abandoned_plan_files: Callable[..., Any]
    evaluate_plan_edit_feasibility: Callable[..., Any]
    exclude_tool_subtasks_from_resume: Callable[..., Any]
    find_missing_grounded_production_artifacts: Callable[..., Any]
    logger: Any
    nearest_mutating_upstream_owner: Callable[..., Any]
    quarantine_abandoned_plan_files: Callable[..., Any]
    resolve_effective_scope_conflict_owners: Callable[..., Any]
    resolve_python_module_to_candidate_artifact_path: Callable[..., Any]
    resolve_runtime_plan_gap_owner: Callable[..., Any]
    revise_plan_for_grounded_scope_owner: Callable[..., Any]
    revise_plan_for_planned_prerequisite: Callable[..., Any]
    revise_plan_for_runtime_plan_gap: Callable[..., Any]
    split_planned_source: Callable[..., Any]
    subtask_scope_conflict_report: Callable[..., Any]
    transitive_subtask_dependents: Callable[..., Any]
    verification_failure_evidence: Callable[..., Any]
    verification_owner_recovery_context: Callable[..., Any]
    # kriya.agents.contracts
    parse_planner_structured_output: Callable[..., Any]
    # kriya.control.decisions
    DecisionLedger: Any
    # kriya.control.persistence
    UnreadableRunRecordError: Any
    load_artifact_registry: Callable[..., Any]
    load_control_state: Callable[..., Any]
    load_control_state_run_reference: Callable[..., Any]
    load_run_record: Callable[..., Any]
    save_approved_plan: Callable[..., Any]
    save_artifact_registry: Callable[..., Any]
    save_control_state: Callable[..., Any]
    # kriya.control.run_coordinator
    annotate_run: Callable[..., Any]
    authorize_candidate_workspace: Callable[..., Any]
    mark_run_stage: Callable[..., Any]
    # kriya.control.run_record
    RunLifecycle: Any
    # kriya.core.attempt_evidence
    attempt_evidence_scope: Any
    # kriya.core.llm (all-import cycle)
    InferenceDeadlineError: Any
    # kriya.policy.filesystem
    WriteScopeMode: Any
    # kriya.static_analysis.service
    StaticAnalysisCandidate: Any
    StaticAnalysisService: Any
    commit_guard: Callable[..., Any]
    static_analysis_result_fields: Callable[..., Any]
    # kriya.workflow
    subtask_executor: Any
    # kriya.workflow.acceptance
    goal_requires_runtime_behavior: Callable[..., Any]
    # kriya.workflow.acceptance_approval
    bound_approval: Callable[..., Any]
    # kriya.workflow.acceptance_oracle
    bound_acceptance: Callable[..., Any]
    # kriya.workflow.checkpoint (all-import cycle)
    ResumeStatus: Any
    compute_base_commit: Callable[..., Any]
    compute_tree_hash: Callable[..., Any]
    compute_workspace_content_hash: Callable[..., Any]
    validate_resume_against_reality: Callable[..., Any]
    # kriya.workflow.commit_service (all-import cycle)
    TerminalCommitRequest: Any
    commit_verified_candidate: Callable[..., Any]
    interrupted_commit_outcome: Callable[..., Any]
    plan_terminal_writes: Callable[..., Any]
    run_commit_states: Callable[..., Any]
    # kriya.workflow.context_budget (all-import cycle)
    CandidatePrompts: Any
    candidate_model: Callable[..., Any]
    fit_reference_section: Callable[..., Any]
    fit_structural_evidence: Callable[..., Any]
    # kriya.workflow.context_projection (all-import cycle)
    project_implementation_source: Callable[..., Any]
    render_established_file_context: Callable[..., Any]
    # kriya.workflow.contract_baseline
    NoMutationRequired: Any
    VerificationAuthorityUnavailable: Any
    # kriya.workflow.deterministic_failure_diagnostic
    DeterministicFailureDiagnosticStore: Any
    # kriya.workflow.edit_feasibility (all-import cycle)
    infeasible_results: Callable[..., Any]
    repair_error_text: Callable[..., Any]
    # kriya.workflow.edit_safety
    read_file_revision: Callable[..., Any]
    # kriya.workflow.execution_plan
    PlanSourceKind: Any
    # kriya.workflow.graph_retrieval (all-import cycle)
    localization_candidates: Callable[..., Any]
    render_localization_candidates: Callable[..., Any]
    # kriya.workflow.migration
    MigrationResolution: Any
    MigrationResolutionStatus: Any
    find_migration_incomplete: Callable[..., Any]
    resolve_migration_resolution: Callable[..., Any]
    # kriya.workflow.obligations
    ObligationAuthority: Any
    ObligationKind: Any
    ObligationLedger: Any
    ObligationRecord: Any
    ObligationStatus: Any
    # kriya.workflow.ownership_findings (all-import cycle)
    find_ownership_findings: Callable[..., Any]
    findings_prompt_block: Callable[..., Any]
    grounded_owner_candidates: Callable[..., Any]
    owner_candidates_prompt_block: Callable[..., Any]
    record_findings: Callable[..., Any]
    settle_findings: Callable[..., Any]
    # kriya.workflow.plan_executor (all-import cycle)
    WorkUnitInvocation: Any
    # kriya.workflow.plan_normalization
    coalesce_same_file_owners: Callable[..., Any]
    derive_verification_contracts: Callable[..., Any]
    redirect_immutable_test_edits: Callable[..., Any]
    scope_verification_to_requirements: Callable[..., Any]
    # kriya.workflow.plan_revision
    PLAN_SCOPE_REVISION_REQUIRED: Any
    # kriya.workflow.plan_schema
    ExecutionMethod: Any
    ExecutionRole: Any
    FileAction: Any
    FileOwnershipRelation: Any
    build_engineering_plan_from_planner_output: Callable[..., Any]
    # kriya.workflow.plan_targets (all-import cycle)
    check_plan_targets: Callable[..., Any]
    # kriya.workflow.plan_validation (all-import cycle)
    canonicalize_planned_file_actions: Callable[..., Any]
    validate_plan: Callable[..., Any]
    # kriya.workflow.planner_repair (all-import cycle)
    STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS: Any
    build_structured_plan_repair_prompt: Callable[..., Any]
    classify_planner_outcome: Callable[..., Any]
    classify_structured_plan_parse_issue: Callable[..., Any]
    planner_response_model: Callable[..., Any]
    record_planner_outcome: Callable[..., Any]
    # kriya.workflow.planning_diagnostics
    bounded_repository_evidence: Callable[..., Any]
    persist_planning_attempt_diagnostic: Callable[..., Any]
    # kriya.workflow.planning_request
    AUTHORITATIVE_PLANNER_SYSTEM_PROMPT: Any
    BOUNDED_SUBTASK_DESIGN: Any
    # kriya.workflow.recovery_plan
    RecoveryAction: Any
    RecoveryExecutionPlanStatus: Any
    # kriya.workflow.requirement_contract
    bound_requirement_contract: Callable[..., Any]
    requirement_set_for: Callable[..., Any]
    # kriya.workflow.requirements
    VerificationAuthorityRequired: Any
    blocking_requirements: Callable[..., Any]
    requirement_evidence: Callable[..., Any]
    requirement_outcomes: Callable[..., Any]
    requirement_verdict_details: Callable[..., Any]
    requirements_prompt_block: Callable[..., Any]
    seed_requirement_obligations: Callable[..., Any]
    # kriya.workflow.retry_progress
    VERIFICATION_RETRY_NO_CHANGE_POSSIBLE: Any
    # kriya.workflow.static_checks
    derive_stack_contract: Callable[..., Any]
    log_stack_contract_boundary: Callable[..., Any]
    validate_stack_contract_artifacts: Callable[..., Any]
    # kriya.workflow.subtask_checkpoint (all-import cycle)
    topological_subtask_order: Callable[..., Any]
    # kriya.workflow.subtask_context_projection
    project_for_subtask: Callable[..., Any]
    # kriya.workflow.subtask_telemetry (all-import cycle)
    record_plan_created: Callable[..., Any]
    record_subtask_attempt: Callable[..., Any]
    # kriya.workflow.terminal_gate_service (all-import cycle)
    TERMINAL_GATES_NOT_RUN: Any
    TerminalGateRequest: Any
    TerminalGateService: Any
    TerminalGateValidators: Any
    _verify_original_requirements: Callable[..., Any]
    enforce_preserved_reference_terminal_integrity: Callable[..., Any]
    # kriya.workflow.verification_report
    build_verification_report: Callable[..., Any]
    # kriya.workflow.workflow (all-import cycle)
    _log_phase_banner: Callable[..., Any]
    # kriya.workflow.workflow_types
    SubtaskResult: Any
    SubtaskStatus: Any
    # kriya.workflow.worktree
    WorktreeSyncError: Any
    create_git_worktree: Callable[..., Any]
    remove_git_worktree: Callable[..., Any]
    # function-local imports the owner may not do itself, done by the controller when the body reaches them
    load_contract_planning_constraints: Callable[[], Any]
    load_goal_facts: Callable[[], Any]
    load_run_contract: Callable[[], Any]
    load_run_contract_baseline: Callable[[], Any]


class EnforceLoop:
    """The structured enforce run, moved verbatim from ``WorkflowController._run_structured_enforce`` (E12)."""

    @staticmethod
    async def run(
        deps: EnforceDeps, goal: str, workspace_path: str, route: Any, run_id: str,
        control_context: WorkflowControlContext, control_state: Any,
        legacy_kwargs: Dict[str, Any],
    ) -> Tuple[
        Dict[str, Any], Optional[EngineeringPlan], Tuple[SubtaskResult, ...],
        Tuple[Any, ...], Optional[VerificationReport], Any,
    ]:
        """MA7.8 - WorkflowController actually OWNING the real outcome for
        the first time. Deliberately does NOT reimplement edit-application/
        compile-test verification/approval gating (SubtaskExecutor still
        only produces file content or a tool result, exactly as documented
        elsewhere in this module) - instead reuses the SAME real, mature
        mechanism kriya/workflow/milestones.py::run_milestones() already
        uses for the analogous "decompose one big goal into smaller real
        generation calls" problem: call the EXISTING, unmodified
        self.workflow_engine.run_generation_workflow() once per SUBTASK
        (instead of once per MILESTONE), in dependency order, threading
        each completed subtask's real written-file content forward as the
        next one's supplementary_context/established_files - the exact
        same render_established_file_context/project_implementation_source
        machinery run_milestones() already calls, not a second copy of it.
        This is real per-subtask retry locality (MA7's own stated goal):
        each subtask gets its OWN run_generation_workflow() call, with its
        own independent retry budget/Quality-Gates loop/approval gate - a
        failure in subtask 2 only ever retries subtask 2's own call, never
        re-triggers subtask 1 or 3.

        MA7-C1 (2026-08-25 external review): each subtask's own call no
        longer runs a FRESH Planner+Architect cycle internally either - the
        already-validated Subtask is now authoritative. build_subtask_plan_
        text()/build_subtask_goal_text() synthesize predetermined_plan/
        predetermined_design (plus predetermined_architect_files from the
        subtask's own planned_files) and pass them into
        run_generation_workflow(), which skips its own stages 2/3 (Plan/
        Architect LLM calls) entirely when supplied - see that method's own
        docstring for the exact mechanism and why it's a dedicated
        parameter, not an overload of resume_state. Every OTHER stage
        (Graph RAG retrieval, skill matching, the Developer/Quality-Gates
        retry loop, failure grounding, attribution, repair, approval,
        regression verification, trace logging) is completely unmodified -
        this closes the "current: validated subtask -> fresh legacy
        Planner/Architect cycle -> Developer" gap the external review
        flagged as the single most important remaining MA6/MA7
        architectural discrepancy, without duplicating or reimplementing
        any of the retry/verification engine SubtaskExecutor deliberately
        still doesn't own. MA6 invariant 4 (a subtask may not silently
        modify undeclared files) is now actually enforced, not just
        detectable - see the subtask loop below.

        Known, honest scope boundaries for this first real cut (not silent
        gaps - each is a deliberate, separate decision):
        - TOOL-1 (2026-09-13): TOOL-tagged subtasks are now executed for
          real (see the per-subtask loop below) - by the time this landed,
          "zero policy gate a raw shell string can't trivially defeat" was
          no longer accurate: TOOL-002/TOOL-003 (MCP invocation/capability
          authority) and SEC-005 (ShellTool package-manager network
          authority) had both since closed, and every real TOOL dispatch
          converges on the exact same governed `BaseTool.execute()`
          boundary `kriya tools execute` already uses - no new or weaker
          policy mapping was introduced for this. `_run_structured_shadow`'s
          own hard stop (above) is unchanged and unrelated - that
          restriction is specific to shadow's own non-mutating contract,
          not a security gap this pass closed.
        - Subtask-spanning resume (2026-08-24): when the CALLER passes
          resume=True or resume_id=<id> (the same flags each subtask's own
          run_generation_workflow() call already accepted), this method now
          ALSO checks for a persisted ControlState from an earlier
          interrupted enforce run of this workspace and, if the current
          re-planned EngineeringPlan's content_hash() and the workspace's
          real git tree_hash/base_commit still match what was recorded
          (kriya/workflow/checkpoint.py's validate_resume_against_reality,
          MA5.9 - genuinely wired to something for the first time), skips
          already-COMPLETED subtasks rather than re-running them, restoring
          their real on-disk file content into established_file_context.
          Any drift in either check -> the resume is refused, exactly like
          the legacy path's own workspace/config/goal drift check, and the
          plan runs fresh from subtask 1. MA6.7's SubtaskCheckpoint/
          resolve_subtask_resume_point (a plan-hash+tree-hash-keyed
          per-subtask mechanism) remains a real, unused alternative for a
          future, more granular resume signal - not needed now that
          ControlState.subtask_states covers the same real gap.
        - Some duplication with _run_structured_shadow's own plan-build/
          validate prologue (Planner call, parse, validate_plan) is
          accepted here rather than refactoring that already-live,
          independently-tested method under time pressure - a real,
          flagged simplification opportunity, not something to silently
          leave undocumented.
        - MA7.9 adds real subtask_retry_locality data to the aggregated
          result (each subtask's own generation_metrics()["calls"], real
          per-subtask retry counts, never spilling into another subtask's
          count by construction) and MA7.10 adds real ArtifactRegistry
          derivation. PRD-004 derives those facts from the fully verified
          isolated candidate before commit, then persists the exact derived
          records after the one real-workspace batch commit succeeds.
          PRD-029: the ContractRegistry now has its hook too, without any
          Planner-declared contract schema. The commit seam derives
          public_api contract records from the verified candidate's
          deterministic public-signature diff plus the goal's DIRECT
          authorizations (_enforce_contract_transition ->
          contract_lifecycle.derive_contract_transition). It invalidates
          consumers, requires the final subtask's full suite on the
          candidate, and commits the registry in the same transaction as
          the source.
        - Context quality, verified by construction rather than measured:
          each subtask's Developer call goes through the UNMODIFIED
          run_generation_workflow(), so it receives the full legacy Graph
          RAG context (hybrid vector+dependency-graph retrieval) - the
          SAME context quality any ordinary `kriya generate` call already
          gets, strictly richer than _run_structured_shadow's own
          deliberately narrower ContextOrchestrator-only context (see that
          method's own docstring). Enforce mode does not inherit shadow's
          context-quality gap.

A structural, PRE-EXECUTION problem (no parseable plan, zero subtasks,
        failed validation - a TOOL-tagged subtask present is no longer one
        of these, see TOOL-001 above) enters a bounded
        local PLAN_REPAIR loop. Two unsuccessful corrections raise
        _UnsafeStructuredPlan and fail closed without a legacy fallback,
        preserving the authoritative write-scope boundary. A
        subtask-loop failure (at least one real run_generation_workflow()
        call has already happened) is NOT this exception - it stays a
        clean, normally-shaped FAILURE result in the aggregated dict
        (same status/quality_gates_passed shape any ordinary
        run_generation_workflow() failure already has), never a legacy
        fallback, since real side effects may already exist in the
        workspace by that point. Only a genuine bug in this method's own
        code propagates as a real (uncaught) exception."""

        def _persist_control_state(cs: Any) -> Any:
            """STATE-001 (2026-09-14): every real save of ControlState
            WITHIN this method's own per-subtask loop must reflect CURRENT
            workspace content, never a value computed once, early, before
            the loop began. Unlike base_commit/tree_hash (safe to compute
            once - neither is sensitive to an uncommitted write), subtasks
            in THIS execution mode apply real content changes to
            workspace_path as plain file writes, incrementally, as each one
            completes - a workspace_content_hash carried unchanged from the
            pre-loop computation through every later save would persist
            STALE pre-run content for every state actually saved after any
            subtask completed, silently reintroducing the exact drift-
            blindness this fix exists to close (found live via the real
            test_workflow_controller_enforce.py resume tests: a genuinely
            zero-drift resume was incorrectly refused).

            Deliberately a LOCAL closure, not a change to
            save_control_state() itself (kriya/control/persistence.py) -
            that function's own contract is "persist exactly what you're
            given," relied on directly by callers doing a plain save-then-
            reload round-trip (test_control_plane_end_to_end.py's own
            control_state.content_hash() == reloaded_state.content_hash()
            check) elsewhere in this codebase; centralizing the refresh
            there would silently diverge what gets persisted from what the
            caller explicitly passed, breaking that real invariant. This
            closure is the correct, narrow place: the ONE method whose own
            control flow actually needs per-save freshness."""
            cs = cs.with_updates(workspace_content_hash=deps.compute_workspace_content_hash(workspace_path))
            deps.save_control_state(workspace_path, cs)
            return cs

        ledger = deps.DecisionLedger()

        kernel = getattr(deps.workflow_engine, "kernel", None)
        # LR-R1-M1: structured planning runs before any work unit; open the
        # run's attempt-evidence store now (observational, never blocks).
        deps.attempt_evidence_scope.ensure_store(getattr(kernel, "config", None))
        # REQUIREMENT-CLOSURE-PLAIN-GOAL-001 / VERIFICATION-CONTRACT-003: right
        # after the run's evidence store exists and before any retrieval or
        # model call, the run's verification contract is compiled from the
        # authoritative requirement set and the repository; under the blocking
        # policy a goal it cannot admit is refused here, spending nothing (the
        # direct path refuses at the same point) - the refusal is still
        # recorded. Admitted, the contract is sealed and its digest binds the
        # enforce ControlState (no subtask recorded under another contract is
        # ever reused).
        _requirement_policy_blocks, compile_run_contract, seal_run_contract = deps.load_run_contract()

        kernel_config = getattr(kernel, "config", None)
        autonomy = getattr(kernel_config, "autonomy", None)
        verification_contract = compile_run_contract(
            deps.workflow_engine, goal, goal, workspace_path,
            deps.requirement_set_for(goal, deps.bound_requirement_contract(deps.workflow_engine)),
        )
        if _requirement_policy_blocks(autonomy):
            admission = verification_contract.refusal()
            if admission is not None:
                if isinstance(admission, deps.VerificationAuthorityRequired) and admission.authority_requests:
                    # BACKEND-READINESS-004 (D2): seal the typed requests before the typed stop.
                    from kriya.workflow.authority_request import seal_requests_for_refusal

                    admission.authority_requests_path = seal_requests_for_refusal(kernel_config, verification_contract, admission)
                raise admission
        sealed_contract_path = seal_run_contract(kernel_config, verification_contract) if kernel_config is not None else None
        # PLAN-TEST-IMMUTABILITY-SCOPE-001: existing test files the contract's
        # TEST_IMMUTABILITY claim freezes - a planning constraint, never a model input.
        documentation_referent_paths, frozen_file_paths, immutable_test_files = deps.load_contract_planning_constraints()

        immutable_tests = immutable_test_files(verification_contract, workspace_path)
        # OD-3: the files the goal froze by name (FILE_IMMUTABILITY) - the same kind of planning constraint.
        frozen_files = sorted({path for paths in frozen_file_paths(verification_contract).values() for path in paths})
        # PLAN-DOCUMENTATION-UNIT-VERIFICATION-001: documentation the sealed contract judges itself.
        documentation_paths = documentation_referent_paths(verification_contract)
        # PLAN-TARGET-GROUNDING-001 (c): manifests that only delegate to a file the goal's own package name occurs in.
        delegating_shim_targets, goal_facts_block = deps.load_goal_facts()

        shim_targets = delegating_shim_targets(goal, workspace_path)
        baseline_report = None
        if verification_contract.authorities and verification_contract.refusal() is None and kernel_config is not None:
            from kriya.workflow.contract_baseline import BaselineAuthorityReport
            run_contract_baseline = deps.load_run_contract_baseline()

            try:
                baseline_report = run_contract_baseline(
                    deps.workflow_engine, verification_contract,
                    deps.requirement_set_for(goal, deps.bound_requirement_contract(deps.workflow_engine)), workspace_path,
                    autonomy_cfg=autonomy, config=kernel_config)
            except Exception as exc:
                # Review VC3-R8 / D2: a baseline that could not run is an environment outcome - typed stop.
                deps.logger.warning(f"WorkflowController enforce run {run_id!r}: baseline authority run unavailable: "
                               f"{type(exc).__name__}: {exc}")
                baseline_report = BaselineAuthorityReport(base_revision=None)
                baseline_report.authorities_run.append({
                    "kind": "baseline_run", "digest": None, "reason_code": "AUTHORITY_EXECUTION_UNAVAILABLE",
                    "reason": f"the baseline authority run raised {type(exc).__name__}: {exc}"})
            if baseline_report is not None and baseline_report.unavailable_authorities():
                raise deps.VerificationAuthorityUnavailable(verification_contract, baseline_report)
            if baseline_report is not None and baseline_report.no_mutation_required:
                raise deps.NoMutationRequired(verification_contract, baseline_report)
        # S18 (OI-127): ONE snapshot of the planned-tool catalogue (subtask_executor.plannable_tool_names, the projection
        # of the one governance resolution). A registry that cannot be listed admits no tool (fail closed); every
        # validate_plan below checks TOOL subtasks against the plannable names and repair prompts show only those.
        # S18b (OI-143): a run without a kernel has no registry, so it admits no tool either (the same validation, never
        # skipped); a TOOL subtask is refused at validation instead of reaching dispatch.
        tool_catalogue = plannable_tool_names(getattr(kernel, "registry", None), getattr(kernel, "config", None))
        available_tool_names = list(tool_catalogue.registered)
        planned_tool_names = list(tool_catalogue.plannable)

        # Subtask-spanning resume (MA5.9 finally wired to something,
        # 2026-08-24) - opt-in only, same convention as
        # run_generation_workflow()'s own resume (no auto-detection from
        # goal-text matching). Loaded BEFORE validate_plan (moved here
        # 2026-08-24, see has_own_established_progress below for why) -
        # MA6 has no separate plan-persistence sidecar the way MA3's
        # milestone flow does (plan_text above is a FRESH Planner call
        # every time), so trusting a persisted ControlState's subtask_states
        # for the actual SKIP decision is only safe when the freshly-
        # rebuilt plan and the real workspace both still match what was
        # recorded - exactly the drift compute_control_plane_hashes/
        # validate_resume_against_reality were built to catch. Any mismatch
        # (or no prior state at all) -> resumed_subtask_states stays empty
        # and the plan runs fresh from subtask 1, same as if resume had
        # never been requested.
        prior_control_state: Optional[Any] = None
        if legacy_kwargs.get("resume") or legacy_kwargs.get("resume_id"):
            prior_control_state = deps.load_control_state(workspace_path)
            if prior_control_state is None:
                deps.logger.info(
                    f"WorkflowController enforce run {run_id!r}: resume requested but no prior "
                    "ControlState found for this workspace - starting the plan fresh."
                )

        # Real live-validation finding, 2026-08-24, protocol_encoder_java:
        # after subtask s1 genuinely completed (real Protocol.java on disk)
        # and s2 exhausted its retries, a resumed run's FRESH re-plan
        # correctly triggered extension_points validation (the workspace is
        # no longer empty - _workspace_appears_empty's own exemption
        # correctly stopped applying) - but the Planner, unprompted about
        # continuation, didn't supply one. That validation failure raised
        # _StructuredPlanUnavailable, which fell back to the LEGACY
        # whole-goal path - the exact safety net built for a truly
        # side-effect-free pre-execution failure - except here real side
        # effects already existed (s1's Protocol.java), and the legacy
        # Developer, with no reason to leave it alone, regenerated it from
        # scratch and introduced new bugs that didn't exist before. The
        # established content here is Kriya's OWN prior subtask output for
        # THIS SAME resumed goal, not foreign pre-existing work the
        # extension_points rule exists to protect - so a real resume in
        # progress (a persisted ControlState with at least one completed
        # subtask) exempts extension_points the same honest way a truly
        # empty workspace already does, via the has_own_established_progress
        # flag threaded into validate_plan below.
        has_own_established_progress = bool(
            prior_control_state and any(v == "completed" for v in prior_control_state.subtask_states.values())
        )

        # Authoritative plan correction is bounded and happens before any
        # implementation subtask can run. This keeps planned_files as a
        # hard write boundary while allowing a local Planner to correct a
        # structurally invalid late verification step. Exhaustion never
        # falls back to whole-goal legacy generation, because that would
        # discard the boundary we are trying to establish.
        deps._log_phase_banner("STRUCTURED PLANNING")
        planner_model = getattr(
            getattr(deps.workflow_engine.planner, "role_llm", None), "model", None,
        ) or getattr(getattr(getattr(deps.workflow_engine, "kernel", None), "config", None), "llm", None)
        if planner_model is not None and not isinstance(planner_model, str):
            planner_model = getattr(planner_model, "model", None)
        # The Planner agent's own output budget (agent_llms.planner.max_output_tokens,
        # else llm.planner_max_tokens; set at its construction).
        planner_token_cap = getattr(deps.workflow_engine.planner, "max_output_tokens", None)
        if not isinstance(planner_token_cap, int):
            planner_token_cap = getattr(
                getattr(getattr(getattr(deps.workflow_engine, "kernel", None), "config", None), "llm", None),
                "planner_max_tokens", None,
            )
        deps.logger.info("Generating validated EngineeringPlan (planner_model=%s)...", planner_model or "default")
        planning_repository_candidates = deps._authoritative_planner_extension_candidates(
            workspace_path, goal=goal,
        )
        # PRV-11 (2026-08-30): built ONCE, before any Planner call, from the
        # SAME bounded candidate set - never recomputed per repair attempt
        # (the candidate file set doesn't change across repair rounds, only
        # the plan does). Feeds BOTH the Planner's own prompt (below) and
        # find_missing_grounded_production_artifacts() (in the validation
        # loop below) from the identical resolved_edges, so a prompt hint
        # and a validation error can never disagree about what the graph
        # says. See build_planning_structural_evidence's own docstring for
        # the live incident this closes.
        structural_evidence_text, structural_resolved_edges = deps.build_planning_structural_evidence(
            workspace_path, planning_repository_candidates,
        )
        authoritative_planner_request = deps.build_authoritative_planner_request(
            goal,
            route_kind=route.kind,
            extension_candidates=planning_repository_candidates,
            repository_candidates=planning_repository_candidates,
            structural_evidence=structural_evidence_text,
        )
        # PRD-020: the user's original requirements, fixed from the goal
        # before any model sees it; subtasks map to them by id (lineage
        # only), and the terminal gate settles each from verifier evidence.
        # PRD-021: existing owners whose responsibility the goal names, from
        # the same bounded candidates and graph edges as the evidence above.
        try:
            owner_candidates = deps.grounded_owner_candidates(
                workspace_path, goal, candidate_paths=planning_repository_candidates,
                resolved_edges=structural_resolved_edges,
            )
        except Exception as error:
            deps.logger.warning("Grounded owner candidates unavailable: %s", error)
            owner_candidates = []
        owner_block = deps.owner_candidates_prompt_block(
            owner_candidates, where="set on the subtask that creates the file")
        if owner_block:
            authoritative_planner_request += "\n\n" + owner_block
        # Code Intelligence R1 slice 2: the ranked member/config candidates the
        # goal points at (symbol ids, kinds, signatures, channels; never a
        # body) - localization context for planning, never write authority:
        # every subtask's Developer context re-localizes from the subtask's own
        # goal and re-reads exact current bytes.
        try:
            localization = await deps.localization_candidates(
                deps.workflow_engine.kernel.config, workspace_path, goal)
        except deps.InferenceDeadlineError:
            raise
        except Exception as error:
            deps.logger.warning("Code Intelligence localization unavailable for planning: %s", error)
            localization = []
        localization_block = deps.render_localization_candidates(localization)
        if localization_block:
            authoritative_planner_request += "\n" + localization_block
        # PLAN-TARGET-GROUNDING-001 (b): where the goal's own names live (declaring supertype, packaging manifests,
        # delegating shims) - deterministic, provenance-labelled, bounded; evidence only.
        memory_dir = getattr(getattr(kernel_config, "paths", None), "memory", None)
        goal_facts_text = goal_facts_block(
            os.path.join(memory_dir, "dependency_graph.db") if isinstance(memory_dir, str) else None, workspace_path, goal)
        if goal_facts_text:
            authoritative_planner_request += goal_facts_text
        # GR-R1A: the operator's explicit, closed requirement contract when one
        # is bound; else the requirements derived from the goal.
        requirement_set = deps.requirement_set_for(goal, deps.bound_requirement_contract(deps.workflow_engine))
        authoritative_planner_request += "\n\n" + deps.requirements_prompt_block(requirement_set, instruction=(
            "Set requirement_ids on each subtask to the REQ ids it serves, using only these ids. "
            "Never drop, merge or reword a requirement: one no subtask serves stays open."
        ))
        # AUTH-GOAL-CONTAMINATION-001: retrieved reference text informs the
        # decomposition as fenced, untrusted context, appended after every
        # goal-derived section (initial and repair requests alike). Nothing
        # above, and no authority decision, ever reads it.
        # PROMPT-BUDGET-FIT-001A: fitted into the room each request (the
        # first one and every repair round) leaves after its system prompt
        # and every goal-derived section, trimmed at whole entries.
        raw_reference = legacy_kwargs.get("reference_context", "")
        # PLANNER-CONTEXT-FIT-001: what the request is built around ranks the
        # structural relationships when they do not all fit - grounded owners
        # first, then Code Intelligence candidates in rank order.
        structural_focus: Dict[str, int] = {candidate.path: 0 for candidate in owner_candidates}
        for candidate in localization:
            structural_focus.setdefault(candidate.path, len(structural_focus) + 1)
        structural_lines = structural_evidence_text.split("\n") if structural_evidence_text else []

        def planner_prompts(request: str) -> Optional[Any]:
            """``request`` fitted for each Planner candidate's own request
            (PROMPT-FIT-ROLE-CHAIN-001): its structural relationships first
            (PLANNER-CONTEXT-FIT-001; the goal, protocol, requirements, owner
            and Code Intelligence candidates are never trimmed), then the
            fenced reference into what is left - the untrusted reference gives
            way first. None when there is nothing to fit (the request goes out
            unchanged)."""
            fits_structure = bool(structural_lines) and request.count(structural_evidence_text) == 1
            if kernel is None or (not raw_reference.strip() and not fits_structure):
                return None  # no configuration to size against, or nothing to fit

            def build(capacity: Any, candidate: Any) -> str:
                text, structural = request, None
                if fits_structure:
                    before, after = request.split(structural_evidence_text)
                    fit, structural = deps.fit_structural_evidence(
                        capacity, (deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT, before, after), structural_lines,
                        structural_focus)
                    text = before + fit.value + after
                    if fit.value != structural_evidence_text:
                        structural.update(
                            capacity_tokens=capacity.tokens,
                            estimated_tokens_before=capacity.count(deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT)
                            + capacity.count(request),
                            estimated_tokens_after=capacity.count(deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT)
                            + capacity.count(text))
                    else:
                        structural = None
                reference = None
                if raw_reference.strip():
                    reference = deps.fit_reference_section(capacity, (deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT, text),
                                                      raw_reference)
                    text += reference.value
                    if not (reference.omitted or reference.builds > 1
                            or reference.value.count("\n[Source: ") < raw_reference.count("\n[Source: ")):
                        reference = None
                if structural is not None or reference is not None:
                    ledger.record_and_persist(
                        workspace_path, "context.request_fit", run_id=run_id,
                        request="structured_planner", model=deps.candidate_model(kernel.config, candidate),
                        reference=reference.to_dict() if reference is not None else None,
                        structural_evidence=structural,
                    )
                return text

            return deps.CandidatePrompts(kernel.config, deps.workflow_engine.planner, "planner", build,
                                    max_tokens_override=planner_token_cap if isinstance(planner_token_cap, int)
                                    else None)

        # The persisted planning diagnostics show the first candidate's request.
        authoritative_planner_prompts = planner_prompts(authoritative_planner_request)
        if authoritative_planner_prompts is not None:
            authoritative_planner_request = authoritative_planner_prompts.first()
        planning_repository_evidence = deps.bounded_repository_evidence(
            workspace_path, planning_repository_candidates,
        )
        deps.mark_run_stage(workspace_path, deps.RunLifecycle.PLANNING)
        plan_text = await deps.workflow_engine.planner.run(
            authoritative_planner_request,
            max_tokens_override=planner_token_cap,
            system_prompt_override=deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
            json_mode=True,
            candidate_prompt=authoritative_planner_prompts,
        )
        # MODEL-EVIDENCE-HARDENING-001: the model that answered (escalation
        # may have used a chain model); each response's typed outcome is
        # charged to it below.
        plan_response_model = deps.planner_response_model(deps.workflow_engine.planner)
        deps._log_phase_banner("PLAN VALIDATION")
        repair_attempts = 0
        # MA8 (PRV-05 run #8, 2026-08-28) - kriya/workflow/obligations.py.
        # One ledger for the whole run, created here (before
        # PLAN_STRUCTURAL_VALIDITY obligations start being produced) and
        # threaded unchanged into every subtask's run_generation_workflow()
        # call below (MIGRATION_COMPLETION/GOAL_SPEC_REQUIREMENT obligations)
        # and the terminal migration gate - "satisfied" means the same thing
        # everywhere in one run. See this module's own docstring for the two
        # concrete run #8 defects this closes.
        obligation_ledger = deps.ObligationLedger()
        deps.seed_requirement_obligations(obligation_ledger, requirement_set)
        # VERIFICATION-CONTRACT-003 (D1): structural non-claims need no closure.
        from kriya.workflow.contract_compilation import record_contract_dispositions, record_contract_non_claims

        record_contract_non_claims(obligation_ledger, requirement_set, verification_contract,
                                   source="workflow_controller.verification_contract")
        # BACKEND-READINESS-004 (D3): the operator's sealed dispositions, recorded once as audit.
        record_contract_dispositions(obligation_ledger, requirement_set, verification_contract,
                                     source="workflow_controller.verification_contract")
        # PRV-17 (2026-09-08, P7 efficiency finding) - kriya/workflow/
        # deterministic_failure_diagnostic.py. One store for the whole run,
        # deliberately separate from obligation_ledger above (see that
        # module's own docstring for why) - threaded unchanged into every
        # subtask's run_generation_workflow() call below so a plan-scope-
        # recovery re-invocation of the SAME subtask (a brand new
        # GenerationState) never loses a conclusion already proven here.
        deterministic_failure_diagnostics = deps.DeterministicFailureDiagnosticStore()
        # raw_plan is the Planner's own asserted output, before
        # canonicalize_planned_file_actions() derives create/modify from
        # real repository state - kept only so the resume-hash comparison
        # below (current_plan_hash = raw_plan.content_hash()) reflects what
        # the Planner actually said, not a Kriya-derived detail. A file a
        # completed subtask already wrote legitimately flips create->modify
        # between the original planning call and a later resume replan of
        # the SAME goal/intent; hashing the canonicalized plan would make
        # that legitimate, expected drift look like "a different plan,"
        # wrongly refusing an otherwise-valid resume (found via this
        # session's own regression sweep, not a live incident).
        raw_plan: Optional[EngineeringPlan] = None
        # Planner-convergence audit (2026-09-06, P2 run 7): the retained
        # semantic-repair baseline - the most recent FAILED attempt that was
        # never itself a strict regression against what came before it (see
        # _is_strict_regression's own docstring for the live incident this
        # closes). Seeded on the first failure, updated on every subsequent
        # non-regressed failure, and deliberately NEVER updated by a
        # strict-regression candidate - the next repair prompt, and the
        # terminal exhaustion report, are built from this retained state
        # instead of a regressed candidate's own worse one. This does not
        # change the repair_attempts bound (still fixed at 2) or refund a
        # slot spent on a rejected regression - it only changes which
        # state seeds the NEXT prompt/report.
        retained_plan_text: Optional[str] = None
        retained_errors: List[str] = []
        retained_reason_codes: Optional[frozenset] = None
        retained_validation_evidence: List[Dict[str, Any]] = []
        retained_invalid_subtask_ids: List[str] = []
        # PRV-17 (2026-09-07, P7): the errors that prompted the CURRENT
        # round's own candidate plan - i.e. what prompt_errors held on the
        # PREVIOUS loop iteration, right before this one's plan was
        # generated. Used only by _semantic_contract_regression_subtasks'
        # "unless directly implicated" exemption below; empty on the first
        # iteration (nothing prompted the initial plan), which correctly
        # exempts nothing.
        prior_prompt_errors: List[str] = []
        while True:
            errors: List[str] = []
            reason_codes: List[str] = []
            validation_evidence: List[Dict[str, Any]] = []
            invalid_subtask_ids: List[str] = []
            plan: Optional[EngineeringPlan] = None
            regressions_before = len(obligation_ledger.regressions)

            structured_output, parse_issue = deps.parse_planner_structured_output(plan_text)
            if structured_output is None:
                errors.append(f"structured plan parse failed: {parse_issue}")
                # PLANNER-ROBUST-001 (2026-09-19): shared classifier - see
                # kriya/workflow/planner_repair.py's own docstring. Behavior
                # unchanged; this is the same three-way string match that
                # was previously inlined here, now the single source both
                # this loop and the legacy run_generation_workflow() path
                # call, so identical parse_issue text can never classify
                # differently between the two.
                reason_codes.extend(deps.classify_structured_plan_parse_issue(parse_issue))
            else:
                raw_plan = deps.build_engineering_plan_from_planner_output(
                    structured_output, plan_id=run_id, kind=route.kind,
                )
                if raw_plan is None:
                    errors.append("structured output parsed but produced zero subtasks")
                    reason_codes.append("STRUCTURED_PLAN_EMPTY")
                else:
                    plan, _ = deps.canonicalize_planned_file_actions(raw_plan, workspace_path)
                    # PR-1: one owning work unit per mutable file - unordered
                    # same-file implementation units are merged when safe
                    # (deterministic; an unsafe split still reaches the
                    # validator's typed ownership conflict and bounded repair).
                    plan, coalesced = deps.coalesce_same_file_owners(plan)
                    # BACKEND-READINESS-V2: a MODIFY of a test the goal froze becomes a CREATE of a new sibling test
                    # (the Planner repeated the same existing path through every bounded repair).
                    plan, immutable_redirects = deps.redirect_immutable_test_edits(
                        plan, immutable_tests or (), lambda rel: os.path.exists(os.path.join(workspace_path, rel)),
                    )
                    # A verification unit's contract is what its own
                    # depends_on consumes (deterministic restatement).
                    plan, derived_contracts = deps.derive_verification_contracts(plan)
                    # PLAN-VERIFICATION-SCOPE-001: runtime verification the
                    # request does not require is removed when that is safe;
                    # what remains is refused by validate_plan below.
                    plan, verification_scope = deps.scope_verification_to_requirements(
                        plan, deps.goal_requires_runtime_behavior(goal),
                    )
                    if coalesced or immutable_redirects or derived_contracts or verification_scope:
                        ledger.record_and_persist(
                            workspace_path, "structured_plan_normalized", run_id=run_id,
                            repair_attempt=repair_attempts, merges=coalesced,
                            immutable_test_redirects=immutable_redirects,
                            derived_contracts=derived_contracts, verification_scope=verification_scope,
                        )
                    # TOOL-001 (2026-09-13): TOOL-tagged subtasks are no
                    # longer refused here - they now execute through the
                    # governed path in the per-subtask loop below (real
                    # kernel.registry lookup -> BaseTool.execute() -> the
                    # SAME ExecutionPolicy/TOOL-002/TOOL-003/SEC-005
                    # controls direct CLI tool execution already goes
                    # through). validate_plan() below still requires
                    # tool_name to resolve to a REGISTERED tool
                    # (available_tool_names) - an unknown tool_name is
                    # still rejected here, at plan-validation time, not
                    # silently deferred to execution.

                    validation = await deps.validate_plan(
                        plan, workspace_path=workspace_path,
                        available_tool_names=available_tool_names, plannable_tool_names=planned_tool_names,
                        route=route, triage_service=deps.workflow_engine.engineering_triage,
                        resuming_own_established_progress=has_own_established_progress,
                        require_model_planned_files=True,
                        require_semantic_contracts=True,
                        runtime_verification_required=deps.goal_requires_runtime_behavior(goal),
                        stack_contract=deps.derive_stack_contract(goal),
                        obligation_ledger=obligation_ledger, revision=repair_attempts,
                        known_requirement_ids=requirement_set.ids,
                        immutable_test_files=immutable_tests, frozen_files=frozen_files, documentation_paths=documentation_paths,
                        delegating_shims=shim_targets,
                    )
                    errors.extend(validation.errors)
                    reason_codes.extend(validation.reason_codes)
                    validation_evidence.extend(validation.evidence)
                    # PR-3: a subtask's stated Code Intelligence mutation
                    # targets must exist in the current view and agree with
                    # the files it owns (evidence, never authorization).
                    target_errors, target_codes, target_evidence = deps.check_plan_targets(
                        plan, workspace_path, lambda: os.path.join(
                            deps.workflow_engine.kernel.config.paths.memory, "dependency_graph.db"))
                    if target_evidence:
                        ledger.record_and_persist(
                            workspace_path, "structured_plan_targets", run_id=run_id,
                            repair_attempt=repair_attempts, targets=target_evidence,
                        )
                    errors.extend(target_errors)
                    reason_codes.extend(target_codes)
                    validation_evidence.extend(t for t in target_evidence if t["verdict"] != "consistent")
                    unbounded_model_subtasks = [
                        st.id for st in plan.subtasks
                        if st.execution_method == deps.ExecutionMethod.MODEL
                        and st.execution_role != deps.ExecutionRole.VERIFICATION
                        and not st.planned_files
                    ]
                    invalid_subtask_ids.extend(unbounded_model_subtasks)
                    if unbounded_model_subtasks and not any(
                        "declares no planned_files" in error for error in errors
                    ):
                        errors.append(
                            f"MODEL subtask(s) {unbounded_model_subtasks!r} declare no planned_files"
                        )
                        reason_codes.append("MODEL_SUBTASK_MISSING_PLANNED_FILES")

                    # PRV-11 (2026-08-30): bounded plan-completeness check -
                    # see find_missing_grounded_production_artifacts's own
                    # docstring for the live incident. Uses the SAME
                    # resolved_edges the Planner's own prompt was already
                    # built from above - never recomputed, never a second,
                    # possibly-disagreeing source of truth.
                    missing_artifacts = deps.find_missing_grounded_production_artifacts(
                        plan, structural_resolved_edges,
                        workspace_path=workspace_path, obligation_ledger=obligation_ledger,
                        revision=repair_attempts,
                    )
                    unowned_gaps = [g for g in missing_artifacts if g["reason"] == "unowned"]
                    miswired_gaps = [g for g in missing_artifacts if g["reason"] == "not_in_dependency_chain"]
                    semantic_gaps = [
                        g for g in missing_artifacts
                        if g["reason"] == "semantic_provider_mismatch"
                    ]
                    if unowned_gaps:
                        errors.append(
                            "grounded structural evidence shows test file(s) referencing a "
                            "production artifact no subtask owns: " + "; ".join(
                                f"{gap['test_file']} (subtask {gap['consumer_subtask']!r}) "
                                f"references {gap['missing_production_artifact']}"
                                for gap in unowned_gaps
                            )
                        )
                        reason_codes.append("MISSING_GROUNDED_PRODUCTION_ARTIFACT")
                    if miswired_gaps:
                        errors.append(
                            "grounded structural evidence shows test file(s) whose own requires/"
                            "depends_on skip past the real intermediate producer: " + "; ".join(
                                f"{gap['test_file']} (subtask {gap['consumer_subtask']!r}) "
                                f"references {gap['missing_production_artifact']} (owned by "
                                f"subtask {gap['owning_subtask']!r}, which is not in this test's "
                                "own depends_on chain)"
                                for gap in miswired_gaps
                            )
                        )
                        reason_codes.append("MISWIRED_GROUNDED_DEPENDENCY_EDGE")
                    if semantic_gaps:
                        errors.append(
                            "grounded structural evidence shows test file(s) whose requires do "
                            "not resolve to the subtask owning the referenced production artifact: "
                            + "; ".join(
                                f"{gap['test_file']} (subtask {gap['consumer_subtask']!r}) "
                                f"references {gap['missing_production_artifact']} (owned by subtask "
                                f"{gap['owning_subtask']!r}, provides={gap['owner_provides']!r}, "
                                f"test requires={gap['test_requires']!r})"
                                for gap in semantic_gaps
                            )
                        )
                        reason_codes.append("GROUNDED_SEMANTIC_PROVIDER_MISMATCH")

                    # PLAN-EDIT-FEASIBILITY-001: an edit the executor's own
                    # edit protocol can be shown unable to run is a typed
                    # contradiction for the existing bounded plan repair (the
                    # Planner decides the revision; nothing is dropped or
                    # rewritten here, no budget is added).
                    edit_feasibility = await deps.evaluate_plan_edit_feasibility(
                        getattr(getattr(deps.workflow_engine, "kernel", None), "config", None),
                        plan, goal, workspace_path,
                    )
                    ledger.record_and_persist(
                        workspace_path, "structured_plan_edit_feasibility", run_id=run_id,
                        repair_attempt=repair_attempts, results=[item.to_dict() for item in edit_feasibility],
                    )
                    for infeasible in deps.infeasible_results(edit_feasibility):
                        errors.append(deps.repair_error_text(infeasible))
                        invalid_subtask_ids.append(infeasible.subtask_id)
                        validation_evidence.append(infeasible.to_dict())
                        reason_codes.append("PLANNED_EDIT_INFEASIBLE")

            reason_codes = list(dict.fromkeys(reason_codes))
            invalid_subtask_ids = list(dict.fromkeys(invalid_subtask_ids))
            # PRV-17 (2026-09-07, P7): which subtask(s), if any, this round's
            # own candidate plan silently regressed a previously-validated
            # requires/provides fact for, unrelated to what this round was
            # actually asked to fix - see _semantic_contract_regression_
            # subtasks' own docstring. Computed unconditionally (harmless
            # when plan is None or nothing regressed - both yield an empty
            # list) so it's available below regardless of which branch set
            # errors/reason_codes.
            semantic_contract_regression_subtasks = deps._semantic_contract_regression_subtasks(
                obligation_ledger, regressions_before, deps._subtask_ids_mentioned(prior_prompt_errors),
            )
            if semantic_contract_regression_subtasks:
                # PRV-17 (2026-09-07, P7): folded into errors/reason_codes
                # (rather than a separate accept/reject branch) so every
                # existing downstream mechanism this loop already has - the
                # "plan is not None and not errors" acceptance gate right
                # below, is_strict_regression's own reason-code-set
                # comparison, the next repair prompt's targeted_correction
                # text, the terminal exhaustion report - uniformly treats
                # this candidate as still-invalid, with no separate
                # accept/reject path to keep in sync.
                errors.append(
                    "plan repair silently regressed previously-validated requires/provides "
                    f"for unrelated subtask(s) {semantic_contract_regression_subtasks!r} while "
                    "fixing something else - restore their previously-validated requires/"
                    "provides entries exactly as declared before this repair round"
                )
                reason_codes.append("SEMANTIC_CONTRACT_REGRESSION_REJECTED")
                reason_codes = list(dict.fromkeys(reason_codes))
            # PRV-17 (2026-09-08, P5 audit): sibling check to the requires/
            # provides one above, same rationale, different identity (a
            # (source, target) file pair rather than a subtask id) - see
            # _preserved_reference_regressions' own docstring for the P5
            # oscillation this closes.
            preserved_reference_regressions = deps._preserved_reference_regressions(
                obligation_ledger, regressions_before,
                deps._preserved_reference_pairs_mentioned(prior_prompt_errors),
            )
            if preserved_reference_regressions:
                errors.append(
                    "plan repair silently regressed previously-validated preserved "
                    f"reference(s) {preserved_reference_regressions!r} while fixing something "
                    "else - restore these preserved_references declarations exactly as declared "
                    "before this repair round"
                )
                reason_codes.append("PRESERVED_REFERENCE_REGRESSION_REJECTED")
                reason_codes = list(dict.fromkeys(reason_codes))
            # MODEL-EVIDENCE-HARDENING-001: one typed outcome per Planner
            # response (valid / malformed / structured validation failure /
            # policy rejection / other), into the role metrics.
            deps.record_planner_outcome(
                deps.workflow_engine.planner,
                deps.classify_planner_outcome(reason_codes, valid=plan is not None and not errors),
                model=plan_response_model,
            )
            if plan is not None and not errors:
                approved_stack_contract = deps.derive_stack_contract(goal)
                deps.log_stack_contract_boundary("plan", approved_stack_contract, None)
                try:
                    deps.persist_planning_attempt_diagnostic(
                        workspace_path, run_id,
                        attempt=repair_attempts,
                        planner_request=authoritative_planner_request,
                        planner_system_prompt=deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
                        raw_plan_response=plan_text,
                        plan=plan,
                        validation_errors=errors,
                        reason_codes=reason_codes,
                        repository_evidence=planning_repository_evidence,
                        repair_prompt=None,
                    )
                except Exception as diagnostic_error:
                    deps.logger.warning(
                        "Failed to persist local structured-planning diagnostics for run %r "
                        "attempt %d: %s", run_id, repair_attempts, diagnostic_error,
                    )
                ledger.record_and_persist(
                    workspace_path, "structured_plan_validation", run_id=run_id,
                    valid=True, repair_attempts=repair_attempts,
                )
                break

            repair_prompt = None
            repair_prompts = None
            # Planner-convergence audit (2026-09-06, P2 run 7): classify
            # THIS attempt against the retained baseline before deciding
            # what seeds the next repair prompt (and, on exhaustion, the
            # terminal report) - see _is_strict_regression's own docstring.
            # Per-attempt forensic logging below (ledger.record_and_persist/
            # logger.warning/persist_planning_attempt_diagnostic) still
            # records what THIS attempt actually produced, unmodified -
            # only the forward-looking prompt state and the terminal
            # exception are redirected to the retained baseline.
            current_reason_code_set = frozenset(reason_codes)
            is_strict_regression = deps._is_strict_regression(retained_reason_codes, current_reason_code_set)
            # PRV-17 (2026-09-07, P7): a candidate that silently regressed an
            # unimplicated subtask's requires/provides is rejected the same
            # way a strict reason-code-set regression already is - reusing
            # the identical retained-baseline redirect below, never a second
            # rejection path. _is_strict_regression's own semantics (and the
            # `is_strict_regression` value fed to it) are untouched by this
            # OR - see _semantic_contract_regression_subtasks' own docstring.
            treat_as_regression = (
                is_strict_regression
                or bool(semantic_contract_regression_subtasks)
                or bool(preserved_reference_regressions)
            )
            if treat_as_regression:
                prompt_plan_text = retained_plan_text
                prompt_errors = retained_errors
                prompt_reason_codes = list(retained_reason_codes)
                prompt_validation_evidence = retained_validation_evidence
            else:
                prompt_plan_text = plan_text
                prompt_errors = errors
                prompt_reason_codes = reason_codes
                prompt_validation_evidence = validation_evidence
                retained_plan_text = plan_text
                retained_errors = errors
                retained_reason_codes = current_reason_code_set
                retained_validation_evidence = validation_evidence
                retained_invalid_subtask_ids = invalid_subtask_ids
            # PRV-17 (2026-09-07, P7): captured for the NEXT loop iteration's
            # own _subtask_ids_mentioned(prior_prompt_errors) call - see that
            # variable's own initialization comment above the while loop.
            prior_prompt_errors = prompt_errors
            if repair_attempts < deps.STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS:
                # MA8: everything PLAN_STRUCTURAL_VALIDITY currently reports
                # SATISFIED (as of the validate_plan() call just above) must
                # survive the next draft - see build_structured_plan_repair_
                # prompt's own docstring for the run #8 incident this closes,
                # and relevant_for_preservation's own docstring for why this
                # is unconditional on `kind` rather than correlated to what
                # else is currently violated (PRV-11, 2026-08-31).
                #
                # PRESERVED_REFERENCE (PRV-11 preservation extension,
                # 2026-09-07, Production Validation P5): the identical
                # reinforcement need at a finer resolution than reason codes
                # can see - MISSING_GROUNDED_PRODUCTION_ARTIFACT stays the
                # same reason code whether ONE grounded target is still
                # unowned or a DIFFERENT one is, so _is_strict_regression's
                # own reason-code-set comparison cannot detect a repair that
                # silently drops one already-correct preserved_references
                # entry while adding another (found live: P5's PetTests.java
                # needed both BaseEntity.java and Visit.java preserved
                # simultaneously - two rounds each declared only one,
                # alternating, never both together). Each line below
                # self-attributes its own (source, target) pair explicitly,
                # so one source's already-validated preservation can never
                # be misread as applying to a different source - no new
                # ledger, no new prompt section, this is the exact same
                # must_preserve mechanism PLAN_STRUCTURAL_VALIDITY already
                # uses, just also fed from this obligation kind.
                must_preserve = (
                    [
                        f"{rec.description} (evidence: {json.dumps(rec.evidence, default=str)})"
                        for rec in obligation_ledger.relevant_for_preservation(
                            deps.ObligationKind.PLAN_STRUCTURAL_VALIDITY,
                        )
                    ]
                    + deps._preserved_reference_must_preserve_lines(obligation_ledger)
                    + deps._semantic_contract_must_preserve_lines(obligation_ledger)
                )
                repair_prompt = deps.build_structured_plan_repair_prompt(
                    goal, prompt_plan_text, prompt_errors, prompt_reason_codes, repair_attempts + 1,
                    route_kind=route.kind,
                    extension_candidates=planning_repository_candidates,
                    repository_candidates=planning_repository_candidates,
                    must_preserve=must_preserve,
                    validation_evidence=prompt_validation_evidence,
                    available_tool_names=planned_tool_names,
                )
                repair_prompts = planner_prompts(repair_prompt)
                if repair_prompts is not None:
                    repair_prompt = repair_prompts.first()
            try:
                deps.persist_planning_attempt_diagnostic(
                    workspace_path, run_id,
                    attempt=repair_attempts,
                    planner_request=authoritative_planner_request,
                    planner_system_prompt=deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
                    raw_plan_response=plan_text,
                    plan=plan,
                    validation_errors=errors,
                    reason_codes=reason_codes,
                    repository_evidence=planning_repository_evidence,
                    repair_prompt=repair_prompt,
                )
            except Exception as diagnostic_error:
                deps.logger.warning(
                    "Failed to persist local structured-planning diagnostics for run %r "
                    "attempt %d: %s", run_id, repair_attempts, diagnostic_error,
                )

            ledger.record_and_persist(
                workspace_path, "structured_plan_validation", run_id=run_id,
                valid=False, repair_attempts=repair_attempts,
                reason_codes=reason_codes, invalid_subtask_ids=invalid_subtask_ids,
                error_count=len(errors),
            )
            deps.logger.warning(
                "WorkflowController enforce run %r: structured plan validation failed "
                "(attempt=%d, reason_codes=%s, invalid_subtask_ids=%s)",
                run_id, repair_attempts, reason_codes, invalid_subtask_ids,
            )
            if repair_attempts >= deps.STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS:
                # Planner-convergence audit (2026-09-06, P2 run 7): the
                # TERMINAL report must reflect the retained (non-regressed)
                # baseline, not a final attempt that was itself rejected as
                # a strict regression - reporting the worse candidate's
                # state here would misrepresent APPLICATION_RUNTIME_OWNER_
                # MISSING as though it were part of the best reachable
                # state, when the retained baseline already resolved it.
                final_reason_codes = (
                    list(retained_reason_codes) if treat_as_regression else list(reason_codes)
                )
                final_invalid_subtask_ids = (
                    retained_invalid_subtask_ids if treat_as_regression else invalid_subtask_ids
                )
                final_reason_codes.append("STRUCTURED_PLAN_REPAIR_EXHAUSTED")
                # MA8 (PRV-05 run #8): distinguish "kept oscillating between
                # constraints" from "just never converged" - both are
                # reported, never used to raise the repair-attempt bound
                # itself (that stays fixed at 2, per this fix's own scope).
                oscillating = obligation_ledger.oscillating_ids(deps.ObligationKind.PLAN_STRUCTURAL_VALIDITY)
                if oscillating:
                    final_reason_codes.append("PLAN_REPAIR_OSCILLATION")
                    deps.logger.error(
                        "WorkflowController enforce run %r: plan repair OSCILLATED on "
                        "obligation(s) %s - full revision history: %s",
                        run_id, oscillating,
                        {oid: [(r.revision, r.status.value) for r in obligation_ledger.history(oid)]
                         for oid in oscillating},
                    )
                else:
                    final_reason_codes.append("PLAN_REPAIR_NON_CONVERGENCE")
                raise deps._UnsafeStructuredPlan(
                    "structured plan remained unsafe after two bounded repair attempts",
                    reason_codes=list(dict.fromkeys(final_reason_codes)),
                    invalid_subtask_ids=final_invalid_subtask_ids,
                    repair_attempts=repair_attempts,
                )

            repair_attempts += 1
            assert repair_prompt is not None
            ledger.record_and_persist(
                workspace_path, "structured_plan_repair_requested", run_id=run_id,
                repair_attempt=repair_attempts, reason_codes=reason_codes,
                invalid_subtask_ids=invalid_subtask_ids,
            )
            plan_text = await deps.workflow_engine.planner.run(
                repair_prompt,
                max_tokens_override=planner_token_cap,
                system_prompt_override=deps.AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
                json_mode=True,
                candidate_prompt=repair_prompts,
            )
            plan_response_model = deps.planner_response_model(deps.workflow_engine.planner)

        assert plan is not None
        deps.logger.info(
            "Plan validation: PASSED (%d subtasks, %d dependency edges, %d planned files, %d global invariants)",
            len(plan.subtasks), sum(len(st.depends_on) for st in plan.subtasks),
            sum(len(st.planned_files) for st in plan.subtasks), len(plan.global_invariants),
        )
        for stage in plan.subtasks:
            deps.logger.info(
                "  %s files=%s depends_on=%s",
                stage.id.upper(), [pf.path for pf in stage.planned_files], stage.depends_on,
            )
        deps.record_plan_created(ledger, plan)
        assert raw_plan is not None
        current_plan_hash = raw_plan.content_hash()

        execution_context = await deps._build_context(
            goal, plan, workspace_path, route, control_context, control_state,
        )

        resumed_subtask_states: Dict[str, str] = {}
        # PRD-008: the structured record of what this resume reused (logged,
        # annotated on the RunRecord and returned), one per resume request.
        enforce_resume_decision: Optional[Dict[str, Any]] = None
        if prior_control_state is not None:
            enforce_resume_decision = {
                "source": "control_state",
                "prior_control_state_run_id": prior_control_state.run_id,
                "subtask_completion_scope": prior_control_state.subtask_completion_scope,
                "reused_subtasks": [],
                "reason": None,
            }
            current_approval_digest = getattr(deps.bound_approval(deps.workflow_engine), "digest", None)
            current_contract_digest = getattr(deps.bound_requirement_contract(deps.workflow_engine), "digest", None)
            if prior_control_state.requirement_contract_digest != current_contract_digest:
                # GR-R1A: subtasks recorded under another (or no) explicit
                # requirement contract are never reused under this one.
                enforce_resume_decision["reason"] = "REQUIREMENT_CONTRACT_CHANGED"
                deps.logger.warning(
                    f"WorkflowController enforce run {run_id!r}: refusing subtask resume - the requirement "
                    "contract differs from the one the recorded subtasks ran under. Starting the plan fresh."
                )
            elif prior_control_state.acceptance_approval_digest != current_approval_digest:
                # B3: human acceptance authority is bound before generation; a
                # candidate produced under another (or no) approval is never
                # reused under this one - the plan runs fresh.
                enforce_resume_decision["reason"] = "ACCEPTANCE_APPROVAL_CHANGED"
                deps.logger.warning(
                    f"WorkflowController enforce run {run_id!r}: refusing subtask resume - the acceptance "
                    "approval differs from the one the recorded subtasks ran under. Starting the plan fresh."
                )
            elif prior_control_state.verification_contract_digest != verification_contract.digest:
                # VERIFICATION-CONTRACT-003: the sealed contract (scopes, closers,
                # bound authorities) is authority the subtasks ran under; a state
                # recorded under another one - or before contracts existed (None)
                # - is never reused. Fail closed.
                enforce_resume_decision["reason"] = "VERIFICATION_CONTRACT_CHANGED"
                deps.logger.warning(
                    f"WorkflowController enforce run {run_id!r}: refusing subtask resume - the verification "
                    "contract differs from the one the recorded subtasks ran under. Starting the plan fresh."
                )
            elif prior_control_state.current_plan_hash != current_plan_hash:
                enforce_resume_decision["reason"] = "PLAN_CHANGED"
                deps.logger.warning(
                    f"WorkflowController enforce run {run_id!r}: refusing subtask resume - the "
                    "freshly re-planned goal no longer matches the plan these subtask states "
                    "were recorded against. Starting the plan fresh."
                )
            elif prior_control_state.subtask_completion_scope != "workspace":
                # A sandbox-only completion was discarded with its sandbox
                # (a failed or interrupted plan never commits), so it proves
                # nothing about the real workspace; a legacy state cannot say
                # where its completions live.
                enforce_resume_decision["reason"] = "COMPLETIONS_NOT_IN_WORKSPACE"
                deps.logger.warning(
                    f"WorkflowController enforce run {run_id!r}: reusing no subtask - the recorded "
                    "completions were never applied to the real workspace (scope "
                    f"{prior_control_state.subtask_completion_scope or 'unrecorded'!s}). "
                    "Starting the plan fresh."
                )
            else:
                # PRD-008: the record the prior ControlState was derived
                # under must still say its commit state is settled; a
                # record that is gone leaves the state's provenance unknown.
                prior_run_id = deps.load_control_state_run_reference(workspace_path)
                prior_run_record = None
                prior_run_record_error = None
                prior_run_record_missing = None
                if prior_run_id is not None:
                    try:
                        prior_run_record = deps.load_run_record(workspace_path, prior_run_id)
                    except (deps.UnreadableRunRecordError, ValueError) as error:
                        prior_run_record_error = str(error)
                    else:
                        if prior_run_record is None:
                            prior_run_record_missing = prior_run_id
                resume_check = deps.validate_resume_against_reality(
                    run_record=prior_run_record,
                    run_record_error=prior_run_record_error,
                    run_record_missing=prior_run_record_missing,
                    checkpoint_data={
                        "base_commit": prior_control_state.base_commit,
                        "tree_hash": prior_control_state.tree_hash,
                        # STATE-001 (2026-09-14): required for a real
                        # content-drift signal - tree_hash alone cannot see
                        # any of THIS run's own uncommitted subtask writes,
                        # since subtask writes are plain file writes, never
                        # a git commit between subtasks.
                        "workspace_content_hash": prior_control_state.workspace_content_hash,
                    },
                    workspace_path=workspace_path,
                )
                if resume_check.status != deps.ResumeStatus.OK:
                    enforce_resume_decision["reason"] = "RESUME_VALIDATION_FAILED"
                    enforce_resume_decision["decisions"] = [item.to_dict() for item in resume_check.decisions]
                    deps.logger.warning(
                        f"WorkflowController enforce run {run_id!r}: refusing subtask resume - "
                        f"workspace drift detected ({'; '.join(resume_check.mismatches)}). "
                        "Starting the plan fresh."
                    )
                else:
                    resumed_subtask_states, skipped_tool_subtasks = deps.exclude_tool_subtasks_from_resume(
                        plan, dict(prior_control_state.subtask_states),
                    )
                    if skipped_tool_subtasks:
                        deps.logger.warning(
                            f"WorkflowController enforce run {run_id!r}: TOOL-tagged subtask(s) "
                            f"{skipped_tool_subtasks!r} were recorded completed by an earlier "
                            "interrupted run, but TOOL subtasks are never resume-skipped "
                            "(authority must always revalidate - see exclude_tool_subtasks_from_"
                            "resume's own docstring) - they will re-execute for real."
                        )
                    enforce_resume_decision["reused_subtasks"] = sorted(
                        subtask_id for subtask_id, status in resumed_subtask_states.items()
                        if status == "completed"
                    )
                    deps.logger.info(
                        f"WorkflowController enforce run {run_id!r}: resuming - "
                        f"{sum(1 for v in resumed_subtask_states.values() if v == 'completed')} "
                        "subtask(s) already completed will be skipped."
                    )
            deps.logger.info(
                "WorkflowController enforce run %r: resume decision %s",
                run_id, json.dumps(enforce_resume_decision, sort_keys=True),
            )
            annotation_error = deps.annotate_run(workspace_path, resume_decision=enforce_resume_decision)
            if annotation_error:
                deps.logger.warning(
                    f"WorkflowController enforce run {run_id!r}: could not record the resume "
                    f"decision on the run record: {annotation_error}"
                )

        # A refused resume (either branch above) leaves resumed_subtask_states
        # empty while prior_control_state still holds real record of an
        # earlier, now-abandoned plan's completed work - the exact gap that
        # let ProtocolMain.java linger in the real workspace live, 2026-08-25
        # (protocol_encoder_java): see compute_abandoned_plan_files()/
        # quarantine_abandoned_plan_files()'s own docstrings above for the
        # full incident and design. Best-effort/non-fatal, matching this
        # whole method's "a bookkeeping/cleanup convenience never fails the
        # real run" convention - deliberately scoped to only the resume-was-
        # REQUESTED-but-refused case (prior_control_state is only ever loaded
        # at all when the caller opted into resume), not a broader always-on
        # scan of the workspace for anything Kriya might have ever written.
        # PRD-008: only completions that reached the real workspace left
        # files there; a sandbox-only record may name paths the user's own
        # files occupy, which must never be moved.
        if (
            prior_control_state is not None and not resumed_subtask_states
            and prior_control_state.subtask_completion_scope == "workspace"
        ):
            try:
                new_plan_files = {pf.path for st in plan.subtasks for pf in st.planned_files}
                abandoned_files = deps.compute_abandoned_plan_files(
                    prior_control_state.subtask_states,
                    prior_control_state.subtask_written_files,
                    new_plan_files,
                )
                quarantined = deps.quarantine_abandoned_plan_files(
                    workspace_path, abandoned_files, prior_control_state.run_id,
                )
                if quarantined:
                    deps.logger.warning(
                        f"WorkflowController enforce run {run_id!r}: the abandoned plan's own "
                        f"completed subtask(s) had already applied {quarantined!r} to the real "
                        "workspace - moved to .kriya/abandoned_plan_files/ since no subtask in "
                        "the freshly re-planned goal references them anymore."
                    )
            except Exception as e:
                deps.logger.warning(f"WorkflowController enforce run {run_id!r}: abandoned-plan-file cleanup failed (non-fatal): {e}")

        # base_commit/tree_hash are refreshed here for EVERY route kind, not
        # just REFACTOR (_attach_refactor_baseline above only sets them for
        # that one kind) - subtask resume needs a real workspace-drift
        # signal regardless of what kind of change this plan is, and these
        # are the same real git-derived fields/functions that method
        # already uses, just given a second, route-independent purpose.
        control_state = control_state.with_updates(
            # Reused completions are in the real workspace; the scope becomes
            # "candidate" once a separate plan sandbox is created below.
            subtask_completion_scope="workspace" if resumed_subtask_states else None,
            acceptance_approval_digest=getattr(deps.bound_approval(deps.workflow_engine), "digest", None),
            requirement_contract_digest=getattr(deps.bound_requirement_contract(deps.workflow_engine), "digest", None),
            verification_contract_digest=verification_contract.digest,
            current_plan_hash=current_plan_hash, subtask_states=dict(resumed_subtask_states),
            base_commit=deps.compute_base_commit(workspace_path), tree_hash=deps.compute_tree_hash(workspace_path),
            workspace_content_hash=deps.compute_workspace_content_hash(workspace_path),
        )

        order = deps.topological_subtask_order(plan)
        total = len(order)
        approved_stage_states = {
            subtask_id: (
                "completed" if resumed_subtask_states.get(subtask_id) == "completed" else "pending"
            )
            for subtask_id in order
        }
        control_state = control_state.with_updates(subtask_states=dict(approved_stage_states))
        # Persist the complete validated plan before any implementation can
        # begin. ControlState remains the compact resume index; this owned
        # document is the durable source for what was approved and how each
        # ordered execution stage progressed.
        deps.annotate_run(workspace_path, approved_plan_hash=current_plan_hash)
        deps.save_approved_plan(
            workspace_path, plan.plan_id,
            deps.build_approved_plan_document(
                plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                stage_states=approved_stage_states, lifecycle_state="approved",
            ),
        )
        control_state = _persist_control_state(control_state)
        # PRD-021: files the approved plan still creates although an existing
        # owner's responsibility is named - GROUNDED findings, recorded and
        # shown to the subtask that creates the file; never a denial.
        planned_actions = [(st, pf) for st in plan.subtasks for pf in st.planned_files]
        ownership_findings = deps.settle_findings(
            deps.find_ownership_findings(
                [(pf.path, st.id, st.description) for st, pf in planned_actions
                 if pf.action == deps.FileAction.CREATE],
                owner_candidates,
                touched_paths=[pf.path for _, pf in planned_actions if pf.action == deps.FileAction.MODIFY],
            ),
            goal=goal,
            justifications={path: reason for st in plan.subtasks
                            for path, reason in st.ownership_justification.items()},
            removed_paths=[pf.path for _, pf in planned_actions if pf.action == deps.FileAction.DELETE],
        )
        deps.record_findings(obligation_ledger, ownership_findings, revision=repair_attempts,
                        source="workflow_controller.approved_plan")
        for finding in ownership_findings:
            deps.logger.warning("Grounded ownership finding (%s): %s", finding.status, finding.to_dict())
        # The authoritative plan is one transaction. Individual subtask
        # workflows may apply only into this plan-level sandbox; the user
        # workspace remains unchanged until every subtask and any bounded
        # owner-recovery pass succeeds.
        planned_paths = sorted({
            planned_file.path
            for planned_subtask in plan.subtasks
            for planned_file in planned_subtask.planned_files
        })
        original_plan_revisions = {
            path: deps.read_file_revision(os.path.join(workspace_path, path))
            for path in planned_paths
        }
        # Happens before any subtask has run - zero real side effects exist
        # yet, so a bootstrap failure here (git missing, read-only FS, disk
        # quota) fits _StructuredPlanUnavailable's own established "pre-
        # execution, safe to fail closed" contract exactly (see that class's
        # docstring) - reused here rather than letting create_git_worktree's
        # RuntimeError/ValueError propagate uncaught and crash the whole
        # enforce run; the caller already converts it into a clean
        # needs_review WorkflowResult.
        try:
            plan_workspace_path = deps.create_git_worktree(workspace_path)
        except (deps.WorktreeSyncError, RepositoryContentFilterRefused):
            # FILE-INTEGRITY-CONTRACT-001: a sandbox that does not hold the
            # workspace's exact bytes is a typed stop (execute() reports its
            # reason code), never a planning problem; S19 Q1: so is a
            # repository-defined content filter.
            raise
        except Exception as e:
            raise deps._StructuredPlanUnavailable(
                f"failed to create isolated plan-level worktree sandbox: {e}"
            ) from e
        # PRD-006: subtasks run the (coordinated) engine against this
        # candidate; it is part of this run, authorized explicitly.
        if plan_workspace_path != workspace_path:
            deps.authorize_candidate_workspace(plan_workspace_path)
        # PRD-008: every completion recorded from here on lives where the
        # subtasks write; in a separate sandbox that is discarded unless the
        # whole plan commits, so it is not resumable until then.
        control_state = control_state.with_updates(
            subtask_completion_scope="workspace" if plan_workspace_path == workspace_path else "candidate",
        )
        deps.mark_run_stage(workspace_path, deps.RunLifecycle.CANDIDATE)
        # Resolved ONCE, here, against workspace_path - the real, immutable
        # PRE-mutation baseline, before plan_workspace_path accumulates any
        # subtask's committed writes - and reused unchanged by every bounded
        # subtask's own attempt AND the terminal global gate below. Found
        # live, PRV-05 run 6 (2026-08-28): re-resolving source/target
        # identity from CURRENT (progressively-mutated) workspace state at
        # each call site is itself the defect (see kriya/workflow/
        # migration.py's own docstring) - a migration obligation is a
        # goal-level invariant, not something to rediscover at every point
        # in time. Wrapped in its own try/except (not just the terminal
        # gate's) - an internal bug HERE must also fail closed, at the
        # terminal gate below, rather than propagate as an unhandled
        # exception that crashes the whole enforce run.
        try:
            migration_resolution = deps.resolve_migration_resolution(goal, workspace_path)
        except Exception as e:
            migration_resolution = deps.MigrationResolution(
                deps.MigrationResolutionStatus.INDETERMINATE,
                reason=f"migration resolution itself raised ({type(e).__name__}: {e})",
            )
        established_file_context: Dict[str, str] = {}
        # LR-R1-P5: who established each artifact, at which bytes (integration evidence).
        established_provenance = deps.EstablishedProvenance(plan_workspace_path)
        subtask_results: List[SubtaskResult] = []
        subtask_call_results: List[Dict[str, Any]] = []
        knowledge_gap_break: Optional[Dict[str, Any]] = None
        # MA8.1 completion (2026-08-29 v2 design review): "Requirement-
        # scoped repeated owner recovery." A live PRV-06 run proved a bare
        # owner-id set (reopen each owner at most once per run) is the
        # wrong abstraction - it blocked a SECOND, genuinely different
        # grounded requirement on the same owner (s3/MainApplication.java)
        # purely because s3 had already been reopened once for a DIFFERENT
        # requirement earlier in the same run. Replaced with a per-
        # (originating subtask, owner, required files) generation counter -
        # incremented once a full owner-recovery-and-downstream-retry cycle
        # completes for that exact tuple (see the increment site near
        # execution_role="consumer_retry" below), never per raw occurrence.
        # A requirement recurring WITHIN the current generation is sticky
        # (same obligation id, same MUST_FIX - see _get_or_create_cross_
        # owner_obligation); a requirement surfacing AFTER a generation has
        # completed gets a fresh id and is eligible for a new reopening.
        # Boundedness is preserved WITHOUT a new retry engine or budget: a
        # given requirement id's own recovery attempts are capped by
        # reusing _MAX_PLAN_SCOPE_REVISION_ATTEMPTS (the same constant the
        # sibling DAG-mutation revision loop already uses), checked via the
        # ledger's own history() - no new counter needed for that part.
        recovery_generation_by_key: Dict[Tuple[str, str, Tuple[str, ...]], int] = {}
        plan_recovery_events: List[Dict[str, Any]] = []
        # Recovery Execution Contract (PRV-06, 2026-08-29) - a live incident
        # this closes: an owner's OWN regenerated candidate can be byte-
        # identical to what it already had, pass the owner's own local
        # gates (nothing there checks the cross-owner requirement itself),
        # and still get logged OWNER_RECOVERY_COMPLETED passed=True -
        # burning a full consumer-retry cycle (and, once the consumer fails
        # again, an entire FRESH generation) rediscovering a defect that
        # was never actually touched. Keyed by (owner, required files) -
        # deliberately NOT by generation/requirement_id, which change every
        # cycle by design - so it survives across generations for the SAME
        # underlying recurring problem. See the no-progress check at this
        # dict's own use site for the full reasoning.
        recovery_candidate_fingerprints: Dict[Tuple[str, Tuple[str, ...]], Tuple[str, ...]] = {}
        # General Obligation-Centric Recovery Execution (2026-08-30): one
        # RecoveryExecutionPlan "generation" counter per originating
        # subtask - mirrors recovery_generation_by_key's own per-cycle
        # advance, but scoped to the WHOLE plan (which may span several
        # owner-groups) rather than one (owner, files) pair. Feeds
        # build_recovery_execution_plan's own plan_generation argument, so
        # each fresh RecoveryExecutionPlan.id and its fingerprint keys
        # (rekeyed to include the plan id - see the fingerprint dict above's
        # own docstring and the implementation spec's §9) stay scoped to the
        # active recovery cycle, never bleeding into a later, genuinely new
        # requirement on the same subtask.
        recovery_plan_generation_by_subtask: Dict[str, int] = {}

        async def _invoke_bounded_subtask(
            target: Subtask,
            target_position: int,
            *,
            recovery_context: str = "",
            execution_role: str = "planned",
            grounded_locations: Sequence[Mapping[str, Any]] = (),
        ) -> Dict[str, Any]:
            target_goal = deps.build_subtask_goal_text(
                target, target_position, total, plan=plan, grounding_goal=goal,
            )
            target_files = [pf.path for pf in target.planned_files]
            target_context = deps.project_for_subtask(execution_context, target)
            # GRAPHIFY-OVERSIZE-REQUEST-001: a planned file's full current
            # source is not rendered into supplementary_context (mandatory
            # Developer text no request fit can shrink - a 331 KB planned
            # file made every request unsendable). Its path goes to the
            # attempt, which renders it through the budgeted optional
            # planned_source section instead.
            planned_source_files, rendered_context = deps.split_planned_source(target_context)
            target_context_text = deps.subtask_executor._render_context_package(rendered_context)
            kernel = getattr(deps.workflow_engine, "kernel", None)
            config = getattr(kernel, "config", None)
            process_profiles = getattr(config, "process_profiles", None)
            # PRV-11 (2026-08-30): which subtasks have ALREADY finished
            # SUCCESSFULLY as of THIS call, read live off the same
            # approved_stage_states this loop already maintains - feeds
            # resolve_future_owner_verification_deferral()'s own "has the
            # future owner not yet executed" check (kriya/workflow/
            # workflow.py). Recomputed fresh on every call, never cached -
            # approved_stage_states is mutated in place as each subtask
            # completes.
            completed_subtask_ids = frozenset(
                subtask_id for subtask_id, status in approved_stage_states.items()
                if status == deps.SubtaskStatus.COMPLETED.value
            )
            return await deps.workflow_engine.run_generation_workflow(
                goal=target_goal,
                workspace_path=plan_workspace_path,
                supplementary_context="\n\n".join(filter(None, (
                    deps.build_subtask_constraint_context(goal),
                    deps.build_subtask_semantic_context(plan, target),
                    deps.render_established_file_context(established_file_context),
                    target_context_text,
                    deps.findings_prompt_block([f for f in ownership_findings if f.subtask_id == target.id]),
                ))),
                planned_source_files=planned_source_files,
                # Recovery Execution Contract (PRV-06, 2026-08-29): NO LONGER
                # folded into supplementary_context above - a live incident
                # traced this exact text (MUST_FIX/MUST_PRESERVE/EVIDENCE/
                # ACCEPTANCE) ending up inside "existing code context," a
                # passive reference blob the full-set generation prompt
                # explicitly frames as "the model's own prior attempt's
                # content," never as an active instruction. A recovery
                # requirement is not code context, a coding convention, or
                # repository history - it is the CURRENT GOVERNING
                # INSTRUCTION for this specific invocation. Threaded as its
                # own first-class field all the way into the task
                # description retry_prompts.py builds (see AttemptContext.
                # recovery_contract_block's own docstring) - never merely
                # another paragraph prepended to existing context.
                recovery_contract_block=recovery_context,
                grounded_locations=list(grounded_locations),
                established_files=sorted(established_file_context.keys()),
                predetermined_plan=deps.build_subtask_plan_text(target),
                predetermined_design=deps.BOUNDED_SUBTASK_DESIGN,
                predetermined_architect_files=target_files,
                allowed_write_relpaths=target_files,
                # PRV-05 run 7 (2026-08-28): the validated EngineeringPlan and
                # this subtask's own id, so migration validation and retry
                # attribution can both answer "who owns this file, and are we
                # there yet" via EngineeringPlan.classify_file_ownership() -
                # see AttemptContext.structured_plan's own docstring.
                structured_plan=plan,
                current_subtask_id=target.id,
                # MA8 (PRV-05 run #8): same ledger the plan-repair loop
                # above used, so MIGRATION_COMPLETION obligations recorded
                # during this subtask's own attempt accumulate into the
                # SAME per-run ledger, not a fresh one per subtask.
                obligation_ledger=obligation_ledger,
                deterministic_failure_diagnostics=deterministic_failure_diagnostics,
                completed_subtask_ids=completed_subtask_ids,
                # DENY_ALL for a verification-role subtask - enforced at the
                # real write gate (AuthorizedFileWriter), not merely implied
                # by target_files being empty. Every other subtask keeps
                # today's inferred ALLOWLIST/UNRESTRICTED behavior (None ->
                # run_generation_workflow's own backward-compatible
                # inference).
                write_scope_mode=(
                    deps.WriteScopeMode.DENY_ALL if target.execution_role == deps.ExecutionRole.VERIFICATION
                    else None
                ),
                required_verification=[vm.model_dump(mode="json") for vm in target.verification],
                runtime_verification_required=any(
                    vm.requires_application_runtime for vm in target.verification
                ),
                strict_spec_compliance=True,
                execution_scope=f"subtask={target.id} role={execution_role}",
                reopened_owner=execution_role == "verification_owner_recovery",
                grounding_goal=goal,
                migration_resolution=migration_resolution,
                strict_dependency_index=bool(
                    process_profiles is not None
                    and getattr(process_profiles, "enabled", False) is True
                    and getattr(process_profiles, "enforce_context_depth", False) is True
                ),
                # PRD-008A: a unit of this structured plan, never a new direct
                # intent (which would wrap it in its own one-unit plan). The
                # structured subtask loop is not yet executed by execute_plan;
                # its subtasks keep active_work_unit=None, as before.
                work_unit=deps.WorkUnitInvocation(
                    deps.PlanSourceKind.STRUCTURED, plan.plan_id, target.id, plan.content_hash(),
                ),
                # TRACE-ENFORCE-SUBTASK-LINKAGE-001: the subtask's own traces.db
                # row names the enforce run it belongs to (<run_id>.enforce,
                # the terminal row _write_enforce_trace writes).
                enforce_run_id=f"{run_id}.enforce",
                **{k: v for k, v in legacy_kwargs.items() if k != "trace_id_override"},
            )

        # ENFORCE-VERIFICATION-UNIT-STOP-BEFORE-TERMINAL-GATE-001: each verification-only unit may reopen its
        # upstream owner once per run (the bound), whatever the owner then produces.
        verification_owner_reopened: set = set()
        for position, subtask_id in enumerate(order, start=1):
            subtask = plan.subtask_by_id(subtask_id)
            if subtask is None:
                continue

            if resumed_subtask_states.get(subtask_id) == "completed":
                deps.logger.info(f"Subtask '{subtask.id}' ({position}/{total}) already completed (resume) - skipping.")
                subtask_results.append(deps.SubtaskResult(
                    subtask_id=subtask.id, status=deps.SubtaskStatus.COMPLETED,
                    execution_method=deps.ExecutionMethod.MODEL.value, error=None,
                ))
                for planned_file in subtask.planned_files:
                    try:
                        with open(
                            os.path.join(plan_workspace_path, planned_file.path), "r", encoding="utf-8", errors="replace",
                        ) as fh:
                            content = fh.read()
                    except OSError as e:
                        deps.logger.warning(
                            f"WorkflowController enforce run {run_id!r}: could not capture established "
                            f"content for {planned_file.path!r} from resumed subtask {subtask_id!r}: {e}"
                        )
                        continue
                    projection = deps.project_implementation_source(
                        content, planned_file.path, deps._ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                        reason="established_by_earlier_subtask",
                    )
                    established_file_context[planned_file.path] = projection.content
                    established_provenance.record(planned_file.path, subtask_id)
                continue

            approved_stage_states[subtask_id] = "in_progress"
            control_state = control_state.with_updates(
                subtask_states={**control_state.subtask_states, subtask_id: "in_progress"},
            )
            deps.save_approved_plan(
                workspace_path, plan.plan_id,
                deps.build_approved_plan_document(
                    plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                    stage_states=approved_stage_states, lifecycle_state="in_progress",
                ),
            )
            control_state = _persist_control_state(control_state)

            deps._log_phase_banner(f"SUBTASK '{subtask.id}' ({position}/{total}): {subtask.description[:40]}")
            deps.logger.info(
                "Current subtask: %s/%s id=%s files=%s depends_on=%s relevant_invariant_ids=%s",
                position, total, subtask.id, [pf.path for pf in subtask.planned_files],
                subtask.depends_on, subtask.relevant_global_invariant_ids,
            )

            if subtask.execution_method == deps.ExecutionMethod.TOOL:
                # TOOL-001 (2026-09-13): governed execution, converging on
                # the SAME kernel.registry -> BaseTool.execute() boundary
                # `kriya tools execute` (direct CLI) already uses, via the
                # existing subtask_executor.execute() - unchanged, and
                # already the exact function _run_structured_shadow calls
                # for MODEL subtasks above; TOOL was the one
                # execution_method it (deliberately) never dispatched for
                # real. No retry/recovery/obligation-ledger/scope-conflict
                # machinery applies here - all of that is MODEL-generation-
                # specific (a Quality-Gates retry loop over LLM output); a
                # TOOL subtask is one deterministic call through a tool the
                # tool's OWN implementation is responsible for authorizing/
                # containing (ExecutionPolicy, TOOL-002/TOOL-003 durable
                # approval + OCI containment for MCP tools, SEC-005
                # registry-scoped network authority for ShellTool
                # package-manager commands - none of it duplicated or
                # reimplemented here). subtask.tool_arguments passes
                # straight through as validated kwargs
                # (BaseTool.execute()'s own arguments_schema validation);
                # nothing here inspects or trusts subtask.description,
                # planner rationale, or any other free-form field as an
                # authority signal. S1 (OI-88): the call is MODEL-AUTHORED, never
                # the operator's - subtask_executor dispatches it through
                # kriya.tools.tool.execute_model_authored() with the roots
                # below, where each tool's own authorize_model_invocation()
                # (the existing reader/writer/control-path/TOOL-002 authority)
                # decides before anything runs.
                tool_context = deps.project_for_subtask(execution_context, subtask)
                # LR-R1-M1: the action as its own evidence unit (observational).
                with deps.attempt_evidence_scope.tool_unit(getattr(kernel, "config", None), subtask.id,
                                                      subtask.tool_name) as evidence:
                    # S1: the model-authored call may touch only the owner workspace and this plan's candidate
                    # worktree; S18c/S18d: a relative read path resolves in the candidate when the step depends on a
                    # MODEL unit that observably changed it - this run's own record of what each unit applied
                    # (control_state.subtask_written_files) - else in the owner workspace
                    # (subtask_executor.reads_candidate_view).
                    result = await deps.subtask_executor.execute(
                        subtask=subtask, plan=plan, context=tool_context, kernel=kernel,
                        tool_roots=(workspace_path, plan_workspace_path),
                        candidate_writes=control_state.subtask_written_files,
                    )
                    evidence["result"] = result
                subtask_results.append(result)
                deps.record_subtask_attempt(ledger, plan, result, attempt=1)
                control_state = control_state.with_updates(
                    subtask_states={**control_state.subtask_states, subtask.id: result.status.value},
                )
                approved_stage_states[subtask.id] = result.status.value
                tool_lifecycle_state = (
                    "in_progress" if result.status == deps.SubtaskStatus.COMPLETED
                    else "needs_review" if result.status == deps.SubtaskStatus.NEEDS_REVIEW
                    else "failed"
                )
                deps.save_approved_plan(
                    workspace_path, plan.plan_id,
                    deps.build_approved_plan_document(
                        plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                        stage_states=approved_stage_states, lifecycle_state=tool_lifecycle_state,
                    ),
                )
                control_state = _persist_control_state(control_state)
                if result.status != deps.SubtaskStatus.COMPLETED:
                    deps.logger.warning(
                        f"WorkflowController enforce run {run_id!r}: stopped at subtask {subtask_id!r} "
                        f"({position}/{total}) - TOOL execution did not complete (status="
                        f"{result.status.value})." + (f" Reason: {result.error}" if result.error else "")
                    )
                    break
                continue

            # MA7-C1 (2026-08-25 external review): the validated Subtask is
            # now authoritative - predetermined_plan/predetermined_design/
            # predetermined_architect_files (kriya/workflow/workflow.py)
            # make run_generation_workflow() skip its own fresh Planner+
            # Architect cycle entirely and use this subtask's own already-
            # decided description/file list instead, while every other real
            # guarantee (Graph RAG retrieval, skill matching, the Developer/
            # Quality-Gates retry loop, failure grounding, attribution,
            # repair, approval, regression verification, trace logging)
            # stays completely intact and un-duplicated - see that method's
            # own docstring for the full account of what is and isn't
            # skipped.
            predetermined_files = [pf.path for pf in subtask.planned_files]
            call_result = await _invoke_bounded_subtask(subtask, position)
            scope_conflict = call_result.get("plan_scope_conflict") or {}
            if scope_conflict.get("reason_code") == "PLANNED_PREREQUISITE_OWNER_REQUIRED":
                owner_id = scope_conflict.get("required_owner_subtask_id")
                capability = scope_conflict.get("required_capability")
                if isinstance(owner_id, str) and isinstance(capability, str) and capability:
                    prerequisite_revision = deps.revise_plan_for_planned_prerequisite(
                        plan,
                        consumer_subtask_id=subtask.id,
                        owner_subtask_id=owner_id,
                        capability=capability,
                        consumer_artifacts=scope_conflict.get("consumer_artifacts") or (),
                    )
                    prerequisite_validation = await deps.validate_plan(
                        prerequisite_revision,
                        workspace_path=plan_workspace_path,
                        available_tool_names=available_tool_names, plannable_tool_names=planned_tool_names,
                        route=route,
                        triage_service=deps.workflow_engine.engineering_triage,
                        resuming_own_established_progress=True,
                        require_model_planned_files=True,
                        require_semantic_contracts=True,
                        runtime_verification_required=deps.goal_requires_runtime_behavior(goal),
                        stack_contract=deps.derive_stack_contract(goal),
                        known_requirement_ids=requirement_set.ids,
                        immutable_test_files=immutable_tests, frozen_files=frozen_files, documentation_paths=documentation_paths,
                        delegating_shims=shim_targets,
                    )
                    if prerequisite_validation.valid:
                        prior_hash = current_plan_hash
                        plan = prerequisite_revision
                        current_plan_hash = plan.content_hash()
                        order = deps.topological_subtask_order(plan)
                        execution_context = await deps._build_context(
                            goal, plan, plan_workspace_path, route, control_context, control_state,
                        )
                        subtask = plan.subtask_by_id(subtask_id)
                        assert subtask is not None
                        deps.save_approved_plan(
                            workspace_path, plan.plan_id,
                            deps.build_approved_plan_document(
                                plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                                stage_states=approved_stage_states,
                                lifecycle_state="prerequisite_revised",
                            ),
                        )
                        plan_recovery_events.append({
                            "failed_subtask": subtask.id,
                            "classification": "PLAN_SCOPE_DEFECT",
                            "reason_code": "PLANNED_PREREQUISITE_OWNER_REQUIRED",
                            "prerequisite_owner": owner_id,
                            "required_capability": capability,
                            "prior_plan_hash": prior_hash,
                            "revised_plan_hash": current_plan_hash,
                            "ownership_preserved": True,
                        })
                        deps.logger.warning(
                            "PLANNED_PREREQUISITE_WIRED consumer=%s owner=%s capability=%s "
                            "- owner remains responsible for its artifact and now executes upstream.",
                            subtask.id, owner_id, capability,
                        )
                    else:
                        scope_conflict = {
                            **scope_conflict,
                            "reason": "planned prerequisite wiring failed revalidation: "
                            + "; ".join(prerequisite_validation.errors),
                        }
            elif scope_conflict.get("reason_code") == "RUNTIME_PLAN_GAP":
                # Runtime-Evidence Plan Repair (PRV-17 Run 13, 2026-09-04) -
                # see ObligationKind.RUNTIME_PLAN_GAP's own docstring for the
                # live incident (myproject.wsgi never planned by any
                # subtask) and kriya/workflow/attempt.py's own _execute_
                # managed_service_verification for where this evidence
                # originates. Structurally parallel to the
                # PLANNED_PREREQUISITE_OWNER_REQUIRED branch just above -
                # revise, revalidate, swap in - but for the case that branch
                # cannot handle: no owner exists yet for the missing
                # artifact at all. Positioned BEFORE grounded_scope_files is
                # computed below deliberately: resolve_effective_scope_
                # conflict_owners() (used further down) can only find an
                # owner for a file the plan ALREADY declares - this block's
                # entire job is to make that true first, never to reuse the
                # generic PLAN_SCOPE_DEFECT merge path's own "genuinely
                # unowned -> assign to the FAILING subtask" fallback
                # (revise_plan_for_grounded_scope_owner), which would
                # silently hand a managed-service verification subtask
                # (empty write scope by design) a new artifact it was never
                # meant to own - exactly what PRV-17 Run 13's own audit
                # explicitly ruled out.
                missing_artifact = scope_conflict.get("missing_logical_artifact")
                obligation_id = f"runtime_plan_gap.{subtask.id}.{missing_artifact}"
                prior_attempts = len(obligation_ledger.history(obligation_id)) if obligation_ledger else 0
                if not isinstance(missing_artifact, str) or not missing_artifact:
                    deps.logger.error(
                        "RUNTIME_PLAN_GAP_UNRESOLVED subtask=%s reason=missing_logical_artifact_absent_or_invalid",
                        subtask.id,
                    )
                elif prior_attempts >= 2:
                    if obligation_ledger:
                        obligation_ledger.record(deps.ObligationRecord(
                            id=obligation_id, kind=deps.ObligationKind.RUNTIME_PLAN_GAP,
                            status=deps.ObligationStatus.VIOLATED, authority=deps.ObligationAuthority.DETERMINISTIC,
                            description=(
                                f"Runtime evidence repeatedly proved {missing_artifact!r} missing "
                                "with no resolvable owner/path."
                            ),
                            source="managed_service_verification", revision=subtask.id,
                            evidence={"missing_logical_artifact": missing_artifact, "attempts": prior_attempts},
                            owner_subtask_id=None, terminal_required=True,
                        ))
                    deps.logger.error(
                        "RUNTIME_PLAN_REPAIR_NO_PROGRESS subtask=%s missing_logical_artifact=%s attempts=%d",
                        subtask.id, missing_artifact, prior_attempts,
                    )
                else:
                    candidate_path = deps.resolve_python_module_to_candidate_artifact_path(
                        missing_artifact, plan_workspace_path,
                    )
                    owner_id = (
                        deps.resolve_runtime_plan_gap_owner(plan, candidate_path, subtask)
                        if candidate_path else None
                    )
                    if candidate_path is None or owner_id is None:
                        if obligation_ledger:
                            obligation_ledger.record(deps.ObligationRecord(
                                id=obligation_id, kind=deps.ObligationKind.RUNTIME_PLAN_GAP,
                                status=deps.ObligationStatus.INDETERMINATE, authority=deps.ObligationAuthority.DETERMINISTIC,
                                description=(
                                    f"Runtime evidence proved {missing_artifact!r} missing, but no "
                                    "single safe physical path/owner could be resolved deterministically."
                                ),
                                source="managed_service_verification", revision=subtask.id,
                                evidence={
                                    "missing_logical_artifact": missing_artifact,
                                    "candidate_path": candidate_path,
                                    "owner_subtask_id": owner_id,
                                },
                                owner_subtask_id=None, terminal_required=True,
                            ))
                        deps.logger.error(
                            "RUNTIME_PLAN_GAP_UNRESOLVED subtask=%s missing_logical_artifact=%s "
                            "candidate_path=%s owner_subtask_id=%s - ambiguous or no compatible "
                            "owner; refusing to guess.",
                            subtask.id, missing_artifact, candidate_path, owner_id,
                        )
                    else:
                        gap_revision = deps.revise_plan_for_runtime_plan_gap(
                            plan, owner_subtask_id=owner_id, artifact_path=candidate_path,
                            reason=(
                                f"Runtime-Evidence Plan Repair: {subtask.id}'s managed-service "
                                f"verification deterministically proved Python module "
                                f"{missing_artifact!r} is imported but never planned."
                            ),
                        )
                        gap_validation = await deps.validate_plan(
                            gap_revision,
                            workspace_path=plan_workspace_path,
                            available_tool_names=available_tool_names, plannable_tool_names=planned_tool_names,
                            route=route,
                            triage_service=deps.workflow_engine.engineering_triage,
                            resuming_own_established_progress=True,
                            require_model_planned_files=True,
                            require_semantic_contracts=True,
                            runtime_verification_required=deps.goal_requires_runtime_behavior(goal),
                            stack_contract=deps.derive_stack_contract(goal),
                            immutable_test_files=immutable_tests, frozen_files=frozen_files, documentation_paths=documentation_paths,
                        delegating_shims=shim_targets,
                        )
                        if obligation_ledger:
                            obligation_ledger.record(deps.ObligationRecord(
                                id=obligation_id, kind=deps.ObligationKind.RUNTIME_PLAN_GAP,
                                status=(
                                    deps.ObligationStatus.PENDING if gap_validation.valid
                                    else deps.ObligationStatus.VIOLATED
                                ),
                                authority=deps.ObligationAuthority.DETERMINISTIC,
                                description=(
                                    f"Runtime-Evidence Plan Repair adds {candidate_path!r} to "
                                    f"{owner_id!r}'s planned files."
                                ),
                                source="managed_service_verification", revision=subtask.id,
                                evidence={
                                    "missing_logical_artifact": missing_artifact,
                                    "candidate_path": candidate_path,
                                    "validation_errors": gap_validation.errors,
                                },
                                owner_subtask_id=owner_id, terminal_required=True,
                                repair_scope=(candidate_path,),
                            ))
                        if gap_validation.valid:
                            prior_hash = current_plan_hash
                            plan = gap_revision
                            current_plan_hash = plan.content_hash()
                            order = deps.topological_subtask_order(plan)
                            execution_context = await deps._build_context(
                                goal, plan, plan_workspace_path, route, control_context, control_state,
                            )
                            subtask = plan.subtask_by_id(subtask_id)
                            assert subtask is not None
                            deps.save_approved_plan(
                                workspace_path, plan.plan_id,
                                deps.build_approved_plan_document(
                                    plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                                    stage_states=approved_stage_states,
                                    lifecycle_state="runtime_plan_gap_revised",
                                ),
                            )
                            plan_recovery_events.append({
                                "failed_subtask": subtask.id,
                                "classification": "RUNTIME_PLAN_GAP",
                                "reason_code": "RUNTIME_PLAN_GAP",
                                "missing_logical_artifact": missing_artifact,
                                "new_artifact_path": candidate_path,
                                "new_artifact_owner": owner_id,
                                "prior_plan_hash": prior_hash,
                                "revised_plan_hash": current_plan_hash,
                                "ownership_preserved": False,
                            })
                            deps.logger.warning(
                                "RUNTIME_PLAN_GAP_REPAIRED subtask=%s missing_logical_artifact=%s "
                                "new_artifact=%s owner=%s - owner reopens for the added artifact "
                                "before %s resumes.",
                                subtask.id, missing_artifact, candidate_path, owner_id, subtask.id,
                            )
                            # Reshape scope_conflict into the ordinary
                            # PLAN_SCOPE_DEFECT contract so every downstream
                            # mechanism (resolve_effective_scope_conflict_
                            # owners, build_recovery_execution_plan, the
                            # existing reopen/regenerate/resume machinery)
                            # runs completely unmodified - the owner it now
                            # resolves to is correct because gap_revision
                            # already made it true, not because this dict
                            # says so.
                            scope_conflict = {
                                "classification": "PLAN_SCOPE_DEFECT",
                                "reason_code": "RUNTIME_PLAN_GAP",
                                "failure_type": scope_conflict.get("failure_type"),
                                "reason": (
                                    f"Runtime-Evidence Plan Repair: {candidate_path!r} was added to "
                                    f"{owner_id!r}'s planned files after deterministic runtime "
                                    "evidence proved it missing."
                                ),
                                "required_files": [candidate_path],
                                "attribution_tier": "architectural_owner",
                            }
                        else:
                            deps.logger.error(
                                "RUNTIME_PLAN_GAP_REVISION_REJECTED subtask=%s candidate_path=%s "
                                "owner=%s validation_errors=%s",
                                subtask.id, candidate_path, owner_id, gap_validation.errors,
                            )
            grounded_scope_files = deps._plan_scope_conflict_files(scope_conflict)
            # MA8 (spec §30): a DETERMINISTIC-authority obligation
            # (currently only the migration gate's authoritative_files,
            # attribution tier "authoritative_deterministic" - see
            # attribution.py) naming a file some OTHER real subtask in this
            # validated plan already owns must never be silently folded
            # into the failing subtask via the merge loop just below
            # (revise_plan_for_grounded_scope_owner, designed for genuine
            # architecture-discovery - PRV-03/04's own case) - that would
            # be exactly the "silently edit across stage boundaries" the
            # spec forbids, whether the true owner already ran (a
            # completed predecessor that should be REOPENED, not stripped)
            # or hasn't run yet / sits outside this subtask's own
            # dependency chain entirely (a genuine plan inconsistency that
            # should surface for revision, not be auto-resolved).
            #
            # - PAST_ORDERED (a real upstream owner, found via
            #   resolve_scope_conflict_owners which walks depends_on
            #   transitively): skip the merge loop entirely so control
            #   falls through to the existing reopen-owner path below.
            # - FUTURE_ORDERED / UNRELATED (owned by some other subtask,
            #   but not upstream - resolve_scope_conflict_owners finds
            #   nothing for it, yet classify_file_ownership confirms it
            #   isn't UNOWNED either): also skip the merge loop, but this
            #   time the reopen-owner path finds nothing either (it only
            #   ever looks upstream) - the scope conflict then surfaces
            #   unresolved via the existing PLAN_SCOPE_REVISION_REQUIRED
            #   aggregation below (`scope_conflict_result`), which is
            #   exactly the doc's own "treat as plan inconsistency... do
            #   not cross-edit silently" outcome, achieved by NOT resolving
            #   it automatically rather than by inventing a new failure path.
            # - Genuinely UNOWNED (nobody in the plan declared this file at
            #   all): leave grounded_scope_files untouched - the merge
            #   loop's own "no owner -> plan/scope defect -> use the
            #   existing authoritative plan revision mechanism" behavior is
            #   correct for this case and stays exactly as it was.
            #
            # Widened (2026-08-29, PRV-06, MA8.1), narrowly: the PAST_ORDERED
            # disjunct below (resolve_scope_conflict_owners finds a real,
            # unique UPSTREAM owner) now skips the merge loop regardless of
            # attribution_tier - the original gate ("authoritative_
            # deterministic" only) left an ordinary grounded compile-error
            # finding (tier "architectural_owner" - a downstream subtask
            # proving an upstream owner's file is missing something, e.g. a
            # build manifest missing a test dependency) going through the
            # DAG-mutating merge loop even when a clean upstream owner was
            # resolvable. revise_plan_for_grounded_scope_owner() is for
            # genuine architecture-discovery (permanently reassigning
            # ownership because the PLAN mis-assigned it) - a different
            # operation from "temporarily reopen an owner that's already
            # correctly assigned, to satisfy one new requirement, then
            # resume the downstream subtask," which is exactly what the
            # non-mutating owner-recovery path below (resolve_scope_
            # conflict_owners + CROSS_OWNER_ARTIFACT_REQUIREMENT) does - the
            # "recovery scheduling, not plan dependency" distinction that
            # incident's forensic analysis identified as the actual
            # conceptual mistake behind a live-observed cyclic-DAG revision
            # failure.
            #
            # The FUTURE_ORDERED/UNRELATED disjunct (owned, but NOT
            # upstream) deliberately KEEPS its original authoritative_
            # deterministic-only gate, unchanged - test_enforce_revises_
            # service_scope_to_grounded_controller_and_continues exercises
            # exactly this combination (tier="architectural_owner", a real
            # owner that is downstream, not upstream, of the failing
            # subtask) and depends on it continuing to route through the
            # merge path; widening THAT disjunct too was not evidenced by
            # the PRV-06 incident (whose owner was genuinely upstream) and
            # would have silently changed already-tested behavior with no
            # live incident behind it.
            # MA8.1 completion v3 (2026-08-29, "Effective Artifact Ownership
            # and Recovery Routing"): resolve_effective_scope_conflict_
            # owners replaces the bare depends_on-only resolve_scope_
            # conflict_owners here - the live PRV-06 incident this closes
            # is a downstream subtask needing an artifact whose real,
            # already-completed modifier sits OUTSIDE its own declared
            # depends_on (see resolve_effective_artifact_owner's own
            # docstring). This is the actual root fix: with it, this skip-
            # condition correctly recognizes a real effective owner and
            # never lets the failure fall through into the DAG-mutating
            # merge path below at all for that case.
            if (
                scope_conflict.get("classification") == "PLAN_SCOPE_DEFECT"
                and grounded_scope_files
                and (
                    deps.resolve_effective_scope_conflict_owners(
                        plan, grounded_scope_files, subtask, order=order, control_state=control_state,
                    )
                    or (
                        scope_conflict.get("attribution_tier") == "authoritative_deterministic"
                        and any(
                            plan.classify_file_ownership(subtask.id, path) != deps.FileOwnershipRelation.UNOWNED
                            for path in grounded_scope_files
                        )
                    )
                )
            ):
                grounded_scope_files = []
            # PRV-03: a chain of grounded owners (each repair surfacing the
            # NEXT file outside scope) needs repeated revise-and-revalidate
            # passes, not just one - see _MAX_PLAN_SCOPE_REVISION_ATTEMPTS.
            for _scope_revision_attempt in range(deps._MAX_PLAN_SCOPE_REVISION_ATTEMPTS):
                if not (
                    scope_conflict.get("classification") == "PLAN_SCOPE_DEFECT"
                    and grounded_scope_files
                ):
                    break
                revised_plan = deps.revise_plan_for_grounded_scope_owner(
                    plan, subtask.id, grounded_scope_files, plan_workspace_path,
                )
                revision_validation = await deps.validate_plan(
                    revised_plan,
                    workspace_path=plan_workspace_path,
                    available_tool_names=available_tool_names, plannable_tool_names=planned_tool_names,
                    route=route,
                    triage_service=deps.workflow_engine.engineering_triage,
                    resuming_own_established_progress=True,
                    require_model_planned_files=True,
                    require_semantic_contracts=True,
                    runtime_verification_required=deps.goal_requires_runtime_behavior(goal),
                    stack_contract=deps.derive_stack_contract(goal),
                    # The same sealed planning inputs every other validation of this run uses (independent
                    # review of the repair, finding 2): a revision must not refuse what admission accepted.
                    immutable_test_files=immutable_tests, frozen_files=frozen_files, documentation_paths=documentation_paths,
                        delegating_shims=shim_targets,
                )
                if revision_validation.valid:
                    prior_hash = current_plan_hash
                    plan = revised_plan
                    current_plan_hash = plan.content_hash()
                    execution_context = await deps._build_context(
                        goal, plan, plan_workspace_path, route, control_context, control_state,
                    )
                    subtask = plan.subtask_by_id(subtask_id)
                    assert subtask is not None
                    predetermined_files = [pf.path for pf in subtask.planned_files]
                    for path in grounded_scope_files:
                        original_plan_revisions.setdefault(
                            path, deps.read_file_revision(os.path.join(workspace_path, path)),
                        )
                    approved_stage_states = {
                        sid: approved_stage_states.get(sid, "pending")
                        for sid in deps.topological_subtask_order(plan)
                    }
                    approved_stage_states[subtask.id] = "in_progress"
                    control_state = control_state.with_updates(
                        current_plan_hash=current_plan_hash,
                        subtask_states=dict(approved_stage_states),
                    )
                    deps.save_approved_plan(
                        workspace_path, plan.plan_id,
                        deps.build_approved_plan_document(
                            plan, plan_hash=current_plan_hash,
                            repair_attempts=repair_attempts,
                            stage_states=approved_stage_states,
                            lifecycle_state="scope_revised",
                        ),
                    )
                    control_state = _persist_control_state(control_state)
                    plan_recovery_events.append({
                        "failed_subtask": subtask.id,
                        "classification": "PLAN_SCOPE_DEFECT",
                        "required_repair_files": sorted(grounded_scope_files),
                        "prior_plan_hash": prior_hash,
                        "revised_plan_hash": current_plan_hash,
                        "ownership_revalidated": True,
                        "revalidation_basis": "deterministic grounded owner plus validate_plan",
                    })
                    deps.logger.warning(
                        "Authoritative PLAN_SCOPE_DEFECT recovery revised subtask %s scope to %s "
                        "and revalidated plan %s.",
                        subtask.id, predetermined_files, current_plan_hash,
                    )
                    call_result = await _invoke_bounded_subtask(
                        subtask, position,
                        recovery_context=(
                            "--- authoritative PLAN_SCOPE_DEFECT recovery ---\n"
                            f"The validated scope now includes grounded owner(s): "
                            f"{json.dumps(grounded_scope_files)}. Modify the actual owner; do not "
                            "repeat the former service-only repair."
                        ),
                        execution_role="plan_scope_recovery",
                        # P2: the grounded loci of the failure that widened the
                        # scope (revision-bound), so the new owner's exact lines
                        # are shown to the re-invoked stage.
                        grounded_locations=scope_conflict.get("grounded_locations") or (),
                    )
                    scope_conflict = call_result.get("plan_scope_conflict") or {}
                    grounded_scope_files = deps._plan_scope_conflict_files(scope_conflict)
                else:
                    revision_failure_reason = (
                        "authoritative grounded-owner plan revision failed validation: "
                        + "; ".join(revision_validation.errors)
                    )
                    deps.logger.error(
                        "WorkflowController enforce run %r: authoritative PLAN_SCOPE_DEFECT "
                        "recovery for subtask %r (grounded owner file(s) %s) produced a plan "
                        "that failed revalidation - %s. Falling back to upstream-owner "
                        "recovery; if that also cannot resolve it, this run terminates with "
                        "the scope conflict unresolved.",
                        run_id, subtask.id, grounded_scope_files, revision_failure_reason,
                    )
                    deps.logger.info(
                        "PLAN_SCOPE_REVISION_REJECTED subtask=%s grounded_files=%s reason=%s",
                        subtask.id, grounded_scope_files, revision_failure_reason,
                    )
                    deps.logger.info(
                        "OWNER_RECOVERY_FALLBACK_STARTED subtask=%s required_files=%s",
                        subtask.id, sorted(set(scope_conflict.get("required_files", []))),
                    )
                    scope_conflict = {
                        **scope_conflict,
                        "reason": revision_failure_reason,
                    }
                    break
            else:
                if (
                    scope_conflict.get("classification") == "PLAN_SCOPE_DEFECT"
                    and grounded_scope_files
                ):
                    deps.logger.error(
                        "WorkflowController enforce run %r: subtask %r hit the bound of %d "
                        "authoritative PLAN_SCOPE_DEFECT revision attempts and still surfaces a "
                        "further grounded owner scope conflict (%s) - falling back to "
                        "upstream-owner recovery; if that also cannot resolve it, this run "
                        "terminates with the scope conflict unresolved.",
                        run_id, subtask.id, deps._MAX_PLAN_SCOPE_REVISION_ATTEMPTS, grounded_scope_files,
                    )
                    deps.logger.info(
                        "OWNER_RECOVERY_FALLBACK_STARTED subtask=%s required_files=%s",
                        subtask.id, sorted(set(scope_conflict.get("required_files", []))),
                    )
            # MA8.1 completion (2026-08-29 v2): looped, not one-shot - a
            # downstream retry after a successful owner recovery may
            # surface a genuinely NEW grounded requirement on the SAME
            # owner (see "Requirement-scoped repeated owner recovery"
            # near recovery_generation_by_key's own declaration above).
            # Each iteration re-derives a fresh RecoveryExecutionPlan
            # against the CURRENT scope_conflict (General Obligation-
            # Centric Recovery Execution, 2026-08-30 - see that section's
            # own comment right below); boundedness comes from each
            # requirement's own attempt count (via the ledger's history()),
            # not from a blanket "this owner already ran once" guard.
            for _owner_recovery_cycle in range(deps._MAX_PLAN_SCOPE_REVISION_ATTEMPTS):
                # General Obligation-Centric Recovery Execution (2026-08-30):
                # generalizes MA8.1's owner-recovery loop from exactly-one-
                # owner to N independently, unambiguously resolved owners -
                # see the implementation spec (repo root) for the full
                # design this closes: a downstream requirement spanning
                # files owned by two DIFFERENT prior subtasks (e.g. pom.xml
                # owned by s1, App.java owned by s2, both needed by the same
                # compile failure) used to fail closed purely because more
                # than one owner was involved (`len(owner_map) == 1`), even
                # though each artifact resolved to its own owner cleanly and
                # unambiguously. build_recovery_execution_plan replaces that
                # bare owner-count gate with a real per-artifact ambiguity
                # check (SCOPE_RECOVERY_OWNER_UNRESOLVED now fires only when
                # an artifact's OWN owner is genuinely unresolvable) plus a
                # dependency-ordered grouping of the (possibly several)
                # owners that must be reopened together.
                exec_plan = deps.build_recovery_execution_plan(
                    plan, scope_conflict, subtask, order=order, control_state=control_state,
                    plan_generation=recovery_plan_generation_by_subtask.get(subtask.id, 0),
                    recovery_generation_by_key=recovery_generation_by_key,
                )
                if exec_plan is None:
                    # build_recovery_execution_plan already logged
                    # SCOPE_RECOVERY_OWNER_UNRESOLVED or
                    # RECOVERY_GROUP_ORDER_AMBIGUOUS when scope_conflict was
                    # non-empty - an empty scope_conflict (the very first
                    # pass) logs nothing, matching the original single-owner
                    # behavior exactly.
                    break
                deps.logger.info(
                    "RECOVERY_EXECUTION_PLAN_STARTED plan_id=%s groups=%s",
                    exec_plan.id, list(exec_plan.group_order),
                )
                invoked_group_records: List[Dict[str, Any]] = []
                plan_locally_accepted = True
                for group_id in exec_plan.group_order:
                    group = next(g for g in exec_plan.groups if g.group_id == group_id)
                    exec_plan.active_group_id = group_id
                    owner_id = group.owner_subtask_id
                    required_owner_files = [p.artifact for p in group.participants]
                    owner = plan.subtask_by_id(owner_id)
                    if owner is None:
                        plan_locally_accepted = False
                        break
                    generation_key = (subtask.id, owner_id, tuple(sorted(required_owner_files)))
                    generation = recovery_generation_by_key.get(generation_key, 0)
                    candidate_requirement_id = deps._cross_owner_obligation_id(
                        subtask.id, owner_id, required_owner_files,
                        scope_conflict.get("failure_type") or "unknown", generation,
                    )
                    # PRV-11 (2026-08-30): MUST_CHANGE vs VERIFY - see
                    # RecoveryAction's own docstring (recovery_plan.py) for
                    # the live incident this closes. A group is MUST_CHANGE
                    # if ANY of its participants is (the group is only as
                    # conservative as its most demanding participant) -
                    # everything below this point branches on it.
                    group_disposition = (
                        deps.RecoveryAction.MUST_CHANGE
                        if any(p.recovery_action is deps.RecoveryAction.MUST_CHANGE for p in group.participants)
                        else deps.RecoveryAction.VERIFY
                    )
                    if group_disposition is deps.RecoveryAction.VERIFY:
                        # Do not call the Developer at all - the evidence
                        # that named this artifact never proved its current
                        # content needs to change. Carry it forward
                        # unconditionally (never RECOVERY_NO_PROGRESS for an
                        # untouched VERIFY participant - nothing was ever
                        # asked to change) and let the ORIGINATING CONSUMER's
                        # own retry be the real arbiter, exactly as the
                        # owner's own local gates already were for a
                        # MUST_CHANGE participant. `recovery_generation_by_key`
                        # still advances - this is what lets a requirement
                        # that RECURS after a full skip-and-reverify cycle
                        # promote to MUST_CHANGE on the NEXT generation (see
                        # derive_recovery_participants's own promotion note).
                        deps.logger.info(
                            "RECOVERY_VERIFY_CARRIED_FORWARD requirement_id=%s owner=%s generation=%d "
                            "artifacts=%s - evidence did not prove current content requires mutation; "
                            "Developer not called, existing content carried forward.",
                            candidate_requirement_id, owner_id, generation, sorted(required_owner_files),
                        )
                        owner_result = {"status": "success", "quality_gates_passed": True, "files": []}
                        recovery_generation_by_key[generation_key] = generation + 1
                        invoked_group_records.append({
                            "owner_id": owner_id,
                            "required_owner_files": required_owner_files,
                            "invalidated": (),
                            "candidate_requirement_id": candidate_requirement_id,
                            "generation": generation,
                            "cross_owner_obligation": None,
                            "owner_result": owner_result,
                        })
                        exec_plan.completed_group_ids = exec_plan.completed_group_ids + (group_id,)
                        continue
                    prior_attempts = (
                        len(obligation_ledger.history(candidate_requirement_id))
                        if obligation_ledger is not None else 0
                    )
                    if prior_attempts >= deps._MAX_PLAN_SCOPE_REVISION_ATTEMPTS:
                        deps.logger.warning(
                            "WorkflowController enforce run %r: requirement %r exhausted its "
                            "bounded recovery attempts (%d) - stopping rather than reopening %r "
                            "again for the same unresolved requirement.",
                            run_id, candidate_requirement_id, prior_attempts, owner_id,
                        )
                        plan_locally_accepted = False
                        break
                    invalidated = deps.transitive_subtask_dependents(plan, owner_id)
                    for invalidated_id in invalidated:
                        approved_stage_states[invalidated_id] = "pending"
                    approved_stage_states[owner_id] = "in_progress"
                    control_state = control_state.with_updates(
                        subtask_states={
                            **control_state.subtask_states,
                            **{invalidated_id: "pending" for invalidated_id in invalidated},
                            owner_id: "in_progress",
                        },
                    )
                    deps.save_approved_plan(
                        workspace_path, plan.plan_id,
                        deps.build_approved_plan_document(
                            plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                            stage_states=approved_stage_states, lifecycle_state="recovering",
                        ),
                    )
                    control_state = _persist_control_state(control_state)
                    owner_position = order.index(owner_id) + 1
                    # MA8.1 (PRV-06, 2026-08-29): the grounded reason this
                    # owner is being reopened is promoted into a durable
                    # CROSS_OWNER_ARTIFACT_REQUIREMENT obligation and
                    # threaded explicitly into the owner's own Developer
                    # prompt - the exact requirement-propagation gap the
                    # live incident exposed. Sticky within one generation
                    # (see _get_or_create_cross_owner_obligation's own
                    # docstring) - a recurring unresolved requirement reuses
                    # the SAME MUST_FIX text, never degrading back to
                    # generic brownfield framing; a genuinely new generation
                    # gets its own fresh requirement instead of being
                    # silently blocked.
                    cross_owner_obligation = deps._get_or_create_cross_owner_obligation(
                        obligation_ledger,
                        originating_subtask_id=subtask.id,
                        owner_subtask_id=owner_id,
                        required_files=required_owner_files,
                        scope_conflict=scope_conflict,
                        generation=generation,
                        revision=len(plan_recovery_events),
                    )
                    recovery_context = deps._build_owner_recovery_context(
                        owner_id=owner_id,
                        failed_subtask_id=subtask.id,
                        required_owner_files=required_owner_files,
                        cross_owner_obligation=cross_owner_obligation,
                        scope_conflict=scope_conflict,
                    )
                    deps.logger.info(
                        "CROSS_OWNER_RECOVERY_SCHEDULED requirement_id=%s artifact=%s owner_subtask_id=%s",
                        candidate_requirement_id, sorted(required_owner_files), owner_id,
                    )
                    deps.logger.info(
                        "RECOVERY_GROUP_STARTED plan_id=%s group_id=%s owner=%s",
                        exec_plan.id, group_id, owner_id,
                    )
                    deps.logger.info(
                        "OWNER_RECOVERY_STARTED requirement_id=%s owner=%s generation=%d",
                        candidate_requirement_id, owner_id, generation,
                    )
                    # Recovery Execution Contract (PRV-06, 2026-08-29): seed
                    # the no-progress baseline from the file's CURRENT
                    # (pre-recovery) content the FIRST time this (plan,
                    # owner, required files) triple is ever reopened - so
                    # even generation 0's own candidate can be caught if it
                    # reproduces exactly what was already there before
                    # recovery started (the live incident's own worst case:
                    # every single regeneration, including the first,
                    # matched the original unfixed content). Never
                    # overwritten once seeded here - only the comparison/
                    # update after the recovery attempt itself (below) ever
                    # advances it. Rekeyed to include exec_plan.id (spec §9)
                    # so the fingerprint stays scoped to the ACTIVE recovery
                    # requirement identity, not merely owner/files long term
                    # - the same owner and file can legitimately be reopened
                    # later for a different requirement without a stale
                    # fingerprint from an earlier, unrelated cycle leaking
                    # in.
                    fingerprint_key = (exec_plan.id, owner_id, tuple(sorted(required_owner_files)))
                    if fingerprint_key not in recovery_candidate_fingerprints:
                        recovery_candidate_fingerprints[fingerprint_key] = tuple(
                            deps.read_file_revision(os.path.join(plan_workspace_path, path))
                            for path in sorted(required_owner_files)
                        )
                    owner_result = None
                    _self_correction_kernel = getattr(deps.workflow_engine, "kernel", None)
                    if _self_correction_kernel is not None:
                        owner_result = await deps._attempt_owner_recovery_self_correction(
                            kernel=_self_correction_kernel,
                            developer_llm=deps.workflow_engine.developer.llm,
                            plan_workspace_path=plan_workspace_path,
                            workspace_path=workspace_path,
                            owner=owner,
                            required_owner_files=required_owner_files,
                            scope_conflict=scope_conflict,
                        )
                    if owner_result is None:
                        owner_result = await _invoke_bounded_subtask(
                            owner, owner_position, recovery_context=recovery_context,
                            execution_role="owner_recovery",
                        )
                    owner_declared = {pf.path for pf in owner.planned_files}
                    owner_undeclared = sorted(
                        set(owner_result.get("files") or []) - owner_declared
                    )
                    owner_written = set(owner_result.get("files") or [])
                    # Recovery Execution Contract (PRV-06, 2026-08-29): the
                    # owner's own local gates (compile/goal-spec for ITS OWN
                    # file, nothing cross-owner-aware) are necessary but not
                    # sufficient - a regenerated candidate that is BYTE-
                    # IDENTICAL to the immediately prior recovery attempt's
                    # own candidate for this exact (owner, required files)
                    # pair proves the requirement was never actually acted
                    # on, regardless of what the owner's own gates say.
                    # Live incident this closes: pom.xml regenerated
                    # identically across 3 separate generations, each one
                    # locally "passing" (nothing there checks whether
                    # <mainClass> actually matches the real entrypoint
                    # class), burning a full consumer-retry cycle each time
                    # before the SAME unresolved defect surfaced again.
                    candidate_fingerprint = tuple(
                        deps.read_file_revision(os.path.join(plan_workspace_path, path))
                        for path in sorted(required_owner_files)
                    )
                    recovery_no_progress = (
                        bool(owner_result.get("quality_gates_passed"))
                        and recovery_candidate_fingerprints.get(fingerprint_key) == candidate_fingerprint
                    )
                    recovery_candidate_fingerprints[fingerprint_key] = candidate_fingerprint
                    owner_local_accepted = (
                        bool(owner_result.get("quality_gates_passed"))
                        and not owner_undeclared
                        and set(required_owner_files).issubset(owner_written)
                        and not recovery_no_progress
                    )
                    if recovery_no_progress:
                        deps.logger.warning(
                            "RECOVERY_NO_PROGRESS requirement_id=%s owner=%s generation=%d - "
                            "the regenerated candidate for %s is byte-identical to the immediately "
                            "prior recovery attempt for this exact (owner, required files) pair - "
                            "the owner's own local gates passed, but this is not treated as "
                            "legitimate recovery progress.",
                            candidate_requirement_id, owner_id, generation, sorted(required_owner_files),
                        )
                    # This exact (origin, owner, files) tuple has now been
                    # evaluated once for this cycle - the NEXT scope
                    # conflict for this same tuple (if any) is a new
                    # generation, eligible for its own fresh requirement
                    # rather than being silently treated as a continuation
                    # of this one. Advanced immediately (not deferred to
                    # plan finalization below) so it advances exactly once
                    # per group regardless of what the REST of the plan
                    # goes on to do.
                    recovery_generation_by_key[generation_key] = generation + 1
                    invoked_group_records.append({
                        "owner_id": owner_id,
                        "required_owner_files": required_owner_files,
                        "invalidated": invalidated,
                        "candidate_requirement_id": candidate_requirement_id,
                        "generation": generation,
                        "cross_owner_obligation": cross_owner_obligation,
                        "owner_result": owner_result,
                    })
                    if not owner_local_accepted:
                        plan_locally_accepted = False
                        break
                    # Group locally accepted - fold its real, on-disk output
                    # forward before the NEXT group runs. This is what gives
                    # a later group free candidate visibility into an
                    # earlier group's fix: _invoke_bounded_subtask already
                    # writes into plan_workspace_path, the ONE persistent
                    # git worktree shared by every subtask in this whole
                    # enforce run (unlike MA9's own in-memory candidate_view,
                    # which exists only because a single subtask's OWN
                    # Developer calls don't hit disk until that whole
                    # attempt's Quality Gates pass - not the situation here,
                    # see the implementation spec's §2).
                    for path in owner_result.get("files") or []:
                        try:
                            with open(
                                os.path.join(plan_workspace_path, path), "r",
                                encoding="utf-8", errors="replace",
                            ) as fh:
                                owner_content = fh.read()
                        except OSError:
                            continue
                        projection = deps.project_implementation_source(
                            owner_content, path,
                            deps._ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                            reason="recovered_upstream_subtask",
                        )
                        established_file_context[path] = projection.content
                        established_provenance.record(path, owner_id)
                    # Correctness Continuity Part C: the reopened owner may
                    # itself be a relationship's consumer (not just its
                    # producer) - no-op when it isn't.
                    deps._evaluate_integration_obligations(
                        plan, obligation_ledger, owner_id, established_file_context, owner_position,
                        provenance=established_provenance,
                    )
                    exec_plan.completed_group_ids = exec_plan.completed_group_ids + (group_id,)

                # Recovery Execution Contract Invariant 3, generalized
                # (implementation spec §10): every group's own local gates
                # passing is NECESSARY but not SUFFICIENT - "group 1 PASS +
                # group 2 PASS" is not "recovery PASS." Only after EVERY
                # group in this plan locally accepted does the plan-level
                # acceptance question even get asked, and only after THAT
                # passes does the originating consumer ever get resumed -
                # exactly once per plan, never once per group (the review's
                # own explicit anti-circularity requirement).
                # `call_result` is deliberately NEVER reset here - it must
                # keep whatever value it already held (the originating
                # subtask's own failed attempt, or a prior cycle's consumer
                # retry) whenever THIS cycle never reaches the consumer
                # invocation below, exactly like the original single-owner
                # code (which never declared/reset it inside this loop
                # either) - `subtask_call_results.append(call_result)` after
                # this whole loop, and the quality_gates_passed/
                # plan_scope_conflict reads right after that, require it to
                # always be a real dict, never None.
                acceptance_precheck: Optional[bool] = None
                consumer_invoked_this_cycle = False
                plan_recovery_accepted: Optional[bool] = None
                if plan_locally_accepted:
                    all_required_files = sorted(
                        p.artifact for g in exec_plan.groups for p in g.participants
                    )
                    acceptance_precheck = deps._evaluate_recovery_acceptance_precheck(
                        scope_conflict=scope_conflict,
                        plan_workspace_path=plan_workspace_path,
                        required_owner_files=all_required_files,
                    )
                    if acceptance_precheck is False:
                        deps.logger.warning(
                            "RECOVERY_ACCEPTANCE_UNSATISFIED plan_id=%s - a deterministic "
                            "pre-consumer acceptance check found the originating recovery "
                            "requirement still unmet for %s, even though every group's own "
                            "local gates passed and every candidate genuinely changed from "
                            "its prior state.",
                            exec_plan.id, all_required_files,
                        )
                        plan_recovery_accepted = False
                    else:
                        approved_stage_states[subtask.id] = "in_progress"
                        control_state = control_state.with_updates(
                            subtask_states={**control_state.subtask_states, subtask.id: "in_progress"},
                        )
                        deps.save_approved_plan(
                            workspace_path, plan.plan_id,
                            deps.build_approved_plan_document(
                                plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                                stage_states=approved_stage_states, lifecycle_state="in_progress",
                            ),
                        )
                        control_state = _persist_control_state(control_state)
                        owners_repaired = ", ".join(g.owner_subtask_id for g in exec_plan.groups)
                        deps.logger.info(
                            "CONSUMER_RETRY_STARTED subtask=%s plan_id=%s owners=%s",
                            subtask.id, exec_plan.id, owners_repaired,
                        )
                        call_result = await _invoke_bounded_subtask(
                            subtask, position,
                            recovery_context=(
                                "--- upstream recovery completed ---\n"
                                f"Upstream owner(s) {owners_repaired} were repaired and "
                                "re-verified. Re-run this consumer against the updated "
                                "workspace."
                            ),
                            execution_role="consumer_retry",
                        )
                        consumer_invoked_this_cycle = True
                        # MA8.1 (PRV-06, 2026-08-29) / Recovery Execution
                        # Contract Invariant 3 (2026-08-29): the obligation's
                        # own acceptance condition IS this consumer_retry
                        # call's Quality Gates - not a separate, speculative
                        # check of any owner's file content (which an
                        # owner's own narrow gate has no way to evaluate
                        # against a DOWNSTREAM requirement anyway - confirmed
                        # live: pom.xml's own compile check happily accepted
                        # a still-incomplete manifest). plan_recovery_accepted
                        # is now the SAME signal OWNER_RECOVERY_COMPLETED
                        # itself reports below - "group local PASS" is never
                        # conflated with "recovery PASS" anywhere in this
                        # cycle.
                        plan_recovery_accepted = bool(call_result.get("quality_gates_passed"))
                else:
                    plan_recovery_accepted = False

                deps.logger.info(
                    "RECOVERY_EXECUTION_PLAN_COMPLETED plan_id=%s passed=%s",
                    exec_plan.id, plan_recovery_accepted,
                )
                exec_plan.status = (
                    deps.RecoveryExecutionPlanStatus.SATISFIED if plan_recovery_accepted
                    else deps.RecoveryExecutionPlanStatus.REJECTED
                )
                # Finalize EVERY group that was actually invoked this cycle
                # with the SAME plan-level outcome - atomic by construction:
                # a plan that fails after every group locally passed (the
                # plan-level acceptance check, or the consumer's own retry)
                # still marks every touched owner NEEDS_REVIEW, never leaves
                # an earlier group's owner silently stranded "in_progress."
                for record in invoked_group_records:
                    owner_id = record["owner_id"]
                    required_owner_files = record["required_owner_files"]
                    cross_owner_obligation = record["cross_owner_obligation"]
                    candidate_requirement_id = record["candidate_requirement_id"]
                    generation = record["generation"]
                    owner_result = record["owner_result"]
                    invalidated = record["invalidated"]
                    if cross_owner_obligation is not None and plan_recovery_accepted:
                        obligation_ledger.record(deps.ObligationRecord(
                            id=cross_owner_obligation.id,
                            kind=deps.ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
                            status=deps.ObligationStatus.SATISFIED,
                            # Sticky, matching _get_or_create_cross_owner_obligation's own
                            # "recurring" branch (control-plane audit, 2026-08-30) - this
                            # SATISFIED closing record must carry the SAME authority the
                            # obligation was actually created with, never re-hardcoded
                            # DETERMINISTIC regardless of whether the originating evidence
                            # was advisory self-diagnosis.
                            authority=cross_owner_obligation.authority,
                            description=cross_owner_obligation.description,
                            source="workflow_controller.owner_recovery",
                            revision=len(plan_recovery_events),
                            evidence=cross_owner_obligation.evidence,
                            owner_subtask_id=cross_owner_obligation.owner_subtask_id,
                            terminal_required=False,
                            repair_scope=cross_owner_obligation.repair_scope,
                        ))
                    deps.logger.info(
                        "OWNER_RECOVERY_COMPLETED requirement_id=%s owner=%s generation=%d passed=%s",
                        candidate_requirement_id, owner_id, generation, bool(plan_recovery_accepted),
                    )
                    plan_recovery_events.append({
                        "failed_subtask": subtask.id,
                        "reopened_owner": owner_id,
                        "required_repair_files": sorted(required_owner_files),
                        "classification": scope_conflict.get(
                            "classification", "PLAN_SCOPE_INSUFFICIENT",
                        ),
                        "reason": scope_conflict.get("reason"),
                        "invalidated_subtasks": invalidated,
                        "ownership_revalidated": True,
                        "revalidation_basis": "unique approved upstream file owner",
                        "owner_recovery_passed": bool(plan_recovery_accepted),
                        "requirement_id": candidate_requirement_id,
                        "generation": generation,
                    })
                    approved_stage_states[owner_id] = "completed" if plan_recovery_accepted else "needs_review"
                    control_state = record_committed_effects(control_state.with_updates(
                        subtask_states={
                            **control_state.subtask_states,
                            owner_id: "completed" if plan_recovery_accepted else "needs_review",
                        },
                    ), owner_id, owner_result)
                    deps.save_approved_plan(
                        workspace_path, plan.plan_id,
                        deps.build_approved_plan_document(
                            plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                            stage_states=approved_stage_states,
                            lifecycle_state="in_progress" if plan_recovery_accepted else "needs_review",
                        ),
                    )
                    control_state = _persist_control_state(control_state)
                    # Matches the original single-owner code's own
                    # distinction exactly: the FINAL reported
                    # subtask_results list is only ever downgraded to
                    # NEEDS_REVIEW when the consumer was NEVER actually
                    # invoked this cycle - whether because a group's own
                    # local gate failed, OR because every group passed
                    # locally but the plan-level acceptance precheck
                    # rejected the plan before ever resuming the consumer
                    # (RECOVERY_ACCEPTANCE_UNSATISFIED - see
                    # test_enforce_owner_recovery_wrong_fix_blocks_
                    # completion_and_never_resumes_consumer). When the
                    # consumer WAS invoked and its own retry simply failed
                    # again, every touched owner's own local work was fine -
                    # subtask_results stays untouched, since a LATER cycle
                    # may still resolve the requirement (see
                    # test_enforce_permits_a_second_distinct_generation_
                    # recovery_on_the_same_owner). approved_stage_states/
                    # control_state still reflect needs_review uniformly
                    # either way (observability/persistence, unaffected by
                    # this distinction).
                    if not consumer_invoked_this_cycle:
                        for result_index, prior_result in enumerate(subtask_results):
                            if prior_result.subtask_id == owner_id:
                                subtask_results[result_index] = deps.SubtaskResult(
                                    subtask_id=owner_id,
                                    status=deps.SubtaskStatus.NEEDS_REVIEW,
                                    execution_method=deps.ExecutionMethod.MODEL.value,
                                    error="upstream owner failed bounded plan recovery verification",
                                    reason_codes=("PLAN_RECOVERY_OWNER_FAILED",),
                                )
                                break

                recovery_plan_generation_by_subtask[subtask.id] = (
                    recovery_plan_generation_by_subtask.get(subtask.id, 0) + 1
                )
                if plan_recovery_accepted:
                    break
                if not consumer_invoked_this_cycle:
                    # The plan never reached the consumer (a group failed
                    # locally, or the plan-level acceptance precheck
                    # rejected it before ever resuming the consumer) - no
                    # new scope_conflict to continue on, matching the
                    # original single-owner "not owner_local_accepted"
                    # branch's own unconditional break.
                    break
                scope_conflict = call_result.get("plan_scope_conflict") or {}
                if not scope_conflict:
                    break
                continue
            # ENFORCE-VERIFICATION-UNIT-STOP-BEFORE-TERMINAL-GATE-001 (BACKEND-FINAL-CLOSURE-005, measured on
            # P4-T4): a verification-only unit that fails and cannot change its own inputs used to end the run
            # right there (LR-R1-P4's truthful terminal) - but the unit whose work it judged never heard of the
            # failure. The unit's own deterministic gate output IS new information for the nearest completed
            # mutating ancestor: that owner is reopened ONCE per verification unit with the evidence; only a
            # candidate that actually changed the owner's files, passed the owner's own gates and stayed inside
            # its declared scope is folded forward, and the verification unit then runs once more on the new
            # tree. An unchanged or failed owner candidate leaves the original failure standing (GR-R0: no retry
            # without new information; nothing here ever turns a failure into success).
            if (
                call_result.get("no_progress_reason") == deps.VERIFICATION_RETRY_NO_CHANGE_POSSIBLE
                and not subtask.planned_files
                and subtask.id not in verification_owner_reopened
            ):
                verification_owner_reopened.add(subtask.id)
                owner = deps.nearest_mutating_upstream_owner(
                    plan, subtask,
                    [sid for sid, stage_state in approved_stage_states.items()
                     if stage_state == deps.SubtaskStatus.COMPLETED.value],
                )
                if owner is not None:
                    owner_files = sorted(pf.path for pf in owner.planned_files if pf.action != deps.FileAction.DELETE)
                    owner_position = order.index(owner.id) + 1 if owner.id in order else position
                    before = tuple(deps.read_file_revision(os.path.join(plan_workspace_path, path)) for path in owner_files)
                    evidence = deps.verification_failure_evidence(call_result)
                    deps.logger.warning(
                        "VERIFICATION_OWNER_RECOVERY_STARTED verification=%s owner=%s files=%s",
                        subtask.id, owner.id, owner_files,
                    )
                    owner_result = await _invoke_bounded_subtask(
                        owner, owner_position, execution_role="verification_owner_recovery",
                        recovery_context=deps.verification_owner_recovery_context(
                            verification_subtask_id=subtask.id, owner_id=owner.id, owner_files=owner_files,
                            evidence=evidence,
                        ),
                    )
                    after = tuple(deps.read_file_revision(os.path.join(plan_workspace_path, path)) for path in owner_files)
                    owner_undeclared = sorted(
                        set(owner_result.get("files") or []) - {pf.path for pf in owner.planned_files})
                    # Acceptance needs ALL of: the owner's gates passed, its final review was performed (a
                    # candidate whose review is refused or deadline-stopped after the gates IS applied with
                    # quality_gates_passed False - independent review F1, workflow.py's review refusal path),
                    # its declared verification COMPLETED (the same evaluation the planned path applies), a
                    # byte change in the plan worktree, and no file outside its declared scope (MA6 invariant 4).
                    owner_status, _owner_error, _owner_codes = deps._evaluate_subtask_verification(owner, owner_result)
                    owner_accepted = (
                        owner_result.get("quality_gates_passed") is True
                        and not owner_result.get("final_review_refusal")
                        and owner_status == deps.SubtaskStatus.COMPLETED
                        and after != before
                        and not owner_undeclared
                    )
                    deps.logger.warning(
                        "VERIFICATION_OWNER_RECOVERY_%s verification=%s owner=%s changed=%s gates=%s undeclared=%s",
                        "ACCEPTED" if owner_accepted else "REJECTED", subtask.id, owner.id, after != before,
                        bool(owner_result.get("quality_gates_passed")), owner_undeclared,
                    )
                    # The decision in the attempt-evidence store (LR-R1-M1): a rejected reopen is the run's
                    # terminal cause (the verification failure stands and the loop stops below); an accepted
                    # one is followed by the verification rerun, whose own unit result then speaks.
                    from kriya.workflow.run_events import EventAuthority, RunEvent

                    deps.attempt_evidence_scope.mirror_event(RunEvent(
                        kind=("verification.owner_recovery_accepted" if owner_accepted
                              else "verification.owner_recovery_rejected"),
                        attempt=0, source="workflow_controller.enforce", authority=EventAuthority.AUTHORITATIVE,
                        message=(f"upstream owner {owner.id} reopened for verification unit {subtask.id}: "
                                 + ("accepted, the verification runs again" if owner_accepted
                                    else "no accepted change, the verification failure stands")),
                        details={
                            "verification_subtask_id": subtask.id, "owner_subtask_id": owner.id,
                            "owner_files": owner_files, "changed": after != before,
                            "owner_quality_gates_passed": bool(owner_result.get("quality_gates_passed")),
                            "undeclared_files": owner_undeclared,
                            "reason_codes": [deps.VERIFICATION_RETRY_NO_CHANGE_POSSIBLE],
                            "error": None if owner_accepted else (
                                "the reopened owner produced no accepted change; the verification unit's "
                                f"failure stands ({deps.VERIFICATION_RETRY_NO_CHANGE_POSSIBLE})"),
                        },
                    ))
                    if owner_accepted:
                        for path in owner_result.get("files") or []:
                            try:
                                with open(os.path.join(plan_workspace_path, path), "r", encoding="utf-8",
                                          errors="replace") as fh:
                                    owner_content = fh.read()
                            except OSError:
                                continue
                            projection = deps.project_implementation_source(
                                owner_content, path, deps._ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                                reason="recovered_upstream_subtask",
                            )
                            established_file_context[path] = projection.content
                            established_provenance.record(path, owner.id)
                        deps._evaluate_integration_obligations(
                            plan, obligation_ledger, owner.id, established_file_context, owner_position,
                            provenance=established_provenance,
                        )
                        control_state = record_committed_effects(control_state, owner.id, owner_result)
                        # Persisted before the rerun: a crash between the fold and the verification leaves the
                        # owner's real written set behind (independent review F8). The reopen bound itself is
                        # per process: a resumed run that re-verifies and fails may reopen once more.
                        control_state = _persist_control_state(control_state)
                        call_result = await _invoke_bounded_subtask(
                            subtask, position, execution_role="verification_rerun",
                            recovery_context=(
                                "--- upstream recovery completed ---\n"
                                f"Upstream owner {owner.id} was repaired after this verification failed; "
                                "the current tree is the one to verify."
                            ),
                        )
            subtask_call_results.append(call_result)

            quality_gates_passed = bool(call_result.get("quality_gates_passed"))
            # MA6 invariant 4 (kriya/workflow/workflow_types.py::SubtaskResult's
            # own docstring: "a subtask may not modify undeclared files
            # silently... deciding what to do about it is the calling
            # orchestrator's job") - finally enforced here, not just
            # detectable. Skipped entirely when the subtask declared NO
            # planned_files at all (nothing to enforce against, would
            # otherwise guarantee a false violation on every file written).
            undeclared_files = (
                sorted(set(call_result.get("files") or []) - set(predetermined_files))
                if predetermined_files else []
            )
            verification_status, verification_error, verification_reason_codes = (
                deps._evaluate_subtask_verification(subtask, call_result)
            )
            passed = (
                quality_gates_passed
                and not undeclared_files
                and verification_status == deps.SubtaskStatus.COMPLETED
            )
            review_refusal = call_result.get("final_review_refusal")
            if not quality_gates_passed:
                scope_conflict = call_result.get("plan_scope_conflict")
                if review_refusal:
                    # PROMPT-BUDGET-FIT-001C: the subtask's gates passed but
                    # its final review was refused before inference.
                    error = f"subtask final review not performed: {review_refusal.get('detail')}"
                elif scope_conflict:
                    error = deps.subtask_scope_conflict_report(scope_conflict)[0]
                else:
                    error = f"subtask did not pass Quality Gates (status={call_result.get('status')!r})"
            elif undeclared_files:
                error = (
                    f"subtask wrote file(s) outside its own declared planned_files scope: "
                    f"{undeclared_files!r} (declared: {predetermined_files!r}) - refusing to accept "
                    "a Quality-Gates-passed result that silently broadened the validated subtask's scope"
                )
            elif verification_error:
                error = verification_error
            else:
                error = None
            result = deps.SubtaskResult(
                subtask_id=subtask.id,
                status=(
                    deps.SubtaskStatus.COMPLETED if passed
                    else deps.SubtaskStatus.NEEDS_REVIEW if call_result.get("plan_scope_conflict")
                    else deps.SubtaskStatus.FAILED if not quality_gates_passed or undeclared_files
                    else verification_status
                ),
                execution_method=deps.ExecutionMethod.MODEL.value,
                undeclared_files=tuple(undeclared_files), error=error,
                reason_codes=(
                    deps.subtask_scope_conflict_report(call_result["plan_scope_conflict"])[1]
                    if call_result.get("plan_scope_conflict")
                    else (review_refusal.get("reason_code"),) if review_refusal
                    # ENFORCE-VERIFIED-NO-CHANGE-001: the unit completed with
                    # no mutation, on deterministic evidence - recorded, never
                    # implied by an empty file list.
                    else tuple(verification_reason_codes) + ("VERIFIED_NO_CHANGE",)
                    if passed and call_result.get("completion_kind") == "VERIFIED_NO_CHANGE"
                    else verification_reason_codes
                ),
            )
            subtask_results.append(result)
            deps.record_subtask_attempt(ledger, plan, result, attempt=1)

            # Persisted incrementally and fail-closed, not only at
            # the end of the whole plan - a mid-plan crash must leave
            # accurate resumable state behind, matching run_milestones()'s
            # own completed_milestone_ids sidecar convention.
            # The real, applied file list (not the plan's mere upfront
            # planned_files declaration) - see ControlState.subtask_
            # written_files's own docstring for why a later re-plan
            # needs this to detect abandoned residue. S18d P2b: only what
            # this invocation committed, added to the unit's record.
            control_state = record_committed_effects(control_state.with_updates(
                subtask_states={**control_state.subtask_states, subtask.id: result.status.value},
            ), subtask.id, call_result)
            approved_stage_states[subtask.id] = result.status.value
            plan_lifecycle_state = (
                "in_progress" if passed else
                "needs_review" if result.status == deps.SubtaskStatus.NEEDS_REVIEW else "failed"
            )
            deps.save_approved_plan(
                workspace_path, plan.plan_id,
                deps.build_approved_plan_document(
                    plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                    stage_states=approved_stage_states, lifecycle_state=plan_lifecycle_state,
                ),
            )
            control_state = _persist_control_state(control_state)

            if not passed:
                deps.logger.warning(
                    f"WorkflowController enforce run {run_id!r}: stopped at subtask {subtask_id!r} "
                    f"({position}/{total}) - did not pass Quality Gates."
                    + (f" Reason: {error}" if error else "")
                )
                # Found live, 2026-08-25 (ignite_qpid_protocol): a subtask's own
                # internal run_generation_workflow() call can hit KnowledgeGuard's
                # stage-0 gap check (kriya/workflow/workflow.py) and return
                # status="knowledge_gap" - previously this got flattened into an
                # ordinary "did not pass Quality Gates" failure with the real
                # gap_report silently discarded, so the CLI's own already-built
                # confirmation UX (interactive prompt / --knowledge-policy / -y,
                # kriya/cli.py's `res.get("status") == "knowledge_gap"` check)
                # could never fire for enforce mode at all - the run just produced
                # zero files with zero visible explanation. Scoped to the SAME
                # safety boundary _StructuredPlanUnavailable already uses (only
                # when literally nothing has been established yet - a knowledge
                # gap on a LATER subtask, after real prior work exists, stays the
                # ordinary generic failure rather than inviting a fresh top-level
                # retry that would silently re-run already-completed subtasks).
                if call_result.get("status") == "knowledge_gap" and not established_file_context:
                    knowledge_gap_break = {
                        "status": "knowledge_gap",
                        "gap_report": call_result.get("gap_report"),
                        "run_id": call_result.get("run_id", run_id),
                    }
                break

            for path in call_result.get("files", []):
                try:
                    with open(os.path.join(plan_workspace_path, path), "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError as e:
                    deps.logger.warning(
                        f"WorkflowController enforce run {run_id!r}: could not capture established "
                        f"content for {path!r} after subtask {subtask_id!r}: {e}"
                    )
                    continue
                projection = deps.project_implementation_source(
                    content, path, deps._ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                    reason="established_by_earlier_subtask",
                )
                established_file_context[path] = projection.content
                established_provenance.record(path, subtask_id)

            # Correctness Continuity Part C (PRV-06, 2026-08-29): this
            # subtask just finished and its files are now in
            # established_file_context - the earliest point any
            # relationship it's a CONSUMER for can be deterministically
            # (re-)evaluated. No-op when the plan declares no integration
            # relationships at all (every plan predating this feature).
            deps._evaluate_integration_obligations(
                plan, obligation_ledger, subtask_id, established_file_context, position,
                provenance=established_provenance,
            )

        # Authoritative scope recovery may merge/remove a stage. Completion
        # must be measured against the revalidated CURRENT plan, not the
        # original loop's pre-revision `total`.
        current_plan_subtask_ids = {subtask.id for subtask in plan.subtasks}
        latest_status_by_subtask = {
            result.subtask_id: result.status for result in subtask_results
        }
        subtasks_completed = deps.all_subtasks_completed(
            current_plan_subtask_ids, latest_status_by_subtask,
        )
        all_completed = subtasks_completed

        gate_report = deps.TERMINAL_GATES_NOT_RUN
        terminal_observability_errors: List[Dict[str, str]] = []
        post_commit_persistence_errors: List[Dict[str, str]] = []
        workspace_commit_completed = False
        workspace_commit_evidence: Optional[Dict[str, Any]] = None
        workspace_commit_failure: Optional[Dict[str, Any]] = None
        committed_contract_registry: Optional[Dict[str, Any]] = None
        terminal_commit_started = False
        commit_journal_before: Any = None

        async def _emit_terminal_event(event_name: str, **data: Any) -> None:
            """Terminal lifecycle events are telemetry, never correctness gates."""
            events = getattr(kernel, "events", None) if kernel is not None else None
            if events is None:
                return
            payload = {"run_id": run_id, "plan_id": plan.plan_id, **data}
            try:
                await events.emit(event_name, payload)
            except Exception as error:
                terminal_observability_errors.append({
                    "event": event_name,
                    "error": f"{type(error).__name__}: {error}",
                })
                deps.logger.error(
                    "WorkflowController enforce run %r: terminal event %r failed: %s",
                    run_id, event_name, error,
                )

        async def _emit_gate_outcome(
            gate: str, status: str, reason: Optional[str] = None,
        ) -> None:
            payload: Dict[str, Any] = {"gate": gate, "status": status}
            if reason:
                payload["reason"] = reason
            await _emit_terminal_event("terminal_gate_outcome", **payload)

        def _needs_review() -> bool:
            return (
                any(r.status == deps.SubtaskStatus.NEEDS_REVIEW for r in subtask_results)
                or gate_report.artifact_error is not None
                or workspace_commit_failure is not None
            )

        def _lifecycle_of(completed: bool) -> str:
            """The one rule for the approved plan's final lifecycle, shared by the ordinary path and the error handler."""
            return "completed" if completed else "needs_review" if _needs_review() else "failed"

        def _discard_sandbox_completions() -> None:
            # Sandbox-only completion state cannot be resumed after the candidate is discarded. All-or-nothing: the plan's
            # stage states and the control state change together, so a failing save leaves them in agreement.
            nonlocal approved_stage_states, control_state
            discarded_stage_states = {
                subtask_id: ("pending" if status == "completed" else status)
                for subtask_id, status in approved_stage_states.items()
            }
            discarded_control_state = _persist_control_state(control_state.with_updates(
                subtask_states={
                    subtask_id: ("pending" if status == "completed" else status)
                    for subtask_id, status in control_state.subtask_states.items()
                },
                subtask_written_files={},
            ))
            approved_stage_states = discarded_stage_states
            control_state = discarded_control_state

        def _adopt_sandbox_completions() -> None:
            # The sandbox's completions are now the workspace's.
            nonlocal control_state
            control_state = _persist_control_state(control_state.with_updates(subtask_completion_scope="workspace"))

        def _save_plan_after_error(lifecycle: str) -> None:
            # The error handler's plan save: logged, never replaces the error being handled.
            try:
                deps.save_approved_plan(
                    workspace_path, plan.plan_id,
                    deps.build_approved_plan_document(
                        plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                        stage_states=approved_stage_states, lifecycle_state=lifecycle,
                    ),
                )
            except Exception as finalize_error:
                deps.logger.error(
                    "WorkflowController enforce run %r: final plan state could not be saved after an error: %s",
                    run_id, finalize_error,
                )

        def _save_derived_artifacts(records: Any) -> None:
            # Artifact facts derived from the verified candidate, persisted only after the source commit (one step shared by the
            # ordinary path and the error handler: an interrupt after a durable commit still records them).
            artifact_registry = deps.load_artifact_registry(workspace_path)
            for record in records:
                artifact_registry.record(record)
            deps.save_artifact_registry(workspace_path, artifact_registry)

        try:
            if subtasks_completed:
                # PRD-004: every blocking terminal gate runs against the one
                # isolated candidate. The real workspace remains immutable
                # until this complete set has passed (PRD-030: the gates are
                # terminal_gate_service's, the commit commit_service's).
                deps.mark_run_stage(workspace_path, deps.RunLifecycle.VERIFYING)
                await _emit_terminal_event(
                    "terminal_gates_started", candidate_workspace=plan_workspace_path,
                )
                engine_config = getattr(getattr(deps.workflow_engine, "kernel", None), "config", None)
                # The validators are bound from this module's names on every
                # run, so they stay the one place the gates' checks are chosen.
                gate_service = deps.TerminalGateService(deps.TerminalGateValidators(
                    find_migration_incomplete=deps.find_migration_incomplete,
                    validate_stack_contract_artifacts=deps.validate_stack_contract_artifacts,
                    enforce_preserved_reference_terminal_integrity=deps.enforce_preserved_reference_terminal_integrity,
                    blocking_requirements=deps.blocking_requirements,
                    verify_original_requirements=deps._verify_original_requirements,
                    evaluate_static_analysis=deps.StaticAnalysisService(engine_config).evaluate_candidate
                    if engine_config is not None else None,
                ))
                gate_report = await gate_service.run(deps.TerminalGateRequest(
                    plan=plan, goal=goal, candidate_root=plan_workspace_path, workspace_path=workspace_path,
                    migration_resolution=migration_resolution, obligation_ledger=obligation_ledger,
                    requirement_set=requirement_set, autonomy=getattr(engine_config, "autonomy", None),
                    spec_compliance=getattr(deps.workflow_engine, "spec_compliance", None),
                    milestone_id=control_state.current_milestone_id or run_id,
                    commit_batch=lambda: deps.plan_terminal_writes(
                        plan, plan_workspace_path, workspace_path, original_plan_revisions,
                    ),
                    static_analysis_candidate=deps.StaticAnalysisCandidate(
                        materialize=lambda: deps.plan_terminal_writes(
                            plan, plan_workspace_path, workspace_path, original_plan_revisions,
                        ),
                        workspace_path=workspace_path, run_id=run_id,
                        unit_id=control_state.current_milestone_id or run_id,
                        in_place=plan_workspace_path == workspace_path,
                    ),
                    acceptance=deps.bound_acceptance(deps.workflow_engine),
                    acceptance_approval=deps.bound_approval(deps.workflow_engine),
                    verification_contract=verification_contract, engine=deps.workflow_engine,
                ), _emit_gate_outcome)
                all_completed = gate_report.commit_eligible

                if all_completed:
                    await _emit_terminal_event("commit_eligible")
                    if plan_workspace_path != workspace_path:
                        commit_journal_before = deps.run_commit_states(workspace_path, run_id)
                        terminal_commit_started = True
                    commit = deps.commit_verified_candidate(gate_report, deps.TerminalCommitRequest(
                        plan=plan, candidate_root=plan_workspace_path, workspace_path=workspace_path,
                        original_plan_revisions=original_plan_revisions, approved_plan_hash=current_plan_hash,
                        obligation_ledger=obligation_ledger, run_id=run_id,
                        contract_transition_for=lambda writes, transaction_id: deps._enforce_contract_transition(
                            writes, workspace_path=workspace_path, goal=goal, plan=plan,
                            transaction_id=transaction_id, subtask_call_results=subtask_call_results,
                        ),
                        static_analysis=deps.commit_guard(engine_config, gate_report.static_analysis),
                    ))
                    workspace_commit_completed = commit.completed
                    workspace_commit_failure = commit.failure
                    workspace_commit_evidence = commit.evidence
                    committed_contract_registry = commit.contract_registry
                    post_commit_persistence_errors.extend(commit.record_errors)
                    if workspace_commit_failure is not None:
                        all_completed = False
                        await _emit_terminal_event(
                            "workspace_commit_failed", **workspace_commit_failure,
                        )
                    if workspace_commit_completed:
                        await _emit_terminal_event(
                            "workspace_commit_completed",
                            commit_evidence=workspace_commit_evidence,
                        )
                    if workspace_commit_completed and plan_workspace_path != workspace_path:
                        # The sandbox's completions are now the workspace's.
                        # A failure here leaves "candidate" (nothing reused
                        # later): safe, and never undoes the commit.
                        try:
                            _adopt_sandbox_completions()
                        except Exception as error:
                            post_commit_persistence_errors.append({
                                "operation": "save_control_state",
                                "error": f"{type(error).__name__}: {error}",
                            })

            if not all_completed and plan_workspace_path != workspace_path:
                _discard_sandbox_completions()
        except BaseException:
            # D-F1: an unexpected error (the terminal gates or the commit raised) ends the run as an error, and the durable
            # state must say what the ordinary path would have persisted for the decision already made. Before a durable
            # commit: the sandbox completions are discarded and the plan saved with the lifecycle the ordinary path derives
            # (`failed`, or `needs_review` when a returned commit failure or a review subtask already decided so). After a
            # durable commit the workspace holds the verified work: the completions are the workspace's and the plan
            # `completed`, exactly the ordinary path's two post-commit steps. Never masks the error being handled.
            if terminal_commit_started and not workspace_commit_completed and workspace_commit_failure is None:
                # The commit raised (an interrupt included) before the service handed its result back. The local flags say nothing
                # then: the journal decides whether the workspace holds the candidate, and an unproven commit is UNCERTAIN - the
                # existing outcome - so the candidate is kept for explicit recovery and the plan is needs_review, never `failed`.
                # P-2: the unproven marker is set BEFORE the lookup and only the journal's proof clears it. A second interrupt
                # (a BaseException the `except Exception` below cannot take) escapes while the lookup runs, and the `finally`
                # then still finds the candidate marked retained instead of removing the only copy of the verified bytes.
                workspace_commit_failure = {
                    "reason_code": "WORKSPACE_COMMIT_UNCERTAIN", "workspace_state": "UNCERTAIN",
                    "error": "the terminal commit was interrupted before its outcome was proven",
                }
                # The plan document gets the same pessimistic-until-proof treatment: needs_review is durable BEFORE the lookup
                # (stage states and completion scope are untouched: the COMMITTED proof below still adopts them), so a second
                # interrupt cannot leave `in_progress` over an unproven commit. The proof below overwrites it.
                _save_plan_after_error(_lifecycle_of(False))
                try:
                    settled = deps.interrupted_commit_outcome(workspace_path, run_id, commit_journal_before)
                except Exception as finalize_error:        # never replaces the error being handled: unproven means UNCERTAIN
                    deps.logger.error(
                        "WorkflowController enforce run %r: the terminal commit's outcome could not be read after an error: %s",
                        run_id, finalize_error,
                    )
                    settled = "UNCERTAIN"
                if settled == "COMMITTED":
                    workspace_commit_completed = True
                    workspace_commit_failure = None
                elif settled is None:                       # proven: nothing durable happened, or it was rolled back
                    workspace_commit_failure = None
            if workspace_commit_completed:
                if plan_workspace_path != workspace_path:
                    try:
                        _adopt_sandbox_completions()
                    except Exception as finalize_error:
                        deps.logger.error(
                            "WorkflowController enforce run %r: sandbox completions could not be adopted after an error: %s",
                            run_id, finalize_error,
                        )
                if gate_report.candidate_derived_artifacts:
                    try:
                        _save_derived_artifacts(gate_report.candidate_derived_artifacts)
                    except Exception as finalize_error:
                        deps.logger.error(
                            "WorkflowController enforce run %r: derived artifacts could not be persisted after an error: %s",
                            run_id, finalize_error,
                        )
                error_lifecycle = _lifecycle_of(True)
            else:
                if plan_workspace_path != workspace_path:
                    try:
                        _discard_sandbox_completions()
                    except Exception as finalize_error:
                        deps.logger.error(
                            "WorkflowController enforce run %r: sandbox state could not be finalized after an error: %s",
                            run_id, finalize_error,
                        )
                error_lifecycle = _lifecycle_of(False)
            _save_plan_after_error(error_lifecycle)
            raise
        finally:
            candidate_retained = (
                workspace_commit_failure is not None
                and workspace_commit_failure["workspace_state"] == "UNCERTAIN"
            )
            if plan_workspace_path != workspace_path and not candidate_retained:
                deps.remove_git_worktree(workspace_path, plan_workspace_path)
            elif candidate_retained:
                # The verified candidate is the only copy of the bytes an
                # uncertain commit may have partly applied; keep it for
                # explicit recovery instead of deleting it.
                workspace_commit_failure["retained_candidate_path"] = plan_workspace_path

        needs_review = _needs_review()
        final_plan_lifecycle = _lifecycle_of(all_completed)
        try:
            deps.save_approved_plan(
                workspace_path, plan.plan_id,
                deps.build_approved_plan_document(
                    plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                    stage_states=approved_stage_states, lifecycle_state=final_plan_lifecycle,
                ),
            )
        except Exception as error:
            persistence_error = {
                "operation": "save_approved_plan",
                "error": f"{type(error).__name__}: {error}",
            }
            if workspace_commit_completed:
                post_commit_persistence_errors.append(persistence_error)
            else:
                deps.logger.error(
                    "WorkflowController enforce run %r: final plan persistence failed: %s",
                    run_id, error,
                )

        aggregated: Dict[str, Any] = {
            "status": "success" if all_completed else "needs_review" if needs_review else "failed",
            "quality_gates_passed": all_completed,
            "run_id": run_id,
            "subtask_results": [r.to_dict() for r in subtask_results],
            "files": sorted(established_file_context.keys()),
            "plan_repair_attempts": repair_attempts,
        }
        # PRD-031A: the static-analysis outcome (ACCEPTED_RISK is never PASS).
        aggregated.update(deps.static_analysis_result_fields(gate_report.static_analysis))
        for key, value in gate_report.global_gaps():
            if value:
                aggregated[key] = value
                deps.logger.error("WorkflowController enforce run %r: %s", run_id, value)
        # PRD-020: every original requirement's outcome, as the direct path
        # reports it (the verifier's verdict, or closed by evidence).
        try:
            aggregated["requirements"] = {
                **requirement_set.to_dict(),
                "outcomes": {rid: outcome.value for rid, outcome in
                             deps.requirement_outcomes(obligation_ledger, requirement_set).items()},
                "evidence": deps.requirement_evidence(obligation_ledger, requirement_set),
                # Each verdict with its reason code, verifier and candidate.
                "verdicts": deps.requirement_verdict_details(obligation_ledger, requirement_set),
                # Every closure attempt, closed or not, with why (a requirement
                # left open by missing evidence is otherwise undiagnosable).
                "closure_attempts": list(gate_report.requirement_closure_attempts),
            }
        except Exception as error:
            deps.logger.warning(f"Original requirement outcomes unavailable for the result: {error}")
        if gate_report.artifact_error:
            deps.logger.error(
                "WorkflowController enforce run %r: candidate artifact derivation failed: %s",
                run_id, gate_report.artifact_error,
            )
            aggregated["artifact_error"] = gate_report.artifact_error
        if workspace_commit_evidence is not None:
            aggregated["commit_evidence"] = workspace_commit_evidence
        if workspace_commit_failure is not None:
            aggregated.setdefault("reason_codes", []).append(workspace_commit_failure["reason_code"])
            aggregated["workspace_commit_failure"] = workspace_commit_failure
            deps.logger.error(
                "WorkflowController enforce run %r: terminal workspace commit failed (%s, workspace %s): %s",
                run_id, workspace_commit_failure["reason_code"],
                workspace_commit_failure["workspace_state"], workspace_commit_failure["error"],
            )
        if knowledge_gap_break is not None:
            aggregated["status"] = knowledge_gap_break["status"]
            aggregated["gap_report"] = knowledge_gap_break["gap_report"]
            aggregated["run_id"] = knowledge_gap_break["run_id"]
        scope_conflict_result = next(
            (result.get("plan_scope_conflict") for result in subtask_call_results
             if result.get("plan_scope_conflict")),
            None,
        )
        if scope_conflict_result is not None:
            aggregated["failure_type"] = "PLANNING_ERROR"
            aggregated["recovery"] = "PLAN_REPAIR"
            codes = aggregated.setdefault("reason_codes", [])
            codes.append(deps.PLAN_SCOPE_REVISION_REQUIRED)
            codes.extend(code for code in deps.subtask_scope_conflict_report(scope_conflict_result)[1]
                         if code not in codes)
            aggregated["plan_scope_conflict"] = scope_conflict_result
        if plan_recovery_events:
            aggregated["plan_recovery_events"] = plan_recovery_events
        if subtask_call_results:
            aggregated["last_subtask_result"] = subtask_call_results[-1]
            aggregated["subtask_retry_locality"] = [
                {
                    "subtask_id": r.subtask_id,
                    "generation_calls": cr.get("generation_metrics", {}).get("calls"),
                    "quality_gates_passed": bool(cr.get("quality_gates_passed")),
                }
                for r, cr in zip(subtask_results, subtask_call_results, strict=False)
            ]

        # Artifact facts were derived from the verified candidate. Persist
        # those exact facts only after the source commit succeeds. A failure
        # here is a persistence/observability failure and cannot rewrite the
        # already-established verification result.
        candidate_derived_artifacts = gate_report.candidate_derived_artifacts
        if workspace_commit_completed and candidate_derived_artifacts:
            try:
                _save_derived_artifacts(candidate_derived_artifacts)
                aggregated["derived_artifacts"] = [
                    record.to_dict() for record in candidate_derived_artifacts
                ]
            except Exception as error:
                deps.logger.error(
                    "WorkflowController enforce run %r: post-commit artifact persistence failed: %s",
                    run_id, error,
                )
                post_commit_persistence_errors.append({
                    "operation": "save_artifact_registry",
                    "error": f"{type(error).__name__}: {error}",
                })
        # PRD-029: the ContractRegistry transition committed with the source.
        aggregated["contract_registry"] = committed_contract_registry
        if terminal_observability_errors:
            aggregated["observability_errors"] = terminal_observability_errors
        if post_commit_persistence_errors:
            aggregated["post_commit_persistence_errors"] = post_commit_persistence_errors
        if enforce_resume_decision is not None:
            aggregated["resume_decision"] = enforce_resume_decision
        # VERIFICATION-CONTRACT-003: the sealed contract this run executed under
        # (its report; the trace row derives the contract events from it).
        aggregated["verification_contract"] = {**verification_contract.report(), "stored_path": sealed_contract_path}
        if baseline_report is not None:
            aggregated["baseline_authority"] = baseline_report.to_dict()

        report = deps.build_verification_report(plan.acceptance_criteria)
        return aggregated, plan, tuple(subtask_results), ledger.all(), report, control_state


# ----------------------------------------------------------------------------- S18d P2b (OI-150 a): the per-unit record
# of what each MODEL unit committed to this run's candidate (ControlState.subtask_written_files - the evidence the
# planned-read view (subtask_executor.reads_candidate_view), owner resolution (owner_recovery) and the abandoned-plan
# quarantine (resume_support) read).

def committed_files(result: Mapping[str, Any]) -> List[str]:
    """The files one unit invocation COMMITTED to the run's candidate, by the candidate transaction's own result
    authority - never a label, role or recovery action: its terminal commit ran (``quality_gates_passed`` is True only
    after the sub-run's commit committed - stage_apply sets it after the commit service's terminal commit, and any commit that did
    not commit stops the sub-run), or its candidate was applied before its final review was refused
    (``final_review_refusal.candidate_applied``). A failed, refused or rolled-back invocation committed nothing,
    whatever its ``files`` report."""
    refusal = result.get("final_review_refusal") or {}
    if result.get("quality_gates_passed") is True or refusal.get("candidate_applied") is True:
        return list(result.get("files") or [])
    return []


def record_committed_effects(control_state: Any, unit_id: str, result: Mapping[str, Any]) -> Any:
    """The ONE update of ControlState.subtask_written_files inside a run. Invariant: a unit's entry is the union of the
    files that every invocation of that unit in this run (its first run, an owner recovery, a verification reopen, a
    consumer retry, a scope recovery) committed to the candidate - a later invocation never replaces it (a VERIFY-only
    recovery carried forward with files [] used to erase the owner's first-run change: TEST-B, P2), an uncommitted one
    never grows it, and the entry exists once the unit ran (empty = it changed nothing; no entry = no evidence). Only
    a candidate boundary starts the record over: a new run's ControlState, or the reset when the candidate is
    discarded."""
    prior = control_state.subtask_written_files.get(unit_id, ())
    return control_state.with_updates(subtask_written_files={
        **control_state.subtask_written_files,
        unit_id: sorted(set(prior) | set(committed_files(result))),
    })


# ----------------------------------------------------------------------------- E14 (OI-62): moved verbatim from
# kriya/workflow/workflow_controller.py - the enforce run's per-subtask verification verdict and its commit's
# ContractRegistry transition builder (EnforceDeps binds the controller's forwarders, which reach these through the
# controller's call-time accessor _enforce_loop(); a test patch of the controller name keeps intercepting).

def _evaluate_subtask_verification(
    subtask: Subtask, call_result: Dict[str, Any],
) -> Tuple[SubtaskStatus, Optional[str], Tuple[str, ...]]:
    if not subtask.verification:
        return SubtaskStatus.COMPLETED, None, ()
    evidence = call_result.get("verification_results")
    if not isinstance(evidence, list) or len(evidence) != len(subtask.verification):
        return (
            SubtaskStatus.NEEDS_REVIEW,
            "declared verification requirements have no complete authoritative evidence",
            ("VERIFICATION_EVIDENCE_MISSING",),
        )
    unresolved: List[str] = []
    failed: List[str] = []
    for method, item in zip(subtask.verification, evidence, strict=True):
        if not isinstance(item, dict) or item.get("type") != method.type.value:
            unresolved.append(method.description)
            continue
        if method.type == VerificationMethodType.TOOL and item.get("tool_name") != method.tool_name:
            unresolved.append(method.description)
            continue
        if item.get("description", "") != method.description:
            unresolved.append(method.description)
            continue
        if item.get("passed") is False:
            failed.append(method.description)
        elif item.get("passed") is not True:
            unresolved.append(method.description)
    if failed:
        return SubtaskStatus.FAILED, f"verification failed: {failed!r}", ("VERIFICATION_FAILED",)
    if unresolved:
        return (
            SubtaskStatus.NEEDS_REVIEW,
            f"verification unresolved: {unresolved!r}",
            ("VERIFICATION_UNRESOLVED",),
        )
    return SubtaskStatus.COMPLETED, None, ()


def _enforce_contract_transition(
    terminal_writes: List[Any], *, workspace_path: str, goal: str, plan: Any, transaction_id: str,
    subtask_call_results: List[Dict[str, Any]],
) -> Callable[..., Any]:
    """PRD-029: the enforce commit's ContractRegistry transition builder.
    Baseline = the live workspace (the candidate lives in the plan
    workspace until this commit); authorizations = DIRECT ones from the
    user's goal for this plan; downstream verification = the final
    subtask's own terminal full suite, which ran on the final candidate with
    tests executed."""
    from kriya.workflow.contract_authority import derive_direct_contract_authorizations
    from kriya.workflow.contract_lifecycle import derive_contract_transition

    original, final = {}, {}
    for write in terminal_writes:
        if write.delete:
            continue
        relpath = (
            os.path.relpath(write.target_path, workspace_path) if os.path.isabs(write.target_path)
            else write.target_path
        )
        final[relpath] = write.content
        path = os.path.join(workspace_path, relpath)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                original[relpath] = handle.read()
    last = subtask_call_results[-1] if subtask_call_results else {}
    downstream_verified = any(
        item.get("type") == "regression_test" and item.get("status") == "PASS_WITH_TESTS"
        for item in (last.get("deterministic_gate_evidence") or [])
    )
    return functools.partial(
        derive_contract_transition, workspace_path=workspace_path, registry=None,
        original_contents=original, final_contents=final,
        authorizations=tuple(derive_direct_contract_authorizations(goal, plan)),
        transaction_id=transaction_id, downstream_verified=downstream_verified,
    )
