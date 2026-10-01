from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.process_profile import HEAVY_PROFILE, LIGHT_PROFILE
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ImpactVector, RiskClass


def _route(risk: RiskClass) -> EngineeringRoute:
    from kriya.workflow.triage import determine_execution_weight
    weight = determine_execution_weight(ChangeKind.TASK, risk)
    return EngineeringRoute(
        kind=ChangeKind.TASK,
        impact=ImpactVector(),
        initial_risk_class=risk,
        current_risk_class=risk,
        max_observed_risk_class=risk,
        execution_weight=weight,
    )


def test_for_route_resolves_the_matching_profile():
    control = WorkflowControlContext.for_route(_route(RiskClass.LOW))
    assert control.process_profile == LIGHT_PROFILE


def test_with_route_re_resolves_profile_never_leaves_it_stale():
    control = WorkflowControlContext.for_route(_route(RiskClass.LOW))
    assert control.process_profile == LIGHT_PROFILE

    escalated_route = control.engineering_route.with_recomputed_risk(
        RiskClass.HIGH, ImpactVector(build_system_change=True), ["planned_build_system_change"],
    )
    new_control = control.with_route(escalated_route)

    assert new_control.process_profile == HEAVY_PROFILE
    # original untouched (frozen)
    assert control.process_profile == LIGHT_PROFILE


def test_control_context_is_frozen():
    control = WorkflowControlContext.for_route(_route(RiskClass.LOW))
    try:
        control.process_profile = HEAVY_PROFILE
        raise SystemExit("WorkflowControlContext should be frozen/immutable")
    except Exception as e:
        assert type(e).__name__ == "FrozenInstanceError"
