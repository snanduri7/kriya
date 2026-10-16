"""SkillCatalogService / ToolCatalogService (slice B2): the read-only Skills and Tools catalog use cases, extracted unchanged from
``kriya/kup/catalog_ops.py`` (Demo V2). Each asks ``SkillService`` / ``ToolService`` about the RESOLVED configuration of a folder.
Nothing here starts a process, makes a network request or writes a file; no descriptor carries a filesystem path.

Skill discovery still looks at the process working directory (``skills/`` beside the configuration; J1 replaces that with skill
scopes): the adapter enters the folder around these calls, so APPLICATION itself never reads or changes the working directory.
"""
from __future__ import annotations

from typing import Any, Dict

from kriya.context.model import RequestContext
from kriya.skills.service import SkillService
from kriya.tools.service import ToolService


class SkillCatalogService:
    def list(self, ctx: RequestContext, cfg, workspace: str) -> Dict[str, Any]:
        items, total = SkillService(cfg, workspace).list()
        return {"items": [d.to_dict() for d in items], "total": total, "truncated": total > len(items)}

    def describe(self, ctx: RequestContext, cfg, workspace: str, skill_id: str) -> Dict[str, Any]:
        descriptor = SkillService(cfg, workspace).describe(skill_id)
        return {"found": descriptor is not None, "skill": descriptor.to_dict() if descriptor is not None else None}


class ToolCatalogService:
    def list(self, ctx: RequestContext, cfg, workspace: str) -> Dict[str, Any]:
        items = ToolService(cfg, workspace).list()
        return {"items": [d.to_dict() for d in items], "total": len(items)}

    def describe(self, ctx: RequestContext, cfg, workspace: str, tool_id: str) -> Dict[str, Any]:
        descriptor = ToolService(cfg, workspace).describe(tool_id)
        return {"found": descriptor is not None, "tool": descriptor.to_dict() if descriptor is not None else None}

    def approval_state(self, ctx: RequestContext, cfg, workspace: str, tool_id: str) -> Dict[str, Any]:
        service = ToolService(cfg, workspace)
        descriptor = service.describe(tool_id)
        state = descriptor.approval if descriptor is not None else service.approval_state(tool_id)
        return {"found": descriptor is not None, "tool_id": tool_id,
                "approval": {"state": state.state, "reason": state.reason, "records_on_file": state.records_on_file, "recorded_tool_names": list(state.recorded_tool_names)}}


__all__ = ["SkillCatalogService", "ToolCatalogService"]
