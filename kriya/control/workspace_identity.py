"""Stable local workspace identity for persistent control-plane ownership."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict


class WorkspaceOwnershipError(RuntimeError):
    """Persistent state belongs to a different workspace."""


def canonical_workspace(workspace_path: str) -> str:
    """PLAT-017 (BACKEND-READINESS-004): the one canonical string every
    control-plane identity, lock and record derives from - the real path
    spelled as the filesystem stores it (kriya/platform/filesystem_semantics.
    canonical_spelling), so a case variant of the same directory is the same
    workspace on a case-insensitive filesystem."""
    from kriya.platform.filesystem_semantics import canonical_spelling

    return canonical_spelling(workspace_path)


def workspace_identity(workspace_path: str) -> str:
    return hashlib.sha256(canonical_workspace(workspace_path).encode("utf-8")).hexdigest()


def legacy_workspace_identity(workspace_path: str) -> str:
    """The pre-PLAT-017 identity (normcase of the real path): still accepted
    when reading state written under it, never written any more."""
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(workspace_path)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def workspace_identities(workspace_path: str) -> frozenset:
    """Every identity this workspace may have been recorded under."""
    return frozenset({workspace_identity(workspace_path), legacy_workspace_identity(workspace_path)})


def ownership_metadata(workspace_path: str) -> Dict[str, str]:
    return {"workspace_id": workspace_identity(workspace_path), "version": "2"}


def json_document_is_ownerless(path: str) -> bool:
    """True only for an existing, valid legacy JSON document without ownership."""
    if not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"persistent state at {path!r} is not a JSON object")
    return payload.get("_workspace") is None


def validate_ownership(workspace_path: str, payload: Dict[str, Any], source: str) -> None:
    owner = payload.get("_workspace")
    if owner is None:
        return  # backward-compatible migration for pre-ownership state
    expected = workspace_identity(workspace_path)
    actual = owner.get("workspace_id") if isinstance(owner, dict) else None
    if actual not in workspace_identities(workspace_path):  # the legacy (normcase) id stays readable
        raise WorkspaceOwnershipError(
            f"persistent state at {source!r} belongs to workspace {actual!r}, not {expected!r}"
        )
