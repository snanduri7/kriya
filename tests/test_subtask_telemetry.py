"""MA6.12: subtask-execution telemetry (kriya/workflow/subtask_telemetry.py) -
first real pytest coverage for this module."""

from kriya.control.decisions import DecisionLedger
from kriya.workflow.context_package import build_context_package, make_context_item
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, Subtask
from kriya.workflow.subtask_checkpoint import ResumePointStatus, SubtaskResumeResult
from kriya.workflow.subtask_telemetry import (
    record_context_package_for_subtask,
    record_plan_created,
    record_replan,
    record_subtask_attempt,
    record_subtask_resume,
    record_undeclared_file_touch,
)
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus


def _plan():
    return EngineeringPlan(
        plan_id="p1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(id="s1", description="model work", execution_method=ExecutionMethod.MODEL),
            Subtask(id="s2", description="tool work", execution_method=ExecutionMethod.TOOL, tool_name="lint"),
        ],
    )


def test_record_plan_created_counts_execution_methods():
    ledger = DecisionLedger()
    decision = record_plan_created(ledger, _plan())
    assert decision.type == "plan_created"
    assert decision.fields["subtask_count"] == 2
    assert decision.fields["model_subtask_count"] == 1
    assert decision.fields["tool_subtask_count"] == 1
    assert decision in ledger.all()


def test_record_subtask_attempt_captures_status_and_undeclared_count():
    ledger = DecisionLedger()
    plan = _plan()
    result = SubtaskResult(
        subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model",
        files=({"filepath": "a.py", "content": "x"},), undeclared_files=("b.py",),
    )
    decision = record_subtask_attempt(ledger, plan, result, attempt=2)
    assert decision.type == "subtask_attempt"
    assert decision.fields["subtask_id"] == "s1"
    assert decision.fields["attempt"] == 2
    assert decision.fields["status"] == "completed"
    assert decision.fields["undeclared_file_count"] == 1
    assert decision.fields["actual_file_count"] == 1


def test_record_subtask_attempt_tool_output_counts_as_one_file():
    ledger = DecisionLedger()
    plan = _plan()
    result = SubtaskResult(subtask_id="s2", status=SubtaskStatus.COMPLETED, execution_method="tool", tool_output={"ok": True})
    decision = record_subtask_attempt(ledger, plan, result, attempt=1)
    assert decision.fields["actual_file_count"] == 1


def test_record_undeclared_file_touch():
    ledger = DecisionLedger()
    plan = _plan()
    result = SubtaskResult(
        subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model", undeclared_files=("b.py", "c.py"),
    )
    decision = record_undeclared_file_touch(ledger, plan, result)
    assert decision.type == "subtask_undeclared_file_touch"
    assert decision.fields["undeclared_files"] == ["b.py", "c.py"]


def test_record_context_package_for_subtask_reuses_shared_summary_fields():
    ledger = DecisionLedger()
    plan = _plan()
    item = make_context_item(
        path="a.py", content="x", reason="named in request",
        source_type="named_in_request", trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    decision = record_context_package_for_subtask(ledger, plan, "s1", package)
    assert decision.type == "subtask_context_package"
    assert decision.fields["subtask_id"] == "s1"
    assert decision.fields["plan_id"] == "p1"
    # Exactly one decision recorded - not the double-recording bug this
    # module's own docstring warns against (MA5.10/MA6.12 precedent).
    assert len(ledger.all()) == 1


def test_record_replan():
    ledger = DecisionLedger()
    decision = record_replan(ledger, "p1", reason="undeclared file touch", new_plan_hash="newhash")
    assert decision.type == "plan_replanned"
    assert decision.fields["reason"] == "undeclared file touch"
    assert decision.fields["new_plan_hash"] == "newhash"


def test_record_subtask_resume():
    ledger = DecisionLedger()
    plan = _plan()
    resume_result = SubtaskResumeResult(
        status=ResumePointStatus.RESUME, next_subtask_id="s2",
        completed_subtask_ids=["s1"], mismatches=[],
    )
    decision = record_subtask_resume(ledger, plan, resume_result)
    assert decision.type == "subtask_resume"
    assert decision.fields["status"] == "resume"
    assert decision.fields["next_subtask_id"] == "s2"
    assert decision.fields["completed_count"] == 1
    assert decision.fields["mismatch_count"] == 0
