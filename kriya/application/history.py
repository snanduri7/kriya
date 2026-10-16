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

The trace snapshot is read through the ``TraceHistory`` port (kriya/application/services.py), implemented by STATE's snapshot reader
(``kriya.control.history.SnapshotTrace``); nothing here opens a database. Since D2 the service also owns the snapshot use cases the KUP
adapter used to run itself (acquire, list, verify, prune) over STATE's ``HistoryStore``, and takes the published ``Snapshot`` value (not a
reader object) on every history read; a store refusal becomes an ``ApplicationRefusal`` with the same code, message, database state and
extra fields.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from kriya.application.evidence import recorded_evidence_id, recorded_workspace_identity
from kriya.application.services import ApplicationRefusal, TraceHistory
from kriya.application.store_refusal import store_refusals
from kriya.context.model import RequestContext
from kriya.context.workspace_identity import workspace_identities, workspace_identity
from kriya.control.history import RETAIN_SNAPSHOTS, SNAPSHOT_CORRUPT, Snapshot
from kriya.control.history.port import HistoryLocation, HistoryStore
from kriya.policy.authority import scrub_potential_secrets

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


@dataclass(frozen=True)
class SnapshotAnswer:
    """A snapshot use case's answer: the data the adapter serializes and the published snapshot it concerns (its source block)."""

    snapshot: Snapshot
    data: Dict[str, Any]


class HistoryService:
    """The run history of the published trace snapshots of one state directory (STATE's ``HistoryStore``, owned by the composition)."""

    def __init__(self, store: Optional[HistoryStore] = None) -> None:
        self.store = store if store is not None else HistoryStore()

    # -- snapshots (moved from kriya/kup/cli_ops.py in D2: _acquire, _list_snapshots, _verify, _prune)

    def location(self, ctx: RequestContext, cfg) -> HistoryLocation:
        """The state directory, trace store and snapshot directory of ``cfg`` (paths only: nothing is opened or created)."""
        return self.store.location(cfg)

    def snapshot(self, ctx: RequestContext, cfg, snapshot_id: str) -> Snapshot:
        """The published snapshot with exactly this id, cheaply integrity-checked; never another one."""
        with store_refusals():
            return self.store.require(cfg, snapshot_id)

    def summary(self, ctx: RequestContext, cfg, snapshot: Snapshot) -> Dict[str, Any]:
        """The snapshot's acquisition summary with the source store's metadata observed now (``stat`` only)."""
        return self.store.summary(cfg, snapshot)

    def acquire(self, ctx: RequestContext, cfg, *, workspace: Optional[str]) -> SnapshotAnswer:
        """Acquire and publish one consistent snapshot; refused while a run is active for ``workspace`` (when given)."""
        run_active_check = None
        if workspace:
            from kriya.application.project import ProjectService

            def run_active_check() -> Optional[str]:
                return ProjectService().active_run(ctx, workspace)  # read-only: never creates, locks or writes
        with store_refusals():
            result = self.store.acquire(cfg, run_active_check=run_active_check)
        snap = result["snapshot"]
        data = {**self.store.summary(cfg, snap), "orphans_removed": result["orphans_removed"], "pruned": result["pruned"],
                "backup_steps": result["manifest"]["backup_steps"], "duration_ms": result["manifest"]["duration_ms"]}
        return SnapshotAnswer(snapshot=snap, data=data)

    def snapshots(self, ctx: RequestContext, cfg, *, verify: bool) -> Dict[str, Any]:
        """Every published snapshot, newest first, each with its summary (and its digest verified when ``verify``)."""
        location = self.store.location(cfg)
        now = self.store.source_metadata(cfg)
        items = []
        for snap in self.store.published(cfg):
            entry = self.store.summary_of(snap, now)
            if verify:
                entry["digest_verified"] = self.store.verify(snap) is None
            items.append(entry)
        return {"snapshot_directory": location.snapshot_directory, "snapshots": items, "retain": RETAIN_SNAPSHOTS}

    def verify_snapshot(self, ctx: RequestContext, cfg, snapshot_id: str) -> SnapshotAnswer:
        """Digest verification of EXACTLY the requested snapshot. ``data`` lacks only ``verified_at``, which the adapter stamps."""
        with store_refusals():
            snap = self.store.require(cfg, snapshot_id)  # typed MISSING / UNAVAILABLE / CORRUPT; never another id
        started = time.monotonic()
        reason = self.store.verify(snap)
        if reason:
            raise ApplicationRefusal(SNAPSHOT_CORRUPT, reason, snapshot_id=snap.snapshot_id)
        file_meta = snap.manifest.get("snapshot_file", {})
        data = {"snapshot_id": snap.snapshot_id, "digest_verified": True, "sha256": file_meta.get("sha256"), "size": file_meta.get("size"),
                "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
                "guarantee": "digest verified at this request; size and mtime are checked on every later query"}
        return SnapshotAnswer(snapshot=snap, data=data)

    def prune(self, ctx: RequestContext, cfg, *, keep: int) -> Dict[str, Any]:
        """Keep the newest ``keep`` published snapshots (0 removes every one); remove orphaned staging."""
        with store_refusals():
            return self.store.prune(cfg, keep=keep)

    # -- history of one published snapshot

    def list(self, ctx: RequestContext, cfg, snapshot: Snapshot, *, limit: Optional[int], cursor: Optional[str]) -> Dict[str, Any]:
        """One page of runs, newest first (the snapshot reader validates ``limit`` and ``cursor``)."""
        with store_refusals():
            return self.store.trace(snapshot).page(limit, cursor)

    def list_in_workspace(self, ctx: RequestContext, cfg, snapshot: Snapshot, *, limit: int, cursor: Optional[str], workspace: str) -> Dict[str, Any]:
        """One page of the runs whose evidence manifest records ``workspace`` (GUI M5)."""
        with store_refusals():
            return scoped_history_list(cfg, self.store.trace(snapshot), limit, cursor, workspace)

    def detail(self, ctx: RequestContext, cfg, snapshot: Snapshot, run_id: str) -> Dict[str, Any]:
        """The stored row of one run plus the derived ``application_outcome`` section of its recorded gate outcomes."""
        with store_refusals():
            detail = self.store.trace(snapshot).detail(run_id)
        gates = detail["gate_outcomes"]
        outcome = application_outcome(gates["data"] if gates["availability"] == "recorded" else None)
        detail["application_outcome"] = {"availability": "recorded", "provenance": APPLICATION_OUTCOME_PROVENANCE, "reason": None, "data": outcome}
        return detail

    def prompt(self, ctx: RequestContext, cfg, snapshot: Snapshot, run_id: str) -> Dict[str, Any]:
        with store_refusals():
            return self.store.trace(snapshot).prompt(run_id)


__all__ = ["APPLICATION_OUTCOME_PROVENANCE", "BLOCKED_REASON_CODES", "HistoryService", "MAX_SCANNED_RUNS", "OUTPUT_MAX_CHARS",
           "SCAN_PAGE", "STATUS_EXECUTED", "STATUS_NOT_APPLICABLE", "STATUS_UNKNOWN", "SnapshotAnswer", "application_outcome",
           "scoped_history_list"]
