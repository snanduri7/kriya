"""Planner Reliability R1, PR-3: a plan stays consistent with the Code
Intelligence targets it selects.

The enforce Planner is shown ranked Code Intelligence candidates (symbol and
configuration ids). Code Intelligence is evidence, never authorization: the
Planner need not select any candidate, top-ranked or not. But a subtask that
states it will change a candidate (``Subtask.mutation_targets``) must be
consistent with that statement - live (spring-framework-petclinic pet types
cache): the relevant XML configuration candidates were shown, the plan
omitted the XML and contradicted its own prerequisite declarations, and the
repair never converged.

Checked deterministically against the CURRENT Code Intelligence view:
- the id names an existing symbol/config entry (an unknown or stale id is
  PLAN_TARGET_UNKNOWN - never accepted on the model's word);
- the stated file is that entry's file;
- the subtask owns that file (planned_files; a verification unit owns none,
  so a target stated on one is always inconsistent).
Any inconsistency is PLAN_TARGET_INCONSISTENT; both go through the existing
bounded plan repair.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.workflow.plan_schema import EngineeringPlan

PLAN_TARGET_UNKNOWN = "PLAN_TARGET_UNKNOWN"
PLAN_TARGET_INCONSISTENT = "PLAN_TARGET_INCONSISTENT"


def validate_mutation_targets(
    plan: EngineeringPlan, service: Optional[Any],
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """(errors, reason codes, evidence records - one per stated target).
    ``service``: a CodeIntelligenceService current view; without one a plan
    that states targets cannot be checked and every target is unknown."""
    errors: List[str] = []
    codes: List[str] = []
    evidence: List[Dict[str, Any]] = []
    for st in plan.subtasks:
        owned = {pf.path for pf in st.planned_files}
        for target in st.mutation_targets:
            symbol = service.symbol(target.target_id) if service is not None else None
            record = {"subtask": st.id, "target_id": target.target_id, "file": target.file,
                      "action": target.action, "requirement_ids": list(target.requirement_ids),
                      "resolved_path": symbol.path if symbol is not None else None}
            if symbol is None:
                record["verdict"] = PLAN_TARGET_UNKNOWN
                errors.append(f"subtask {st.id!r} mutation target {target.target_id!r} is not a current Code "
                              "Intelligence symbol/config id (use only ids listed in the localization candidates)")
                codes.append(PLAN_TARGET_UNKNOWN)
            elif symbol.path != target.file:
                record["verdict"] = PLAN_TARGET_INCONSISTENT
                errors.append(f"subtask {st.id!r} mutation target {target.target_id!r} lives in {symbol.path!r}, "
                              f"not {target.file!r}")
                codes.append(PLAN_TARGET_INCONSISTENT)
            elif target.file not in owned:
                record["verdict"] = PLAN_TARGET_INCONSISTENT
                errors.append(f"subtask {st.id!r} states it will change {target.target_id!r} but does not own "
                              f"{target.file!r} in planned_files")
                codes.append(PLAN_TARGET_INCONSISTENT)
            else:
                record["verdict"] = "consistent"
            evidence.append(record)
    return errors, list(dict.fromkeys(codes)), evidence


def check_plan_targets(
    plan: EngineeringPlan, workspace_path: str, dependency_graph_path: Callable[[], str],
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """``validate_mutation_targets`` against the workspace's current Code
    Intelligence view. A plan stating no targets costs nothing (the index
    path is not even resolved): stating targets is optional, Code
    Intelligence never forces a selection."""
    if not any(st.mutation_targets for st in plan.subtasks):
        return [], [], []
    from kriya.workflow.graph_retrieval import open_code_intelligence

    service = open_code_intelligence(workspace_path, dependency_graph_path())
    if service is None:
        return validate_mutation_targets(plan, None)
    try:
        return validate_mutation_targets(plan, service.current_view())
    finally:
        service.close()
