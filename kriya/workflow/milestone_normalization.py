"""Legacy (v1) -> Schema v2 milestone normalization - see
kriya/agents/contracts.py's MilestoneV2 for the target shape and its own
docstring for why v1 `Milestone` stays completely unchanged and is still the
only type any real code path constructs today (MA3.1). This module is
additive too: nothing calls it yet - MA3.6 wires it into the real
MilestonePlannerAgent -> MilestonePlanValidator pipeline once the planner
prompt itself (MA3.5) can emit v2-shaped fields; until then this is a pure,
independently-testable function over the already-real, already-working v1
MilestoneList Kriya parses today (kriya/agents/contracts.py's
parse_milestone_list)."""

from typing import List

from kriya.agents.contracts import AcceptanceCriterion, Milestone, MilestoneV2


def normalize_legacy_milestones(milestones: List[Milestone]) -> List[MilestoneV2]:
    """Converts a v1 milestone sequence (goal/success_criterion/
    depends_on_previous) into an equivalent MilestoneV2 DAG.

    Canonical `M1`, `M2`, ... ids are assigned STRICTLY by list position - v1
    carries no identity of its own, so this is the one legitimate place
    position becomes identity, by construction. Everything downstream of
    this function (MA3.3's validator, MA3.7's ID-based run state) must still
    honor "identity != list index" from here on - it operates on the
    resulting ids, never re-derives them from position.

    depends_on_previous is resolved the SAME way
    kriya/workflow/milestones.py's build_milestone_goal_text() already
    treats it live today, so this normalization can never disagree with the
    orchestrator's real, current behavior: milestone 1 is unconditionally
    non-dependent (depends_on=[]) regardless of its own depends_on_previous
    value (a first milestone structurally has no predecessor - see that
    function's own docstring for why); milestone N>1 depends on milestone
    N-1's canonical id only when depends_on_previous is True, otherwise
    depends_on=[].

    success_criterion becomes a single acceptance criterion, id
    "<milestone_id>-A1" - v1 never expressed more than one checkable outcome
    per milestone, so one-to-one is the only faithful mapping.

    mode/extends/entrypoint/provides/consumes/adds_dependencies are left at
    their MilestoneV2 defaults (None/[]) rather than guessed - v1 plans carry
    no evidence for any of them. MA3.3's validator treats mode=None as "no
    extension/composition-specific checks apply," which is exactly correct
    for a plan that never expressed either."""
    normalized: List[MilestoneV2] = []
    for i, m in enumerate(milestones):
        milestone_id = f"M{i + 1}"
        depends_on: List[str] = []
        if i > 0 and m.depends_on_previous:
            depends_on = [normalized[i - 1].id]
        normalized.append(
            MilestoneV2(
                id=milestone_id,
                goal=m.goal,
                depends_on=depends_on,
                acceptance=[
                    AcceptanceCriterion(id=f"{milestone_id}-A1", description=m.success_criterion)
                ],
            )
        )
    return normalized
