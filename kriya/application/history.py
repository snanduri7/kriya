"""HistoryService (slice B2): the run-history read use cases, extracted unchanged from ``kriya/kup/cli_ops.py`` (``_history``),
``kriya/kup/workspace_ops.py`` (the GUI M5 workspace scope of ``history.list``) and ``kriya/kup/application_outcome.py`` (the
PRODUCT-UX-COMPLETION projection of a run's recorded runtime execution, moved here whole).

Run scope: the trace ``runs`` table has no workspace column. The only recorded link from a run to a workspace is its attempt-evidence
manifest (``workspace_identity``), reached through the run's ``evidence.attempt_store`` event. A run whose link is missing or
unreadable is UNATTRIBUTED, never assumed to belong to (or not belong to) the workspace; the counts are reported.

ApplicationOutcome: did Kriya EXECUTE the application it built, and what did it print? A read-only projection of data Kriya already
persisted. Source (TRACED, kriya/workflow/attempt.py): the Runtime Verification Gate runs a resolved command in the sandboxed workspace
and records, in the run's gate outcome (``runs.gate_outcomes``), ``type`` ``run_verification`` (or ``run_verification_hung`` when the
process had to be killed) and ``steps``: per command ``command`` (argv), ``exit_code``, ``stdout``, ``stderr``, ``timed_out``. The
projection only reads those recorded fields. What it never does: invent a command, derive one from a build file, execute anything, or
claim success the record does not carry. ``succeeded`` is true only when the gate outcome says success AND the last step exited 0
without timing out. A run whose goal never triggered the gate is ``NOT_APPLICABLE`` - there is no authoritative "how to start it" in
Kriya, so none is offered. Command and output are passed through the existing secret scrub (kriya/policy/telemetry.py) and bounded.

The trace snapshot is read through the ``TraceHistory`` port (kriya/application/services.py); nothing here opens a database.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from kriya.application.evidence import recorded_evidence_id, recorded_workspace_identity
from kriya.application.services import TraceHistory
from kriya.context.model import RequestContext
from kriya.context.workspace_identity import workspace_identities, workspace_identity
from kriya.policy.telemetry import scrub_potential_secrets

# ------------------------------------------------------------------ application outcome (moved from kriya/kup/application_outcome.py)

STATUS_EXECUTED = "EXECUTED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_UNKNOWN = "UNKNOWN"
RUNTIME_GATE_TYPES = ("run_verification", "run_verification_hung")
# Why runtime verification could not even start, as Kriya's OWN closed vocabulary. The failure records no structured field in the persisted gate outcome (``diagnostics`` stays on the in-memory
# Failure), but Kriya authors the message and begins it with the code and a colon, so the projection reads exactly that prefix against this closed table - never free text, never a substring.
BLOCKED_REASON_CODES = ("MANAGED_SERVICE_APPROVAL_UNSUPPORTED", "REQUIRED_RUNTIME_VERIFICATION_DECLINED", "REQUIRED_RUNTIME_VERIFICATION_DISABLED")
INFRASTRUCTURE_GATE_TYPE = "verification_infrastructure_failure"
OUTPUT_MAX_CHARS = 4000
APPLICATION_OUTCOME_PROVENANCE = "derived:runs.gate_outcomes"


def _bounded(text: Any) -> Dict[str, Any]:
    value = scrub_potential_secrets(text) if isinstance(text, str) else ""
    return {"text": value[:OUTPUT_MAX_CHARS], "truncated": len(value) > OUTPUT_MAX_CHARS}


def _command(argv: Any) -> Optional[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    return scrub_potential_secrets(" ".join(argv))


def _blocked_reason(gate_outcomes: List[Any]) -> Optional[str]:
    """The newest recorded runtime-verification stop whose message begins with a code of the closed table, or None."""
    for gate in reversed(gate_outcomes):
        if isinstance(gate, dict) and gate.get("type") == INFRASTRUCTURE_GATE_TYPE and isinstance(gate.get("output"), str):
            for code in BLOCKED_REASON_CODES:
                if gate["output"].startswith(code + ":"):
                    return code
    return None


def application_outcome(gate_outcomes: Any) -> Dict[str, Any]:
    """The projection for one run's recorded gate outcomes (a list of dicts, or whatever the column held)."""
    if not isinstance(gate_outcomes, list):
        return {"status": STATUS_UNKNOWN, "reason": "no_gate_outcomes_recorded"}
    runtime = [g for g in gate_outcomes if isinstance(g, dict) and g.get("type") in RUNTIME_GATE_TYPES and isinstance(g.get("steps"), list) and g["steps"]]
    if not runtime:
        blocked = _blocked_reason(gate_outcomes)
        return {"status": STATUS_NOT_APPLICABLE, "reason": "no_runtime_execution_recorded", **({"blocked_reason_code": blocked} if blocked else {})}
    gate = runtime[-1]  # the newest recorded execution (the last attempt that ran the application)
    steps: List[Dict[str, Any]] = [s for s in gate["steps"] if isinstance(s, dict)]
    if not steps:
        return {"status": STATUS_UNKNOWN, "reason": "execution_steps_unreadable"}
    commands = [s.get("command") for s in steps]
    from kriya.workflow.verification_authority import (
        deterministic_sequence_kind,  # lazy, as before B2 (the KUP read path loads no kernel module at import)
    )

    if all(isinstance(c, list) and c and all(isinstance(a, str) for a in c) for c in commands) and deterministic_sequence_kind(commands) is not None:
        # every recorded command is a build or test runner: a compile is not "the application ran" (independent review I6)
        return {"status": STATUS_NOT_APPLICABLE, "reason": "build_or_test_execution_only"}
    last = steps[-1]
    exit_code = last.get("exit_code") if isinstance(last.get("exit_code"), int) and not isinstance(last.get("exit_code"), bool) else None
    timed_out = bool(last.get("timed_out")) or gate.get("type") == "run_verification_hung"
    return {
        "status": STATUS_EXECUTED,
        "succeeded": gate.get("success") is True and exit_code == 0 and not timed_out,
        "attempt": gate.get("attempt") if isinstance(gate.get("attempt"), int) else None,
        "command": _command(last.get("command")),
        "commands": [c for c in (_command(s.get("command")) for s in steps) if c is not None],
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": _bounded(last.get("stdout")),
        "stderr": _bounded(last.get("stderr")),
    }


# ------------------------------------------------------------------ workspace scope of history.list (moved from kriya/kup/workspace_ops.py)

MAX_SCANNED_RUNS = 2000
SCAN_PAGE = 200  # rows read per step while filling a scoped page


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
    return recorded_evidence_id(details)


def _verdict(cfg, evidence_id: Optional[str], identities: Iterable[str]) -> str:
    """match | other | unattributed, from the manifest's own recorded workspace_identity and nothing else."""
    state, recorded = recorded_workspace_identity(cfg, evidence_id)
    if state != "recorded":
        return "unattributed"
    return "match" if recorded in set(identities) else "other"


def scoped_history_list(cfg, trace: TraceHistory, n: int, cursor: Optional[str], workspace: str) -> Dict[str, Any]:
    """``history.list`` restricted to runs whose evidence manifest records this workspace (an absolute path; ``n`` the validated page
    size: the adapter checks both arguments). Cursor semantics are unchanged (the cursor names the last scanned row, so a page never
    repeats or skips a row); a page may scan many rows to fill itself, bounded by MAX_SCANNED_RUNS."""
    identities = workspace_identities(workspace)
    matched: List[Dict[str, Any]] = []
    counts = {"matched": 0, "other": 0, "unattributed": 0, "scanned": 0}
    next_cursor = cursor
    truncated = False
    with trace.events_reader() as events_by_run:
        while len(matched) < n:
            if counts["scanned"] >= MAX_SCANNED_RUNS:
                truncated = True
                break
            page = trace.page(SCAN_PAGE, next_cursor)
            events = events_by_run([r["run_id"] for r in page["runs"]]) if page["runs"] else {}
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
                next_cursor = trace.cursor_after(stopped_at["timestamp"], stopped_at["run_id"])
                break
            next_cursor = page["next_cursor"]
            if next_cursor is None:  # the whole history has been scanned
                break
    return {"runs": matched, "next_cursor": next_cursor,
            "workspace_scope": {"workspace_identity": workspace_identity(workspace), "basis": "evidence manifest workspace_identity", **counts,
                                "truncated": truncated}}


class HistoryService:
    """The read side of the run history of one published trace snapshot (``trace``, chosen by the caller per request)."""

    def list(self, ctx: RequestContext, cfg, trace: TraceHistory, *, limit: Optional[int], cursor: Optional[str]) -> Dict[str, Any]:
        """One page of runs, newest first (the trace reader validates ``limit`` and ``cursor``)."""
        return trace.page(limit, cursor)

    def list_in_workspace(self, ctx: RequestContext, cfg, trace: TraceHistory, *, limit: int, cursor: Optional[str], workspace: str) -> Dict[str, Any]:
        """One page of the runs whose evidence manifest records ``workspace`` (GUI M5)."""
        return scoped_history_list(cfg, trace, limit, cursor, workspace)

    def detail(self, ctx: RequestContext, cfg, trace: TraceHistory, run_id: str) -> Dict[str, Any]:
        """The stored row of one run (``trace.detail``) plus the derived ``application_outcome`` section of its recorded gate outcomes."""
        detail = trace.detail(run_id)
        gates = detail["gate_outcomes"]
        outcome = application_outcome(gates["data"] if gates["availability"] == "recorded" else None)
        detail["application_outcome"] = {"availability": "recorded", "provenance": APPLICATION_OUTCOME_PROVENANCE, "reason": None, "data": outcome}
        return detail

    def prompt(self, ctx: RequestContext, cfg, trace: TraceHistory, run_id: str) -> Dict[str, Any]:
        return trace.prompt(run_id)


__all__ = ["APPLICATION_OUTCOME_PROVENANCE", "BLOCKED_REASON_CODES", "HistoryService", "MAX_SCANNED_RUNS", "OUTPUT_MAX_CHARS",
           "SCAN_PAGE", "STATUS_EXECUTED", "STATUS_NOT_APPLICABLE", "STATUS_UNKNOWN", "application_outcome", "scoped_history_list"]
