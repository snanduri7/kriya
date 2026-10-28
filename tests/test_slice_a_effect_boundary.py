"""Wave 2 Slice A, stage 1: the typed effect-boundary fact of a failed TOOL result.

The owner (kriya/tools/tool.py::execute_model_authored) classifies by PHASE: everything raised before ``_run_model_authored`` is invoked is PROVEN_PRE_EFFECT; anything raised
from that call onward, and anything nobody classified, is MAY_HAVE_EFFECTED. The same exception classes (ModelInvocationRefused, PolicyDeniedError, ToolExecutionError) arise on BOTH
sides of the boundary, so no test here (and no code) keys on a class, a status or a reason code."""
from unittest.mock import MagicMock

import pytest
from _planned_tool_seam import admit_planned_test_tool
from _strict_doubles import strict_kernel
from pydantic import BaseModel, ConfigDict
from test_tool001_autonomous_tool_execution import (  # noqa: F401 - the autouse fixture admits the mechanics double and sets the mode
    _plan,
    _RecordingTool,
    _tool_mechanics_not_hitl_gate,
    _tool_subtask,
)

from kriya.core.registry import ComponentRegistryError
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.tools.tool import (
    TOOL_OPERATOR_ONLY,
    ModelInvocationRefused,
    ToolEffectBoundary,
    ToolExecutionError,
    effect_boundary_of,
)
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

PRE, MAY = ToolEffectBoundary.PROVEN_PRE_EFFECT, ToolEffectBoundary.MAY_HAVE_EFFECTED


def _denial():
    request = ActionRequest(action_type=ActionType.WRITE_FILE, target="x")
    return PolicyDeniedError(request=request, result=PolicyResult(decision=PolicyDecision.DENY, reason_code="TEST_DENIED", explanation="no", matched_rule="t"))


def _kernel(tool=None, *, missing=False):
    kernel = strict_kernel()
    kernel.registry.get = MagicMock(side_effect=ComponentRegistryError("no such tool")) if missing else MagicMock(return_value=tool)
    return kernel


async def _execute(subtask, kernel):
    return await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)


def _revalidate(subtask, kernel):
    return subtask_executor.revalidate_tool_subtask_authority(subtask, kernel, (), _plan(subtask), None)


def test_the_unclassified_default_is_may_have_effected_everywhere():
    assert effect_boundary_of(ToolExecutionError("raw, nobody classified this")) == MAY
    assert effect_boundary_of(RuntimeError("not even a tool error")) == MAY
    unclassified = SubtaskResult(subtask_id="s1", status=SubtaskStatus.FAILED, execution_method="tool")
    assert unclassified.effect_boundary is None and unclassified.resolved_effect_boundary == MAY      # no fact = may have effected, never pre-effect


@pytest.mark.asyncio
async def test_a_policy_denial_in_the_authority_phase_is_proven_pre_effect_and_the_effect_never_ran(monkeypatch):
    tool = _RecordingTool(return_value="done")
    monkeypatch.setattr(_RecordingTool, "authorize_model_invocation", lambda self, validated, invocation: (_ for _ in ()).throw(_denial()))
    result = await _execute(_tool_subtask(tool_arguments={"path": "a"}), _kernel(tool))
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == ("TEST_DENIED",)
    assert result.effect_boundary == PRE
    tool.calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_call_time_authority_denial_is_proven_pre_effect_and_the_effect_never_ran(monkeypatch):
    """revalidate_authority is the tool's own call-time authority (MCP: TOOL-002/003). The execute path now runs it BEFORE ``_run_model_authored``."""
    tool = _RecordingTool(return_value="done")
    monkeypatch.setattr(_RecordingTool, "revalidate_authority", lambda self, args, invocation: (_ for _ in ()).throw(_denial()))
    result = await _execute(_tool_subtask(tool_arguments={"path": "a"}), _kernel(tool))
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == ("TEST_DENIED",) and result.effect_boundary == PRE
    tool.calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_governance_refusal_before_the_run_phase_is_proven_pre_effect(monkeypatch):
    tool = _RecordingTool(return_value="done")
    monkeypatch.setattr(_RecordingTool, "authorize_model_invocation", lambda self, validated, invocation: (_ for _ in ()).throw(ModelInvocationRefused(TOOL_OPERATOR_ONLY, "no")))
    result = await _execute(_tool_subtask(tool_arguments={"path": "a"}), _kernel(tool))
    assert result.status == SubtaskStatus.NEEDS_REVIEW and result.effect_boundary == PRE
    tool.calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_validation_failure_before_the_run_phase_is_proven_pre_effect(monkeypatch):
    class _Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")
        path: str

    class _StrictTool(_RecordingTool):
        arguments_schema = _Strict

        @property
        def planned_governance(self):
            return super().planned_governance

    admit_planned_test_tool(monkeypatch, _StrictTool)
    tool = _StrictTool(return_value="done")
    result = await _execute(_tool_subtask(tool_arguments={"nope": 1}), _kernel(tool))
    assert result.status == SubtaskStatus.FAILED and "Argument validation failed" in (result.error or "") and result.effect_boundary == PRE
    tool.calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_failure_and_the_revalidate_path_are_proven_pre_effect():
    subtask = _tool_subtask(tool_arguments={"path": "a"})
    missing = await _execute(subtask, _kernel(missing=True))
    assert missing.status == SubtaskStatus.NEEDS_REVIEW and missing.effect_boundary == PRE
    assert _revalidate(subtask, _kernel(missing=True)).effect_boundary == PRE


@pytest.mark.asyncio
async def test_revalidation_refusal_of_a_still_registered_tool_is_proven_pre_effect(monkeypatch):
    tool = _RecordingTool(return_value="done")
    monkeypatch.setattr(_RecordingTool, "revalidate_authority", lambda self, args, invocation: (_ for _ in ()).throw(_denial()))
    refusal = _revalidate(_tool_subtask(tool_arguments={"path": "a"}), _kernel(tool))
    assert refusal is not None and refusal.effect_boundary == PRE
    tool.calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_tool_that_invokes_its_effect_and_then_raises_is_may_have_effected():
    tool = _RecordingTool(side_effect=RuntimeError("died after the effect began"))
    result = await _execute(_tool_subtask(tool_arguments={"path": "a"}), _kernel(tool))
    assert result.status == SubtaskStatus.FAILED and result.effect_boundary == MAY
    tool.calls.assert_awaited_once()                                  # the control: the effect WAS invoked


@pytest.mark.asyncio
@pytest.mark.parametrize("raised", [
    ModelInvocationRefused(TOOL_OPERATOR_ONLY, "raised inside the run phase"),   # the SAME class the authority phase raises
    _denial(),                                                                    # the SAME cause the authority phase carries
    ToolExecutionError("a typed tool error from inside the run"),
], ids=["ModelInvocationRefused", "PolicyDeniedError", "ToolExecutionError"])
async def test_the_same_exception_classes_raised_in_the_run_phase_are_may_have_effected(raised):
    """Phase, not class: these exact classes are PROVEN_PRE_EFFECT from the authority phase (tests above)."""
    tool = _RecordingTool(side_effect=raised)
    result = await _execute(_tool_subtask(tool_arguments={"path": "a"}), _kernel(tool))
    assert result.status == SubtaskStatus.FAILED and result.effect_boundary == MAY
    tool.calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_completed_tool_runs_its_effect_once_after_the_authority_phase():
    tool = _RecordingTool(return_value="ok")
    result = await _execute(_tool_subtask(tool_arguments={"path": "a"}), _kernel(tool))
    assert result.status == SubtaskStatus.COMPLETED
    tool.calls.assert_awaited_once_with(path="a")
