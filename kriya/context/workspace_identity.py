"""Stable local workspace identity for persistent control-plane ownership."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

WORKSPACE_OWNERSHIP_MISSING = "WORKSPACE_OWNERSHIP_MISSING"
WORKSPACE_OWNERSHIP_MISMATCH = "WORKSPACE_OWNERSHIP_MISMATCH"

# D2 M4 follow-up: the one remediation every ownership refusal carries. Recovery (`kriya runs recover`) settles owned evidence; it cannot establish
# that a file is this workspace's, so it is never the advice here, and the file is preserved, never deleted.
OWNERSHIP_REMEDIATION = (
    "Automatic recovery cannot establish ownership of this file. Preserve or archive it outside the active Kriya control-state location "
    "(do not delete it), and run again only after you have established that no interrupted mutation still requires recovery."
)

# D2 M4 (owner decision 2026-10-23): the one closed table of the reason codes an ownership refusal carries; the message is rendered from it only.
OWNERSHIP_REFUSALS = {
    WORKSPACE_OWNERSHIP_MISSING: (
        "persistent state at {source!r} carries no workspace ownership stamp. Kriya writes every control document with one, so this file was "
        "not written by Kriya for this workspace; Kriya never adopts it by stamping it. " + OWNERSHIP_REMEDIATION
    ),
    WORKSPACE_OWNERSHIP_MISMATCH: (
        "persistent state at {source!r} belongs to workspace {actual!r}, not {expected!r}. " + OWNERSHIP_REMEDIATION
    ),
}


class WorkspaceOwnershipError(RuntimeError):
    """Persistent state is not provably this workspace's: it belongs to a different workspace, or carries no ownership stamp. The ownership stamp
    is IDENTITY hygiene (a hash of the workspace path), not authentication: it stops accidental and carried state, nothing that can forge it."""

    reason_code = WORKSPACE_OWNERSHIP_MISMATCH


class WorkspaceOwnershipMissing(WorkspaceOwnershipError):
    """D2 M4: a document that must carry an ownership stamp carries none. Never repaired by stamping it (no implicit migration)."""

    reason_code = WORKSPACE_OWNERSHIP_MISSING


def _ownership_error(reason_code: str, source: str, *, expected: str = "", actual: Any = None) -> WorkspaceOwnershipError:
    message = OWNERSHIP_REFUSALS[reason_code].format(source=source, expected=expected, actual=actual)
    return (WorkspaceOwnershipMissing if reason_code == WORKSPACE_OWNERSHIP_MISSING else WorkspaceOwnershipError)(message)


# Directories Kriya itself owns: nothing beneath one is a user's workspace, so a repository search never ascends THROUGH one (a scoped snapshot,
# a candidate sandbox under ``<root>/.kriya/``, a gate view under the state root must not take the identity of the repository around them).
_KRIYA_OWNED_DIRECTORIES = frozenset({".kriya", "gate-views", "gate-caches"})


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


def validate_ownership(workspace_path: str, payload: Dict[str, Any], source: str) -> None:
    """D2 M4: a control document must carry the ownership stamp of THIS workspace. A missing stamp is ``WorkspaceOwnershipMissing``
    (``WORKSPACE_OWNERSHIP_MISSING``), a stamp naming another workspace is ``WorkspaceOwnershipError`` (``WORKSPACE_OWNERSHIP_MISMATCH``);
    neither is ever repaired by stamping the document. The pre-PLAT-017 spelling of the same workspace's identity stays readable (an alias,
    not an adoption)."""
    owner = payload.get("_workspace")
    if owner is None:
        raise _ownership_error(WORKSPACE_OWNERSHIP_MISSING, source)
    actual = owner.get("workspace_id") if isinstance(owner, dict) else None
    if actual not in workspace_identities(workspace_path):  # the legacy (normcase) id stays readable
        raise _ownership_error(WORKSPACE_OWNERSHIP_MISMATCH, source, expected=workspace_identity(workspace_path), actual=actual)


def validate_ledger_line_ownership(workspace_path: str, payload: Dict[str, Any], source: str) -> None:
    """D2 M4: the decision ledger's per-line stamp (``_workspace_id``), strictly: absent is ``WorkspaceOwnershipMissing``, another workspace's
    is ``WorkspaceOwnershipError`` (the ledger never accepted the legacy spelling, and still does not)."""
    actual = payload.get("_workspace_id")
    if actual is None:
        raise _ownership_error(WORKSPACE_OWNERSHIP_MISSING, source)
    expected = workspace_identity(workspace_path)
    if actual != expected:
        raise _ownership_error(WORKSPACE_OWNERSHIP_MISMATCH, source, expected=expected, actual=actual)


def ensure_existing_document_owned(workspace_path: str, current: bytes, source: str) -> None:
    """D2 M4: a control document is never overwritten while it is another workspace's or carries no stamp - overwriting would give it THIS workspace's
    stamp, which is the adoption M4 retires. ``current`` is the file's present bytes (empty: nothing there). Content that is not a JSON object at
    all is corrupt, not foreign: it is replaced exactly as before (recovery from a damaged store)."""
    if not current:
        return
    try:
        payload = json.loads(current.decode("utf-8"))
    except ValueError:
        return
    if isinstance(payload, dict):
        validate_ownership(workspace_path, payload, source)
