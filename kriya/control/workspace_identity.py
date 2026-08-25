"""Stable local workspace identity for persistent control-plane ownership."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict


class WorkspaceOwnershipError(RuntimeError):
    """Persistent state belongs to a different workspace."""


def workspace_identity(workspace_path: str) -> str:
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(workspace_path)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ownership_metadata(workspace_path: str) -> Dict[str, str]:
    return {"workspace_id": workspace_identity(workspace_path), "version": "1"}


def validate_ownership(workspace_path: str, payload: Dict[str, Any], source: str) -> None:
    owner = payload.get("_workspace")
    if owner is None:
        return  # backward-compatible migration for pre-ownership state
    expected = workspace_identity(workspace_path)
    actual = owner.get("workspace_id") if isinstance(owner, dict) else None
    if actual != expected:
        raise WorkspaceOwnershipError(
            f"persistent state at {source!r} belongs to workspace {actual!r}, not {expected!r}"
        )
