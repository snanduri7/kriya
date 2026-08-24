"""Subtask-execution telemetry - MA6.12 of the MA6 structured-execution
implementation plan. Thin, pure record-builder functions over
kriya/control/decisions.py's DecisionLedger (MA5.5), the exact same
pattern kriya/control/telemetry.py (MA5.10) already established for the
rest of the control plane - each function produces exactly one Decision,
never full source content (only ids, hashes, counts, and small structural
summaries), and persisting it to disk stays the caller's own choice via
DecisionLedger.append_to_file()/record_and_persist().

Covers section 62's field list: plan_id/plan_hash/subtask_count/
subtask_attempts/subtask attribution/planned-vs-actual files/undeclared
file touches/context package per subtask/tool-vs-model execution counts/
replan count/resume count.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from kriya.control.decisions import Decision, DecisionLedger
from kriya.control.telemetry import _context_package_summary_fields
from kriya.workflow.context_package import ContextPackage
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod
from kriya.workflow.subtask_checkpoint import SubtaskResumeResult
from kriya.workflow.workflow_types import SubtaskResult


def record_plan_created(ledger: DecisionLedger, plan: EngineeringPlan) -> Decision:
    method_counts = Counter(st.execution_method.value for st in plan.subtasks)
    return ledger.record(
        "plan_created", plan_id=plan.plan_id, plan_hash=plan.content_hash(), kind=plan.kind.value,
        subtask_count=len(plan.subtasks),
        tool_subtask_count=method_counts.get(ExecutionMethod.TOOL.value, 0),
        model_subtask_count=method_counts.get(ExecutionMethod.MODEL.value, 0),
        acceptance_criteria_count=len(plan.acceptance_criteria),
    )


def record_subtask_attempt(
    ledger: DecisionLedger, plan: EngineeringPlan, result: SubtaskResult, attempt: int
) -> Decision:
    """One record per SubtaskExecutor.execute() call (MA6.5) - `attempt`
    is the caller's own retry counter (this module has no opinion on retry
    policy, only reports what happened)."""
    subtask = plan.subtask_by_id(result.subtask_id)
    planned_files = [pf.path for pf in subtask.planned_files] if subtask else []
    return ledger.record(
        "subtask_attempt", plan_id=plan.plan_id, plan_hash=plan.content_hash(),
        subtask_id=result.subtask_id, attempt=attempt, execution_method=result.execution_method,
        status=result.status.value, planned_file_count=len(planned_files),
        actual_file_count=len(result.files) if result.files else (1 if result.tool_output is not None else 0),
        undeclared_file_count=len(result.undeclared_files), error=result.error,
    )


def record_undeclared_file_touch(ledger: DecisionLedger, plan: EngineeringPlan, result: SubtaskResult) -> Decision:
    """MA6 spec, section 62: "undeclared file touches (actual != planned)
    -> record scope surprise -> re-risk -> possibly re-plan/re-triage,
    never silently accepted." This function is the "record scope surprise"
    half only - deciding whether/how to re-risk or re-plan is the calling
    orchestrator's job (WorkflowController, MA6.8/6.9), not telemetry's.
    Caller is expected to only call this when result.undeclared_files is
    non-empty (mirrors record_artifact_drift's own "caller already knows
    something happened" convention) - called unconditionally here would
    otherwise silently record a spurious "0 undeclared files" event for
    every ordinary, unremarkable attempt."""
    return ledger.record(
        "subtask_undeclared_file_touch", plan_id=plan.plan_id, subtask_id=result.subtask_id,
        undeclared_files=list(result.undeclared_files),
    )


def record_context_package_for_subtask(
    ledger: DecisionLedger, plan: EngineeringPlan, subtask_id: str, package: ContextPackage
) -> Decision:
    """Reuses record_context_package_summary's own field computation
    (kriya/control/telemetry.py's _context_package_summary_fields, MA5.10) -
    never a second, independently-drifting implementation of "what does a
    context package summary look like." Records exactly ONE decision
    (not also a second, untagged "context_package_built" event) - the
    subtask/plan tags are folded in from the start, not appended after
    that helper already recorded its own separate event as a side effect."""
    return ledger.record(
        "subtask_context_package", plan_id=plan.plan_id, subtask_id=subtask_id,
        **_context_package_summary_fields(package),
    )


def record_replan(ledger: DecisionLedger, plan_id: str, reason: str, new_plan_hash: Optional[str] = None) -> Decision:
    return ledger.record("plan_replanned", plan_id=plan_id, reason=reason, new_plan_hash=new_plan_hash)


def record_subtask_resume(ledger: DecisionLedger, plan: EngineeringPlan, result: SubtaskResumeResult) -> Decision:
    return ledger.record(
        "subtask_resume", plan_id=plan.plan_id, status=result.status.value,
        next_subtask_id=result.next_subtask_id, completed_count=len(result.completed_subtask_ids),
        mismatch_count=len(result.mismatches), mismatches=list(result.mismatches),
    )
