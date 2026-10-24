"""Creating the workspace authority namespace on the HOST (D2 EXEC-LAYER rebuild, slice S1).

A contained workload that gets a workspace mounted read-write must never be the one that creates ``<workspace>/.kriya`` or the protected leaves
beneath it (``kriya.policy.protected_authority_paths``): whatever it creates there, Kriya would later read as its own. MEASURED on real Docker
(2026-10-24): with the leaf missing, the workload created and forged it; with ``.kriya`` replaceable, the workload renamed it away and left a
forged copy at the host-visible name. So the OCI backend creates these directories on the host before the container starts, and binds them
(``kriya/tools/containment_oci.py``). This module owns only the creation and the validation; the names are the canonical owner's.

Directories only - nothing is written into them. ``0700`` at creation (``chmod`` after ``mkdir``, so a restrictive umask cannot leave a directory
unwritable and a permissive one cannot widen it); an existing directory is verified and never chmod'd. ``lstat`` throughout: a symlink is
refused, never followed.
"""
from __future__ import annotations

import os
import stat

from kriya.platform.services import platform_services
from kriya.policy.protected_authority_paths import WORKSPACE_ANCHOR_RELPATH, WORKSPACE_AUTHORITY_RELPATHS

AUTHORITY_DIRECTORY_MODE = 0o700


class AuthorityNamespaceUnsafeError(RuntimeError):
    """The workspace authority namespace cannot be established as real directories owned by this user (a symlink, a file, another owner's
    directory, or an unusable workspace). The caller must not expose the workspace to a contained workload."""


def _ensure_private_directory(path: str) -> None:
    """One directory that exists on the HOST as a real directory owned by this user. Raises ``AuthorityNamespaceUnsafeError``."""
    try:
        os.mkdir(path, AUTHORITY_DIRECTORY_MODE)
        os.chmod(path, AUTHORITY_DIRECTORY_MODE)
    except FileExistsError:
        pass
    except OSError as error:
        raise AuthorityNamespaceUnsafeError(f"cannot create {path!r}: {error}") from error
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise AuthorityNamespaceUnsafeError(f"{path!r} is not a real directory (symlink or file)")
    if info.st_uid != platform_services().host_identity.current().uid:  # the platform port: no OS call here
        raise AuthorityNamespaceUnsafeError(f"{path!r} is owned by uid {info.st_uid}, not by this user")


def create_workspace_authority_namespace(workspace_path: str) -> str:
    """Make sure ``<workspace>/.kriya`` (the structural anchor) and the protected leaves ``.kriya/control`` and ``.kriya/checkpoints`` exist on
    the host as real directories owned by this user. Returns the anchor path. Raises ``AuthorityNamespaceUnsafeError`` on a symlink, a
    non-directory, a foreign owner or a workspace that does not exist."""
    root = os.path.realpath(workspace_path)
    anchor = os.path.join(root, WORKSPACE_ANCHOR_RELPATH)
    _ensure_private_directory(anchor)
    for relative in WORKSPACE_AUTHORITY_RELPATHS:
        _ensure_private_directory(os.path.join(root, relative))
    return anchor
