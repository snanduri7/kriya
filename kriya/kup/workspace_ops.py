"""KUP workspace operations (GUI M5): ``workspace.describe`` and the admission rule for a ``workspace`` argument.

Transport only (full modularization B2): the folder argument is validated here (``validated_workspace``: the one admission rule every
KUP operation that opens a folder applies) and the answer is ``client.projects.describe`` (``kriya.application.ProjectService``, through the ``KriyaClient``) wrapped in the envelope. The
workspace scope of ``history.list`` is ``kriya.application.HistoryService.list_in_workspace``.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from kriya.context.workspace_identity import canonical_workspace
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency, source_block
from kriya.kup.store import SnapshotError


def _workspace_argument(request: Dict[str, Any]) -> str:
    workspace = request.get("workspace")
    if not isinstance(workspace, str) or not os.path.isabs(workspace) or any(ord(c) < 32 for c in workspace):
        raise SnapshotError(policy.INVALID_REQUEST, "workspace must be an absolute path without control characters")
    return workspace


def validated_workspace(request: Dict[str, Any]) -> tuple:
    """(requested, canonical) for a ``workspace`` argument, or a typed WORKSPACE_UNAVAILABLE: the one admission rule for opening a folder."""
    requested = _workspace_argument(request)
    if not os.path.lexists(requested):
        raise SnapshotError(policy.WORKSPACE_UNAVAILABLE, f"no such folder: {requested}", reason="not_found")
    if not os.path.isdir(requested):
        raise SnapshotError(policy.WORKSPACE_UNAVAILABLE, f"not a folder: {requested}", reason="not_a_directory")
    if not os.access(requested, os.R_OK | os.X_OK):
        raise SnapshotError(policy.WORKSPACE_UNAVAILABLE, f"the folder cannot be read: {requested}", reason="unreadable")
    return requested, canonical_workspace(requested)


def describe(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    requested, workspace = validated_workspace(request)
    data = client.projects.describe(client.context(workspace_root=workspace, request_id=request_id), cfg, requested, workspace)
    block = source_block(data["state"]["directory"], client.history.location(client.context(workspace_root=None, request_id=request_id), cfg).trace_database, None)
    return envelope("workspace.describe", request_id, data=data, source=block, consistency=live_consistency())
