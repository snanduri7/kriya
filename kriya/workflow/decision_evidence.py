"""The kernel's own attempt-evidence emission helpers (full modularization W4, E10).

Moved verbatim from ``kriya/workflow/attempt.py``: the attempt's evidence boundary (``_attempt_evidence_boundary``, the
decorator of ``run_attempt``: the attempt-evidence scope and its close, the ``obligations.snapshot`` at the close) and the
two snapshot recorders (``_record_obligations_snapshot``, ``_record_authority_snapshot``). Every record is observational,
emitted at the same point with the same payload as before; a recorder that cannot build its payload logs a warning
through attempt.py's logger and records nothing, as before.

This is the KERNEL-side owner of the kernel's own emission helpers; the EVIDENCE module owns the store, the record kinds
and the typed decision records (``kriya/core/attempt_evidence/decisions.py``, D1: ``emit_decision`` and the five frozen
records - not duplicated here, and not used yet: no decision record is emitted by this slice).

``kriya/workflow/attempt.py`` keeps the three names as forwarders (``run_attempt`` is still decorated by
``attempt._attempt_evidence_boundary``). What arrives in ``EvidenceDeps``, resolved through ``kriya.workflow.attempt`` by
``attempt._evidence_deps()``: attempt.py's ``logger`` and ``_record_obligations_snapshot`` (the decorator receives the
builder itself and resolves it when the wrapped attempt closes, as the global lookup did). This module imports only the
EVIDENCE scope; it never imports ``kriya.workflow.attempt``, ``workflow``, ``workflow_controller`` or ``kriya.cli``, nor
any all-import-cycle module.

``RunState`` is the attempt's ``kriya.workflow.state.GenerationState`` and ``AttemptCtx`` its
``kriya.workflow.attempt.AttemptContext``; both are spelled ``Any`` (``kriya/workflow/stage_context.py`` says why the
state module is not imported; AttemptContext lives in attempt.py, OI-46).
"""
import functools
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from kriya.core.attempt_evidence import scope as attempt_evidence_scope

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext


@dataclass(frozen=True)
class EvidenceDeps:
    """What the moved evidence helpers may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._evidence_deps()``. Every field is required: a builder that omits one fails at construction, never at the
    first use."""

    # attempt.py's logger
    logger: Any
    # the moved siblings (their forwarders on attempt.py)
    _record_obligations_snapshot: Callable[..., Any]


def _record_authority_snapshot(deps: EvidenceDeps, state: RunState, ctx: AttemptCtx, kwargs: Dict[str, Any],
                               capabilities: Dict[str, Any], requested: Dict[str, Any], model: str) -> None:
    """LR-R1-M1 ``authority.snapshot``: what this Developer invocation was
    authorized to change and on what source, recorded before inference
    (observational; every value is already decided above)."""
    if attempt_evidence_scope.capture_mode() is None:
        return
    try:
        allowed = list(getattr(ctx, "allowed_write_relpaths", None) or [])
        mode = getattr(ctx, "write_scope_mode", None)
        targets = []
        for path, capability in capabilities.items():
            item = state.known_target_context_items.get(path)
            targets.append({
                **capability.summary(),
                "requested_operation": getattr(requested.get(path), "value", requested.get(path)),
                "whole_file_authority": capability.full_file,
                "shown_tier": getattr(item, "tier", None), "shown_member_id": getattr(item, "member_id", None),
                "shown_is_exact": getattr(item, "is_exact", None), "shown_revision": getattr(item, "revision", None),
                "member_units_shown": len(state.known_target_member_items.get(path, ())),
                "in_write_scope": (path in allowed) if allowed else None,
            })
        payload = {"model": model, "write_scope_mode": getattr(mode, "value", mode), "authorized_write_scope": allowed,
                   "targets": targets, "known_target_files": list(kwargs.get("known_target_files") or [])}
    except Exception as error:  # observational: never alters the attempt
        deps.logger.warning("Attempt evidence: authority.snapshot not built (%s: %s)", type(error).__name__, error)
        return
    attempt_evidence_scope.emit("authority.snapshot", payload)


def _attempt_evidence_boundary(load_deps: Callable[[], EvidenceDeps], func):
    """LR-R1-M1: run one attempt inside its attempt-evidence scope
    (observational). The attempt's number is read from
    ``state.attempt_number`` at each record - it is incremented first thing
    inside the attempt, never predicted here."""
    @functools.wraps(func)
    async def wrapper(state: RunState, ctx: AttemptCtx) -> None:
        with attempt_evidence_scope.attempt_scope(lambda: state.attempt_number) as closing:
            try:
                await func(state, ctx)
                # run_attempt returned: generation and the candidate gates
                # completed. Whether the attempt as a whole succeeded is the
                # retry loop's attempt.concluded (later checks run there).
                closing.update(outcome="RETURNED", candidate_gates_passed=state.candidate_gates_succeeded)
            finally:
                load_deps()._record_obligations_snapshot(ctx)
    return wrapper


def _record_obligations_snapshot(deps: EvidenceDeps, ctx: AttemptCtx) -> None:
    """LR-R1-M1 ``obligations.snapshot`` at attempt close: the ledger's
    revision and digest and the unresolved terminal obligations by kind."""
    ledger = getattr(ctx, "obligation_ledger", None)
    if ledger is None or attempt_evidence_scope.capture_mode() is None:
        return
    try:
        revision, digest = ledger.fingerprint()
        unresolved: Dict[str, List[str]] = {}
        for record in ledger.unresolved_terminal_obligations():
            unresolved.setdefault(getattr(record.kind, "value", str(record.kind)), []).append(record.id)
        payload = {"ledger_revision": revision, "ledger_digest": digest,
                   "unresolved_terminal": {kind: sorted(ids) for kind, ids in sorted(unresolved.items())}}
    except Exception as error:  # observational: never alters the attempt
        deps.logger.warning("Attempt evidence: obligations.snapshot not built (%s)", error)
        return
    attempt_evidence_scope.emit("obligations.snapshot", payload)
