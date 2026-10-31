"""W3-7 (GENERATION-BUDGET-COUNTS-HUMAN-WAIT-001, owner decision 2026-10-30): the run's generation deadline is PAUSED while Kriya waits on a human approval.

MEASURED (W3-0, coordinator re-run of worker C2): a correct run with a 45 s human approval wait against a 40 s generation budget stopped ``time_budget_exhausted`` (quality gates
false); the 0 s control succeeded. The owner's source-review wait counted against the budget that exists to bound MODEL and TOOL time. Model inference, tool execution, dependency
acquisition and internal computation keep consuming it; only the human wait is excluded. Time is injected (``run_coordinator._now``) for the clock mechanics; the end-to-end test
reproduces the measured mechanism with a REAL sleeping approval callback.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "state_machine"))
import _kernel_golden as kg  # noqa: E402


class _SleepingApproval:
    """What an approval callback returns when a person is still reading: a FRESH awaitable per ask that really sleeps, then approves (the kernel golden's ``approve`` is a
    MagicMock return value, so this object is returned unchanged on every ask)."""

    def __init__(self, seconds):
        self.seconds = seconds

    def __await__(self):
        async def _read_and_approve():
            await asyncio.sleep(self.seconds)
            return True
        return _read_and_approve().__await__()


# Stage 2A: the design names a library the goal did not, so the human is asked BEFORE the Developer; the model work (Developer, gates) FOLLOWS the wait. This is the shape of
# the measured defect (the live owner's source-review wait came before generation).
_DESIGN = "Use mylib==1.2.3 in calc.py.\n```json\n" + json.dumps({"files": ["calc.py"]}) + "\n```"


def _stage_2a_run(tmp_path, *, wait, budget):
    def configure(cfg):
        cfg.autonomy.generation_time_budget_seconds = budget
        cfg.autonomy.generation_seconds_per_file_estimate = 1
        cfg.autonomy.generation_gate_reserve_seconds = 0
    outcome = kg._stage_run(tmp_path, architect=lambda n: _DESIGN, approve=_SleepingApproval(wait), configure=configure)
    result = outcome.result.legacy_result if hasattr(outcome.result, "legacy_result") else outcome.result
    return result, outcome


@pytest.mark.parametrize("wait", [0, 14])
def test_a_human_wait_longer_than_the_budget_does_not_exhaust_it(wait, tmp_path):
    # The wait alone exceeds the budget (pre-fix: generation_budget_exhausted, MEASURED); the 12 s budget leaves the non-wait work (planning, design, the Developer pass)
    # ample margin on a loaded runner (a 4 s budget flaked under -n 8).
    result, outcome = _stage_2a_run(tmp_path, wait=wait, budget=12)
    assert result.get("quality_gates_passed") is True, {k: result.get(k) for k in ("status", "failure_category", "environment_failure", "quality_gates_passed")}
    assert result.get("failure_category") != "generation_budget_exhausted"


# ----------------------------------------------------------------------------------------------------------------------------------- the clock mechanics (injected time)

from kriya.control import run_coordinator as rc  # noqa: E402


class _Time:
    """The injected clock: ``run_coordinator._now``. Tests advance it by hand."""

    def __init__(self, start=1000.0):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def clock(monkeypatch, tmp_path):
    fake = _Time()
    monkeypatch.setattr(rc, "_now", fake, raising=False)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with rc.begin_mutating_run(str(workspace)):
        yield fake


def _remaining(fake, budget):
    started = rc.claim_run_generation_clock()
    return budget - (fake.value - started)


def test_a_pause_credits_exactly_the_wait_so_the_remaining_budget_is_unchanged(clock):
    rc.claim_run_generation_clock()                                # the first model call starts the clock
    clock.advance(10)                                              # model/tool time: counts
    assert _remaining(clock, 100) == 90
    with rc.generation_clock_paused():
        clock.advance(500)                                         # a person reading the diff: does not count
        assert _remaining(clock, 100) == 90                        # frozen while paused
    assert _remaining(clock, 100) == 90                            # and credited exactly on resume
    clock.advance(5)
    assert _remaining(clock, 100) == 85                            # work after the wait counts again


def test_repeated_pause_and_resume_cannot_create_budget(clock):
    rc.claim_run_generation_clock()
    for _ in range(50):
        with rc.generation_clock_paused():
            clock.advance(2)
        clock.advance(1)
    assert _remaining(clock, 1000) == 1000 - 50                    # only the 50 s of real work counted; the pauses added nothing beyond their own length


def test_nested_waits_are_one_wait(clock):
    rc.claim_run_generation_clock()
    with rc.generation_clock_paused():
        clock.advance(3)
        with rc.generation_clock_paused():
            clock.advance(4)
        clock.advance(5)                                           # still inside the outer wait
        assert _remaining(clock, 100) == 100
    assert _remaining(clock, 100) == 100                           # 12 s credited once, not 3 + 4 + 12


def test_a_failure_during_the_wait_never_leaves_the_clock_paused(clock):
    rc.claim_run_generation_clock()
    with pytest.raises(RuntimeError):
        with rc.generation_clock_paused():
            clock.advance(7)
            raise RuntimeError("the approval callback failed")
    clock.advance(10)
    assert _remaining(clock, 100) == 90                            # 7 s credited, then the clock runs again


def test_a_cancelled_wait_resumes_the_clock_too(clock):
    rc.claim_run_generation_clock()

    async def waiting():
        with rc.generation_clock_paused():
            clock.advance(9)
            await asyncio.sleep(30)

    async def run():
        task = asyncio.ensure_future(waiting())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    clock.advance(1)
    assert _remaining(clock, 100) == 99


def test_a_wait_before_the_first_claim_does_not_start_or_credit_the_clock(clock):
    with rc.generation_clock_paused():
        clock.advance(50)
    started = rc.claim_run_generation_clock()
    assert started == clock.value                                  # the budget starts at the first model call, as before


def test_the_pause_is_a_no_op_outside_a_run(monkeypatch):
    fake = _Time()
    monkeypatch.setattr(rc, "_now", fake, raising=False)
    assert rc.claim_run_generation_clock() is None
    with rc.generation_clock_paused():
        fake.advance(10)
    assert rc.claim_run_generation_clock() is None


def test_a_claim_made_inside_a_wait_starts_a_frozen_clock(clock):
    with rc.generation_clock_paused():
        clock.advance(20)                                          # the wait is already 20 s old when the first model call is claimed
        first = rc.claim_run_generation_clock()
        assert first == clock.value                                # the budget starts now ...
        clock.advance(40)
        assert rc.claim_run_generation_clock() == first + 40       # ... but is frozen: its effective start moves with the wait
        assert _remaining(clock, 100) == 100
    assert _remaining(clock, 100) == 100
    clock.advance(10)
    assert _remaining(clock, 100) == 90


# ------------------------------------------------------------------------------------------------------------- the human-callback sites (injected time)

from kriya.policy import approval as ap  # noqa: E402


def test_a_synchronous_blocking_callback_is_inside_the_pause(clock):
    """The terminal's own callback is synchronous (click.prompt blocks inside the CALL): the pause must start before the call, not at the await."""
    rc.claim_run_generation_clock()

    def blocking_prompt(files, reason):
        clock.advance(300)                                          # the person thinks
        return True

    outcome = asyncio.run(ap.resolve_approval(blocking_prompt, [], "change approval"))
    assert outcome.approved and _remaining(clock, 100) == 100


def test_an_asynchronous_callback_is_inside_the_pause(clock):
    rc.claim_run_generation_clock()

    async def desktop_review(files, reason):
        clock.advance(300)
        return True

    assert asyncio.run(ap.resolve_approval(desktop_review, [], "change approval")).approved
    assert _remaining(clock, 100) == 100


def test_a_callback_that_raises_does_not_leave_the_run_paused(clock):
    rc.claim_run_generation_clock()

    def broken(files, reason):
        clock.advance(30)
        raise OSError("the review channel closed")

    with pytest.raises(OSError):
        asyncio.run(ap.resolve_approval(broken, [], "change approval"))
    clock.advance(10)
    assert _remaining(clock, 100) == 90


def test_the_typed_runtime_command_review_is_inside_the_pause(clock):
    rc.claim_run_generation_clock()

    def review(subject):
        clock.advance(120)
        return ap.RuntimeCommandDecision(subject.fingerprint)

    subject = ap.RuntimeCommandSubject(unit_id=None, criterion="run", working_directory="/w", commands=(("true",),), input_channel="none", stdin_sha256=None, reason="run it")
    callback = ap.TypedApprovalCallback(lambda files, reason: True, review)
    decision = asyncio.run(ap.ask(callback, subject))
    assert decision.approved_fingerprint == subject.fingerprint and _remaining(clock, 100) == 100


def test_model_and_tool_time_still_counts_and_only_the_wait_is_excluded(clock):
    """Negative control: the same elapsed seconds OUTSIDE a human wait consume the budget exactly as before (no timeout was raised, nothing else pauses)."""
    rc.claim_run_generation_clock()
    clock.advance(30)                                               # a model call and a tool run
    with rc.generation_clock_paused():
        clock.advance(1000)                                         # a human
    clock.advance(30)                                               # more model work
    assert _remaining(clock, 100) == 40


def test_a_new_run_after_a_wait_starts_a_fresh_clock_and_nothing_carries_over(monkeypatch, tmp_path):
    """Resume/checkpoint (TRACED: the run clock lives in the in-memory run lease and is not part of any checkpoint): a resumed run is a new run that claims its own clock at
    its first model call, so a wait in the earlier run can neither consume nor create budget in it."""
    fake = _Time()
    monkeypatch.setattr(rc, "_now", fake, raising=False)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with rc.begin_mutating_run(str(workspace)):
        rc.claim_run_generation_clock()
        with rc.generation_clock_paused():
            fake.advance(500)
    fake.advance(77)
    with rc.begin_mutating_run(str(workspace)):
        assert rc.claim_run_generation_clock() == fake.value


# --------------------------------------------------------------------------------------------------- the structural tripwire for every human-callback call site

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

_HUMAN_CALLBACK_NAMES = {"skill_gap_callback", "skill_conflict_callback", "web_lookup_callback", "web_lookup_query_callback"}
_POLICY_AWAITERS = {"await_if_awaitable", "resolve_approval_result"}     # the two ways a caller turns a callback's answer into a decision outside the policy module


def _calls_outside_a_human_wait(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []

    def visit(node, protected):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            protected = protected or any(isinstance(item.context_expr, ast.Call) and getattr(item.context_expr.func, "id", None) == "human_wait" for item in node.items)
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            if (name in _HUMAN_CALLBACK_NAMES or name in _POLICY_AWAITERS) and not protected:
                offenders.append((path.name, node.lineno, name))
        for child in ast.iter_child_nodes(node):
            visit(child, protected)

    visit(tree, False)
    return offenders


def test_every_human_callback_site_outside_the_policy_module_sits_inside_a_human_wait():
    """A new call of a human callback (or a new use of the two awaiters) that is not inside ``with human_wait():`` would silently charge the owner's wait to the budget again."""
    root = Path(__file__).resolve().parent.parent / "kriya"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.parts[-2:] == ("policy", "approval.py"):
            continue          # the policy module wraps its own call-and-await sites (resolve_approval, ask) and is pinned by the behavioural tests above
        offenders += _calls_outside_a_human_wait(path)
    assert offenders == [], offenders


def test_the_tripwire_sees_a_site_without_the_wrap(tmp_path):
    """The tripwire is not vacuous: an unwrapped site is reported, a wrapped one is not."""
    bad = tmp_path / "bad.py"
    bad.write_text("async def f(skill_gap_callback):\n    return await await_if_awaitable(skill_gap_callback('x', []))\n")
    good = tmp_path / "good.py"
    good.write_text("async def f(skill_gap_callback):\n    with human_wait():\n        return await await_if_awaitable(skill_gap_callback('x', []))\n")
    assert [name for _file, _line, name in _calls_outside_a_human_wait(bad)] == ["await_if_awaitable", "skill_gap_callback"]
    assert _calls_outside_a_human_wait(good) == []


# ------------------------------------------------------------------------------------------------------ the foundation hook and the state copies of the run clock

from kriya.foundation import human_wait as hw  # noqa: E402


class _Copy:
    """What run_lifecycle.open_run_state gives each invocation: an object with a ``budget_started_monotonic`` copy of the run clock."""

    def __init__(self, started):
        self.budget_started_monotonic = started


def test_the_state_copy_of_the_run_clock_follows_the_credit(clock):
    rc.claim_run_generation_clock()
    state = _Copy(rc.claim_run_generation_clock())
    rc.follow_run_generation_clock(state)
    clock.advance(10)
    with rc.generation_clock_paused():
        clock.advance(500)
    assert state.budget_started_monotonic == rc.claim_run_generation_clock()
    assert 100 - (clock.value - state.budget_started_monotonic) == 90      # the attempt budget check (it reads only this copy) sees the wait excluded


def test_a_state_created_inside_a_wait_is_credited_once_not_twice(clock):
    rc.claim_run_generation_clock()
    with rc.generation_clock_paused():
        clock.advance(30)
        state = _Copy(rc.claim_run_generation_clock())                     # the frozen effective start at creation
        rc.follow_run_generation_clock(state)
        clock.advance(70)
    assert state.budget_started_monotonic == rc.claim_run_generation_clock() == clock.value
    clock.advance(5)
    assert 100 - (clock.value - state.budget_started_monotonic) == 95


def test_a_state_that_is_gone_does_not_break_the_resume(clock):
    rc.claim_run_generation_clock()
    rc.follow_run_generation_clock(_Copy(0.0))                              # dropped immediately: only a dead weak reference remains
    import gc
    gc.collect()
    with rc.generation_clock_paused():
        clock.advance(3)
    clock.advance(1)
    assert _remaining(clock, 100) == 99


def test_the_hook_is_a_no_op_without_a_provider_and_enters_the_registered_scope(monkeypatch):
    entered = []

    class _Scope:
        def __enter__(self):
            entered.append("in")

        def __exit__(self, *exc):
            entered.append("out")

    monkeypatch.setattr(hw, "_provider", None)
    with hw.human_wait():
        pass
    assert entered == []
    monkeypatch.setattr(hw, "_provider", _Scope)
    with hw.human_wait():
        entered.append("inside")
    assert entered == ["in", "inside", "out"]


def test_the_run_coordinator_registers_its_pause_as_the_scope():
    assert hw._provider is rc.generation_clock_paused
