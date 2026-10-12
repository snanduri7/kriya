"""BACKEND-READINESS-V2, S4_B (also S4_A attempt 0): a plan that edits a test the goal froze.

Live (JsonPath, "Every existing test must keep passing unchanged"): the Planner scheduled DeepScanTest.java (modify) for
a regression test. PLAN_EDITS_IMMUTABLE_TEST named that exact path and said to use a new file; the Planner re-described
the subtask as "create a new test file" and kept the same existing path through both bounded repairs (and regressed another
unit in the second), so nothing was written. The fixture is the recorded first draft (tests/fixtures/planner_live)."""
import asyncio
import json
import os
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from kriya.workflow.plan_normalization import IMMUTABLE_TEST_EDIT_REDIRECTED, redirect_immutable_test_edits
from kriya.workflow.plan_schema import EngineeringPlan, FileAction
from kriya.workflow.plan_validation import validate_plan

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "planner_live")
TEST_PATH = "json-path/src/test/java/com/jayway/jsonpath/DeepScanTest.java"
NEW_PATH = "json-path/src/test/java/com/jayway/jsonpath/DeepScanRegressionTest.java"
GOAL = ("$..book.length() gives 4 the first time and 18 on every later evaluation of the same path\n\n"
        "Every existing test must keep passing unchanged (./gradlew :json-path:test).")


def _plan():
    with open(os.path.join(FIXTURES, "jsonpath_deepscan_edits_immutable_test.json")) as handle:
        return EngineeringPlan.model_validate(json.load(handle))


def _files(plan):
    return [(st.id, pf.path, pf.action.value) for st in plan.subtasks for pf in st.planned_files]


def _validate(plan, workspace, immutable):
    return asyncio.run(validate_plan(plan, workspace_path=str(workspace), require_model_planned_files=True,
                                     require_semantic_contracts=True, immutable_test_files=immutable))


def _workspace(tmp_path, extra=()):
    for rel in (TEST_PATH, "json-path/src/main/java/com/jayway/jsonpath/internal/path/ScanPathToken.java",
                "json-path/src/main/java/com/jayway/jsonpath/internal/function/Parameter.java",
                "json-path/src/test/java/com/jayway/jsonpath/BaseTest.java", *extra):
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("class X {}\n")
    return tmp_path


def test_the_recorded_draft_edits_an_immutable_test_and_the_redirect_removes_exactly_that_violation(tmp_path):
    workspace = _workspace(tmp_path)
    plan = _plan()
    assert ("s1", TEST_PATH, "modify") in _files(plan)
    assert "PLAN_EDITS_IMMUTABLE_TEST" in _validate(plan, workspace, [TEST_PATH]).reason_codes   # the baseline failure
    redirected, records = redirect_immutable_test_edits(plan, [TEST_PATH], lambda rel: (workspace / rel).exists())
    assert records == [{"reason_code": IMMUTABLE_TEST_EDIT_REDIRECTED, "subtask": "s1", "from": TEST_PATH, "to": NEW_PATH}]
    assert ("s1", NEW_PATH, "create") in _files(redirected) and ("s1", TEST_PATH, "modify") not in _files(redirected)
    # everything else about the plan is unchanged: other files, ordering, references, descriptions
    assert [f for f in _files(redirected) if f[0] != "s1"] == [f for f in _files(plan) if f[0] != "s1"]
    assert [(st.id, st.depends_on, st.description) for st in redirected.subtasks] == \
           [(st.id, st.depends_on, st.description) for st in plan.subtasks]
    moved = redirected.subtask_by_id("s1").planned_files[0]
    assert moved.preserved_references == plan.subtask_by_id("s1").planned_files[0].preserved_references
    assert TEST_PATH in moved.reason and "unchanged" in moved.reason
    assert "PLAN_EDITS_IMMUTABLE_TEST" not in _validate(redirected, workspace, [TEST_PATH]).reason_codes


def test_the_redirect_is_not_a_way_around_the_goal_nothing_else_is_touched(tmp_path):
    workspace = _workspace(tmp_path)
    plan = _plan()
    exists = lambda rel: (workspace / rel).exists()  # noqa: E731
    # a test the goal did not freeze is left exactly as planned
    assert redirect_immutable_test_edits(plan, ["other/Frozen.java"], exists) == (plan, [])
    assert redirect_immutable_test_edits(plan, [], exists) == (plan, [])
    # a DELETE of a frozen test is never rewritten: it stays a typed validator violation
    deleting = plan.model_copy(deep=True)
    deleting.subtasks[0].planned_files[0].action = FileAction.DELETE
    assert redirect_immutable_test_edits(deleting, [TEST_PATH], exists) == (deleting, [])
    assert "PLAN_EDITS_IMMUTABLE_TEST" in _validate(deleting, workspace, [TEST_PATH]).reason_codes
    # a CREATE is already compliant
    creating = plan.model_copy(deep=True)
    creating.subtasks[0].planned_files[0].action = FileAction.CREATE
    assert redirect_immutable_test_edits(creating, [TEST_PATH], exists) == (creating, [])


def test_the_new_name_never_collides_and_is_stable_across_units(tmp_path):
    workspace = _workspace(tmp_path, extra=[NEW_PATH])          # the first free name already exists on disk
    plan = _plan()
    exists = lambda rel: (workspace / rel).exists()  # noqa: E731
    redirected, records = redirect_immutable_test_edits(plan, [TEST_PATH], exists)
    assert records[0]["to"] == NEW_PATH.replace("Regression", "Regression2")
    # two units editing the same frozen test are redirected to the same new file (one owner of the new path)
    twin = plan.model_copy(deep=True)
    twin.subtasks[1].planned_files.append(plan.subtasks[0].planned_files[0].model_copy(deep=True))
    twin_out, twin_records = redirect_immutable_test_edits(twin, [TEST_PATH], lambda rel: False)
    assert {r["to"] for r in twin_records} == {NEW_PATH} and len(twin_records) == 2
    assert all(path == NEW_PATH for _, path, _ in _files(twin_out) if path.endswith("RegressionTest.java"))


def test_the_new_name_avoids_a_path_another_unit_already_creates():
    plan = _plan()
    plan.subtasks[1].planned_files.append(plan.subtasks[0].planned_files[0].model_copy(update={"path": NEW_PATH, "action": FileAction.CREATE}))
    _, records = redirect_immutable_test_edits(plan, [TEST_PATH], lambda rel: False)
    assert records[0]["to"] == NEW_PATH.replace("Regression", "Regression2")


@pytest.mark.parametrize("path,expected", [
    ("pkg/FooTest.java", "pkg/FooRegressionTest.java"),
    ("pkg/FooTests.java", "pkg/FooRegressionTests.java"),
    ("pkg/FooIT.kt", "pkg/FooRegressionIT.kt"),
    ("tests/test_more.py", "tests/test_more_regression.py"),
    ("tests/more_test.py", "tests/more_regression_test.py"),
    ("web/foo.test.ts", "web/foo.regression.test.ts"),
    ("web/foo.spec.js", "web/foo.regression.spec.js"),
])
def test_the_new_file_keeps_the_naming_convention_the_runner_collects_by(path, expected, tmp_path):
    workspace = _workspace(tmp_path)
    plan = _plan()
    plan.subtasks[0].planned_files[0].path = path
    redirected, records = redirect_immutable_test_edits(plan, [path], lambda rel: False)
    assert records[0]["to"] == expected and redirected.subtasks[0].planned_files[0].path == expected
    assert workspace  # silence unused


@pytest.mark.parametrize("path", ["pkg/Foo.java", "tests/conftest.py", "pkg/Foo.go", "README.md", "pkg/Test.java"])
def test_an_unknown_convention_is_left_to_the_validator(path):
    plan = _plan()
    plan.subtasks[0].planned_files[0].path = path
    assert redirect_immutable_test_edits(plan, [path], lambda rel: False) == (plan, [])


def test_the_enforce_controller_accepts_the_recorded_draft_without_a_repair_round(tmp_path):
    """Through the real WorkflowController and the real validate_plan, the recorded S4_B first draft: before, validation
    failed with PLAN_EDITS_IMMUTABLE_TEST and the bounded repair (planner.run again) began."""
    from test_workflow_controller import _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

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
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_plan()):
        asyncio.run(WorkflowController(engine).execute(GOAL, str(workspace), migration_mode="enforce"))
    assert engine.planner.run.await_count == 1                                   # no repair round
    decisions = [json.loads(line) for line in open(os.path.join(workspace, ".kriya/control/decisions.jsonl"))]
    [normalized] = [d for d in decisions if d.get("type") == "structured_plan_normalized"]
    assert normalized["immutable_test_redirects"] == [
        {"reason_code": IMMUTABLE_TEST_EDIT_REDIRECTED, "subtask": "s1", "from": TEST_PATH, "to": NEW_PATH}]
    assert [d["valid"] for d in decisions if d.get("type") == "structured_plan_validation"] == [True]
    assert [NEW_PATH] in calls and not any(TEST_PATH in call for call in calls)  # the unit writes the NEW file only


def test_the_enforce_controller_checks_the_real_workspace_for_a_free_name(tmp_path):
    from test_workflow_controller import _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

    workspace = _workspace(tmp_path / "ws", extra=[NEW_PATH])
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)
    engine = _workflow_engine()
    engine.planner.run = AsyncMock(return_value="structured plan")
    engine.engineering_triage.recompute_from_files = AsyncMock(side_effect=lambda route, **_: route)
    engine.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_plan()):
        asyncio.run(WorkflowController(engine).execute(GOAL, str(workspace), migration_mode="enforce"))
    decisions = [json.loads(line) for line in open(os.path.join(workspace, ".kriya/control/decisions.jsonl"))]
    [normalized] = [d for d in decisions if d.get("type") == "structured_plan_normalized"]
    assert normalized["immutable_test_redirects"][0]["to"] == NEW_PATH.replace("Regression", "Regression2")


def test_without_a_frozen_test_the_controller_keeps_the_plan_as_is(tmp_path):
    """Negative control: the same draft under a goal that does not freeze existing tests is not rewritten."""
    from test_workflow_controller import _workflow_engine

    from kriya.workflow.workflow_controller import WorkflowController

    workspace = _workspace(tmp_path / "ws")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)
    engine = _workflow_engine()
    engine.planner.run = AsyncMock(return_value="structured plan")
    engine.engineering_triage.recompute_from_files = AsyncMock(side_effect=lambda route, **_: route)
    engine.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_plan()):
        asyncio.run(WorkflowController(engine).execute(
            "$..book.length() gives 4 the first time and 18 on every later evaluation of the same path",
            str(workspace), migration_mode="enforce"))
    decisions = [json.loads(line) for line in open(os.path.join(workspace, ".kriya/control/decisions.jsonl"))]
    assert not any(d.get("immutable_test_redirects") for d in decisions)
