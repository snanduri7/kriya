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
