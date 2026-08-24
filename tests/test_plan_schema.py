"""MA6.1: EngineeringPlan/Subtask schema (kriya/workflow/plan_schema.py) -
first real pytest coverage for this module. No tests existed for any MA6
module before this; see project memory for why."""

import pytest
from pydantic import ValidationError

from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    PlannerStructuredOutput,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    build_engineering_plan_from_planner_output,
)
from kriya.workflow.triage import ChangeKind


def _model_subtask(**overrides):
    defaults = dict(id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL)
    defaults.update(overrides)
    return Subtask(**defaults)


def _tool_subtask(**overrides):
    defaults = dict(id="s1", description="run a tool", execution_method=ExecutionMethod.TOOL, tool_name="lint")
    defaults.update(overrides)
    return Subtask(**defaults)


# --- PlannedFile ---

def test_planned_file_rejects_absolute_path():
    with pytest.raises(ValidationError):
        PlannedFile(path="/etc/passwd", action=FileAction.MODIFY)


def test_planned_file_rejects_path_traversal():
    with pytest.raises(ValidationError):
        PlannedFile(path="a/../../b.py", action=FileAction.CREATE)


def test_planned_file_rejects_blank_path():
    with pytest.raises(ValidationError):
        PlannedFile(path="   ", action=FileAction.CREATE)


def test_planned_file_accepts_a_real_relative_path():
    pf = PlannedFile(path="src/a.py", action=FileAction.MODIFY)
    assert pf.path == "src/a.py"


# --- VerificationMethod ---

def test_verification_method_tool_requires_tool_name():
    with pytest.raises(ValidationError):
        VerificationMethod(type=VerificationMethodType.TOOL, description="compile check")


def test_verification_method_judgment_must_not_set_tool_name():
    with pytest.raises(ValidationError):
        VerificationMethod(type=VerificationMethodType.JUDGMENT, description="looks right", tool_name="pytest")


def test_verification_method_tool_with_tool_name_is_valid():
    vm = VerificationMethod(type=VerificationMethodType.TOOL, description="compile", tool_name="compile_check")
    assert vm.tool_name == "compile_check"


# --- AcceptanceCriterion ---

def test_acceptance_criterion_blank_id_rejected():
    with pytest.raises(ValidationError):
        AcceptanceCriterion(id="  ", description="works")


def test_acceptance_criterion_tool_method_requires_tool_name():
    with pytest.raises(ValidationError):
        AcceptanceCriterion(id="ac1", description="compiles", method=VerificationMethodType.TOOL)


def test_acceptance_criterion_judgment_method_must_not_set_tool_name():
    with pytest.raises(ValidationError):
        AcceptanceCriterion(
            id="ac1", description="looks right", method=VerificationMethodType.JUDGMENT, tool_name="pytest",
        )


def test_acceptance_criterion_defaults_to_judgment():
    ac = AcceptanceCriterion(id="ac1", description="works")
    assert ac.method == VerificationMethodType.JUDGMENT


# --- Subtask ---

def test_subtask_blank_id_rejected():
    with pytest.raises(ValidationError):
        _model_subtask(id="   ")


def test_subtask_tool_execution_requires_tool_name():
    with pytest.raises(ValidationError):
        Subtask(id="s1", description="run", execution_method=ExecutionMethod.TOOL)


def test_subtask_model_execution_must_not_set_tool_name():
    with pytest.raises(ValidationError):
        Subtask(id="s1", description="write code", execution_method=ExecutionMethod.MODEL, tool_name="lint")


def test_subtask_model_execution_must_not_set_tool_arguments():
    with pytest.raises(ValidationError):
        Subtask(
            id="s1", description="write code", execution_method=ExecutionMethod.MODEL,
            tool_arguments={"path": "a.py"},
        )


def test_subtask_cannot_depend_on_itself():
    with pytest.raises(ValidationError):
        _model_subtask(depends_on=["s1"])


def test_subtask_rejects_duplicate_dependency():
    with pytest.raises(ValidationError):
        _model_subtask(depends_on=["s2", "s2"])


def test_subtask_valid_tool_subtask():
    st = _tool_subtask(tool_arguments={"path": "a.py"})
    assert st.execution_method == ExecutionMethod.TOOL
    assert st.tool_arguments == {"path": "a.py"}


def test_subtask_valid_model_subtask_with_dependency():
    st = _model_subtask(depends_on=["s0"])
    assert st.depends_on == ["s0"]


# --- EngineeringPlan ---

def test_engineering_plan_rejects_blank_plan_id():
    with pytest.raises(ValidationError):
        EngineeringPlan(plan_id=" ", kind=ChangeKind.TASK, subtasks=[_model_subtask()])


def test_engineering_plan_rejects_zero_subtasks():
    with pytest.raises(ValidationError):
        EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[])


def test_engineering_plan_subtask_by_id_found_and_missing():
    plan = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    assert plan.subtask_by_id("s1").id == "s1"
    assert plan.subtask_by_id("does-not-exist") is None


def test_engineering_plan_content_hash_is_stable_and_deterministic():
    plan_a = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    plan_b = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    assert plan_a.content_hash() == plan_b.content_hash()
    assert plan_a.content_hash() == plan_a.content_hash()


def test_engineering_plan_content_hash_changes_with_content():
    plan_a = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s1")])
    plan_b = EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[_model_subtask(id="s2")])
    assert plan_a.content_hash() != plan_b.content_hash()


# --- build_engineering_plan_from_planner_output ---

def test_build_engineering_plan_returns_none_for_zero_subtasks():
    output = PlannerStructuredOutput(subtasks=[])
    plan = build_engineering_plan_from_planner_output(output, plan_id="p1", kind=ChangeKind.TASK)
    assert plan is None


def test_build_engineering_plan_supplies_plan_id_and_kind_from_caller_not_output():
    output = PlannerStructuredOutput(
        subtasks=[_model_subtask(id="s1")],
        acceptance_criteria=[AcceptanceCriterion(id="ac1", description="works")],
        extension_points=["kriya.tools.registry"],
        refactor_baseline="abc123",
    )
    plan = build_engineering_plan_from_planner_output(output, plan_id="run-42", kind=ChangeKind.REFACTOR)
    assert plan is not None
    assert plan.plan_id == "run-42"
    assert plan.kind == ChangeKind.REFACTOR
    assert plan.extension_points == ["kriya.tools.registry"]
    assert plan.refactor_baseline == "abc123"
    assert len(plan.subtasks) == 1
