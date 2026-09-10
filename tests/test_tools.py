import os
import sys

import pytest

# Ensure workspace root is in path to import plugins
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from kriya.tools.tool import ToolExecutionError
from plugins.core_tools import FilesystemTool, GitTool, ShellTool


@pytest.mark.asyncio
async def test_filesystem_tool(tmp_path):
    tool = FilesystemTool()
    
    # 1. Test Write
    test_file = tmp_path / "hello.txt"
    res_write = await tool.execute(
        operation="write",
        path=str(test_file),
        content="Hello Kriya Platform!"
    )
    assert "Successfully wrote" in res_write
    assert os.path.exists(test_file)
    
    # 2. Test Read
    res_read = await tool.execute(
        operation="read",
        path=str(test_file)
    )
    assert res_read == "Hello Kriya Platform!"
    
    # 3. Test List
    res_list = await tool.execute(
        operation="list",
        path=str(tmp_path)
    )
    assert "hello.txt" in res_list

@pytest.mark.asyncio
async def test_filesystem_tool_errors(tmp_path):
    tool = FilesystemTool()
    
    # Test read non-existent file
    with pytest.raises(ToolExecutionError):
        await tool.execute(operation="read", path=str(tmp_path / "missing.txt"))
        
    # Test missing content for write
    with pytest.raises(ToolExecutionError):
        await tool.execute(operation="write", path=str(tmp_path / "test.txt"))

@pytest.mark.asyncio
async def test_shell_tool():
    tool = ShellTool()

    # Run a simple echo command
    res = await tool.execute(command="echo 'Hello CLI'")
    assert res["exit_code"] == 0
    assert "Hello CLI" in res["stdout"]

def test_shell_tool_requires_confirmation():
    assert ShellTool().requires_confirmation is True

def test_other_tools_do_not_require_confirmation():
    assert FilesystemTool().requires_confirmation is False
    assert GitTool().requires_confirmation is False

@pytest.mark.asyncio
async def test_shell_tool_env_is_restricted_by_default(monkeypatch):
    monkeypatch.setenv("KRIYA_TEST_SECRET", "super-secret-value")
    tool = ShellTool()

    res = await tool.execute(command="echo $KRIYA_TEST_SECRET")
    assert res["stdout"].strip() == ""

@pytest.mark.asyncio
async def test_shell_tool_sudo_is_denied_before_execution(tmp_path):
    """POL-001: ShellTool previously executed args.command with zero
    ExecutionPolicy consultation - reuses the same always-on
    enforce_hard_invariants hard stop kriya/tools/validate.py's own command
    execution already applies. A sentinel file proves the command never
    actually ran (not just that the call raised)."""
    tool = ShellTool()
    sentinel = tmp_path / "sudo_ran.txt"
    # BaseTool.execute() wraps every exception from _run() into
    # ToolExecutionError (pre-existing framework behavior, not specific to
    # this check) - the original PolicyDeniedError's message text survives
    # inside it via `from e`.
    with pytest.raises(ToolExecutionError, match="COMMAND_SUDO_DENIED"):
        await tool.execute(command=f"sudo touch {sentinel}")
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_shell_tool_ordinary_command_unaffected_by_policy_check():
    tool = ShellTool()
    res = await tool.execute(command="echo 'still works'")
    assert res["exit_code"] == 0
    assert "still works" in res["stdout"]


@pytest.mark.asyncio
async def test_shell_tool_unparseable_command_does_not_block_execution():
    """A malformed-quoting string must not silently gain a free pass around
    other enforcement - it degrades to a single opaque token the allowlist
    stage has no opinion on, rather than blocking a command this check
    cannot even parse."""
    tool = ShellTool()
    res = await tool.execute(command="echo 'unterminated")
    assert isinstance(res["exit_code"], int)


@pytest.mark.asyncio
async def test_shell_tool_env_full_when_sandbox_disabled(monkeypatch):
    from kriya.config import AppConfig
    monkeypatch.setenv("KRIYA_TEST_SECRET", "super-secret-value")

    cfg = AppConfig()
    cfg.autonomy.sandbox_execution = False
    tool = ShellTool(autonomy_cfg=cfg.autonomy)

    res = await tool.execute(command="echo $KRIYA_TEST_SECRET")
    assert res["stdout"].strip() == "super-secret-value"

@pytest.mark.asyncio
async def test_git_tool(tmp_path):
    # Initialize a git repository in tmp_path
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)

    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        tool = GitTool()
        res = await tool.execute(subcommand="status")
        assert "On branch" in res or "No commits yet" in res
    finally:
        os.chdir(old_cwd)


# --- POL-001-P2: GitTool's mutating boundary (`git commit`) ---
# status/diff/log/branch(list)/blame are GIT_READ-shaped and never gated as
# GIT_WRITE; commit is the only currently-supported mutating subcommand.

def _init_git_repo(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "a.txt").write_text("hello")
    subprocess.run(["git", "add", "a.txt"], cwd=str(tmp_path), capture_output=True)


def _git_log(tmp_path):
    import subprocess
    return subprocess.run(
        ["git", "log", "--oneline"], cwd=str(tmp_path), capture_output=True, text=True,
    ).stdout


@pytest.mark.asyncio
async def test_git_tool_read_only_command_remains_executable_under_enforce(tmp_path):
    """status/diff/log/branch/blame are GIT_READ-shaped - never gated as
    GIT_WRITE, unaffected regardless of execution_policy.mode."""
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)

    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        from kriya.config.config import ExecutionPolicyConfig
        tool = GitTool(execution_policy_cfg=ExecutionPolicyConfig(mode="enforce"))
        res = await tool.execute(subcommand="status")
        assert "On branch" in res or "No commits yet" in res
    finally:
        os.chdir(old_cwd)


@pytest.mark.asyncio
async def test_git_tool_commit_allow_executes_under_audit_default(tmp_path):
    """audit mode (today's default, no execution_policy_cfg) preserves
    current behavior - the commit still executes and actually lands."""
    _init_git_repo(tmp_path)
    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        tool = GitTool()
        await tool.execute(subcommand="commit", message="pol001p2 audit commit")
        assert "pol001p2 audit commit" in _git_log(tmp_path)
    finally:
        os.chdir(old_cwd)


@pytest.mark.asyncio
async def test_git_tool_commit_require_approval_fails_closed_under_enforce(tmp_path):
    """An ordinary `git commit` reaches _check_git_destructive's own
    catch-all rule (GIT_WRITE_REQUIRES_APPROVAL) - no approval_callback is
    reachable at this plugin-tool boundary, so under mode="enforce" it
    fails closed (Invariant 4) rather than executing. The subprocess must
    never start - nothing lands in git."""
    from kriya.config.config import ExecutionPolicyConfig

    _init_git_repo(tmp_path)
    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        tool = GitTool(execution_policy_cfg=ExecutionPolicyConfig(mode="enforce"))
        with pytest.raises(ToolExecutionError, match="GIT_WRITE_REQUIRES_APPROVAL"):
            await tool.execute(subcommand="commit", message="should never land")
        assert "should never land" not in _git_log(tmp_path)
    finally:
        os.chdir(old_cwd)


@pytest.mark.asyncio
async def test_git_tool_commit_deny_never_starts_subprocess(tmp_path):
    """No real rule reaches DENY for a plain commit today (_check_git_
    destructive's DENY codes are all push/config/remote-mutation specific),
    so this is forced via a monkeypatched evaluate() - but routed through
    the real `execute()` -> `_run()` path, not the helper directly, so it
    actually proves the required claim: the subprocess never starts and
    nothing lands in git, not just that a helper function raises."""
    from kriya.config.config import ExecutionPolicyConfig
    from kriya.policy.model import PolicyDecision, PolicyResult

    _init_git_repo(tmp_path)
    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        tool = GitTool(execution_policy_cfg=ExecutionPolicyConfig(mode="enforce"))
        tool._execution_policy.evaluate = lambda request: PolicyResult(
            decision=PolicyDecision.DENY, reason_code="TEST_FORCED_DENY", explanation="forced for test",
        )
        with pytest.raises(ToolExecutionError, match="TEST_FORCED_DENY"):
            await tool.execute(subcommand="commit", message="should never land - forced deny")
        assert "should never land" not in _git_log(tmp_path)
    finally:
        os.chdir(old_cwd)


@pytest.mark.asyncio
async def test_git_tool_commit_audit_mode_evaluates_but_never_blocks(tmp_path):
    """Explicit mode="audit" (not just the no-cfg default) also preserves
    current behavior - evaluation happens (auditable), execution is never
    blocked on the result."""
    from kriya.config.config import ExecutionPolicyConfig

    _init_git_repo(tmp_path)
    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        tool = GitTool(execution_policy_cfg=ExecutionPolicyConfig(mode="audit"))
        await tool.execute(subcommand="commit", message="pol001p2 explicit audit commit")
        assert "pol001p2 explicit audit commit" in _git_log(tmp_path)
    finally:
        os.chdir(old_cwd)


# --- POL-001-P2: ShellTool shell-wrapper REQUIRE_APPROVAL gap (Step 4 finding) ---

@pytest.mark.asyncio
async def test_shell_tool_shell_wrapper_require_approval_fails_closed_under_enforce():
    """enforce_hard_invariants (P1) never acts on REQUIRE_APPROVAL - a raw
    `bash -c "..."` invocation reaches COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL,
    which P1's wiring silently let through. No approval_callback is
    reachable at this boundary, so under mode="enforce" this now fails
    closed, mirroring GitTool's own commit gate."""
    from kriya.config.config import ExecutionPolicyConfig

    tool = ShellTool(execution_policy_cfg=ExecutionPolicyConfig(mode="enforce"))
    with pytest.raises(ToolExecutionError, match="COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL"):
        await tool.execute(command="bash -c 'echo hi'")


@pytest.mark.asyncio
async def test_shell_tool_shell_wrapper_still_allowed_under_audit_default():
    """No execution_policy_cfg (today's real default) - preserves P1's
    existing behavior exactly, `bash -c` still executes."""
    tool = ShellTool()
    res = await tool.execute(command="bash -c 'echo hi'")
    assert res["exit_code"] == 0
    assert "hi" in res["stdout"]


@pytest.mark.asyncio
async def test_shell_tool_sudo_still_denied_under_enforce_mode_too():
    """P1 regression check: the unconditional hard-invariant sudo block
    must keep firing even when the new mode-gated REQUIRE_APPROVAL check is
    also active under mode="enforce" - the two checks are additive, neither
    should suppress the other."""
    from kriya.config.config import ExecutionPolicyConfig

    tool = ShellTool(execution_policy_cfg=ExecutionPolicyConfig(mode="enforce"))
    with pytest.raises(ToolExecutionError, match="COMMAND_SUDO_DENIED"):
        await tool.execute(command="sudo echo hi")


# --- POL-001-P4: decisive audit-vs-enforce differential evidence -----------
# Same ActionType (RUN_COMMAND), same shell-wrapper command, same production
# path (BaseTool.execute -> ShellTool._run -> ExecutionPolicy.evaluate),
# config loaded through the REAL load_config() (not a bare
# ExecutionPolicyConfig(...) construction, unlike the P2 tests above) - only
# execution_policy.mode differs between the two halves of this test. Proves,
# with a filesystem sentinel (not just an exception message), that audit
# mode consults the policy but still lets the subprocess run, while enforce
# mode blocks BEFORE the subprocess ever starts.

def _write_execution_policy_kriya_yaml(cfg_dir, mode: str) -> str:
    cfg_dir.mkdir()
    (cfg_dir / "kriya.yaml").write_text(
        f"execution_policy:\n  enabled: true\n  mode: {mode}\n"
        f"paths:\n  logs: {cfg_dir}/logs\n  memory: {cfg_dir}/memory\n  skills: {cfg_dir}/skills\n"
    )
    return str(cfg_dir / "kriya.yaml")


@pytest.mark.asyncio
async def test_shell_tool_wrapper_audit_vs_enforce_differential_through_load_config(tmp_path):
    from kriya.config.config import load_config
    from kriya.policy.model import PolicyDecision

    sentinel = tmp_path / "wrapper_ran.txt"
    command = f"bash -c 'touch {sentinel}'"

    # --- audit half: consults the policy, does not block ---
    audit_cfg = load_config(_write_execution_policy_kriya_yaml(tmp_path / "audit_cfg", "audit"))
    assert audit_cfg.execution_policy.mode == "audit"
    audit_tool = ShellTool(autonomy_cfg=audit_cfg.autonomy, execution_policy_cfg=audit_cfg.execution_policy)
    captured_audit = []
    real_eval_audit = audit_tool._execution_policy.evaluate

    def spy_audit(request):
        result = real_eval_audit(request)
        captured_audit.append(result)
        return result

    audit_tool._execution_policy.evaluate = spy_audit

    res = await audit_tool.execute(command=command)
    assert res["exit_code"] == 0
    assert sentinel.exists(), "audit mode must not block the real subprocess"
    assert len(captured_audit) == 1
    assert captured_audit[0].decision == PolicyDecision.REQUIRE_APPROVAL
    assert captured_audit[0].reason_code == "COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL"

    sentinel.unlink()

    # --- enforce half: exact same command, only mode differs ---
    enforce_cfg = load_config(_write_execution_policy_kriya_yaml(tmp_path / "enforce_cfg", "enforce"))
    assert enforce_cfg.execution_policy.mode == "enforce"
    enforce_tool = ShellTool(autonomy_cfg=enforce_cfg.autonomy, execution_policy_cfg=enforce_cfg.execution_policy)
    captured_enforce = []
    real_eval_enforce = enforce_tool._execution_policy.evaluate

    def spy_enforce(request):
        result = real_eval_enforce(request)
        captured_enforce.append(result)
        return result

    enforce_tool._execution_policy.evaluate = spy_enforce

    with pytest.raises(ToolExecutionError, match="COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL"):
        await enforce_tool.execute(command=command)
    assert not sentinel.exists(), "enforce mode must block before the subprocess ever starts"
    # Two evaluate() calls, not one: enforce_hard_invariants (P1, unconditional)
    # evaluates first and does not raise on REQUIRE_APPROVAL; ShellTool's own
    # P2 mode-gated block evaluates a second time and raises on that result -
    # this is real production control flow, not a test artifact.
    assert len(captured_enforce) == 2
    for result in captured_enforce:
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL
        assert result.reason_code == "COMMAND_SHELL_WRAPPER_REQUIRES_APPROVAL"


@pytest.mark.asyncio
async def test_git_tool_commit_audit_vs_enforce_differential_through_load_config(tmp_path):
    """Same differential shape as the ShellTool test above, for GitTool's
    other genuinely mode-dependent path, in an isolated TEMPORARY git repo
    (never a user repository)."""
    import subprocess

    from kriya.config.config import load_config
    from kriya.policy.model import PolicyDecision

    async def run_phase(mode: str):
        repo_dir = tmp_path / f"repo_{mode}"
        repo_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo_dir), check=True)
        (repo_dir / "f.txt").write_text("evidence")
        subprocess.run(["git", "add", "f.txt"], cwd=str(repo_dir), check=True)

        cfg = load_config(_write_execution_policy_kriya_yaml(tmp_path / f"cfg_{mode}", mode))
        assert cfg.execution_policy.mode == mode
        tool = GitTool(execution_policy_cfg=cfg.execution_policy)
        captured = []
        real_eval = tool._execution_policy.evaluate

        def spy(request):
            result = real_eval(request)
            captured.append(result)
            return result

        tool._execution_policy.evaluate = spy

        old_cwd = os.getcwd()
        os.chdir(str(repo_dir))
        error = None
        try:
            await tool.execute(subcommand="commit", message="pol001p4 differential commit")
        except ToolExecutionError as e:
            error = e
        finally:
            os.chdir(old_cwd)

        return captured, error, _git_log(repo_dir)

    captured_audit, error_audit, log_audit = await run_phase("audit")
    assert len(captured_audit) == 1
    assert captured_audit[0].decision == PolicyDecision.REQUIRE_APPROVAL
    assert captured_audit[0].reason_code == "GIT_WRITE_REQUIRES_APPROVAL"
    assert error_audit is None
    assert "pol001p4 differential commit" in log_audit

    captured_enforce, error_enforce, log_enforce = await run_phase("enforce")
    assert len(captured_enforce) == 1
    assert captured_enforce[0].decision == PolicyDecision.REQUIRE_APPROVAL
    assert captured_enforce[0].reason_code == "GIT_WRITE_REQUIRES_APPROVAL"
    assert error_enforce is not None
    assert "GIT_WRITE_REQUIRES_APPROVAL" in str(error_enforce)
    assert "pol001p4 differential commit" not in log_enforce
