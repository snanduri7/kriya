from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision


def test_allowlisted_build_test_commands_are_sandboxed():
    policy = ExecutionPolicy()
    allowlisted = [
        ("mvn", "compile"),
        ("mvn", "clean", "compile", "-Dmaven.compiler.showWarnings=true"),
        ("mvn", "test"),
        ("mvn", "clean", "test"),
        ("mvn", "verify"),
        ("gradle", "test"),
        ("./gradlew", "test"),
        ("pytest",),
        ("pytest", "-x", "tests/"),
        ("python", "-m", "pytest"),
        ("npm", "test"),
        ("npm", "run", "build"),
        ("go", "test"),
        ("cargo", "test"),
    ]
    for command in allowlisted:
        result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=command))
        assert result.decision == PolicyDecision.ALLOW_SANDBOXED, command
        assert result.reason_code == "COMMAND_ALLOWLISTED"
        assert result.requires_sandbox is True


def test_non_allowlisted_commands_deny_with_specific_reason_code():
    policy = ExecutionPolicy()
    not_allowlisted = [
        ("mvn", "install"),
        ("mvn", "deploy"),
        ("pip", "install", "-q", "-r", "requirements.txt"),
        ("javap", "-public", "-classpath", "cp", "Foo"),
        ("bundle", "install", "--path", "vendor/bundle"),
        ("rspec",),
    ]
    for command in not_allowlisted:
        result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=command))
        assert result.decision == PolicyDecision.DENY, command
        assert result.reason_code == "COMMAND_NOT_ALLOWLISTED"


def test_sudo_is_denied_unconditionally():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=("sudo", "rm", "-rf", "/")))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "COMMAND_SUDO_DENIED"


def test_shell_wrapper_with_dash_c_requires_approval():
    policy = ExecutionPolicy()
    for shell in ("sh", "bash", "zsh", "ksh", "dash"):
        result = policy.evaluate(ActionRequest(
            action_type=ActionType.RUN_COMMAND, command=(shell, "-c", "echo hi"),
        ))
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL, shell
        assert result.reason_code == "COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL"
        assert result.requires_approval is True


def test_shell_invoked_without_dash_c_is_not_treated_as_a_wrapper():
    """A bare 'bash script.sh' (no -c) doesn't get arbitrary nested-command
    execution the way '-c' does - falls through to the ordinary allowlist
    check instead of the wrapper rule."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=("bash", "script.sh")))
    assert result.reason_code != "COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL"


def test_command_matching_is_by_executable_basename_not_full_path():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(
        action_type=ActionType.RUN_COMMAND, command=("/usr/local/bin/mvn", "test"),
    ))
    assert result.decision == PolicyDecision.ALLOW_SANDBOXED


def test_git_read_action_type_is_untouched_by_command_allowlist_stage():
    """Git commands are governed by GIT_READ/GIT_WRITE (MA4.2's own
    default-allow for reads; MA4.8's future git-write rules), never routed
    through the RUN_COMMAND command-allowlist stage this task added."""
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.GIT_READ, command=("git", "status")))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "DEFAULT_READ_ONLY_ALLOWED"
