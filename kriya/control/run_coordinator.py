"""Single composition root for mutating Kriya runs.

The coordinator turns the process-level workspace lock into an explicit,
short-lived capability.  Public mutation entry points use
``coordinated_mutation`` so callers cannot accidentally bypass ownership;
older callers need no signature change because the adapter acquires a
context when one is not already active.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import subprocess
import time
import weakref
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, TypeVar, cast

from kriya.config.environment import child_environment
from kriya.config.resolution import ProjectStateError, require_run_project
from kriya.context.workspace_identity import authority_root, workspace_identity
from kriya.control.commit_state import (
    REASON_APPLIED_REQUIREMENTS_OPEN,
    REASON_REQUIREMENT_TRUTH_UNRESOLVED,
    REASON_RESULT_CONTRADICTS_RUN_RECORD,
    REASON_UNCERTAIN_RUN_RECORD,
    RECOVERY_COMMAND,
    UncertainWorkspaceStateError,
    assess_workspace_commit_state,
)
from kriya.control.persistence import (
    load_run_record,
    load_work_ledger,
    save_run_record,
    save_work_ledger,
    work_ledger_path,
)
from kriya.control.run_ownership import acquire_run_lock
from kriya.control.run_record import (
    COMMIT_COMMITTED,
    REQUIREMENT_TRUTH_NO_REQUIREMENT_SET,
    TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN,
    IllegalRunTransitionError,
    RunLifecycle,
    RunRecord,
    normalized_requirement_truth,
    requirement_truth_gap,
    summarize_commit_result,
)
from kriya.control.source_identity import SourceIdentity, run_source_identity
from kriya.control.work_ledger import (
    DIRECT_UNIT,
    GRANT_DEVELOPER_ATTEMPT,
    GRANT_UNIT_INVOCATION,
    UNIT_INVOCATION_CEILING,
    WORK_CEILING_EXHAUSTED,
    WORK_LEDGER_MISSING,
    WORK_LEDGER_NOT_PERSISTED,
    WORK_LEDGER_RESUME_REFUSED,
    WorkLedger,
    WorkLedgerError,
)
from kriya.control.work_ledger import (
    WORK_LEDGER_NOT_THIS_WORK as WORK_LEDGER_NOT_THIS_WORK,  # re-exported: the reason codes of a refused resume
)
from kriya.control.work_ledger import (
    WORK_LEDGER_STOP_PREFIXES as WORK_LEDGER_STOP_PREFIXES,  # re-exported: the terminal classification's reason-code prefixes
)
from kriya.control.work_ledger import (
    WORK_LEDGER_UNREADABLE as WORK_LEDGER_UNREADABLE,
)
from kriya.control.work_ledger import (
    ResumeLedgerRefused as ResumeLedgerRefused,  # re-exported: the enforce path's typed stop for a refused resume
)
from kriya.control.work_ledger import (
    resume_ledger_refused_result as resume_ledger_refused_result,  # re-exported: the one ``resume_refused`` result both pipelines return
)
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.core.logging_setup import run_log
from kriya.foundation.human_wait import register_human_wait_scope
from kriya.platform import git_argv, git_environment
from kriya.repository.atomic_write import (
    DurableWriteError as DurableWriteError,  # re-exported: what a durable transition raises (kriya.cli handles it without importing the repository layer)
)
from kriya.repository.carried_state import refuse_repository_carried_control_state
from kriya.repository.git import refuse_repository_content_filters

logger = logging.getLogger(__name__)


class InvalidRunContextError(RuntimeError):
    """A missing, expired, or wrong-workspace mutation capability was used."""


@dataclass
class _RunLease:
    active: bool = True
    mutation_depth: int = 0
    record: Optional[RunRecord] = None
    # PRD-006: isolated candidate workspaces (e.g. the enforce plan worktree)
    # this run created and explicitly authorized. Nested mutation of one of
    # them is part of the same run; nothing else is.
    candidate_workspace_ids: set = field(default_factory=set)
    # PROVIDER-CONTRACT-001A: the monotonic start of this run's generation
    # budget (claim_run_generation_clock).
    generation_clock: Optional[float] = None
    # W3-7: while a human approval is awaited (generation_clock_paused) the budget does not run. ``human_wait_started`` is the monotonic moment the outermost wait
    # began; ``human_wait_depth`` makes overlapping waits one wait.
    human_wait_started: Optional[float] = None
    human_wait_depth: int = 0
    # W3-7: the states that copied the run clock at their start (``follow_run_generation_clock``); a resume sets each to the lease's credited value, so the lease stays the one truth.
    clock_followers: list = field(default_factory=list)
    # S6 (C-02, C-11): the run's OWN history rows (rows that belong to no larger plan: the direct pipeline's row, the enforce
    # controller's ``.enforce`` row): (db path, row id, the row writer's corrector), in first-write order. Each is reconciled with the record after the terminal
    # decision (``_reconcile_trace_rows``).
    trace_rows: list = field(default_factory=list)
    # S8 (C-07, C-08): the run's work ledger (kriya/control/work_ledger.py), created at the first grant or adopted by a resume. ``prior_consumed_seconds`` is the generation
    # time the ledger had consumed before this process; it is folded into ``generation_clock`` when the clock starts, so every reader of the clock sees the same remaining
    # budget. ``work_unit`` is the plan unit whose invocation is granted now (None: the direct shape).
    work_ledger: Optional[WorkLedger] = None
    # Why this run's own new ledger had no valid earlier ledger to carry (WORK_LEDGER_MISSING / WORK_LEDGER_UNREADABLE), None when it carries one.
    displaced_ledger_reason: Optional[str] = None
    # The run's ONE resume-ledger decision: ``resume_adopted`` once it continues the workspace's ledger, ``resume_refusal`` (the reason code) once its resume was refused.
    # A refusal is final for the run: it is granted nothing afterwards.
    resume_adopted: bool = False
    resume_refusal: Optional[str] = None
    prior_consumed_seconds: float = 0.0
    work_unit: Optional[str] = None


@dataclass(frozen=True)
class RunContext:
    """Evidence that this process currently owns one workspace mutation run."""

    run_id: str
    workspace_path: str
    workspace_id: str
    base_revision: Optional[str]
    base_tree_hash: Optional[str]
    # RUN-SOURCE-IDENTITY-001: the Kriya source that executes this run, read ONCE by begin_mutating_run (the only place a
    # RunContext is built). Required: a context cannot exist without it. Evidence serializes this value and reads no git.
    kriya_source: SourceIdentity
    _lease: _RunLease = field(repr=False, compare=False)

    @property
    def active(self) -> bool:
        return self._lease.active

    @property
    def record_revision(self) -> Optional[int]:
        return self._lease.record.revision if self._lease.record is not None else None

    @property
    def is_outermost_mutation(self) -> bool:
        return self._lease.mutation_depth == 1


_ACTIVE_RUN: ContextVar[Optional[RunContext]] = ContextVar(
    "kriya_active_mutating_run", default=None
)


def _canonical_workspace(workspace_path: str) -> str:
    from kriya.context.workspace_identity import canonical_workspace  # PLAT-017: one canonical form everywhere

    return canonical_workspace(workspace_path)


def _git_revision(workspace_path: str, revision: str) -> Optional[str]:
    try:
        result = subprocess.run(
            git_argv(["git", "rev-parse", revision]),
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False, env=git_environment(child_environment()),
        )
    except (OSError, ValueError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def transition_mutating_run(context: RunContext, state: RunLifecycle, *, durable: bool = False, **updates: Any) -> RunRecord:
    """Persist one validated lifecycle transition for the active capability. ``durable=True`` (D1): an authoritative TOOL lifecycle transition - temp write, file fsync, atomic
    replace, parent-directory fsync, every step required (a failure raises kriya.repository.atomic_write.DurableWriteError and the record is not advanced)."""
    ledger = context._lease.work_ledger
    if ledger is not None and state in (RunLifecycle.SUCCESS, RunLifecycle.FAILURE, RunLifecycle.UNCERTAIN):
        # S8 (C-08): the terminal record names the ledger the run worked under and its final counts (derived; the ledger document is the authority). The run's last
        # granted work is over, so its grant is settled with the clock's real consumption first.
        ledger.open_grant = None
        ledger.previous = None      # the run ends without having adopted the ledger it displaced: it was a new run
        _save_work_ledger(context)
        updates.setdefault("work_ledger", ledger.reference())
    return _persist(context, lambda record: record.transition(state, **updates), durable=durable)


def _persist(context: RunContext, change: Callable[[RunRecord], RunRecord], *, durable: bool = False) -> RunRecord:
    require_mutating_run(context.workspace_path, context)
    current = context._lease.record or load_run_record(context.workspace_path, context.run_id)
    if current is None:
        raise InvalidRunContextError("active RunContext has no durable RunRecord")
    updated = change(current)
    try:
        save_run_record(context.workspace_path, updated, expected_revision=current.revision, **({"durable": True} if durable else {}))
    except DurableWriteError as error:
        if error.step == "directory_fsync":
            # The replace HAPPENED: the new record is visible on disk and only its durability is unknown, so this run's view of its own record must follow the file (the caller
            # still treats the transition as failed) - otherwise the run's terminal handling would write against a stale revision.
            context._lease.record = updated
        raise
    context._lease.record = updated
    return updated


def owning_run(workspace_path: str) -> Optional[RunContext]:
    """The active run whose OWN workspace is ``workspace_path``, else None.

    Lifecycle and commit records describe the run's real workspace. Work
    inside an authorized candidate (the enforce plan worktree) is part of
    the run but is not a real-workspace commit, so it is never recorded as
    one; every commit that targets the real workspace is, however deeply
    nested (e.g. once per milestone)."""
    context = current_run_context()
    if context is None or context._lease.record is None:
        return None
    return context if _canonical_workspace(workspace_path) == context.workspace_path else None


def mark_run_stage(workspace_path: str, state: RunLifecycle, **evidence: Any) -> None:
    """Best-effort forward stage marker for the owning run.

    Stage markers are progress evidence, not authority: an out-of-order or
    repeated marker is skipped, and a persistence failure is logged, so a
    marker can never fail a run. Commit intent is NOT a marker - see
    begin_run_commit()."""
    context = owning_run(workspace_path)
    if context is None or context._lease.record.lifecycle_state == state:
        return
    try:
        transition_mutating_run(context, state, **evidence)
    except IllegalRunTransitionError as error:
        logger.debug("Run %s: stage marker skipped: %s", context.run_id, error)
    except Exception as error:
        logger.warning("Run %s: stage %s not persisted: %s", context.run_id, state.value, error)


def annotate_run(workspace_path: str, **evidence: Any) -> Optional[str]:
    """Best-effort evidence annotation for the owning run; returns an error
    string instead of raising."""
    context = owning_run(workspace_path)
    if context is None or context._lease.record.terminal:
        return None
    try:
        _persist(context, lambda record: record.annotate(**evidence))
    except Exception as error:
        logger.warning("Run %s: evidence annotation not persisted: %s", context.run_id, error)
        return f"{type(error).__name__}: {error}"
    return None


def record_model_runtime_use(fingerprint_id: str) -> None:
    """PRD-013: add a model runtime fingerprint id to the active run in this
    execution context (every model call is attributable). Best effort: a
    call outside a run, or a persistence failure, never fails the call."""
    context = current_run_context()
    if context is None or not fingerprint_id:
        return
    record = context._lease.record
    if record is None or record.terminal or fingerprint_id in record.model_runtime_fingerprint_ids:
        return
    try:
        _persist(context, lambda current: current.annotate(
            model_runtime_fingerprint_ids=[*current.model_runtime_fingerprint_ids, fingerprint_id],
        ))
    except Exception as error:
        logger.warning("Run %s: model runtime fingerprint not persisted: %s", context.run_id, error)


def record_model_admission(details: Any) -> None:
    """C-09 / S6 (MODEL-ADMISSION-RUNRECORD-ANNOTATION-001): reference the run's model admission decision on its RunRecord - whether it
    was enforced, whether every dispatchable binding was QUALIFIED, the qualification policy digest, and the digest of the exact decision
    the ``model.admission`` run event carries (the record references evidence, it does not copy it). The FIRST decision of a run is the
    run's (a nested unit re-records the same one). Best effort, like every annotation: it never fails a run."""
    context = current_run_context()
    if context is None or not isinstance(details, dict):
        return
    record = context._lease.record
    if record is None or record.terminal or record.model_admission is not None:
        return
    try:
        decision_digest = hashlib.sha256(json.dumps(details, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        reference = {"enforced": details.get("enforced"), "all_qualified": details.get("all_qualified"),
                     "policy_digest": details.get("policy_digest"), "decision_digest": decision_digest}
        _persist(context, lambda current: current.annotate(model_admission=reference))
    except Exception as error:
        logger.warning("Run %s: model admission reference not persisted: %s", context.run_id, error)


def _now() -> float:
    """The one read of the clock the run's generation budget is measured on (``time.monotonic``). A module-level indirection so the pause arithmetic is tested with injected,
    deterministic time instead of real sleeps."""
    return time.monotonic()


def claim_run_generation_clock() -> Optional[float]:
    """PROVIDER-CONTRACT-001A: the monotonic start of the active run's
    generation budget (``autonomy.generation_time_budget_seconds``). The
    first claim - the run's first model call or generation pass - starts it;
    every later work unit, retry and role reads the same value, so nothing
    inside a run resets the budget. None outside a run.

    W3-7: while a human approval is being awaited (``generation_clock_paused``)
    the start moves forward with the clock, so every reader (the model
    deadline, embeddings, the validator and runtime deadlines, the attempt
    budget check) sees the remaining budget frozen; on resume the wait is
    credited exactly once."""
    context = current_run_context()
    if context is None:
        return None
    lease = context._lease
    paused_since = lease.human_wait_started
    if lease.generation_clock is None:
        # a claim inside a wait starts a clock that is frozen until the wait ends (effective start == now, see below).
        # S8: a resumed ledger's consumption is already spent, so the clock starts that far in the past.
        lease.generation_clock = (paused_since if paused_since is not None else _now()) - lease.prior_consumed_seconds
    if paused_since is not None:
        return lease.generation_clock + (_now() - paused_since)
    return lease.generation_clock


@contextmanager
def generation_clock_paused() -> Iterator[None]:
    """W3-7 (GENERATION-BUDGET-COUNTS-HUMAN-WAIT-001, owner decision 2026-10-30): the run's generation deadline does not run while Kriya waits on a human approval.
    Only that wait is excluded - model inference, tool execution, dependency acquisition and internal computation keep consuming the budget, and no timeout is raised.
    The exit credits the wait exactly once (``finally``: a failing or cancelled callback never leaves the clock paused); overlapping waits count as one; repeated
    pause/resume adds only the real time spent waiting, never budget. A wait before the first claim changes nothing (the clock starts at the first model call). A no-op
    outside a run."""
    context = current_run_context()
    if context is None:
        yield
        return
    lease = context._lease
    lease.human_wait_depth += 1
    if lease.human_wait_depth == 1:
        lease.human_wait_started = _now()
    try:
        yield
    finally:
        lease.human_wait_depth -= 1
        if lease.human_wait_depth == 0:
            started, lease.human_wait_started = lease.human_wait_started, None
            if lease.generation_clock is not None and started is not None:
                lease.generation_clock += max(0.0, _now() - started)
                for reference in lease.clock_followers:
                    follower = reference()
                    if follower is not None:
                        follower.budget_started_monotonic = lease.generation_clock


register_human_wait_scope(generation_clock_paused)   # W3-7: the foundation hook human_wait() enters this scope


def follow_run_generation_clock(state: Any) -> None:
    """W3-7: ``state.budget_started_monotonic`` is a copy of the run clock taken at the start of one invocation (run_lifecycle.open_run_state); the attempt budget check reads
    only that copy. Register it so that a human wait's credit reaches it: on resume it is set to the run lease's (credited) clock. A no-op outside a run."""
    context = current_run_context()
    if context is not None:
        context._lease.clock_followers.append(weakref.ref(state))


# ----------------------------------------------------------------------------- S8 (C-07, C-08): the work ledger on the lease.
# The lease is the ledger's one owner inside a run; ``kriya/control/work_ledger.py`` is its state, ``persistence`` its writer.
# Outside a run every function is a no-op (None / nothing): pure owners and their tests stay unchanged.

def _consumed_seconds(lease: _RunLease) -> float:
    """The generation time the ledger has consumed: what earlier runs consumed plus this process's clock (a human wait excluded, exactly like the clock itself)."""
    if lease.generation_clock is None:
        return lease.prior_consumed_seconds
    moment = lease.human_wait_started if lease.human_wait_started is not None else _now()
    return max(0.0, moment - lease.generation_clock)


def _ledger_on_disk(context: RunContext) -> Tuple[Optional[WorkLedger], Optional[str]]:
    """(the workspace's ledger document as a ledger, None) or (None, why there is none to continue)."""
    if not os.path.lexists(work_ledger_path(context.workspace_path)):
        return None, WORK_LEDGER_MISSING
    try:
        payload = load_work_ledger(context.workspace_path)
        if payload is None:
            raise WorkLedgerError("not a readable document")
        return WorkLedger.from_dict(payload), None
    except Exception as error:
        logger.warning("Run %s: the work ledger cannot be read (%s).", context.run_id, error)
        return None, WORK_LEDGER_UNREADABLE


def _work_ledger(context: RunContext) -> WorkLedger:
    """The run's ledger; created at its first grant. Creating it must not destroy what a later resume decision of this same run would continue: the ledger found on
    disk is carried inside the new one (``previous``) until the run adopts it or ends."""
    lease = context._lease
    if lease.work_ledger is None:
        fresh = WorkLedger.new(context.run_id)
        earlier, lease.displaced_ledger_reason = _ledger_on_disk(context)
        if earlier is not None:
            fresh.previous = earlier.folded().to_dict()
        lease.work_ledger = fresh
    return lease.work_ledger


def _save_work_ledger(context: RunContext) -> Optional[str]:
    """Write the ledger with the clock's current consumption; an error string instead of raising (the caller decides what a failed write means)."""
    ledger = _work_ledger(context)
    ledger.consumed_seconds = max(ledger.consumed_seconds, _consumed_seconds(context._lease))
    try:
        save_work_ledger(context.workspace_path, ledger.to_dict())
    except Exception as error:
        logger.error("Run %s: work ledger not persisted: %s", context.run_id, error)
        return f"{type(error).__name__}: {error}"
    return None


def open_work_ledger() -> Optional[str]:
    """The run's ledger exists durably from here on, before the run does any work of its own. A plan run calls this where it starts (and after its resume decision): work
    that passes no grant (planning, a TOOL subtask) must still leave the ledger a later resume continues - without it, a resume of a run that stopped at such work would
    find no ledger and be refused. None when the ledger is written (or already was, or outside a run); otherwise the error, and nothing is kept in memory."""
    context = current_run_context()
    if context is None or context._lease.resume_refusal is not None or context._lease.work_ledger is not None:
        return None
    _work_ledger(context)
    error = _save_work_ledger(context)
    if error is not None:
        context._lease.work_ledger = None
    return error


def _resume_refused(context: RunContext, reason: str) -> str:
    """The run's resume is refused, once and for the whole run: nothing is granted to it afterwards. A ledger this run displaced before it learned that (a grant made before
    the resume decision) is put back as the workspace's ledger, with what this run already charged added - a refused resume never costs the earlier work its history."""
    lease = context._lease
    lease.resume_refusal = reason
    current = lease.work_ledger
    if current is not None and current.previous is not None:
        restored = WorkLedger.from_dict(current.previous)
        restored.absorb(current)
        restored.runs = [run for run in restored.runs if run != context.run_id]   # this run charged the ledger; it did not continue it
        lease.work_ledger = restored
        _save_work_ledger(context)
    elif current is not None and lease.resume_adopted and len(current.runs) > 1:
        # The ledger was continued before the earlier work was identified, and that work turned out to be another run's: this run is not one of the ledger's runs.
        current.runs = [run for run in current.runs if run != context.run_id]
        _save_work_ledger(context)
    logger.error("Run %s: resume refused - the work ledger cannot be continued (%s). Nothing is granted to this run.", context.run_id, reason)
    return reason


def _resume_refusal_stop(context: RunContext) -> Optional[str]:
    """The typed refusal of every grant asked by a run whose resume was refused, else None."""
    reason = context._lease.resume_refusal
    if reason is None:
        return None
    return f"{WORK_LEDGER_RESUME_REFUSED}: this run's resume was refused ({reason}); no work is granted to it."


def resume_work_ledger(prior_run_id: Optional[str], *, parent_found: bool = True) -> Optional[str]:
    """The active run was asked to resume: it continues the workspace's ledger, or its resume is REFUSED. None when the ledger is adopted (or already was); otherwise the
    reason code, and the caller must stop with the typed refusal - a refused resume is never continued as a fresh run (a new allowance is a new run, asked for as one).

    ``parent_found``: the resume identified the earlier work (a checkpoint, a ControlState) and ``prior_run_id`` is the run that recorded it; the ledger must name that run.
    False: the earlier work is not identified (yet, or at all) - the run continues the ledger the workspace holds, which owns what was consumed whether or not any of the
    earlier work can be reused. The direct pipeline asks this first, before it grants anything (a missing or unreadable ledger is refused with nothing written), and asks
    again with the checkpoint's run once it has read one.

    Never a partial adoption: time, attempts, unit invocations and retry-family history continue together. Whatever this run already recorded under its own ledger is
    added, never dropped."""
    context = current_run_context()
    if context is None:
        return None
    lease = context._lease
    if lease.resume_refusal is not None:
        return lease.resume_refusal
    current = lease.work_ledger
    if current is not None and (prior_run_id in current.runs or (lease.resume_adopted and not parent_found)):
        return None
    if current is None:
        # Nothing was granted yet: the disk holds the earlier state.
        earlier, reason = _ledger_on_disk(context)
        earlier = earlier.folded() if earlier is not None else None
    else:
        # This run already works under its own ledger; the earlier state is the one that ledger displaced.
        earlier = WorkLedger.from_dict(current.previous) if current.previous is not None else None
        reason = lease.displaced_ledger_reason
    if earlier is None:
        return _resume_refused(context, reason or WORK_LEDGER_NOT_THIS_WORK)
    if parent_found and (not prior_run_id or prior_run_id not in earlier.runs):
        # The earlier work names no run, or another one: nothing ties it to the ledger the workspace holds.
        return _resume_refused(context, WORK_LEDGER_NOT_THIS_WORK)
    adopted = earlier.continued_by(context.run_id)
    if current is not None:   # work this run already granted before it learned what it resumes (its time is on the live clock)
        adopted.developer_attempts += current.developer_attempts
        for unit, count in current.unit_invocations.items():
            adopted.unit_invocations[unit] = adopted.unit_invocations.get(unit, 0) + count
        for unit, state in current.retry.items():
            adopted.retry.setdefault(unit, state)
        adopted.open_grant = current.open_grant
    # The earlier consumption is spent: move the clock (and every state that copied it) that far back, once.
    added = adopted.consumed_seconds
    lease.prior_consumed_seconds += added
    if lease.generation_clock is not None:
        lease.generation_clock -= added
        for reference in lease.clock_followers:
            follower = reference()
            if follower is not None:
                follower.budget_started_monotonic = lease.generation_clock
    lease.work_ledger = adopted
    error = _save_work_ledger(context)
    if error is not None:
        # The continuation is not durable: this run would work under a ledger a later resume cannot see.
        lease.work_ledger = current
        lease.prior_consumed_seconds -= added
        if lease.generation_clock is not None:
            lease.generation_clock += added
            for reference in lease.clock_followers:
                follower = reference()
                if follower is not None:
                    follower.budget_started_monotonic = lease.generation_clock
        return _resume_refused(context, WORK_LEDGER_UNREADABLE)
    lease.resume_adopted = True
    logger.info("Run %s: continuing work ledger %s (%.1fs consumed, %d Developer attempt(s), unit invocations %s).",
                context.run_id, adopted.ledger_id, adopted.consumed_seconds, adopted.developer_attempts, dict(sorted(adopted.unit_invocations.items())))
    return None


def _grant(context: RunContext, change: Callable[[WorkLedger], None], undo: Callable[[WorkLedger], None]) -> Optional[str]:
    ledger = _work_ledger(context)
    previous_grant = ledger.open_grant
    change(ledger)
    error = _save_work_ledger(context)
    if error is not None:
        undo(ledger)
        ledger.open_grant = previous_grant
        return f"{WORK_LEDGER_NOT_PERSISTED}: the grant could not be durably recorded ({error}); the work is not started."
    return None


def grant_unit_invocation(unit_id: str) -> Optional[str]:
    """Durably count one invocation of ``unit_id`` BEFORE it starts. None when granted (or outside a run); otherwise the typed refusal: the unit's ceiling is exhausted
    (WORK_CEILING_EXHAUSTED) or the grant could not be written (WORK_LEDGER_NOT_PERSISTED). The count is never given back - not by a failure, a crash or a resume."""
    context = current_run_context()
    if context is None:
        return None
    refusal = _resume_refusal_stop(context)
    if refusal is not None:
        return refusal
    ledger = _work_ledger(context)
    refusal = ledger.invocation_refusal(unit_id)
    if refusal is not None:
        return refusal

    def change(target: WorkLedger) -> None:
        target.unit_invocations[unit_id] = target.unit_invocations.get(unit_id, 0) + 1
        target.open_grant = {"kind": GRANT_UNIT_INVOCATION, "unit_id": unit_id, "reserved_seconds": 0.0}

    def undo(target: WorkLedger) -> None:
        target.unit_invocations[unit_id] -= 1
        if not target.unit_invocations[unit_id]:
            del target.unit_invocations[unit_id]

    return _grant(context, change, undo)


@contextmanager
def work_unit_scope(unit_id: str) -> Iterator[None]:
    """The plan unit whose granted invocation runs inside this scope: nested generation work is attributed to it (its Developer attempts, its retry-family state) instead of
    being counted as a unit of its own. The exit settles the grant with the clock's real consumption."""
    context = current_run_context()
    if context is None:
        yield
        return
    lease = context._lease
    previous, lease.work_unit = lease.work_unit, unit_id
    try:
        yield
    finally:
        lease.work_unit = previous
        settle_work()


def begin_generation_invocation() -> Tuple[str, Optional[str]]:
    """(unit id, refusal) for one generation invocation that starts now. Inside ``work_unit_scope`` the plan's unit loop already granted it. Otherwise (the direct pipeline,
    a milestone unit) the invocation IS the unit's: it is granted here, under the run's active work unit (``direct`` for a plain goal). Outside a run: no ledger, no refusal."""
    context = current_run_context()
    if context is None:
        return DIRECT_UNIT, None
    lease = context._lease
    if lease.work_unit is not None:
        return lease.work_unit, None
    active = lease.record.active_work_unit if lease.record is not None else None
    unit_id = (active or {}).get("work_unit_id") or DIRECT_UNIT
    return unit_id, grant_unit_invocation(unit_id)


def current_work_unit() -> Optional[str]:
    context = current_run_context()
    return context._lease.work_unit if context is not None else None


def grant_developer_attempt(reserved_seconds: float, unit_id: Optional[str] = None) -> Optional[str]:
    """Durably count one Developer attempt BEFORE the model is called, with the time the attempt reserves (the estimate the budget check just admitted). A process that dies
    inside the attempt leaves the grant open, and the run that resumes the ledger is charged the reservation. None when granted (or outside a run), else the typed refusal."""
    context = current_run_context()
    if context is None:
        return None
    refusal = _resume_refusal_stop(context)
    if refusal is not None:
        return refusal
    unit_id = context._lease.work_unit or unit_id or DIRECT_UNIT
    # An invocation granted before the run learned what it resumes (the direct pipeline opens its state first) may have taken the unit past its ceiling once the
    # earlier ledger was adopted: it does no Developer work.
    used = _work_ledger(context).unit_invocations.get(unit_id, 0)
    if used > UNIT_INVOCATION_CEILING:
        return (f"{WORK_CEILING_EXHAUSTED}: unit {unit_id!r} has used all {UNIT_INVOCATION_CEILING} invocations its work ledger grants "
                f"({used} recorded, ledger {_work_ledger(context).ledger_id}); a resume continues the same ledger.")

    def change(target: WorkLedger) -> None:
        target.developer_attempts += 1
        target.open_grant = {"kind": GRANT_DEVELOPER_ATTEMPT, "unit_id": unit_id, "reserved_seconds": max(0.0, float(reserved_seconds))}

    def undo(target: WorkLedger) -> None:
        target.developer_attempts -= 1

    return _grant(context, change, undo)


def settle_work() -> None:
    """Granted work ended in this process: close the open grant with the clock's real consumption. Best-effort (an unwritten settlement leaves the grant open, which only ever
    charges MORE on a resume)."""
    context = current_run_context()
    if context is None or context._lease.work_ledger is None:
        return
    context._lease.work_ledger.open_grant = None
    _save_work_ledger(context)


def unit_retry_state(unit_id: str) -> Optional[Dict[str, Any]]:
    """The retry-family state recorded for ``unit_id`` under the active ledger (None: none recorded, or outside a run)."""
    context = current_run_context()
    if context is None or context._lease.work_ledger is None:
        return None
    state = context._lease.work_ledger.retry.get(unit_id)
    return dict(state) if state is not None else None


def record_unit_retry_state(unit_id: str, state: Dict[str, Any]) -> None:
    """Durably record ``unit_id``'s retry-family state (written at every recorded failure). Best-effort: the in-memory ledger stays authoritative for this process, and an
    unwritten state costs a resumed run its family history only in the direction of less allowance (kriya/workflow/retry_ledger.py restores an absent state as exhausted)."""
    context = current_run_context()
    if context is None or context._lease.resume_refusal is not None:   # a run whose resume was refused did no work and writes no ledger
        return
    _work_ledger(context).retry[unit_id] = dict(state)
    _save_work_ledger(context)


def work_ledger_resumed() -> bool:
    """Whether the active run continues an earlier run's ledger."""
    context = current_run_context()
    return bool(context is not None and context._lease.work_ledger is not None and len(context._lease.work_ledger.runs) > 1)


def owning_run_committed_work_units(workspace_path: str) -> Optional[List[str]]:
    """RunRecord.committed_work_units() of the run owning ``workspace_path``
    (None outside a run)."""
    context = owning_run(workspace_path)
    if context is None or context._lease.record is None:
        return None
    return context._lease.record.committed_work_units()


def owning_run_commits(workspace_path: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """(run_id, a copy of its commit cycles) for the run that owns
    ``workspace_path``, else None. Read-only: callers that attribute commits
    to their own work (the milestone driver) take them from the RunRecord,
    never from a workflow's reported file list."""
    context = owning_run(workspace_path)
    if context is None:
        return None
    return context.run_id, [dict(cycle) for cycle in context._lease.record.commits]


def owning_run_work_unit(workspace_path: str) -> Optional[Dict[str, Any]]:
    """The owning run's active unit of work (PRD-008 S4c), else None."""
    context = owning_run(workspace_path)
    if context is None or context._lease.record.active_work_unit is None:
        return None
    return dict(context._lease.record.active_work_unit)


def begin_run_commit(
    workspace_path: str, transaction_id: str, *, candidate_hash: Optional[str],
    intent: str = "APPLY_VERIFIED_CANDIDATE", contract_registry: Optional[Dict[str, Any]] = None,
    approval: Optional[Dict[str, Any]] = None, **evidence: Any,
) -> Optional[RunContext]:
    """Durably record commit intent before any real-workspace byte changes.

    Returns the owning context (pass it to settle_run_commit), or None when
    no run owns ``workspace_path``. Raises when intent cannot be persisted:
    the caller must then not commit, because a crash inside an unrecorded
    commit could never be recognized afterwards."""
    context = owning_run(workspace_path)
    if context is None:
        return None
    _persist(context, lambda record: record.begin_commit(
        transaction_id, intent=intent, candidate_hash=candidate_hash,
        contract_registry=contract_registry, approval=approval, **evidence,
    ))
    return context


def settle_run_commit(context: Optional[RunContext], result: str) -> Optional[str]:
    """Record the outcome of the current commit cycle; returns an error
    string instead of raising (the workspace outcome already happened)."""
    if context is None:
        return None
    try:
        _persist(context, lambda record: record.settle_commit(result))
    except Exception as error:
        logger.error("Run %s: commit result %s not persisted: %s", context.run_id, result, error)
        return f"{type(error).__name__}: {error}"
    return None


def _unverified_work_units(record: Optional[RunRecord]) -> List[str]:
    """PRD-008A: work units of this run's ExecutionPlan that are not VERIFIED."""
    states = (record.work_unit_states if record is not None else None) or {}
    return sorted(
        unit_id for unit_id, state in states.items()
        if not isinstance(state, dict) or state.get("status") != "VERIFIED"
    )


# The words of a reconciled result (C-02, S6). ``applied_requirements_open`` is the one status of a committed change whose requirement
# truth is not resolved; every surface (CLI exit table, history row, desktop) reads it from the reconciled result or the RunRecord.
RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN = "applied_requirements_open"
RESULT_SUCCESS, RESULT_APPLIED_REQUIREMENTS_OPEN, RESULT_FAILURE, RESULT_UNDECIDED = "SUCCESS", "APPLIED_REQUIREMENTS_OPEN", "FAILURE", "UNDECIDED"


@dataclass(frozen=True)
class ResultDecision:
    """What the result contract decided for one run: the terminal word and, for a non-success, why."""

    outcome: str
    reason: Optional[str] = None


def _claims_success(payload: Dict[str, Any]) -> bool:
    return payload.get("quality_gates_passed") is True or str(payload.get("status", "")).lower() == "success"


def _reports_failure(payload: Dict[str, Any]) -> bool:
    return payload.get("quality_gates_passed") is False or str(payload.get("status", "")).lower() in {
        "failed", "failure", "needs_review", RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN}


def evaluate_result_contract(record: RunRecord, payload: Dict[str, Any]) -> ResultDecision:
    """C-02 (S6; owner rulings 2026-11-15 and 2026-11-16): the ONE decision of a mutating run's terminal result, from the record's own
    facts. The payload is not result authority: its claim is the executor's statement that its mandatory technical gates passed, and it
    can only LOSE a success (a payload that reports failure, or says nothing, never ends SUCCESS); it can never create one. SUCCESS
    needs, all from the record: every work unit of the executed plan VERIFIED, a settled commit (or none), and a requirement truth that
    permits it (``requirement_truth_gap``: every mandatory requirement resolved - verified, or accepted through the sealed B3 modality -
    or an explicitly requirements-free shape). A committed change with any other truth is APPLIED_REQUIREMENTS_OPEN: an explicit
    non-success that keeps the application fact. Requirement policies (record / block) decide only whether a run stops early; they never
    change this word."""
    if not _claims_success(payload):
        return ResultDecision(RESULT_FAILURE if _reports_failure(payload) else RESULT_UNDECIDED, "the run did not report passing gates")
    unverified = _unverified_work_units(record)
    if unverified:
        return ResultDecision(RESULT_FAILURE, f"work units {unverified} are not VERIFIED")
    gap = requirement_truth_gap(record.requirement_truth)
    if gap is None:
        return ResultDecision(RESULT_SUCCESS)
    if not record.unsettled_commits and summarize_commit_result(record.commits) == COMMIT_COMMITTED:
        return ResultDecision(RESULT_APPLIED_REQUIREMENTS_OPEN, gap)
    return ResultDecision(RESULT_FAILURE, gap)


def _record_requirement_truth(context: RunContext, payload: Dict[str, Any]) -> None:
    """The run's FINAL requirement truth, from the outermost entry's terminal evaluation (the requirement owner's own value,
    ``requirements.requirement_truth``, which the result transports), written on the record BEFORE the terminal transition. A truth that
    is missing or malformed writes nothing, so the record keeps none and cannot end SUCCESS. A persistence failure is the same: no
    recorded truth, no success."""
    truth = normalized_requirement_truth(payload.get("requirement_truth"))
    record = context._lease.record
    if truth is None or record is None or record.terminal or record.requirement_truth == truth:
        return
    if truth["status"] == REQUIREMENT_TRUTH_NO_REQUIREMENT_SET:
        # A requirements-free shape is declared by its own entry (the direct tool lifecycle), never by a result: a workflow result that
        # says it has no requirement set is missing its semantic truth, and that is never success.
        logger.error("Run %s: a result declared itself requirements-free; that is not a requirement truth.", context.run_id)
        return
    try:
        _persist(context, lambda current: current.annotate(requirement_truth=truth))
    except Exception as error:
        logger.error("Run %s: requirement truth not persisted (the run cannot end SUCCESS): %s", context.run_id, error)


def _terminate_from_result(context: RunContext, payload: Dict[str, Any]) -> None:
    """The coordinator's terminal transition for a run that returned ``payload`` (the only caller of the result contract)."""
    _record_requirement_truth(context, payload)
    record = context._lease.record
    decision = evaluate_result_contract(record, payload)
    if decision.outcome == RESULT_UNDECIDED:
        return                      # no claim either way: the run's exit fails the record closed, as before
    if decision.outcome == RESULT_SUCCESS:
        try:
            transition_mutating_run(context, RunLifecycle.SUCCESS)
            return
        except IllegalRunTransitionError as error:
            logger.error("Run %s: reported success contradicts its record: %s", context.run_id, error)
    elif decision.outcome == RESULT_APPLIED_REQUIREMENTS_OPEN:
        logger.error("Run %s: the change is committed but %s - ending %s.", context.run_id, decision.reason, TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN)
        try:
            transition_mutating_run(context, RunLifecycle.FAILURE, terminal_status=TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN)
            return
        except IllegalRunTransitionError as error:
            logger.error("Run %s: %s refused by its record: %s", context.run_id, TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN, error)
    elif _claims_success(payload):
        logger.error("Run %s: reported success but %s - failing closed.", context.run_id, decision.reason)
    _fail_active_run(context)


def _fail_active_run(context: RunContext, error: Optional[BaseException] = None) -> None:
    record = context._lease.record
    if record is None or record.terminal:
        return
    uncertain = record.commit_state_unknown or (error is not None and any(
        cls.__name__ == "UncertainCommitError" for cls in type(error).__mro__
    ))
    transition_mutating_run(
        context, RunLifecycle.UNCERTAIN if uncertain else RunLifecycle.FAILURE,
    )


def _open_requirement_ids(record: RunRecord) -> List[str]:
    return [str(item.get("id")) for item in ((record.requirement_truth or {}).get("unresolved") or [])]


def _reconciled_with_record(payload: Dict[str, Any], record: RunRecord, run_id: str) -> Dict[str, Any]:
    """RDCG-B-001 (owner decision 2026-10-23) and C-02 (S6): the ONE result-truth owner for a mutating run. A result that claims success
    while the run's authoritative RunRecord did not end SUCCESS is not terminal success: UNCERTAIN -> ``needs_review`` (with the recovery
    command); terminal status APPLIED_REQUIREMENTS_OPEN -> ``applied_requirements_open`` (the change IS applied and stays applied; the
    requirement truth and the open requirement ids travel with it); any other terminal state -> ``failed``. ``quality_gates_passed`` is the
    aggregate terminal truth (the CLI exit code and every consumer read it as success), so it becomes False; what the executor reported is
    kept beside it as ``technical_gates_passed``, and the factual sub-results the workflow produced (candidate_gates_passed,
    terminal_regression_passed, overall_attempt_passed, files, commit_evidence, post_commit_persistence_errors, ...) are kept as they are."""
    uncertain = record.lifecycle_state == RunLifecycle.UNCERTAIN
    applied_open = not uncertain and record.terminal_status == TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN
    gap = None if uncertain else requirement_truth_gap(record.requirement_truth)
    reconciled = {
        **payload,
        "status": "needs_review" if uncertain else RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN if applied_open else "failed",
        "quality_gates_passed": False,
        "reason_codes": [REASON_UNCERTAIN_RUN_RECORD if uncertain else REASON_APPLIED_REQUIREMENTS_OPEN if applied_open
                         else REASON_REQUIREMENT_TRUTH_UNRESOLVED if gap is not None and not _unverified_work_units(record)
                         else REASON_RESULT_CONTRADICTS_RUN_RECORD],
        "run_record_state": record.lifecycle_state.value,
        "reported_status": payload.get("status"),
        "run_id": payload.get("run_id") or run_id,
        "run_record_id": run_id,
    }
    if uncertain:
        reconciled["recovery_command"] = RECOVERY_COMMAND
        reconciled.setdefault("error", f"The run's commit could not be confirmed in its run record. Run `{RECOVERY_COMMAND}` to settle it from the durable evidence.")
        return reconciled
    if applied_open or gap is not None:
        reconciled["technical_gates_passed"] = True          # the executor's gates passed (its claim); the requirements did not close
        reconciled["commit_result"] = record.commit_result
        reconciled["open_requirements"] = _open_requirement_ids(record)
        if record.requirement_truth is not None and normalized_requirement_truth(payload.get("requirement_truth")) != record.requirement_truth:
            # the result's own truth is not the record's (or is malformed): the record's is the one reported. When they are the same
            # truth the result keeps the requirement owner's full form (it carries each open requirement's text for display).
            reconciled["requirement_truth"] = dict(record.requirement_truth)
    if applied_open:
        reconciled.setdefault("error", (
            f"The change was applied and its technical gates passed, but {gap}"
            + (f" ({', '.join(reconciled['open_requirements'])})" if reconciled["open_requirements"] else "")
            + ". The run is not successful; nothing was rolled back."))
    elif gap is not None and not _unverified_work_units(record):
        reconciled.setdefault("error", f"The run reported passing gates but {gap}; it is not successful.")
    else:
        reconciled.setdefault("error", f"The run reported success but its run record ended {record.lifecycle_state.value}.")
    return reconciled


def note_run_trace_row(db_path: str, row_id: str, reconcile: Callable[..., Any]) -> None:
    """Called by the one history-row writer (``TraceLogger.log_run``) for a row that belongs to no larger plan. Remembers it as the
    active run's own row, with the writer's own corrector for it (``trace.reconcile_run_row``: the row store depends on the coordinator,
    never the reverse - tests/architecture/test_cycles.py). A no-op outside a run."""
    context = current_run_context()
    if context is None:
        return
    key = (db_path, row_id, reconcile)
    if key[:2] not in [row[:2] for row in context._lease.trace_rows]:
        context._lease.trace_rows.append(key)


_ROW_STATUS_BY_RESULT = {"needs_review": "needs_review", RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN: RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN, "failed": "failure"}


def _reconcile_trace_rows(context: RunContext, result: Any) -> None:
    """The history row is written, or corrected, AFTER reconciliation, with the reconciled status and the RunRecord revision (C-02
    'persisted identity and state'). Observational: a failure is logged and never changes the result."""
    record = context._lease.record
    if record is None or not record.terminal or not context._lease.trace_rows:
        return
    payload = result.legacy_result if hasattr(result, "legacy_result") else result
    reconciled_status = _ROW_STATUS_BY_RESULT.get(str(payload.get("status"))) if isinstance(payload, dict) and "run_record_state" in payload else None
    details = {"run_record_id": context.run_id, "run_record_revision": record.revision, "lifecycle_state": record.lifecycle_state.value,
               "terminal_status": record.terminal_status, "commit_result": record.commit_result,
               "requirement_truth": record.requirement_truth, "row_status_corrected": reconciled_status is not None}
    for db_path, row_id, reconcile_run_row in context._lease.trace_rows:
        try:
            # the two row writers' own words for a non-success: the enforce controller's row says "failed", the direct pipeline's "failure"
            row_status = "failed" if reconciled_status == "failure" and row_id.endswith(".enforce") else reconciled_status
            reconcile_run_row(db_path, row_id, status=row_status, details=details)
        except Exception as error:
            logger.warning("Run %s: history row %s not reconciled with the run record: %s", context.run_id, row_id, error)


def current_run_context() -> Optional[RunContext]:
    """Return the active capability in this execution context, if any."""
    context = _ACTIVE_RUN.get()
    return context if context is not None and context.active else None


def require_mutating_run(
    workspace_path: str, context: Optional[RunContext] = None
) -> RunContext:
    """Validate a capability before mutation and return it.

    Capabilities expire when their coordinator context exits and are bound to
    the canonical workspace identity, so neither a saved token nor a path
    alias can be used to authorize a later or different run.
    """
    candidate = context or current_run_context()
    expected = workspace_identity(_canonical_workspace(workspace_path))
    if candidate is None:
        raise InvalidRunContextError("mutating operation requires an active RunContext")
    if not candidate.active:
        raise InvalidRunContextError("RunContext has expired")
    if candidate.workspace_id != expected and expected not in candidate._lease.candidate_workspace_ids:
        raise InvalidRunContextError(
            "RunContext belongs to a different workspace "
            f"({candidate.workspace_path!r}, not {_canonical_workspace(workspace_path)!r}) "
            "and that path is not a candidate workspace this run authorized"
        )
    return candidate


def authorize_candidate_workspace(
    candidate_path: str, context: Optional[RunContext] = None,
) -> Optional[RunContext]:
    """Authorize an isolated candidate workspace created by the active run.

    Called by the owner of the candidate (e.g. the enforce controller right
    after creating its plan worktree). Authorization is explicit and
    lease-scoped - never inferred from path containment, because a candidate
    sandbox may live outside the real workspace - and expires with the run.
    Returns None when no run is active (nothing to extend).
    """
    active = context or current_run_context()
    if active is None:
        return None
    require_mutating_run(active.workspace_path, active)
    active._lease.candidate_workspace_ids.add(
        workspace_identity(_canonical_workspace(candidate_path))
    )
    return active


def candidate_workspace_root(path: str) -> Optional[str]:
    """The active run's canonical workspace when ``path`` is an isolated
    candidate workspace that run authorized (the enforce plan worktree);
    None when no run is active, or ``path`` is the run's own workspace or
    anything the run did not authorize. WORKTREE-CANONICAL-ROOT-001: a
    worktree for a candidate is rooted here, never inside the candidate."""
    context = current_run_context()
    if context is None:
        return None
    canonical = _canonical_workspace(path)
    if canonical == context.workspace_path:
        return None
    if workspace_identity(canonical) in context._lease.candidate_workspace_ids:
        return context.workspace_path
    return None


@contextmanager
def begin_mutating_run(
    workspace_path: str, *, run_id: Optional[str] = None
) -> Iterator[RunContext]:
    """Acquire ownership and create a run capability for ``workspace_path``.

    Lock and capability release run for normal return, exceptions, and
    ``KeyboardInterrupt``.  The OS also releases the underlying lock if the
    process terminates without executing Python cleanup.

    PRD-008 order: lock first, then assess the workspace's PRIOR commit
    state, and only then create this run's record - so every mutating entry
    point (generate, fix, milestones, enforce, direct tool writes, proposal
    execution, any future API) refuses a possibly half-committed workspace
    with UncertainWorkspaceStateError, and the new record can never
    contaminate that assessment. At the end, still under the lock, run state
    is pruned reference-safely (kriya/control/retention.py).
    """
    canonical = _canonical_workspace(workspace_path)
    existing = current_run_context()
    if existing is not None:
        # Nested workflow/controller boundaries are part of the same run.
        yield require_mutating_run(canonical, existing)
        return

    # S19 Q1 (OI-141): a workspace whose own git configuration defines a content-filter command is refused, typed,
    # before the run takes the lock, writes anything or runs any git (every mutating entry: CLI and KUP).
    refuse_repository_content_filters(canonical)
    # D2 M3: Kriya runtime/control state that the repository itself TRACKS (a clone, a checkout, a pull) is never Kriya's own: refused, typed, before the lock.
    refuse_repository_carried_control_state(authority_root(canonical))
    if run_id is None:
        # GUI M6: the first top-level run of a desktop launch takes the launch's own id (kriya/control/launch.py); every other run is unchanged.
        from kriya.control.launch import claim_run_id
        run_id = claim_run_id(canonical)
    with acquire_run_lock(canonical, run_id=run_id) as acquired_run_id:
        assessment = assess_workspace_commit_state(canonical)
        if not assessment.safe:
            raise UncertainWorkspaceStateError(assessment)
        lease = _RunLease()
        base_revision = _git_revision(canonical, "HEAD")
        base_tree_hash = _git_revision(canonical, "HEAD^{tree}")
        context = RunContext(
            run_id=acquired_run_id,
            workspace_path=canonical,
            workspace_id=workspace_identity(canonical),
            base_revision=base_revision,
            base_tree_hash=base_tree_hash,
            kriya_source=run_source_identity(),
            _lease=lease,
        )
        record = RunRecord.new(
            acquired_run_id, context.workspace_id, base_revision, base_tree_hash,
        )
        save_run_record(canonical, record, expected_revision=None)
        lease.record = record
        from kriya.control.launch import note_run_started
        note_run_started(canonical, acquired_run_id, base_revision)
        # The run log wraps the whole run, so terminal-record and retention
        # lines land in it; it never alters the run (see run_log).
        # LR-R1-M1: the attempt-evidence run scope spans the whole run. It is
        # observational (never raises, never alters the run); its store opens
        # at the first scope that knows the configuration.
        with run_log(context.run_id, canonical), attempt_evidence_scope.run_scope(context):
            token = _ACTIVE_RUN.set(context)
            try:
                yield context
            except BaseException as error:
                try:
                    _fail_active_run(context, error)
                except Exception as record_error:  # never mask the run's own error
                    logger.error("Run %s: terminal failure record not persisted: %s", context.run_id, record_error)
                raise
            finally:
                try:
                    _fail_active_run(context)
                except Exception as record_error:
                    logger.error("Run %s: terminal record not persisted: %s", context.run_id, record_error)
                else:
                    from kriya.control.retention import prune_after_run
                    prune_after_run(canonical, context.run_id)
                finally:
                    attempt_evidence_scope.close_run(context, lambda: load_run_record(canonical, context.run_id))
                    from kriya.control.retention import workspace_run_references
                    attempt_evidence_scope.prune_after_run(context, lambda: workspace_run_references(canonical))
                    # Always expire the capability with the lock, so a same-process
                    # caller (e.g. the REPL) can never reuse it without ownership.
                    lease.active = False
                    _ACTIVE_RUN.reset(token)


@contextmanager
def _use_or_begin(
    workspace_path: str, supplied: Optional[RunContext]
) -> Iterator[RunContext]:
    if supplied is None:
        with begin_mutating_run(workspace_path) as context:
            yield context
        return

    context = require_mutating_run(workspace_path, supplied)
    token = _ACTIVE_RUN.set(context)
    try:
        yield context
    finally:
        _ACTIVE_RUN.reset(token)


F = TypeVar("F", bound=Callable[..., Any])


def _run_configuration(arguments: dict) -> Any:
    """The configuration the entry point runs with, from its engine (``self.kernel``/``self.workflow_engine.kernel``/``we``), or None."""
    for value in arguments.values():
        for owner in (value, getattr(value, "workflow_engine", None)):
            config = getattr(getattr(owner, "kernel", None), "config", None)
            if callable(getattr(config, "model_dump", None)):
                return config
    return None


def _config_fingerprint(arguments: dict) -> Optional[str]:
    """The resume checkpoint's own config fingerprint, from the entry point's configuration."""
    from kriya.control.checkpoint import compute_config_fingerprint

    config = _run_configuration(arguments)
    if config is None:
        return None
    try:
        payload = config.model_dump()
    except Exception:
        return None
    return compute_config_fingerprint(payload) if isinstance(payload, dict) else None


def project_refusal_payload(refusal: ProjectStateError, *, goal: Any = None, workspace_path: Any = None, run_id: Optional[str] = None) -> Dict[str, Any]:
    """The structured result of a run refused by the run-entry project rule: failed, nothing written, the refusal's own closed reason code."""
    return {"status": "failed", "quality_gates_passed": False, "files": [], "reason_codes": [refusal.reason_code], "error": str(refusal),
            "goal": goal, "workspace_path": workspace_path, "run_id": run_id}


def _refuse_foreign_project(function: Callable[..., Any], arguments: dict, context: RunContext) -> Any:
    """C-05 (S3, owner decision 2026-11-16): the RUN-ENTRY project rule, at the one boundary every mutating entry point passes. The run's project
    is the workspace this run holds the lease for (``context.workspace_path``; a unit that operates in an authorized candidate still belongs to
    that run, so the project never comes from the unit's directory or from containment). The entry point's configuration is bound to it when it
    carries no identity, and refused when it carries another project's (CONFIG's verdict, ``require_run_project``). Returns None to proceed, or
    the owner's structured refusal (``project_refusal_result``); an entry point without one raises the typed ``ProjectStateError``. Nothing has
    run yet: no model call, no store, no candidate."""
    config = _run_configuration(arguments)
    if config is None:
        return None
    try:
        require_run_project(config, context.workspace_path)
    except ProjectStateError as refusal:
        logger.error("%s refused: %s", function.__qualname__, refusal)
        render = getattr(arguments.get("self"), "project_refusal_result", None)
        if not callable(render):
            raise
        return render(refusal, arguments)
    return None


def _reconcile_result(result: Any, record: RunRecord, run_id: str) -> Any:
    """``result`` unchanged unless it claims success while the run's record did not end SUCCESS (see ``_reconciled_with_record``). A ``WorkflowResult`` carries the payload as
    ``legacy_result``."""
    payload = result.legacy_result if hasattr(result, "legacy_result") else result
    if not isinstance(payload, dict) or not _claims_success(payload) or record.lifecycle_state == RunLifecycle.SUCCESS:
        return result
    logger.error("Run %s: the result claimed success but its run record is %s - reporting it as not successful.", run_id, record.lifecycle_state.value)
    reconciled = _reconciled_with_record(payload, record, run_id)
    return replace(result, legacy_result=reconciled) if hasattr(result, "legacy_result") else reconciled


def coordinated_mutation(function: F) -> F:
    """Ensure an async public mutation API always executes with ownership.

    ``run_context=`` is accepted as an additive capability parameter even
    when the wrapped legacy signature does not expose it.  Omitting it invokes
    the compatibility adapter, which safely acquires a fresh run context.

    When a fresh run is refused because prior commit state is uncertain,
    an owner that defines ``workspace_refusal_result(assessment, arguments)``
    (WorkflowEngine, WorkflowController) returns its own structured refusal;
    any other entry point raises UncertainWorkspaceStateError.
    """
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        supplied = kwargs.pop("run_context", None)
        if supplied is not None and not isinstance(supplied, RunContext):
            raise InvalidRunContextError("run_context must be a RunContext")
        bound = signature.bind_partial(*args, **kwargs)
        workspace_path = bound.arguments.get("workspace_path")
        if not isinstance(workspace_path, (str, os.PathLike)):
            raise InvalidRunContextError(
                f"{function.__qualname__} requires a workspace_path for mutation ownership"
            )
        with ExitStack() as stack:
            try:
                context = stack.enter_context(_use_or_begin(os.fspath(workspace_path), supplied))
            except UncertainWorkspaceStateError as refusal:
                render = getattr(bound.arguments.get("self"), "workspace_refusal_result", None)
                if not callable(render):
                    raise
                logger.error("%s refused: %s", function.__qualname__, refusal)
                return render(refusal.assessment, bound.arguments)
            lease = context._lease
            lease.mutation_depth += 1
            outermost = lease.mutation_depth == 1
            if outermost and lease.record is not None and lease.record.lifecycle_state == RunLifecycle.NEW:
                goal = bound.arguments.get("goal")
                goal_hash = (
                    hashlib.sha256(goal.encode("utf-8")).hexdigest()
                    if isinstance(goal, str) else None
                )
                transition_mutating_run(
                    context, RunLifecycle.RUNNING, goal_hash=goal_hash,
                    effective_config_fingerprint=_config_fingerprint(bound.arguments),
                )
            try:
                result = _refuse_foreign_project(function, bound.arguments, context)
                if result is None:
                    result = await function(*args, **kwargs)
                if outermost and lease.record is not None and not lease.record.terminal:
                    payload = result.legacy_result if hasattr(result, "legacy_result") else result
                    if isinstance(payload, dict):
                        _terminate_from_result(context, payload)
                if outermost and lease.record is not None:
                    result = _reconcile_result(result, lease.record, context.run_id)
                    _reconcile_trace_rows(context, result)
                return result
            except BaseException as error:
                if outermost:
                    try:
                        _fail_active_run(context, error)
                    except Exception as record_error:  # never mask the real error
                        logger.error(
                            "Run %s: terminal failure record not persisted: %s",
                            context.run_id, record_error,
                        )
                raise
            finally:
                lease.mutation_depth -= 1

    return cast(F, wrapper)
