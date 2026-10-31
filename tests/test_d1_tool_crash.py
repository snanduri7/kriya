"""D1 slice 3c: crash windows with a genuine process boundary. A child process runs the real enforce loop and dies (os._exit(137), no finalizer) at each window of
STARTED -> effect -> COMPLETED; a fresh parent reloads the persisted state, resumes, and must classify the unresolved TOOL execution as needs_review WITHOUT running the effect."""
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_d1_tool_lifecycle import _plan, _state, _tool_results
from test_e12_enforce_characterization import C, _enforce, _enforce_engine

from kriya.control import commit_state
from kriya.control.persistence import control_state_path
from kriya.control.state import SUBTASK_STATE_TOOL_STARTED
from kriya.workflow.resume_support import TOOL_EXECUTION_UNRESOLVED
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_d1_tool_crash_child.py")

# window -> the lines the CHILD's effect wrote before it died
WINDOWS = {
    "before_effect": [],
    "during_effect": ["EFFECT_BEGIN"],
    "after_effect_before_completed": ["EFFECT_BEGIN", "EFFECT_DONE"],
    "during_completed_persistence": ["EFFECT_BEGIN", "EFFECT_DONE"],
}


def _crash(tmp_path, window):
    effects = tmp_path.parent / f"{tmp_path.name}-effects.log"
    done = subprocess.run([sys.executable, CHILD, str(tmp_path), str(effects), window], capture_output=True, text=True, timeout=240, check=False)
    assert done.returncode == 137, f"the child did not die at the window: rc={done.returncode}\n{done.stderr[-1500:]}"
    return effects.read_text().splitlines() if effects.exists() else []


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("window", list(WINDOWS))
async def test_a_crash_in_any_window_resumes_as_needs_review_and_the_effect_is_never_run_again(tmp_path, window):
    assert _crash(tmp_path, window) == WINDOWS[window]
    before = open(control_state_path(str(tmp_path)), "rb").read()
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_STARTED, "the unresolved STARTED record must be what survived the crash"
    effect = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    revalidation = MagicMock(return_value=None)
    with patch(f"{C}.subtask_executor.execute", new=effect), patch(f"{C}.subtask_executor.revalidate_tool_subtask_authority", new=revalidation):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)   # a NEW engine and controller: state comes from disk only
    effect.assert_not_awaited()                                       # not retried, whichever window it died in
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False and calls == []
    assert commit_state.REASON_UNRESOLVED_TOOL_EXECUTION in legacy["reason_codes"], legacy   # the ONE gate refused before anything started
    assert legacy["unresolved_tool_executions"][0]["subtask_ids"] == ["t1"]
    assert open(control_state_path(str(tmp_path)), "rb").read() == before, "the unresolved record was overwritten or erased"
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_STARTED


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_effect", "during_effect"])
async def test_a_FRESH_run_after_a_crash_cannot_overwrite_the_unresolved_record_or_run_the_effect(tmp_path, window):
    """What could erase the only evidence? The ControlState is ONE document per workspace: a fresh (non-resume) run would replace it. The gate refuses instead."""
    _crash(tmp_path, window)
    before = open(control_state_path(str(tmp_path)), "rb").read()
    effect = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan())          # NOT resume
    effect.assert_not_awaited()
    assert result.legacy_result["status"] == "needs_review" and calls == []
    assert commit_state.REASON_UNRESOLVED_TOOL_EXECUTION in result.legacy_result["reason_codes"]
    assert open(control_state_path(str(tmp_path)), "rb").read() == before


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_DIFFERENT_plan_cannot_complete_or_govern_another_executions_unresolved_record(tmp_path):
    """Identity: the unresolved record belongs to run/plan A. A run of plan B (different plan, same subtask ids) neither inherits it nor overwrites it."""
    _crash(tmp_path, "during_effect")
    before = open(control_state_path(str(tmp_path)), "rb").read()
    from test_e12_enforce_characterization import _plan_of, _unit
    other = _plan_of(_unit("t1", "z.py"), _unit("s2", "b.py", depends_on=["t1"]))
    result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, other, resume=True)
    assert result.legacy_result["status"] == "needs_review" and calls == [], "another plan ran over an unresolved TOOL record"
    assert open(control_state_path(str(tmp_path)), "rb").read() == before


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_legacy_in_progress_record_of_a_TOOL_subtask_is_classified_needs_review_by_the_loop_never_retried(tmp_path):
    """A pre-D1 crash left plain "in_progress" (no marker, so the plan-blind gate cannot see it): the resume classification - the one owner - catches it inside the loop."""
    _crash(tmp_path, "during_effect")
    from kriya.control.persistence import load_control_state, save_control_state
    state = load_control_state(str(tmp_path))
    save_control_state(str(tmp_path), state.with_updates(subtask_states={**state.subtask_states, "t1": "in_progress"}))
    effect = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    with patch(f"{C}.subtask_executor.execute", new=effect):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    effect.assert_not_awaited()
    assert result.legacy_result["status"] == "needs_review" and calls == []
    assert TOOL_EXECUTION_UNRESOLVED in _tool_results(result)["t1"]["reason_codes"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_crash_after_a_durable_completed_resumes_without_running_the_effect_again_and_revalidates_authority(tmp_path):
    assert _crash(tmp_path, "after_durable_completed") == ["EFFECT_BEGIN", "EFFECT_DONE"]   # the effect ran exactly once, in the child
    assert _state(tmp_path).subtask_states["t1"] == "completed"
    effect = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    revalidation = MagicMock(return_value=None)
    with patch(f"{C}.subtask_executor.execute", new=effect), patch(f"{C}.subtask_executor.revalidate_tool_subtask_authority", new=revalidation):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan(), resume=True)
    effect.assert_not_awaited()                                       # the completed effect is terminal for its execution identity
    assert revalidation.call_count == 1                               # authority was still revalidated, effect-free
    assert result.legacy_result["status"] == "success" and [c["current_subtask_id"] for c in calls] == ["s2"]
    assert _state(tmp_path).subtask_states["t1"] == "completed"
