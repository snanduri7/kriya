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
import logging
import os
import subprocess
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, TypeVar, cast

from kriya.control.commit_state import UncertainWorkspaceStateError, assess_workspace_commit_state
from kriya.control.persistence import load_run_record, save_run_record
from kriya.control.run_ownership import acquire_run_lock
from kriya.control.run_record import IllegalRunTransitionError, RunLifecycle, RunRecord
from kriya.control.workspace_identity import workspace_identity
from kriya.core.logging_setup import run_log

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


@dataclass(frozen=True)
class RunContext:
    """Evidence that this process currently owns one workspace mutation run."""

    run_id: str
    workspace_path: str
    workspace_id: str
    base_revision: Optional[str]
    base_tree_hash: Optional[str]
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
    return os.path.normcase(os.path.realpath(os.path.abspath(workspace_path)))


def _git_revision(workspace_path: str, revision: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", revision],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def transition_mutating_run(context: RunContext, state: RunLifecycle, **updates: Any) -> RunRecord:
    """Persist one validated lifecycle transition for the active capability."""
    return _persist(context, lambda record: record.transition(state, **updates))


def _persist(context: RunContext, change: Callable[[RunRecord], RunRecord]) -> RunRecord:
    require_mutating_run(context.workspace_path, context)
    current = context._lease.record or load_run_record(context.workspace_path, context.run_id)
    if current is None:
        raise InvalidRunContextError("active RunContext has no durable RunRecord")
    updated = change(current)
    save_run_record(context.workspace_path, updated, expected_revision=current.revision)
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
    intent: str = "APPLY_VERIFIED_CANDIDATE", **evidence: Any,
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
        transaction_id, intent=intent, candidate_hash=candidate_hash, **evidence,
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


def _complete_successful_run(context: RunContext) -> None:
    """Terminal SUCCESS from what the record itself proves.

    Never walks stages or reads the result payload: the record reaches
    SUCCESS only after a settled COMMITTED cycle, or with no commit cycle at
    all (NO_COMMIT), and - PRD-008A - only when every work unit of the plan
    it executed is VERIFIED (partial completion is never SUCCESS, whichever
    mode produced it). Anything else is contradictory and fails closed."""
    unverified = _unverified_work_units(context._lease.record)
    if unverified:
        logger.error(
            "Run %s: reported success but work units %s are not VERIFIED - failing closed.",
            context.run_id, unverified,
        )
        _fail_active_run(context)
        return
    try:
        transition_mutating_run(context, RunLifecycle.SUCCESS)
    except IllegalRunTransitionError as error:
        logger.error("Run %s: reported success contradicts its record: %s", context.run_id, error)
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
            _lease=lease,
        )
        record = RunRecord.new(
            acquired_run_id, context.workspace_id, base_revision, base_tree_hash,
        )
        save_run_record(canonical, record, expected_revision=None)
        lease.record = record
        # The run log wraps the whole run, so terminal-record and retention
        # lines land in it; it never alters the run (see run_log).
        with run_log(context.run_id, canonical):
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


def _config_fingerprint(arguments: dict) -> Optional[str]:
    """The resume checkpoint's own config fingerprint, from the entry
    point's engine (``self.kernel``/``self.workflow_engine.kernel``/``we``)."""
    from kriya.workflow.checkpoint import compute_config_fingerprint

    for value in arguments.values():
        for owner in (value, getattr(value, "workflow_engine", None)):
            config = getattr(getattr(owner, "kernel", None), "config", None)
            dump = getattr(config, "model_dump", None)
            if callable(dump):
                try:
                    payload = dump()
                except Exception:
                    return None
                return compute_config_fingerprint(payload) if isinstance(payload, dict) else None
    return None


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
                result = await function(*args, **kwargs)
                if outermost and lease.record is not None and not lease.record.terminal:
                    payload = result.legacy_result if hasattr(result, "legacy_result") else result
                    if isinstance(payload, dict):
                        status = str(payload.get("status", "")).lower()
                        quality = payload.get("quality_gates_passed")
                        if quality is True or status == "success":
                            _complete_successful_run(context)
                        elif quality is False or status in {"failed", "failure", "needs_review"}:
                            _fail_active_run(context)
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
