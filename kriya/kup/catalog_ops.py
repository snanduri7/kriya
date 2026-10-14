"""KUP ``skills.*`` and ``tools.*``: the typed, read-only operations the GUI reads Skills and Tools through (Demo V2).

Each is a thin adapter over ``SkillService`` / ``ToolService``: it resolves the folder's configuration exactly as ``execution.context`` does
(``_load_in``), asks the service, and wraps the answer in the KUP envelope. Nothing here starts a process, makes a network request or writes a file.

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

from kriya.control.workspace_identity import workspace_identity
from kriya.kup import policy
from kriya.kup.execution import _CWD_LOCK, _load_in
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace
from kriya.skills.service import SkillService
from kriya.tools.service import ToolService

ID_MAX = 200
OPERATIONS = ("skills.list", "skills.describe", "tools.list", "tools.describe", "tools.approval_state")


def _identifier(request: Dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value or len(value) > ID_MAX or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SnapshotError(policy.INVALID_REQUEST, f"{key} must be a non-empty string of at most {ID_MAX} characters without control characters")
    return value


def _base(workspace: str) -> Dict[str, Any]:
    return {"workspace": {"path": workspace, "name": os.path.basename(workspace.rstrip(os.sep)) or workspace, "identity": workspace_identity(workspace)}}


def _run(operation: str, request: Dict[str, Any], request_id: str, needs_id: Optional[str], answer: Callable[[Any, str, Optional[str]], Dict[str, Any]],
         empty: Dict[str, Any]) -> Dict[str, Any]:
    """Validate, resolve the folder's configuration, and call ``answer(cfg, workspace, identifier)``; an unresolvable configuration is a typed ``available: false``."""
    identifier = _identifier(request, needs_id) if needs_id else None
    _requested, workspace = validated_workspace(request)
    _state, wcfg, authority, failure = _load_in(workspace)
    data = {**_base(workspace), "available": wcfg is not None, "unavailable_reason": failure, "authority_status": authority["status"]}
    if wcfg is None:
        data.update(empty)
    else:
        with _CWD_LOCK:  # skill discovery also looks at the process working directory (skills/ beside the configuration)
            previous = os.getcwd()
            os.chdir(workspace)
            try:
                data.update(answer(wcfg, workspace, identifier))
            finally:
                os.chdir(previous)
    return envelope(operation, request_id, data=data, source=None, consistency=live_consistency())


def skills_list(_cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    def answer(wcfg, workspace, _identifier_unused):
        items, total = SkillService(wcfg, workspace).list()
        return {"items": [d.to_dict() for d in items], "total": total, "truncated": total > len(items)}
    return _run("skills.list", request, request_id, None, answer, {"items": [], "total": 0, "truncated": False})


def skills_describe(_cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    def answer(wcfg, workspace, skill_id):
        descriptor = SkillService(wcfg, workspace).describe(skill_id)
        return {"found": descriptor is not None, "skill": descriptor.to_dict() if descriptor is not None else None}
    return _run("skills.describe", request, request_id, "skill_id", answer, {"found": False, "skill": None})


def tools_list(_cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    def answer(wcfg, workspace, _identifier_unused):
        items = ToolService(wcfg, workspace).list()
        return {"items": [d.to_dict() for d in items], "total": len(items)}
    return _run("tools.list", request, request_id, None, answer, {"items": [], "total": 0})


def tools_describe(_cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    def answer(wcfg, workspace, tool_id):
        descriptor = ToolService(wcfg, workspace).describe(tool_id)
        return {"found": descriptor is not None, "tool": descriptor.to_dict() if descriptor is not None else None}
    return _run("tools.describe", request, request_id, "tool_id", answer, {"found": False, "tool": None})


def tools_approval_state(_cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    def answer(wcfg, workspace, tool_id):
        service = ToolService(wcfg, workspace)
        descriptor = service.describe(tool_id)
        state = descriptor.approval if descriptor is not None else service.approval_state(tool_id)
        return {"found": descriptor is not None, "tool_id": tool_id,
                "approval": {"state": state.state, "reason": state.reason, "records_on_file": state.records_on_file, "recorded_tool_names": list(state.recorded_tool_names)}}
    return _run("tools.approval_state", request, request_id, "tool_id", answer, {"found": False, "tool_id": None, "approval": None})


HANDLERS = {"skills.list": skills_list, "skills.describe": skills_describe, "tools.list": tools_list, "tools.describe": tools_describe,
            "tools.approval_state": tools_approval_state}
