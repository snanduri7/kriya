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
from kriya.control.persistence import load_run_record, save_run_record
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
        # a claim inside a wait starts a clock that is frozen until the wait ends (effective start == now, see below)
        lease.generation_clock = paused_since if paused_since is not None else _now()
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
    **evidence: Any,
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
        contract_registry=contract_registry, **evidence,
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
