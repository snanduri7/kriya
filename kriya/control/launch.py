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
from typing import Any, Awaitable, Dict, List, Optional

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
        self._trace_run_id: Optional[str] = None
        self._record: Dict[str, Any] = {
            "schema": LAUNCH_SCHEMA, "kind": "kriya.launch", "launch_id": launch_id,
            "workspace": {"path": workspace, "identity": workspace_identity},
            "requested_at": _now(), "goal": goal_digest(goal), "options": options, "preflight": preflight,
            "process": {"pid": os.getpid()}, "phase": PHASE_STARTING, "run": None, "ended": None,
            "history": [{"phase": PHASE_STARTING, "at": _now()}],
            "approval_required": None, "declined_decisions": [], "pending_approval": None, "approval_history": [],
        }
        self._review_sequence = 0
        self.parent_pid = os.getppid()  # the host this process started under: a review whose host is gone is declined (kriya/control/launch_approval.py)

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

    def note_approval_required(self, kind: str, reason: str, details: Dict[str, Any]) -> None:
        """A step that needs a human decision was reached. The desktop has no approval action (owner decision: never auto-approved), so the run stops;
        this records WHICH decision and why. The CALLER then declines (returns False) without prompting, so the workflow stops cleanly with the human-rejected
        outcome; a prompt with no terminal raises click.Abort, which the workflow counts as a failed attempt and RETRIES (MEASURED in the second live smoke).
        The first one is kept (it is the one that stopped the run)."""
        if self._record.get("approval_required") is not None:
            return
        self._record["approval_required"] = {"kind": kind, "reason": reason[:400], "details": details, "noted_at": _now()}
        try:
            save_launch_record(self.workspace, self.launch_id, self._record)
        except Exception as error:
            logger.error("Launch %s: approval requirement not recorded (%s: %s)", self.launch_id, type(error).__name__, error)

    def decline_decision(self, kind: str, reason: str, details: Dict[str, Any]) -> None:
        """A decision the workflow can proceed WITHOUT (a skill gap, a skill conflict, a live-lookup review or query): the desktop declines it - nothing is
        approved, the run continues exactly as it would on a prompt that found no answer - and says so in the record. Informational: it never makes the
        run's outcome 'approval required' (only a decision that stops the run does)."""
        declined = self._record.setdefault("declined_decisions", [])
        if len(declined) >= 20:
            return
        declined.append({"kind": kind, "reason": reason[:300], "details": details, "noted_at": _now()})
        try:
            save_launch_record(self.workspace, self.launch_id, self._record)
        except Exception as error:
            logger.error("Launch %s: declined decision not recorded (%s: %s)", self.launch_id, type(error).__name__, error)

    def note_trace_row(self, trace_run_id: str, run_events: Any) -> None:
        """A traces.db row was written. It belongs to THIS launch only if its recorded ``evidence.attempt_store`` event names the launch's run id - the same
        link the read side resolves (evidence.manifest from_trace). The workflow result's own ``run_id`` is not that id on every path (MEASURED: a checkpoint id)."""
        for event in run_events if isinstance(run_events, list) else []:
            details = event.get("details") if isinstance(event, dict) and event.get("kind") == "evidence.attempt_store" else None
            if isinstance(details, dict) and details.get("run_id") == self.launch_id:
                self._trace_run_id = trace_run_id

    def request_change_approval(self, files: List[Dict[str, str]], reason: str) -> Optional[Awaitable[bool]]:
        """The change gate asked for a human decision. Record the exact pending review durably (bound to this launch, workspace and candidate) and return the
        awaitable that resolves to the human's decision (see kriya/control/launch_approval.py). None = no review could be recorded: the caller declines."""
        from kriya.control.launch_approval import (
            HISTORY_MAX,
            STATUS_EXPIRED,
            STATUS_PENDING,
            await_decision,
            build_pending,
        )

        if not files:
            return None
        self._review_sequence += 1
        pending = build_pending(self.launch_id, self._record["workspace"]["identity"], self._review_sequence, files, reason)
        previous = self._record.get("pending_approval")
        history = list(self._record.get("approval_history") or [])
        if isinstance(previous, dict):
            history.append({**previous, "status": STATUS_EXPIRED if previous.get("status") == STATUS_PENDING else previous.get("status")})
        self._record["approval_history"] = history[-HISTORY_MAX:]
        self._record["pending_approval"] = pending
        try:
            save_launch_record(self.workspace, self.launch_id, self._record)
        except Exception as error:  # no durable pending review means nothing can be approved against it
            logger.error("Launch %s: pending review not recorded (%s: %s)", self.launch_id, type(error).__name__, error)
            self._record["pending_approval"], self._record["approval_history"] = previous, history[:-1] if isinstance(previous, dict) else history
            return None
        return await_decision(self, pending["review_id"], pending["candidate_digest"])

    def resolve_review(self, review_id: str, status: str, decision: Dict[str, Any]) -> bool:
        """Record how the pending review ended BEFORE the workflow is told. False (nothing recorded, so the caller must not approve) when the review is
        no longer the pending one or the record cannot be written."""
        from kriya.control.launch_approval import STATUS_PENDING

        pending = self._record.get("pending_approval")
        if not isinstance(pending, dict) or pending.get("review_id") != review_id or pending.get("status") != STATUS_PENDING:
            return False
        updated = {**pending, "status": status, "decision": decision}
        self._record["pending_approval"] = updated
        try:
            save_launch_record(self.workspace, self.launch_id, self._record)
        except Exception as error:
            logger.error("Launch %s: review decision not recorded (%s: %s)", self.launch_id, type(error).__name__, error)
            self._record["pending_approval"] = pending
            return False
        return True

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
        from kriya.control.launch_approval import STATUS_EXPIRED, STATUS_PENDING

        pending = self._record.get("pending_approval")
        if isinstance(pending, dict) and pending.get("status") == STATUS_PENDING:
            # the launch ends with a review nobody decided (interrupted before the wait began, a crash): the record must not keep claiming it is waiting
            self._record["pending_approval"] = {**pending, "status": STATUS_EXPIRED, "decision": {"decision": None, "decided_at": _now(), "source": "launch-ended"}}
        if result.get("status") == "knowledge_gap":
            # strict KnowledgeGuard stop: acknowledging the gap is a human decision the desktop cannot make (kriya generate --ack-knowledge-gap is a CLI option)
            gaps = [g for g in (result.get("gap_report") or {}).get("gaps", []) if isinstance(g, dict)]
            self.note_approval_required("knowledge_gap_acknowledgement", "KnowledgeGuard found libraries Kriya has no verified information for; acknowledging the gap is a human decision",
                                        {"libraries": [str(g.get("library")) for g in gaps][:20]})
        trace_id = self._trace_run_id
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


def note_approval_required(kind: str, reason: str, details: Dict[str, Any]) -> bool:
    """Called by the interactive callbacks of `kriya generate` before they would prompt. Inside a desktop launch: record the decision and return True (the
    caller then DECLINES without prompting - nothing is approved). Outside a launch: return False and change nothing. Never raises."""
    try:
        if _ACTIVE is None:
            return False
        _ACTIVE.note_approval_required(kind, reason, details)
        return True
    except Exception as error:  # observational
        logger.error("Launch: approval requirement not recorded (%s: %s)", type(error).__name__, error)
        return True


def request_change_approval(files: List[Dict[str, str]], reason: str) -> Optional[Awaitable[bool]]:
    """For the change gate's callback in `kriya generate`: inside a desktop launch with a non-empty change set, the awaitable that resolves to the human
    decision (the callback returns it; the change gate awaits it). None outside a launch, for an empty change set, or when no review could be recorded."""
    try:
        return _ACTIVE.request_change_approval(files, reason) if _ACTIVE is not None else None
    except Exception as error:  # observational: a failure to record means the caller declines
        logger.error("Launch: change review not requested (%s: %s)", type(error).__name__, error)
        return None


def note_trace_row(trace_run_id: str, run_events: Any) -> None:
    """Called by TraceLogger.log_run after a row is written. A no-op outside a desktop launch. Never raises."""
    try:
        if _ACTIVE is not None:
            _ACTIVE.note_trace_row(trace_run_id, run_events)
    except Exception as error:  # observational
        logger.error("Launch: trace row not linked (%s: %s)", type(error).__name__, error)


def decline_decision(kind: str, reason: str, details: Dict[str, Any]) -> bool:
    """For the callbacks of `kriya generate` whose decision the run can proceed without: inside a desktop launch, record that it was declined and return True
    (the caller then returns its declining value WITHOUT prompting); outside a launch return False and change nothing. Never raises."""
    try:
        if _ACTIVE is None:
            return False
        _ACTIVE.decline_decision(kind, reason, details)
        return True
    except Exception as error:  # observational
        logger.error("Launch: declined decision not recorded (%s: %s)", type(error).__name__, error)
        return True


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
