"""Kernel patch points (full modularization E1): the closed table of every test patch on the three kernel modules.

``tests/`` patches names ON ``kriya.workflow.attempt``, ``kriya.workflow.workflow`` and
``kriya.workflow.workflow_controller`` 424 times (MEASURED at 4eaf610 by ``patch_sites.patch_sites``: AST over every
test file, string/object/decorator/monkeypatch/assignment forms; the plan's CURRENT-DEPENDENCY-MAP §8 grep counted
56 + 33 + 195 + 21). W4 moves code out of these modules; a patch keeps working only while (1) the name stays on the
module, (2) it is the same object kind, and (3) the code that should be intercepted still looks the name up THROUGH
that module at call time. A patch on a re-export does not redirect the owner module's own calls: after a move, the
patched facade name exists but the moved caller resolves the owner's binding, and the patch silently intercepts
nothing. This table pins, per patch target: the number of patch sites in tests, the module that DEFINES the object
today (``owner``), and where kriya/ looks it up through the patched module (``lookups``: module -> number of lookup
sites; ``None`` for a class/module attribute target, which is global wherever the class is used) and which modules
bound it at import (``bypass``: those calls are never intercepted). An E-slice that changes any of these updates the
row in the same commit, after retargeting the listed patches or keeping the lookup on the facade (StageDeps /
``TerminalGateValidators`` pattern). The dynamic proof that a patched name is what the run calls is
``tests/test_kernel_seams.py``.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Callable, Dict, FrozenSet, List, Mapping, NamedTuple, Optional, Sequence, Tuple

import pytest

from . import module_map as mm
from . import patch_sites as ps

A, W, C = ps.KERNEL_MODULES


class P(NamedTuple):
    sites: int
    owner: str                                        # module that defines the patched object today
    lookups: Optional[Tuple[Tuple[str, int], ...]]    # where kriya/ resolves it through the patched module
    bypass: Tuple[str, ...]                           # modules bound to the original at import (never intercepted)


PATCH_POINTS: Dict[Tuple[str, str], P] = {
    # kriya.workflow.attempt
    # E8 characterization: +1 site (tests/test_gate_orchestrator.py records what the candidate gates hand to the
    # runtime-verification seam - E9's code - through this name).
    # E8: 1 -> 1: the executing gates' call moved to gate_orchestrator and reads GateDeps (one load in attempt._gate_deps).
    (A, '_approve_and_execute_runtime_verification'): P(1, A, ((A, 1),), ()),
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the dynamic proof that both runtime call sites
    # resolve it through this module at call time).
    # E9: 2 -> 1 lookup: the two callers (approve-and-execute, direct) moved to runtime_verification and read
    # RuntimeDeps._approve_runtime_commands (one load in attempt._runtime_deps); the site above is the dynamic proof.
    (A, '_approve_runtime_commands'): P(2, A, ((A, 1),), ()),
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): 2 sites (the suite judgment's attribution, looked
    # up by _judge_candidate_suite; one load).
    (A, '_attribute_candidate_suite'): P(2, A, ((A, 1),), ()),
    # E7: unchanged - still the retry_prompts function attempt.py imports, looked up by run_attempt's two targeted
    # branches (which stay in attempt.py); retry_prompts.py stays its only definition.
    (A, '_build_targeted_retry_prompt'): P(2, 'kriya.workflow.retry_prompts', ((A, 2),), ()),
    # E8 (guard): 2 -> 2: the toolchain preflight's load moved to gate_orchestrator and reads GateDeps (one load in
    # attempt._gate_deps); the verification-only/runtime path keeps its own.
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the direct path's preflight through this name).
    (A, '_check_java_toolchain_mismatch'): P(3, 'kriya.workflow.toolchain', ((A, 2),), ()),
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): +1 site, the dynamic proof that a patch of this
    # name reaches both the CandidateDeps and the GenerationDeps field.
    (A, '_code_intelligence_for'): P(3, A, ((A, 2),), ()),
    # E6 characterization: +1 site (tests/test_fallback_decision.py records which request profiles a fallback decision
    # resolves, through this name: the selection, the call-time substitution and the model entry look it up here).
    # E6: 4 -> 3 lookups: the selection's and the substitution's loads moved to fallback_decision and read
    # FallbackDeps._developer_request_profile (one load in attempt._fallback_deps), the model entry's to generation_service
    # (one load in attempt._generation_deps); the retry preparation keeps its own. The patch site above is the dynamic proof.
    # E7: 3 -> 3 lookups: the retry preparation's load moved to repair_orchestrator and reads
    # RepairDeps._developer_request_profile (one load in attempt._repair_deps).
    (A, '_developer_request_profile'): P(1, A, ((A, 3),), ()),
    (A, '_ensure_generation_time_budget'): P(2, A, ((A, 1),), ()),
    # E8 characterization: +1 site (tests/test_gate_orchestrator.py: the managed-service early return of the gates).
    # E8: 2 -> 2: the executing gates' load moved (GateDeps, one load in attempt._gate_deps); the direct runtime path keeps its own.
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the direct path hands a managed service over by
    # this name).
    (A, '_execute_managed_service_verification'): P(2, A, ((A, 2),), ()),
    (A, '_enter_developer_model'): P(1, A, ((A, 1),), ()),
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): 1 site (the rejected-locator fallback request's
    # routing; loads: that helper and run_attempt).
    (A, '_fallback_routing'): P(1, A, ((A, 2),), ()),
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): 1 site (the D1 patch-only set's authority, looked
    # up through CandidateDeps; one load in attempt._candidate_deps).
    (A, '_has_authoritative_full_source'): P(1, A, ((A, 1),), ()),
    # E8 characterization: +1 site (tests/test_gate_orchestrator.py records the scope every suite judgment is asked for).
    # E8: 4 -> 2 lookups: the three loads of the moved gates (the selection fallback's and the full suite's judgment,
    # the verification-only judge_suite lambda) read GateDeps._judge_candidate_suite (one load in attempt._gate_deps);
    # the test-delta covering run keeps its own. The patch site above is the dynamic proof (all three paths).
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): +1 site, the dynamic proof that a patch of this
    # name reaches the GateDeps field.
    # E14 pin2 (written before the moves, for the pin1 survivors u07/u09): +2 sites, the test-delta covering run's
    # judgment scope and its attribution evidence through this name.
    # E14: 2 -> 1 lookup: the test-delta covering run (_raise_unexecuted_test_delta) moved to gate_orchestrator and reads
    # GateDeps._judge_candidate_suite (the one load in attempt._gate_deps).
    (A, '_judge_candidate_suite'): P(4, A, ((A, 1),), ()),
    (A, '_maybe_run_developer_investigation'): P(1, A, ((A, 1),), ()),
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the dynamic proof that both runtime call sites
    # resolve it through this module at call time).
    # E9: 3 -> 1 lookup: the three calls (approve-and-execute x2, direct) moved to runtime_verification and read
    # RuntimeDeps (one load in attempt._runtime_deps).
    (A, '_prepare_finite_command_runtime_artifacts'): P(2, A, ((A, 1),), ()),
    # E10 post-move pin: +1 site (tests/test_candidate_transaction.py: run_attempt's evidence boundary resolves the
    # obligations-snapshot recorder through this module when the attempt closes; one load in attempt._evidence_deps).
    (A, '_record_obligations_snapshot'): P(1, A, ((A, 1),), ()),
    # E10: 2 -> 1 lookup: the staging loop's two pre-flight calls moved to candidate_transaction and read
    # CandidateDeps._reject_exception_masking (one load in attempt._candidate_deps).
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): +1 site, the dynamic proof that a patch of this
    # name reaches the CandidateDeps field.
    (A, '_reject_exception_masking'): P(2, A, ((A, 1),), ()),
    (A, '_require_qualified_retry_identity'): P(1, A, ((A, 1),), ()),
    # E8 (guard): 2 -> 2: the toolchain preflight's load moved (GateDeps, one load in attempt._gate_deps); the other stays.
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the direct path's preflight through this name).
    (A, '_resolve_java_home_override'): P(5, 'kriya.workflow.toolchain', ((A, 2),), ()),
    # E7: the body moved to repair_orchestrator; attempt.py keeps a forwarder (owner A). 3 -> 3 lookups: the retry
    # preparation's load reads RepairDeps (one load in attempt._repair_deps), generation_service's and
    # run_attempt's are unchanged.
    (A, '_resolve_known_target_member_hints'): P(1, A, ((A, 3),), ()),
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the dynamic proof that both runtime call sites
    # resolve it through this module at call time).
    # E9: 3 -> 1 lookup: the subject's and both executors' resolution moved to runtime_verification and read
    # RuntimeDeps._resolve_run_command (one load in attempt._runtime_deps).
    (A, '_resolve_run_command'): P(11, 'kriya.workflow.file_resolution', ((A, 1),), ()),
    (A, '_restore_api_contract_owners_deterministically'): P(1, A, ((A, 1),), ()),
    # E7: the body moved to repair_orchestrator; attempt.py keeps an async forwarder (owner A) that run_attempt's
    # coordinated branch still calls by this name (the 5 patch sites intercept the whole coordinated repair).
    (A, '_run_coordinated_repair_generation'): P(5, A, ((A, 1),), ()),
    # E7: 5 -> 5 lookups: the coordinated repair's per-participant call moved to repair_orchestrator and reads
    # RepairDeps._run_developer_generation (one load in attempt._repair_deps).
    (A, '_run_developer_generation'): P(11, A, ((A, 5),), ()),
    # E8: the body moved to gate_orchestrator; attempt.py keeps an async forwarder (owner A) that run_attempt calls by this name.
    (A, '_run_verification_only_attempt'): P(1, A, ((A, 1),), ()),
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): 5 sites (the process-boundary obligation's
    # RepairContract sync; one load in _record_process_boundary_obligation).
    (A, '_sync_active_repair_contract'): P(5, A, ((A, 1),), ()),
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the dynamic proof that both runtime call sites
    # resolve it through this module at call time).
    (A, '_validate_and_convert_managed_service_contract'): P(2, A, ((A, 1),), ()),
    # E7: 3 -> 3 lookups: the coordinated candidate view's materialization moved to repair_orchestrator and reads
    # RepairDeps.apply_anchored_edits (one load in attempt._repair_deps).
    # E10: 3 -> 2 lookups: the staging loop's and the structural-insertion check's loads moved to candidate_transaction
    # and read CandidateDeps.apply_anchored_edits (one load in attempt._candidate_deps); RepairDeps keeps its load.
    (A, 'apply_anchored_edits'): P(1, 'kriya.workflow.edit_safety', ((A, 2),), ()),
    (A, 'defer_execution_until_source_review'): P(1, A, ((A, 1),), ()),
    (A, 'exact_window_reserve'): P(1, 'kriya.workflow.edit_capability', ((A, 2),), ()),
    # E8 (guard): 1 -> 1: the executing gates' load moved (GateDeps, one load in attempt._gate_deps).
    (A, 'extract_target_test'): P(4, 'kriya.workflow.file_resolution', ((A, 1),), ()),
    # E8 (guard): 2 -> 2: the executing gates' load moved (GateDeps, one load in attempt._gate_deps); the other stays.
    (A, 'find_runnable_test_files'): P(3, 'kriya.workflow.file_resolution', ((A, 2),), ()),
    (A, 'normalize_written_filepath'): P(1, 'kriya.workflow.file_resolution', ((A, 1),), ()),
    (A, 'run_attempt'): P(10, A, (('kriya.workflow.best_of_n', 1),), (W,)),
    (A, 'run_managed_service_verification'): P(17, 'kriya.tools.service_runtime', ((A, 1),), ()),
    # E6: 9 -> 8 lookups: the generation's two time.monotonic() loads moved to generation_service and read
    # GenerationDeps.time, bound to this module's time by attempt._generation_deps at call time (one new load), so a
    # patch of kriya.workflow.attempt.time still reaches the generation's duration measurement.
    # E8: 8 -> 7 lookups: the compile timing's two time.monotonic() loads moved to gate_orchestrator and read
    # GateDeps.time (one load in attempt._gate_deps), so a patch of kriya.workflow.attempt.time still reaches it.
    # E9 characterization: +1 site (tests/test_runtime_verification.py: the runtime timings read this module's clock).
    # E9: 7 -> 4 lookups: the four time.monotonic() loads of the direct and managed-service timings moved to
    # runtime_verification and read RuntimeDeps.time (one load in attempt._runtime_deps).
    (A, 'time'): P(2, 'time', ((A, 4),), ()),
    # kriya.workflow.workflow
    (W, 'RepositoryAnalyzer'): P(3, 'kriya.analyzer.analyzer', ((W, 2),), ()),
    (W, 'RepositoryAnalyzer.index_repository'): P(3, 'kriya.analyzer.analyzer', None, ()),
    (W, 'WorkflowEngine.run_generation_workflow'): P(19, W, None, ()),
    (W, '_direct_terminal_writes'): P(1, W, ((W, 4),), ()),
    (W, '_ensure_repository_indexed'): P(2, W, ((W, 1),), ()),
    (W, '_gate_outcome_proven'): P(1, W, ((W, 2),), ()),
    (W, '_migration_identities'): P(1, W, ((W, 1),), ()),
    # E4: 2 -> 1 lookup: both loads (_oracle_base_revision, mutation_scope_evidence) moved to requirement_closers and
    # bind CloserDeps.run_committed_paths, built by _closer_deps from this name at call time (the two patch sites are
    # the dynamic proof: they patch it on this module and drive the moved closers).
    (W, '_run_committed_paths'): P(3, W, ((W, 1),), ()),
    (W, '_run_contract_acceptance'): P(1, W, ((W, 1),), ()),
    (W, '_run_static_analysis_gate'): P(2, W, ((W, 1),), ()),
    # E5: 3 -> 1 lookup: the three loads moved with the Developer loop to development_loop, which binds
    # StageDeps.assert_candidate_unchanged_since_source_review (one load in WorkflowEngine._development_deps, resolved
    # here when stage 4 begins; the two patch sites drive the moved loop and are the dynamic proof).
    (W, 'assert_candidate_unchanged_since_source_review'): P(2, A, ((W, 1),), ()),
    # E5 characterization: +1 site (tests/test_development_loop.py: a rejected baseline reuse is recorded).
    (W, 'capture_brownfield_baselines'): P(2, 'kriya.workflow.validation_baseline', ((W, 1),), ()),
    (W, 'classify_ownership_violations'): P(1, 'kriya.workflow.architectural_choice', ((W, 1),), ()),
    (W, 'close_requirements_by_mutation_scope'): P(3, W, (('kriya.workflow.terminal_gate_service', 1), (W, 1)), ()),
    (W, 'close_requirements_with_derived_examples'): P(1, W, (('kriya.workflow.terminal_gate_service', 1), (W, 1)), ()),
    (W, 'close_requirements_with_named_tests'): P(1, W, (('kriya.workflow.terminal_gate_service', 1), (W, 1)), ()),
    (W, 'commit_terminal_candidate'): P(2, 'kriya.workflow.terminal_commit', ((W, 1),), ()),
    # E5: 5 -> 3 lookups: the loop's three loads (auto baseline revision, the baseline capture's revision, the
    # targeted POST replay) moved to development_loop and bind StageDeps.compute_workspace_content_hash (+1 load in
    # _development_deps); the checkpoint opening and the run-contract baseline keep theirs.
    (W, 'compute_workspace_content_hash'): P(3, 'kriya.workflow.checkpoint', ((W, 3),), ()),
    # E5 characterization: +1 site (tests/test_development_loop.py: the prelude's sandbox failure).
    (W, 'create_git_worktree'): P(3, 'kriya.workflow.worktree', ((W, 1),), ()),
    (W, 'delete_checkpoint'): P(2, 'kriya.control.checkpoint', ((W, 2),), ()),
    (W, 'derive_contract_transition'): P(4, 'kriya.workflow.contract_lifecycle', ((W, 1),), ()),
    # E5 characterization: +1 site (tests/test_development_loop.py: a Best-of-N recorded failure is never handled again).
    (W, 'handle_attempt_failure'): P(3, 'kriya.workflow.retry_strategy', ((W, 1),), ()),
    # E3: +1 site (tests/test_work_unit_stages.py proves the moved architecture stage calls the facade name).
    (W, 'include_response_construction_owners'): P(2, 'kriya.workflow.file_resolution', ((W, 1),), ()),
    # E2: +1 site (test_outcome proves the moved run-lifecycle code logs through it); 151 -> 145 lookups: 8 moved
    # logger calls now reach run_lifecycle/outcome as StageDeps.logger / the forwarder's logger= argument, both
    # resolved here at call time (+2: WorkflowEngine._stage_deps, _record_run_exception).
    # E3: 145 -> 90 lookups: the 55 logger loads of stages 0-3 moved to the stage modules, which bind
    # StageDeps.logger (WorkflowEngine._work_unit_deps -> _stage_deps, resolved here at call time; proven by the
    # same test_outcome logger test, extended to the KnowledgeGuard stage).
    # E4: 90 -> 65 lookups: the 5 logger loads of the moved requirement closers bind CloserDeps.logger (one new load
    # in the module-level _closer_deps) and the 21 of the moved stages 5-7 bind StageDeps.logger (_late_stage_deps ->
    # _stage_deps, no new load); both resolved here at call time. +2 sites: the dynamic proofs in
    # tests/test_work_unit_stages.py (the moved lesson extraction's swallowed failure, and the final review stage and a
    # closer forwarder, log through the patched facade logger).
    # E4 (OI-32): 65 -> 62: the three run-opening early exits' trace-write warnings now log through
    # run_lifecycle.persist_early_exit_trace (StageDeps.logger from _stage_deps, resolved here at call time).
    # E5 characterization: +1 site (tests/test_development_loop.py: stage 4 logs through the patched facade logger).
    # E5: 62 -> 36 lookups: the 26 logger loads of the moved Developer loop bind StageDeps.logger
    # (_development_deps -> _stage_deps, no new load).
    (W, 'logger'): P(6, W, ((W, 36),), ()),
    (W, 'resolve_future_owner_verification_deferral'): P(1, 'kriya.workflow.attribution', ((W, 1),), ()),
    # E3 characterization: +1 site (the legacy_stage_context_success golden scripts the retrieval through it).
    (W, 'retrieve_graph_context'): P(2, 'kriya.workflow.graph_retrieval', ((W, 1),), ()),
    # E5 characterization: +8 sites (tests/test_development_loop.py drives stage 4 through the facade's run_attempt: the
    # per-iteration resets, Best-of-N's handed-over stop, the prelude's run-wide decisions, the terminal checks, resume,
    # the rejected baseline reuse, the terminal regression's environment stop).
    (W, 'run_attempt'): P(11, A, ((W, 1),), ()),
    (W, 'save_checkpoint'): P(6, 'kriya.control.checkpoint', ((W, 1),), ()),
    # E12 characterization: 3 new rows (tests/test_e12_enforce_characterization.py patches the enforce run's call-time
    # imports of the facade where they are defined: the controller's function-local imports read them here at call time).
    (W, 'compile_run_contract'): P(1, W, ((W, 1),(C, 1),), ()),
    (W, 'immutable_test_files'): P(1, W, ((C, 1),), ()),
    (W, 'run_contract_baseline'): P(3, W, ((W, 1),(C, 1),), ()),
    # kriya.workflow.workflow_controller
    (C, 'ArtifactRegistry.derive_from_workspace'): P(4, 'kriya.control.artifacts', None, ()),
    (C, 'StaticAnalysisService.evaluate_candidate'): P(1, 'kriya.static_analysis.service', None, ()),
    (C, 'WorkflowController._run_structured_enforce'): P(1, C, None, ()),
    (C, 'WorkflowController._write_enforce_trace'): P(2, C, None, ()),
    # E12: 2 -> 1 lookup: the enforce loop's loads moved to enforce_loop and read EnforceDeps (one load in
    # workflow_controller._enforce_deps); the patch sites above are the dynamic proof.
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): +1 site, the dynamic proof that a patch of this
    # name reaches the EnforceDeps field.
    (C, '_evaluate_subtask_verification'): P(2, C, ((C, 1),), ()),
    # E11 characterization: +2 sites (tests/test_e11_owner_modules.py: the planned-edit feasibility through this name).
    # E11: 2 -> 1 lookup: evaluate_plan_edit_feasibility's two calls moved to planning_request and read
    # PlanningDeps.assess_planned_edit (one load in workflow_controller._planning_deps); the sites above are the dynamic proof.
    (C, 'assess_planned_edit'): P(3, 'kriya.workflow.edit_feasibility', ((C, 1),), ()),
    (C, 'blocking_requirements'): P(1, 'kriya.workflow.requirements', ((C, 1),), ()),
    (C, 'build_engineering_plan_from_planner_output'): P(70, 'kriya.workflow.plan_schema', ((C, 2),), ()),
    (C, 'build_planning_structural_evidence'): P(3, C, (('kriya.workflow.ownership_findings', 1), (C, 1)), ()),
    (C, 'canonicalize_planned_file_actions'): P(1, 'kriya.workflow.plan_validation', ((C, 2),), ()),
    # E11 characterization: +1 site (tests/test_e11_owner_modules.py: one unit grounding per unit, through this name).
    (C, 'collect_unit_grounding'): P(9, 'kriya.workflow.edit_feasibility', ((C, 1),), ()),
    (C, 'create_git_worktree'): P(30, 'kriya.workflow.worktree', ((C, 1),), ()),
    (C, 'enforce_preserved_reference_terminal_integrity'): P(3, 'kriya.workflow.terminal_gate_service', ((C, 1),), ()),
    (C, 'evaluate_plan_edit_feasibility'): P(5, C, ((C, 1),), ()),
    (C, 'find_migration_incomplete'): P(2, 'kriya.workflow.migration', ((C, 1),), ()),
    (C, 'find_missing_grounded_production_artifacts'): P(3, C, ((C, 1),), ()),
    (C, 'grounded_owner_candidates'): P(2, 'kriya.workflow.ownership_findings', ((C, 1),), ()),
    (C, 'localization_candidates'): P(2, 'kriya.workflow.graph_retrieval', ((C, 1),), ()),
    # E11 characterization: +2 sites (tests/test_e11_owner_modules.py).
    (C, 'model_capacities'): P(13, 'kriya.workflow.edit_feasibility', ((C, 1),), ()),
    (C, 'parse_planner_structured_output'): P(75, 'kriya.agents.contracts', ((C, 2),), ()),
    (C, 'record_plan_created'): P(1, 'kriya.workflow.subtask_telemetry', ((C, 2),), ()),
    (C, 'remove_git_worktree'): P(10, 'kriya.workflow.worktree', ((C, 1),), ()),
    (C, 'resolve_migration_resolution'): P(4, 'kriya.workflow.migration', ((C, 1),), ()),
    # E11 characterization: +2 sites (tests/test_e11_owner_modules.py: both legacy-ownership migrations stamp the
    # artifact registry through this name).
    # E11: 3 -> 2 lookups: the two legacy-ownership migrations moved to resume_support and read
    # ResumeDeps.save_artifact_registry (one load in workflow_controller._resume_deps); the enforce loop keeps its own.
    (C, 'save_artifact_registry'): P(4, 'kriya.control.persistence', ((C, 2),), ()),
    # E11 characterization: +1 site (tests/test_e11_owner_modules.py: the controller migration stamps the control
    # state through this name).
    (C, 'save_control_state'): P(7, 'kriya.control.persistence', ((C, 6),), ()),
    (C, 'subtask_executor.execute'): P(9, 'kriya.workflow.subtask_executor', None, ()),
    # E12: 5 -> 2 lookups: the enforce loop's loads moved to enforce_loop and read EnforceDeps (one load in
    # workflow_controller._enforce_deps); the shadow run's load is _shadow_deps' (E12 1/2); the patch sites above
    # are the dynamic proof.
    (C, 'validate_plan'): P(55, 'kriya.workflow.plan_validation', ((C, 2),), ()),
    (C, 'validate_stack_contract_artifacts'): P(1, 'kriya.workflow.static_checks', ((C, 1),), ()),
    # E12 characterization: 37 new rows (tests/test_e12_enforce_characterization.py pins the enforce loop's and the shadow
    # run's decisions through these controller names; E12 moves the bodies and reads each through EnforceDeps/ShadowDeps).
    (C, 'EstablishedProvenance'): P(1, C, ((C, 3),), ()),
    (C, 'TerminalGateRequest'): P(2, 'kriya.workflow.terminal_gate_service', ((C, 1),), ()),
    (C, 'TerminalGateService'): P(1, 'kriya.workflow.terminal_gate_service', ((C, 1),), ()),
    # E12: 3 -> 1 lookup: the enforce loop's loads moved to enforce_loop and read EnforceDeps (one load in
    # workflow_controller._enforce_deps); the patch sites above are the dynamic proof.
    (C, '_evaluate_integration_obligations'): P(2, C, ((C, 1),), ()),
    (C, '_preserved_reference_must_preserve_lines'): P(1, C, ((C, 1),), ()),
    (C, '_preserved_reference_regressions'): P(1, C, ((C, 1),), ()),
    (C, '_semantic_contract_must_preserve_lines'): P(1, C, ((C, 1),), ()),
    (C, '_semantic_contract_regression_subtasks'): P(1, C, ((C, 1),), ()),
    # E12: 2 -> 1 lookup: the enforce loop's loads moved to enforce_loop and read EnforceDeps (one load in
    # workflow_controller._enforce_deps); the patch sites above are the dynamic proof.
    (C, 'annotate_run'): P(1, 'kriya.control.run_coordinator', ((C, 1),), ()),
    (C, 'bound_acceptance'): P(1, 'kriya.workflow.acceptance_oracle', ((C, 1),), ()),
    # E12: 3 -> 1 lookup: the enforce loop's loads moved to enforce_loop and read EnforceDeps (one load in
    # workflow_controller._enforce_deps); the patch sites above are the dynamic proof.
    (C, 'bound_approval'): P(1, 'kriya.workflow.acceptance_approval', ((C, 1),), ()),
    (C, 'build_recovery_execution_plan'): P(7, C, ((C, 1),), ()),
    (C, 'build_structured_plan_repair_prompt'): P(1, 'kriya.workflow.planner_repair', ((C, 1),), ()),
    (C, 'check_plan_targets'): P(1, 'kriya.workflow.plan_targets', ((C, 1),), ()),
    (C, 'classify_planner_outcome'): P(1, 'kriya.workflow.planner_repair', ((C, 2),), ()),
    (C, 'coalesce_same_file_owners'): P(1, 'kriya.workflow.plan_normalization', ((C, 1),), ()),
    (C, 'commit_verified_candidate'): P(2, 'kriya.workflow.commit_service', ((C, 1),), ()),
    (C, 'derive_verification_contracts'): P(1, 'kriya.workflow.plan_normalization', ((C, 1),), ()),
    (C, 'find_ownership_findings'): P(1, 'kriya.workflow.ownership_findings', ((C, 1),), ()),
    (C, 'findings_prompt_block'): P(1, 'kriya.workflow.ownership_findings', ((C, 1),), ()),
    (C, 'load_control_state_run_reference'): P(1, 'kriya.control.persistence', ((C, 1),), ()),
    (C, 'load_run_record'): P(1, 'kriya.control.persistence', ((C, 1),), ()),
    (C, 'owner_candidates_prompt_block'): P(1, 'kriya.workflow.ownership_findings', ((C, 1),), ()),
    (C, 'quarantine_abandoned_plan_files'): P(1, C, ((C, 1),), ()),
    (C, 'record_findings'): P(2, 'kriya.workflow.ownership_findings', ((C, 1),), ()),
    (C, 'record_planner_outcome'): P(2, 'kriya.workflow.planner_repair', ((C, 2),), ()),
    (C, 'redirect_immutable_test_edits'): P(1, 'kriya.workflow.plan_normalization', ((C, 1),), ()),
    (C, 'render_localization_candidates'): P(1, 'kriya.workflow.graph_retrieval', ((C, 1),), ()),
    (C, 'resolve_effective_scope_conflict_owners'): P(1, C, ((C, 1),), ()),
    (C, 'resolve_python_module_to_candidate_artifact_path'): P(4, C, ((C, 1),), ()),
    (C, 'resolve_runtime_plan_gap_owner'): P(4, C, ((C, 1),), ()),
    (C, 'revise_plan_for_grounded_scope_owner'): P(2, C, ((C, 1),), ()),
    (C, 'revise_plan_for_planned_prerequisite'): P(1, C, ((C, 1),), ()),
    (C, 'revise_plan_for_runtime_plan_gap'): P(3, C, ((C, 1),), ()),
    # E12: 11 -> 1 lookup: the enforce loop's loads moved to enforce_loop and read EnforceDeps (one load in
    # workflow_controller._enforce_deps); the patch sites above are the dynamic proof.
    (C, 'save_approved_plan'): P(7, 'kriya.control.persistence', ((C, 1),), ()),
    (C, 'scope_verification_to_requirements'): P(1, 'kriya.workflow.plan_normalization', ((C, 1),), ()),
    (C, 'settle_findings'): P(1, 'kriya.workflow.ownership_findings', ((C, 1),), ()),
}


def _sites_by_target(sites: Sequence[ps.PatchSite]) -> Dict[Tuple[str, str], List[str]]:
    found: Dict[Tuple[str, str], List[str]] = {}
    for site in sites:
        found.setdefault((site.module, site.target), []).append(f"{site.file}:{site.line}")
    return found


def _resolve(module: str, target: str) -> Tuple[Optional[object], Optional[object]]:
    """(first-segment object, leaf object) on the module, or (None, None) when any segment is missing."""
    head: object = importlib.import_module(module)
    first = None
    for index, part in enumerate(target.split(".")):
        if not hasattr(head, part):
            return None, None
        head = getattr(head, part)
        first = head if index == 0 else first
    return first, head


def owner_of(module: str, obj: object) -> str:
    if inspect.ismodule(obj):
        return obj.__name__
    if inspect.isclass(obj) or inspect.isfunction(obj):
        return obj.__module__
    return module    # a module-level object (logger, constant, instance): owned where it is bound


def patch_point_problems(table: Mapping[Tuple[str, str], P], sites: Sequence[ps.PatchSite],
                         resolve: Callable[[str, str], Tuple[Optional[object], Optional[object]]],
                         lookups: Callable[[str, str], ps.Lookups]) -> List[str]:
    """Every difference between the pinned table and the measured tree, each naming the patches to retarget."""
    problems: List[str] = []
    measured = _sites_by_target(sites)
    unresolved = [where for (module, _target), where in measured.items() if module == ps.UNRESOLVED for where in where]
    if unresolved:
        problems.append(f"patch targets naming a kernel module that the finder cannot resolve: {unresolved}")
    for key in sorted(set(measured) - set(table)):
        if key[0] != ps.UNRESOLVED:
            problems.append(f"NEW patch target {key[0]}.{key[1]} ({len(measured[key])} sites: {measured[key]}): "
                            "add its row (sites, owner, lookups, bypass)")
    for key in sorted(set(table) - set(measured)):
        problems.append(f"patch target {key[0]}.{key[1]} is no longer patched by any test: delete its row")
    for key in sorted(set(table) & set(measured)):
        module, target = key
        row, where = table[key], measured[key]
        if len(where) != row.sites:
            problems.append(f"{module}.{target}: {len(where)} patch sites, table says {row.sites}: {where}")
        first, leaf = resolve(module, target)
        if leaf is None:
            problems.append(f"{module}.{target} no longer exists on {module}: these {len(where)} patches now fail or "
                            f"patch nothing; keep the name on the facade or retarget them: {where}")
            continue
        owner = owner_of(module, first)
        if owner != row.owner:
            problems.append(f"{module}.{target.split('.')[0]} is now defined in {owner} (table: {row.owner}); a patch "
                            f"on the re-export does not redirect {owner}'s own calls. Retarget or keep the lookup on "
                            f"{module}, then update the row: {where}")
        if row.lookups is not None:
            found = lookups(module, target.split(".")[0])
            if found.interceptable != row.lookups:
                problems.append(f"{module}.{target}: kriya/ now looks it up through {module} at {found.interceptable} "
                                f"(table: {row.lookups}); a moved call site that resolves another binding is no longer "
                                f"intercepted by these patches - retarget them or keep the lookup on the facade: {where}")
            if found.bound_at_import != row.bypass:
                problems.append(f"{module}.{target}: modules bound to the original at import are {found.bound_at_import} "
                                f"(table: {row.bypass}); their calls are never intercepted by these patches: {where}")
    return problems


@pytest.fixture(scope="module")
def measured_sites():
    return ps.patch_sites()


@pytest.fixture(scope="module")
def index():
    return ps.LookupIndex(mm.trees())


def test_every_kernel_patch_target_is_pinned_and_still_reaches_what_it_patched(measured_sites, index):
    problems = patch_point_problems(PATCH_POINTS, measured_sites, _resolve, index.lookups)
    assert problems == [], "\n".join(problems)


def test_the_table_totals_are_the_measured_e1_baseline():
    totals = {module: sum(row.sites for (owner, _t), row in PATCH_POINTS.items() if owner == module) for module in ps.KERNEL_MODULES}
    # E1 baseline 87/69/268; E2 +1 W.logger site (test_outcome); E3 +1 W.retrieve_graph_context site (golden
    # scenario) and +1 W.include_response_construction_owners site (test_work_unit_stages); E4 +2 W.logger sites
    # (test_work_unit_stages: the moved late stages and closers log through the patched facade logger); E5 +1 W.logger and
    # +1 W.create_git_worktree, +1 W.handle_attempt_failure, +1 W.capture_brownfield_baselines and +10 W.run_attempt sites
    # (test_development_loop: stage 4's characterization and dynamic proofs, written before the move); E6 +1
    # A._developer_request_profile site (test_fallback_decision: the profiles a fallback decision resolves); E8 +3 A
    # sites, 3 new rows (test_gate_orchestrator: the runtime-verification and managed-service seams, the suite judgment);
    # E9 +8 A sites (test_runtime_verification: the dynamic proofs of the runtime seams, written before the move); E10 +1
    # A site, 1 new row (test_candidate_transaction: the evidence boundary's call-time recorder lookup, post-move pin); E11
    # +8 C sites (test_e11_owner_modules: the feasibility and legacy-migration names, written before the move); E12 +4 W
    # sites (3 new rows) and +102 C sites: 37 new rows (63 sites) and 39 more sites on 14 existing rows
    # (test_e12_enforce_characterization: the enforce loop's and the shadow run's decisions, written before the move); the
    # orchestrator's E12 pins (ca1c97c) add +1 W.run_contract_baseline and +4 C sites (validate_plan +2, create_git_worktree
    # +1, build_planning_structural_evidence +1)
    # E14 characterization (tests/test_e14_characterization.py, written before the moves): +12 A sites (+1 each on
    # _code_intelligence_for, _judge_candidate_suite and _reject_exception_masking; 4 new rows with 9 sites:
    # _attribute_candidate_suite 2, _fallback_routing 1, _has_authoritative_full_source 1, _sync_active_repair_contract 5)
    # and +1 C site (_evaluate_subtask_verification)
    # E14 pin2: +2 A sites (_judge_candidate_suite: the test-delta covering run's scope and attribution evidence)
    # TAF-3 e2e control (tests/test_taf3_enforce_terminal_refusal.py): +3 C sites (parse_planner_structured_output,
    # build_engineering_plan_from_planner_output, validate_plan: the real enforce route's planner parse, plan build and plan validation)
    # suite-preservation typed-refusal control (tests/test_suite_preservation_typed_refusal.py): +1 W site
    # (compute_workspace_content_hash: the ordinary failure injected at the real lazy-capture seam)
    # counter-evidence control (tests/test_counter_evidence_monotonic.py): +1 W site (_run_committed_paths: the deterministic violation
    # planted in the run's committed history of the measured end-to-end scenario)
    # RDCG-B-001 result-truth control (tests/test_rdcg_b001_result_truth.py): +1 W site (run_generation_workflow: the needs_review result the
    # generate command renders)
    assert totals == {A: 114, W: 96, C: 386}
    assert len(PATCH_POINTS) == 132


# ----------------------------------------------------------------------------- owner modules (DEPENDENCY-RULES)

# Modules W4 extracted from the three kernel facades. They reach the facades' patched names through an injected
# ``*Deps`` (``kriya/workflow/stage_context.py``) and never import a facade (a facade import would bind the
# originals and silently defeat the suite's patches) nor the CLI. E-slices add their modules here.
KERNEL_OWNER_MODULES = ("kriya.workflow.stage_context", "kriya.workflow.run_lifecycle", "kriya.workflow.outcome",
                        # E3: the design stages and their runner
                        "kriya.workflow.work_unit_runner", "kriya.workflow.stage_knowledge_guard",
                        "kriya.workflow.stage_analysis", "kriya.workflow.stage_planning",
                        "kriya.workflow.stage_architecture",
                        # E4: the review, approval/apply and lesson stages and the requirement closers
                        "kriya.workflow.stage_review", "kriya.workflow.stage_apply", "kriya.workflow.requirement_closers",
                        # E5: the Developer loop (stage 4) and one iteration's typed lifecycle
                        "kriya.workflow.development_loop", "kriya.workflow.attempt_lifecycle",
                        # E6: the Developer request assembly and generation, and the llm_chain fallback decisions
                        "kriya.workflow.generation_service", "kriya.workflow.fallback_decision",
                        # E7: the retry/repair preparation and the coordinated repair
                        "kriya.workflow.repair_orchestrator",
                        # E8: the candidate-gate orchestration and the verification-only attempt
                        "kriya.workflow.gate_orchestrator",
                        # E9: runtime and managed-service verification
                        "kriya.workflow.runtime_verification",
                        # E10: the kernel's own attempt-evidence emission helpers
                        "kriya.workflow.decision_evidence",
                        # E10: spec compliance, requirement verdicts and the contract-change classification
                        "kriya.workflow.spec_compliance",
                        # E10: the candidate staging transaction and its helpers
                        "kriya.workflow.candidate_transaction",
                        # E11: the plan revisions and plan-graph queries (from workflow_controller.py)
                        "kriya.workflow.plan_revision",
                        # E11: the owner recovery and integration obligations (from workflow_controller.py)
                        "kriya.workflow.owner_recovery",
                        # E11: the planning request and the plan checks (from workflow_controller.py)
                        "kriya.workflow.planning_request",
                        # E11: the resume support and legacy-ownership migrations (from workflow_controller.py)
                        "kriya.workflow.resume_support",
                        # E12: the shadow run and the enforce run's trace row (WorkflowController methods)
                        "kriya.workflow.shadow_mode",
                        # E12: the structured enforce run (WorkflowController._run_structured_enforce)
                        "kriya.workflow.enforce_loop",
                        # E14: the process-boundary test-candidate facts and the dependency-graph readers (OI-57)
                        "kriya.workflow.process_boundary")
FORBIDDEN_FOR_OWNERS = (*ps.KERNEL_MODULES, "kriya.cli")


def owner_import_problems(trees: Mapping[str, object]) -> List[str]:
    """Every import (any scope) of a facade or the CLI in an owner module's tree, as ``module:line -> target``."""
    import ast

    problems = []
    for module, tree in sorted(trees.items()):
        for node in ast.walk(tree):
            targets: List[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                targets = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            for target in targets:
                if any(target == banned or target.startswith(banned + ".") for banned in FORBIDDEN_FOR_OWNERS):
                    problems.append(f"{module}:{node.lineno} -> {target}")
    return sorted(set(problems))


def test_kernel_owner_modules_never_import_a_facade_or_the_cli():
    import ast

    trees = {}
    for module in KERNEL_OWNER_MODULES:
        path = mm.REPO / (module.replace(".", "/") + ".py")
        trees[module] = ast.parse(path.read_text(encoding="utf-8"))
    assert owner_import_problems(trees) == []


def owner_cycle_problems(sccs: Sequence[FrozenSet[str]], owners: Sequence[str]) -> List[str]:
    """Every owner module that is a member of an import strongly connected component (all imports, function-local
    included): an owner the facade imports and that reaches back into the facade's cycle silently joins it."""
    return sorted(f"{owner} is in a {len(scc)}-module import cycle" for owner in owners for scc in sccs if owner in scc)


def test_kernel_owner_modules_are_outside_every_import_cycle():
    """E6 (the OI-46 lesson): the owner modules W4 extracted are imported by their facades, members of the kernel's
    all-import cycle; an owner that imports anything reaching back into it (top level or function-local) joins it."""
    from . import test_cycles as cycles

    files = mm.python_files()
    _top, every = cycles.graphs(mm.scan(), {mm.module_name(path) for path in files})
    assert owner_cycle_problems(cycles.strongly_connected(every), KERNEL_OWNER_MODULES) == []


def test_the_owner_cycle_rule_catches_a_planted_cycle():
    from . import test_cycles as cycles

    graph = {"facade": {"owner", "other"}, "owner": {"leaf", "cycle_member"}, "cycle_member": {"facade"},
             "other": {"leaf"}, "leaf": set()}
    assert owner_cycle_problems(cycles.strongly_connected(graph), ("owner", "other")) == [
        "owner is in a 3-module import cycle"]


def test_the_owner_import_rule_catches_a_planted_facade_import():
    import ast

    planted = {"kriya.workflow.owner": ast.parse(
        "import kriya.workflow.workflow\n\ndef f():\n    from kriya.workflow.attempt import run_attempt\n"
        "    from kriya.workflow import workflow_controller\n    from kriya.cli import main\n")}
    assert owner_import_problems(planted) == [
        "kriya.workflow.owner:1 -> kriya.workflow.workflow", "kriya.workflow.owner:4 -> kriya.workflow.attempt",
        "kriya.workflow.owner:4 -> kriya.workflow.attempt.run_attempt",
        "kriya.workflow.owner:5 -> kriya.workflow.workflow_controller", "kriya.workflow.owner:6 -> kriya.cli",
        "kriya.workflow.owner:6 -> kriya.cli.main"]


# ----------------------------------------------------------------------------- planted self-checks

_PLANTED_TEST = """
import kriya.workflow.attempt as attempt_module
from unittest import mock
from unittest.mock import patch
from kriya.workflow import workflow
from kriya.workflow.workflow import WorkflowEngine
import kriya.workflow.workflow_controller

TARGET = "kriya.workflow.attempt"
NAME = "late"


@patch("kriya.workflow.attempt.decorated")
def test_a(monkeypatch, decorated):
    with patch("kriya.workflow.attempt.run_attempt"), mock.patch(f"{TARGET}.formatted"), patch(TARGET + ".added"):
        pass
    patch.object(attempt_module, "via_object")
    patch.object(WorkflowEngine, "run_generation_workflow")
    patch.object(kriya.workflow.workflow_controller, "dotted_module")
    patch.multiple("kriya.workflow.workflow", one=1, two=2)
    monkeypatch.setattr("kriya.workflow.workflow.string_form", 1)
    monkeypatch.setattr(workflow, "object_form", 1)
    monkeypatch.delattr(workflow, "removed")
    attempt_module.assigned = 1
    patch(f"kriya.workflow.attempt.{NAME}x")
    patch(f"kriya.workflow.attempt.{unknown}")
    patch("kriya.workflow.attempt_other.not_kernel")
    patch("kriya.tools.validate.PolymorphicValidator.run_tests")
    "kriya.workflow.attempt.prose_only"
"""

_PLANTED_KRIYA = {
    # owner defines f and calls it bare; facade re-exports at import; lazy imports at call time; attribute access
    "kriya/workflow/owner.py": "def f():\n    pass\n\n\ndef caller():\n    return f()\n",
    "kriya/workflow/facade.py": "from kriya.workflow.owner import f\n\n\ndef uses():\n    return f()\n",
    "kriya/workflow/lazy.py": "def go():\n    from kriya.workflow.facade import f\n    return f()\n",
    "kriya/workflow/attr.py": "from kriya.workflow import facade\n\n\ndef go():\n    return facade.f() + facade.f()\n",
    "kriya/workflow/bound.py": "from kriya.workflow.facade import f\n\n\ndef go():\n    return f()\n",
}


def test_the_finder_detects_every_patch_form_and_ignores_prose_and_other_modules():
    sites = ps.sites_in_source("tests/planted.py", _PLANTED_TEST)
    found = sorted((s.module.rsplit(".", 1)[-1] if s.module != ps.UNRESOLVED else "?", s.target, s.form) for s in sites)
    assert found == sorted([
        ("attempt", "decorated", "patch"), ("attempt", "run_attempt", "patch"), ("attempt", "formatted", "patch"),
        ("attempt", "added", "patch"), ("attempt", "via_object", "patch.object"),
        ("workflow", "WorkflowEngine.run_generation_workflow", "patch.object"),
        ("workflow_controller", "dotted_module", "patch.object"),
        ("workflow", "one", "patch.multiple"), ("workflow", "two", "patch.multiple"),
        ("workflow", "string_form", "setattr"), ("workflow", "object_form", "setattr"), ("workflow", "removed", "delattr"),
        ("attempt", "assigned", "assign"), ("attempt", "latex", "patch"),
        ("?", "kriya.workflow.attempt.<unresolved>", "patch"),
    ])


def test_the_lookup_index_separates_call_time_lookups_from_import_time_bindings():
    index = ps.LookupIndex(mm.parse_sources(_PLANTED_KRIYA))
    # a patch on facade.f reaches the lazy import and the two attribute loads, and facade's own bare call;
    # bound.py bound the original at import (never intercepted)
    assert index.lookups("kriya.workflow.facade", "f") == ps.Lookups(
        (("kriya.workflow.attr", 2), ("kriya.workflow.facade", 1), ("kriya.workflow.lazy", 1)), ("kriya.workflow.bound",))
    # a patch on owner.f reaches only owner's own bare call: facade bound it at import
    assert index.lookups("kriya.workflow.owner", "f") == ps.Lookups((("kriya.workflow.owner", 1),), ("kriya.workflow.facade",))


def test_a_moved_renamed_or_rebound_patch_target_fails_with_the_sites_to_retarget():
    """The three ways an E-slice breaks a patch, each detected on a planted tree and table."""
    sites = [ps.PatchSite("tests/t.py", 3, "kriya.workflow.facade", "f", "patch")]
    table = {("kriya.workflow.facade", "f"): P(1, "kriya.workflow.facade", (("kriya.workflow.facade", 1),), ())}

    def resolve_with(objects):
        return lambda module, target: (objects.get(target), objects.get(target))

    def native():
        pass

    def moved():   # a function object defined in the new owner
        pass
    native.__module__ = "kriya.workflow.facade"
    moved.__module__ = "kriya.workflow.owner"

    def same_index(_module, _name):
        return ps.Lookups((("kriya.workflow.facade", 1),), ())

    def moved_caller(_module, _name):
        return ps.Lookups((), ("kriya.workflow.stage",))

    assert patch_point_problems(table, sites, resolve_with({"f": native}), same_index) == []
    # 1. renamed / removed from the facade
    [missing] = patch_point_problems(table, sites, resolve_with({}), same_index)
    assert "no longer exists" in missing and "tests/t.py:3" in missing
    # 2. moved to a new owner, re-exported on the facade
    [rebound] = patch_point_problems(table, sites, resolve_with({"f": moved}), same_index)
    assert "now defined in kriya.workflow.owner" in rebound and "tests/t.py:3" in rebound
    # 3. kept on the facade but its caller moved out (the lookup through the facade is gone)
    lookup, bypass = patch_point_problems(table, sites, resolve_with({"f": native}), moved_caller)
    assert "now looks it up through kriya.workflow.facade at ()" in lookup and "tests/t.py:3" in lookup
    assert "never intercepted" in bypass
    # 4. a new patch target, a dropped one, a changed count
    grown = sites + [ps.PatchSite("tests/t.py", 9, "kriya.workflow.facade", "g", "patch")]
    assert any(p.startswith("NEW patch target kriya.workflow.facade.g") for p in
               patch_point_problems(table, grown, resolve_with({"f": native}), same_index))
    assert patch_point_problems(table, [], resolve_with({"f": native}), same_index) == [
        "patch target kriya.workflow.facade.f is no longer patched by any test: delete its row"]


# ----------------------------------------------------------------------------- E7: the retry/repair names

# Written at c41e0ff, BEFORE kriya/workflow/repair_orchestrator.py exists (E7 moves these bodies there): every name
# stays importable from kriya.workflow.attempt with the same parameters (names, kinds, defaults) and the same
# sync/async kind, so the 23 test files and the 3 production modules (generation_service, investigation's docs,
# edit_feasibility's function-local import of _goal_named_member_candidates) that reach them through attempt keep
# calling them exactly as before. Annotations are not pinned: an owner module spells the kernel's cycle types Any.
E7_ATTEMPT_CALLABLES = {
    "_goal_named_members_of": "(goal, owner)",
    "_resolve_known_target_member_hints": "(ctx, known_target_files)",
    "_goal_named_member_candidates": "(goal, path)",
    "_goal_named_member_hints": "(ctx, known_target_files)",
    "_merge_goal_named_member_hints": "(ctx, member_hints, paths)",
    "_validated_member_hints": "(ctx, candidates_by_path)",
    "_resolve_retry_member_hints": "(ctx, state, target_files)",
    "_record_retry_projection_context_items": "(state, retry_package)",
    "_preserve_member_exact_precision": "(state, path, new_item)",
    "_classify_retry_target_source_origin": "(state, ctx, path)",
    "_target_source_record": "(state, ctx, path)",
    "_compute_retry_evidence_fingerprint": "(state, target_files, mode, model_identity, member_hints)",
    "_effective_retry_temperature": "(ctx, model_identity)",
    "_prepare_retry_context": ("(state, ctx, *, target_files, prompt_window, model_identity, base_code_context='', "
                               "enable_no_progress_gate=True)"),
    "_retry_package_for_attempt": "(state, ctx, *, target_files, prompt_window, exclude=None)",
    "_materialize_candidate_content": "(ctx, filepath, file_obj)",
    "async _run_coordinated_repair_generation": ("(state, ctx, contract, base_code_context, stream_callback, "
                                                 "attempt_operation, optional_sections=())"),
}


def _unannotated(function) -> str:
    signature = inspect.signature(function)
    plain = signature.replace(parameters=[p.replace(annotation=inspect.Parameter.empty)
                                          for p in signature.parameters.values()],
                              return_annotation=inspect.Signature.empty)
    return ("async " if inspect.iscoroutinefunction(function) else "") + str(plain)


def test_the_retry_and_repair_names_keep_their_call_shape_on_attempt():
    attempt = importlib.import_module(A)
    found = {}
    for key in E7_ATTEMPT_CALLABLES:
        name = key.split(" ")[-1]
        shape = _unannotated(getattr(attempt, name))
        found[key] = shape.split(" ", 1)[1] if shape.startswith("async ") else shape
        assert shape.startswith("async ") == key.startswith("async "), name
    assert found == E7_ATTEMPT_CALLABLES
    assert attempt._GOAL_CALL_NAME.pattern == r"\b([a-z_][A-Za-z0-9_]*)\s*\("
    assert attempt._GOAL_MEMBER_HINTS_MAX == 4
    assert attempt._DETERMINISTIC_VERDICT_REASON_CODES == frozenset({"ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"})
    import dataclasses
    assert [f.name for f in dataclasses.fields(attempt.RetryContextPreparation)] == [
        "retry_package", "retry_error_context", "member_hints", "member_hint_rendered"]


# ----------------------------------------------------------------------------- E8: the candidate-gate names

# Written at 74f01ac, BEFORE kriya/workflow/gate_orchestrator.py exists (E8 moves these bodies there): every name stays
# importable from kriya.workflow.attempt with the same parameters and the same sync/async kind (run_attempt calls
# five of them by these names; tests/test_file_integrity_contract_001.py imports two, tests/test_lr_r1_p4_* wraps the
# verification-only attempt; the goal-spec statuses keep their values). Annotations are not pinned.
E8_ATTEMPT_CALLABLES = {
    "_apply_candidate_pom_corrections": "(state, ctx, compile_known_files)",
    "_bind_verification_tree": "(state, ctx)",
    "_record_candidate_change": "(state, ctx)",
    "_require_worktree_matches_candidate": "(state, ctx)",
    "async _run_verification_only_attempt": "(state, ctx)",
    "_run_pom_validation_gate": "(state, ctx, tree_binding)",
    "_new_candidate_validator": "(ctx)",
    "_resolve_candidate_toolchain": "(state, ctx, validator)",
    "apply_pre_review_candidate_corrections": "(state, ctx)",
    "async _run_executing_candidate_gates": "(state, ctx, skipping_candidate_gates, tree_binding, active_code_context)",
    "async _run_deferred_executing_gates": "(state, ctx, skipping_candidate_gates, tree_binding, active_code_context)",
}


def test_the_candidate_gate_names_keep_their_call_shape_on_attempt():
    attempt = importlib.import_module(A)
    found = {}
    for key in E8_ATTEMPT_CALLABLES:
        name = key.split(" ")[-1]
        shape = _unannotated(getattr(attempt, name))
        found[key] = shape.split(" ", 1)[1] if shape.startswith("async ") else shape
        assert shape.startswith("async ") == key.startswith("async "), name
    assert found == E8_ATTEMPT_CALLABLES
    assert (attempt.SPEC_MODEL_REPORTED_MISSING, attempt.SPEC_MODEL_INDETERMINATE,
            attempt.SPEC_COMPLIANCE_MODEL_ADVISORY) == ("model_reported_missing", "model_indeterminate",
                                                        "SPEC_COMPLIANCE_MODEL_ADVISORY")


# ----------------------------------------------------------------------------- E9: the runtime-verification names

# Written at 76f5e9a, BEFORE kriya/workflow/runtime_verification.py exists (E9 moves these bodies there): every name stays
# importable from kriya.workflow.attempt with the same parameters and the same sync/async kind (GateDeps hands the
# candidate gates five of them by these names; milestones.py imports _build_python_runtime_grounding; 14 test files call
# them through attempt), and the constants keep their values. Annotations are not pinned.
E9_ATTEMPT_CALLABLES = {
    "_classify_grounded_contract_verdict": "(output, worktree_path, files_written)",
    "async _resolve_runtime_verification_grade": "(ctx, state, contract_verdict, grade_kwargs, *, run_result=None)",
    "_deterministic_result_provenance_field": "(verification_authority)",
    "_prepare_finite_command_runtime_artifacts": "(resolved_run_commands, validator, ctx, state)",
    "_raise_runtime_verification_infrastructure_failure": "(state, run_result, commands)",
    "_raise_candidate_runtime_entrypoint_failure": "(state, run_result, commands, entrypoint)",
    "_apply_runtime_verification_contract": "(commands, input_channel)",
    "_run_verification_basis_hash": "(ctx, state)",
    "_collect_python_runtime_grounding_facts": "(root)",
    "_build_python_runtime_grounding": "(root)",
    "_runtime_verification_is_advisory_only": "(ctx, judgment)",
    "_derive_runtime_command_subject": "(ctx, judgment, intro)",
    "_runtime_commands_need_approval": "(ctx, state, judgment, autonomy_mode)",
    "async _approve_runtime_commands": "(ctx, state, judgment, intro)",
    "_required_runtime_verification_missing_message": "(judgment)",
    "_resolve_execution_mode": "(judgment)",
    "_looks_like_shell_compound_command": "(command)",
    "_reject_non_local_managed_service_host": "(field_name, host)",
    "_validate_and_convert_readiness": "(raw)",
    "_validate_and_convert_probe": "(raw)",
    "_validate_and_convert_managed_service_contract": "(managed_service, worktree_path, validator)",
    "_managed_service_blocked_without_approval_subject": "(state, ctx)",
    "async _execute_managed_service_verification": "(state, ctx, judgment, known_files, validator)",
    "_raise_if_required_runtime_verification_declined": "(state, ctx)",
    "async _execute_runtime_verification_directly": "(state, ctx, validator)",
    "async _approve_and_execute_runtime_verification": ("(state, ctx, validator, active_code_context, judgment, "
                                                        "autonomy_cfg_rv)"),
}


def test_the_runtime_verification_names_keep_their_call_shape_on_attempt():
    attempt = importlib.import_module(A)
    found = {}
    for key in E9_ATTEMPT_CALLABLES:
        name = key.split(" ")[-1]
        shape = _unannotated(getattr(attempt, name))
        found[key] = shape.split(" ", 1)[1] if shape.startswith("async ") else shape
        assert shape.startswith("async ") == key.startswith("async "), name
    assert found == E9_ATTEMPT_CALLABLES
    assert (attempt.MANAGED_SERVICE_APPROVAL_UNSUPPORTED, attempt._RUNTIME_VERIFICATION_SYNTHETIC_INPUT) == (
        "MANAGED_SERVICE_APPROVAL_UNSUPPORTED", "kriya-verification-input")
    assert attempt._SUPPORTED_RUNTIME_EXECUTION_MODES == frozenset({"finite_command", "managed_service"})
    assert (attempt._MANAGED_SERVICE_SHELL_MARKERS, attempt._MANAGED_SERVICE_FORBIDDEN_LEADING_EXECUTABLES,
            attempt._SHELL_INTERPRETER_EXECUTABLES) == (frozenset({"&&", "||", ";", "&"}), frozenset({"nohup", "sleep"}),
                                                        frozenset({"sh", "bash", "zsh", "dash", "ksh"}))
    assert (attempt._MANAGED_SERVICE_READINESS_KINDS, attempt._MANAGED_SERVICE_PROBE_KINDS) == (
        frozenset({"tcp", "http"}), frozenset({"http"}))
    assert attempt._DISTRUST_DISCLOSURE_NOTE.startswith("A deterministic check already inspected the captured output")
    assert attempt._PYTHON_RUNTIME_GROUNDING_EXCLUDED_DIRS == {
        ".git", ".kriya", "__pycache__", ".venv", "venv", "env", "node_modules", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "dist", "build", ".eggs", "site-packages"}


# ----------------------------------------------------------------------------- E10: candidate staging, spec, evidence

# Written at 6742f29, BEFORE kriya/workflow/candidate_transaction.py, spec_compliance.py and decision_evidence.py exist
# (E10 moves these bodies there): every name stays importable from kriya.workflow.attempt with the same parameters and
# the same sync/async kind (GateDeps, GenerationDeps and RepairDeps hand the gates, the Developer request and the
# coordinated repair a dozen of them by these names; terminal_gate_service imports _close_requirements_by_migration_gate
# at call time; 24 test files call them through attempt), the constants keep their values, and run_attempt stays the
# attempt module's coroutine function wrapped by the evidence boundary. Annotations are not pinned.
E10_ATTEMPT_CALLABLES = {
    "_capture_original": "(state, ctx, relpath)",
    "_completeness_gated_operation": "(filepath, base_operation, *, file_exists, ctx, state)",
    "_is_restore_public_contract_phase": "(state)",
    "_has_authoritative_full_source": "(filepath, ctx, state)",
    "_current_revision": "(filepath, ctx)",
    "_validate_actual_mutation_authority": "(filepath, actual_operation, *, file_exists, ctx, state)",
    "_operation_map": "(ctx, filepaths, attempt_operation, state=None)",
    "_edit_capability_loci": "(state, ctx, path, lines, *, revision=None, raw_digest=None)",
    "_remember_anchor_loci": "(state, path, edits, current)",
    "_only_response_validity_failures": "(state)",
    "_decide_edit_capabilities": "(state, ctx, kwargs)",
    "_structural_insertion_locus": "(state, ctx, path, content)",
    "_requested_operation": "(kwargs, path)",
    "_current_edit_capability": "(state, path)",
    "_record_edit_protocol_failure": "(state, path, family)",
    "_authorize_anchors": "(state, path, edits, current)",
    "_authorize_structural_insertion": "(state, capability, path, index, edit, current)",
    "_sent_exact_source_covers": "(state, capability, path, search, current)",
    "_spec_compliance_authoritative_context": "(ledger)",
    "_migration_obligations_all_satisfied": "(ledger)",
    "_spec_requirements_contradicting_authority": "(missing_requirements, ledger)",
    "_identifier_local_terms": "(text, identifier, radius=3)",
    "_extract_requirement_identifier_tokens": "(requirement)",
    "_spec_requirements_naming_planner_only_identifiers": "(missing_requirements, goal_text)",
    "_goal_spec_requirement_obligation_id": "(subtask_id)",
    "_stage_scoped_spec_compliance_goal": "(ctx)",
    "_goal_spec_evidence_fingerprint": "(goal, file_contents)",
    "_record_original_requirement_verdicts": "(state, ctx, spec_result, fingerprint)",
    "_downgrade_suppressed_requirement_claims": "(state, ctx, suppressed, fingerprint)",
    "_close_requirements_by_migration_gate": "(state, ctx, fingerprint)",
    "_structured_plan_paths": "(ctx)",
    "_stage_ownership_redirect_restoration": "(state, ctx, staged_writes)",
    "async _classify_and_escalate_contract_changes": ("(state, ctx, violations, baseline_contents, candidate_contents, "
                                                      "run_direct_authorizations, authorized_changes=())"),
    "_settled_goal_spec_requirement": "(ledger, obligation_id, fingerprint)",
    "_restore_api_contract_owners_deterministically": "(state, contract)",
    "_verified_no_change_proposal": "(state, ctx, files, missing_files)",
    "_attempt_evidence_boundary": "(func)",
    "_record_obligations_snapshot": "(ctx)",
    "_record_authority_snapshot": "(state, ctx, kwargs, capabilities, requested, model)",
}


def test_the_candidate_spec_and_evidence_names_keep_their_call_shape_on_attempt():
    attempt = importlib.import_module(A)
    found = {}
    for key in E10_ATTEMPT_CALLABLES:
        name = key.split(" ")[-1]
        shape = _unannotated(getattr(attempt, name))
        found[key] = shape.split(" ", 1)[1] if shape.startswith("async ") else shape
        assert shape.startswith("async ") == key.startswith("async "), name
    assert found == E10_ATTEMPT_CALLABLES
    assert attempt.MAX_SYMBOL_LOCI_PER_FILE == 2
    assert attempt._RESPONSE_VALIDITY_FAILURES == frozenset({
        "operation_contract", "anchored_edit", "structural_corruption", "no_op_edit", "diagnosis_mismatch"})
    assert attempt._REQUIREMENT_WORD_RE.pattern == r"[A-Za-z_][A-Za-z0-9_-]*"
    assert attempt._REQUIREMENT_PROVENANCE_STOPWORDS == {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "does", "for", "from", "has", "have", "in", "instead",
        "is", "it", "must", "named", "not", "of", "on", "or", "requires", "require", "required", "should", "that",
        "the", "this", "to", "uses", "using", "with"}
    run_attempt = attempt.run_attempt
    assert inspect.iscoroutinefunction(run_attempt) and run_attempt.__module__ == A
    assert run_attempt.__wrapped__.__name__ == "run_attempt" and run_attempt.__wrapped__.__module__ == A
