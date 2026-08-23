"""kriya/workflow/triage.py - MA1.1's pure risk/weight functions had only
ad-hoc standalone verification until now (no permanent pytest coverage);
backfilled here alongside MA2.2's own explicitly-required escalation tests,
since both exercise the same small set of pure functions/methods and
belong in one file. Deliberately NOT covering EngineeringTriageService.
classify()'s goal-text/repository signal detectors here - that's MA1.2's
own gap, out of scope for this pass."""

from kriya.workflow.triage import (
    ChangeKind,
    EngineeringRoute,
    ExecutionWeight,
    ImpactVector,
    RiskClass,
    determine_execution_weight,
    determine_risk_class,
    escalate_risk,
)


def _route(kind: ChangeKind, risk: RiskClass, impact: ImpactVector = None) -> EngineeringRoute:
    impact = impact if impact is not None else ImpactVector()
    weight = determine_execution_weight(kind, risk)
    return EngineeringRoute(
        kind=kind,
        impact=impact,
        initial_risk_class=risk,
        current_risk_class=risk,
        max_observed_risk_class=risk,
        execution_weight=weight,
    )


# --- determine_risk_class ---------------------------------------------------

def test_risk_class_low_when_nothing_fires():
    assert determine_risk_class(ImpactVector()) == RiskClass.LOW


def test_risk_class_high_on_security_boundary_alone():
    assert determine_risk_class(ImpactVector(security_boundary_change=True)) == RiskClass.HIGH


def test_risk_class_high_on_build_system_alone():
    assert determine_risk_class(ImpactVector(build_system_change=True)) == RiskClass.HIGH


def test_risk_class_medium_on_persistence_change_with_no_downstream_references():
    assert determine_risk_class(ImpactVector(persistence_change=True)) == RiskClass.MEDIUM


def test_risk_class_high_on_persistence_change_with_downstream_references():
    assert determine_risk_class(
        ImpactVector(persistence_change=True, downstream_references=1)
    ) == RiskClass.HIGH


def test_risk_class_medium_on_five_or_more_files_touched():
    assert determine_risk_class(ImpactVector(files_touched=5)) == RiskClass.MEDIUM
    assert determine_risk_class(ImpactVector(files_touched=4)) == RiskClass.LOW


# --- determine_execution_weight: the design's own kind x risk table --------

def test_task_low_is_light():
    assert determine_execution_weight(ChangeKind.TASK, RiskClass.LOW) == ExecutionWeight.LIGHT


def test_task_medium_is_standard():
    assert determine_execution_weight(ChangeKind.TASK, RiskClass.MEDIUM) == ExecutionWeight.STANDARD


def test_high_task_becomes_heavy():
    """The design's own JWT-fix example: a bug-shaped (`task`) request that
    is nonetheless HIGH risk must land on HEAVY - never silently fall back
    to "tasks get the light treatment" because of how it reads."""
    assert determine_execution_weight(ChangeKind.TASK, RiskClass.HIGH) == ExecutionWeight.HEAVY


def test_enhancement_high_is_heavy():
    assert determine_execution_weight(ChangeKind.ENHANCEMENT, RiskClass.HIGH) == ExecutionWeight.HEAVY


def test_refactor_high_is_heavy():
    assert determine_execution_weight(ChangeKind.REFACTOR, RiskClass.HIGH) == ExecutionWeight.HEAVY


def test_milestone_low_is_standard_never_light():
    """A milestone-shaped request never gets LIGHT depth, even at LOW risk -
    the one case where kind alone changes the floor of execution_weight."""
    assert determine_execution_weight(ChangeKind.MILESTONE, RiskClass.LOW) == ExecutionWeight.STANDARD


# --- MA2.2: monotonic risk escalation ---------------------------------------

def test_risk_can_escalate_low_to_high():
    route = _route(ChangeKind.TASK, RiskClass.LOW)
    escalated = route.with_recomputed_risk(
        RiskClass.HIGH, ImpactVector(build_system_change=True), ["planned_build_system_change"],
    )
    assert escalated.current_risk_class == RiskClass.HIGH
    assert escalated.max_observed_risk_class == RiskClass.HIGH


def test_risk_never_downgrades():
    route = _route(ChangeKind.TASK, RiskClass.LOW)
    escalated = route.with_recomputed_risk(RiskClass.HIGH, ImpactVector(build_system_change=True), [])
    de_escalated = escalated.with_recomputed_risk(RiskClass.LOW, ImpactVector(), [])
    assert de_escalated.current_risk_class == RiskClass.LOW
    assert de_escalated.max_observed_risk_class == RiskClass.HIGH  # never lowered


def test_weight_recomputed_after_escalation():
    route = _route(ChangeKind.TASK, RiskClass.LOW)
    assert route.execution_weight == ExecutionWeight.LIGHT
    escalated = route.with_recomputed_risk(RiskClass.HIGH, ImpactVector(build_system_change=True), [])
    assert escalated.execution_weight == ExecutionWeight.HEAVY


def test_current_risk_can_change_but_max_remains_high():
    """The design's own worked example: TASK/LOW/LIGHT -> (Architect finds
    pom.xml) -> TASK/HIGH/HEAVY -> (Developer only touches one .java file)
    -> current may read lower, but max_observed_risk_class (and therefore
    execution_weight) stays HEAVY."""
    route = _route(ChangeKind.TASK, RiskClass.LOW)
    after_architect = route.with_recomputed_risk(
        RiskClass.HIGH, ImpactVector(build_system_change=True), ["planned_build_system_change"],
    )
    assert after_architect.max_observed_risk_class == RiskClass.HIGH
    assert after_architect.execution_weight == ExecutionWeight.HEAVY

    after_developer = after_architect.with_recomputed_risk(
        RiskClass.LOW, ImpactVector(), [],
    )
    assert after_developer.current_risk_class == RiskClass.LOW
    assert after_developer.max_observed_risk_class == RiskClass.HIGH
    assert after_developer.execution_weight == ExecutionWeight.HEAVY


def test_escalate_risk_module_function_delegates_to_the_same_invariant():
    """escalate_risk (MA1.1) is now a thin wrapper over
    EngineeringRoute.with_recomputed_risk (MA2.2) - confirms it still
    behaves identically post-refactor: monotonic max, weight recomputed,
    route.impact left unchanged (no new ImpactVector passed)."""
    route = _route(ChangeKind.TASK, RiskClass.LOW)
    escalated = escalate_risk(route, RiskClass.HIGH, new_reason_codes=["x"])
    assert escalated.max_observed_risk_class == RiskClass.HIGH
    assert escalated.execution_weight == ExecutionWeight.HEAVY
    assert escalated.impact is route.impact
    assert escalated.reason_codes == ["x"]
    assert route.max_observed_risk_class == RiskClass.LOW  # original untouched


def test_reason_codes_accumulate_across_recomputations_not_overwritten():
    route = _route(ChangeKind.TASK, RiskClass.LOW)
    first = route.with_recomputed_risk(RiskClass.MEDIUM, ImpactVector(), ["a"])
    second = first.with_recomputed_risk(RiskClass.HIGH, ImpactVector(), ["b"])
    assert second.reason_codes == ["a", "b"]
