"""Run lifecycle of one ``run_generation_workflow`` invocation (full modularization W4, E2).

Moved out of ``kriya/workflow/workflow.py`` (bodies verbatim; the engine keeps every name tests patch and hands
its collaborators in through ``StageDeps``):

- ``open_run_state``: the run's ``GenerationState`` (claiming the run's generation clock, PROVIDER-CONTRACT-001A)
  and the run-level identity events recorded before any model call (egress authority, attempt evidence store,
  primary model runtime, role independence, applied routes);
- ``trace_run_events``: the run's events for a traces.db row (budget expansions drained in, PRD-018 role metrics);
- ``persist_run_trace``: the run's traces.db row for the pre-Reviewer checkpoint and the terminal (stage 8) write;
- ``record_run_exception``: the ``<trace_id>.exception`` row of a run that raises (MODEL-EVIDENCE-HARDENING-001).

This module never imports the engine facade, the controller or the CLI (``tests/test_outcome.py``).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence

from kriya.control.run_coordinator import claim_run_generation_clock
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.core.state_paths import trace_db_path
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.stage_context import RunState, StageDeps


@dataclass(frozen=True)
class RunTrace:
    """The traces.db identity of one invocation, fixed once the run has its trace id (before any early exit)."""

    trace_id: str
    goal: str
    started_at: float
    milestone_group_id: Optional[str] = None
    milestone_index: Optional[int] = None
    milestone_total: Optional[int] = None
    enforce_run_id: Optional[str] = None


class TraceDetail(NamedTuple):
    """The stage-level content a full trace row carries besides the run state."""

    retrieved_chunks: List[Any]
    active_skills: List[Any]
    prompt_rendered: str


class TerminalTrace(NamedTuple):
    """The terminal row's outcome fields (absent from the pre-Reviewer checkpoint row)."""

    failure_category: Optional[str]
    failure_report: List[Dict[str, Any]]


async def open_run_state(
    deps: StageDeps, *, error_context: Optional[str], engineering_route: Any, process_profile: Any,
) -> RunState:
    """The run's state and its run-level identity events, in the order the run has always recorded them."""
    # PROVIDER-CONTRACT-001A: the generation budget is the run's, not this
    # invocation's - a milestone unit or enforce subtask never restarts it.
    run_clock = claim_run_generation_clock()
    state = deps.new_state(
        error_context=error_context or "",
        engineering_route=engineering_route,
        process_profile=process_profile,
    )
    if run_clock is not None:
        state.budget_started_monotonic = run_clock
    # PRD-012: every outbound channel's configured authority (capability
    # class, destinations, the trusted field they come from, the
    # containment they run under) is persisted with the run. Telemetry
    # only: each channel's own owner still enforces its decision.
    try:
        state.record_event(RunEvent(
            kind="egress.authority", attempt=0, source="workflow.run_generation_workflow",
            authority=EventAuthority.AUXILIARY, message="configured outbound-network authority",
            details=deps.egress_authority_details(deps.config),
        ))
    except Exception as exc:  # never blocks the run; the gap is logged loudly
        deps.logger.warning(f"Could not record the run's egress authority: {exc}")
    # LR-R1-M1: where this run's attempt evidence lives (or why it does
    # not). The only change the recorder makes to a trace row.
    state.record_event(RunEvent(
        kind="evidence.attempt_store", attempt=0, source="workflow.run_generation_workflow",
        authority=EventAuthority.AUXILIARY, message="attempt evidence store",
        details=attempt_evidence_scope.store_pointer(),
    ))
    # PRD-013/014: the exact runtime of the primary model and its
    # qualification state, persisted with the run. Every call also adds
    # its own fingerprint id to the RunRecord (LLMClient).
    try:
        state.record_event(RunEvent(
            kind="model.runtime", attempt=0, source="workflow.run_generation_workflow",
            authority=EventAuthority.AUXILIARY, message="primary model runtime identity",
            details=await deps.primary_model_runtime_details(deps.config),
        ))
    except Exception as exc:
        deps.logger.warning(f"Could not record the run's model runtime identity: {exc}")
    # PRD-018: which roles share which exact runtime (visibility; the
    # independence policy itself is enforced before the workflow starts,
    # kriya/cli.py _workflow_config).
    try:
        state.record_event(RunEvent(
            kind="model.role_independence", attempt=0, source="workflow.run_generation_workflow",
            authority=EventAuthority.AUXILIARY, message="role to exact-runtime assignment",
            details=await asyncio.to_thread(deps.role_independence_details, deps.config),
        ))
    except Exception as exc:
        deps.logger.warning(f"Could not record the run's role-runtime assignment: {exc}")
    # PRD-019: the routes the workflow command applied (candidates,
    # rejections, final route), one event per routed role.
    routing_plan = getattr(deps.config, "_routing_plan", None)
    for decision in (routing_plan.to_events() if routing_plan is not None else []):
        state.record_event(RunEvent(
            kind="model.route", attempt=0, source="workflow.run_generation_workflow",
            authority=EventAuthority.AUXILIARY,
            message=f"{decision['role']} routed to {decision['model']} ({decision['source']})",
            details=decision,
        ))
    return state


def trace_run_events(
    state: RunState, *, model_clients: Sequence[Any], developer_llm: Any, role_metrics_of: Callable[[Any], Any],
) -> List[Dict[str, Any]]:
    """The run's events for the trace, after moving any automatic budget
    expansion recorded by a model client into them (PRD-016)."""
    state.drain_budget_expansions(*model_clients)
    metrics = role_metrics_of(developer_llm)
    if metrics is not None and not state.role_metrics_recorded:
        # PRD-018: the calls per (role, model, exact runtime) since the
        # engine's previous trace row (RoleMetrics.take_unreported).
        state.role_metrics_recorded = True
        state.record_event(RunEvent(
            kind="model.role_metrics", attempt=state.attempt_number, source="workflow",
            authority=EventAuthority.AUXILIARY,
            message="per-role model metrics of this run (observations, not verification evidence)",
            details={"rows": metrics.take_unreported()},
        ))
    return [event.to_dict() for event in state.run_events]


def persist_run_trace(
    deps: StageDeps, state: RunState, trace: RunTrace, detail: TraceDetail, *, status: str,
    terminal: Optional[TerminalTrace] = None, failure_note: str = "Failed to write run trace",
    recorded_note: bool = False,
) -> None:
    """One full traces.db row for this run (``runs.run_id`` is the primary key and ``log_run`` replaces, so a later
    write supersedes an earlier one). Never raises: a trace failure is logged. The row's arguments are evaluated
    in the order the inline writes evaluated them (the store first; ``trace_events`` drains budget expansions and
    records the role-metrics event, so it runs only when the store opened, before the evidence and metrics)."""
    try:
        from kriya.control.trace import TraceLogger
        trace_db = trace_db_path(deps.config)
        trace_logger = TraceLogger(trace_db)
        row: Dict[str, Any] = dict(
            run_id=trace.trace_id,
            goal=trace.goal,
            duration_sec=deps.wall_clock() - trace.started_at,
            # + best_of_n_candidates_tried: retry_count alone understates true effort whenever Best-of-N
            # discarded independent candidates before this run's winning/final attempt (retry_count gets
            # reset to 0 for each fresh candidate - see kriya/workflow/best_of_n.py).
            attempts=state.budgets.retry_count + state.budgets.best_of_n_candidates_tried,
            status=status,
            files_modified=list(state.all_files_written),
            retrieved_chunks=detail.retrieved_chunks,
            active_skills=detail.active_skills,
            prompt_rendered=detail.prompt_rendered,
            gate_outcomes=state.gate_outcomes,
            model_hops=state.model_hops,
        )
        if terminal is not None:
            row["failure_category"] = terminal.failure_category
            row["failure_report"] = terminal.failure_report
        row.update(
            milestone_group_id=trace.milestone_group_id,
            milestone_index=trace.milestone_index,
            milestone_total=trace.milestone_total, enforce_run_id=trace.enforce_run_id,
            run_events=deps.trace_events(state),
            evidence_records=[record.to_dict() for record in state.evidence_records],
            generation_metrics=state.generation_metrics(
                total_wall_seconds=deps.monotonic() - state.generation_started_monotonic,
            ),
        )
        trace_logger.log_run(**row)
        if recorded_note:
            deps.logger.info(f"Persistent run trace recorded: {trace.trace_id}")
    except Exception as trace_ex:
        deps.logger.warning(f"{failure_note}: {trace_ex}")


def record_run_exception(
    engine: Any, trace: Dict[str, Any], error: BaseException, *, logger: Any,
    write_outcome_trace: Callable[..., bool],
) -> None:
    """MODEL-EVIDENCE-HARDENING-001: a run that raises (e.g. mid-planning,
    after Planner/Architect calls) still leaves a traces row,
    ``<trace_id>.exception``, with the exception and every model call no
    earlier row of it reported (each counted once). Not a RunRecord and never
    a success; the exception is re-raised unchanged by the caller."""
    if not trace.get("trace_id"):
        return
    try:
        trace_db = trace_db_path(engine.kernel.config)
    except Exception as trace_error:  # the exception itself still propagates
        logger.warning("No traces.db for the failed run %s: %s", trace.get("trace_id"), trace_error)
        return
    write_outcome_trace(
        trace_db, run_id=f"{trace['trace_id']}.exception", goal=str(trace.get("goal") or ""), status="error",
        llm=getattr(getattr(engine, "developer", None), "llm", None), source="workflow.run_generation_workflow",
        started_at=trace.get("started_at"), failure_category=type(error).__name__,
        milestone_group_id=trace.get("milestone_group_id"), enforce_run_id=trace.get("enforce_run_id"),
        events=[RunEvent(
            kind="run.exception", attempt=0, source="workflow.run_generation_workflow",
            authority=EventAuthority.AUTHORITATIVE, message=f"{type(error).__name__}: {str(error)[:300]}",
            details={"exception_type": type(error).__name__, "message": str(error)[:2000]},
        ).to_dict()],
    )


__all__ = ["RunTrace", "TerminalTrace", "TraceDetail", "open_run_state", "persist_run_trace", "record_run_exception",
           "trace_run_events"]
