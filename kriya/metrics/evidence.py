"""Content-free projections of Kriya's persisted run evidence (PRD-033).

Every projection keeps only codes, counts, identities and timings. The
columns and fields that can hold proprietary content (the goal, rendered
prompts, retrieved chunks, gate output, failed content, attempted edits,
file locations, event messages, evidence paths) are never selected or are
dropped at parse time, so no report built from these can leak them.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def canonical_digest(value: Any) -> str:
    """SHA-256 of ``value`` as canonical JSON (sorted keys, no whitespace)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# Trace row kinds, from the run id convention of their writers.
KIND_GENERATION = "generation"  # one run_generation_workflow call (direct, milestone unit, enforce subtask)
KIND_ENFORCE = "enforce"  # workflow_controller's enforce terminal row: <run_id>.enforce
KIND_MILESTONE_PLAN = "milestone_plan"  # kriya plan-milestones: <group>.milestone-plan
KIND_EXCEPTION = "exception"  # a run that raised: <trace_id>.exception
_SUFFIX_KINDS = ((".enforce", KIND_ENFORCE), (".milestone-plan", KIND_MILESTONE_PLAN), (".exception", KIND_EXCEPTION))

UNCLASSIFIED = "unclassified"
NO_DEVELOPER = "none"

# The only role-metric fields read (PRD-018 rows).
_ROLE_FIELDS = (
    "role", "model", "runtime_digest", "runtime_exact", "inference_settings_digest", "calls",
    "protocol_failures", "schema_failures", "latency_seconds", "prompt_tokens", "completion_tokens",
    "estimated_token_calls", "attempts", "attempts_passed", "first_pass_success", "retries_triggered",
    "structured_valid", "structured_malformed", "structured_validation_failures",
    "structured_policy_rejections", "structured_other_failures",
)
# Per event kind, the only detail keys read (everything else, including every
# message, is dropped).
_EVENT_DETAIL_KEYS: Mapping[str, Tuple[str, ...]] = {
    "static_analysis.result": ("outcome", "requirement", "accepted_risk", "reason_codes", "waiver_ids", "coverage"),
    "workspace_commit.failed": ("reason_code", "workspace_state"),
    "review.pre_approval_unattached": ("reason_code",),
    "approval.decision": ("outcome", "triggers"),
    "authority.expansion": ("outcome", "reason_code"),
    "model.transition": ("initial", "fallback"),
}


@dataclass(frozen=True)
class TraceRun:
    """One ``runs`` row, content-free."""

    run_id: str
    timestamp: str
    kind: str
    status: str
    failure_category: Optional[str]
    task_class: str
    duration_seconds: float
    total_wall_seconds: float
    verification_seconds: float
    # Validator invocations that were timed (compile and runtime verification
    # only - test runs are not timed; docs/assurance/KRIYA_PERFORMANCE_TELEMETRY.md).
    timed_validators: int
    llm_calls: int
    full_set_retries: int
    targeted_retries: int
    milestone_group: bool
    # TRACE-ENFORCE-SUBTASK-LINKAGE-001: the enforce run a subtask row belongs to.
    enforce_run_id: Optional[str]
    failure_types: Tuple[str, ...]
    # (attempt, gate type, success)
    gates: Tuple[Tuple[int, str, bool], ...]
    event_counts: Mapping[str, int]
    events: Tuple[Tuple[str, Mapping[str, Any]], ...]
    role_rows: Tuple[Mapping[str, Any], ...]

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def events_of(self, kind: str) -> List[Mapping[str, Any]]:
        return [details for event_kind, details in self.events if event_kind == kind]

    @property
    def developer_identity(self) -> str:
        """The exact runtime + inference settings that made most of this run's
        Developer calls (ties broken by the identity itself), or ``none``."""
        totals: Dict[str, int] = {}
        for row in self.role_rows:
            if row.get("role") == "developer" and int(row.get("calls") or 0) > 0:
                identity = f"{row.get('runtime_digest')}|{row.get('inference_settings_digest')}"
                totals[identity] = totals.get(identity, 0) + int(row.get("calls") or 0)
        if not totals:
            return NO_DEVELOPER
        return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _json(value: Optional[str], default: Any) -> Any:
    try:
        parsed = json.loads(value) if value else default
    except ValueError:
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _kind(run_id: str) -> str:
    for suffix, kind in _SUFFIX_KINDS:
        if run_id.endswith(suffix):
            return kind
    return KIND_GENERATION


def _task_class(kind: str, metrics: Mapping[str, Any]) -> str:
    route = metrics.get("engineering_route")
    if isinstance(route, dict) and isinstance(route.get("kind"), str):
        return route["kind"].lower()
    return kind if kind != KIND_GENERATION else UNCLASSIFIED


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def project_trace_row(row: Mapping[str, Any]) -> TraceRun:
    """The content-free projection of one ``runs`` row (a mapping of the
    columns ``load_trace_runs`` selects)."""
    run_id = str(row["run_id"])
    kind = _kind(run_id)
    metrics = _json(row.get("generation_metrics"), {})
    llm = metrics.get("llm") if isinstance(metrics.get("llm"), dict) else {}
    validators = metrics.get("validators") if isinstance(metrics.get("validators"), dict) else {}
    retry = metrics.get("retry") if isinstance(metrics.get("retry"), dict) else {}
    gates = tuple(
        (int(g.get("attempt") or 0), str(g.get("type") or ""), bool(g.get("success")))
        for g in _json(row.get("gate_outcomes"), []) if isinstance(g, dict)
    )
    failure_types = tuple(
        str(entry.get("failure_type")) for entry in _json(row.get("failure_report"), [])
        if isinstance(entry, dict) and entry.get("failure_type")
    )
    counts: Dict[str, int] = {}
    events: List[Tuple[str, Mapping[str, Any]]] = []
    role_rows: List[Mapping[str, Any]] = []
    for event in _json(row.get("run_events"), []):
        if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
            continue
        event_kind = event["kind"]
        counts[event_kind] = counts.get(event_kind, 0) + 1
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if event_kind == "model.role_metrics":
            role_rows.extend({key: item.get(key) for key in _ROLE_FIELDS}
                             for item in details.get("rows", []) if isinstance(item, dict))
        elif event_kind in _EVENT_DETAIL_KEYS:
            events.append((event_kind, {key: details.get(key) for key in _EVENT_DETAIL_KEYS[event_kind]}))
    return TraceRun(
        run_id=run_id, timestamp=str(row.get("timestamp") or ""), kind=kind,
        status=str(row.get("status") or ""), failure_category=row.get("failure_category") or None,
        task_class=_task_class(kind, metrics), duration_seconds=_number(row.get("duration_sec")),
        total_wall_seconds=_number(metrics.get("total_wall_seconds")),
        verification_seconds=_number(validators.get("wall_seconds")),
        timed_validators=int(_number(validators.get("invocations"))),
        llm_calls=int(_number(llm.get("calls"))),
        full_set_retries=int(_number(retry.get("full_set_attempts"))),
        targeted_retries=int(_number(retry.get("targeted_attempts"))),
        milestone_group=bool(row.get("milestone_group_id")),
        enforce_run_id=str(row["enforce_run_id"]) if row.get("enforce_run_id") else None,
        failure_types=failure_types, gates=gates, event_counts=counts, events=tuple(events),
        role_rows=tuple(role_rows),
    )


# The only columns ever read from traces.db.
_TRACE_COLUMNS = (
    "run_id", "timestamp", "status", "failure_category", "duration_sec", "generation_metrics",
    "gate_outcomes", "failure_report", "run_events", "milestone_group_id", "enforce_run_id",
)


def load_trace_runs(trace_db: str, *, since: Optional[str] = None) -> List[TraceRun]:
    """Every ``runs`` row of ``trace_db`` (read-only), projected, ordered by
    (timestamp, run id). ``since`` keeps rows whose timestamp sorts at or
    after it (the trace's own ``YYYY-MM-DD HH:MM:SS`` format)."""
    if not os.path.exists(trace_db):
        return []
    connection = sqlite3.connect(f"file:{trace_db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        available = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        columns = [column for column in _TRACE_COLUMNS if column in available]
        rows = connection.execute(f"SELECT {', '.join(columns)} FROM runs").fetchall()
    finally:
        connection.close()
    runs = [project_trace_row(dict(row)) for row in rows]
    if since:
        runs = [run for run in runs if run.timestamp >= since]
    return sorted(runs, key=lambda run: (run.timestamp, run.run_id))


@dataclass(frozen=True)
class RunRecordFacts:
    """One RunRecord, content-free (PRD-007/008)."""

    lifecycle: str
    commit_result: str
    terminal_status: Optional[str]
    resume_invalidated_stages: Tuple[str, ...]
    resumed: bool


@dataclass(frozen=True)
class WorkspaceFacts:
    records: Tuple[RunRecordFacts, ...] = ()
    unreadable: int = 0
    workspaces: int = 0
    missing: Tuple[str, ...] = field(default_factory=tuple)


def load_run_record_facts(workspaces: Sequence[str]) -> WorkspaceFacts:
    """RunRecords of each workspace (read-only; an unreadable record is
    counted, never guessed)."""
    from kriya.control.persistence import scan_run_records

    records: List[RunRecordFacts] = []
    unreadable = 0
    missing = []
    for workspace in workspaces:
        if not os.path.isdir(workspace):
            missing.append(os.path.basename(os.path.normpath(workspace)))
            continue
        scan = scan_run_records(workspace)
        unreadable += len(scan.unreadable)
        for record in scan.records:
            decision = record.resume_decision if isinstance(record.resume_decision, dict) else {}
            invalidated = decision.get("invalidated_stages") or ()
            records.append(RunRecordFacts(
                lifecycle=record.lifecycle_state.value, commit_result=record.commit_result,
                terminal_status=record.terminal_status,
                resume_invalidated_stages=tuple(sorted(str(stage) for stage in invalidated)),
                resumed=bool(decision),
            ))
    return WorkspaceFacts(records=tuple(records), unreadable=unreadable, workspaces=len(workspaces),
                          missing=tuple(sorted(missing)))


def load_chaos_report(path: str) -> Mapping[str, Any]:
    """The content section of a PRD-032 chaos report (``chaos-report.json``)."""
    with open(path, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    content = report.get("content") if isinstance(report, dict) else None
    if not isinstance(content, dict) or not isinstance(content.get("scenarios"), list):
        raise ValueError(f"{path} is not a chaos report")
    return {"content_digest": report.get("content_digest"), "scenarios": content["scenarios"]}


def iter_role_rows(runs: Iterable[TraceRun]) -> Iterable[Tuple[TraceRun, Mapping[str, Any]]]:
    for run in runs:
        for row in run.role_rows:
            yield run, row
