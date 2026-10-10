"""Launch records: the durable, workspace-bound identity of a run the desktop host started (GUI M6).

A desktop launch needs three things the terminal path never had to provide: an identity the cockpit can follow from the instant it clicks
Start Run, a deterministic link from that identity to the workspace the person opened (including a run that fails before any evidence
exists), and a terminal record that does not depend on the host noticing a process exit. This module is that, and nothing more:

- the launch id IS the run id: ``begin_mutating_run`` accepts a caller-supplied id (``acquire_run_lock(run_id=...)``), so the RunRecord, the
  attempt-evidence store and the launch record share one name from the first write. The id is validated here (a fixed shape, never a path) and
  claimed exactly once, by the first top-level run that starts in the launch's own workspace.
- the record lives beside the RunRecords (``<workspace>/.kriya/control/launches/<id>.json``), written through the same ownership-stamped,
  revision-grounded control writer. It holds identities, digests and a phase - never the goal text, never model or tool content.
- it adds NO second success authority: ``ended`` copies what the workflow itself returned (status, failure category) and says so; the
  RunRecord lifecycle, the evidence store and the trace row remain the sources the cockpit shows as the outcome.

Nothing here decides whether a run may start. The preflight (kriya/kup/execution.py) reports; the workflow's own gates (KnowledgeGuard,
planning, verification, SEC-009, the workspace lock, commit-state assessment) decide, unchanged.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kriya.control.persistence import load_launch_record, save_launch_record

logger = logging.getLogger(__name__)

LAUNCH_SCHEMA = 1
LAUNCH_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")  # the shape of kriya.control.run_ownership._new_run_id()
GOAL_MAX_CHARS = 16000

PHASE_STARTING = "STARTING"   # record written; the run has not claimed the workspace yet
PHASE_RUNNING = "RUNNING"     # the RunRecord exists (the workspace lock is held by this launch)
PHASE_ENDED = "ENDED"         # the launching process finished (any outcome - see ``ended``)
PHASES = (PHASE_STARTING, PHASE_RUNNING, PHASE_ENDED)


class LaunchRefused(Exception):
    """A launch that cannot start. ``code`` is a closed reason; ``message`` is for the person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def valid_launch_id(value: Any) -> bool:
    return isinstance(value, str) and LAUNCH_ID_RE.match(value) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def goal_digest(goal: str) -> Dict[str, Any]:
    return {"sha256": hashlib.sha256(goal.encode("utf-8")).hexdigest(), "chars": len(goal)}


def validate_goal(goal: Any) -> str:
    if not isinstance(goal, str) or not goal.strip():
        raise LaunchRefused("GOAL_MISSING", "an engineering goal is required")
    if len(goal) > GOAL_MAX_CHARS:
        raise LaunchRefused("GOAL_TOO_LONG", f"the goal is {len(goal)} characters; the limit is {GOAL_MAX_CHARS}")
    if "\x00" in goal:
        raise LaunchRefused("GOAL_INVALID", "the goal contains a NUL character")
    return goal


class LaunchSession:
    """One launch: the record and its phase transitions. Every write is best effort ONLY after the first (a launch whose record cannot be
    written is refused, so a run can never exist without its workspace link)."""

    def __init__(self, workspace: str, workspace_identity: str, launch_id: str, goal: str, preflight: Dict[str, Any], options: Dict[str, Any]) -> None:
        if not valid_launch_id(launch_id):
            raise LaunchRefused("LAUNCH_ID_INVALID", "the launch id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx")
        self.workspace = workspace
        self.launch_id = launch_id
        self._claimed = False
        self._record: Dict[str, Any] = {
            "schema": LAUNCH_SCHEMA, "kind": "kriya.launch", "launch_id": launch_id,
            "workspace": {"path": workspace, "identity": workspace_identity},
            "requested_at": _now(), "goal": goal_digest(goal), "options": options, "preflight": preflight,
            "process": {"pid": os.getpid()}, "phase": PHASE_STARTING, "run": None, "ended": None,
            "history": [{"phase": PHASE_STARTING, "at": _now()}],
        }

    @property
    def record(self) -> Dict[str, Any]:
        return self._record

    def open(self) -> None:
        if load_launch_record(self.workspace, self.launch_id) is not None:
            raise LaunchRefused("LAUNCH_ID_IN_USE", f"launch {self.launch_id} already exists")
        try:
            save_launch_record(self.workspace, self.launch_id, self._record)
        except OSError as error:
            raise LaunchRefused("LAUNCH_RECORD_UNAVAILABLE", f"the launch record could not be written: {error}") from error
        activate(self)

    def _transition(self, phase: str, **fields: Any) -> None:
        self._record.update(fields)
        self._record["phase"] = phase
        self._record["history"].append({"phase": phase, "at": _now()})
        try:
            save_launch_record(self.workspace, self.launch_id, self._record)
        except Exception as error:  # the run is already underway: say so loudly, never alter it
            logger.error("Launch %s: record not updated to %s (%s: %s)", self.launch_id, phase, type(error).__name__, error)

    def claim_run_id(self, workspace: str) -> Optional[str]:
        """The id the first top-level run in this launch's workspace must use (once)."""
        if self._claimed or workspace != self.workspace:
            return None
        self._claimed = True
        return self.launch_id

    def run_started(self, run_id: str, base_revision: Optional[str]) -> None:
        if run_id == self.launch_id:
            self._transition(PHASE_RUNNING, run={"run_id": run_id, "started_at": _now(), "base_revision": base_revision})

    def finish(self, exit_code: int, result: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        """Seal the record with what the launching process ended with. ``workflow_*`` are the workflow's own returned words, copied, not judged."""
        result = result if isinstance(result, dict) else {}
        trace_id = result.get("run_id") if isinstance(result.get("run_id"), str) else None
        message = error or _text(result.get("error"))
        self._transition(PHASE_ENDED, ended={
            "at": _now(), "exit_code": exit_code, "workflow_status": _text(result.get("status")),
            "quality_gates_passed": result.get("quality_gates_passed") if isinstance(result.get("quality_gates_passed"), bool) else None,
            "failure_category": _text(result.get("failure_category")), "trace_run_id": trace_id,
            "error": message[:400] if message else None,
            "note": "copied from the workflow's own result; the RunRecord, evidence store and trace row are the outcome authority",
        })
        deactivate(self)


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


# -- the one active launch of this process ----------------------------------------------------------------------------------------------

_ACTIVE: Optional[LaunchSession] = None


def activate(session: LaunchSession) -> None:
    global _ACTIVE
    _ACTIVE = session


def deactivate(session: LaunchSession) -> None:
    global _ACTIVE
    if _ACTIVE is session:
        _ACTIVE = None


def claim_run_id(workspace: str) -> Optional[str]:
    """Called by ``begin_mutating_run`` for a top-level run with no explicit id."""
    return _ACTIVE.claim_run_id(workspace) if _ACTIVE is not None else None


def note_run_started(workspace: str, run_id: str, base_revision: Optional[str]) -> None:
    """Called by ``begin_mutating_run`` once the RunRecord exists. Never raises."""
    try:
        if _ACTIVE is not None and workspace == _ACTIVE.workspace:
            _ACTIVE.run_started(run_id, base_revision)
    except Exception as error:  # observational
        logger.error("Launch: run start not recorded (%s: %s)", type(error).__name__, error)


def read_launch(workspace: str, launch_id: str) -> Optional[Dict[str, Any]]:
    return load_launch_record(workspace, launch_id)


def recorded_phases(record: Dict[str, Any]) -> List[str]:
    return [str(h.get("phase")) for h in record.get("history", []) if isinstance(h, dict)]
