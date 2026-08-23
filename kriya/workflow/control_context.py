"""WorkflowControlContext - MA2.3 of the control-plane implementation plan.
A lightweight runtime object pairing an EngineeringRoute (MA1,
kriya/workflow/triage.py) with its resolved ProcessProfile (MA2.1,
kriya/workflow/process_profile.py), so run_generation_workflow() has ONE
place to keep them together and pass around later, rather than threading
two separate parameters through every internal function that eventually
needs either.

NOT the future MA5 ControlState (kriya/control/state.py, not built yet) -
that will be the persistent, durable control-plane record spanning
contracts/artifacts/decisions across a whole run or milestone sequence.
This is deliberately narrower and ephemeral: immutable metadata for the
CURRENT run_generation_workflow() call only, constructed once triage
completes, threaded as a local variable - not persisted on its own.
(engineering_route is separately carried on GenerationState, for
telemetry - see state.py's own comment on that field for why it stays
there rather than being folded into this object.)

MA2.3 itself makes no runtime behavior change - nothing yet reads
process_profile to alter context/planning/approval/verification. That
starts at MA2.5/MA2.6.
"""

from dataclasses import dataclass

from kriya.workflow.process_profile import ProcessProfile, process_profile_for
from kriya.workflow.triage import EngineeringRoute


@dataclass(frozen=True)
class WorkflowControlContext:
    """engineering_route and process_profile are kept TOGETHER on purpose -
    a ProcessProfile that no longer matches its route's execution_weight
    (because only one half got updated after a re-triage) is exactly the
    drift this pairing exists to rule out. Always construct via for_route()/
    with_route() below rather than building one by hand with a
    process_profile that wasn't actually resolved from the same route."""

    engineering_route: EngineeringRoute
    process_profile: ProcessProfile

    @classmethod
    def for_route(cls, route: EngineeringRoute) -> "WorkflowControlContext":
        """The only supported construction path - resolves process_profile
        from route.execution_weight itself, so the pairing can never be
        built already out of sync."""
        return cls(engineering_route=route, process_profile=process_profile_for(route.execution_weight))

    def with_route(self, new_route: EngineeringRoute) -> "WorkflowControlContext":
        """Returns a NEW WorkflowControlContext for an updated route (e.g.
        MA2.4's post-Architect recomputation, EngineeringRoute.
        with_recomputed_risk) - re-resolves process_profile from the new
        route's execution_weight rather than leaving the old profile
        attached to a route it no longer describes."""
        return WorkflowControlContext.for_route(new_route)
