"""TEST-DEADLINE-TIMING-001: a deterministic budget clock for the run-deadline tests.

Root cause (TRACED): the tests expressed "N seconds of the generation budget are left" as
``generation_clock = time.monotonic() - (budget - N)`` and the product then read the REAL ``time.monotonic()``
when it dispatched the call. Under load the real time spent between those two reads is unbounded, so a test that
left 0.5 s (later 2.5 s) of budget saw ``INFERENCE_DEADLINE_EXHAUSTED`` (nothing dispatched) where it asserted
``INFERENCE_DEADLINE_EXCEEDED`` (a dispatched call cut at the deadline) - a deterministic transition tested through
wall-clock timing. Earlier fixes widened the margin; widening only moves the load at which the race loses.

This helper freezes the clock the BUDGET is measured on, at the two places that read it for the model deadline:
``kriya.control.run_coordinator._now`` (the run's generation clock; a documented injection seam, already used by
tests/test_w37_human_wait_clock.py) and ``kriya.core.llm.time`` (the dispatch-time ``remaining`` arithmetic).
It deliberately leaves everything else on real time: ``asyncio.wait_for`` still cuts a dispatched call after the
REAL ``remaining`` seconds (that is the behaviour under test), durations and ``time.time`` are real, and nothing in
``kriya.workflow.attempt`` (a pinned kernel module, tests/architecture/test_patch_points.py) is patched.
"""
from __future__ import annotations

import time as _real_time

from kriya.control import run_coordinator
from kriya.core import llm as llm_module


class FrozenClock:
    """A monotonic clock that moves only when a test advances it."""

    def __init__(self) -> None:
        self.now = _real_time.monotonic()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _TimeShim:
    """``time`` for kriya.core.llm: real everything, except ``monotonic`` is the injected clock."""

    def __init__(self, clock: FrozenClock) -> None:
        self.monotonic = clock

    def __getattr__(self, name):
        return getattr(_real_time, name)


def freeze_budget_clock(monkeypatch) -> FrozenClock:
    """Freeze the generation-budget clock for this test (undone by ``monkeypatch``)."""
    clock = FrozenClock()
    monkeypatch.setattr(run_coordinator, "_now", clock)
    monkeypatch.setattr(llm_module, "time", _TimeShim(clock))
    return clock


def set_generation_budget_used(clock: FrozenClock, seconds: float) -> None:
    """The active run's generation clock started ``seconds`` ago, by the injected clock (an earlier unit's model work)."""
    run_coordinator.current_run_context()._lease.generation_clock = clock() - seconds  # pylint: disable=protected-access
