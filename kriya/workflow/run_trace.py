"""MODEL-EVIDENCE-HARDENING-001: a traces.db row for work that ends outside
``run_generation_workflow``'s own trace writes.

PRD-018 role metrics reach the routing table only through a ``runs`` row's
``model.role_metrics`` event, and each row carries the calls recorded since
the engine's previous row (``RoleMetrics.take_unreported``). Two kinds of
work made model calls without ever writing such a row:

- an enforce run whose structured planning failed (or that ended after its
  last subtask row, e.g. the terminal requirement verifier): its calls were
  lost, or absorbed into the next run's row in a long-lived process;
- ``kriya plan-milestones``, whose Planner calls are its own process's only
  calls.

``write_outcome_trace`` writes one row with the work's real terminal status
and the unreported metrics. It never creates a RunRecord or a lifecycle
state, and never reports a status the work did not have.
"""
from __future__ import annotations

import functools
import logging
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from kriya.workflow.run_events import EventAuthority, RunEvent

logger = logging.getLogger(__name__)


def unreported_role_metrics_event(llm: Any, *, source: str) -> Optional[Dict[str, Any]]:
    """The ``model.role_metrics`` event for the calls ``llm`` recorded since
    its previous report, or None when there are none (or no metrics)."""
    from kriya.core.role_metrics import RoleMetrics

    metrics = getattr(llm, "role_metrics", None)
    if not isinstance(metrics, RoleMetrics):
        return None
    rows = metrics.take_unreported()
    if not rows:
        return None
    return RunEvent(
        kind="model.role_metrics", attempt=0, source=source, authority=EventAuthority.AUXILIARY,
        message="per-role model metrics of this run (observations, not verification evidence)",
        details={"rows": rows},
    ).to_dict()


def write_outcome_trace(
    trace_db: Optional[str], *, run_id: str, goal: str, status: str, llm: Any, source: str,
    started_at: Optional[float] = None, failure_category: Optional[str] = None,
    events: Iterable[Dict[str, Any]] = (), files: Iterable[str] = (),
    milestone_group_id: Optional[str] = None,
) -> bool:
    """Write one ``runs`` row for ``run_id`` with its terminal ``status``,
    ``events`` and the unreported role metrics. Returns whether it was
    written; a trace failure is logged, never raised (the work's own result
    stands either way)."""
    if not trace_db:
        return False
    run_events = [dict(event) for event in events]
    metrics_event = unreported_role_metrics_event(llm, source=source)
    if metrics_event is not None:
        run_events.append(metrics_event)
    try:
        from kriya.core.trace import TraceLogger

        TraceLogger(trace_db).log_run(
            run_id=run_id, goal=goal,
            duration_sec=max(0.0, time.time() - started_at) if started_at else 0.0,
            attempts=0, status=status, files_modified=sorted(files),
            failure_category=failure_category, milestone_group_id=milestone_group_id,
            run_events=run_events,
        )
        return True
    except Exception as error:  # the work's own result stands; tested for the normal output
        logger.warning("Failed to write the %s trace row for %s: %s", source, run_id, error)
        return False


# The active generation run's trace identity, set by the run once it has
# one (``note_run_trace``) and read by ``record_run_exceptions``. One holder
# per call, so a nested run (an enforce subtask) never overwrites its caller's.
_RUN_TRACE: ContextVar[Optional[Dict[str, Any]]] = ContextVar("kriya_run_trace", default=None)


def note_run_trace(**fields: Any) -> None:
    holder = _RUN_TRACE.get()
    if holder is not None:
        holder.update(fields)


def record_run_exceptions(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Decorates ``WorkflowEngine.run_generation_workflow``: an exception
    (or cancellation) escaping the run writes its ``.exception`` trace row
    (``workflow._record_run_exception``) and is re-raised unchanged."""
    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        holder: Dict[str, Any] = {}
        token = _RUN_TRACE.set(holder)
        try:
            return await func(self, *args, **kwargs)
        except BaseException as error:
            from kriya.workflow.workflow import _record_run_exception

            try:
                _record_run_exception(self, holder, error)
            except Exception as trace_error:  # never replaces the run's own exception
                logger.warning("Could not record the run's exception trace: %s", trace_error)
            raise
        finally:
            _RUN_TRACE.reset(token)

    return wrapper


__all__ = ["note_run_trace", "record_run_exceptions", "unreported_role_metrics_event", "write_outcome_trace"]
