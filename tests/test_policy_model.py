from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.workflow.process_profile import STANDARD_PROFILE
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass


def test_policy_decision_has_exactly_four_distinct_outcomes():
    """MA4.1's own guardrail: ALLOW_SANDBOXED must never collapse into
    ALLOW, and REQUIRE_APPROVAL must never collapse into DENY - each of the
    four values carries materially different downstream control behavior."""
    values = {d.value for d in PolicyDecision}
    assert values == {"allow", "allow_sandboxed", "require_approval", "deny"}
    assert len(PolicyDecision) == 4


def test_action_type_is_a_closed_small_vocabulary():
    values = {a.value for a in ActionType}
    assert values == {
        "read_file", "write_file", "run_command",
        "network_access", "llm_network_access",
        "install_package", "git_read", "git_write", "publish_artifact",
    }


def test_action_request_defaults_are_all_none_or_empty():
    request = ActionRequest(action_type=ActionType.RUN_COMMAND)
    assert request.target is None
    assert request.command is None
    assert request.network_target is None
    assert request.workspace_path is None
    assert request.engineering_route is None
    assert request.process_profile is None
    assert request.metadata == {}


def test_action_request_is_frozen():
    request = ActionRequest(action_type=ActionType.GIT_WRITE, target="main")
    try:
        request.target = "other"
        assert False, "ActionRequest must be immutable"
    except AttributeError:
        pass


def test_action_request_carries_real_engineering_route_and_process_profile():
    route = EngineeringRoute(
        kind=ChangeKind.TASK,
        impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.STANDARD,
    )
    request = ActionRequest(
        action_type=ActionType.WRITE_FILE,
        target="src/main.py",
        workspace_path="/repo",
        engineering_route=route,
        process_profile=STANDARD_PROFILE,
    )
    assert request.engineering_route is route
    assert request.process_profile is STANDARD_PROFILE


def test_policy_result_defaults_are_safe():
    result = PolicyResult(
        decision=PolicyDecision.DENY,
        reason_code="UNKNOWN_ACTION_DENIED",
        explanation="No rule recognized this action; failing closed.",
    )
    assert result.matched_rule is None
    assert result.requires_sandbox is False
    assert result.requires_approval is False


def test_policy_result_is_frozen():
    result = PolicyResult(decision=PolicyDecision.ALLOW, reason_code="X", explanation="y")
    try:
        result.decision = PolicyDecision.DENY
        assert False, "PolicyResult must be immutable"
    except AttributeError:
        pass
