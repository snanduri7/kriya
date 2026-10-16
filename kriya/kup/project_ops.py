"""KUP ``workspace.create``: the ONE place the desktop can ask Kriya to make a new project folder.

Transport only (full modularization B3): the ``parent`` argument is validated here (an absolute path without control characters) and the
answer is the envelope. The use case and its rules (one path segment, an existing writable parent, a single ``os.mkdir``, no Git, no
configuration, no files) are ``kriya.application.ProjectService.create`` (``client.projects.create`` through the ``KriyaClient``; kriya/application/project.py).
"""
from __future__ import annotations

import os
from typing import Any, Dict

from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError


def create(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    parent = request.get("parent")
    if not isinstance(parent, str) or not parent or not os.path.isabs(parent) or any(ord(c) < 32 for c in parent) or len(parent) > 4096:
        raise SnapshotError(policy.INVALID_REQUEST, "parent must be an absolute path without control characters", reason="invalid_parent")
    data = client.projects.create(client.context(workspace_root=None, request_id=request_id), parent, request.get("name"))
    return envelope("workspace.create", request_id, data=data, source=None, consistency=live_consistency())
