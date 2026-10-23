"""D2 M1a (owner decision 2026-10-23): human-decision state lives OUTSIDE the repository.

The records of a desktop-started run that a person acts on - the launch record (which carries the pending review a person is asked to decide), the
one decision of each review, and the exact diff that review shows - are authority, not workspace content. They live under the operator's authority
home, ``<authority_home>/launches/<workspace identity>/`` (``KRIYA_AUTHORITY_HOME``, else ``~/.kriya/authority``), never under ``<workspace>/.kriya``,
so a repository (or anything checked into one) cannot carry a decision or a pending review into a run.

* Old workspace files (``<workspace>/.kriya/control/launches/*``) are NEVER read, trusted or migrated: nothing here looks at that location, so a review
  that was pending there is simply absent and a fresh review is required.
* The M4 ownership contract is unchanged: every document carries the workspace stamp, an existing document that is unstamped or another workspace's is
  never overwritten (so never adopted by being re-stamped), and the loaders in ``persistence`` still require the stamp.
* The directories are private (0700) and the files 0600; every write is staged, fsynced and renamed (or, for a decision, hard-linked once).
* The authority home is refused when it resolves inside the workspace's authority root: a home carried by the repository would put the decision
  back inside it.

This is identity hygiene and a location rule, not authentication: a process running as the same user can still write the operator's home (M2 adds
the private host-to-waiter channel for that). It closes decisions that arrive WITH the repository.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict

from kriya.config.environment import authority_home
from kriya.context.workspace_identity import (
    authority_root,
    ensure_existing_document_owned,
    ownership_metadata,
    workspace_identity,
)
from kriya.platform.filesystem_semantics import PathRelation, path_relation

HUMAN_DECISION_NAMESPACE = "launches"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class HumanDecisionStorePathError(ValueError):
    """The human-decision location resolves inside the workspace (or cannot be established): refused before anything is written."""


def human_decision_dir(workspace_path: str) -> str:
    """``<authority home>/launches/<workspace identity>`` for this workspace. Pure: creates nothing."""
    directory = os.path.join(os.path.realpath(authority_home()), HUMAN_DECISION_NAMESPACE, workspace_identity(workspace_path))
    if path_relation(authority_root(workspace_path), directory) is not PathRelation.OUTSIDE:
        raise HumanDecisionStorePathError(
            f"the authority home resolves inside the workspace ({directory!r}); human-decision state must live outside the repository"
        )
    return directory


def human_decision_path(workspace_path: str, filename: str) -> str:
    return os.path.join(human_decision_dir(workspace_path), filename)


def _ensure_private_directory(directory: str) -> None:
    """Creates ``launches/`` and ``launches/<identity>/`` as 0700 (umask-proof). The authority home above them is created if absent, and left as it is
    if present."""
    os.makedirs(os.path.dirname(os.path.dirname(directory)), exist_ok=True)
    for level in (os.path.dirname(directory), directory):
        try:
            os.mkdir(level, _DIRECTORY_MODE)
        except FileExistsError:
            pass
        os.chmod(level, _DIRECTORY_MODE)


def _read_current(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return b""


def _fsync_directory(directory: str) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_document(workspace_path: str, path: str, payload: Dict[str, Any], *, exclusive: bool = False) -> None:
    """Writes one ownership-stamped document at ``path`` (which must be in this workspace's human-decision directory).

    Replace mode refuses to overwrite an existing document that is unstamped or another workspace's (``WorkspaceOwnershipMissing`` /
    ``WorkspaceOwnershipError``). ``exclusive`` writes the document only if none exists, ATOMICALLY: the complete document is staged beside the final
    name and then hard-linked to it (``os.link`` fails with ``FileExistsError`` whoever created the name and however concurrently), so of any number of
    simultaneous writers exactly one succeeds and a reader never sees a partial document."""
    directory = human_decision_dir(workspace_path)
    if os.path.dirname(path) != directory:
        raise HumanDecisionStorePathError(f"{path!r} is not in the human-decision directory {directory!r}")
    _ensure_private_directory(directory)
    if not exclusive:
        ensure_existing_document_owned(workspace_path, _read_current(path), path)
    owned = dict(payload)
    owned["_workspace"] = ownership_metadata(workspace_path)
    content = json.dumps(owned, indent=2, sort_keys=True).encode("utf-8")
    staging = f"{path}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(staging, path)
        else:
            os.replace(staging, path)
        _fsync_directory(directory)
    finally:
        try:
            os.unlink(staging)
        except FileNotFoundError:
            pass
