"""Single composition root for mutating Kriya runs.

The coordinator turns the process-level workspace lock into an explicit,
short-lived capability.  Public mutation entry points use
``coordinated_mutation`` so callers cannot accidentally bypass ownership;
older callers need no signature change because the adapter acquires a
context when one is not already active.
"""

from __future__ import annotations

import inspect
import os
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Iterator, Optional, TypeVar, cast

from kriya.control.run_ownership import acquire_run_lock
from kriya.control.workspace_identity import workspace_identity


class InvalidRunContextError(RuntimeError):
    """A missing, expired, or wrong-workspace mutation capability was used."""


@dataclass
class _RunLease:
    active: bool = True


@dataclass(frozen=True)
class RunContext:
    """Evidence that this process currently owns one workspace mutation run."""

    run_id: str
    workspace_path: str
    workspace_id: str
    base_revision: Optional[str]
    _lease: _RunLease = field(repr=False, compare=False)

    @property
    def active(self) -> bool:
        return self._lease.active


_ACTIVE_RUN: ContextVar[Optional[RunContext]] = ContextVar(
    "kriya_active_mutating_run", default=None
)


def _canonical_workspace(workspace_path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(workspace_path)))


def _base_revision(workspace_path: str) -> Optional[str]:
    """Return the git revision owned at run start, or None outside git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


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
    if candidate.workspace_id != expected:
        raise InvalidRunContextError(
            "RunContext belongs to a different workspace "
            f"({candidate.workspace_path!r}, not {_canonical_workspace(workspace_path)!r})"
        )
    return candidate


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
        context = RunContext(
            run_id=acquired_run_id,
            workspace_path=canonical,
            workspace_id=workspace_identity(canonical),
            base_revision=_base_revision(canonical),
            _lease=lease,
        )
        token = _ACTIVE_RUN.set(context)
        try:
            yield context
        finally:
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
        with _use_or_begin(os.fspath(workspace_path), supplied):
            return await function(*args, **kwargs)

    return cast(F, wrapper)
