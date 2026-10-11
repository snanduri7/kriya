"""KUP ``workspace.create``: the ONE place the desktop can ask Kriya to make a new project folder.

Why Kriya owns it: the desktop renderer never touches the filesystem, and Electron's main process only forwards typed requests. A new project is a
folder; nothing else. No Git repository is initialised (a Kriya run needs the ``git`` BINARY for its sandbox, not a repository in the folder -
an empty plain folder is a supported greenfield workspace), nothing is scaffolded (the goal drives generation), and no configuration file is written.

The rules, all enforced here and tested adversarially:

* ``parent`` is an absolute path to an EXISTING, readable, writable directory (a symlinked parent is followed to its real directory, which is what is reported);
* ``name`` is ONE path segment: 1..100 characters after Unicode NFC normalisation, no separator, no control character, no ``:`` (Finder shows it as ``/``), not
  ``.``/``..``, no leading dot, no leading/trailing whitespace;
* the target must NOT exist (not even as a dangling symlink); creation is a single ``os.mkdir`` (never ``makedirs``), so it can never create a missing parent and
  two racing creators cannot both succeed - the loser gets ``already_exists``.
"""
from __future__ import annotations

import os
import unicodedata
from typing import Any, Dict

from kriya.control.workspace_identity import canonical_workspace, workspace_identity
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError

NAME_MAX = 100
_FORBIDDEN = frozenset('/\\:\0')


def validate_project_name(name: Any) -> str:
    """The NFC-normalised, validated folder name, or a typed INVALID_REQUEST (extra ``reason``: invalid_name)."""
    def refuse(why: str) -> SnapshotError:
        return SnapshotError(policy.INVALID_REQUEST, f"the project name is not valid: {why}", reason="invalid_name")

    if not isinstance(name, str):
        raise refuse("it must be text")
    normal = unicodedata.normalize("NFC", name)
    if not normal or len(normal) > NAME_MAX:
        raise refuse(f"it must have 1 to {NAME_MAX} characters")
    if normal != normal.strip():
        raise refuse("it must not start or end with a space")
    if any(ch in _FORBIDDEN or unicodedata.category(ch).startswith("C") for ch in normal):
        raise refuse("it must not contain a slash, a colon or a control character")
    if normal in (".", "..") or normal.startswith("."):
        raise refuse("it must not start with a dot")
    return normal


def _unavailable(message: str, reason: str) -> SnapshotError:
    return SnapshotError(policy.WORKSPACE_UNAVAILABLE, message, reason=reason)


def create(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:  # noqa: ARG001 - the operation table passes the configuration to every handler
    parent = request.get("parent")
    if not isinstance(parent, str) or not parent or not os.path.isabs(parent) or any(ord(c) < 32 for c in parent) or len(parent) > 4096:
        raise SnapshotError(policy.INVALID_REQUEST, "parent must be an absolute path without control characters", reason="invalid_parent")
    name = validate_project_name(request.get("name"))
    if not os.path.isdir(parent):
        raise _unavailable(f"the parent folder does not exist or is not a folder: {parent}", "parent_not_found")
    real_parent = canonical_workspace(parent)
    if not os.access(real_parent, os.R_OK | os.W_OK | os.X_OK):
        raise _unavailable(f"the parent folder is not writable: {real_parent}", "permission_denied")
    target = os.path.join(real_parent, name)
    if os.path.lexists(target):
        raise _unavailable(f"{name} already exists in {real_parent}", "already_exists")
    try:
        os.mkdir(target, 0o755)  # atomic; never creates a missing parent
    except FileExistsError as error:
        raise _unavailable(f"{name} already exists in {real_parent}", "already_exists") from error
    except PermissionError as error:
        raise _unavailable(f"the folder cannot be created in {real_parent}: permission denied", "permission_denied") from error
    except OSError as error:
        raise _unavailable(f"the folder could not be created: {error.strerror or type(error).__name__}", "create_failed") from error
    workspace = canonical_workspace(target)
    data = {"workspace": workspace, "name": name, "parent": real_parent, "identity": workspace_identity(workspace), "created": True,
            "git_initialized": False,
            "note": "An empty folder was created. Nothing else was written: no Git repository, no configuration, no files."}
    return envelope("workspace.create", request_id, data=data, source=None, consistency=live_consistency())
