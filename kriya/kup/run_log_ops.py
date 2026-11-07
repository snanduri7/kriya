"""KUP ``run.log`` (owner ruling 2026-11-07, the cockpit's "Kriya Log"): one bounded page of a run's REAL application log, redacted by the logging owner and again by the projection.

Transport only: argument validation (a run id of the control-id shape, an opaque ``o<offset>`` cursor, a limit) before anything is read, and the envelope. The use case is ``client.runs.log``
(kriya/application/run_log.py): the log directory comes from the resolved configuration, never from the request, and no phase is inferred from a message. ``available: false`` with a closed ``reason`` is
an answer (the run wrote no log, the directory is unreadable), not an error: the cockpit says so instead of showing an empty log as if nothing had happened.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency, source_block, validate_run_id
from kriya.kup.store import SnapshotError


def run_log(cfg, request: Dict[str, Any], request_id: str, client) -> Dict[str, Any]:
    run_id = validate_run_id(request.get("run_id"))
    cursor = request.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or re.match(policy.RUN_LOG_CURSOR_PATTERN, cursor) is None):
        raise SnapshotError(policy.INVALID_REQUEST, "cursor must be a value returned by a previous run.log page")
    offset = int(cursor[1:]) if cursor is not None else 0
    limit = request.get("limit", policy.RUN_LOG_DEFAULT_LIMIT)
    if limit is None:
        limit = policy.RUN_LOG_DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= policy.RUN_LOG_MAX_LIMIT:
        raise SnapshotError(policy.INVALID_REQUEST, f"limit must be an integer from 1 to {policy.RUN_LOG_MAX_LIMIT}")
    context = client.context(workspace_root=None, request_id=request_id)
    data = client.runs.log(context, cfg, run_id, offset, limit)
    location = client.history.location(client.context(workspace_root=None, request_id=request_id), cfg)
    return envelope("run.log", request_id, data=data, source=source_block(location.state_directory, location.trace_database, None), consistency=live_consistency())
