"""Planner Reliability R1, PR-1: one owning work unit per mutable file.

Live (more-itertools, production config): the Planner split one change by
symbol into unordered implementation units writing the same file - "fix
one()" and "fix only()", four split_* units for maxsplit. The validator's
AMBIGUOUS_PLANNED_FILE_OWNERSHIP repair never converged (the identical plan
three times; again with an explicit merge instruction) and both runs ended in
planning. The fixtures are those exact parsed plans.
"""
import asyncio
import json
import os

from kriya.workflow.plan_normalization import COALESCED_FILE_OWNERS, coalesce_same_file_owners
from kriya.workflow.plan_schema import EngineeringPlan, IntegrationRelationshipKind
from kriya.workflow.plan_validation import validate_plan

KIND = next(iter(IntegrationRelationshipKind)).value
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "planner_live")


def _live(name):
    with open(os.path.join(FIXTURES, f"{name}.json")) as handle:
        return EngineeringPlan.model_validate(json.load(handle))


def _workspace(tmp_path):
    (tmp_path / "more_itertools").mkdir()
    (tmp_path / "more_itertools/more.py").write_text("def one(iterable):\n    return iterable\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_more.py").write_text("def test_x():\n    pass\n")
    return str(tmp_path)


def _validate(plan, workspace):
    return asyncio.run(validate_plan(plan, workspace_path=workspace, require_model_planned_files=True,
                                     require_semantic_contracts=True))


def _shape(plan):
    return [(st.id, [pf.path for pf in st.planned_files], st.depends_on) for st in plan.subtasks]


def test_the_live_one_only_plan_gets_one_owner_and_validates(tmp_path):
    workspace = _workspace(tmp_path)
    plan = _live("more_itertools_one_only")
    assert "AMBIGUOUS_PLANNED_FILE_OWNERSHIP" in _validate(plan, workspace).reason_codes
    normalized, records = coalesce_same_file_owners(plan)
    assert _shape(normalized) == [("s1", ["more_itertools/more.py"], []), ("s3", [], ["s1"])]
    assert records == [{"reason_code": COALESCED_FILE_OWNERS, "path": "more_itertools/more.py",
                        "kept": "s1", "absorbed": "s2"}]
    assert _validate(normalized, workspace).reason_codes == []
    owner = normalized.subtask_by_id("s1")
    # Nothing of the absorbed unit is dropped.
    absorbed = plan.subtask_by_id("s2")
    assert absorbed.description in owner.description
    assert set(absorbed.provides) <= set(owner.provides)
    assert all(v in owner.verification for v in absorbed.verification)


def test_the_live_maxsplit_plan_merges_every_same_file_unit_and_rewires_its_dependents(tmp_path):
    workspace = _workspace(tmp_path)
    plan = _live("more_itertools_maxsplit")
    normalized, records = coalesce_same_file_owners(plan)
    assert [r["absorbed"] for r in records] == ["s2", "s3", "s4"]
    assert _shape(normalized) == [("s1", ["more_itertools/more.py"], []),
                                  ("s5", ["tests/test_more.py"], ["s1"]), ("s6", [], ["s1", "s5"])]
    assert "AMBIGUOUS_PLANNED_FILE_OWNERSHIP" not in _validate(normalized, workspace).reason_codes
    for field in ("acceptance_criteria_ids", "requirement_ids", "relevant_global_invariant_ids"):
        assert set().union(*(getattr(st, field) for st in plan.subtasks if st.id in {"s1", "s2", "s3", "s4"})) \
            <= set(getattr(normalized.subtask_by_id("s1"), field))


def _plan(*subtasks, relationships=()):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "integration_relationships": list(relationships),
        "subtasks": [{"id": sid, "description": f"do {sid}", "execution_method": "model", "depends_on": deps,
                      "planned_files": [{"path": p, "action": "modify"} for p in paths], "provides": [f"cap_{sid}"],
                      "verification": [{"type": "tool", "tool_name": "test", "verifier_kind": "test",
                                        "description": "t", "requires_runtime_execution": False}]}
                     for sid, paths, deps in subtasks]})


def test_a_sequential_ownership_chain_is_kept_as_is():
    plan = _plan(("a", ["X.java"], []), ("b", ["X.java"], ["a"]))
    assert coalesce_same_file_owners(plan) == (plan, [])


def test_dependents_and_integration_relationships_follow_the_absorbed_unit():
    plan = _plan(("a", ["X.java"], []), ("b", ["X.java"], []), ("c", ["Y.java"], ["b"]),
                 relationships=[{"id": "r1", "kind": KIND, "relationship_statement": "x", "producer_subtask_ids": ["b"],
                                 "consumer_subtask_ids": ["c"], "participating_artifacts": ["X.java", "Y.java"]}])
    normalized, records = coalesce_same_file_owners(plan)
    assert [r["absorbed"] for r in records] == ["b"]
    assert normalized.subtask_by_id("c").depends_on == ["a"]
    assert normalized.integration_relationships[0].producer_subtask_ids == ["a"]
    assert normalized.subtask_by_id("a").provides == ["cap_a", "cap_b"]


def test_a_requirement_one_unit_provides_the_other_becomes_self_provided():
    plan = _plan(("a", ["X.java"], []), ("b", ["X.java", "Z.java"], []))
    plan = EngineeringPlan.model_validate({**plan.model_dump(mode="json"), "subtasks": [
        {**plan.subtasks[0].model_dump(mode="json"), "requires": ["cap_b"]}, plan.subtasks[1].model_dump(mode="json")]})
    merged = coalesce_same_file_owners(plan)[0].subtask_by_id("a")
    assert merged.requires == [] and set(merged.provides) == {"cap_a", "cap_b"}
    assert [pf.path for pf in merged.planned_files] == ["X.java", "Z.java"]


def test_verification_and_tool_units_are_never_competing_owners():
    plan = EngineeringPlan.model_validate({"plan_id": "p", "kind": "task", "subtasks": [
        {"id": "a", "description": "edit", "execution_method": "model",
         "planned_files": [{"path": "X.java", "action": "modify"}]},
        {"id": "v", "description": "verify", "execution_method": "model", "execution_role": "verification",
         "depends_on": ["a"], "verification": [{"type": "tool", "tool_name": "test", "verifier_kind": "test",
                                                "description": "t", "requires_runtime_execution": False}]},
        {"id": "t", "description": "tool", "execution_method": "tool", "tool_name": "shell",
         "planned_files": [{"path": "X.java", "action": "modify"}]}]})
    assert coalesce_same_file_owners(plan) == (plan, [])


def test_the_enforce_controller_runs_the_live_split_plan_with_one_owner_and_no_repair(tmp_path):
    """Through the real WorkflowController enforce path and the real
    validate_plan: the Planner returns the exact live one()/only() plan;
    before, validation failed and the repair never converged."""
    import subprocess
    from unittest.mock import AsyncMock, patch

    from test_workflow_controller import _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

    (tmp_path / "ws").mkdir()
    workspace = _workspace(tmp_path / "ws")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)
    engine = _workflow_engine()
    engine.planner.run = AsyncMock(return_value="structured plan")
    engine.engineering_triage.recompute_from_files = AsyncMock(side_effect=lambda route, **_: route)
    calls = []

    async def generation(*args, **kwargs):
        calls.append(sorted(kwargs.get("allowed_write_relpaths") or []))
        return {"status": "success", "quality_gates_passed": True, "files": []}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               return_value=_live("more_itertools_one_only")):
        asyncio.run(WorkflowController(engine).execute(
            "Fix one()/only() dropping a falsy user-supplied exception", workspace, migration_mode="enforce"))
    assert engine.planner.run.await_count == 1  # no repair round
    decisions = [json.loads(line) for line in open(os.path.join(workspace, ".kriya/control/decisions.jsonl"))]
    [normalized] = [d for d in decisions if d.get("type") == "structured_plan_normalized"]
    assert normalized["merges"][0]["absorbed"] == "s2"
    assert [d["valid"] for d in decisions if d.get("type") == "structured_plan_validation"] == [True]
    assert ["more_itertools/more.py"] in calls and calls.count(["more_itertools/more.py"]) == 1
