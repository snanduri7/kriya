"""Evidence scopes and the production emit facade (LR-R1-M1 design §5).

Production code records evidence only through this module. Identity (run,
unit, invocation, phase, attempt, call, wire) comes from ContextVars set by
the scope managers below - never from the caller - so a caller cannot
misattribute a record (design §6.5).

Every function here is observational (invariant I-2):
- nothing returns a value a decision could read except ``store_pointer`` (a
  telemetry event payload) and ``capture_mode``;
- no ``Exception`` escapes (a failure degrades the store and is logged);
- ``BaseException`` (cancellation, KeyboardInterrupt) always propagates;
- with no run scope, or with capture ``off``, every emit is a no-op.

Scopes nest like the code: run (``begin_mutating_run``) > unit/invocation
(``run_generation_workflow``) > phase or attempt > call > wire.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"          # run scope open, no configuration seen yet
STATUS_OPEN = "OPEN"
STATUS_DISABLED = "DISABLED"        # capture: off (operator) or no evidence configuration
STATUS_UNAVAILABLE = "RECORDER_UNAVAILABLE"
STATUS_CLOSED = "CLOSED"


@dataclass
class _RunScope:
    run_id: str
    context: Any
    status: str = STATUS_PENDING
    reason: str = ""
    capture: Optional[str] = None
    writer: Optional[AttemptEvidenceWriter] = None
    directory: Optional[str] = None
    calls: int = 0
    invocations: Counter = field(default_factory=Counter)
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class _UnitScope:
    unit_id: str
    unit_kind: str
    invocation_seq: int


@dataclass(frozen=True)
class _AttemptScope:
    attempt_number: Callable[[], Optional[int]]


@dataclass
class _CallScope:
    call_seq: int
    role: Optional[str]
    wires: int = 0


_ROLE_PHASES = {
    "planner": "planning", "planner_shadow": "planning", "architect": "architect", "reviewer": "review",
    "spec_compliance": "requirement_verification", "run_verifier": "requirement_verification",
    "localization": "localization", "milestone_planner": "milestone_planning",
}

_RUN: contextvars.ContextVar[Optional[_RunScope]] = contextvars.ContextVar("attempt_evidence_run", default=None)
_UNIT: contextvars.ContextVar[Optional[_UnitScope]] = contextvars.ContextVar("attempt_evidence_unit", default=None)
# Explicit phases are reserved (no producer sets one in M1; design deviation
# recorded in handover/LR_R1_M1_0_INVESTIGATION_NOTES.md §7).
_PHASE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("attempt_evidence_phase", default=None)
_ATTEMPT: contextvars.ContextVar[Optional[_AttemptScope]] = contextvars.ContextVar(
    "attempt_evidence_attempt", default=None)
_CALL: contextvars.ContextVar[Optional[_CallScope]] = contextvars.ContextVar("attempt_evidence_call", default=None)
_WIRE: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("attempt_evidence_wire", default=None)


def _configured_capture(cfg: Any) -> Optional[str]:
    """The operator's capture mode, or None when the object carries no real
    evidence configuration (a test double): no store is opened then."""
    evidence = getattr(cfg, "evidence", None)
    recorder = getattr(evidence, "attempt_recorder", None)
    capture = getattr(recorder, "capture", None)
    return capture if isinstance(capture, str) and capture in model.CAPTURE_MODES else None


def _identity() -> Dict[str, Any]:
    run, unit, attempt, call = _RUN.get(), _UNIT.get(), _ATTEMPT.get(), _CALL.get()
    phase = _PHASE.get()
    attempt_number = None
    if attempt is not None:
        attempt_number = attempt.attempt_number()
        phase = phase or "attempt"
    elif phase is None and call is not None and call.role:
        # A non-attempt call's phase is the role the code itself set for it
        # (role_metrics.model_role), never a guess from its text.
        phase = _ROLE_PHASES.get(call.role, call.role)
    return {
        "run_id": run.run_id if run else None,
        "unit_id": unit.unit_id if unit else None,
        "unit_kind": unit.unit_kind if unit else None,
        "invocation_seq": unit.invocation_seq if unit else None,
        "phase": phase,
        "attempt_number": attempt_number,
        "call_seq": call.call_seq if call else None,
        "wire_seq": _WIRE.get(),
        "role": call.role if call else None,
    }


def _active_writer() -> Optional[AttemptEvidenceWriter]:
    run = _RUN.get()
    if run is None or run.writer is None or run.status != STATUS_OPEN:
        return None
    return run.writer


def emit(kind: str, payload: Mapping[str, Any], *, content: Optional[Mapping[str, Any]] = None,
         provenance: str = model.OBSERVED) -> None:
    """Record one evidence record under the current scope. Never raises an
    Exception; a no-op without an open store."""
    try:
        writer = _active_writer()
        if writer is None:
            return
        identity = _identity()
        if identity["run_id"] != writer.run_id:
            writer.gap(kind, f"scope run {identity['run_id']!r} differs from the store's run")
            return
        writer.record(kind, payload, identity=identity, content=content, provenance=provenance)
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: %s not recorded (%s: %s)", kind, type(error).__name__, error)


def capture_mode() -> Optional[str]:
    """The active store's capture mode (None when no store is open)."""
    writer = _active_writer()
    return writer.capture if writer is not None else None


def capture_reasoning() -> bool:
    writer = _active_writer()
    return bool(writer is not None and writer.capture_reasoning)


def _sync() -> None:
    writer = _active_writer()
    if writer is not None:
        writer.sync()


# -- run --------------------------------------------------------------------


@contextlib.contextmanager
def run_scope(context: Any) -> Iterator[None]:
    """Opened by ``begin_mutating_run`` (top-level run only). The store itself
    opens lazily, at the first scope that knows the configuration
    (``ensure_store``)."""
    scope = _RunScope(run_id=str(getattr(context, "run_id", "")), context=context)
    token = _RUN.set(scope)
    try:
        yield
    finally:
        _RUN.reset(token)


def ensure_store(cfg: Any) -> None:
    """Open the active run's store on first use. Observational (D5): a store
    that cannot be opened is RECORDER_UNAVAILABLE and the run continues."""
    run = _RUN.get()
    if run is None or run.status != STATUS_PENDING:
        return
    with run.lock:
        if run.status != STATUS_PENDING:
            return
        try:
            capture = _configured_capture(cfg)
            if capture is None:
                run.status, run.reason = STATUS_DISABLED, "no evidence configuration"
                return
            run.capture = capture
            if capture == model.CAPTURE_OFF:
                run.status, run.reason = STATUS_DISABLED, "capture: off"
                return
            from kriya.core.state_paths import resolve_state_directory

            state_dir = resolve_state_directory(cfg)[0]
            writer = AttemptEvidenceWriter(state_dir, run.run_id, capture=capture,
                                           manifest=_run_manifest(run.context))
            run.writer, run.directory, run.status = writer, writer.directory, STATUS_OPEN
        except Exception as error:  # observational (D5): RecorderUnavailable, a bad state dir, anything
            run.status, run.reason = STATUS_UNAVAILABLE, f"{type(error).__name__}: {error}"
            logger.warning("RECORDER_UNAVAILABLE: attempt evidence for run %s is not recorded (%s); "
                           "the run continues unchanged", run.run_id, run.reason)
            return
    emit("run.opened", _run_manifest(run.context))


def _run_manifest(context: Any) -> Dict[str, Any]:
    from kriya.build_info import version_report

    try:
        build = version_report()
    except Exception as error:  # build provenance is optional evidence
        build = {"unavailable": f"{type(error).__name__}: {error}"}
    return {
        "kriya_build": build,
        "workspace_identity": getattr(context, "workspace_id", None),
        "workspace_path": getattr(context, "workspace_path", None),
        "base_revision": getattr(context, "base_revision", None),
        "base_tree_hash": getattr(context, "base_tree_hash", None),
    }


def close_run(context: Any, load_record: Callable[[], Any]) -> None:
    """``run.closed`` (after the RunRecord's terminal transition) and the
    seal. Called by ``begin_mutating_run``'s exit; never raises. The record
    is read (read-only) only when a store is open."""
    try:
        run = _RUN.get()
        if run is None or run.run_id != getattr(context, "run_id", None):
            return
        if run.status == STATUS_OPEN and run.writer is not None:
            run_record = load_record()
            emit("run.closed", {
                "terminal_status": getattr(run_record, "terminal_status", None),
                "lifecycle_state": getattr(run_record, "lifecycle_state", None),
                "record_revision": getattr(run_record, "revision", None),
                "commit_result": getattr(run_record, "commit_result", None),
                "model_calls": run.calls,
                "unit_invocations": dict(run.invocations),
            })
            run.writer.seal("run.closed")
            run.status = STATUS_CLOSED
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: run close not recorded (%s: %s)", type(error).__name__, error)


def store_pointer() -> Dict[str, Any]:
    """Details of the ``evidence.attempt_store`` run event (the only change a
    trace row sees, design §4)."""
    run = _RUN.get()
    if run is None:
        return {"status": "NO_RUN_SCOPE"}
    pointer: Dict[str, Any] = {"run_id": run.run_id, "status": run.status, "capture": run.capture}
    if run.reason:
        pointer["reason"] = run.reason
    if run.writer is not None:
        pointer["seq_at_row"] = run.writer.seq
        pointer["path"] = run.directory
    return pointer


# -- unit / phase / attempt ---------------------------------------------------


@contextlib.contextmanager
def unit_scope(cfg: Any, unit_id: str, unit_kind: str, payload: Optional[Mapping[str, Any]] = None,
               content: Optional[Mapping[str, Any]] = None) -> Iterator[Dict[str, Any]]:
    """One ``run_generation_workflow`` invocation. The yielded dict collects
    the ``unit.closed`` payload."""
    ensure_store(cfg)
    run = _RUN.get()
    seq = 0
    if run is not None:
        run.invocations[unit_id] += 1
        seq = run.invocations[unit_id]
    token = _UNIT.set(_UnitScope(unit_id=unit_id, unit_kind=unit_kind, invocation_seq=seq))
    closing: Dict[str, Any] = {}
    try:
        emit("unit.opened", {"unit_id": unit_id, "unit_kind": unit_kind, "invocation_seq": seq,
                             **dict(payload or {})}, content=content)
        try:
            yield closing
        except BaseException as error:
            closing.setdefault("outcome", "EXCEPTION")
            closing.setdefault("error_type", type(error).__name__)
            raise
        finally:
            closing.setdefault("outcome", "RETURNED")
            emit("unit.closed", closing)
            _sync()
    finally:
        _UNIT.reset(token)


@contextlib.contextmanager
def attempt_scope(attempt_number: Callable[[], Optional[int]]) -> Iterator[Dict[str, Any]]:
    """One Developer + gates attempt. ``attempt_number`` is read at each emit
    (the attempt's own counter, incremented first thing in the attempt)."""
    token = _ATTEMPT.set(_AttemptScope(attempt_number=attempt_number))
    closing: Dict[str, Any] = {}
    try:
        try:
            yield closing
        except BaseException as error:
            closing.setdefault("outcome", "FAILED" if type(error).__name__ in (
                "QualityGateFailure", "IncompleteGenerationError") else "STOPPED")
            closing.setdefault("error_type", type(error).__name__)
            raise
        finally:
            closing.setdefault("outcome", "RETURNED")
            emit("attempt.closed", closing)
            _sync()
    finally:
        _ATTEMPT.reset(token)


def attempt_opened(payload: Mapping[str, Any]) -> None:
    emit("attempt.opened", payload)


# -- calls and wires (M1.5) -----------------------------------------------------


@contextlib.contextmanager
def call_scope(role: Optional[str]) -> Iterator[Optional[int]]:
    """One logical model call; yields its run-global call_seq (None without a
    store)."""
    run = _RUN.get()
    if run is None or run.status != STATUS_OPEN:
        yield None
        return
    with run.lock:
        run.calls += 1
        seq = run.calls
    token = _CALL.set(_CallScope(call_seq=seq, role=role))
    try:
        yield seq
    finally:
        _CALL.reset(token)


@contextlib.contextmanager
def wire_scope() -> Iterator[Optional[int]]:
    """One physical provider request inside the current call."""
    call = _CALL.get()
    if call is None:
        yield None
        return
    call.wires += 1
    token = _WIRE.set(call.wires)
    dispatched = _WIRE_DISPATCHED.set(False)
    try:
        yield call.wires
    finally:
        _WIRE_DISPATCHED.reset(dispatched)
        _WIRE.reset(token)


_WIRE_DISPATCHED: contextvars.ContextVar[bool] = contextvars.ContextVar("attempt_evidence_wire_sent", default=False)
_NEXT_WIRE_REASON: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "attempt_evidence_wire_reason", default=None)


def next_wire_reason(reason: str) -> None:
    """Why the next wire request of this call is sent (a resend inside one
    logical call: ``response_format_dropped``, ``empty_content_floor``)."""
    _NEXT_WIRE_REASON.set(reason)


def _reasoning_summary(text: Optional[str]) -> Dict[str, Any]:
    if not text:
        return {"present": False, "chars": 0, "digest": None}
    return {"present": True, "chars": len(text), "digest": model.digest(text.encode("utf-8"))}


def record_wire_request(runtime: Any, request: Any) -> None:
    """``model.request`` for one wire request: the exact messages, tools and
    adapter body (``wire_payload``), recorded immediately before dispatch."""
    if _active_writer() is None:
        return
    try:
        reason = _NEXT_WIRE_REASON.get() or "initial"
        _NEXT_WIRE_REASON.set(None)
        stream = getattr(request, "stream_callback", None) is not None
        body = runtime.wire_payload(request, stream=stream)
        response_format = getattr(request, "response_format", None) or {}
        timeout = getattr(request, "timeout", None)
        payload = {
            "dispatched": True, "wire_reason": reason, "adapter": getattr(runtime, "name", None),
            "model": request.model, "stream": stream, "response_format": response_format.get("type"),
            "temperature": request.temperature, "max_tokens": request.max_tokens,
            "tools": len(request.tools) if request.tools else 0,
            "timeout_seconds": timeout if isinstance(timeout, (int, float)) else None,
            "wire_body": "RECORDED" if body is not None else "NOT_RECORDED (the adapter declares no wire body)",
        }
        _WIRE_DISPATCHED.set(True)
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: model.request not built (%s: %s)", type(error).__name__, error)
        return
    emit("model.request", payload,
         content={"messages": request.messages, "tools": request.tools, "wire_body": body})


def record_wire_response(response: Any) -> None:
    """``model.response``: the transport content verbatim (D3) - as the
    adapter received it when it captures that, else the content it
    returned, flagged - and the separately returned reasoning as a digest
    (its text only under ``full_with_reasoning``)."""
    if _active_writer() is None:
        return
    try:
        raw = getattr(response, "raw_content", None)
        reasoning = getattr(response, "reasoning_text", None)
        tool_calls = [{"id": c.id, "name": c.name, "arguments": c.arguments}
                      for c in getattr(response, "tool_calls", None) or []]
        payload = {
            "finish_reason": response.finish_reason, "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens, "reasoning_chars": response.reasoning_chars,
            "reasoning_field": _reasoning_summary(reasoning), "provider_metadata": dict(response.provider_metadata),
            "tool_calls": len(tool_calls), "raw_content_recorded": raw is not None,
        }
        content = {"content": raw if raw is not None else response.content, "tool_calls": tool_calls or None,
                   "reasoning": reasoning if capture_reasoning() else None}
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: model.response not built (%s: %s)", type(error).__name__, error)
        return
    emit("model.response", payload, content=content)


def record_wire_error(error: BaseException) -> None:
    """``model.response`` for a wire request that raised (backend error,
    timeout, deadline, cancellation); the caller re-raises."""
    try:
        payload = {"dispatched": _WIRE_DISPATCHED.get(), "error_type": type(error).__name__,
                   "error": str(error)[:500], "cancelled": type(error).__name__ == "CancelledError"}
    except Exception:  # an exception whose str() fails must never replace the run's own
        payload = {"dispatched": _WIRE_DISPATCHED.get(), "error_type": type(error).__name__, "error": None,
                   "cancelled": False}
    emit("model.response", payload)


def record_undispatched_call(error: BaseException, messages: Any, tools: Any = None) -> None:
    """A logical call that ended before any wire request (an egress, budget,
    schema or deadline refusal): the refused prompt is still recorded."""
    call = _CALL.get()
    if call is None or call.wires:
        return
    try:
        payload = {"dispatched": False, "refusal_type": type(error).__name__, "refusal": str(error)[:500],
                   "reason_code": getattr(error, "reason_code", None)}
    except Exception:  # never replaces the run's own exception
        payload = {"dispatched": False, "refusal_type": type(error).__name__, "refusal": None, "reason_code": None}
    emit("model.request", payload, content={"messages": messages, "tools": tools})


def record_call_result(result: Any) -> None:
    """``model.result``: the logical call's normalized outcome (every
    terminal path of LLMClient goes through ``_finish``)."""
    if _active_writer() is None:
        return
    try:
        payload = {key: getattr(result, key, None) for key in (
            "model", "runtime_fingerprint", "runtime_fingerprint_exact", "inference_settings_digest",
            "finish_reason", "max_tokens", "prompt_tokens", "prompt_tokens_reported", "completion_tokens",
            "tokens_estimated", "reasoning_present", "reasoning_chars", "reasoning_source", "elapsed_seconds",
            "backend_status", "backend_error", "protocol", "budget", "prefix_reuse")}
        for key in ("status", "parser_status"):
            value = getattr(result, key, None)
            payload[key] = getattr(value, "value", value)
        payload["tool_calls"] = len(getattr(result, "tool_calls", None) or [])
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: model.result not built (%s: %s)", type(error).__name__, error)
        return
    emit("model.result", payload)


# -- mirrors of the existing evidence streams (design §5.2) --------------------
# The mirrored object travels as content (a blob in ``full``, a digest in
# ``digest_only``): event messages and evidence payloads can quote source and
# gate output. Only its content-free identifying fields are in the payload.


def _mirror(kind: str, to_dict: Callable[[], Mapping[str, Any]], fields: tuple, name: str) -> None:
    if _active_writer() is None:
        return
    try:
        value = dict(to_dict())
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: %s not mirrored (%s: %s)", kind, type(error).__name__, error)
        return
    emit(kind, {key: value.get(key) for key in fields}, content={name: value}, provenance=model.MIRRORED)


def mirror_event(event: Any) -> None:
    """A RunEvent recorded by GenerationState.record_event (or written into an
    outcome trace row)."""
    to_dict = event.to_dict if hasattr(event, "to_dict") else (lambda: event)
    _mirror("mirror.event", to_dict, ("kind", "attempt", "source", "authority", "failure_type", "operation"),
            "event")


def mirror_decision(decision: Any) -> None:
    _mirror("mirror.decision", decision.to_dict, ("type",), "decision")


def mirror_evidence(record: Any) -> None:
    _mirror("mirror.evidence", record.to_dict, ("kind", "source", "attempt", "sensitivity"), "evidence")


def mirror_gate_outcome(index: int, outcome: Any) -> None:
    _mirror("mirror.gate_outcome", lambda: {"index": index, **dict(outcome)}, ("index", "type", "success", "attempt"),
            "outcome")


def mirror_gate_outcomes_restored(outcomes: Any, source: str) -> None:
    _mirror("mirror.gate_outcomes_restored", lambda: {"count": len(outcomes), "source": source,
                                                       "outcomes": list(outcomes)}, ("count", "source"), "restored")
