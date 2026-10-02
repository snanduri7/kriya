"""Planner Reliability R1: a verification unit's semantic contract is what
its own depends_on consumes.

Live (spring-framework-petclinic pet types cache, Planner R1 replay at
1354bf6): every implementation unit was well-formed, the terminal
verification unit owned no file, depended on all of them and declared
neither provides nor requires. SUBTASK_SEMANTIC_CONTRACT_MISSING's repair
returned the identical plan three times and the run ended in planning (the
same shape in 5 plans of 3 runs, both Spring tasks). The fixture is that
exact parsed plan.
"""
import asyncio
import json
import os
import subprocess

from kriya.workflow.plan_normalization import VERIFICATION_CONTRACT_DERIVED, derive_verification_contracts
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.plan_validation import validate_plan

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "planner_live", "petclinic_xml_pettypes.json")


def _live():
    with open(FIXTURE) as handle:
        return EngineeringPlan.model_validate(json.load(handle))


def _workspace(root):
    for st in _live().subtasks:
        for pf in st.planned_files:
            path = root / pf.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("package x;\nclass X {}\n")
    return str(root)


def _validate(plan, workspace):
    return asyncio.run(validate_plan(plan, workspace_path=workspace, require_model_planned_files=True,
                                     require_semantic_contracts=True))


def test_the_live_plan_gets_its_verification_contract_and_validates(tmp_path):
    workspace = _workspace(tmp_path)
    plan = _live()
    assert _validate(plan, workspace).reason_codes == ["SUBTASK_SEMANTIC_CONTRACT_MISSING"]
    normalized, records = derive_verification_contracts(plan)
    assert records == [{"reason_code": VERIFICATION_CONTRACT_DERIVED, "subtask": "s4",
                        "requires": ["petTypeCache"], "from_depends_on": ["s1", "s2", "s3"]}]
    assert normalized.subtask_by_id("s4").requires == ["petTypeCache"]
    assert _validate(normalized, workspace).reason_codes == []
    # Nothing else changes.
    assert [st.model_dump() for st in normalized.subtasks if st.id != "s4"] == \
           [st.model_dump() for st in plan.subtasks if st.id != "s4"]


def _plan(*subtasks):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "subtasks": [{"execution_method": "model", "description": st["id"],
                      "verification": [{"type": "tool", "tool_name": "test", "verifier_kind": "test",
                                        "description": "run the tests"}],
                      **{**st, "planned_files": [{"action": "modify", **pf} for pf in st.get("planned_files", [])]}}
                     for st in subtasks]})


def test_no_contract_is_invented_when_the_dependencies_provide_nothing_or_there_are_none():
    plan = _plan({"id": "s1", "execution_role": "implementation", "planned_files": [{"path": "a.py"}]},
                 {"id": "s2", "execution_role": "verification", "depends_on": ["s1"]},
                 {"id": "s3", "execution_role": "verification"})
    assert derive_verification_contracts(plan) == (plan, [])


def test_only_verification_units_without_a_stated_contract_are_derived():
    plan = _plan({"id": "s1", "execution_role": "implementation", "planned_files": [{"path": "a.py"}],
                  "provides": ["a", "b"]},
                 {"id": "s2", "execution_role": "implementation", "planned_files": [{"path": "b.py"}],
                  "depends_on": ["s1"]},
                 {"id": "s3", "execution_role": "verification", "depends_on": ["s1"], "provides": ["checked"]},
                 {"id": "s4", "execution_role": "verification", "depends_on": ["s1", "s3"]},
                 {"id": "s5", "execution_role": "verification", "depends_on": ["s1"], "requires": ["a"]})
    normalized, records = derive_verification_contracts(plan)
    assert [r["subtask"] for r in records] == ["s4"]
    assert normalized.subtask_by_id("s4").requires == ["a", "b", "checked"]
    assert normalized.subtask_by_id("s2").requires == []  # an implementation unit is never derived
    assert normalized.subtask_by_id("s3").requires == []  # a stated contract is kept as-is
    assert normalized.subtask_by_id("s5").requires == ["a"]


def test_the_enforce_controller_runs_the_live_plan_without_a_repair_round(tmp_path):
    """Through the real WorkflowController enforce loop and validate_plan."""
    from unittest.mock import AsyncMock, patch

    from test_workflow_controller import _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

    (tmp_path / "ws").mkdir()
    workspace = _workspace(tmp_path / "ws")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)
    engine = _workflow_engine()
    # The live plan names a Code Intelligence target; this workspace has no
    # index (PR-3 is covered by tests/test_planner_target_consistency.py).
    untargeted = EngineeringPlan.model_validate({**_live().model_dump(mode="json"), "subtasks": [
        {**st, "mutation_targets": []} for st in _live().model_dump(mode="json")["subtasks"]]})
    engine.planner.run = AsyncMock(return_value="structured plan")
    engine.engineering_triage.recompute_from_files = AsyncMock(side_effect=lambda route, **_: route)
    engine.run_generation_workflow = AsyncMock(
        return_value={"status": "success", "quality_gates_passed": True, "files": []})
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
               return_value=untargeted):
        asyncio.run(WorkflowController(engine).execute(
            "Cache the pet types the same way the vets are cached", workspace, migration_mode="enforce"))
    assert engine.planner.run.await_count == 1
    decisions = [json.loads(line) for line in open(os.path.join(workspace, ".kriya/control/decisions.jsonl"))]
    [normalized] = [d for d in decisions if d.get("type") == "structured_plan_normalized"]
    assert normalized["derived_contracts"][0]["subtask"] == "s4" and normalized["merges"] == []
    assert [d["valid"] for d in decisions if d.get("type") == "structured_plan_validation"] == [True]
