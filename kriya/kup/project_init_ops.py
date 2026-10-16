"""KUP ``project.initialize``: Project Setup for a folder that has no configuration yet (PRODUCT-UX-COMPLETION).

Transport only (full modularization B3): the folder argument is admitted here (``validated_workspace``) and the answer is the envelope. The use
case (Kriya's own recommended profile, validated by the real ``AppConfig`` schema and created exclusively: never renderer text, never an
overwrite) is ``kriya.application.ProjectService.initialize`` (``client.projects.initialize`` through the ``KriyaClient``; body: kriya/application/project_init.py).
"""
from __future__ import annotations

from typing import Any, Dict

from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.workspace_ops import validated_workspace


def initialize(cfg: Any, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    _requested, workspace = validated_workspace(request)
    data = client.projects.initialize(client.context(workspace_root=workspace, request_id=request_id), workspace)
    return envelope("project.initialize", request_id, data=data, source=None, consistency=live_consistency())
