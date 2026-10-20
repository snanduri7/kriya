"""ProjectService (slices B2, B3): what Kriya reports about a folder, extracted unchanged from ``kriya/kup/workspace_ops.py``
(``workspace.describe``, GUI M5) and ``kriya/kup/cli_ops.py`` (``workspace.status`` and the snapshot-acquisition run guard); and the two
write use cases, extracted unchanged from ``kriya/kup/project_ops.py`` (``workspace.create``: the ONE place the desktop can ask Kriya to
make a new project folder) and ``kriya/kup/project_init_ops.py`` (``project.initialize``, whose body is ``kriya/application/project_init.py``).

Kriya is the only interpreter of a folder: the canonical spelling and identity Kriya itself uses (kriya/context/workspace_identity.py),
what Git reports, the recovery assessment, and which state directory Kriya resolves. Read-only: nothing is created, locked, written
or executed from the folder (``describe_checkout`` disables fsmonitor and optional locks). A folder is never classified as greenfield
or brownfield: an empty directory, a Git checkout, a plain project and a modified repository are all reported by the same fields.

The adapter validates the folder argument (``kriya.kup.workspace_ops.validated_workspace``: the one admission rule for opening a
folder) before any of these runs.

``create`` (Why Kriya owns it: the desktop renderer never touches the filesystem, and Electron's main process only forwards typed requests.)
A new project is a folder; nothing else. No Git repository is initialised (a Kriya run needs the ``git`` BINARY for its sandbox, not a
repository in the folder - an empty plain folder is a supported greenfield workspace), nothing is scaffolded (the goal drives generation),
and no configuration file is written. The rules, all enforced here and tested adversarially:

* ``parent`` (validated by the adapter as an absolute path) must be an EXISTING, readable, writable directory (a symlinked parent is
  followed to its real directory, which is what is reported);
* ``name`` is ONE path segment: 1..100 characters after Unicode NFC normalisation, no separator, no control character, no ``:`` (Finder
  shows it as ``/``), not ``.``/``..``, no leading dot, no leading/trailing whitespace;
* the target must NOT exist (not even as a dangling symlink); creation is a single ``os.mkdir`` (never ``makedirs``), so it can never
  create a missing parent and two racing creators cannot both succeed - the loser gets ``already_exists``.
"""
from __future__ import annotations

import os
import unicodedata
from typing import Any, Dict, Optional

from kriya.application.services import INVALID_REQUEST, WORKSPACE_UNAVAILABLE, ApplicationRefusal
from kriya.context.model import RequestContext
from kriya.context.workspace_identity import canonical_workspace, workspace_identity
from kriya.control.control_store import CONTROL_DIRECTORY
from kriya.control.recovery import STATUS_CLEAN, STATUS_RUN_ACTIVE, assess_recovery
from kriya.core.state_paths import resolve_state_directory, trace_db_path
from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters
from kriya.tools.git_metadata import describe_checkout

CONFIG_FILE_NAMES = ("kriya.yaml", "kriya.yml")
NAME_MAX = 100
_FORBIDDEN = frozenset('/\\:\0')
CONFIG_NOT_APPLIED = ("The cockpit reads with its own configuration directory (KUP host setting); a kriya.yaml inside the opened folder "
                      "is not applied to these reads.")



def validate_project_name(name: Any) -> str:
    """The NFC-normalised, validated folder name, or a typed INVALID_REQUEST (extra ``reason``: invalid_name)."""
    def refuse(why: str) -> ApplicationRefusal:
        return ApplicationRefusal(INVALID_REQUEST, f"the project name is not valid: {why}", reason="invalid_name")

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


def _unavailable(message: str, reason: str) -> ApplicationRefusal:
    return ApplicationRefusal(WORKSPACE_UNAVAILABLE, message, reason=reason)


class ProjectService:
    def status(self, ctx: RequestContext, workspace: str) -> Dict[str, Any]:
        """The recovery assessment of an (absolute) workspace path, with ``kriya runs status``'s own exit code."""
        assessment = assess_recovery(workspace)
        status = assessment.status
        exit_code = 0 if status == STATUS_CLEAN else (3 if status == STATUS_RUN_ACTIVE else 1)  # = kriya runs status's own exit codes
        return {"workspace": assessment.workspace_path, "run_active": status == STATUS_RUN_ACTIVE, "status": status, "exit_code": exit_code,
                "assessment": assessment.to_dict()}

    def active_run(self, ctx: RequestContext, workspace: str) -> Optional[str]:
        """The run the recovery assessment reports active in ``workspace``, or None. Read-only: never creates, locks or writes."""
        assessment = assess_recovery(workspace)
        return assessment.run_active if getattr(assessment, "run_active", None) else None

    def admit_repository_read(self, ctx: RequestContext, folder: str) -> None:
        """Admit a read-only Kriya git command (``git status``) in ``folder``, or refuse it typed. S19 P1b (owner decision A,
        2026-10-20): an adapter that runs its own filter-capable git reaches the ONE repository content-filter authority
        (kriya.repository.git) through this use case, never by importing REPOSITORY; call it immediately before that git
        (nothing is memoized: the configuration can change between two calls). Refuses with
        ``ApplicationRefusal(WORKSPACE_UNAVAILABLE)`` whose ``reason`` is the repository refusal's own reason code and
        whose message is that refusal's (redacted) text."""
        try:
            refuse_repository_content_filters(folder)
        except RepositoryContentFilterRefused as refusal:
            raise ApplicationRefusal(WORKSPACE_UNAVAILABLE, str(refusal), reason=refusal.reason_code) from refusal

    def describe(self, ctx: RequestContext, cfg, requested: str, workspace: str) -> Dict[str, Any]:
        """The facts Kriya reports about an admitted folder (``workspace`` = its canonical spelling, ``requested`` = the path asked for)."""
        checkout = describe_checkout(workspace)
        assessment = assess_recovery(workspace)
        state_directory, state_source = resolve_state_directory(cfg)
        return {
            "workspace": workspace,
            "requested": requested,
            "name": os.path.basename(workspace.rstrip(os.sep)) or workspace,
            "identity": workspace_identity(workspace),
            "checkout": {"state": checkout.state, "root": checkout.root, "head": checkout.head, "branch": checkout.branch, "detached": checkout.detached,
                         "dirty": checkout.dirty, "changed_entries": checkout.changed_entries, "changed_truncated": checkout.changed_truncated,
                         "reason": checkout.reason},
            "recovery": {"status": assessment.status, "run_active": assessment.status == STATUS_RUN_ACTIVE},
            "kriya_directory_present": os.path.isdir(os.path.join(workspace, CONTROL_DIRECTORY)),
            "configuration": {"file_in_workspace": any(os.path.isfile(os.path.join(workspace, n)) for n in CONFIG_FILE_NAMES), "note": CONFIG_NOT_APPLIED},
            "state": {"directory": state_directory, "source": state_source, "trace_database_present": os.path.isfile(trace_db_path(cfg))},
        }

    def create(self, ctx: RequestContext, parent: str, name: Any) -> Dict[str, Any]:
        """Create the empty folder ``name`` in the existing folder ``parent`` (an absolute path the adapter validated)."""
        name = validate_project_name(name)
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
        return {"workspace": workspace, "name": name, "parent": real_parent, "identity": workspace_identity(workspace), "created": True,
                "git_initialized": False,
                "note": "An empty folder was created. Nothing else was written: no Git repository, no configuration, no files."}

    def initialize(self, ctx: RequestContext, workspace: str) -> Dict[str, Any]:
        """Project Setup for the admitted folder ``workspace`` (canonical): Kriya's own recommended configuration, created exclusively."""
        from kriya.application.project_init import (
            initialize_project,  # the configuration schema and yaml only for this use case
        )

        return initialize_project(workspace)


__all__ = ["CONFIG_FILE_NAMES", "CONFIG_NOT_APPLIED", "NAME_MAX", "ProjectService", "validate_project_name"]
