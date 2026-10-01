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
        # The PRD-008 S4c record shapes, from their one definition.
        from kriya.workflow.milestone_completion import integration_work_unit, milestone_unit_record

        if unit.role is WorkUnitRole.INTEGRATION:
            return integration_work_unit(plan.plan_id, unit.definition_digest)
        return milestone_unit_record(plan.plan_id, unit.id, unit.definition_digest)
    # STRUCTURED subtasks never had a durable unit record; unchanged.
    return None


# The milestone plan's integration unit. Not a valid planner milestone id
# shape, so it can never collide with one.
INTEGRATION_WORK_UNIT_ID = "__integration__"


def milestone_execution_plan(run_state: Any) -> ExecutionPlan:
    """A milestone sequence (MilestoneRunState) as one ExecutionPlan.

    One PRIMARY unit per milestone, in the plan file's declared order (which
    breaks topological ties exactly as run_milestones always has), then the
    INTEGRATION unit depending on all of them, with deterministic replay as
    the plan-wide phase between the two. Unit digests are the pre-PRD-008A
    values (milestone_definition_digest; the integration unit's is the
    ordered plan digest), so every checkpoint and completion proof already
    on disk still matches. Raises InvalidExecutionPlanError for a plan that
    must not execute (a cycle or an unknown dependency in a hand-edited
    plan file, which topological_order would otherwise silently drop)."""
    from kriya.workflow.execution_plan import TerminalPhase, stable_topological_order
    from kriya.workflow.milestone_completion import milestone_definition_digest
    from kriya.workflow.milestones import _plan_digest, build_integration_goal_text

    units = [
        WorkUnit.build(
            id=milestone.id,
            goal=milestone.goal,
            definition_digest=milestone_definition_digest(milestone),
            acceptance_criteria=[(item.id, item.description) for item in milestone.acceptance],
            depends_on=milestone.depends_on,
            provides=[capability.name for capability in milestone.provides],
            consumes=milestone.consumes,
            verification_requirements=[item.description for item in milestone.acceptance],
            # Lossless: the full source definition (mode, extends,
            # entrypoint, adds_dependencies, capability descriptions, ...).
            provenance={"milestone": milestone.model_dump(mode="json")},
        )
        for milestone in run_state.milestones
    ]
    # Validates the milestone units first (cycle / unknown dependency), so the
    # integration unit is only derived from an order that really exists.
    primary = ExecutionPlan.build(
        plan_id=run_state.group_id, source_kind=PlanSourceKind.MILESTONE, work_units=units,
    )
    by_id = {milestone.id: milestone for milestone in run_state.milestones}
    ordered = [by_id[unit.id] for unit in stable_topological_order(primary.work_units)]
    integration = WorkUnit.build(
        id=INTEGRATION_WORK_UNIT_ID,
        goal=build_integration_goal_text(run_state.original_goal, ordered),
        definition_digest=_plan_digest(ordered),
        role=WorkUnitRole.INTEGRATION,
        depends_on=[milestone.id for milestone in ordered],
        provenance={"original_goal": run_state.original_goal},
    )
    return ExecutionPlan.build(
        plan_id=run_state.group_id, source_kind=PlanSourceKind.MILESTONE,
        work_units=[*units, integration],
        terminal_phases=[TerminalPhase.REPLAY_PRIOR_VERIFICATIONS],
        provenance={"group_id": run_state.group_id},
    )
