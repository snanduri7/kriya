"""PRD-008A: adapters from each user-facing mode to the canonical ExecutionPlan.

    direct goal        -> ExecutionPlan(DIRECT,    [one WorkUnit])
    MilestoneRunState  -> ExecutionPlan(MILESTONE, [milestones..., integration])

Adapters translate; they decide nothing about safety. They also own the
durable *record* of a unit's identity (RunRecord.active_work_unit -> commit
cycles and checkpoints), because that record's shape predates PRD-008A and
must stay byte-compatible with every checkpoint and completion proof already
on disk.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from kriya.workflow.execution_plan import ExecutionPlan, PlanSourceKind, WorkUnit, WorkUnitRole

# The direct plan's only unit. Stable across runs, so a direct checkpoint's
# record can be recognized by a later direct run.
DIRECT_WORK_UNIT_ID = "direct"
DIRECT_WORK_UNIT_KIND = "direct"


def direct_execution_plan(goal: str, *, plan_id: str) -> ExecutionPlan:
    """A direct `kriya generate` goal as a one-unit plan.

    No decomposition and no model call: the unit's goal is the user's goal,
    verbatim. Its definition digest is the goal's own SHA-256 (the same value
    as the RunRecord's goal_hash)."""
    unit = WorkUnit.build(
        id=DIRECT_WORK_UNIT_ID,
        goal=goal,
        definition_digest=hashlib.sha256(goal.encode("utf-8")).hexdigest(),
    )
    return ExecutionPlan.build(plan_id=plan_id, source_kind=PlanSourceKind.DIRECT, work_units=[unit])


def work_unit_record(plan: ExecutionPlan, unit_id: str) -> Optional[Dict[str, Any]]:
    """The durable identity record of one unit (RunRecord.active_work_unit,
    copied into each commit cycle and saved with each checkpoint).

    A direct record deliberately carries no definition digest: a direct
    unit's definition is its goal, and PRD-008's goal fingerprint already
    judges that on every resume - matching on it here would pre-empt that
    validator's typed decision with a silent non-match."""
    unit = plan.unit(unit_id)
    if plan.source_kind is PlanSourceKind.DIRECT:
        return {
            "kind": DIRECT_WORK_UNIT_KIND, "group_id": None, "milestone_id": None,
            "definition_digest": None, "work_unit_id": unit.id,
        }
    if plan.source_kind is PlanSourceKind.MILESTONE:
        # Exactly kriya/workflow/milestone_completion.py's milestone_work_unit
        # / integration_work_unit shapes (PRD-008 S4c).
        if unit.role is WorkUnitRole.INTEGRATION:
            return {
                "kind": "integration", "group_id": plan.plan_id, "milestone_id": None,
                "definition_digest": unit.definition_digest,
            }
        return {
            "kind": "milestone", "group_id": plan.plan_id, "milestone_id": unit.id,
            "definition_digest": unit.definition_digest,
        }
    # STRUCTURED subtasks never had a durable unit record; unchanged.
    return None
