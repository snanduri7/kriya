"""ENFORCE-VERIFIED-NO-CHANGE-001: a planned enforce work unit may complete
without a mutation only when Kriya deterministically verifies it is already
satisfied.

This EXTENDS the existing completion semantics: a milestone that committed
nothing completes as VERIFIED_NO_CHANGE only when every acceptance criterion
is covered by deterministic evidence (kriya/workflow/milestone_completion.py
``no_change_verification``). An enforce unit is judged by the SAME rule -
``criterion_coverage`` + ``coverage_refusal`` - never a second definition.

A Developer NO CHANGE answer is never success: it is a request for Kriya to
verify the current candidate. The unit's evidence is the final attempt's own
deterministic gates, run on the current candidate (every dependency unit
already applied) after the normal candidate gates and the terminal
regression. A criterion is covered only by a ``method: tool`` criterion whose
tool maps to a gate family that executed and passed in that attempt; a test
gate counts only with a parsed, non-zero executed-test count. A judgment
criterion, a compile gate standing in for behaviour, a Developer / Planner /
Reviewer opinion, or an unparseable test count is never evidence.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.workflow import milestone_completion as mc
from kriya.workflow.plan_schema import EngineeringPlan

VERIFIED_NO_CHANGE = mc.VERIFIED_NO_CHANGE
VERIFIED_NO_CHANGE_REFUSED = "VERIFIED_NO_CHANGE_REFUSED"

# A criterion's tool -> the coverage kind milestone_completion's rule knows.
_TOOL_COVERAGE_KIND = {
    "compile": "compile",
    "test": "test",
    "tests": "test",
    "regression": "regression_test",
    "run_verification": "run_verification",
    "application_runtime": "run_verification",
}
_GATE_TYPES_FOR_KIND = {
    "compile": ("compile",),
    "test": ("regression_test", "test", "targeted_test"),
    "regression_test": ("regression_test",),
    "run_verification": ("run_verification",),
}


def executed_test_count(output: str) -> Optional[int]:
    """Tests that ran and passed, parsed from pytest or Surefire output; None
    when the output is neither (never a guess)."""
    from kriya.workflow.validation_baseline import (
        parse_pytest_structured_outcomes,
        parse_surefire_structured_outcomes,
    )

    for parser in (parse_surefire_structured_outcomes, parse_pytest_structured_outcomes):
        parsed = parser(output or "")
        if parsed is not None and parsed[1]:
            return parsed[1].get("passed", 0)
    return None


def _latest(outcomes: Sequence[Dict[str, Any]], gate_types: Sequence[str], attempt: int) -> Optional[Dict[str, Any]]:
    """The attempt's latest outcome of one of ``gate_types``. A regression
    outcome recorded as a FUTURE_OWNER deferral (PRV-11: the failing tests
    are owned by a later unit, so the gate "passed" for this one) proves
    nothing about the current candidate and is never evidence here
    (independent review F3, BACKEND-FINAL-CLOSURE-005)."""
    for gate_type in gate_types:
        found = next((o for o in reversed(outcomes) if o.get("type") == gate_type and o.get("attempt") == attempt),
                     None)
        if found is not None:
            return None if found.get("deferred_to_future_owner") else found
    return None


def unit_coverage_items(
    plan: EngineeringPlan, subtask_id: str, gate_outcomes: Sequence[Dict[str, Any]], attempt: int,
) -> List[Dict[str, Any]]:
    """milestone_completion's coverage-map items for the unit's tool
    criteria, from the attempt's own gate outcomes. A criterion with no
    deterministic tool, or whose gate did not run, gets no item (UNCOVERED)."""
    subtask = plan.subtask_by_id(subtask_id)
    criteria = {criterion.id: criterion for criterion in plan.acceptance_criteria}
    items: List[Dict[str, Any]] = []
    for criterion_id in subtask.acceptance_criteria_ids if subtask is not None else []:
        criterion = criteria.get(criterion_id)
        if criterion is None:
            continue
        # A judgment criterion has no tool_name (plan schema), so no kind.
        kind = _TOOL_COVERAGE_KIND.get(criterion.tool_name or "")
        outcome = _latest(gate_outcomes, _GATE_TYPES_FOR_KIND.get(kind, ()), attempt) if kind else None
        if outcome is None:
            continue
        passed = outcome.get("success") is True
        item = {"criterion_id": criterion_id, "kind": outcome["type"], "selector": criterion.tool_name,
                "attempt": attempt}
        if outcome["type"] in mc._TEST_EVIDENCE_KINDS:
            # The shared rule turns a missing / zero count into NO_TESTS_EXECUTED.
            item["tests_executed"] = executed_test_count(str(outcome.get("output", "")))
            item["status"] = mc.PASS_WITH_TESTS if passed else mc.FAILED
        else:
            item["status"] = mc.PASSED if passed else mc.FAILED
        items.append(item)
    return items


def verify_no_change_unit(
    plan: EngineeringPlan, subtask_id: str, result: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(binding, None) when every acceptance criterion of the unit is covered
    by deterministic evidence in ``result`` (``deterministic_gate_evidence`` +
    ``acceptance_coverage`` of the final attempt, the milestone result shape),
    else (None, typed refusal). Every criterion of the unit counts - a
    criterion a later unit also claims is still this unit's to prove when it
    changes nothing."""
    subtask = plan.subtask_by_id(subtask_id)
    criteria = list(subtask.acceptance_criteria_ids) if subtask is not None else []
    covered, statuses = mc.criterion_coverage(criteria, result)
    refusal = mc.coverage_refusal(statuses, unit="work unit")
    if refusal is not None:
        return None, refusal
    return {"kind": VERIFIED_NO_CHANGE, "acceptance_coverage": covered, "criteria": dict(statuses)}, None
