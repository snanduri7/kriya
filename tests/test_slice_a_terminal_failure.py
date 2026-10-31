"""Wave 2 Slice A, stage 3: through the REAL enforce loop, a TOOL attempt that FAILED after its effect may have begun is terminal for that attempt; a failure PROVEN to be
before the effect stays re-armable through the ordinary resume and authority path; the attempt number is on disk in the same durable write as tool_started.

The executor's result is the only thing faked (it carries the effect-boundary fact the execution owner attaches, pinned in tests/test_slice_a_effect_boundary.py); everything
after it - state transitions, persistence, the workspace gate and the resume classification - is the real code."""
from unittest.mock import AsyncMock, patch

import pytest
from test_d1_tool_lifecycle import DONE, Faults, _plan, _state, _tool_results
from test_e12_enforce_characterization import C, _enforce, _enforce_engine

from kriya.control import commit_state
from kriya.control.persistence import control_state_path, load_control_state, save_control_state
from kriya.control.state import SUBTASK_STATE_TOOL_ABANDONED, SUBTASK_STATE_TOOL_FAILED, SUBTASK_STATE_TOOL_STARTED
from kriya.repository import atomic_write
from kriya.tools.tool import ToolEffectBoundary
from kriya.workflow.resume_support import TOOL_ATTEMPT_ABANDONED, TOOL_ATTEMPT_FAILED_TERMINAL
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

MAY_FAILED = SubtaskResult(subtask_id="t1", status=SubtaskStatus.FAILED, execution_method="tool", error="died after the effect began",
                           effect_boundary=ToolEffectBoundary.MAY_HAVE_EFFECTED)
PRE_REFUSED = SubtaskResult(subtask_id="t1", status=SubtaskStatus.FAILED, execution_method="tool", error="denied before the effect", reason_codes=("TEST_DENIED",),
                            effect_boundary=ToolEffectBoundary.PROVEN_PRE_EFFECT)


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_attempt_number_is_on_disk_when_the_effect_runs_and_a_may_have_effected_failure_is_terminal(tmp_path):
    seen = {}

    async def effect(**kwargs):
        state = _state(tmp_path)
        seen["at_effect"] = (state.subtask_states.get("t1"), dict(state.tool_attempts))   # read from DISK while the effect runs
        return MAY_FAILED

    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(side_effect=effect)):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert seen["at_effect"] == (SUBTASK_STATE_TOOL_STARTED, {"t1": 1}), "STARTED and its attempt identity must be one durable record"
    state = _state(tmp_path)
    assert state.subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED and state.tool_attempts == {"t1": 1}
    assert result.legacy_result["status"] != "success" and result.legacy_result["quality_gates_passed"] is False and calls == []


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("result,boundary", [(MAY_FAILED, "may_have_effected"), (PRE_REFUSED, "proven_pre_effect")], ids=["may-have-effected", "proven-pre-effect"])
async def test_every_tool_attempt_is_appended_to_the_decision_ledger_with_its_attempt_and_effect_boundary(tmp_path, result, boundary):
    """Rule-4 normal output of the broad catch around the ledger append in the enforce loop (`_record_tool_attempt`): when the append works, the
    history record exists, durable, naming the attempt number, status and boundary - the ControlState still decides what a resume may do."""
    from kriya.control.decisions import DecisionLedger, load_decision_ledger

    real_append, durable_flags = DecisionLedger.append_to_file, []

    def spy(self, workspace_path, decision, **kwargs):
        if decision.type == "subtask_attempt":
            durable_flags.append(kwargs.get("durable"))
        return real_append(self, workspace_path, decision, **kwargs)

    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=result)), patch.object(DecisionLedger, "append_to_file", spy):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert durable_flags == [True]                                    # fsync-grade history, not the default best-effort append
    attempts = [d.to_dict() for d in load_decision_ledger(str(tmp_path)).all() if d.type == "subtask_attempt" and d.fields.get("subtask_id") == "t1"]
    assert len(attempts) == 1
    assert (attempts[0]["attempt"], attempts[0]["status"], attempts[0]["execution_method"], attempts[0]["effect_boundary"]) == (1, "failed", "tool", boundary)


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [True, False], ids=["resume", "fresh-run"])
async def test_a_terminal_failed_attempt_is_never_executed_again_by_a_resume_or_a_fresh_run_and_its_record_survives(tmp_path, resume):
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=MAY_FAILED)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    before = open(control_state_path(str(tmp_path)), "rb").read()
    effect = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=resume)
    effect.assert_not_awaited()                                       # the same attempt is NOT executed again
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False and calls == []
    assert commit_state.REASON_UNRESOLVED_TOOL_EXECUTION in legacy["reason_codes"] and legacy["unresolved_tool_executions"][0]["subtask_ids"] == ["t1"]
    assert open(control_state_path(str(tmp_path)), "rb").read() == before, "the terminal record was overwritten or erased"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_failure_proven_to_be_before_the_effect_stays_re_armable_and_keeps_its_attempt(tmp_path):
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=PRE_REFUSED)):
        first, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    state = _state(tmp_path)
    assert state.subtask_states["t1"] == "failed" and state.tool_attempts == {"t1": 1}      # NOT terminal
    assert state.undisposed_tool_subtask_ids == () and first.legacy_result["status"] != "success"
    effect = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        resumed, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    assert effect.await_count == 1                                    # the ordinary resume path re-runs it (and its fresh authority runs inside the executor)
    assert resumed.legacy_result["status"] == "success" and [c["current_subtask_id"] for c in calls] == ["s2"]
    assert _state(tmp_path).subtask_states["t1"] == "completed" and _state(tmp_path).tool_attempts == {"t1": 1}   # same attempt: nothing had been effected


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_unclassified_failure_defaults_to_terminal(tmp_path):
    """No effect_boundary supplied anywhere = the SubtaskResult default = MAY_HAVE_EFFECTED = terminal (fail closed)."""
    unclassified = SubtaskResult(subtask_id="t1", status=SubtaskStatus.FAILED, execution_method="tool", error="who knows")
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=unclassified)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
async def test_a_terminal_record_that_cannot_be_made_durable_leaves_the_started_record_and_the_effect_is_never_retried(tmp_path, monkeypatch, step):
    Faults(monkeypatch, write=2, step=step)                           # write 1 = STARTED (with its attempt), write 2 = the terminal failure record
    effect = AsyncMock(return_value=MAY_FAILED)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    assert effect.await_count == 1 and calls == [] and result.legacy_result["status"] != "success"
    # the durable terminal write failed; a later ordinary save may still record the (truthful, more restrictive) terminal marker - what it can never be is re-armable
    assert _state(tmp_path).subtask_states["t1"] in (SUBTASK_STATE_TOOL_STARTED, SUBTASK_STATE_TOOL_FAILED) and _state(tmp_path).tool_attempts == {"t1": 1}
    monkeypatch.undo()
    again = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=again):
        resumed, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    again.assert_not_awaited()
    assert resumed.legacy_result["status"] == "needs_review"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_loop_stops_at_an_abandoned_subtask_without_executing_it_and_never_reports_success(tmp_path):
    """tool_abandoned is an operator disposition, not an undisposed record: the workspace gate lets the run start, and the resume classification (one owner) stops at it."""
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=MAY_FAILED)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    state = load_control_state(str(tmp_path))
    save_control_state(str(tmp_path), state.with_updates(subtask_states={**state.subtask_states, "t1": SUBTASK_STATE_TOOL_ABANDONED}))
    effect = AsyncMock(return_value=DONE)
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    effect.assert_not_awaited()
    assert result.legacy_result["status"] != "success" and result.legacy_result["quality_gates_passed"] is False and calls == []
    assert TOOL_ATTEMPT_ABANDONED in _tool_results(result)["t1"]["reason_codes"]
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_ABANDONED and _state(tmp_path).tool_attempts == {"t1": 1}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_terminal_failed_marker_is_classified_by_the_one_resume_owner_when_the_gate_is_not_in_the_way(tmp_path):
    """The loop's own terminal stop (the gate normally refuses first): reach it by excluding the gate's view of the run, as an in-process resume of the same run would."""
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=MAY_FAILED)):
        await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())
    effect = AsyncMock(return_value=DONE)
    real = commit_state.assess_workspace_commit_state
    own_run = _state(tmp_path).run_id

    def without_that_run(workspace, *, exclude_run_ids=()):
        return real(workspace, exclude_run_ids=(*exclude_run_ids, own_run))

    with (patch(f"{C}.subtask_executor.execute", new=effect), patch(f"{C}.assess_workspace_commit_state", new=without_that_run),
          patch("kriya.control.run_coordinator.assess_workspace_commit_state", new=without_that_run)):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    effect.assert_not_awaited()
    assert TOOL_ATTEMPT_FAILED_TERMINAL in _tool_results(result)["t1"]["reason_codes"]
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED
