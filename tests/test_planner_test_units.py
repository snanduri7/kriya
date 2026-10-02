"""Planner Reliability R1, PR-2: test work the goal did not ask for is
verification, not Developer mutation.

Live (spring-petclinic configurable page size, production config, run 6): s1
(application.properties) and s2 (OwnerController) passed every gate including
the full regression; the Planner-added s3 "Update OwnerControllerTests to
verify configurable page size behavior" - no requirement id, the same
judgment criterion as s1/s2, a goal that never mentions tests - then failed on
its own (protocol violations, then NO CHANGE NEEDED) and stopped the run with
nothing applied. The fixture is that exact plan.
"""
import asyncio
import json
import os

from kriya.workflow.plan_normalization import DEMOTED_UNREQUESTED_TEST_UNIT, demote_unrequested_test_units
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionRole, VerifierKind
from kriya.workflow.plan_validation import validate_plan

GOAL = ("Owner search results are always shown five per page. Make the number of owners per page configurable "
        "through an application property named petclinic.owners.page-size, keeping 5 as the default.")
TEST_FILE = "src/test/java/org/springframework/samples/petclinic/owner/OwnerControllerTests.java"


def _live():
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "planner_live", "petclinic_page_size.json")) as h:
        return EngineeringPlan.model_validate(json.load(h))


def test_the_live_unrequested_test_unit_becomes_verification(tmp_path):
    plan, records = demote_unrequested_test_units(_live(), GOAL)
    assert records == [{"reason_code": DEMOTED_UNREQUESTED_TEST_UNIT, "subtask": "s3", "files": [TEST_FILE]}]
    s1, s2, s3 = plan.subtasks
    assert (s1.execution_role, s2.execution_role) == (ExecutionRole.IMPLEMENTATION, ExecutionRole.IMPLEMENTATION)
    assert s3.execution_role == ExecutionRole.VERIFICATION and s3.planned_files == []
    assert s3.depends_on == ["s2"] and [v.verifier_kind for v in s3.verification] == [VerifierKind.TEST]
    for rel in ("src/main/resources/application.properties",
                "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java", TEST_FILE):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")
    result = asyncio.run(validate_plan(plan, workspace_path=str(tmp_path), require_model_planned_files=True,
                                       require_semantic_contracts=True))
    assert result.reason_codes == []


def test_a_goal_that_asks_for_tests_keeps_its_test_mutation_units():
    plan = _live()
    assert demote_unrequested_test_units(plan, GOAL + " Add unit tests for the new property.") == (plan, [])


def _plan(paths, verification=None):
    return EngineeringPlan.model_validate({"plan_id": "p", "kind": "task", "subtasks": [
        {"id": "a", "description": "edit", "execution_method": "model",
         "planned_files": [{"path": p, "action": "modify"} for p in paths],
         "verification": verification or []}]})


def test_a_unit_that_also_changes_source_is_not_test_only_and_stays():
    plan = _plan(["src/main/java/a/A.java", "src/test/java/a/ATest.java"])
    assert demote_unrequested_test_units(plan, "make A faster") == (plan, [])


def test_a_demoted_unit_always_carries_a_test_verifier():
    plan, _ = demote_unrequested_test_units(
        _plan(["tests/test_a.py"], [{"type": "tool", "tool_name": "compile", "verifier_kind": "compile",
                                     "description": "c", "requires_runtime_execution": False}]),
        "make a() faster")
    kinds = [v.verifier_kind for v in plan.subtasks[0].verification]
    assert kinds == [VerifierKind.COMPILE, VerifierKind.TEST]


def test_the_enforce_controller_runs_the_demoted_unit_as_deny_all_verification(tmp_path):
    import subprocess
    from unittest.mock import AsyncMock, patch

    from test_workflow_controller import _workflow_engine

    from kriya.policy.filesystem import WriteScopeMode
    from kriya.workflow.workflow_controller import WorkflowController

    workspace = tmp_path / "ws"
    for rel in ("src/main/resources/application.properties",
                "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java", TEST_FILE):
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text("x\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)
    engine = _workflow_engine()
    engine.planner.run = AsyncMock(return_value="structured plan")
    engine.engineering_triage.recompute_from_files = AsyncMock(side_effect=lambda route, **_: route)
    calls = []

    async def generation(*args, **kwargs):
        allowed = sorted(kwargs.get("allowed_write_relpaths") or [])
        calls.append((kwargs.get("write_scope_mode"), allowed,
                      [v["verifier_kind"] for v in kwargs.get("required_verification") or []]))
        for rel in allowed:  # the unit's own candidate change
            with open(os.path.join(kwargs["workspace_path"], rel), "a", encoding="utf-8") as handle:
                handle.write("changed\n")
        return {"status": "success", "quality_gates_passed": True, "files": allowed,
                "verification_results": [{**v, "passed": True, "source": "authoritative_gate_outcome"}
                                         for v in kwargs.get("required_verification") or []]}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_live()):
        asyncio.run(WorkflowController(engine).execute(GOAL, str(workspace), migration_mode="enforce"))
    assert len(calls) == 3
    assert calls[2] == (WriteScopeMode.DENY_ALL, [], ["test"])  # s3: run the tests, write nothing
    assert all(TEST_FILE not in allowed for _, allowed, _ in calls)
