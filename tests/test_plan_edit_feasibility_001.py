"""PLAN-EDIT-FEASIBILITY-001: a plan is never approved with an edit the
executor's own edit protocol can already be shown unable to run.

The decision (kriya/workflow/edit_feasibility.py) reuses the executor's own
helpers; these tests pin that it agrees with the real allocator and the real
EditCapability on identical inputs, that only a proven INFEASIBLE refuses a
plan, and that the refusal enters the existing bounded plan repair (no budget
added, no plan rewritten, no authority widened)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config

from kriya.workflow import edit_feasibility as ef
from kriya.workflow.context_budget import _reserve_graph_context_budget, build_known_target_context, estimate_tokens
from kriya.workflow.edit_capability import build_edit_capability
from kriya.workflow.edit_feasibility import (
    EditFeasibility,
    Feasibility,
    ModelCapacity,
    UnitGrounding,
    assess_planned_edit,
    infeasible_results,
    model_capacities,
    repair_error_text,
)
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
)
from kriya.workflow.planner_repair import build_structured_plan_repair_prompt
from kriya.workflow.workflow_controller import WorkflowController, evaluate_plan_edit_feasibility

# A Java-shaped test class: many unrelated members, none named by the goals below.
_PATH = "src/test/java/pkg/WidgetTest.java"
_GOAL = "Widget.render() returns a stale label after rename. Fix it so the label is current."


def _big_test_file(methods: int) -> str:
    body = "".join(
        f"    @Test public void case{i}() {{\n        assertEquals({i}, Widgets.value({i}));\n    }}\n\n"
        for i in range(methods)
    )
    return "package pkg;\n\npublic class WidgetTest {\n" + body + "}\n"


_NO_GROUNDING = UnitGrounding()


def _caps(room: int, *more: int) -> list:
    return [ModelCapacity("primary", room), *(ModelCapacity(f"fallback{i}", r) for i, r in enumerate(more))]


def _assess(content: str, *, room: int, goal: str = _GOAL, grounding=_NO_GROUNDING, capacities=None,
            path: str = _PATH, unit_files: int = 1) -> EditFeasibility:
    return assess_planned_edit(
        subtask_id="s2", path=path, content=content, unit_goal=goal, grounding_goal=goal,
        capacities=capacities if capacities is not None else _caps(room), grounding=grounding,
        unit_file_count=unit_files)


# --- the decision: FEASIBLE / INFEASIBLE / UNKNOWN ----------------------------------------------------------

def test_a_file_that_fits_the_largest_room_is_feasible():
    content = _big_test_file(3)
    result = _assess(content, room=estimate_tokens(content) + 100)
    assert result.outcome is Feasibility.FEASIBLE


def test_minimum_unit_beyond_every_room_with_no_source_of_an_editable_region_is_infeasible():
    content = _big_test_file(400)
    room = estimate_tokens(content) // 4
    result = _assess(content, room=room)
    assert result.outcome is Feasibility.INFEASIBLE
    evidence = result.evidence
    assert evidence["file"] == _PATH
    assert evidence["required_capacity_tokens"] == estimate_tokens(content)
    assert evidence["available_capacity_tokens"] == room
    assert evidence["edit_protocol"] == ["anchored_edit", "full_file_replacement"]
    assert evidence["models"] == [{"model": "primary", "room_tokens": room}]
    assert evidence["provenance"]


def test_the_exact_boundary_is_feasible_one_token_over_is_infeasible():
    content = _big_test_file(120)
    cost = estimate_tokens(content)
    assert _assess(content, room=cost).outcome is Feasibility.FEASIBLE
    assert _assess(content, room=cost - 1).outcome is Feasibility.INFEASIBLE


def test_the_decision_uses_the_largest_room_among_the_capacities_it_is_given():
    content = _big_test_file(120)
    cost = estimate_tokens(content)
    assert _assess(content, room=0, capacities=_caps(cost - 10, cost)).outcome is Feasibility.FEASIBLE
    assert _assess(content, room=0, capacities=_caps(cost - 10, cost - 5)).outcome is Feasibility.INFEASIBLE


def test_unknown_capacity_is_unknown_never_a_rejection():
    content = _big_test_file(400)
    result = _assess(content, room=0, capacities=[])
    assert result.outcome is Feasibility.UNKNOWN
    assert infeasible_results([result]) == []


def test_an_unevaluated_unit_retrieval_is_unknown_and_asks_for_grounding():
    content = _big_test_file(400)
    result = _assess(content, room=10, grounding=None)
    assert result.outcome is Feasibility.UNKNOWN
    assert result.evidence.get("needs_grounding") is True


def test_a_file_the_unit_retrieval_grounds_is_unknown():
    content = _big_test_file(400)
    assert _assess(content, room=10, grounding=UnitGrounding(frozenset({_PATH}))).outcome is Feasibility.UNKNOWN
    # grounding of another file does not block the refusal
    assert _assess(content, room=10, grounding=UnitGrounding(frozenset({"other.java"}))).outcome is Feasibility.INFEASIBLE


def test_large_file_with_a_small_editable_member_the_goal_names_is_not_refused():
    content = _big_test_file(400)
    goal = "Make case7() assert the current label."
    result = _assess(content, room=10, goal=goal)
    assert result.outcome is Feasibility.UNKNOWN
    assert result.evidence["members"]


def test_code_the_goal_quotes_gives_exact_windows_and_is_feasible():
    content = _big_test_file(400)
    goal = "Change `assertEquals(7, Widgets.value(7));` so it expects the new value."
    result = _assess(content, room=2000, goal=goal)
    assert result.outcome is Feasibility.FEASIBLE
    assert result.evidence["loci"]


def test_quoted_code_whose_windows_exceed_the_budget_is_unknown_not_refused():
    content = _big_test_file(400)
    goal = "Change `assertEquals(7, Widgets.value(7));` so it expects the new value."
    assert _assess(content, room=1, goal=goal).outcome is Feasibility.UNKNOWN


def test_a_new_member_the_goal_quotes_gets_a_structural_insertion_locus_and_is_feasible():
    content = _big_test_file(400)
    goal = "Add `public void brandNewCheck() { }` to WidgetTest so the label case is covered."
    # the insertion locus reads the user's own goal (grounding_goal), not the planner's unit text
    result = assess_planned_edit(
        subtask_id="s2", path=_PATH, content=content, unit_goal="Cover the label case in the tests.",
        grounding_goal=goal, capacities=_caps(2000), grounding=_NO_GROUNDING)
    assert result.outcome is Feasibility.FEASIBLE
    assert "insertion" in result.reason
    # the executor's own resolver agrees on the same bytes
    from kriya.workflow.edit_safety import content_revision
    from kriya.workflow.insertion_locus import resolve_insertion_locus

    locus, _why = resolve_insertion_locus(_PATH, content, goal=goal, revision=content_revision(content))
    assert locus is not None


def test_minimum_editable_member_too_large_for_every_room_still_requires_the_other_sources_to_be_absent():
    # a one-method class: the minimum unit IS the whole file, nothing smaller exists
    content = "package pkg;\n\npublic class Huge {\n    void m() {\n" + "        step();\n" * 4000 + "    }\n}\n"
    result = _assess(content, room=100, path="src/main/java/pkg/Huge.java", goal="Make the runner faster.")
    assert result.outcome is Feasibility.INFEASIBLE


def test_t2_shaped_reproducer_carries_no_task_or_repository_identity():
    # A large test file edited by an unrequested "add tests" unit: no goal-named member, no quoted code.
    goal = "Rendering drops the suffix of a label when it is renamed. Keep the suffix."
    unit_goal = goal + "\n\nAdd unit tests for the specific bug cases."
    content = _big_test_file(500)
    result = _assess(content, room=estimate_tokens(content) // 5, goal=unit_goal)
    assert result.outcome is Feasibility.INFEASIBLE
    text = repair_error_text(result)
    assert _PATH in text and str(result.evidence["required_capacity_tokens"]) in text
    # Kriya states what it knows; it never names a file to choose or an action to take.
    assert "create" not in text.lower() and "instead" not in text.lower()


# --- the decision agrees with the executor's own allocator and capability -------------------------------

@pytest.mark.parametrize("delta", [-3, -1, 0, 1, 3])
def test_preflight_and_the_real_known_target_allocator_agree_around_the_room(tmp_path, delta):
    content = _big_test_file(60)
    room = estimate_tokens(content) + delta
    target = tmp_path / _PATH
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    _rendered, package = build_known_target_context(
        [_PATH], str(tmp_path), None, room, exact_member_budget=room)
    runtime_fits_whole = any(i.path == _PATH and i.tier == "full" and i.is_exact for i in package.relevant_files)
    runtime_unfit = any(e.get("reason") == "minimum_authority_unfit" for e in package.omitted)

    preflight = _assess(content, room=room)
    assert runtime_fits_whole == (preflight.outcome is Feasibility.FEASIBLE)
    assert runtime_unfit == (preflight.outcome is Feasibility.INFEASIBLE)
    if preflight.outcome is Feasibility.INFEASIBLE:
        # and the executor's capability, with no locus and no insertion, offers no operation
        assert not build_edit_capability(_PATH, content, full_file=False, loci=[], budget_chars=room * 4).feasible


def test_quoted_code_feasibility_matches_the_real_edit_capability():
    content = _big_test_file(200)
    goal = "Change `assertEquals(7, Widgets.value(7));` so it expects the new value."
    result = _assess(content, room=2000, goal=goal)
    capability = build_edit_capability(_PATH, content, full_file=False, loci=result.evidence["loci"],
                                       budget_chars=ef.exact_window_reserve(2000) * 4)
    assert capability.feasible and result.outcome is Feasibility.FEASIBLE


def test_model_capacities_are_the_primary_binding_only_because_attempt_one_never_starts_on_a_fallback():
    from kriya.workflow.context_budget import allocation_window

    config = strict_config(
        llm={"model": "primary-m", "context_window": 16384},
        llm_chain=[{"model": "fallback-m", "context_window": 65536}],
    )
    capacities = model_capacities(config)
    assert [c.model for c in capacities] == ["primary-m"]
    assert capacities[0].room_tokens == _reserve_graph_context_budget(allocation_window(config, config.llm))
    # a larger fallback must not rescue a unit whose first invocation runs on the primary
    big = allocation_window(config, config.llm_chain[0])
    assert _reserve_graph_context_budget(big) > capacities[0].room_tokens


# --- the plan-level evaluation --------------------------------------------------------------------------

def _subtask(sid, path, action=FileAction.MODIFY, *, role=ExecutionRole.IMPLEMENTATION, depends_on=()):
    verification = [VerificationMethod(
        type=VerificationMethodType.JUDGMENT, description="the work is verified")] if role == ExecutionRole.VERIFICATION else []
    return Subtask(
        id=sid, description=f"work on {path}", execution_method=ExecutionMethod.MODEL, execution_role=role,
        depends_on=list(depends_on), verification=verification,
        planned_files=[PlannedFile(path=path, action=action)] if path else [])


def _plan(*subtasks):
    return EngineeringPlan(plan_id="p", kind="task", subtasks=list(subtasks))


def _seed(tmp_path, rel, content):
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_plan_with_one_impossible_unit_among_feasible_ones(tmp_path):
    _seed(tmp_path, "src/main/java/pkg/Widget.java", "package pkg;\npublic class Widget { void render() {} }\n")
    _seed(tmp_path, _PATH, _big_test_file(500))
    plan = _plan(
        _subtask("s1", "src/main/java/pkg/Widget.java"),
        _subtask("s2", _PATH, depends_on=["s1"]),
        _subtask("s3", None, role=ExecutionRole.VERIFICATION, depends_on=["s1"]),
    )
    grounding = AsyncMock(return_value=UnitGrounding())
    with patch.object(ef, "model_capacities", return_value=_caps(800)), \
            patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(800)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding", new=grounding):
        results = await evaluate_plan_edit_feasibility(strict_config(), plan, _GOAL, str(tmp_path))

    outcomes = {(r.subtask_id, r.path): r.outcome for r in results}
    assert outcomes == {("s1", "src/main/java/pkg/Widget.java"): Feasibility.FEASIBLE,
                        ("s2", _PATH): Feasibility.INFEASIBLE}
    # the unit retrieval ran for the one file whose whole source cannot fit, and only for it
    grounding.assert_awaited_once()
    unit_goal = grounding.await_args.args[2]
    assert "work on " + _PATH in unit_goal and "'s2' (2 of 3)" in unit_goal


@pytest.mark.asyncio
async def test_create_actions_and_missing_or_unreadable_files_are_not_refused(tmp_path):
    _seed(tmp_path, "blob.bin", "x")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00bad" * 100)
    plan = _plan(
        _subtask("s1", "src/test/java/pkg/NewTest.java", FileAction.CREATE),
        _subtask("s2", "blob.bin", depends_on=["s1"]),
        _subtask("s3", "src/test/java/pkg/Missing.java", depends_on=["s2"]),
    )
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(10)):
        results = await evaluate_plan_edit_feasibility(strict_config(), plan, _GOAL, str(tmp_path))
    assert [(r.subtask_id, r.outcome) for r in results] == [("s2", Feasibility.UNKNOWN)]


@pytest.mark.asyncio
async def test_evaluation_never_changes_the_plan_or_its_authority(tmp_path):
    _seed(tmp_path, _PATH, _big_test_file(500))
    plan = _plan(_subtask("s2", _PATH))
    before = plan.model_dump_json()
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding",
                  new=AsyncMock(return_value=UnitGrounding())):
        results = await evaluate_plan_edit_feasibility(strict_config(), plan, _GOAL, str(tmp_path))
    assert infeasible_results(results)
    assert plan.model_dump_json() == before
    assert [pf.path for st in plan.subtasks for pf in st.planned_files] == [_PATH]


@pytest.mark.asyncio
async def test_a_bare_engine_without_kernel_config_decides_nothing(tmp_path):
    _seed(tmp_path, _PATH, _big_test_file(500))
    assert await evaluate_plan_edit_feasibility(None, _plan(_subtask("s2", _PATH)), _GOAL, str(tmp_path)) == []


# --- the planner-repair contract ---------------------------------------------------------------------------

def test_repair_prompt_states_the_contradiction_without_choosing_for_the_planner():
    result = _assess(_big_test_file(500), room=300)
    prompt = build_structured_plan_repair_prompt(
        _GOAL, "previous plan", [repair_error_text(result)], ["PLANNED_EDIT_INFEASIBLE"], 1)
    assert "PLANNED_EDIT_INFEASIBLE" in prompt
    assert repair_error_text(result) in prompt
    assert "Do not keep the same planned edit unchanged" in prompt
    assert "the choice of how to revise it is yours" in prompt
    assert "drop" not in prompt.split("PLANNED_EDIT_INFEASIBLE", 1)[1].split("MISSING_GROUNDED", 1)[0].lower()


# --- the controller loop: existing bounded repair, nothing added -----------------------------------------------

def _loop_engine():
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=_route())
    we.kernel = None
    return we


def _route():
    from test_workflow_controller_enforce import _route as route

    return route()


def _infeasible_result():
    return [EditFeasibility("s2", _PATH, Feasibility.INFEASIBLE, "no region",
                            {"required_capacity_tokens": 35994, "available_capacity_tokens": 5262})]


def _feasible_result():
    return [EditFeasibility("s2", _PATH, Feasibility.FEASIBLE, "fits")]


def _patched_plans(*plans):
    from kriya.workflow.plan_validation import PlanValidationResult

    return (
        patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
              return_value=(MagicMock(), None)),
        patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
              side_effect=list(plans)),
        patch("kriya.workflow.workflow_controller.validate_plan",
              new=AsyncMock(return_value=PlanValidationResult(valid=True))),
    )


@pytest.mark.asyncio
async def test_infeasible_plan_enters_existing_repair_and_no_work_runs_before_it(tmp_path):
    bad = _plan(_subtask("s1", "a.py"), _subtask("s2", _PATH, depends_on=["s1"]))
    good = _plan(_subtask("s1", "a.py"), _subtask("s2", "tests/test_new.py", FileAction.CREATE, depends_on=["s1"]))
    we = _loop_engine()
    we.planner.run = AsyncMock(side_effect=["plan one", "plan two"])
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True,
                                                         "files": ["a.py"]})
    feasibility = AsyncMock(side_effect=[_infeasible_result(), _feasible_result()])
    p1, p2, p3 = _patched_plans(bad, good)
    with p1, p2, p3, patch("kriya.workflow.workflow_controller.evaluate_plan_edit_feasibility", new=feasibility):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    assert feasibility.await_count == 2                      # evaluated again on the repaired plan
    assert we.planner.run.await_count == 2                   # one repair round; the budget is not extended
    repair_prompt = we.planner.run.await_args_list[1].args[0]
    assert "PLANNED_EDIT_INFEASIBLE" in repair_prompt
    assert "cannot be executed under the currently authorized edit protocol" in repair_prompt
    assert result.legacy_result["plan_repair_attempts"] == 1
    # the repaired plan, and only it, executed; the infeasible draft never reached the Developer
    executed_goals = [str(call) for call in we.run_generation_workflow.await_args_list]
    assert executed_goals and all(_PATH not in goal_text for goal_text in executed_goals)


@pytest.mark.asyncio
async def test_repair_that_stays_infeasible_exhausts_the_unchanged_budget_before_any_execution(tmp_path):
    bad = _plan(_subtask("s2", _PATH))
    we = _loop_engine()
    we.planner.run = AsyncMock(return_value="plan")
    we.run_generation_workflow = AsyncMock()
    feasibility = AsyncMock(return_value=_infeasible_result())
    p1, p2, p3 = _patched_plans(bad, bad, bad)
    with p1, p2, p3, patch("kriya.workflow.workflow_controller.evaluate_plan_edit_feasibility", new=feasibility):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "needs_review"
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]
    assert "PLANNED_EDIT_INFEASIBLE" in result.legacy_result["reason_codes"]
    assert result.legacy_result["plan_repair_attempts"] == 2
    assert we.planner.run.await_count == 3                   # initial + the same two repairs as ever
    we.run_generation_workflow.assert_not_awaited()          # no model execution happened


@pytest.mark.asyncio
async def test_unknown_and_feasible_results_never_reach_the_planner(tmp_path):
    plan = _plan(_subtask("s2", _PATH))
    we = _loop_engine()
    we.planner.run = AsyncMock(return_value="plan")
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True,
                                                         "files": [_PATH]})
    unknown = [EditFeasibility("s2", _PATH, Feasibility.UNKNOWN, "retrieval grounds this file")]
    p1, p2, p3 = _patched_plans(plan)
    with p1, p2, p3, patch("kriya.workflow.workflow_controller.evaluate_plan_edit_feasibility",
                           new=AsyncMock(return_value=unknown)):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    assert we.planner.run.await_count == 1 and result.legacy_result["plan_repair_attempts"] == 0
    we.run_generation_workflow.assert_awaited()


def test_the_reason_code_is_registered_and_the_results_serialize():
    from kriya.workflow.planner_repair import PLANNER_POLICY_REJECTION_CODES

    assert ef.PLANNED_EDIT_INFEASIBLE in PLANNER_POLICY_REJECTION_CODES
    json.dumps([r.to_dict() for r in _infeasible_result()])


# --- the unit-retrieval collector: it decides only what the unit's own run would decide ---------------------

_LIMITS = "RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30)"


def test_the_collector_uses_the_same_retrieval_limits_as_the_unit_run():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "kriya" / "workflow"
    assert _LIMITS in (root / "workflow.py").read_text(encoding="utf-8")
    assert _LIMITS in (root / "edit_feasibility.py").read_text(encoding="utf-8")


def test_grounded_paths_covers_every_way_a_retrieval_can_ground_a_file():
    retrieval = MagicMock()
    retrieval.retrieval_member_hints = {"a": ["m"]}
    retrieval.retrieval_symbol_loci = {"b": [(1, "d")]}
    retrieval.verified_grounding = {"c": ["m"]}
    retrieval.hypothesis_candidates = {"d": ["m"]}
    retrieval.localization = [MagicMock(path="e")]
    assert ef.grounded_paths(retrieval) == frozenset("abcde")


@pytest.mark.asyncio
async def test_collector_without_a_vector_index_grounds_nothing_like_the_unit_run(tmp_path):
    config = strict_config(paths={"skills": str(tmp_path / "s"), "memory": str(tmp_path / "m")})
    grounding = await ef.collect_unit_grounding(config, str(tmp_path), "goal")
    assert grounding == UnitGrounding() and grounding.paths == frozenset()


@pytest.mark.asyncio
async def test_collector_is_unknown_under_a_process_profile_retrieval_depth(tmp_path):
    config = strict_config(process_profiles={"enabled": True, "enforce_context_depth": True},
                           paths={"skills": str(tmp_path / "s"), "memory": str(tmp_path / "m")})
    assert await ef.collect_unit_grounding(config, str(tmp_path), "goal") is None


def _indexed_config(tmp_path):
    memory = tmp_path / "m"
    memory.mkdir(exist_ok=True)
    (memory / "vector_index.db").write_bytes(b"")
    return strict_config(paths={"skills": str(tmp_path / "s"), "memory": str(memory)})


@pytest.mark.asyncio
async def test_collector_returns_the_grounded_paths_and_closes_the_store(tmp_path):
    retrieval = MagicMock()
    retrieval.semantic_unavailable = None
    retrieval.retrieval_member_hints = {"x.java": ["m"]}
    retrieval.retrieval_symbol_loci = {}
    retrieval.verified_grounding = {}
    retrieval.hypothesis_candidates = {}
    retrieval.localization = []
    store = MagicMock()
    with patch("kriya.memory.vector.LocalVectorStore", return_value=store), \
            patch("kriya.memory.embedding.configured_client", return_value=MagicMock()), \
            patch("kriya.workflow.graph_retrieval.retrieve_graph_context", new=AsyncMock(return_value=retrieval)) as call:
        grounding = await ef.collect_unit_grounding(_indexed_config(tmp_path), str(tmp_path), "unit goal")
    assert grounding == UnitGrounding(frozenset({"x.java"}))
    assert call.await_args.args[0] == "unit goal"
    store.close.assert_called_once()


@pytest.mark.asyncio
async def test_collector_failure_or_degraded_semantic_leg_is_unknown_never_a_rejection(tmp_path):
    store = MagicMock()
    with patch("kriya.memory.vector.LocalVectorStore", return_value=store), \
            patch("kriya.memory.embedding.configured_client", return_value=MagicMock()), \
            patch("kriya.workflow.graph_retrieval.retrieve_graph_context",
                  new=AsyncMock(side_effect=RuntimeError("embedding endpoint down"))):
        assert await ef.collect_unit_grounding(_indexed_config(tmp_path), str(tmp_path), "g") is None
    store.close.assert_called_once()

    degraded = MagicMock()
    degraded.semantic_unavailable = "EMBEDDING_UNAVAILABLE"
    with patch("kriya.memory.vector.LocalVectorStore", return_value=MagicMock()), \
            patch("kriya.memory.embedding.configured_client", return_value=MagicMock()), \
            patch("kriya.workflow.graph_retrieval.retrieve_graph_context", new=AsyncMock(return_value=degraded)):
        assert await ef.collect_unit_grounding(_indexed_config(tmp_path), str(tmp_path), "g") is None


# --- soundness: bytes that differ at run time, unit kinds, failure to decide ----------------------------------

@pytest.mark.asyncio
async def test_a_file_an_earlier_unit_also_edits_is_unknown_because_the_unit_sees_other_bytes(tmp_path):
    # s1 adds the member s2's goal names; at plan time that member does not exist in the file
    _seed(tmp_path, _PATH, _big_test_file(500))
    first = _subtask("s1", _PATH)
    second = _subtask("s2", _PATH, depends_on=["s1"])
    second.description = "Use `Widget.renderLabel()` in the tests"
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding",
                  new=AsyncMock(return_value=UnitGrounding())):
        results = await evaluate_plan_edit_feasibility(strict_config(), _plan(first, second), _GOAL, str(tmp_path))
    by_unit = {r.subtask_id: r for r in results}
    assert by_unit["s1"].outcome is Feasibility.INFEASIBLE        # the first edit of the file is decided
    assert by_unit["s2"].outcome is Feasibility.UNKNOWN           # the later one is not
    assert "earlier unit" in by_unit["s2"].reason


@pytest.mark.asyncio
async def test_units_run_in_topological_order_and_the_unit_goal_carries_that_position(tmp_path):
    _seed(tmp_path, _PATH, _big_test_file(500))
    _seed(tmp_path, "src/main/java/pkg/Widget.java", "package pkg;\npublic class Widget { }\n")
    # listed s2 first, but s2 depends on s1: s1 runs first
    plan = _plan(_subtask("s2", _PATH, depends_on=["s1"]), _subtask("s1", "src/main/java/pkg/Widget.java"))
    grounding = AsyncMock(return_value=UnitGrounding())
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding", new=grounding):
        results = await evaluate_plan_edit_feasibility(strict_config(), plan, _GOAL, str(tmp_path))
    assert [r.subtask_id for r in results] == ["s1", "s2"]
    assert "'s2' (2 of 2)" in grounding.await_args.args[2]


@pytest.mark.asyncio
async def test_only_model_implementation_units_modifying_an_existing_file_are_evaluated(tmp_path):
    _seed(tmp_path, _PATH, _big_test_file(500))
    created = _subtask("s2", _PATH, FileAction.CREATE)          # the path exists: still not a modify
    tool = _subtask("s3", _PATH, depends_on=["s2"])
    tool.execution_method = ExecutionMethod.TOOL
    tool.tool_name = "formatter"
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding",
                  new=AsyncMock(return_value=UnitGrounding())):
        results = await evaluate_plan_edit_feasibility(strict_config(), _plan(created, tool), _GOAL,
                                                       str(tmp_path))
    assert results == []


@pytest.mark.asyncio
async def test_two_oversized_files_of_one_unit_run_the_unit_retrieval_once(tmp_path):
    other = "src/test/java/pkg/OtherTest.java"
    _seed(tmp_path, _PATH, _big_test_file(500))
    _seed(tmp_path, other, _big_test_file(400))
    unit = _subtask("s1", _PATH)
    unit.planned_files.append(PlannedFile(path=other, action=FileAction.MODIFY))
    grounding = AsyncMock(return_value=UnitGrounding())
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding", new=grounding):
        results = await evaluate_plan_edit_feasibility(strict_config(), _plan(unit), _GOAL, str(tmp_path))
    assert [r.outcome for r in results] == [Feasibility.INFEASIBLE, Feasibility.INFEASIBLE]
    grounding.assert_awaited_once()


def test_the_window_budget_is_shared_between_the_files_of_one_unit():
    content = _big_test_file(400)
    goal = "Change `assertEquals(7, Widgets.value(7));` so it expects the new value."
    room = 400
    assert _assess(content, room=room, goal=goal, unit_files=1).outcome is Feasibility.FEASIBLE
    assert _assess(content, room=room, goal=goal, unit_files=40).outcome is Feasibility.UNKNOWN


@pytest.mark.asyncio
async def test_a_failure_while_deciding_is_unknown_and_never_aborts_the_plan_loop(tmp_path):
    _seed(tmp_path, _PATH, _big_test_file(500))
    plan = _plan(_subtask("s2", _PATH))
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.assess_planned_edit", side_effect=RuntimeError("boom")):
        results = await evaluate_plan_edit_feasibility(strict_config(), plan, _GOAL, str(tmp_path))
    assert [(r.subtask_id, r.outcome) for r in results] == [("s2", Feasibility.UNKNOWN)]
    assert "RuntimeError" in results[0].reason
    # a model-capacity failure degrades every file to UNKNOWN (no capacity to decide with)
    with patch("kriya.workflow.workflow_controller.model_capacities", side_effect=RuntimeError("probe")):
        results = await evaluate_plan_edit_feasibility(strict_config(), plan, _GOAL, str(tmp_path))
    assert [r.outcome for r in results] == [Feasibility.UNKNOWN]


@pytest.mark.asyncio
async def test_the_loop_gives_the_real_evaluator_the_goal_the_plan_the_workspace_and_the_kernel_config(tmp_path):
    from _strict_doubles import strict_kernel

    _seed(tmp_path, _PATH, _big_test_file(500))
    bad = _plan(_subtask("s2", _PATH))
    good = _plan(_subtask("s2", "tests/test_new.py", FileAction.CREATE))
    goal = "unique-user-goal-text for the feasibility wiring"
    we = _loop_engine()
    we.kernel = strict_kernel(strict_config(paths={"skills": str(tmp_path / "s"), "memory": str(tmp_path / "m")}))
    we.planner.run = AsyncMock(side_effect=["plan one", "plan two"])
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True,
                                                         "files": ["tests/test_new.py"]})
    grounding = AsyncMock(return_value=UnitGrounding())
    p1, p2, p3 = _patched_plans(bad, good)
    with p1, p2, p3, patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding", new=grounding):
        result = await WorkflowController(we).execute(goal, str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["plan_repair_attempts"] == 1
    assert goal in grounding.await_args.args[2]                       # the unit goal embeds the user's goal
    assert grounding.await_args.args[1] == str(tmp_path)              # the workspace the file was read from
    assert grounding.await_args.args[0] is we.kernel.config          # the kernel's config
    assert "PLANNED_EDIT_INFEASIBLE" in we.planner.run.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_a_failed_unit_retrieval_is_not_retried_for_each_file_of_the_unit(tmp_path):
    other = "src/test/java/pkg/OtherTest.java"
    _seed(tmp_path, _PATH, _big_test_file(500))
    _seed(tmp_path, other, _big_test_file(400))
    unit = _subtask("s1", _PATH)
    unit.planned_files.append(PlannedFile(path=other, action=FileAction.MODIFY))
    grounding = AsyncMock(return_value=None)                   # the retrieval failed: UNKNOWN for both files
    with patch("kriya.workflow.workflow_controller.model_capacities", return_value=_caps(50)), \
            patch("kriya.workflow.workflow_controller.collect_unit_grounding", new=grounding):
        results = await evaluate_plan_edit_feasibility(strict_config(), _plan(unit), _GOAL, str(tmp_path))
    assert [r.outcome for r in results] == [Feasibility.UNKNOWN, Feasibility.UNKNOWN]
    grounding.assert_awaited_once()
