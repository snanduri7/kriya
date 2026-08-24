from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision


def test_sensitive_paths_deny_regardless_of_workspace_context():
    policy = ExecutionPolicy()
    sensitive = [
        "~/.ssh/id_rsa",
        "/home/user/.ssh/authorized_keys",
        "/home/user/.aws/credentials",
        "/home/user/.kube/config",
        "/home/user/.gnupg/secring.gpg",
        "/repo/.env",
        "/repo/config/credentials.yaml",
        "/repo/secrets.json",
        "/repo/db_password.txt",
    ]
    for target in sensitive:
        for action_type in (ActionType.READ_FILE, ActionType.WRITE_FILE):
            result = policy.evaluate(ActionRequest(action_type=action_type, target=target, workspace_path="/repo"))
            assert result.decision == PolicyDecision.DENY, (action_type, target)
            assert result.reason_code == "SENSITIVE_PATH_DENIED"


def test_sensitive_path_denies_even_without_workspace_context():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="~/.ssh/id_rsa"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "SENSITIVE_PATH_DENIED"


def test_write_within_workspace_is_allowed():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE, target="/repo/src/main.py", workspace_path="/repo",
    ))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "PATH_WITHIN_WORKSPACE_ALLOWED"


def test_write_within_kriya_worktree_subdirectory_is_allowed():
    """.kriya/worktree is always a subpath of the repo root the caller
    passes as workspace_path - no special-casing needed, containment alone
    covers it (design doc section 20)."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE,
        target="/repo/.kriya/worktree/src/main.py",
        workspace_path="/repo",
    ))
    assert result.decision == PolicyDecision.ALLOW


def test_write_outside_workspace_is_denied():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE, target="/etc/passwd", workspace_path="/repo",
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "PATH_OUTSIDE_WORKSPACE_DENIED"


def test_a_sibling_directory_with_a_shared_prefix_is_not_treated_as_inside():
    """/repo-other must not match workspace_path='/repo' via naive string
    prefix - the containment check requires a real path-separator boundary."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.WRITE_FILE, target="/repo-other/src/main.py", workspace_path="/repo",
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "PATH_OUTSIDE_WORKSPACE_DENIED"


def test_read_without_workspace_context_falls_through_to_default_allow():
    """No workspace_path means containment can't be checked - falls through
    to MA4.2's own default-allow for inherently read-only actions."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.READ_FILE, target="/repo/README.md"))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "DEFAULT_READ_ONLY_ALLOWED"


def test_write_without_workspace_context_falls_through_to_default_deny():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.WRITE_FILE, target="/repo/README.md"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "DEFAULT_UNKNOWN_ACTION_DENIED"


def test_filesystem_stage_ignores_non_file_action_types():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=("mvn", "test")))
    assert result.reason_code not in ("SENSITIVE_PATH_DENIED", "PATH_WITHIN_WORKSPACE_ALLOWED", "PATH_OUTSIDE_WORKSPACE_DENIED")
