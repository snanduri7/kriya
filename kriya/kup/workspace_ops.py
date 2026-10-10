"""KUP workspace operations (GUI M5): ``workspace.describe`` and the workspace scope of ``history.list``.

Kriya is the only interpreter of a folder. The desktop host picks a path; everything it may show about that path comes from here:
the canonical spelling and identity Kriya itself uses (kriya/control/workspace_identity.py), what Git reports, the recovery
assessment, and which state directory Kriya resolves. Read-only: nothing is created, locked, written or executed from the folder
(``describe_checkout`` disables fsmonitor and optional locks). A folder is never classified as greenfield or brownfield: an empty
directory, a Git checkout, a plain project and a modified repository are all reported by the same fields.

Run scope: the trace ``runs`` table has no workspace column. The only recorded link from a run to a workspace is its attempt-evidence
manifest (``workspace_identity``), reached through the run's ``evidence.attempt_store`` event. A run whose link is missing or
unreadable is UNATTRIBUTED, never assumed to belong to (or not belong to) the workspace; the counts are reported.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from kriya.control.recovery import STATUS_RUN_ACTIVE, assess_recovery
from kriya.control.workspace_identity import canonical_workspace, workspace_identities, workspace_identity
from kriya.core.state_paths import resolve_state_directory
from kriya.kup import policy
from kriya.kup.evidence_ops import _recorded_evidence_id, recorded_workspace_identity
from kriya.kup.inspect import encode_cursor, envelope, history_list, live_consistency, source_block
from kriya.kup.store import Snapshot, SnapshotError, connect_read_only, trace_store_path
from kriya.tools.git_metadata import describe_checkout

MAX_SCANNED_RUNS = 2000
SCAN_PAGE = 200  # rows read per step while filling a scoped page
CONFIG_FILE_NAMES = ("kriya.yaml", "kriya.yml")
CONFIG_NOT_APPLIED = ("The cockpit reads with its own configuration directory (KUP host setting); a kriya.yaml inside the opened folder "
                      "is not applied to these reads.")


def _workspace_argument(request: Dict[str, Any]) -> str:
    workspace = request.get("workspace")
    if not isinstance(workspace, str) or not os.path.isabs(workspace) or any(ord(c) < 32 for c in workspace):
        raise SnapshotError(policy.INVALID_REQUEST, "workspace must be an absolute path without control characters")
    return workspace


def describe(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    requested = _workspace_argument(request)
    if not os.path.lexists(requested):
        raise SnapshotError(policy.WORKSPACE_UNAVAILABLE, f"no such folder: {requested}", reason="not_found")
    if not os.path.isdir(requested):
        raise SnapshotError(policy.WORKSPACE_UNAVAILABLE, f"not a folder: {requested}", reason="not_a_directory")
    if not os.access(requested, os.R_OK | os.X_OK):
        raise SnapshotError(policy.WORKSPACE_UNAVAILABLE, f"the folder cannot be read: {requested}", reason="unreadable")
    workspace = canonical_workspace(requested)
    checkout = describe_checkout(workspace)
    assessment = assess_recovery(workspace)
    state_directory, state_source = resolve_state_directory(cfg)
    data = {
        "workspace": workspace,
        "requested": requested,
        "name": os.path.basename(workspace.rstrip(os.sep)) or workspace,
        "identity": workspace_identity(workspace),
        "checkout": {"state": checkout.state, "root": checkout.root, "head": checkout.head, "branch": checkout.branch, "detached": checkout.detached,
                     "dirty": checkout.dirty, "changed_entries": checkout.changed_entries, "changed_truncated": checkout.changed_truncated,
                     "reason": checkout.reason},
        "recovery": {"status": assessment.status, "run_active": assessment.status == STATUS_RUN_ACTIVE},
        "kriya_directory_present": os.path.isdir(os.path.join(workspace, ".kriya")),
        "configuration": {"file_in_workspace": any(os.path.isfile(os.path.join(workspace, n)) for n in CONFIG_FILE_NAMES), "note": CONFIG_NOT_APPLIED},
        "state": {"directory": state_directory, "source": state_source, "trace_database_present": os.path.isfile(trace_store_path(cfg))},
    }
    block = source_block(state_directory, trace_store_path(cfg), None)
    return envelope("workspace.describe", request_id, data=data, source=block, consistency=live_consistency())


# --------------------------------------------------------------- run scope

def _events_by_run(conn: sqlite3.Connection, run_ids: List[str]) -> Dict[str, Any]:
    marks = ",".join("?" for _ in run_ids)
    return {row[0]: row[1] for row in conn.execute(f"SELECT run_id, run_events FROM runs WHERE run_id IN ({marks})", run_ids)}


def _recorded_store_id(events_text: Optional[str]) -> Optional[str]:
    """The evidence run id named by the LAST ``evidence.attempt_store`` event of one row, only when it is a safe run id."""
    if events_text is None:
        return None
    try:
        events = json.loads(events_text)
    except ValueError:
        return None
    details = None
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and event.get("kind") == "evidence.attempt_store":
            details = event.get("details")
    return _recorded_evidence_id(details)


def _verdict(cfg, evidence_id: Optional[str], identities: Iterable[str]) -> str:
    """match | other | unattributed, from the manifest's own recorded workspace_identity and nothing else."""
    state, recorded = recorded_workspace_identity(cfg, evidence_id)
    if state != "recorded":
        return "unattributed"
    return "match" if recorded in set(identities) else "other"


def scoped_history_list(cfg, snapshot: Snapshot, limit: Optional[int], cursor: Optional[str], workspace: str) -> Dict[str, Any]:
    """``history.list`` restricted to runs whose evidence manifest records this workspace. Cursor semantics are unchanged (the cursor names
    the last scanned row, so a page never repeats or skips a row); a page may scan many rows to fill itself, bounded by MAX_SCANNED_RUNS."""
    if not os.path.isabs(workspace):
        raise SnapshotError(policy.INVALID_REQUEST, "workspace must be an absolute path")
    n = policy.LIST_DEFAULT if limit is None else int(limit)
    if n < 1 or n > policy.LIST_MAX:
        raise SnapshotError(policy.INVALID_REQUEST, f"limit must be between 1 and {policy.LIST_MAX}")
    identities = workspace_identities(workspace)
    matched: List[Dict[str, Any]] = []
    counts = {"matched": 0, "other": 0, "unattributed": 0, "scanned": 0}
    next_cursor = cursor
    truncated = False
    conn = connect_read_only(snapshot.db_path)
    try:
        while len(matched) < n:
            if counts["scanned"] >= MAX_SCANNED_RUNS:
                truncated = True
                break
            page = history_list(snapshot, SCAN_PAGE, next_cursor)
            events = _events_by_run(conn, [r["run_id"] for r in page["runs"]]) if page["runs"] else {}
            stopped_at: Optional[Dict[str, Any]] = None
            for row in page["runs"]:
                counts["scanned"] += 1
                verdict = _verdict(cfg, _recorded_store_id(events.get(row["run_id"])), identities)
                if verdict == "match":
                    counts["matched"] += 1
                    matched.append(row)
                    if len(matched) == n:
                        stopped_at = row
                        break
                else:
                    counts[verdict] += 1
            if stopped_at is not None:
                next_cursor = encode_cursor(snapshot.snapshot_id, stopped_at["timestamp"], stopped_at["run_id"])
                break
            next_cursor = page["next_cursor"]
            if next_cursor is None:  # the whole history has been scanned
                break
    finally:
        conn.close()
    return {"runs": matched, "next_cursor": next_cursor,
            "workspace_scope": {"workspace_identity": workspace_identity(workspace), "basis": "evidence manifest workspace_identity", **counts,
                                "truncated": truncated}}
