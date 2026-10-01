"""The metric deriver (PRD-033): one pure function from persisted evidence
projections to the metric report content.

Every metric names its evidence and is one of:
- ``{"status": "MEASURED", "value": ..., ...}``;
- ``{"status": "UNAVAILABLE", "reason": ...}``: the evidence does not exist,
  so the value is never a zero or a guess;
- ``{"status": "NOT_PROVIDED"}``: an optional input (workspaces, chaos
  report) was not given.

Populations are never mixed:
- ``generation``: one row per run_generation_workflow call (direct runs,
  milestone units and enforce subtasks alike), plus ``.exception`` rows;
- ``enforce``: the enforce terminal rows;
- ``milestone_plan``: planning rows.

traces.db does not link an enforce subtask's row to its enforce run, so
enforce outcomes are reported only from the terminal rows.

False success and regression escape come only from adjudications (never
from a Reviewer or any model output).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.metrics.adjudication import (
    VERDICT_CONFIRMED_SUCCESS,
    VERDICT_FALSE_SUCCESS,
    VERDICT_REGRESSION_ESCAPE,
    AdjudicationStore,
)
from kriya.metrics.evidence import (
    KIND_ENFORCE,
    KIND_EXCEPTION,
    KIND_GENERATION,
    KIND_MILESTONE_PLAN,
    NO_DEVELOPER,
    TraceRun,
    WorkspaceFacts,
)

REPORT_SCHEMA = "kriya.metrics_report/1"
MEASURED, UNAVAILABLE, NOT_PROVIDED = "MEASURED", "UNAVAILABLE", "NOT_PROVIDED"

CONTEXT_INSUFFICIENCY_TYPES = frozenset({"context_budget_unsatisfiable", "output_budget_unsatisfiable"})
NO_PROGRESS = "no_progress"
CONTAINMENT_FAILED = "containment_setup_failed"
UNAUTHORIZED_TARGET = "unauthorized_generation_target"
STATIC_ANALYSIS_AUTHORIZATION_CODES = frozenset({
    "STATIC_ANALYSIS_EVIDENCE_STALE", "STATIC_ANALYSIS_EVIDENCE_MISSING", "STATIC_ANALYSIS_NOT_PERMITTED",
})
_ROLE_COUNTERS = (
    "calls", "protocol_failures", "schema_failures", "prompt_tokens", "completion_tokens",
    "estimated_token_calls", "attempts", "attempts_passed", "retries_triggered", "structured_valid",
    "structured_malformed", "structured_validation_failures", "structured_policy_rejections",
    "structured_other_failures",
)


def unavailable(reason: str) -> Dict[str, Any]:
    return {"status": UNAVAILABLE, "reason": reason}


def count(value: int) -> Dict[str, Any]:
    return {"status": MEASURED, "value": int(value)}


def ratio(numerator: int, denominator: int, reason: str) -> Dict[str, Any]:
    """A rate over a stated denominator; UNAVAILABLE (never 0) with none."""
    if denominator <= 0:
        return unavailable(reason)
    return {"status": MEASURED, "value": round(numerator / denominator, 4),
            "numerator": int(numerator), "denominator": int(denominator)}


def _distribution(values: Sequence[float], reason: str) -> Dict[str, Any]:
    if not values:
        return unavailable(reason)
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {"status": MEASURED, "count": len(ordered), "total": round(sum(ordered), 3),
            "mean": round(sum(ordered) / len(ordered), 3), "median": round(median, 3), "max": round(ordered[-1], 3)}


def _tally(values: Iterable[Optional[str]]) -> Dict[str, int]:
    tally: Dict[str, int] = {}
    for value in values:
        if value:
            tally[value] = tally.get(value, 0) + 1
    return dict(sorted(tally.items()))


def _first_pass_compile(run: TraceRun) -> Optional[bool]:
    """The run's first compile gate outcome (attempt 1, chronological): did
    the first generated candidate compile? None when no compile gate ran."""
    outcomes = [success for attempt, gate, success in run.gates if attempt == 1 and gate == "compile"]
    return outcomes[0] if outcomes else None


def outcome_block(runs: Sequence[TraceRun]) -> Dict[str, Any]:
    """The run-outcome metrics of one population (or one of its keys)."""
    total = len(runs)
    successes = sum(run.succeeded for run in runs)
    first_pass = [value for value in map(_first_pass_compile, runs) if value is not None]
    walls = [run.total_wall_seconds for run in runs if run.total_wall_seconds > 0]
    timed = [run for run in runs if run.total_wall_seconds > 0 and run.timed_validators > 0]
    verification_total = sum(run.verification_seconds for run in timed)
    timed_wall = sum(run.total_wall_seconds for run in timed)

    def events(kind: str) -> int:
        return sum(run.event_counts.get(kind, 0) for run in runs)

    approvals = [details.get("outcome") for run in runs for details in run.events_of("approval.decision")]
    expansions = [details.get("outcome") for run in runs for details in run.events_of("authority.expansion")]
    return {
        "runs": total,
        "final_verified_success": ratio(successes, total, "no runs"),
        "failure_categories": _tally(run.failure_category for run in runs if not run.succeeded),
        "first_pass_compile": ratio(sum(first_pass), len(first_pass), "no run reached an attempt-1 compile gate"),
        "no_progress_termination": ratio(sum(run.failure_category == NO_PROGRESS for run in runs), total, "no runs"),
        "retries": {
            "full_set": count(sum(run.full_set_retries for run in runs)),
            "targeted": count(sum(run.targeted_retries for run in runs)),
        },
        "context_insufficiency": count(sum(
            1 for run in runs if CONTEXT_INSUFFICIENCY_TYPES & set(run.failure_types))),
        "context_request_fit_reductions": count(events("context.request_fit")),
        "authority_rejections": count(events("operation_authority.rejected") + sum(
            run.failure_category == UNAUTHORIZED_TARGET for run in runs)),
        "authority_expansions": _tally(expansions),
        "containment_failures": count(sum(run.failure_category == CONTAINMENT_FAILED for run in runs)),
        "fallback_transitions": count(sum(bool(details.get("fallback")) for run in runs
                                          for details in run.events_of("model.transition"))),
        "fallback_selection_skips": count(events("model.fallback_selection")),
        "human_escalations": _tally(approvals) if approvals else unavailable(
            "no approval.decision events (runs without human approval, or traced before PRD-033)"),
        "pre_approval_review_unattached": count(events("review.pre_approval_unattached")),
        "workspace_commit_failures": _tally(
            details.get("reason_code") for run in runs for details in run.events_of("workspace_commit.failed")),
        "llm_calls": count(sum(run.llm_calls for run in runs)),
        "wall_time_seconds": _distribution(walls, "no run recorded its wall time"),
        "verification_share": (
            {"status": MEASURED, "value": round(verification_total / timed_wall, 4),
             "verification_seconds": round(verification_total, 3), "wall_seconds": round(timed_wall, 3),
             "runs": len(timed), "coverage": "compile and runtime-verification timings only; test runs are not timed"}
            if timed else unavailable("no run timed a validator (compile/runtime verification)")
        ),
    }


def _grouped(runs: Sequence[TraceRun], key: Callable[[TraceRun], str]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[TraceRun]] = {}
    for run in runs:
        groups.setdefault(key(run), []).append(run)
    return {name: outcome_block(members) for name, members in sorted(groups.items())}


def model_protocol_rows(runs: Sequence[TraceRun]) -> List[Dict[str, Any]]:
    """PRD-018 role-metric rows summed per (exact runtime, inference settings,
    role, task class). An inexact runtime is reported as ``unavailable``."""
    totals: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for run in runs:
        for row in run.role_rows:
            key = (str(row.get("runtime_digest") or "unavailable"),
                   str(row.get("inference_settings_digest") or "unavailable"),
                   str(row.get("role") or "unattributed"), run.task_class)
            total = totals.setdefault(key, {
                "runtime_digest": key[0], "inference_settings_digest": key[1], "role": key[2], "task_class": key[3],
                "models": set(), "runtime_exact": True, "latency_seconds": 0.0, "first_pass_runs": 0,
                "first_pass_successes": 0, **{name: 0 for name in _ROLE_COUNTERS},
            })
            total["models"].add(str(row.get("model") or ""))
            total["runtime_exact"] = total["runtime_exact"] and bool(row.get("runtime_exact"))
            for name in _ROLE_COUNTERS:
                total[name] += int(row.get(name) or 0)
            total["latency_seconds"] = round(total["latency_seconds"] + float(row.get("latency_seconds") or 0.0), 3)
            if row.get("first_pass_success") is not None:
                total["first_pass_runs"] += 1
                total["first_pass_successes"] += int(bool(row["first_pass_success"]))
    rows = []
    for key in sorted(totals):
        total = totals[key]
        total["models"] = sorted(total["models"])
        total["protocol_failure_rate"] = ratio(total["protocol_failures"], total["calls"], "no calls")
        total["structured_output_failure_rate"] = ratio(total["schema_failures"], total["calls"], "no calls")
        total["first_pass_success"] = ratio(total["first_pass_successes"], total["first_pass_runs"],
                                            "no first attempt was charged to this identity")
        rows.append(total)
    return rows


def static_analysis_block(runs: Sequence[TraceRun]) -> Dict[str, Any]:
    """Per run, the last gate result (the one its commit relied on)."""
    finals = [run.events_of("static_analysis.result")[-1] for run in runs if run.events_of("static_analysis.result")]
    commit_codes = [details.get("reason_code") for run in runs for details in run.events_of("workspace_commit.failed")]
    if not finals and not commit_codes:
        return unavailable("no static_analysis.result events (analysis disabled or not configured)")
    waivers = [waiver for details in finals for waiver in (details.get("waiver_ids") or [])]
    return {
        "status": MEASURED,
        "runs_evaluated": len(finals),
        "outcomes": _tally(details.get("outcome") for details in finals),
        "accepted_risk_runs": sum(bool(details.get("accepted_risk")) for details in finals),
        "incomplete_coverage": sum(details.get("coverage") not in (None, "FULL", "NOT_APPLICABLE") for details in finals),
        "reason_codes": _tally(code for details in finals for code in (details.get("reason_codes") or [])),
        "stale_or_missing_authorization_at_commit": sum(code in STATIC_ANALYSIS_AUTHORIZATION_CODES for code in commit_codes),
        "waiver_uses": len(waivers),
        "distinct_waivers": len(set(waivers)),
    }


def adjudication_block(runs: Sequence[TraceRun], store: Optional[AdjudicationStore]) -> Dict[str, Any]:
    if store is None:
        return {"status": NOT_PROVIDED}
    if store.status == "invalid":
        return unavailable(f"the adjudication store is invalid: {store.error}")
    latest = store.by_run() if store.status == "valid" else {}
    successes = [run for run in runs if run.succeeded and run.kind in (KIND_GENERATION, KIND_ENFORCE)]
    judged = [latest[run.run_id] for run in successes if run.run_id in latest]
    verdicts = _tally(record.verdict for record in judged)
    false_success = verdicts.get(VERDICT_FALSE_SUCCESS, 0)
    escapes = verdicts.get(VERDICT_REGRESSION_ESCAPE, 0)
    why = "no SUCCESS run in the window is adjudicated (false success needs deterministic or human adjudication)"
    return {
        "status": MEASURED,
        "store_status": store.status,
        "successful_runs": len(successes),
        "adjudicated_successes": len(judged),
        "adjudication_coverage": ratio(len(judged), len(successes), "no SUCCESS runs"),
        "verdicts": {name: verdicts.get(name, 0) for name in
                     (VERDICT_FALSE_SUCCESS, VERDICT_REGRESSION_ESCAPE, VERDICT_CONFIRMED_SUCCESS)},
        "sources": _tally(record.source for record in judged),
        "false_success_rate": ratio(false_success, len(judged), why),
        "regression_escape_rate": ratio(escapes, len(judged), why),
    }


def chaos_block(chaos: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if chaos is None:
        return {"status": NOT_PROVIDED}
    scenarios = chaos["scenarios"]
    failed = [row for row in scenarios if row.get("verdict") == "FAILED"]
    return {
        "status": MEASURED, "report_digest": chaos.get("content_digest"), "scenarios": len(scenarios),
        "verdicts": _tally(row.get("verdict") for row in scenarios),
        "invariant_failures": len(failed),
        "invariant_failures_by_family": _tally(row.get("family") for row in failed),
    }


def run_record_block(facts: Optional[WorkspaceFacts]) -> Dict[str, Any]:
    if facts is None:
        return {"status": NOT_PROVIDED}
    records = facts.records
    resumed = [record for record in records if record.resumed]
    return {
        "status": MEASURED, "workspaces": facts.workspaces, "missing_workspaces": list(facts.missing),
        "run_records": len(records), "unreadable_run_records": facts.unreadable,
        "lifecycles": _tally(record.lifecycle for record in records),
        "commit_results": _tally(record.commit_result for record in records),
        "recovered": sum(record.lifecycle == "RECOVERED" for record in records),
        "resumed_runs": len(resumed),
        "resume_invalidation": ratio(sum(bool(r.resume_invalidated_stages) for r in resumed), len(resumed),
                                     "no resumed runs"),
        "invalidated_stages": _tally(stage for r in resumed for stage in r.resume_invalidated_stages),
    }


def derive_metrics(
    runs: Sequence[TraceRun], *, adjudications: Optional[AdjudicationStore] = None,
    run_records: Optional[WorkspaceFacts] = None, chaos: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The report content. Pure: the same inputs give the same content."""
    generation = [run for run in runs if run.kind in (KIND_GENERATION, KIND_EXCEPTION)]
    enforce = [run for run in runs if run.kind == KIND_ENFORCE]
    plans = [run for run in runs if run.kind == KIND_MILESTONE_PLAN]
    return {
        "schema": REPORT_SCHEMA,
        "window": {
            "trace_rows": len(runs),
            "first": runs[0].timestamp if runs else None, "last": runs[-1].timestamp if runs else None,
            "run_ids": [run.run_id for run in runs],
        },
        "outcomes": {
            "generation": outcome_block(generation),
            "enforce": outcome_block(enforce) if enforce else unavailable("no enforce terminal rows"),
            "milestone_plans": {"rows": len(plans), "statuses": _tally(run.status for run in plans)},
        },
        "by_task_class": _grouped(generation, lambda run: run.task_class),
        "by_developer_identity": _grouped(generation, lambda run: run.developer_identity or NO_DEVELOPER),
        "model_protocol": model_protocol_rows(runs),
        "static_analysis": static_analysis_block(generation),
        "adjudication": adjudication_block(runs, adjudications),
        "chaos": chaos_block(chaos),
        "run_records": run_record_block(run_records),
    }
