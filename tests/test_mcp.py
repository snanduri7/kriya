import os
import sys

import pytest

from kriya.core.kernel import Kernel
from kriya.mcp.mcp import MCPClient, MCPManager
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import MCPToolIdentity, compute_mcp_schema_digest


@pytest.mark.asyncio
async def test_mcp_client_handshake_and_call():
    # Setup mock server path
    mock_server_path = os.path.join(os.path.dirname(__file__), "mock_mcp_server.py")
    
    # Spawn client using sys.executable (our active test python env)
    client = MCPClient(
        name="mock_svc",
        command=sys.executable,
        args=[mock_server_path]
    )
    
    await client.start()
    try:
        # Check list tools
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo_test"
        
        # Check call tool
        res = await client.call_tool("echo_test", {"message": "hello mcp"})
        content = res.get("content", [])
        assert len(content) == 1
        assert content[0]["text"] == "Echo: hello mcp"
    finally:
        await client.stop()

@pytest.mark.asyncio
async def test_mcp_manager_integration():
    """TOOL-002 P1: a plain MCPManager (bare ExecutionPolicy(), nothing
    pre-approved - today's real production default, since no operator-
    facing MCP approval/capability mechanism exists yet) must DENY the
    tool call, not execute it - this replaces the pre-TOOL-002 assertion
    that this call simply succeeded, which is no longer Kriya's actual,
    intended behavior for an unauthorized MCP invocation."""
    mock_server_path = os.path.join(os.path.dirname(__file__), "mock_mcp_server.py")
    kernel = Kernel()

    # Set up config for the manager
    mcp_config = {
        "mock_svc": {
            "command": sys.executable,
            "args": [mock_server_path]
        }
    }

    manager = MCPManager(kernel)
    await manager.start_all(mcp_config)
    try:
        # Verify tool was registered dynamically into the Kernel Registry
        registered_tools = kernel.registry.list_components("tool")
        assert "mock_svc_echo_test" in registered_tools

        # TOOL-002 P1: registration alone never grants invocation authority -
        # a default (nothing pre-approved) MCPManager must deny the call.
        tool = kernel.registry.get("tool", "mock_svc_echo_test")
        with pytest.raises(Exception) as exc_info:
            await tool.execute(message="kernel call")
        assert isinstance(exc_info.value.__cause__, PolicyDeniedError)

        # Test schema validation of dynamic wrapper (fails before the policy
        # check is ever reached, at BaseTool.execute()'s own arg validation)
        with pytest.raises(Exception):
            # Missing required 'message' argument
            await tool.execute()
    finally:
        await manager.shutdown_all()


@pytest.mark.asyncio
async def test_mcp_manager_integration_allowed_with_pre_approved_identity():
    """TOOL-002 P1: the SAME call succeeds once its exact structured
    identity (server + tool name + schema digest) is explicitly
    pre-approved - proving the ALLOW mechanism itself is sound, not just
    that the default denies."""
    mock_server_path = os.path.join(os.path.dirname(__file__), "mock_mcp_server.py")
    kernel = Kernel()
    mcp_config = {"mock_svc": {"command": sys.executable, "args": [mock_server_path]}}

    # Compute the expected identity ahead of time from the same fixture's
    # known schema shape (mirrors how an operator-facing mechanism would
    # eventually pre-approve a specific, already-known tool identity).
    probe = MCPClient(name="mock_svc", command=sys.executable, args=[mock_server_path])
    await probe.start()
    tools = await probe.list_tools()
    await probe.stop()
    digest = compute_mcp_schema_digest(tools[0].get("inputSchema", {}))
    approved = frozenset({MCPToolIdentity(server_identity="mock_svc", tool_name="echo_test", schema_digest=digest)})

    manager = MCPManager(kernel, execution_policy=ExecutionPolicy(approved_mcp_tool_identities=approved))
    await manager.start_all(mcp_config)
    try:
        tool = kernel.registry.get("tool", "mock_svc_echo_test")
        result = await tool.execute(message="kernel call")
        assert result == "Echo: kernel call"
    finally:
        await manager.shutdown_all()

def test_kriya_local_mcp_tools(tmp_path):
    from kriya.mcp.server import parse_ast, search_code
    
    # Create test python file
    test_file = tmp_path / "hello.py"
    test_file.write_text("class MyTest:\n    def run_test(self, x):\n        pass\n\ndef add(a, b):\n    return a + b\n")
    
    # Test parse_ast
    result_ast = parse_ast(str(test_file))
    assert "MyTest" in result_ast
    assert "run_test" in result_ast
    assert "add" in result_ast
    
    # Test search_code
    result_search = search_code(pattern="def ", path=str(tmp_path), file_glob="*.py")
    assert "hello.py:2" in result_search
    assert "hello.py:5" in result_search
