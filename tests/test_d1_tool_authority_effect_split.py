"""D1 slice 3a/3b: the AUTHORITY decision of a TOOL subtask and its EFFECT are separable. A resume revalidates authority for an already-COMPLETED execution WITHOUT running the
side effect again; the revalidate-only path returns exactly the typed refusal the execute path would, and the execute path still runs the effect once."""
from unittest.mock import MagicMock

import pytest
from _strict_doubles import strict_kernel
from test_tool001_autonomous_tool_execution import (  # noqa: F401 - the autouse fixture admits the mechanics double and sets the mode
    _plan,
    _RecordingTool,
    _tool_mechanics_not_hitl_gate,
    _tool_subtask,
)

from kriya.core.registry import ComponentRegistryError
from kriya.tools.tool import ToolExecutionError
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.workflow_types import SubtaskStatus


def _kernel(tool=None, *, missing=False):
    kernel = strict_kernel()
    if missing:
        kernel.registry.get = MagicMock(side_effect=ComponentRegistryError("no such tool"))
    else:
        kernel.registry.get = MagicMock(return_value=tool)
    return kernel


def _revalidate(subtask, kernel):
    return subtask_executor.revalidate_tool_subtask_authority(subtask, kernel, (), _plan(subtask), None)


async def _execute(subtask, kernel):
    return await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)


@pytest.mark.asyncio
async def test_revalidation_runs_no_effect_while_the_execute_path_runs_it_exactly_once():
    subtask = _tool_subtask(tool_arguments={"path": "a.py"})
    tool = _RecordingTool(return_value={"lint": "clean"})
    assert _revalidate(subtask, _kernel(tool)) is None, "a still-authorized call revalidates clean"
    tool.calls.assert_not_awaited()                                   # AUTHORITY only: the effect never ran
    result = await _execute(subtask, _kernel(tool))
    assert result.status == SubtaskStatus.COMPLETED
    tool.calls.assert_awaited_once_with(path="a.py")                  # the positive control: the same path DOES run the effect


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unregistered", "authority-refusal", "malformed-mode"])
async def test_a_refusal_is_identical_on_the_revalidate_and_the_execute_paths_and_neither_runs_the_effect(case, monkeypatch):
    subtask = _tool_subtask(tool_arguments={"path": "a.py"})
    tool = _RecordingTool(return_value={"lint": "clean"})
    if case == "unregistered":
        kernel = _kernel(missing=True)
    elif case == "authority-refusal":
        monkeypatch.setattr(_RecordingTool, "authorize_model_invocation", lambda self, validated, invocation: (_ for _ in ()).throw(ToolExecutionError("authority denied")))
        kernel = _kernel(tool)
    else:
        from kriya.config.authority import AUTONOMY_MODE_UNKNOWN, AutonomyModeError
        monkeypatch.setattr(subtask_executor, "_autonomy_mode", lambda k: (_ for _ in ()).throw(AutonomyModeError(AUTONOMY_MODE_UNKNOWN, kind="str")))
        kernel = _kernel(tool)
    revalidated = _revalidate(subtask, kernel)
    executed = await _execute(subtask, kernel)
    assert revalidated is not None and executed.status != SubtaskStatus.COMPLETED
    assert (revalidated.status, revalidated.reason_codes, revalidated.error) == (executed.status, executed.reason_codes, executed.error)
    tool.calls.assert_not_awaited()
