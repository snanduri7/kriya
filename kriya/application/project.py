"""ProjectService read side (slice B2): what Kriya reports about a folder, extracted unchanged from ``kriya/kup/workspace_ops.py``
(``workspace.describe``, GUI M5) and ``kriya/kup/cli_ops.py`` (``workspace.status`` and the snapshot-acquisition run guard).

Kriya is the only interpreter of a folder: the canonical spelling and identity Kriya itself uses (kriya/context/workspace_identity.py),
what Git reports, the recovery assessment, and which state directory Kriya resolves. Read-only: nothing is created, locked, written
or executed from the folder (``describe_checkout`` disables fsmonitor and optional locks). A folder is never classified as greenfield
or brownfield: an empty directory, a Git checkout, a plain project and a modified repository are all reported by the same fields.

The adapter validates the folder argument (``kriya.kup.workspace_ops.validated_workspace``: the one admission rule for opening a
folder) before any of these runs.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from kriya.context.model import RequestContext
from kriya.context.workspace_identity import workspace_identity
from kriya.control.control_store import CONTROL_DIRECTORY
from kriya.control.recovery import STATUS_CLEAN, STATUS_RUN_ACTIVE, assess_recovery
from kriya.core.state_paths import resolve_state_directory, trace_db_path
from kriya.tools.git_metadata import describe_checkout

CONFIG_FILE_NAMES = ("kriya.yaml", "kriya.yml")
CONFIG_NOT_APPLIED = ("The cockpit reads with its own configuration directory (KUP host setting); a kriya.yaml inside the opened folder "
                      "is not applied to these reads.")


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


__all__ = ["CONFIG_FILE_NAMES", "CONFIG_NOT_APPLIED", "ProjectService"]
