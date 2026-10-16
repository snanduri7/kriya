"""KUP execution operations (GUI M6): ``execution.preflight`` and ``launch.status``.

Transport only (full modularization B3): argument validation, the envelope, and the two mechanisms the use case is handed - ``_load_in``
(the folder's configuration resolved with the folder as the process working directory, under ``_CWD_LOCK``, which every KUP operation that
enters a folder shares) and ``tool_visibility`` (looked up here at call time: the documented patch point). The use cases are
``kriya.application.RunService.preflight`` / ``.status`` (``client.runs`` through the ``KriyaClient``; kriya/application/run.py); what follows describes them.
The launched process itself (``kriya generate --launch-id``) re-resolves the same document through ``RunService.build_preflight`` with
these two mechanisms.

``execution.preflight`` answers, before Start Run, what a run in this folder would be: which configuration Kriya would load, where it would
record its state, which models it would route to, what security authority applies, and whether the cockpit could observe the result. It is
computed by the SAME resolution a terminal ``kriya generate`` performs when run from that folder (the process working directory decides
configuration discovery - kriya/config/config.py), by temporarily entering the folder in this process. Nothing is cached: every call resolves
again, so an approval granted or revoked a moment ago is seen. The host displays the answer and decides nothing from it; the launched
process re-runs the same function and refuses on any blocker, so the preflight cannot be forged by the host.

``launch.status`` reads one launch back from durable records only: the launch record, the RunRecord, the attempt-evidence store and the
workspace run lock. It never infers an outcome from a process exit; a launch that is not ENDED and whose lock is free is reported as
``interrupted`` (the run did not seal), which is neither success nor a failure Kriya recorded.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict

from kriya.control.launch import valid_launch_id
from kriya.core.state_paths import resolve_state_directory
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency, source_block
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace
from kriya.platform.toolchain_locator import tool_visibility

_CWD_LOCK = threading.Lock()


def _blocker(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


def _load_in(workspace: str):
    """(state, cfg, authority, failure): configuration resolved with ``workspace`` as the working directory, exactly as a terminal run there."""
    from kriya.config.authority import ConfigAuthorityError
    from kriya.config.config import load_config, resolve_config_state

    with _CWD_LOCK:
        previous = os.getcwd()
        os.chdir(workspace)
        try:
            try:
                state = resolve_config_state()
            except Exception as error:  # a malformed kriya.yaml fails here, before any classification: a typed blocker, never a traceback
                return None, None, {"status": "failed", "fields": []}, _blocker("CONFIG_LOAD_FAILED", f"{type(error).__name__}: {str(error)[:300]}")
            try:
                cfg = load_config()
            except ConfigAuthorityError as error:
                fields = [{"field": v.field_path, "source": v.source.value, "classification": v.classification.value, "reason": v.reason}
                          for v in sorted(error.violations, key=lambda x: x.field_path)]
                return state, None, {"status": "refused", "fields": fields}, _blocker(
                    "CONFIG_AUTHORITY_REFUSED",
                    f"{len(fields)} field(s) in this folder's configuration need an operator approval (kriya authority approve): "
                    + ", ".join(f["field"] for f in fields[:6]))
            except Exception as error:  # a malformed or unreadable configuration: typed, never a traceback
                return state, None, {"status": "failed", "fields": []}, _blocker("CONFIG_LOAD_FAILED", f"{type(error).__name__}: {str(error)[:300]}")
            status = "approved" if state.violations else "no_findings"
            return state, cfg, {"status": status, "fields": [v.field_path for v in state.violations]}, None
        finally:
            os.chdir(previous)


def preflight(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    _requested, workspace = validated_workspace(request)
    data = client.runs.preflight(client.context(workspace_root=workspace, request_id=request_id), cfg, workspace, load_in=_load_in, tool_visibility=tool_visibility)
    cockpit_state = data["state"]["cockpit_directory"]
    return envelope("execution.preflight", request_id, data=data, source=source_block(cockpit_state, client.history.location(client.context(workspace_root=None, request_id=request_id), cfg).trace_database, None), consistency=live_consistency())


def launch_status(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    launch_id = request.get("launch_id")
    if not valid_launch_id(launch_id):
        raise SnapshotError(policy.INVALID_REQUEST, "launch_id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx")
    _requested, workspace = validated_workspace(request)
    data = client.runs.status(client.context(workspace_root=workspace, request_id=request_id), cfg, workspace, str(launch_id))
    state_dir = resolve_state_directory(cfg)[0]
    return envelope("launch.status", request_id, data=data, source=source_block(state_dir, client.history.location(client.context(workspace_root=None, request_id=request_id), cfg).trace_database, None), consistency=live_consistency())
