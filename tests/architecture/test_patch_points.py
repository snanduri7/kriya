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
    (A, '_approve_runtime_commands'): P(1, A, ((A, 2),), ()),
    (A, '_build_targeted_retry_prompt'): P(2, 'kriya.workflow.retry_prompts', ((A, 2),), ()),
    (A, '_check_java_toolchain_mismatch'): P(2, 'kriya.workflow.toolchain', ((A, 2),), ()),
    (A, '_code_intelligence_for'): P(2, A, ((A, 2),), ()),
    # E6 characterization: +1 site (tests/test_fallback_decision.py records which request profiles a fallback decision
    # resolves, through this name: the selection, the call-time substitution and the model entry look it up here).
    # E6: 4 -> 3 lookups: the selection's and the substitution's loads moved to fallback_decision and read
    # FallbackDeps._developer_request_profile (one load in attempt._fallback_deps), the model entry's to generation_service
    # (one load in attempt._generation_deps); the retry preparation keeps its own. The patch site above is the dynamic proof.
    (A, '_developer_request_profile'): P(1, A, ((A, 3),), ()),
    (A, '_ensure_generation_time_budget'): P(2, A, ((A, 1),), ()),
    (A, '_enter_developer_model'): P(1, A, ((A, 1),), ()),
    (A, '_maybe_run_developer_investigation'): P(1, A, ((A, 1),), ()),
    (A, '_prepare_finite_command_runtime_artifacts'): P(1, A, ((A, 3),), ()),
    (A, '_reject_exception_masking'): P(1, A, ((A, 2),), ()),
    (A, '_require_qualified_retry_identity'): P(1, A, ((A, 1),), ()),
    (A, '_resolve_java_home_override'): P(4, 'kriya.workflow.toolchain', ((A, 2),), ()),
    (A, '_resolve_known_target_member_hints'): P(1, A, ((A, 3),), ()),
    (A, '_resolve_run_command'): P(10, 'kriya.workflow.file_resolution', ((A, 3),), ()),
    (A, '_restore_api_contract_owners_deterministically'): P(1, A, ((A, 1),), ()),
    (A, '_run_coordinated_repair_generation'): P(5, A, ((A, 1),), ()),
    (A, '_run_developer_generation'): P(11, A, ((A, 5),), ()),
    (A, '_run_verification_only_attempt'): P(1, A, ((A, 1),), ()),
    (A, '_validate_and_convert_managed_service_contract'): P(1, A, ((A, 1),), ()),
    (A, 'apply_anchored_edits'): P(1, 'kriya.workflow.edit_safety', ((A, 3),), ()),
    (A, 'defer_execution_until_source_review'): P(1, A, ((A, 1),), ()),
    (A, 'exact_window_reserve'): P(1, 'kriya.workflow.edit_capability', ((A, 2),), ()),
    (A, 'extract_target_test'): P(4, 'kriya.workflow.file_resolution', ((A, 1),), ()),
    (A, 'find_runnable_test_files'): P(3, 'kriya.workflow.file_resolution', ((A, 2),), ()),
    (A, 'normalize_written_filepath'): P(1, 'kriya.workflow.file_resolution', ((A, 1),), ()),
    (A, 'run_attempt'): P(10, A, (('kriya.workflow.best_of_n', 1),), (W,)),
    (A, 'run_managed_service_verification'): P(17, 'kriya.tools.service_runtime', ((A, 1),), ()),
    # E6: 9 -> 8 lookups: the generation's two time.monotonic() loads moved to generation_service and read
    # GenerationDeps.time, bound to this module's time by attempt._generation_deps at call time (one new load), so a
    # patch of kriya.workflow.attempt.time still reaches the generation's duration measurement.
    (A, 'time'): P(1, 'time', ((A, 8),), ()),
    # kriya.workflow.workflow
    (W, 'RepositoryAnalyzer'): P(3, 'kriya.analyzer.analyzer', ((W, 2),), ()),
    (W, 'RepositoryAnalyzer.index_repository'): P(3, 'kriya.analyzer.analyzer', None, ()),
    (W, 'WorkflowEngine.run_generation_workflow'): P(18, W, None, ()),
    (W, '_direct_terminal_writes'): P(1, W, ((W, 4),), ()),
    (W, '_ensure_repository_indexed'): P(2, W, ((W, 1),), ()),
    (W, '_gate_outcome_proven'): P(1, W, ((W, 2),), ()),
    (W, '_migration_identities'): P(1, W, ((W, 1),), ()),
    # E4: 2 -> 1 lookup: both loads (_oracle_base_revision, mutation_scope_evidence) moved to requirement_closers and
    # bind CloserDeps.run_committed_paths, built by _closer_deps from this name at call time (the two patch sites are
    # the dynamic proof: they patch it on this module and drive the moved closers).
    (W, '_run_committed_paths'): P(2, W, ((W, 1),), ()),
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
    (W, 'compute_workspace_content_hash'): P(2, 'kriya.workflow.checkpoint', ((W, 3),), ()),
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
    # kriya.workflow.workflow_controller
    (C, 'ArtifactRegistry.derive_from_workspace'): P(4, 'kriya.control.artifacts', None, ()),
    (C, 'StaticAnalysisService.evaluate_candidate'): P(1, 'kriya.static_analysis.service', None, ()),
    (C, 'WorkflowController._run_structured_enforce'): P(1, C, None, ()),
    (C, 'WorkflowController._write_enforce_trace'): P(2, C, None, ()),
    (C, '_evaluate_subtask_verification'): P(1, C, ((C, 2),), ()),
    (C, 'assess_planned_edit'): P(1, 'kriya.workflow.edit_feasibility', ((C, 2),), ()),
    (C, 'blocking_requirements'): P(1, 'kriya.workflow.requirements', ((C, 1),), ()),
    (C, 'build_engineering_plan_from_planner_output'): P(63, 'kriya.workflow.plan_schema', ((C, 2),), ()),
    (C, 'build_planning_structural_evidence'): P(1, C, (('kriya.workflow.ownership_findings', 1), (C, 1)), ()),
    (C, 'canonicalize_planned_file_actions'): P(1, 'kriya.workflow.plan_validation', ((C, 2),), ()),
    (C, 'collect_unit_grounding'): P(8, 'kriya.workflow.edit_feasibility', ((C, 1),), ()),
    (C, 'create_git_worktree'): P(22, 'kriya.workflow.worktree', ((C, 1),), ()),
    (C, 'enforce_preserved_reference_terminal_integrity'): P(3, 'kriya.workflow.terminal_gate_service', ((C, 1),), ()),
    (C, 'evaluate_plan_edit_feasibility'): P(3, C, ((C, 1),), ()),
    (C, 'find_migration_incomplete'): P(2, 'kriya.workflow.migration', ((C, 1),), ()),
    (C, 'find_missing_grounded_production_artifacts'): P(2, C, ((C, 1),), ()),
    (C, 'grounded_owner_candidates'): P(1, 'kriya.workflow.ownership_findings', ((C, 1),), ()),
    (C, 'localization_candidates'): P(1, 'kriya.workflow.graph_retrieval', ((C, 1),), ()),
    (C, 'model_capacities'): P(11, 'kriya.workflow.edit_feasibility', ((C, 1),), ()),
    (C, 'parse_planner_structured_output'): P(68, 'kriya.agents.contracts', ((C, 2),), ()),
    (C, 'record_plan_created'): P(1, 'kriya.workflow.subtask_telemetry', ((C, 2),), ()),
    (C, 'remove_git_worktree'): P(9, 'kriya.workflow.worktree', ((C, 1),), ()),
    (C, 'resolve_migration_resolution'): P(3, 'kriya.workflow.migration', ((C, 1),), ()),
    (C, 'save_artifact_registry'): P(1, 'kriya.control.persistence', ((C, 3),), ()),
    (C, 'save_control_state'): P(4, 'kriya.control.persistence', ((C, 6),), ()),
    (C, 'subtask_executor.execute'): P(6, 'kriya.workflow.subtask_executor', None, ()),
    (C, 'validate_plan'): P(46, 'kriya.workflow.plan_validation', ((C, 5),), ()),
    (C, 'validate_stack_contract_artifacts'): P(1, 'kriya.workflow.static_checks', ((C, 1),), ()),
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
    # A._developer_request_profile site (test_fallback_decision: the profiles a fallback decision resolves)
    assert totals == {A: 88, W: 88, C: 268}
    assert len(PATCH_POINTS) == 84


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
                        "kriya.workflow.generation_service", "kriya.workflow.fallback_decision")
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
