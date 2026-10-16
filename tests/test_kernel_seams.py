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
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest
from architecture.test_patch_points import PATCH_POINTS

# The scenario module and its harness (_scripted_run) live in tests/state_machine/, which pytest puts on sys.path
# for its own files only; imported by bare name exactly as the state-machine tier does (one module object).
sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

A, W, C = "kriya.workflow.attempt", "kriya.workflow.workflow", "kriya.workflow.workflow_controller"

KINDS: Dict[Tuple[str, str], str] = {
    (A, '_approve_runtime_commands'): 'async function',
    (A, '_build_targeted_retry_prompt'): 'function',
    (A, '_check_java_toolchain_mismatch'): 'function',
    (A, '_code_intelligence_for'): 'function',
    (A, '_ensure_generation_time_budget'): 'function',
    (A, '_enter_developer_model'): 'function',
    (A, '_maybe_run_developer_investigation'): 'async function',
    (A, '_prepare_finite_command_runtime_artifacts'): 'function',
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
    (C, 'validate_plan'): 'async function',
    (C, 'validate_stack_contract_artifacts'): 'function',
}

# scenario -> the module-attribute patch targets its run must call through the patched module
CALL_THROUGH: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "legacy_success": (
        (A, "_check_java_toolchain_mismatch"), (A, "_ensure_generation_time_budget"), (A, "_enter_developer_model"),
        (A, "_maybe_run_developer_investigation"), (A, "_reject_exception_masking"), (A, "_require_qualified_retry_identity"),
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
HARNESS_PATCHED = ((C, "parse_planner_structured_output"), (C, "build_engineering_plan_from_planner_output"))

NOT_REACHED: Dict[Tuple[str, str], str] = {
    (A, "_approve_runtime_commands"): "runtime-command approval: the scripted harnesses run with run_verification_enabled=False",
    (A, "_code_intelligence_for"): "code-intelligence retry localization (T0): no index in a scripted workspace",
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

