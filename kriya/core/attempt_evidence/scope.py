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
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Tuple

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
    # (state dir, keep_runs, max_bytes) once the store opened (M1.9 retention).
    retention: Optional[Tuple[str, int, int]] = None
    # retry.delta bookkeeping per unit invocation (M1.8b): never read by a decision.
    retry_inputs: Dict[Tuple[Optional[str], Optional[int]], "_UnitInputs"] = field(default_factory=dict)


@dataclass
class _UnitInputs:
    """The inputs of a unit invocation's attempts, as recorded: the open
    attempt's, the previous closed attempt's, and the signature of the last
    diagnosed failure (what triggers the next attempt)."""
    current: Optional[Dict[str, Any]] = None
    previous: Optional[Dict[str, Any]] = None
    last_failure_signature: Optional[str] = None


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
# The most recent logical call this task (or a parent) made: what a parse of
# its answer, which runs after the call scope closed, refers to.
_LAST_CALL: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("attempt_evidence_last_call", default=None)


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
        _observe(kind, payload)
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
            bounds = cfg.evidence.attempt_recorder.retention
            run.retention = (state_dir, bounds.keep_runs, bounds.max_bytes)
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


def prune_after_run(context: Any, references: Callable[[], Any]) -> None:
    """Retention at run close (design §10), after the seal and under the
    caller's workspace lock: the run's own store and every run the
    workspace still references (``references()``) are protected. Never
    raises; never alters the run."""
    try:
        run = _RUN.get()
        if run is None or run.run_id != getattr(context, "run_id", None) or run.retention is None:
            return
        from kriya.core.attempt_evidence.retention import prune_evidence

        state_dir, keep_runs, max_bytes = run.retention
        report = prune_evidence(state_dir, keep_runs=keep_runs, max_bytes=max_bytes,
                                protect_run_ids={run.run_id, *references()})
        if report.pruned:
            logger.info("Attempt evidence: pruned %d store(s), %d bytes freed",
                        len(report.pruned), report.freed_bytes)
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: retention skipped (%s: %s)", type(error).__name__, error)


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
            _close_attempt_inputs()
            emit("attempt.closed", closing)
            _sync()
    finally:
        _ATTEMPT.reset(token)


def enter_attempt_iteration(attempt_number: Callable[[], Optional[int]]) -> Any:
    """One iteration of the retry loop: the attempt's identity for what the
    attempt does after ``run_attempt`` returns (pre-apply verification,
    requirements, static analysis, approval, the terminal regression) and
    for its failure handling. Emits nothing itself; ``run_attempt``'s own
    scope (nested inside) emits attempt.opened/closed. Returns the token for
    ``exit_attempt_iteration`` (called from the loop's ``finally``)."""
    try:
        return _ATTEMPT.set(_AttemptScope(attempt_number=attempt_number))
    except Exception as error:  # observational
        logger.warning("Attempt evidence: attempt iteration not entered (%s)", error)
        return None


def exit_attempt_iteration(token: Any, *, succeeded: bool) -> None:
    """``attempt.concluded`` (the iteration's own outcome: whether the
    attempt as a whole succeeded) and the end of its identity."""
    try:
        emit("attempt.concluded", {"succeeded": bool(succeeded)})
    finally:
        if token is not None:
            try:
                _ATTEMPT.reset(token)
            except (ValueError, RuntimeError) as error:  # observational: never alters the run
                logger.warning("Attempt evidence: attempt iteration not reset (%s)", error)


def attempt_opened(payload: Mapping[str, Any]) -> None:
    emit("attempt.opened", payload)
    inputs = _unit_inputs()
    if inputs is not None:
        attempt = _ATTEMPT.get()
        inputs.current = {
            "attempt": attempt.attempt_number() if attempt is not None else None,
            "mode": payload.get("mode"), "trigger_failure": inputs.last_failure_signature,
            "retry_evidence": None, "sections": {}, "authority": None, "targets": None,
            "model_profile": None, "first": None, "last": None, "staged": False,
        }


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
    _LAST_CALL.set(seq)  # deliberately not reset: the caller's task reads it after the call returns
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
    try:
        _note_developer_request(request)
    except Exception as error:  # observational
        logger.warning("Attempt evidence: retry.delta not built (%s: %s)", type(error).__name__, error)


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


def record_developer_parse(parsed: Any, filepath: str, *, selected_protocol: Optional[str], source: str) -> None:
    """``developer.parse``: how one Developer answer was read (protocol, kind,
    reason code). The model's own analysis text is content, marked as the
    model's claim. ``call_seq_parsed`` is the logical call whose answer this
    is (the task's most recent call)."""
    if _active_writer() is None:
        return
    try:
        payload = {
            "path": filepath, "source": source, "protocol_selected": selected_protocol,
            "protocol": getattr(parsed, "protocol", None), "kind": getattr(parsed, "kind", None),
            "reason_code": getattr(parsed, "reason_code", None), "detail": (getattr(parsed, "detail", None) or "")[:500],
            "edit_count": len(getattr(parsed, "edits", None) or ()), "final_newline": getattr(parsed, "final_newline", None),
            "call_seq_parsed": _LAST_CALL.get(),
        }
        analysis = getattr(parsed, "analysis", None)
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: developer.parse not built (%s: %s)", type(error).__name__, error)
        return
    emit("developer.parse", payload, content={"analysis_model_claimed": analysis})


# -- gates, candidate, obligations (M1.8a) ----------------------------------------

GATE_OUTPUT_CAP_BYTES = 8 * 1024 * 1024


def bounded_output(text: Any) -> Tuple[Optional[str], Dict[str, Any]]:
    """Gate output for the store: kept whole up to GATE_OUTPUT_CAP_BYTES, else
    its head, with the original size and the full output's digest."""
    if text is None:
        return None, {"output_bytes": 0, "truncated": False}
    data = text if isinstance(text, bytes) else str(text).encode("utf-8", "replace")
    meta: Dict[str, Any] = {"output_bytes": len(data), "truncated": len(data) > GATE_OUTPUT_CAP_BYTES}
    if meta["truncated"]:
        meta["full_digest"] = model.digest(data)
        data = data[:GATE_OUTPUT_CAP_BYTES]
    return data.decode("utf-8", "replace"), meta


# -- diagnosis, recovery, fallback, retry delta (M1.8b) ----------------------------

EVIDENCE_MEASURED = "MEASURED"
EVIDENCE_DERIVED = "DERIVED_DETERMINISTIC"
EVIDENCE_MODEL_CLAIMED = "MODEL_CLAIMED"
EVIDENCE_UNKNOWN = "UNKNOWN"
# Attribution tiers whose localization is the model's own judgment.
_MODEL_JUDGED_TIERS = frozenset({"triage", "self_diagnosis"})
# Failure sources whose text is a tool's or gate's own output.
_MEASURED_SOURCES = frozenset({"quality_gate", "validator", "compile", "test", "run_verification"})


def _unit_inputs() -> Optional[_UnitInputs]:
    run, unit = _RUN.get(), _UNIT.get()
    if run is None or run.status != STATUS_OPEN:
        return None
    key = (unit.unit_id, unit.invocation_seq) if unit is not None else (None, None)
    with run.lock:
        return run.retry_inputs.setdefault(key, _UnitInputs())


def _digest_of(value: Any) -> Optional[str]:
    return None if value is None else model.digest(model.as_bytes(value))


def _section_digests(segments: Any) -> Dict[str, str]:
    digests: Dict[str, str] = {}
    for segment in segments or ():
        name = str(segment.get("name"))
        key, index = name, 1
        while key in digests:
            index += 1
            key = f"{name}#{index}"
        digests[key] = segment.get("digest")
    return digests


def _observe(kind: str, payload: Mapping[str, Any]) -> None:
    """Keep the retry-delta inputs current from records just written."""
    inputs = _unit_inputs()
    if inputs is None:
        return
    current = inputs.current
    if kind == "diagnosis":
        inputs.last_failure_signature = _digest_of(
            [payload.get("type"), payload.get("reason_code"), sorted(payload.get("likely_files") or [])])
    if current is None:
        return
    if kind == "prompt.sections" and payload.get("fitter") == "fit_developer_request":
        current["sections"] = _section_digests(payload.get("segments"))
    elif kind == "authority.snapshot":
        current["authority"] = _digest_of({k: v for k, v in payload.items() if k not in ("t_wall", "t_mono_ms")})
        current["targets"] = sorted(t.get("path") for t in payload.get("targets") or [])
    elif kind == "fallback.decision" and payload.get("phase") == "call":
        current["model_profile"] = payload.get("profile_digest")
    elif kind == "candidate.change" and payload.get("decision") == "STAGED":
        current["staged"] = True


def note_retry_evidence(fingerprint_digest: Optional[str]) -> None:
    """The retry evidence this attempt's Developer request is built from
    (its fingerprint digest): a retry.delta dimension, not a record."""
    try:
        inputs = _unit_inputs()
        if inputs is not None and inputs.current is not None:
            inputs.current["retry_evidence"] = fingerprint_digest
    except Exception as error:  # observational
        logger.warning("Attempt evidence: retry evidence not noted (%s)", error)


def _note_developer_request(request: Any) -> None:
    """At each Developer request inside an attempt: remember its inputs, and
    at the attempt's first one emit retry.delta against the previous attempt."""
    call = _CALL.get()
    inputs = _unit_inputs()
    if call is None or call.role != "developer" or inputs is None or inputs.current is None:
        return
    current = inputs.current
    summary = {"model": request.model, "temperature": request.temperature,
               "request_digest": _digest_of(request.messages), "model_profile": current["model_profile"],
               "sections": dict(current["sections"]), "authority": current["authority"],
               "targets": current["targets"]}
    current["last"] = summary
    if current["first"] is None:
        current["first"] = summary
        if inputs.previous is not None:
            emit("retry.delta", retry_delta(inputs.previous, current))


def _close_attempt_inputs() -> None:
    try:
        inputs = _unit_inputs()
        if inputs is None or inputs.current is None:
            return
        if inputs.previous is not None and inputs.current["first"] is None:
            # No Developer request this attempt: its delta is UNKNOWN.
            emit("retry.delta", retry_delta(inputs.previous, inputs.current))
        inputs.previous, inputs.current = inputs.current, None
    except Exception as error:  # observational
        logger.warning("Attempt evidence: retry inputs not closed (%s)", error)


# Dimensions compared by retry_delta (design §3.4); the attempt counter is
# deliberately not one (non-informative).
RETRY_DELTA_DIMENSIONS = ("model", "model_profile", "temperature", "mode", "targets", "authority",
                          "retry_evidence", "failure_signature")


def retry_delta(previous: Mapping[str, Any], current: Mapping[str, Any]) -> Dict[str, Any]:
    """Pure: the input-side difference between the previous attempt's last
    Developer request and this attempt's first (design §3.4). Descriptive
    only - no decision reads it in M1."""
    before, after = previous.get("last"), current.get("first")
    payload: Dict[str, Any] = {"previous_attempt": previous.get("attempt"), "attempt": current.get("attempt")}
    if before is None or after is None:
        side = "previous" if before is None else "current"
        payload.update(information_gain="UNKNOWN", changed=[], dimensions={},
                       reason=f"no recorded Developer request in the {side} attempt")
        return payload
    pairs = {
        "model": (before["model"], after["model"]),
        "model_profile": (before["model_profile"], after["model_profile"]),
        "temperature": (before["temperature"], after["temperature"]),
        "mode": (previous.get("mode"), current.get("mode")),
        "targets": (before["targets"], after["targets"]),
        "authority": (before["authority"], after["authority"]),
        "retry_evidence": (previous.get("retry_evidence"), current.get("retry_evidence")),
        "failure_signature": (previous.get("trigger_failure"), current.get("trigger_failure")),
    }
    for name in sorted(set(before["sections"]) | set(after["sections"])):
        pairs[f"sections.{name}"] = (before["sections"].get(name), after["sections"].get(name))
    changed = sorted(name for name, (old, new) in pairs.items() if old != new)
    payload.update(
        information_gain="PRESENT" if changed else "NONE", changed=changed,
        dimensions={name: {"previous": old, "current": new} for name, (old, new) in pairs.items()},
        request_digests={"previous": before["request_digest"], "current": after["request_digest"]},
    )
    return payload


def _evidence_class(failure: Any) -> str:
    tier = getattr(failure, "attribution_tier", None)
    if tier in _MODEL_JUDGED_TIERS:
        return EVIDENCE_MODEL_CLAIMED
    if tier:
        return EVIDENCE_DERIVED
    if getattr(failure, "source", None) in _MEASURED_SOURCES:
        return EVIDENCE_MEASURED
    return EVIDENCE_UNKNOWN


def record_diagnosis(failure: Any, operation: Optional[str]) -> None:
    """``diagnosis`` for one recorded failure (GenerationState.record_failure),
    and ``candidate.change`` REFUSED for content the failed attempt proposed
    but never staged for its gates."""
    if _active_writer() is None:
        return
    try:
        diagnostics = failure.diagnostics if isinstance(failure.diagnostics, dict) else {}
        raw_text, raw_meta = bounded_output(failure.raw_output)
        payload = {
            "type": failure.type, "source": failure.source, "authority": failure.authority,
            "attempt": failure.attempt, "mode": failure.mode, "operation": operation,
            "reason_code": diagnostics.get("reason_code"),
            "likely_files": list(failure.likely_files),
            "file_locations": [{"filepath": loc.filepath, "line": loc.line, "col": loc.col}
                               for loc in failure.file_locations],
            "attribution_tier": failure.attribution_tier, "attribution_confidence": failure.attribution_confidence,
            "attribution_kind": failure.attribution_kind, "evidence_class": _evidence_class(failure),
            "subtask_id": failure.subtask_id, "raw_output": raw_meta,
        }
        content = {"message": failure.message, "raw_output": raw_text,
                   "attribution_reasoning": failure.attribution_reasoning}
    except Exception as error:  # observational
        logger.warning("Attempt evidence: diagnosis not built (%s: %s)", type(error).__name__, error)
        return
    emit("diagnosis", payload, content=content)
    _record_refused_candidate(failure, payload["reason_code"] or failure.type)


def _record_refused_candidate(failure: Any, reason_code: str) -> None:
    try:
        inputs = _unit_inputs()
        closed = inputs.previous if inputs is not None else None
        if closed is None or closed.get("attempt") != failure.attempt or closed.get("staged"):
            return
        proposed = dict(failure.failed_content or {})
        edits = list(failure.attempted_edits or [])
    except Exception as error:  # observational
        logger.warning("Attempt evidence: refused candidate not built (%s)", error)
        return
    for relpath in sorted(proposed):
        emit("candidate.change", {"decision": "REFUSED", "path": relpath, "reason_code": reason_code,
                                  "attempt": failure.attempt,
                                  "proposed_digest": _digest_of(proposed[relpath])},
             content={"proposed": proposed[relpath]})
    if edits and not proposed:
        emit("candidate.change", {"decision": "REFUSED", "path": None, "reason_code": reason_code,
                                  "attempt": failure.attempt, "attempted_edits": len(edits)},
             content={"attempted_edits": edits})


def record_fallback_decision(payload: Mapping[str, Any]) -> None:
    emit("fallback.decision", payload)
