from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision


def test_local_llm_endpoint_is_allowed():
    policy = ExecutionPolicy()
    for target in ("http://localhost:11434/v1", "http://127.0.0.1:11434/v1", "http://my-machine.local:11434/v1"):
        result = policy.evaluate(ActionRequest(action_type=ActionType.LLM_NETWORK_ACCESS, network_target=target))
        assert result.decision == PolicyDecision.ALLOW, target
        assert result.reason_code == "LOCAL_LLM_ENDPOINT_ALLOWED"


def test_non_local_llm_endpoint_is_denied_with_specific_reason_code():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.LLM_NETWORK_ACCESS, network_target="https://api.openai.com/v1",
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "NETWORK_TARGET_DENIED"


def test_local_plain_network_access_is_allowed():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.NETWORK_ACCESS, network_target="http://127.0.0.1:8080",
    ))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "LOCAL_NETWORK_TARGET_ALLOWED"


def test_non_local_plain_network_access_is_denied_with_specific_reason_code():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.NETWORK_ACCESS, network_target="https://registry.npmjs.org",
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "NETWORK_TARGET_DENIED"


def test_network_egress_stage_reuses_the_real_is_local_url_boundary():
    """A malformed/hostname-less target must fail closed (non-local) here
    too, exactly matching kriya.core.llm.is_local_url's own documented
    fail-closed behavior - this stage deliberately reuses that function
    rather than a second, independently-written local-detection check."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.LLM_NETWORK_ACCESS, network_target="not-a-valid-url-at-all",
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "NETWORK_TARGET_DENIED"


def test_network_egress_stage_ignores_unrelated_action_types():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=("mvn", "test")))
    assert result.reason_code not in (
        "LOCAL_LLM_ENDPOINT_ALLOWED", "LOCAL_NETWORK_TARGET_ALLOWED", "NETWORK_TARGET_DENIED",
    )
