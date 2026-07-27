import os
import sys
import pytest

# Ensure workspace root is in path to import plugins
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from plugins.core_tools import FilesystemTool, ShellTool, GitTool
from kriya.tools.tool import ToolExecutionError

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
