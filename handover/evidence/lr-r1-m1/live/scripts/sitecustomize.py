"""LR-R1-M1 live validation timing shim (measurement harness, NOT Kriya code).

Loaded via PYTHONPATH only for the measured ``kriya generate`` processes. After a
target module is imported it wraps the recorder's entry points and the call-site
recording helpers with a perf_counter accumulator. Only the OUTERMOST recorder
frame on a thread is counted (a helper that calls ``scope.emit`` is one interval).
Writes JSON to $KRIYA_RECORDER_TIMING_OUT at process exit.

Not wrapped (generator context managers whose body is the run itself):
run_scope, unit_scope, attempt_scope, tool_unit, call_scope, wire_scope.
"""
import atexit
import functools
import importlib.abc
import importlib.util
import json
import os
import sys
import threading
import time

_OUT = os.environ.get("KRIYA_RECORDER_TIMING_OUT")
_START = time.perf_counter()
_LOCAL = threading.local()
_LOCK = threading.Lock()
_STATS = {}          # name -> [calls, outermost seconds]
_TOTAL = [0.0]

_SCOPE_FUNCS = (
    "emit", "ensure_store", "close_run", "prune_after_run", "store_pointer", "attempt_opened",
    "record_wire_request", "record_wire_response", "record_wire_error", "record_undispatched_call",
    "record_call_result", "mirror_event", "mirror_decision", "mirror_evidence", "mirror_gate_outcome",
    "mirror_gate_outcomes_restored", "record_developer_parse", "record_authority_transition",
    "note_retry_evidence", "record_diagnosis", "record_fallback_decision", "enter_attempt_iteration",
    "exit_attempt_iteration", "capture_mode", "capture_reasoning", "next_wire_reason",
)
TARGETS = {
    "kriya.core.attempt_evidence.scope": _SCOPE_FUNCS,
    "kriya.workflow.attempt": ("_record_candidate_change", "_record_authority_snapshot"),
    "kriya.tools.validate": ("_record_gate_result",),
    "kriya.workflow.context_budget": ("_recorded_section_fit", "_record_developer_sections"),
    "kriya.workflow.recovery_coordinator": ("_record_recovery_decision",),
    "kriya.workflow.terminal_gate_service": ("_record_terminal_report",),
    "kriya.workflow.run_trace": ("_unit_evidence",),
}


def _wrap(qualified, fn):
    @functools.wraps(fn)
    def timed(*args, **kwargs):
        depth = getattr(_LOCAL, "depth", 0)
        _LOCAL.depth = depth + 1
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            _LOCAL.depth = depth
            if depth == 0:
                elapsed = time.perf_counter() - started
                with _LOCK:
                    entry = _STATS.setdefault(qualified, [0, 0.0])
                    entry[0] += 1
                    entry[1] += elapsed
                    _TOTAL[0] += elapsed
    timed.__lr_timed__ = True
    return timed


def _instrument(module):
    for name in TARGETS.get(module.__name__, ()):
        fn = getattr(module, name, None)
        if fn is None:
            _STATS.setdefault(f"MISSING:{module.__name__}.{name}", [0, 0.0])
            continue
        if not getattr(fn, "__lr_timed__", False):
            setattr(module, name, _wrap(f"{module.__name__}.{name}", fn))


class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self.inner = inner

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        self.inner.exec_module(module)
        _instrument(module)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname not in TARGETS:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            spec = finder.find_spec(fullname, path, target) if hasattr(finder, "find_spec") else None
            if spec is not None and spec.loader is not None:
                spec.loader = _Loader(spec.loader)
                return spec
        return None


def _dump():
    if not _OUT:
        return
    report = {
        "process_wall_seconds": time.perf_counter() - _START,
        "recorder_seconds": _TOTAL[0],
        "functions": {name: {"calls": c, "seconds": s} for name, (c, s) in sorted(_STATS.items())},
        "argv": sys.argv,
    }
    with open(_OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)


if _OUT:
    sys.meta_path.insert(0, _Finder())
    atexit.register(_dump)
