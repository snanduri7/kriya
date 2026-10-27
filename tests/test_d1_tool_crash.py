"""D1 slice 3c: crash windows with a genuine process boundary. A child process runs the real enforce loop and dies (os._exit(137), no finalizer) at each window of
STARTED -> effect -> COMPLETED; a fresh parent reloads the persisted state, resumes, and must classify the unresolved TOOL execution as needs_review WITHOUT running the effect."""
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_d1_tool_lifecycle import _plan, _state, _tool_results
from test_e12_enforce_characterization import C, _enforce, _enforce_engine

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
    assert result.legacy_result["status"] == "needs_review" and result.legacy_result["quality_gates_passed"] is False
    assert TOOL_EXECUTION_UNRESOLVED in _tool_results(result)["t1"]["reason_codes"] and calls == []
    assert _state(tmp_path).subtask_states["t1"] == SUBTASK_STATE_TOOL_STARTED, "the unresolved record was overwritten or erased"
    assert before and os.path.isfile(control_state_path(str(tmp_path)))


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
