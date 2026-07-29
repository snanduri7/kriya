import os
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional, Type
from pydantic import BaseModel, Field, create_model

from kriya.core.kernel import Kernel
from kriya.tools.tool import BaseTool, ToolExecutionError

logger = logging.getLogger(__name__)

# =====================================================================
# 1. Native Stdio JSON-RPC 2.0 MCP Client
# =====================================================================

class MCPClient:
    """Client for communicating with Model Context Protocol (MCP) servers via stdio."""

    def __init__(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        
        self._process: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._request_id = 0
        self._is_running = False

    async def start(self) -> None:
        """Spawn the MCP server process and start listening to stdout."""
        if self._is_running:
            return

        logger.info(f"Starting MCP server '{self.name}' using command: {self.command} {self.args}")
        
        # Merge current process environment with config env
        full_env = {**os.environ, **self.env}
        
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env
            )
            self._is_running = True
            
            # Start background stdout reader
            self._read_task = asyncio.create_task(self._read_stdout())
            # Start background stderr logging reader
            self._read_stderr_task = asyncio.create_task(self._read_stderr())

            # Perform MCP Handshake
            await self._handshake()
            
        except Exception as e:
            logger.error(f"Failed to start MCP server '{self.name}': {e}", exc_info=True)
            await self.stop()
            raise e

    async def stop(self) -> None:
        """Terminate the server process and cleanup resources."""
        if not self._is_running:
            return
        
        self._is_running = False
        logger.info(f"Stopping MCP server '{self.name}'...")
        
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if hasattr(self, "_read_stderr_task") and self._read_stderr_task:
            self._read_stderr_task.cancel()
            try:
                await self._read_stderr_task
            except asyncio.CancelledError:
                pass
            
        if self._process:
            try:
                self._process.terminate()
                await self._process.wait()
            except Exception as e:
                logger.debug(f"Failed to cleanly terminate MCP server '{self.name}' process: {e}")
                
        # Resolve all pending requests as failed
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.set_exception(RuntimeError("MCP server stopped."))
        self._pending_requests.clear()

    async def _handshake(self) -> None:
        """Performs Kriya-MCP initialization handshake."""
        # 1. Initialize Request
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {}
            },
            "clientInfo": {
                "name": "kriya",
                "version": "0.1.0"
            }
        }
        resp = await self._send_request("initialize", init_params)
        logger.debug(f"Handshake initialized response: {resp}")
        
        # 2. Initialized Notification (no response expected)
        await self._send_notification("notifications/initialized", {})

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for the response."""
        if not self._is_running or not self._process or not self._process.stdin:
            raise RuntimeError("MCP Client is not running.")

        self._request_id += 1
        req_id = self._request_id
        
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future
        
        try:
            message = json.dumps(payload) + "\n"
            self._process.stdin.write(message.encode("utf-8"))
            await self._process.stdin.drain()
            return await future
        finally:
            self._pending_requests.pop(req_id, None)

    async def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, does not block for response)."""
        if not self._is_running or not self._process or not self._process.stdin:
            return

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        message = json.dumps(payload) + "\n"
        self._process.stdin.write(message.encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_stdout(self) -> None:
        """Reads newline-separated JSON-RPC messages from server stdout."""
        while self._is_running and self._process and self._process.stdout:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    break
                
                msg_str = line.decode("utf-8").strip()
                if not msg_str:
                    continue
                
                try:
                    msg = json.loads(msg_str)
                except json.JSONDecodeError:
                    logger.warning(f"MCP server '{self.name}' sent invalid JSON: {msg_str}")
                    continue

                if "id" in msg:
                    req_id = msg["id"]
                    future = self._pending_requests.pop(req_id, None)
                    if future and not future.done():
                        if "error" in msg:
                            future.set_exception(RuntimeError(f"Server Error: {msg['error']}"))
                        else:
                            future.set_result(msg.get("result", {}))
                else:
                    # Parse server notifications/requests if needed
                    logger.debug(f"MCP server '{self.name}' sent notification/request: {msg}")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in MCP client reader loop: {e}")
                break

    async def _read_stderr(self) -> None:
        """Log MCP server stderr for diagnostic health reviews."""
        while self._is_running and self._process and self._process.stderr:
            try:
                line = await self._process.stderr.readline()
                if not line:
                    break
                logger.warning(f"[MCP Server: {self.name}] {line.decode('utf-8').strip()}")
            except Exception as e:
                logger.debug(f"MCP server '{self.name}' stderr reader loop exiting: {e}")
                break

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List all tools exposed by the server."""
        resp = await self._send_request("tools/list", {})
        return resp.get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Request the server to execute a specific tool with arguments."""
        resp = await self._send_request("tools/call", {"name": name, "arguments": arguments})
        return resp

# =====================================================================
# 2. Dynamic Tool Wrapper
# =====================================================================

class MCPTool(BaseTool):
    """Kriya BaseTool implementation wrapping a dynamically fetched MCP Tool schema."""

    def __init__(self, mcp_client: MCPClient, tool_meta: Dict[str, Any]) -> None:
        self.client = mcp_client
        self._name = f"{mcp_client.name}_{tool_meta['name']}"
        self._description = tool_meta.get("description", "MCP Dynamic Tool")
        
        # Build arguments schema dynamically using Pydantic create_model
        input_schema = tool_meta.get("inputSchema", {})
        self._schema = self._build_pydantic_schema(input_schema)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return self._schema

    async def _run(self, args: BaseModel) -> Any:
        # Extract arguments and request execution over MCP client
        raw_args = args.model_dump()
        # Clean prefix from tool name to call it on the actual server
        actual_name = self._name.replace(f"{self.client.name}_", "", 1)
        
        response = await self.client.call_tool(actual_name, raw_args)
        
        if response.get("isError"):
            raise ToolExecutionError(f"MCP server execution failed: {response.get('content')}")
            
        content = response.get("content", [])
        # Extract content text strings
        output = []
        for block in content:
            if block.get("type") == "text":
                output.append(block.get("text", ""))
        return "\n".join(output) if output else response

    def _build_pydantic_schema(self, schema_dict: Dict[str, Any]) -> Type[BaseModel]:
        """Convert standard JSON Schema to a Pydantic BaseModel class."""
        properties = schema_dict.get("properties", {})
        required = schema_dict.get("required", [])
        
        fields = {}
        for prop_name, prop_meta in properties.items():
            prop_type = prop_meta.get("type")
            py_type = str
            if prop_type == "integer":
                py_type = int
            elif prop_type == "number":
                py_type = float
            elif prop_type == "boolean":
                py_type = bool
            elif prop_type == "array":
                py_type = list
            elif prop_type == "object":
                py_type = dict

            desc = prop_meta.get("description", "")
            
            if prop_name in required:
                fields[prop_name] = (py_type, Field(..., description=desc))
            else:
                fields[prop_name] = (Optional[py_type], Field(default=None, description=desc))
                
        return create_model(f"MCPToolArgs_{self._name}", **fields)

# =====================================================================
# 3. Central MCP Manager
# =====================================================================

class MCPManager:
    """Manages active MCP client instances and bridges their tools to Kriya registry."""

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self.clients: Dict[str, MCPClient] = {}
        self.registered_tools: Dict[str, List[str]] = {}

    async def start_all(self, mcp_configs: Dict[str, Any]) -> None:
        """Start all configured MCP subprocess servers and register their tools."""
        for server_name, server_cfg in mcp_configs.items():
            try:
                # server_cfg can be a pydantic model (MCPServerConfig) or dictionary
                if hasattr(server_cfg, "model_dump"):
                    cfg_dict = server_cfg.model_dump()
                else:
                    cfg_dict = server_cfg
                
                client = MCPClient(
                    name=server_name,
                    command=cfg_dict["command"],
                    args=cfg_dict.get("args", []),
                    env=cfg_dict.get("env", {})
                )
                await client.start()
                self.clients[server_name] = client
                
                # Fetch and register tools
                tools = await client.list_tools()
                self.registered_tools[server_name] = []
                for t in tools:
                    mcp_tool = MCPTool(client, t)
                    # Register under 'tool' category in kernel registry
                    self.kernel.registry.register("tool", mcp_tool.name, mcp_tool)
                    self.registered_tools[server_name].append(mcp_tool.name)
                    logger.info(f"Registered MCP tool '{mcp_tool.name}' from server '{server_name}'")
                    
            except Exception as e:
                logger.error(f"Failed to load MCP server '{server_name}': {e}", exc_info=True)

    async def shutdown_all(self) -> None:
        """Shutdown all active servers and unregister their tools."""
        for server_name, client in list(self.clients.items()):
            try:
                await client.stop()
            except Exception as e:
                logger.debug(f"Failed to cleanly stop MCP client '{server_name}': {e}")
            
            # Unregister tools from registry
            tool_names = self.registered_tools.pop(server_name, [])
            for tool_name in tool_names:
                try:
                    self.kernel.registry.unregister("tool", tool_name)
                except Exception as e:
                    logger.warning(f"Failed to unregister tool '{tool_name}': {e}")
                    
        self.clients.clear()
