"""MA4.15: ExecutionPolicy.__init__'s optional sensitive_path_patterns
override - the additive constructor change that lets a real caller with
config access (WorkflowEngine) supply AutonomyConfig.sensitive_paths
instead of drifting from the hardcoded default list."""

from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision


def test_default_construction_still_uses_the_hardcoded_list():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="/home/user/.ssh/id_rsa"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "SENSITIVE_PATH_DENIED"


def test_custom_patterns_replace_rather_than_extend_the_default_list():
    """An explicit override is authoritative - a path only the DEFAULT list
    would have caught is no longer denied once a custom list is supplied,
    proving this isn't silently merged with the hardcoded set."""
    policy = ExecutionPolicy(sensitive_path_patterns=[r"totally-custom-pattern"])
    result = policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="/home/user/.ssh/id_rsa"))
    assert result.decision != PolicyDecision.DENY or result.reason_code != "SENSITIVE_PATH_DENIED"


def test_custom_patterns_actually_take_effect():
    policy = ExecutionPolicy(sensitive_path_patterns=[r"totally-custom-pattern"])
    result = policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="/repo/totally-custom-pattern/x"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "SENSITIVE_PATH_DENIED"


def test_empty_list_falls_back_to_the_default_rather_than_disabling_the_rule():
    """An empty override is treated as 'no override supplied', not 'deny
    nothing' - a caller can't accidentally disable sensitive-path
    protection by passing an empty list."""
    policy = ExecutionPolicy(sensitive_path_patterns=[])
    result = policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="/home/user/.ssh/id_rsa"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "SENSITIVE_PATH_DENIED"


def test_other_stages_are_unaffected_by_the_override():
    policy = ExecutionPolicy(sensitive_path_patterns=[r"custom"])
    result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=("sudo", "rm", "-rf", "/")))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "COMMAND_SUDO_DENIED"
