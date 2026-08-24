"""Shared execution-outcome types for the MA6 structured-execution layer -
kriya/workflow/subtask_executor.py (MA6.5) and kriya/workflow/
workflow_controller.py (MA6.8) both depend on this module. Kept separate
from kriya/workflow/plan_schema.py (MA6.1) on purpose: plan_schema.py
describes the Planner's INPUT to execution (immutable, hash-addressed once
validated); this module describes execution OUTCOMES (one produced per
attempt) - the same "input vs run-state" split kriya/control/state.py's
ControlState keeps from kriya/workflow/state.py's GenerationState, for the
same reason: never merge a plan with what happened when it ran.

Expected to grow across MA6's later sub-tasks (MA6.8's WorkflowResult,
MA6.11's VerificationReport, ...) rather than be split into many small
files immediately - see kriya/control/__init__.py-adjacent precedent in
this codebase's own MA6 sequencing notes: per-kind adapters start as
methods inside workflow_controller.py and are only split out if they grow
large. This module follows the same "don't pre-split" discipline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class SubtaskStatus(str, Enum):
    """COMPLETED: the subtask's own execution (model generation or tool
    run) finished without error - this says nothing about whether the
    plan's acceptance criteria or verification later pass; that's a
    separate stage (MA6.11's VerificationReport). FAILED: the execution
    itself errored (model call failed, tool raised). NEEDS_REVIEW: the
    subtask cannot be resolved deterministically (e.g. an unregistered
    tool discovered at execute-time despite passing plan_validation
    earlier - a defensive fail-closed case, not the normal path)."""

    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class SubtaskResult:
    """The outcome of ONE SubtaskExecutor.execute() call - always exactly
    one subtask (MA6 invariant 2). `files` holds the raw file entries a
    MODEL-tagged subtask's DeveloperAgent.run_generation call returned
    (filepath/content/edits shape, unchanged from that existing contract -
    SubtaskExecutor does not reinterpret it); empty for a TOOL-tagged
    subtask, whose raw return value instead lands in `tool_output`.

    undeclared_files: files the execution actually touched that were NOT
    in subtask.planned_files - MA6 invariant 4 ("a subtask may not modify
    undeclared files silently"). SubtaskExecutor only DETECTS and reports
    this; deciding what to do about it (re-risk, re-plan) is the calling
    orchestrator's job (MA6.8/MA6.12), not this dataclass's or
    SubtaskExecutor's - keeping detection and policy in separate layers."""

    subtask_id: str
    status: SubtaskStatus
    execution_method: str

    files: Tuple[Dict[str, Any], ...] = ()
    tool_output: Any = None

    undeclared_files: Tuple[str, ...] = ()

    error: Optional[str] = None
    reason_codes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "status": self.status.value,
            "execution_method": self.execution_method,
            "files": [dict(f) for f in self.files],
            "tool_output": self.tool_output,
            "undeclared_files": list(self.undeclared_files),
            "error": self.error,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class WorkflowResult:
    """WorkflowController.execute()'s (MA6.8) own return shape - control-
    plane bookkeeping (run_id/control_state/route) wrapped AROUND whatever
    the dispatched handler actually produced. `legacy_result` is
    run_generation_workflow()'s existing, unchanged Dict[str, Any] return
    value for this incremental slice (every ChangeKind still routes
    through _run_legacy_generation - see workflow_controller.py's own
    docstring); `subtask_results` stays empty until MA6.9/6.10 wire a real
    kind-specific adapter that actually drives SubtaskExecutor.

    control_state/route are typed Any here (not kriya.control.state.
    ControlState / kriya.workflow.triage.EngineeringRoute directly) purely
    to avoid this shared types module importing either - both already
    depend on plan_schema-adjacent modules; workflow_controller.py, the
    one real producer of a WorkflowResult, has the concrete types."""

    run_id: str
    control_state: Any
    route: Any
    legacy_result: Optional[Dict[str, Any]] = None
    subtask_results: Tuple[SubtaskResult, ...] = ()


class VerificationVerdict(str, Enum):
    """PASS only when every check that could be evaluated passed AND
    nothing was left unresolved. A JUDGMENT criterion with no independent
    grader is UNRESOLVED, not passed - MA6.11's own rule (MA6 spec):
    "the Implementer must never self-grade it." NEEDS_REVIEW is what an
    unresolved check (missing tool result, or any judgment criterion)
    produces; FAIL is reserved for something that was actually checked and
    came back negative."""

    PASS = "pass"
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class VerificationCheck:
    """One AcceptanceCriterion's (plan_schema.py) resolved status.
    `passed=None` means unresolved (no grader ran, or it was a judgment
    criterion with no independent grading) - never treated the same as
    `passed=False`; see VerificationVerdict's own docstring."""

    criterion_id: str
    method_type: str
    description: str
    passed: Optional[bool]
    detail: str = ""


@dataclass(frozen=True)
class VerificationReport:
    """kriya/workflow/verification_report.py::build_verification_report()
    is the one real constructor - initially just WRAPS existing Kriya
    verification results (the Quality Gates loop's own pass/fail, tool
    results a caller already ran), never replaces or re-implements them."""

    checks: Tuple[VerificationCheck, ...] = ()
    failures: Tuple[str, ...] = ()
    verdict: VerificationVerdict = VerificationVerdict.NEEDS_REVIEW
