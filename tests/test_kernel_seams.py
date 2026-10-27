"""Kernel seams (full modularization E1): every name tests patch on the three kernel modules exists with its kind, and
a patch on it is what the run actually calls.

``KINDS`` pins the object kind of every patch target in ``tests/architecture/test_patch_points.py::PATCH_POINTS``
(same key set, asserted). ``CALL_THROUGH`` patches each name exactly as the suite does (``unittest.mock.patch`` on the
dotted module attribute, wrapping the real object so behaviour is unchanged), drives the smallest scripted kernel run
that reaches it (``tests/state_machine/_kernel_golden.py`` scenarios: real controller, engine and attempt; only the
model and, for enforce, the toolchain gates scripted) and asserts the patched object was called: a patch that the
code never resolves through that module is vacuous, and an E-slice that moves the lookup elsewhere fails here.
``NOT_REACHED`` names every target no scripted path reaches, with the reason; their lookup is still pinned
statically by ``test_patch_points`` (``lookups``).

MEASURED observation pinned here (E5 must decide, not silently change it): the Developer loop's ordinary call
``run_attempt(state, attempt_ctx)`` in workflow.py resolves workflow.py's OWN import-time binding; the 10 patches of
``kriya.workflow.attempt.run_attempt`` intercept only the Best-of-N path (``best_of_n.py`` imports it at call time).
SLICE-PLAN E5's guard text ("run_attempt still the loop's call target by name on kriya.workflow.attempt") is
therefore not today's behaviour: the loop's call target by name is ``kriya.workflow.workflow.run_attempt``.
"""
import importlib
import inspect
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest
from architecture.test_patch_points import PATCH_POINTS

# The scenario module and its harness (_scripted_run) live in tests/state_machine/, which pytest puts on sys.path
# for its own files only; imported by bare name exactly as the state-machine tier does (one module object).
sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

A, W, C = "kriya.workflow.attempt", "kriya.workflow.workflow", "kriya.workflow.workflow_controller"

KINDS: Dict[Tuple[str, str], str] = {
    # E14 characterization (tests/test_e14_characterization.py): the four collaborators its pins patch
    (A, '_attribute_candidate_suite'): 'function',
    (A, '_fallback_routing'): 'function',
    (A, '_has_authoritative_full_source'): 'function',
    (A, '_sync_active_repair_contract'): 'function',
    (A, '_approve_and_execute_runtime_verification'): 'async function',
    (A, '_approve_runtime_commands'): 'async function',
    (A, '_build_targeted_retry_prompt'): 'function',
    (A, '_check_java_toolchain_mismatch'): 'function',
    (A, '_code_intelligence_for'): 'function',
    (A, '_developer_request_profile'): 'function',
    (A, '_ensure_generation_time_budget'): 'function',
    (A, '_enter_developer_model'): 'function',
    (A, '_execute_managed_service_verification'): 'async function',
    (A, '_judge_candidate_suite'): 'function',
    (A, '_maybe_run_developer_investigation'): 'async function',
    (A, '_prepare_finite_command_runtime_artifacts'): 'function',
    (A, '_record_obligations_snapshot'): 'function',
    (A, '_reject_exception_masking'): 'function',
    (A, '_require_qualified_retry_identity'): 'function',
    (A, '_resolve_java_home_override'): 'function',
    (A, '_resolve_known_target_member_hints'): 'function',
    (A, '_resolve_run_command'): 'function',
    (A, '_restore_api_contract_owners_deterministically'): 'function',
    (A, '_run_coordinated_repair_generation'): 'async function',
    (A, '_run_developer_generation'): 'async function',
    (A, '_run_verification_only_attempt'): 'async function',
    (A, '_validate_and_convert_managed_service_contract'): 'function',
    (A, 'apply_anchored_edits'): 'function',
    (A, 'defer_execution_until_source_review'): 'function',
    (A, 'exact_window_reserve'): 'function',
    (A, 'extract_target_test'): 'function',
    (A, 'find_runnable_test_files'): 'function',
    (A, 'normalize_written_filepath'): 'function',
    (A, 'run_attempt'): 'async function',
    (A, 'run_managed_service_verification'): 'function',
    (A, 'time'): 'module',
    (W, 'final_review'): 'async function',  # P-B (3d453cf): patched by tests/test_skill_verification_deferral.py
    (W, 'RepositoryAnalyzer'): 'class',
    (W, 'RepositoryAnalyzer.index_repository'): 'async function',
    (W, 'WorkflowEngine.run_generation_workflow'): 'async function',
    (W, '_direct_terminal_writes'): 'function',
    (W, '_ensure_repository_indexed'): 'async function',
    (W, '_gate_outcome_proven'): 'function',
    (W, '_migration_identities'): 'function',
    (W, '_run_committed_paths'): 'function',
    (W, '_run_contract_acceptance'): 'function',
    (W, '_run_static_analysis_gate'): 'function',
    (W, 'assert_candidate_unchanged_since_source_review'): 'function',
    (W, 'capture_brownfield_baselines'): 'function',
    (W, 'classify_ownership_violations'): 'function',
    (W, 'close_requirements_by_mutation_scope'): 'function',
    (W, 'close_requirements_with_derived_examples'): 'function',
    (W, 'close_requirements_with_named_tests'): 'function',
    (W, 'commit_terminal_candidate'): 'function',
    (W, 'compute_workspace_content_hash'): 'function',
    (W, 'create_git_worktree'): 'function',
    (W, 'delete_checkpoint'): 'function',
    (W, 'derive_contract_transition'): 'function',
    (W, 'handle_attempt_failure'): 'async function',
    (W, 'include_response_construction_owners'): 'function',
    (W, 'logger'): 'object:Logger',
    (W, 'resolve_future_owner_verification_deferral'): 'function',
    (W, 'retrieve_graph_context'): 'async function',
    (W, 'run_attempt'): 'async function',
    (W, 'save_checkpoint'): 'function',
    (C, 'ArtifactRegistry.derive_from_workspace'): 'function',
    (C, 'StaticAnalysisService.evaluate_candidate'): 'function',
    (C, 'WorkflowController._run_structured_enforce'): 'async function',
    (C, 'WorkflowController._write_enforce_trace'): 'function',
    (C, '_evaluate_subtask_verification'): 'function',
    (C, 'assess_planned_edit'): 'function',
    (C, 'blocking_requirements'): 'function',
    (C, 'build_engineering_plan_from_planner_output'): 'function',
    (C, 'build_planning_structural_evidence'): 'function',
    (C, 'canonicalize_planned_file_actions'): 'function',
    (C, 'collect_unit_grounding'): 'async function',
    (C, 'create_git_worktree'): 'function',
    (C, 'enforce_preserved_reference_terminal_integrity'): 'function',
    (C, 'evaluate_plan_edit_feasibility'): 'async function',
    (C, 'find_migration_incomplete'): 'function',
    (C, 'find_missing_grounded_production_artifacts'): 'function',
    (C, 'grounded_owner_candidates'): 'function',
    (C, 'localization_candidates'): 'async function',
    (C, 'model_capacities'): 'function',
    (C, 'parse_planner_structured_output'): 'function',
    (C, 'record_plan_created'): 'function',
    (C, 'remove_git_worktree'): 'function',
    (C, 'resolve_migration_resolution'): 'function',
    (C, 'save_artifact_registry'): 'function',
    (C, 'save_control_state'): 'function',
    (C, 'subtask_executor.execute'): 'async function',
    (C, 'subtask_executor.revalidate_tool_subtask_authority'): 'function',
    (C, 'validate_plan'): 'async function',
    (C, 'validate_stack_contract_artifacts'): 'function',
    # E12 characterization: the names tests/test_e12_enforce_characterization.py patches
    (W, 'compile_run_contract'): 'function',
    (W, 'immutable_test_files'): 'function',
    (W, 'run_contract_baseline'): 'function',
    (C, 'EstablishedProvenance'): 'class',
    (C, 'TerminalGateRequest'): 'class',
    (C, 'TerminalGateService'): 'class',
    (C, '_evaluate_integration_obligations'): 'function',
    (C, '_preserved_reference_must_preserve_lines'): 'function',
    (C, '_preserved_reference_regressions'): 'function',
    (C, '_semantic_contract_must_preserve_lines'): 'function',
    (C, '_semantic_contract_regression_subtasks'): 'function',
    (C, 'annotate_run'): 'function',
    (C, 'bound_acceptance'): 'function',
    (C, 'bound_approval'): 'function',
    (C, 'build_recovery_execution_plan'): 'function',
    (C, 'build_structured_plan_repair_prompt'): 'function',
    (C, 'check_plan_targets'): 'function',
    (C, 'classify_planner_outcome'): 'function',
    (C, 'coalesce_same_file_owners'): 'function',
    (C, 'commit_verified_candidate'): 'function',
    (C, 'derive_verification_contracts'): 'function',
    (C, 'find_ownership_findings'): 'function',
    (C, 'findings_prompt_block'): 'function',
    (C, 'load_control_state_run_reference'): 'function',
    (C, 'load_run_record'): 'function',
    (C, 'owner_candidates_prompt_block'): 'function',
    (C, 'quarantine_abandoned_plan_files'): 'function',
    (C, 'record_findings'): 'function',
    (C, 'record_planner_outcome'): 'function',
    (C, 'redirect_immutable_test_edits'): 'function',
    (C, 'render_localization_candidates'): 'function',
    (C, 'resolve_effective_scope_conflict_owners'): 'function',
    (C, 'resolve_python_module_to_candidate_artifact_path'): 'function',
    (C, 'resolve_runtime_plan_gap_owner'): 'function',
    (C, 'revise_plan_for_grounded_scope_owner'): 'function',
    (C, 'revise_plan_for_planned_prerequisite'): 'function',
    (C, 'revise_plan_for_runtime_plan_gap'): 'function',
    (C, 'save_approved_plan'): 'function',
    (C, 'scope_verification_to_requirements'): 'function',
    (C, 'settle_findings'): 'function',
}

# scenario -> the module-attribute patch targets its run must call through the patched module
CALL_THROUGH: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "legacy_success": (
        (A, "_check_java_toolchain_mismatch"), (A, "_developer_request_profile"), (A, "_ensure_generation_time_budget"),
        (A, "_enter_developer_model"),
        # E14 characterization (MEASURED: these two are reached by every scripted attempt; the other two it patches are not)
        (A, "_fallback_routing"), (A, "_has_authoritative_full_source"),
        (A, "_maybe_run_developer_investigation"), (A, "_record_obligations_snapshot"), (A, "_reject_exception_masking"),
        (A, "_require_qualified_retry_identity"),
        (A, "_resolve_known_target_member_hints"), (A, "_run_developer_generation"), (A, "defer_execution_until_source_review"),
        (A, "extract_target_test"), (A, "find_runnable_test_files"), (A, "normalize_written_filepath"),
        (W, "RepositoryAnalyzer"), (W, "_direct_terminal_writes"), (W, "_gate_outcome_proven"), (W, "_migration_identities"), (W, "_run_committed_paths"),
        (W, "_run_static_analysis_gate"), (W, "assert_candidate_unchanged_since_source_review"),
        (W, "capture_brownfield_baselines"), (W, "close_requirements_by_mutation_scope"),
        (W, "close_requirements_with_derived_examples"), (W, "close_requirements_with_named_tests"),
        (W, "commit_terminal_candidate"), (W, "compute_workspace_content_hash"), (W, "create_git_worktree"),
        (W, "delete_checkpoint"), (W, "derive_contract_transition"), (W, "run_attempt"), (W, "save_checkpoint"),
        (C, "save_control_state"),
    ),
    "legacy_gates_exhausted": ((A, "_build_targeted_retry_prompt"), (W, "handle_attempt_failure")),
    "enforce_success_with_verification_unit": (
        (A, "_run_verification_only_attempt"), (C, "_evaluate_subtask_verification"), (C, "assess_planned_edit"),
        (C, "blocking_requirements"), (C, "build_planning_structural_evidence"), (C, "canonicalize_planned_file_actions"),
        (C, "create_git_worktree"), (C, "enforce_preserved_reference_terminal_integrity"),
        (C, "evaluate_plan_edit_feasibility"), (C, "find_missing_grounded_production_artifacts"),
        (C, "grounded_owner_candidates"), (C, "localization_candidates"), (C, "model_capacities"),
        (C, "record_plan_created"), (C, "remove_git_worktree"), (C, "resolve_migration_resolution"), (C, "validate_plan"),
        (C, "validate_stack_contract_artifacts"),

        # E12 characterization: the enforce run reaches these through the controller's names (W: its call-time imports)
        (C, "_preserved_reference_regressions"), (C, "_semantic_contract_regression_subtasks"),
        (C, "EstablishedProvenance"), (C, "TerminalGateRequest"), (C, "TerminalGateService"),
        (C, "_evaluate_integration_obligations"), (C, "annotate_run"), (C, "bound_acceptance"),
        (C, "bound_approval"), (C, "build_recovery_execution_plan"), (C, "check_plan_targets"),
        (C, "classify_planner_outcome"), (C, "coalesce_same_file_owners"), (C, "commit_verified_candidate"),
        (W, "compile_run_contract"), (C, "derive_verification_contracts"), (C, "find_ownership_findings"),
        (C, "findings_prompt_block"), (W, "immutable_test_files"), (C, "owner_candidates_prompt_block"),
        (C, "record_findings"), (C, "record_planner_outcome"), (C, "redirect_immutable_test_edits"),
        (C, "render_localization_candidates"), (C, "save_approved_plan"), (C, "scope_verification_to_requirements"),
        (C, "settle_findings"),
    ),
}

# class-attribute patch targets (global wherever the class is used) the scripted runs call through
CLASS_CALL_THROUGH: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "legacy_success": ((W, "WorkflowEngine.run_generation_workflow"),),
    "enforce_success_with_verification_unit": ((C, "WorkflowController._run_structured_enforce"),
                                               (C, "WorkflowController._write_enforce_trace")),
}

# Patched by the enforce harness itself (tests/_enforce_unit_harness.py): every enforce scenario's units are the
# harness plan's units only because these two patches are what the controller called (asserted below).
HARNESS_PATCHED = ((C, "parse_planner_structured_output"), (C, "build_engineering_plan_from_planner_output"),
                   (W, "final_review"))     # P-B: tests/test_skill_verification_deferral.py seeds a held verification into the stage-5 closer

NOT_REACHED: Dict[Tuple[str, str], str] = {
    # E14 characterization: patched by tests/test_e14_characterization.py, which calls the moved helper that looks each
    # one up directly (MEASURED: no scripted kernel run reaches these two)
    (A, "_attribute_candidate_suite"): "suite attribution against a captured baseline: no scripted kernel run captures one",
    (A, "_sync_active_repair_contract"): "process-boundary RepairContract: needs a test_process_terminated failure",
    (A, "_approve_and_execute_runtime_verification"): "runtime verification (disabled in the scripted runs; E8: "
                                                       "tests/test_gate_orchestrator.py drives the candidate gates "
                                                       "THROUGH this patch point)",
    (A, "_approve_runtime_commands"): "runtime-command approval: the scripted harnesses run with run_verification_enabled=False",
    (A, "_code_intelligence_for"): "code-intelligence retry localization (T0): no index in a scripted workspace",
    (A, "_execute_managed_service_verification"): "managed-service runtime verification (disabled; E8: "
                                                   "tests/test_gate_orchestrator.py drives it through this patch point)",
    (A, "_judge_candidate_suite"): "a candidate gate running the repository suite: no scripted kernel run writes a test "
                                   "module (E8: tests/test_gate_orchestrator.py records each judgment's scope through it)",
    (A, "_prepare_finite_command_runtime_artifacts"): "finite-command runtime verification (disabled in the scripted runs)",
    (A, "_resolve_java_home_override"): "JVM toolchain resolution: the scripted projects are Python",
    (A, "_resolve_run_command"): "runtime verification command resolution (disabled in the scripted runs)",
    (A, "_restore_api_contract_owners_deterministically"): "API-contract recovery: needs a removed public API",
    (A, "_run_coordinated_repair_generation"): "coordinated multi-file repair: the scripted plans have one file",
    (A, "_validate_and_convert_managed_service_contract"): "managed-service runtime verification (disabled)",
    (A, "apply_anchored_edits"): "anchored-edit Developer answers: the scripted Developer writes whole files",
    (A, "exact_window_reserve"): "edit-capability window for a large file: the scripted files are small",
    (A, "run_attempt"): "reached only through Best-of-N: its own call-through test below",
    (A, "run_managed_service_verification"): "managed-service runtime verification (disabled)",
    (A, "time"): "a module object (attempt's clock), not a callable seam",
    (W, "RepositoryAnalyzer.index_repository"): "repository indexing: the scripted runs have no index stage",
    (W, "_ensure_repository_indexed"): "repository indexing: the scripted runs have no index stage",
    (W, "_run_contract_acceptance"): "explicit-contract acceptance: needs a requirement contract",
    (W, "classify_ownership_violations"): "architect ownership choice: needs a brownfield ownership violation",
    (W, "include_response_construction_owners"): "JVM response-construction owners: the scripted projects are Python (E3: "
                                                 "tests/test_work_unit_stages.py drives the moved architecture stage "
                                                 "through this patch point)",
    (W, "logger"): "a Logger object, not a callable seam (E2 guard: StageDeps.logger, proven by "
                   "tests/test_outcome.py::test_the_moved_lifecycle_code_logs_through_the_engines_patched_logger)",
    (W, "resolve_future_owner_verification_deferral"): "milestone future-owner verification deferral",
    (W, "retrieve_graph_context"): "graph RAG: needs an indexed repository (the legacy_stage_context_success golden "
                                   "scripts the retrieval THROUGH this patch point: a vacuous patch drops its graph "
                                   "context from the Planner and Architect prompts the golden digests)",
    (C, "ArtifactRegistry.derive_from_workspace"): "artifact-registry derivation: brownfield artifacts",
    (C, "StaticAnalysisService.evaluate_candidate"): "enforce-level static analysis: the scripted runs disable it",
    (C, "collect_unit_grounding"): "unit grounding: needs code-intelligence grounding",
    (C, "find_migration_incomplete"): "migration route",
    (C, "save_artifact_registry"): "artifact registry write: brownfield artifacts",
    (C, "subtask_executor.execute"): "TOOL subtasks (TOOL-001): the scripted plans have model subtasks only",
    (C, "subtask_executor.revalidate_tool_subtask_authority"): "TOOL subtasks (TOOL-001/D1): the scripted plans have model subtasks only; the resume revalidation is exercised by tests/test_e12_enforce_characterization.py ER10 and tests/test_d1_tool_crash.py",
    (C, "_preserved_reference_must_preserve_lines"): "a plan repair round: the scripted plans validate first time (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "_semantic_contract_must_preserve_lines"): "a plan repair round: the scripted plans validate first time (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "build_structured_plan_repair_prompt"): "a plan repair round: the scripted plans validate first time (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "load_control_state_run_reference"): "a subtask-spanning resume: the scripted runs never resume (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "load_run_record"): "a subtask-spanning resume: the scripted runs never resume (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "quarantine_abandoned_plan_files"): "a refused resume of an abandoned plan (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "resolve_effective_scope_conflict_owners"): "a PLAN_SCOPE_DEFECT conflict: no scripted unit reports one (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "resolve_python_module_to_candidate_artifact_path"): "a RUNTIME_PLAN_GAP conflict (runtime verification is disabled) (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "resolve_runtime_plan_gap_owner"): "a RUNTIME_PLAN_GAP conflict (runtime verification is disabled) (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "revise_plan_for_grounded_scope_owner"): "a PLAN_SCOPE_DEFECT conflict: no scripted unit reports one (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "revise_plan_for_planned_prerequisite"): "a PLANNED_PREREQUISITE_OWNER_REQUIRED conflict (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (C, "revise_plan_for_runtime_plan_gap"): "a RUNTIME_PLAN_GAP conflict (runtime verification is disabled) (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
    (W, "run_contract_baseline"): "a sealed contract with a bound external authority (E12: tests/test_e12_enforce_characterization.py drives it through this patch point)",
}


def _kind(obj) -> str:
    if inspect.ismodule(obj):
        return "module"
    if inspect.isclass(obj):
        return "class"
    if inspect.iscoroutinefunction(obj):
        return "async function"
    if inspect.isfunction(obj) or inspect.ismethod(obj):
        return "function"
    return f"object:{type(obj).__name__}"


def _leaf(module: str, target: str):
    obj = importlib.import_module(module)
    for part in target.split("."):
        obj = getattr(obj, part)
    return obj


@pytest.fixture(autouse=True)
def _fixed_git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


def test_the_seam_tables_cover_exactly_the_measured_patch_targets():
    assert set(KINDS) == set(PATCH_POINTS)
    covered: List[Tuple[str, str]] = [key for keys in CALL_THROUGH.values() for key in keys]
    covered += [key for keys in CLASS_CALL_THROUGH.values() for key in keys]
    covered += list(HARNESS_PATCHED) + list(NOT_REACHED)
    assert sorted(covered) == sorted(PATCH_POINTS), "every patch target is call-through tested or named unreached, once"


@pytest.mark.parametrize("key", sorted(KINDS), ids=lambda key: f"{key[0].rsplit('.', 1)[-1]}.{key[1]}")
def test_every_patched_kernel_name_exists_with_its_kind(key):
    module, target = key
    assert _kind(_leaf(module, target)) == KINDS[key]


@pytest.mark.parametrize("scenario", sorted(CALL_THROUGH))
def test_a_patched_kernel_name_is_what_the_scripted_run_calls(scenario, tmp_path):
    mocks = {}
    patchers = []
    for module, name in CALL_THROUGH[scenario]:
        patcher = patch(f"{module}.{name}", wraps=getattr(importlib.import_module(module), name))
        mocks[(module, name)] = patcher.start()
        patchers.append(patcher)
    class_calls: Dict[Tuple[str, str], int] = {}
    originals = []
    for module, dotted in CLASS_CALL_THROUGH.get(scenario, ()):
        owner_name, attr = dotted.split(".")
        owner = getattr(importlib.import_module(module), owner_name)
        real = owner.__dict__[attr]
        key = (module, dotted)
        class_calls[key] = 0

        def counted(*args, __real=real, __key=key, **kwargs):
            class_calls[__key] += 1
            return __real(*args, **kwargs)
        originals.append((owner, attr, real))
        setattr(owner, attr, counted)
    try:
        outcome = kg.SCENARIOS[scenario](tmp_path)
    finally:
        for owner, attr, real in originals:
            setattr(owner, attr, real)
        for patcher in patchers:
            patcher.stop()
    uncalled = sorted(f"{module}.{name}" for (module, name), mock in mocks.items() if not mock.called)
    assert uncalled == [], f"patched but never called through the module on the {scenario} path: {uncalled}"
    assert all(count > 0 for count in class_calls.values()), class_calls
    # the wrapping changed nothing: the run still ends on its own terminal path
    legacy = getattr(outcome.result, "legacy_result", None) or {}
    assert bool(legacy.get("quality_gates_passed")) is (scenario != "legacy_gates_exhausted")


def test_the_enforce_harness_plan_reaches_the_controller_only_through_its_two_patched_names(tmp_path):
    outcome = kg.SCENARIOS["enforce_success_with_verification_unit"](tmp_path)
    assert {r.subtask_id: r.status.value for r in outcome.result.subtask_results} == {"s1": "completed", "sv": "completed"}


def _best_of(n):
    def configure(cfg):
        cfg.autonomy.best_of_n_first_attempt = n
    return configure


@pytest.mark.parametrize("best_of_n, reached", [(1, False), (2, True)])
def test_a_patch_on_attempt_run_attempt_reaches_only_the_best_of_n_path(best_of_n, reached, tmp_path):
    """MEASURED: workflow.py binds run_attempt at import, so the ordinary loop never calls the attempt-module name."""
    real = importlib.import_module(A).run_attempt
    with patch(f"{A}.run_attempt", wraps=real) as on_attempt, patch(f"{W}.run_attempt", wraps=real) as on_workflow:
        outcome = kg._scripted(tmp_path, lambda model, n, text: kg.GOOD, configure=_best_of(best_of_n))
    assert outcome.result.legacy_result["quality_gates_passed"] is True
    assert on_attempt.called is reached
    assert on_workflow.called is not reached



# ----------------------------------------------------------------------------- E11: the controller's moved names
# The controller's module-level planning-request, plan-revision, owner-recovery and resume-support code moved to four
# owner modules (E11); the controller keeps EVERY name: each function as a forwarder with the pre-move kind and
# signature (MEASURED at 6b33eff, before the move: this table passes on those bytes), each constant and class as a
# re-export of the owner's object. A forwarder deleted, a default or annotation changed, a coroutine turned into a
# function, or a re-export rebound fails here.
E11_CONTROLLER_NAMES: Dict[str, Tuple[str, str, Optional[str]]] = {
    'build_subtask_goal_text': ('planning_request', 'def', "(subtask: 'Subtask', position: 'int', total: 'int', *, plan: 'Optional[EngineeringPlan]' = None, grounding_goal: 'str' = '') -> 'str'"),
    'evaluate_plan_edit_feasibility': ('planning_request', 'async', "(config: 'Optional[Any]', plan: 'EngineeringPlan', goal: 'str', workspace_path: 'str') -> 'List[EditFeasibility]'"),
    'PLANNED_FILE_SOURCE_REASON': ('planning_request', 'str', None),
    'BOUNDED_SUBTASK_DESIGN': ('planning_request', 'str', None),
    'split_planned_source': ('planning_request', 'def', "(package: 'Any') -> 'Tuple[List[str], Any]'"),
    'build_subtask_constraint_context': ('planning_request', 'def', "(original_goal: 'str') -> 'str'"),
    'build_subtask_semantic_context': ('planning_request', 'def', "(plan: 'EngineeringPlan', subtask: 'Subtask') -> 'str'"),
    'build_subtask_plan_text': ('planning_request', 'def', "(subtask: 'Subtask') -> 'str'"),
    'AUTHORITATIVE_PLANNER_SYSTEM_PROMPT': ('planning_request', 'str', None),
    '_authoritative_planner_extension_candidates': ('planning_request', 'def', "(workspace_path: 'str', max_files: 'int' = 100, *, goal: 'str' = '') -> 'List[str]'"),
    '_is_strict_regression': ('planning_request', 'def', "(retained_reason_codes: 'Optional[frozenset]', candidate_reason_codes: 'frozenset') -> 'bool'"),
    '_preserved_reference_must_preserve_lines': ('planning_request', 'def', "(obligation_ledger: 'ObligationLedger') -> 'List[str]'"),
    '_semantic_contract_must_preserve_lines': ('planning_request', 'def', "(obligation_ledger: 'ObligationLedger') -> 'List[str]'"),
    '_SUBTASK_MENTION_RE': ('planning_request', 'Pattern', None),
    '_QUOTED_TOKEN_RE': ('planning_request', 'Pattern', None),
    '_REQUIRES_PROVIDES_ERROR_KEYWORDS': ('planning_request', 'tuple', None),
    '_subtask_ids_mentioned': ('planning_request', 'def', "(texts: 'List[str]') -> 'Set[str]'"),
    '_semantic_contract_regression_subtasks': ('planning_request', 'def', "(obligation_ledger: 'ObligationLedger', regressions_before: 'int', implicated_subtask_ids: 'Set[str]') -> 'List[str]'"),
    '_PRESERVED_REFERENCE_CONFLICT_RE': ('planning_request', 'Pattern', None),
    '_preserved_reference_pairs_mentioned': ('planning_request', 'def', "(texts: 'List[str]') -> 'Set[Tuple[str, str]]'"),
    '_preserved_reference_regressions': ('planning_request', 'def', "(obligation_ledger: 'ObligationLedger', regressions_before: 'int', implicated_pairs: 'Set[Tuple[str, str]]') -> 'List[Tuple[str, str]]'"),
    'build_authoritative_planner_request': ('planning_request', 'def', "(goal: 'str', *, route_kind: 'Optional[ChangeKind]' = None, extension_candidates: 'Optional[List[str]]' = None, repository_candidates: 'Optional[List[str]]' = None, structural_evidence: 'str' = '') -> 'str'"),
    '_JAVA_CONSTRUCTOR_INSTANTIATION_RE': ('planning_request', 'Pattern', None),
    '_JAVA_TYPE_MENTION_RE': ('planning_request', 'Pattern', None),
    'build_planning_structural_evidence': ('planning_request', 'def', "(workspace_path: 'str', candidate_paths: 'List[str]') -> 'Tuple[str, Dict[str, List[str]]]'"),
    '_transitive_depends_on': ('planning_request', 'def', "(plan: 'EngineeringPlan', subtask_id: 'str') -> 'set'"),
    'find_missing_grounded_production_artifacts': ('planning_request', 'def', "(plan: 'EngineeringPlan', resolved_edges: 'Dict[str, List[str]]', *, workspace_path: 'Optional[str]' = None, obligation_ledger: 'Optional[ObligationLedger]' = None, revision: 'object' = None) -> 'List[Dict[str, str]]'"),
    'build_approved_plan_document': ('planning_request', 'def', "(plan: 'EngineeringPlan', *, plan_hash: 'str', repair_attempts: 'int', stage_states: 'Dict[str, str]', lifecycle_state: 'str') -> 'Dict[str, Any]'"),
    'transitive_subtask_dependents': ('plan_revision', 'def', "(plan: 'EngineeringPlan', subtask_id: 'str') -> 'List[str]'"),
    'resolve_scope_conflict_owners': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', required_files: 'List[str]', failed_subtask: 'Subtask') -> 'Dict[str, List[str]]'"),
    'nearest_mutating_upstream_owner': ('plan_revision', 'def', "(plan: 'EngineeringPlan', subtask: 'Subtask', completed_ids: 'Sequence[str]') -> 'Optional[Subtask]'"),
    '_GATE_FAILURE_TYPES': ('owner_recovery', 'frozenset', None),
    'verification_failure_evidence': ('owner_recovery', 'def', "(call_result: 'Mapping[str, Any]') -> 'str'"),
    'verification_owner_recovery_context': ('owner_recovery', 'def', "(*, verification_subtask_id: 'str', owner_id: 'str', owner_files: 'Sequence[str]', evidence: 'str') -> 'str'"),
    'ArtifactOwnerResolutionBasis': ('owner_recovery', 'EnumType', None),
    'OwnerResolution': ('owner_recovery', 'type', None),
    '_successful_modifiers_in_execution_order': ('owner_recovery', 'def', "(path: 'str', order: 'List[str]', control_state: 'ControlState') -> 'List[str]'"),
    'resolve_effective_artifact_owner': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', path: 'str', failed_subtask: 'Subtask', order: 'Optional[List[str]]' = None, control_state: 'Optional[ControlState]' = None) -> 'OwnerResolution'"),
    'resolve_effective_scope_conflict_owners': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', required_files: 'List[str]', failed_subtask: 'Subtask', order: 'Optional[List[str]]' = None, control_state: 'Optional[ControlState]' = None) -> 'Dict[str, List[str]]'"),
    '_cross_owner_obligation_id': ('owner_recovery', 'def', "(originating_subtask_id: 'str', owner_subtask_id: 'str', required_files: 'List[str]', failure_family: 'str', generation: 'int') -> 'str'"),
    '_prior_cross_owner_musts': ('owner_recovery', 'def', "(ledger: 'Optional[ObligationLedger]', *, owner_subtask_id: 'str', required_files: 'List[str]') -> 'List[str]'"),
    '_get_or_create_cross_owner_obligation': ('owner_recovery', 'def', "(ledger: 'Optional[ObligationLedger]', *, originating_subtask_id: 'str', owner_subtask_id: 'str', required_files: 'List[str]', scope_conflict: 'Dict[str, Any]', generation: 'int', revision: 'Any') -> 'Optional[ObligationRecord]'"),
    '_build_owner_recovery_context': ('owner_recovery', 'def', "(*, owner_id: 'str', failed_subtask_id: 'str', required_owner_files: 'List[str]', cross_owner_obligation: 'Optional[ObligationRecord]', scope_conflict: 'Dict[str, Any]') -> 'str'"),
    '_evaluate_recovery_acceptance_precheck': ('owner_recovery', 'def', "(*, scope_conflict: 'Dict[str, Any]', plan_workspace_path: 'str', required_owner_files: 'List[str]') -> 'Optional[bool]'"),
    '_participant_evidence_is_grounded': ('owner_recovery', 'def', "(scope_conflict: 'Dict[str, Any]') -> 'bool'"),
    '_scope_conflict_evidence_authority': ('owner_recovery', 'def', "(scope_conflict: 'Dict[str, Any]') -> 'ObligationAuthority'"),
    'derive_recovery_participants': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', scope_conflict: 'Dict[str, Any]', failed_subtask: 'Subtask', order: 'Optional[List[str]]' = None, control_state: 'Optional[ControlState]' = None, recovery_generation_by_key: 'Optional[Dict[Tuple[str, str, Tuple[str, ...]], int]]' = None) -> 'Tuple[RecoveryParticipant, ...]'"),
    '_order_recovery_groups': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', participants: 'Tuple[RecoveryParticipant, ...]') -> 'Optional[Tuple[RecoveryOwnerGroup, ...]]'"),
    'build_recovery_execution_plan': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', scope_conflict: 'Dict[str, Any]', failed_subtask: 'Subtask', *, order: 'Optional[List[str]]' = None, control_state: 'Optional[ControlState]' = None, plan_generation: 'int' = 0, recovery_generation_by_key: 'Optional[Dict[Tuple[str, str, Tuple[str, ...]], int]]' = None) -> 'Optional[RecoveryExecutionPlan]'"),
    '_attempt_owner_recovery_self_correction': ('owner_recovery', 'async', "(*, kernel: 'Any', developer_llm: 'Any', plan_workspace_path: 'str', workspace_path: 'str', owner: 'Subtask', required_owner_files: 'List[str]', scope_conflict: 'Dict[str, Any]') -> 'Optional[Dict[str, Any]]'"),
    '_integration_reference_token': ('owner_recovery', 'def', "(path: 'str') -> 'str'"),
    '_integration_evidence': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', rel: 'Any', consumer_id: 'str', producer_paths: 'List[str]', consumer_paths: 'List[str]', established_file_context: 'Dict[str, str]', provenance: 'EstablishedProvenance') -> 'Tuple[List[str], Dict[str, Any]]'"),
    '_evaluate_integration_obligations': ('owner_recovery', 'def', "(plan: 'EngineeringPlan', obligation_ledger: 'Optional[ObligationLedger]', completed_subtask_id: 'str', established_file_context: 'Dict[str, str]', revision: 'Any', provenance: 'Optional[EstablishedProvenance]' = None) -> 'None'"),
    '_transitive_dependents': ('plan_revision', 'def', "(plan: 'EngineeringPlan', subtask_id: 'str') -> 'set[str]'"),
    '_transitive_upstream_ids': ('plan_revision', 'def', "(plan: 'EngineeringPlan', subtask_id: 'str') -> 'set[str]'"),
    'PLAN_SCOPE_REVISION_REQUIRED': ('plan_revision', 'str', None),
    'subtask_scope_conflict_report': ('plan_revision', 'def', "(scope_conflict: 'Dict[str, Any]') -> 'Tuple[str, Tuple[str, ...]]'"),
    '_plan_scope_conflict_files': ('plan_revision', 'def', "(scope_conflict: 'Optional[Dict[str, object]]') -> 'List[str]'"),
    'revise_plan_for_grounded_scope_owner': ('plan_revision', 'def', "(plan: 'EngineeringPlan', failed_subtask_id: 'str', grounded_files: 'List[str]', workspace_path: 'str') -> 'EngineeringPlan'"),
    'revise_plan_for_planned_prerequisite': ('plan_revision', 'def', "(plan: 'EngineeringPlan', *, consumer_subtask_id: 'str', owner_subtask_id: 'str', capability: 'str', consumer_artifacts: 'Iterable[str]' = ()) -> 'EngineeringPlan'"),
    'resolve_python_module_to_candidate_artifact_path': ('plan_revision', 'def', "(module_name: 'str', workspace_path: 'str') -> 'Optional[str]'"),
    'resolve_runtime_plan_gap_owner': ('plan_revision', 'def', "(plan: 'EngineeringPlan', candidate_path: 'str', failed_subtask: 'Subtask') -> 'Optional[str]'"),
    'revise_plan_for_runtime_plan_gap': ('plan_revision', 'def', "(plan: 'EngineeringPlan', *, owner_subtask_id: 'str', artifact_path: 'str', reason: 'str') -> 'EngineeringPlan'"),
    'classify_tool_resume': ('resume_support', 'def', "(plan: 'EngineeringPlan', resumed_subtask_states: 'Dict[str, str]') -> 'ToolResumeDecision'"),
    'all_subtasks_completed': ('resume_support', 'def', "(current_plan_subtask_ids: 'Set[str]', latest_status_by_subtask: 'Dict[str, SubtaskStatus]') -> 'bool'"),
    'compute_abandoned_plan_files': ('resume_support', 'def', "(prior_subtask_states: 'Dict[str, str]', prior_subtask_written_files: 'Dict[str, List[str]]', new_plan_files: 'Any') -> 'List[str]'"),
    'quarantine_abandoned_plan_files': ('resume_support', 'def', "(workspace_path: 'str', abandoned_files: 'List[str]', quarantine_subdir: 'str') -> 'List[str]'"),
}
E11_DEPS_BUILDERS = {
    "_planning_deps": ("logger", "model_capacities", "assess_planned_edit", "collect_unit_grounding", "EditFeasibility",
                       "Feasibility", "topological_subtask_order"),
    "_revision_deps": ("topological_subtask_order",),
    "_owner_recovery_deps": ("logger", "DETERMINISTIC_ATTRIBUTION_TIERS"),
    "_resume_deps": ("logger",),   # D2 M4: the retired ownership migrations were the only users of the other collaborators
}


@pytest.mark.parametrize("name", sorted(E11_CONTROLLER_NAMES))
def test_the_controller_keeps_every_e11_name_with_its_kind_and_signature(name):
    _owner, kind, signature = E11_CONTROLLER_NAMES[name]
    obj = getattr(importlib.import_module(C), name)
    if kind in ("def", "async"):
        assert inspect.isfunction(obj) and obj.__module__ == C
        assert inspect.iscoroutinefunction(obj) is (kind == "async")
        assert str(inspect.signature(obj)) == signature
    else:
        assert type(obj).__name__ == kind


@pytest.mark.parametrize("name", sorted(n for n, (_o, kind, _s) in E11_CONTROLLER_NAMES.items() if kind not in ("def", "async")))
def test_every_e11_reexport_is_the_owners_object(name):
    owner = E11_CONTROLLER_NAMES[name][0]
    assert getattr(importlib.import_module(C), name) is getattr(importlib.import_module(f"kriya.workflow.{owner}"), name)


@pytest.mark.parametrize("builder", sorted(E11_DEPS_BUILDERS))
def test_each_e11_deps_builder_resolves_the_controllers_names_at_call_time(builder):
    controller = importlib.import_module(C)
    for field in E11_DEPS_BUILDERS[builder]:
        marker = object()
        with patch.object(controller, field, marker):
            assert getattr(getattr(controller, builder)(), field) is marker, (builder, field)


@pytest.mark.parametrize("loader, module, names", [
    ("_load_run_self_correction_loop", "kriya.workflow.self_correction", ("run_self_correction_loop",)),
])
def test_each_e11_loader_imports_at_call_time(loader, module, names):
    controller = importlib.import_module(C)
    markers = [object() for _ in names]
    with patch.multiple(module, **dict(zip(names, markers, strict=True))):
        loaded = getattr(controller, loader)()
    assert (loaded if len(names) > 1 else (loaded,)) == tuple(markers)


# ----------------------------------------------------------------------------- E12: the enforce loop and the shadow run

# WorkflowController keeps the three method names with their pre-move kind and signature (measured at b834d71); the
# bodies are kriya/workflow/shadow_mode.py (ShadowMode.run, ShadowMode.write_enforce_trace) and
# kriya/workflow/enforce_loop.py (EnforceLoop.run), reached through one Deps built per forwarder entry.
E12_METHODS: Dict[str, Tuple[str, str, str, str]] = {
    "_run_structured_shadow": ("shadow_mode", "ShadowMode.run", "async", "(self, goal: 'str', workspace_path: 'str', route: 'Any', run_id: 'str', control_context: 'WorkflowControlContext', control_state: 'ControlState') -> 'Tuple[Optional[EngineeringPlan], Tuple[SubtaskResult, ...], Tuple[Decision, ...], Optional[VerificationReport], List[str]]'"),
    "_write_enforce_trace": ("shadow_mode", "ShadowMode.write_enforce_trace", "def", "(self, run_id: 'str', goal: 'str', result: 'Dict[str, Any]', started_at: 'float') -> 'bool'"),
    "_run_structured_enforce": ("enforce_loop", "EnforceLoop.run", "async", "(self, goal: 'str', workspace_path: 'str', route: 'Any', run_id: 'str', control_context: 'WorkflowControlContext', control_state: 'ControlState', legacy_kwargs: 'Dict[str, Any]') -> 'Tuple[Dict[str, Any], Optional[EngineeringPlan], Tuple[SubtaskResult, ...], Tuple[Decision, ...], Optional[VerificationReport], ControlState]'"),
}
E12_BUILDERS = {"_shadow_deps": ("shadow_mode", "ShadowDeps"), "_trace_deps": ("shadow_mode", "TraceDeps"),
                "_enforce_deps": ("enforce_loop", "EnforceDeps")}
E12_LOADERS = {
    "_load_write_outcome_trace": ("kriya.workflow.run_trace", ("write_outcome_trace",)),
    "_load_run_contract": ("kriya.workflow.workflow", ("_requirement_policy_blocks", "compile_run_contract",
                                                       "seal_run_contract")),
    "_load_contract_planning_constraints": ("kriya.workflow.workflow", ("documentation_referent_paths",
                                                                        "frozen_file_paths", "immutable_test_files")),
    "_load_goal_facts": ("kriya.workflow.goal_facts", ("delegating_shim_targets", "goal_facts_block")),
    "_load_run_contract_baseline": ("kriya.workflow.workflow", ("run_contract_baseline",)),
    "_load_run_shadow_refused": ("kriya.skills.skill", ("RunShadowRefused",)),         # GIT-MIDRUN-COMMITS-S3-001
}
_E12_SELF_FIELDS = ("workflow_engine", "_build_context")


def _e12_fields(owner: str, deps: str) -> List[str]:
    import dataclasses
    return [f.name for f in dataclasses.fields(getattr(importlib.import_module(f"kriya.workflow.{owner}"), deps))]


@pytest.mark.parametrize("name", sorted(E12_METHODS))
def test_the_controller_keeps_every_e12_method_with_its_kind_and_signature(name):
    _owner, _target, kind, signature = E12_METHODS[name]
    from kriya.workflow.workflow_controller import WorkflowController
    method = WorkflowController.__dict__[name]
    assert inspect.isfunction(method) and method.__module__ == C
    assert inspect.iscoroutinefunction(method) is (kind == "async")
    assert str(inspect.signature(method)) == signature


@pytest.mark.parametrize("builder", sorted(E12_BUILDERS))
def test_each_e12_deps_builder_resolves_every_controller_name_at_call_time(builder):
    """Every module-level field is read from the controller when the builder runs: a patch there is what the moved
    body gets (negative control: a builder that bound an original or imported one directly fails here)."""
    controller_module = importlib.import_module(C)
    owner, deps = E12_BUILDERS[builder]
    engine, context = object(), object()
    controller = type("Controller", (), {"workflow_engine": engine, "_build_context": context})()
    fields = _e12_fields(owner, deps)
    built = getattr(controller_module, builder)(controller)
    assert [f for f in fields if f in _E12_SELF_FIELDS] and built.workflow_engine is engine
    if "_build_context" in fields:
        assert built._build_context is context  # pylint: disable=protected-access
    for field in fields:
        if field in _E12_SELF_FIELDS:
            continue
        source = f"_{field}" if f"_{field}" in E12_LOADERS else field
        marker = object()
        with patch.object(controller_module, source, marker):
            assert getattr(getattr(controller_module, builder)(controller), field) is marker, (builder, field)


@pytest.mark.parametrize("builder", sorted(E12_BUILDERS))
def test_each_e12_deps_is_frozen_and_requires_every_field(builder):
    import dataclasses
    owner, deps = E12_BUILDERS[builder]
    cls = getattr(importlib.import_module(f"kriya.workflow.{owner}"), deps)
    params = cls.__dataclass_params__
    assert params.frozen
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
               for f in dataclasses.fields(cls))
    with pytest.raises(TypeError):
        cls()


@pytest.mark.parametrize("owner, deps", sorted(set(E12_BUILDERS.values())))
def test_the_moved_bodies_read_exactly_their_deps_fields(owner, deps):
    """A body reading a deps attribute that is not a field raises AttributeError at run time - inside several
    log-and-continue handlers that would hide it. Static guard: the set of ``deps.<attr>`` reads in the owner's
    classes equals the fields of the Deps their methods take."""
    import ast
    path = Path(importlib.import_module(f"kriya.workflow.{owner}").__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used = set()
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and not n.name.endswith("Deps")):
        for fn in (m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))):
            annotation = fn.args.args[0].annotation
            if annotation is not None and ast.unparse(annotation) == deps:
                used |= {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)
                         and isinstance(n.value, ast.Name) and n.value.id == "deps"}
    assert used == set(_e12_fields(owner, deps))


@pytest.mark.parametrize("loader", sorted(E12_LOADERS))
def test_each_e12_loader_imports_at_call_time(loader):
    controller = importlib.import_module(C)
    module, names = E12_LOADERS[loader]
    markers = [object() for _ in names]
    with patch.multiple(module, **dict(zip(names, markers, strict=True))):
        loaded = getattr(controller, loader)()
    assert (loaded if len(names) > 1 else (loaded,)) == tuple(markers)


def test_enforce_deps_follow_controller_patches(tmp_path):
    """SLICE-PLAN E12's named seam test: the enforce run reaches the sandbox lifecycle, the plan validation and the
    terminal static analysis through the controller's names, patched after import (the suite's way), and builds its
    EnforceDeps once per run, not per subtask."""
    controller_module = importlib.import_module(C)
    real_builder = controller_module._enforce_deps  # pylint: disable=protected-access
    builds = []

    def counting(controller):
        builds.append(controller)
        return real_builder(controller)
    names = ("create_git_worktree", "remove_git_worktree", "validate_plan", "StaticAnalysisService")
    patchers = [patch.object(controller_module, name, wraps=getattr(controller_module, name)) for name in names]
    mocks = [p.start() for p in patchers]
    try:
        with patch.object(controller_module, "_enforce_deps", side_effect=counting):
            outcome = kg.SCENARIOS["enforce_success_with_verification_unit"](tmp_path)
    finally:
        for patcher in patchers:
            patcher.stop()
    assert [m.called for m in mocks] == [True] * len(names)
    assert len(builds) == 1
    assert {r.subtask_id for r in outcome.result.subtask_results} == {"s1", "sv"}


def test_the_e12_owners_are_imported_only_when_a_run_needs_them():
    """E12 performance rule (MEASURED: creating the 174-field EnforceDeps class costs ~5 ms, ShadowDeps/TraceDeps
    ~1 ms): importing kriya.cli or the controller imports neither owner module; the controller's accessors import them
    when a forwarder first needs one (negative control: an eager top-level import of either fails here)."""
    import subprocess

    code = ("import sys\n"
            "import kriya.cli\n"
            "import kriya.workflow.workflow_controller as controller\n"
            "owners = ('kriya.workflow.enforce_loop', 'kriya.workflow.shadow_mode')\n"
            "assert not [m for m in owners if m in sys.modules], [m for m in owners if m in sys.modules]\n"
            "assert controller._enforce_loop().EnforceLoop and controller._shadow_mode().ShadowMode\n"
            "assert all(m in sys.modules for m in owners)\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]),
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
