"""KUP execution operations (GUI M6): ``execution.preflight`` and ``launch.status``.

Transport only (full modularization B3, D3): argument validation, the envelope, and the toolchain probe the use case is handed
(``tool_visibility``, looked up here at call time: the documented patch point). Since D3 the folder's configuration is resolved by
``ConfigService`` (CONFIG) inside the service; ``_load_in`` stays as the catalog/context operations' tuple-shaped name for the same
resolution, and ``_CWD_LOCK`` is CONTEXT's one working-directory lock. The use cases are
``kriya.application.RunService.preflight`` / ``.status`` (``client.runs`` through the ``KriyaClient``; kriya/application/run.py); what follows describes them.
The launched process itself (``kriya generate --launch-id``) re-resolves the same document through ``RunService.build_preflight``.

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

from typing import Any, Dict

from kriya.config.service import ConfigService
from kriya.context.model import RequestContext
from kriya.context.working_directory import WORKING_DIRECTORY_LOCK
from kriya.control.launch import valid_launch_id
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency, source_block
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace
from kriya.platform.toolchain_locator import tool_visibility

# The ONE working-directory lock (CONTEXT, since D3); the KUP name is kept for the catalog/context operations' skill discovery.
_CWD_LOCK = WORKING_DIRECTORY_LOCK


def _load_in(workspace: str):
    """(state, cfg, authority, failure) of the folder ``workspace``: ``ConfigService.resolve`` (CONFIG, since D3) in the tuple shape the
    catalog/context operations unpack. The name stays: tests patch ``kriya.kup.{catalog_ops,context_ops}._load_in``."""
    return ConfigService().resolve(RequestContext.local(workspace)).as_load_in()


def preflight(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    _requested, workspace = validated_workspace(request)
    data = client.runs.preflight(client.context(workspace_root=workspace, request_id=request_id), cfg, workspace, tool_visibility=tool_visibility)
    cockpit_state = data["state"]["cockpit_directory"]
    return envelope("execution.preflight", request_id, data=data, source=source_block(cockpit_state, client.history.location(client.context(workspace_root=None, request_id=request_id), cfg).trace_database, None), consistency=live_consistency())


def launch_status(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    launch_id = request.get("launch_id")
    if not valid_launch_id(launch_id):
        raise SnapshotError(policy.INVALID_REQUEST, "launch_id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx")
    _requested, workspace = validated_workspace(request)
    data = client.runs.status(client.context(workspace_root=workspace, request_id=request_id), cfg, workspace, str(launch_id))
    location = client.history.location(client.context(workspace_root=None, request_id=request_id), cfg)  # the state directory of cfg
    return envelope("launch.status", request_id, data=data, source=source_block(location.state_directory, location.trace_database, None), consistency=live_consistency())
