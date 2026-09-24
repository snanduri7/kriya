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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Iterator, Optional, TypeVar, cast

from kriya.control.persistence import load_run_record, save_run_record
from kriya.control.run_ownership import acquire_run_lock
from kriya.control.run_record import RunLifecycle, RunRecord
from kriya.control.workspace_identity import workspace_identity


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
    require_mutating_run(context.workspace_path, context)
    current = context._lease.record or load_run_record(context.workspace_path, context.run_id)
    if current is None:
        raise InvalidRunContextError("active RunContext has no durable RunRecord")
    updated = current.transition(state, **updates)
    save_run_record(context.workspace_path, updated, expected_revision=current.revision)
    context._lease.record = updated
    return updated


def _complete_successful_run(context: RunContext, payload: dict) -> None:
    """Persist the proven terminal sequence after the workflow returns success."""
    files = payload.get("files") or []
    state = context._lease.record.lifecycle_state
    if state == RunLifecycle.RUNNING:
        transition_mutating_run(context, RunLifecycle.CANDIDATE)
        state = RunLifecycle.CANDIDATE
    if state == RunLifecycle.CANDIDATE:
        transition_mutating_run(context, RunLifecycle.VERIFYING)
        state = RunLifecycle.VERIFYING
    if state == RunLifecycle.VERIFYING:
        transition_mutating_run(context, RunLifecycle.COMMIT_ELIGIBLE)
        state = RunLifecycle.COMMIT_ELIGIBLE
    if state == RunLifecycle.COMMIT_ELIGIBLE:
        transition_mutating_run(
            context,
            RunLifecycle.COMMITTED,
            commit_intent="APPLY_VERIFIED_CANDIDATE",
            commit_result="COMMITTED" if files else "NO_CHANGES",
        )
    transition_mutating_run(context, RunLifecycle.SUCCESS)


def _fail_active_run(context: RunContext, error: Optional[BaseException] = None) -> None:
    record = context._lease.record
    if record is None or record.terminal:
        return
    uncertain = error is not None and any(
        cls.__name__ == "UncertainCommitError" for cls in type(error).__mro__
    )
    transition_mutating_run(
        context,
        RunLifecycle.UNCERTAIN if uncertain else RunLifecycle.FAILURE,
        commit_result="UNCERTAIN" if uncertain else (record.commit_result or "NOT_COMMITTED"),
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
    """
    canonical = _canonical_workspace(workspace_path)
    existing = current_run_context()
    if existing is not None:
        # Nested workflow/controller boundaries are part of the same run.
        yield require_mutating_run(canonical, existing)
        return

    with acquire_run_lock(canonical, run_id=run_id) as acquired_run_id:
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


def coordinated_mutation(function: F) -> F:
    """Ensure an async public mutation API always executes with ownership.

    ``run_context=`` is accepted as an additive capability parameter even
    when the wrapped legacy signature does not expose it.  Omitting it invokes
    the compatibility adapter, which safely acquires a fresh run context.
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
        with _use_or_begin(os.fspath(workspace_path), supplied) as context:
            lease = context._lease
            lease.mutation_depth += 1
            outermost = lease.mutation_depth == 1
            if outermost and lease.record is not None and lease.record.lifecycle_state == RunLifecycle.NEW:
                goal = bound.arguments.get("goal")
                goal_hash = (
                    hashlib.sha256(goal.encode("utf-8")).hexdigest()
                    if isinstance(goal, str) else None
                )
                transition_mutating_run(context, RunLifecycle.RUNNING, goal_hash=goal_hash)
            try:
                result = await function(*args, **kwargs)
                if outermost and lease.record is not None and not lease.record.terminal:
                    payload = result.legacy_result if hasattr(result, "legacy_result") else result
                    if isinstance(payload, dict):
                        status = str(payload.get("status", "")).lower()
                        quality = payload.get("quality_gates_passed")
                        if quality is True or status == "success":
                            _complete_successful_run(context, payload)
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
