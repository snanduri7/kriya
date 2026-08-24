"""VerificationReport integration - MA6.11 of the MA6 structured-execution
implementation plan. build_verification_report() is the one real
constructor for kriya.workflow.workflow_types.VerificationReport -
initially just WRAPS existing Kriya verification results (the Quality
Gates loop's own overall pass/fail, tool results a caller already ran),
never re-implements or replaces them. No new verification MECHANISM is
introduced here - this is a pure, deterministic aggregation function.

The one real rule this module enforces: an AcceptanceCriterion
(plan_schema.py) with method=judgment and no independent grader is left
UNRESOLVED (VerificationCheck.passed=None), never silently marked passed -
"the Implementer must never self-grade it" (MA6 spec). A caller that later
wires in a real Reviewer/judge pass for a judgment criterion supplies its
verdict through `judgment_results`, exactly like a tool result; until then,
every judgment criterion routes the whole report to NEEDS_REVIEW.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from kriya.workflow.plan_schema import AcceptanceCriterion, VerificationMethodType
from kriya.workflow.workflow_types import VerificationCheck, VerificationReport, VerificationVerdict


def build_verification_report(
    acceptance_criteria: List[AcceptanceCriterion],
    *,
    tool_results: Optional[Dict[str, bool]] = None,
    judgment_results: Optional[Dict[str, bool]] = None,
    existing_gate_success: Optional[bool] = None,
    existing_gate_failures: Optional[List[str]] = None,
) -> VerificationReport:
    """tool_results/judgment_results: criterion tool_name -> passed, resp.
    criterion id -> passed, for whichever criteria a caller already has a
    real, independently-computed verdict for (a tool run, a Reviewer
    pass). A criterion absent from the relevant dict is UNRESOLVED, not
    assumed to pass.

    existing_gate_success/existing_gate_failures: the Quality Gates loop's
    own existing overall result (kriya/workflow/workflow.py) - when
    supplied, a False here always yields at least FAIL/failures on the
    resulting report even if every individual acceptance criterion
    happened to resolve clean, since a plan's acceptance criteria are
    necessarily a PARTIAL view of correctness (compile/test/regression
    failures the existing gates already catch are not duplicated as
    criteria here)."""
    tool_results = tool_results or {}
    judgment_results = judgment_results or {}
    failures: List[str] = list(existing_gate_failures or [])

    checks: List[VerificationCheck] = []
    for criterion in acceptance_criteria:
        if criterion.method == VerificationMethodType.TOOL:
            if criterion.tool_name in tool_results:
                passed = tool_results[criterion.tool_name]
                detail = f"tool={criterion.tool_name!r} result"
            else:
                passed = None
                detail = f"tool={criterion.tool_name!r} has not been run yet - unresolved"
        else:
            if criterion.id in judgment_results:
                passed = judgment_results[criterion.id]
                detail = "resolved by an independent judgment pass"
            else:
                passed = None
                detail = "judgment criterion with no independent grader - never self-graded"

        checks.append(
            VerificationCheck(
                criterion_id=criterion.id, method_type=criterion.method.value,
                description=criterion.description, passed=passed, detail=detail,
            )
        )
        if passed is False:
            failures.append(f"acceptance criterion {criterion.id!r} failed: {criterion.description}")

    any_failed = existing_gate_success is False or any(c.passed is False for c in checks)
    any_unresolved = any(c.passed is None for c in checks)

    if any_failed:
        verdict = VerificationVerdict.FAIL
    elif any_unresolved:
        verdict = VerificationVerdict.NEEDS_REVIEW
    elif existing_gate_success is None and not checks:
        verdict = VerificationVerdict.NEEDS_REVIEW
    else:
        verdict = VerificationVerdict.PASS

    return VerificationReport(checks=tuple(checks), failures=tuple(failures), verdict=verdict)
