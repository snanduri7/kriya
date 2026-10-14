"""ToolService: the read-only façade the GUI/KUP reads Tools through (Demo V2).

Tools here are what the resolved configuration declares: MCP servers (``mcp.<name>``) and enabled plugins (``plugins.enabled``). V1 has three
methods, ``list``, ``describe`` and ``approval_state``, and does NOTHING that starts a process: no MCP server is launched, no plugin is imported,
no network is touched, and nothing is written. Adding, removing, enabling or testing a tool are deliberately absent
(see demo-v2/docs/EXTENSION_CONTRACT.md).

Identity is ``mcp:<server>`` / ``plugin:<name>``, never a path. Anything the backend cannot know without starting the tool is ``None`` and named in
``unknown_fields``. No secret material is ever returned: a server's ``args`` and ``env`` values, which commonly carry tokens, are reduced to a COUNT
and the variable NAMES; the command is reduced to its executable's basename.

Approval state is read from the durable TOOL-002 invocation-approval store (outside the workspace) without starting anything. A durable approval binds
to the exact tool input-schema digest, which exists only after the server is started and its tools are listed, so a record on file can NEVER be called
"approved" here. The states are NOT_APPROVED (no record names this server: it cannot be approved), UNKNOWN (with a closed reason code) and NOT_APPLICABLE.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from kriya.mcp.invocation_approval import (
    MCPApprovalArtifactError,
    MCPTrustPathInsideWorkspaceError,
    default_local_approval_path,
    load_approval_artifact,
    workspace_identity,
)

KIND_MCP = "mcp_server"
KIND_PLUGIN = "plugin"
PROVIDER_CONFIGURED_MCP = "configured_mcp"
PROVIDER_ENABLED_PLUGIN = "enabled_plugin"

STATE_NOT_APPROVED = "NOT_APPROVED"
STATE_UNKNOWN = "UNKNOWN"
STATE_NOT_APPLICABLE = "NOT_APPLICABLE"
REASON_NO_RECORDS = "NO_RECORDS_FOR_SERVER"
REASON_NEEDS_LIVE_IDENTITY = "RECORDS_NEED_LIVE_TOOL_IDENTITY"
REASON_STORE_UNREADABLE = "APPROVAL_STORE_UNREADABLE_OR_INVALID"
REASON_STORE_PATH_REFUSED = "APPROVAL_STORE_PATH_REFUSED"
REASON_NOT_AN_MCP_SERVER = "NOT_AN_MCP_SERVER"
REASON_NOT_FOUND = "TOOL_NOT_FOUND"

STATUS_CONFIGURED = "CONFIGURED"
STATUS_ENABLED = "ENABLED"
HEALTH_NOT_CHECKED = "NOT_CHECKED"  # learned only by starting the tool, which this service never does


@dataclass(frozen=True)
class ApprovalState:
    state: str
    reason: str
    records_on_file: Optional[int]  # for this server; None when it could not be determined
    recorded_tool_names: Tuple[str, ...] = ()  # tool names with a record on file for this server (names only)


@dataclass(frozen=True)
class DeclaredCapabilities:
    workspace_read: bool
    workspace_write: bool
    network: str
    network_hosts: Tuple[str, ...]


@dataclass(frozen=True)
class ToolDescriptor:
    id: str
    name: str
    kind: str
    provider: str
    version: Optional[str]
    status: str
    health: str
    declared_capabilities: Optional[DeclaredCapabilities]
    reference: Dict[str, Any]  # what the tool is configured as, without secret material
    scope: Optional[str]
    approval: ApprovalState
    unknown_fields: Tuple[str, ...] = field(default=())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["unknown_fields"] = list(self.unknown_fields)
        data["approval"]["recorded_tool_names"] = list(self.approval.recorded_tool_names)
        if self.declared_capabilities is not None:
            data["declared_capabilities"]["network_hosts"] = list(self.declared_capabilities.network_hosts)
        return data


def mcp_tool_id(server: str) -> str:
    return f"mcp:{server}"


def plugin_tool_id(name: str) -> str:
    return f"plugin:{name}"


def _server_approval(workspace_root: str, server: str) -> ApprovalState:
    """What the durable store can honestly say about ``server`` without starting it. Never raises: any failure is an explicit UNKNOWN."""
    try:
        path = default_local_approval_path(workspace_root)
    except MCPTrustPathInsideWorkspaceError:
        return ApprovalState(STATE_UNKNOWN, REASON_STORE_PATH_REFUSED, None)
    except (OSError, ValueError):
        return ApprovalState(STATE_UNKNOWN, REASON_STORE_UNREADABLE, None)
    try:
        artifact = load_approval_artifact(path)
        if artifact is not None and artifact.workspace_id != workspace_identity(workspace_root):
            # a store whose own workspace id is not this workspace's is not this workspace's record: nothing in it can be counted
            return ApprovalState(STATE_UNKNOWN, REASON_STORE_UNREADABLE, None)
        matching = [] if artifact is None else [r for r in artifact.records if r.server_identity == server]
        names = tuple(sorted({r.tool_name for r in matching if isinstance(r.tool_name, str)}))
    except (MCPApprovalArtifactError, OSError, ValueError, TypeError, AttributeError):
        # any unreadable, undecodable or malformed store is an explicit UNKNOWN (UnicodeDecodeError is a ValueError); it never escapes into the listing of every other tool
        return ApprovalState(STATE_UNKNOWN, REASON_STORE_UNREADABLE, None)
    if not matching:
        return ApprovalState(STATE_NOT_APPROVED, REASON_NO_RECORDS, 0)
    return ApprovalState(STATE_UNKNOWN, REASON_NEEDS_LIVE_IDENTITY, len(matching), names)


class ToolService:
    """``ToolService(cfg, workspace)``: ``.list()``, ``.describe(tool_id)``, ``.approval_state(tool_id)``. Reads configuration and the approval store; starts nothing."""

    def __init__(self, cfg: Any, workspace: str) -> None:
        self._cfg = cfg
        self._workspace = os.path.realpath(workspace)

    def _mcp(self, server: str) -> ToolDescriptor:
        config = self._cfg.mcp[server]
        caps = config.capabilities
        command = config.command if isinstance(config.command, str) else ""
        return ToolDescriptor(
            id=mcp_tool_id(server), name=server, kind=KIND_MCP, provider=PROVIDER_CONFIGURED_MCP, version=None, status=STATUS_CONFIGURED,
            health=HEALTH_NOT_CHECKED,
            declared_capabilities=DeclaredCapabilities(bool(caps.workspace_read), bool(caps.workspace_write), str(caps.network), tuple(caps.network_hosts)),
            reference={"executable": os.path.basename(command), "argument_count": len(config.args), "environment_variable_names": sorted(config.env)},
            scope=None, approval=_server_approval(self._workspace, server), unknown_fields=("version", "scope"))

    def _plugin(self, name: str) -> ToolDescriptor:
        return ToolDescriptor(
            id=plugin_tool_id(name), name=name, kind=KIND_PLUGIN, provider=PROVIDER_ENABLED_PLUGIN, version=None, status=STATUS_ENABLED,
            health=HEALTH_NOT_CHECKED, declared_capabilities=None, reference={"plugin": name}, scope=None,
            approval=ApprovalState(STATE_NOT_APPLICABLE, REASON_NOT_AN_MCP_SERVER, None),
            unknown_fields=("version", "declared_capabilities", "scope"))

    def list(self) -> List[ToolDescriptor]:
        """MCP servers then enabled plugins, each in name order."""
        servers = [self._mcp(name) for name in sorted(self._cfg.mcp)]
        plugins = [self._plugin(name) for name in sorted(set(self._cfg.plugins.enabled))]
        return servers + plugins

    def describe(self, tool_id: str) -> Optional[ToolDescriptor]:
        kind, _, name = tool_id.partition(":")
        if kind == "mcp" and name in self._cfg.mcp:
            return self._mcp(name)
        if kind == "plugin" and name in set(self._cfg.plugins.enabled):
            return self._plugin(name)
        return None

    def approval_state(self, tool_id: str) -> ApprovalState:
        """The approval state of one tool. A tool that does not exist is UNKNOWN/TOOL_NOT_FOUND, never NOT_APPROVED (absence is not a decision)."""
        descriptor = self.describe(tool_id)
        if descriptor is None:
            return ApprovalState(STATE_UNKNOWN, REASON_NOT_FOUND, None)
        return descriptor.approval
