"""KUP ``skills.*`` and ``tools.*``: the typed, read-only operations the GUI reads Skills and Tools through (Demo V2).

Each is a thin adapter over ``kriya.application.SkillCatalogService`` / ``ToolCatalogService`` (full modularization B2): it validates the arguments,
resolves the folder's configuration exactly as ``execution.context`` does (``_load_in``), enters the folder (skill discovery still reads the process
working directory), asks the service, and wraps the answer in the KUP envelope. Nothing here starts a process, makes a network request or writes a file.

  skills.list           {workspace}                     -> items (SkillDescriptor), total, truncated
  skills.describe       {workspace, skill_id}           -> found, skill
  tools.list            {workspace}                     -> items (ToolDescriptor), total
  tools.describe        {workspace, tool_id}            -> found, tool
  tools.approval_state  {workspace, tool_id}            -> found, tool_id, approval

``workspace`` is the existing KUP scope argument (the folder whose configuration applies); it is not part of an item's identity, and no descriptor
carries a filesystem path. An item that does not exist is a normal answer (``found: false``), not a protocol error.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from kriya.context import RequestContext
from kriya.kup import policy
from kriya.kup.execution import _CWD_LOCK, _load_in
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace

ID_MAX = 200
OPERATIONS = ("skills.list", "skills.describe", "tools.list", "tools.describe", "tools.approval_state")


def _identifier(request: Dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value or len(value) > ID_MAX or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SnapshotError(policy.INVALID_REQUEST, f"{key} must be a non-empty string of at most {ID_MAX} characters without control characters")
    return value


def _base(ctx: RequestContext, workspace: str) -> Dict[str, Any]:
    return {"workspace": {"path": workspace, "name": os.path.basename(workspace.rstrip(os.sep)) or workspace, "identity": ctx.workspace.workspace_id}}


def _run(operation: str, request: Dict[str, Any], request_id: str, client, needs_id: Optional[str],
         answer: Callable[[RequestContext, Any, str, Optional[str]], Dict[str, Any]], empty: Dict[str, Any]) -> Dict[str, Any]:
    """Validate, resolve the folder's configuration, and call ``answer(ctx, cfg, workspace, identifier)`` (a catalog service) inside the folder;
    an unresolvable configuration is a typed ``available: false``."""
    identifier = _identifier(request, needs_id) if needs_id else None
    _requested, workspace = validated_workspace(request)
    _state, wcfg, authority, failure = _load_in(workspace)
    ctx = client.context(workspace_root=workspace, request_id=request_id)
    data = {**_base(ctx, workspace), "available": wcfg is not None, "unavailable_reason": failure, "authority_status": authority["status"]}
    if wcfg is None:
        data.update(empty)
    else:
        with _CWD_LOCK:  # skill discovery also looks at the process working directory (skills/ beside the configuration)
            previous = os.getcwd()
            os.chdir(workspace)
            try:
                data.update(answer(ctx, wcfg, workspace, identifier))
            finally:
                os.chdir(previous)
    return envelope(operation, request_id, data=data, source=None, consistency=live_consistency())


def skills_list(_cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    return _run("skills.list", request, request_id, client, None, lambda ctx, wcfg, ws, _id: client.skills.list(ctx, wcfg, ws),
                {"items": [], "total": 0, "truncated": False})


def skills_describe(_cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    return _run("skills.describe", request, request_id, client, "skill_id", client.skills.describe, {"found": False, "skill": None})


def tools_list(_cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    return _run("tools.list", request, request_id, client, None, lambda ctx, wcfg, ws, _id: client.tools.list(ctx, wcfg, ws), {"items": [], "total": 0})


def tools_describe(_cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    return _run("tools.describe", request, request_id, client, "tool_id", client.tools.describe, {"found": False, "tool": None})


def tools_approval_state(_cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    return _run("tools.approval_state", request, request_id, client, "tool_id", client.tools.approval_state,
                {"found": False, "tool_id": None, "approval": None})


HANDLERS = {"skills.list": skills_list, "skills.describe": skills_describe, "tools.list": tools_list, "tools.describe": tools_describe,
            "tools.approval_state": tools_approval_state}
