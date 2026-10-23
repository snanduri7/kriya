"""Stable local workspace identity for persistent control-plane ownership."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional


class WorkspaceOwnershipError(RuntimeError):
    """Persistent state belongs to a different workspace."""


# Directories Kriya itself owns: nothing beneath one is a user's workspace, so a repository search never ascends THROUGH one (a scoped snapshot,
# a candidate sandbox under ``<root>/.kriya/``, a gate view under the state root must not take the identity of the repository around them).
_KRIYA_OWNED_DIRECTORIES = frozenset({".kriya", "gate-views"})


def find_repository_root(directory: str) -> Optional[str]:
    """The nearest directory at or above ``directory`` that holds a ``.git`` entry (a directory, or the file of a linked work tree / submodule), or
    None: not inside a repository, or inside a Kriya-owned directory that no repository search may cross. Decided on the filesystem, so a path that
    is no repository costs no process. ``directory`` is expected in canonical spelling."""
    current = directory
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current
        if os.path.basename(current) in _KRIYA_OWNED_DIRECTORIES:
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def authority_root(workspace_path: str) -> str:
    """D-001 (owner decision 2026-10-23): the canonical REPOSITORY ROOT a workspace path belongs to - the one root every workspace-scoped AUTHORITY
    (the run lock, the identity, the RunRecords, launches, commit evidence, approvals, recovery) is keyed by, so ``<repo>`` and ``<repo>/sub`` (and
    every alias of either) share it. A genuinely nested repository (its own ``.git``) or linked work tree resolves to ITSELF; a path in no repository
    is its own root. This is NOT the operating root: the directory a run reads and writes (and its sandbox) stays the one that was invoked."""
    canonical = canonical_workspace(workspace_path)
    root = find_repository_root(canonical)
    return canonical if root is None else root


def canonical_workspace(workspace_path: str) -> str:
    """PLAT-017 (BACKEND-READINESS-004): the one canonical string every
    control-plane identity, lock and record derives from - the real path
    spelled as the filesystem stores it (kriya/platform/filesystem_semantics.
    canonical_spelling), so a case variant of the same directory is the same
    workspace on a case-insensitive filesystem."""
    from kriya.platform.filesystem_semantics import canonical_spelling

    return canonical_spelling(workspace_path)


def workspace_identity(workspace_path: str) -> str:
    """The stable identity of the workspace's AUTHORITY (its repository root, see ``authority_root``): the same for ``<repo>`` and ``<repo>/sub``."""
    return hashlib.sha256(authority_root(workspace_path).encode("utf-8")).hexdigest()


def legacy_workspace_identity(workspace_path: str) -> str:
    """The pre-PLAT-017 identity (normcase of the real path, in the SPELLING it was given): still accepted when reading state written under it,
    never written any more. Of the authority root since D-001, found from that same spelling - never from the canonical one, which would fold the
    case variants whose legacy identities are exactly what differ."""
    spelled = os.path.realpath(os.path.abspath(workspace_path))
    root = find_repository_root(spelled)
    return hashlib.sha256(os.path.normcase(spelled if root is None else root).encode("utf-8")).hexdigest()


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
