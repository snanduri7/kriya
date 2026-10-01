"""MA4.13: PolicyDeniedError (kriya/policy/errors.py)."""

from kriya.policy.errors import PolicyDeniedError
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult


def test_policy_denied_error_carries_request_and_result():
    request = ActionRequest(action_type=ActionType.INSTALL_PACKAGE, target="some-package")
    result = PolicyResult(
        decision=PolicyDecision.DENY, reason_code="UNKNOWN_PACKAGE_SOURCE_DENIED", explanation="denied for testing",
    )
    error = PolicyDeniedError(request=request, result=result)
    assert error.request is request
    assert error.result is result


def test_policy_denied_error_message_includes_reason_code_and_explanation():
    request = ActionRequest(action_type=ActionType.RUN_COMMAND, command=("rm", "-rf", "/"))
    result = PolicyResult(
        decision=PolicyDecision.DENY, reason_code="COMMAND_NOT_ALLOWLISTED", explanation="not on the allowlist",
    )
    error = PolicyDeniedError(request=request, result=result)
    message = str(error)
    assert "run_command" in message
    assert "COMMAND_NOT_ALLOWLISTED" in message
    assert "not on the allowlist" in message


def test_policy_denied_error_is_a_real_exception():
    request = ActionRequest(action_type=ActionType.WRITE_FILE, target="a.py")
    result = PolicyResult(decision=PolicyDecision.DENY, reason_code="X", explanation="y")
    try:
        raise PolicyDeniedError(request=request, result=result)
    except PolicyDeniedError as e:
        assert e.result.decision == PolicyDecision.DENY
    else:
        raise AssertionError("expected PolicyDeniedError to be raisable/catchable")
