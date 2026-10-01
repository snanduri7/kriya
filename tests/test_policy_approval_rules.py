"""MA4.9's own parity requirement (design doc section 35: "only remove
duplicate logic after tests prove parity"): kriya/policy/execution.py's
_check_approval_rules stage must reach the SAME approval verdict MA2
already produces for a given ProcessProfile, tested against the REAL
LIGHT_PROFILE/STANDARD_PROFILE/HEAVY_PROFILE objects
(kriya/workflow/process_profile.py), not a hand-rolled stand-in.
"""
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision
from kriya.workflow.process_profile import HEAVY_PROFILE, LIGHT_PROFILE, STANDARD_PROFILE
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass


def _route(risk_class, weight=ExecutionWeight.STANDARD):
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=risk_class, current_risk_class=risk_class, max_observed_risk_class=risk_class,
        execution_weight=weight,
    )


def test_approval_rules_stage_matches_ma2s_real_process_profiles():
    """Parity check against the actual objects MA2's own approval gate
    (kriya/workflow/workflow.py's process_profile_requires_review) reads
    human_review_required from - LIGHT_PROFILE.human_review_required is
    False, STANDARD/HEAVY are True, exactly like MA2's live behavior."""
    policy = ExecutionPolicy()
    assert LIGHT_PROFILE.human_review_required is False
    assert STANDARD_PROFILE.human_review_required is True
    assert HEAVY_PROFILE.human_review_required is True

    light_result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE, target="src/main.py", process_profile=LIGHT_PROFILE,
    ))
    assert light_result.reason_code != "PROCESS_PROFILE_REQUIRES_APPROVAL"

    for profile in (STANDARD_PROFILE, HEAVY_PROFILE):
        result = policy.evaluate(ActionRequest(
            action_type=ActionType.WRITE_FILE, target="src/main.py", process_profile=profile,
        ))
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL
        assert result.reason_code == "PROCESS_PROFILE_REQUIRES_APPROVAL"
        assert result.requires_approval is True


def test_approval_rules_never_grants_a_bare_allow():
    """A LIGHT profile means 'no opinion here', not 'this is safe' - falls
    through to whatever the backstop would have decided anyway (DENY for a
    WRITE_FILE with no workspace_path), never an explicit ALLOW from this
    stage itself."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE, target="src/main.py", process_profile=LIGHT_PROFILE,
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "DEFAULT_UNKNOWN_ACTION_DENIED"


def test_high_risk_route_requires_approval_even_without_a_profile():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE, target="src/main.py",
        engineering_route=_route(RiskClass.HIGH),
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "HIGH_RISK_REQUIRES_APPROVAL"


def test_low_or_medium_risk_route_has_no_opinion():
    policy = ExecutionPolicy()
    for risk in (RiskClass.LOW, RiskClass.MEDIUM):
        result = policy.evaluate(ActionRequest(
            action_type=ActionType.WRITE_FILE, target="src/main.py",
            engineering_route=_route(risk),
        ))
        assert result.reason_code != "HIGH_RISK_REQUIRES_APPROVAL"


def test_process_profile_takes_precedence_over_a_non_high_route():
    """Both fields may be present at once (a real WorkflowControlContext
    always carries both together) - process_profile is checked first."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE, target="src/main.py",
        process_profile=STANDARD_PROFILE, engineering_route=_route(RiskClass.LOW),
    ))
    assert result.reason_code == "PROCESS_PROFILE_REQUIRES_APPROVAL"


def test_stages_that_already_own_their_action_type_are_unaffected():
    """RUN_COMMAND is fully owned by stage 6 (MA4.4) regardless of profile -
    this stage never even runs for it."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.RUN_COMMAND, command=("mvn", "install"), process_profile=HEAVY_PROFILE,
    ))
    assert result.reason_code == "COMMAND_NOT_ALLOWLISTED"


def test_publish_artifact_gets_approval_rules_coverage_despite_no_owning_stage():
    """PUBLISH_ARTIFACT has no dedicated stage (MA5's ArtifactRegistry
    territory) - this stage is the only thing standing between it and the
    generic backstop when a profile/route is available."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.PUBLISH_ARTIFACT, target="my-artifact", process_profile=HEAVY_PROFILE,
    ))
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "PROCESS_PROFILE_REQUIRES_APPROVAL"
