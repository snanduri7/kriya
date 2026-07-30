import os
import sys

import pytest

from kriya.core.kernel import Kernel
from kriya.mcp.mcp import MCPClient, MCPManager


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
        
        # Execute tool through the Kernel Tool Registry
        tool = kernel.registry.get("tool", "mock_svc_echo_test")
        result = await tool.execute(message="kernel call")
        assert result == "Echo: kernel call"
        
        # Test schema validation of dynamic wrapper
        with pytest.raises(Exception):
            # Missing required 'message' argument
            await tool.execute()
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
