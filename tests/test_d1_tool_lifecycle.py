"""D1 slice 3c: the durable lifecycle of an externally side-effecting TOOL subtask, through the REAL enforce loop.

    durable STARTED -> side effect -> durable COMPLETED

Faults are injected at the real OS calls (open / fsync / replace) of the strict durable writer, so the production code path is what fails; crash windows use a genuine child
process that dies with os._exit (no finalizer runs) and a parent that reloads the persisted state (tests/test_d1_tool_crash.py)."""
import errno
import os
import stat
from unittest.mock import AsyncMock, patch

import pytest
from test_e12_enforce_characterization import C, _enforce, _enforce_engine, _plan_of, _unit

from kriya.control.persistence import load_control_state
from kriya.control.state import SUBTASK_STATE_TOOL_STARTED
from kriya.repository import atomic_write
from kriya.workflow.plan_schema import ExecutionMethod, Subtask
from kriya.workflow.resume_support import (
    TOOL_COMPLETED_NOT_DURABLE,
    TOOL_EXECUTION_UNRESOLVED,
    TOOL_STARTED_NOT_DURABLE,
)
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

DONE = SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool", tool_output="effect-output")


def _plan():
    tool = Subtask(id="t1", description="run a command", execution_method=ExecutionMethod.TOOL, tool_name="shell", tool_arguments={"command": "true"})
    return _plan_of(tool, _unit("s2", "b.py", depends_on=["t1"]))


class Faults:
    """Fails exactly one step of exactly one strict durable write (the n-th, counting every call of the durable writer in the run): 1 = the TOOL STARTED transition, 2 = its
    COMPLETED. The failure is injected at the real OS call (open / fsync / replace) and only while THAT write is executing, so no unrelated fsync in the process is hit."""

    def __init__(self, monkeypatch, *, write, step):
        self.write, self.step, self.index, self.active = write, step, 0, False
        real_write, real_fsync, real_replace, real_open = atomic_write.durable_atomic_write_bytes, os.fsync, os.replace, open

        def wrapped(path, data, *, mode=None):
            self.index += 1
            self.active = self.index == self.write
            try:
                return real_write(path, data, mode=mode)
            finally:
                self.active = False

        def fsync(fd):
            if self.active:
                is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
                if (self.step == "file_fsync" and not is_dir) or (self.step == "directory_fsync" and is_dir):
                    raise OSError(errno.EIO, f"injected {self.step} failure")
            return real_fsync(fd)

        def replace(src, dst):
            if self.active and self.step == "replace":
                raise OSError(errno.EXDEV, "injected replace failure")
            return real_replace(src, dst)

        def failing_open(path, mode="r", *args, **kwargs):
            if self.active and self.step == "temp_write" and "kriya-durable" in str(path) and "w" in mode:
                raise OSError(errno.ENOSPC, "injected temp write failure")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(atomic_write, "durable_atomic_write_bytes", wrapped)
        monkeypatch.setattr(os, "fsync", fsync)
        monkeypatch.setattr(os, "replace", replace)
        monkeypatch.setattr(atomic_write, "open", failing_open, raising=False)


def _state(tmp_path):
    return load_control_state(str(tmp_path))


def _tool_results(result):
    return {r["subtask_id"]: r for r in result.legacy_result["subtask_results"]}


@pytest.mark.asyncio
async def test_a_tool_subtask_is_durably_started_before_the_effect_and_durably_completed_after_it(tmp_path):
    seen_at_effect = {}

    async def effect(**kwargs):
        seen_at_effect["state"] = _state(tmp_path).subtask_states.get("t1")   # read from DISK at the moment the effect runs
        return DONE

    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(side_effect=effect)):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert seen_at_effect["state"] == SUBTASK_STATE_TOOL_STARTED, "STARTED was not on disk when the effect ran"
    assert _state(tmp_path).subtask_states["t1"] == "completed" and result.legacy_result["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
async def test_a_started_persistence_failure_at_any_step_means_the_effect_never_runs_and_nothing_claims_success(tmp_path, monkeypatch, step):
    Faults(monkeypatch, write=1, step=step)
    effect = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    effect.assert_not_awaited()                                       # the side effect did NOT run
    assert result.legacy_result["status"] != "success" and result.legacy_result["quality_gates_passed"] is False
    t1 = _tool_results(result)["t1"]
    assert t1["status"] == "failed" and TOOL_STARTED_NOT_DURABLE in t1["reason_codes"]
    assert calls == [], "a later subtask ran after a TOOL STARTED failure"


@pytest.mark.asyncio
@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
async def test_a_completed_persistence_failure_is_needs_review_never_success_and_never_retried(tmp_path, monkeypatch, step):
    Faults(monkeypatch, write=2, step=step)
    effect = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert effect.await_count == 1                                    # the effect ran exactly once
    assert result.legacy_result["status"] == "needs_review" and result.legacy_result["quality_gates_passed"] is False
    t1 = _tool_results(result)["t1"]
    assert t1["status"] == "needs_review" and TOOL_COMPLETED_NOT_DURABLE in t1["reason_codes"]
    assert t1["tool_output"] == "effect-output", "the tool's own output must be kept as the evidence"
    assert calls == [], "a later subtask ran after an unresolved TOOL execution"
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_STARTED, "the lifecycle must stay unresolved on disk, never 'completed'"
    with patch(f"{C}.subtask_executor.execute", new=effect):          # a resume: no automatic retry, still needs_review
        monkeypatch.undo()
        resumed, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    assert effect.await_count == 1, "the unresolved TOOL execution was retried"
    assert resumed.legacy_result["status"] == "needs_review" and TOOL_EXECUTION_UNRESOLVED in _tool_results(resumed)["t1"]["reason_codes"] and calls == []
