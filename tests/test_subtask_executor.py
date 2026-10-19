"""MA6.5: SubtaskExecutor (kriya/workflow/subtask_executor.py) - first real
pytest coverage for this module."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from _planned_tool_seam import admit_planned_test_tool
from _strict_doubles import strict_kernel
from pydantic import BaseModel, ConfigDict

from kriya.core.registry import ComponentRegistryError
from kriya.tools.tool import BaseTool, ToolExecutionError, ToolGovernance
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus


@pytest.fixture(autouse=True)
def _tool_mechanics_not_hitl_gate(monkeypatch):
    """These tests exercise the tool-execution MECHANICS outside human-in-the-loop (the executor reads the mode as
    "guardrails"; S1 refuses undeclared and mutating planned steps in human-in-the-loop inside the tool boundary). The
    human-in-the-loop refusals are pinned in tests/test_tool_subtask_hitl_confirmation.py and
    tests/test_s1_toolbox_authority.py."""
    monkeypatch.setattr("kriya.workflow.subtask_executor._autonomy_mode", lambda kernel: "guardrails")
    admit_planned_test_tool(monkeypatch, _RecordingTool)  # S18: a mechanics double (tests/_planned_tool_seam.py)


class _RecordingArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _RecordingTool(BaseTool):
    """A real BaseTool double: S1's model-authored entry refuses anything that is not a BaseTool."""

    name = "lint"
    description = "a deterministic test tool"
    arguments_schema = _RecordingArgs

    def __init__(self, **calls_kwargs):
        self.calls = AsyncMock(**calls_kwargs)

    @property
    def planned_governance(self):
        return ToolGovernance.READ_ONLY

    async def _run(self, args):
        return await self.calls(**args.model_dump())



def _model_subtask(**overrides):
    defaults = dict(
        id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
    )
    defaults.update(overrides)
    return Subtask(**defaults)


def _tool_subtask(**overrides):
    defaults = dict(id="s1", description="run lint", execution_method=ExecutionMethod.TOOL, tool_name="lint")
    defaults.update(overrides)
    return Subtask(**defaults)


def _plan(subtask):
    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=[subtask])


# --- dispatch / caller-contract errors ---

@pytest.mark.asyncio
async def test_tool_subtask_without_kernel_raises_value_error():
    subtask = _tool_subtask()
    with pytest.raises(ValueError, match="no kernel was supplied"):
        await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package())


@pytest.mark.asyncio
async def test_model_subtask_without_developer_agent_raises_value_error():
    subtask = _model_subtask()
    with pytest.raises(ValueError, match="no developer_agent was supplied"):
        await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package())


# --- MODEL subtasks ---

@pytest.mark.asyncio
async def test_model_subtask_completes_and_returns_declared_files():
    subtask = _model_subtask()
    developer_agent = MagicMock()
    developer_agent.run_generation = AsyncMock(return_value=[{"filepath": "a.py", "content": "print(1)"}])

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), developer_agent=developer_agent,
    )

    assert result.status == SubtaskStatus.COMPLETED
    assert result.execution_method == ExecutionMethod.MODEL.value
    assert result.files == ({"filepath": "a.py", "content": "print(1)"},)
    assert result.undeclared_files == ()


@pytest.mark.asyncio
async def test_model_subtask_flags_undeclared_files():
    subtask = _model_subtask()  # only declares a.py
    developer_agent = MagicMock()
    developer_agent.run_generation = AsyncMock(return_value=[
        {"filepath": "a.py", "content": "x"},
        {"filepath": "b.py", "content": "y"},
    ])

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), developer_agent=developer_agent,
    )

    assert result.status == SubtaskStatus.COMPLETED
    assert result.undeclared_files == ("b.py",)


@pytest.mark.asyncio
async def test_model_subtask_developer_failure_yields_failed_status():
    subtask = _model_subtask()
    developer_agent = MagicMock()
    developer_agent.run_generation = AsyncMock(side_effect=RuntimeError("model call failed"))

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), developer_agent=developer_agent,
    )

    assert result.status == SubtaskStatus.FAILED
    assert result.error == "model call failed"
    assert result.files == ()


@pytest.mark.asyncio
async def test_model_subtask_passes_known_target_files_from_planned_files():
    subtask = _model_subtask(planned_files=[
        PlannedFile(path="a.py", action=FileAction.CREATE),
        PlannedFile(path="b.py", action=FileAction.DELETE),
    ])
    developer_agent = MagicMock()
    developer_agent.run_generation = AsyncMock(return_value=[])

    await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), developer_agent=developer_agent,
    )

    _, kwargs = developer_agent.run_generation.call_args
    # DELETE-action files are excluded from known_target_files (nothing for
    # the Developer to generate content for)
    assert kwargs["known_target_files"] == ["a.py"]


# --- TOOL subtasks ---

@pytest.mark.asyncio
async def test_tool_subtask_completes_with_tool_output():
    subtask = _tool_subtask(tool_arguments={"path": "a.py"})
    tool = _RecordingTool(return_value={"lint": "clean"})
    kernel = strict_kernel()
    kernel.registry.get = MagicMock(return_value=tool)

    result = await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)

    assert result.status == SubtaskStatus.COMPLETED
    assert result.execution_method == ExecutionMethod.TOOL.value
    assert result.tool_output == {"lint": "clean"}
    tool.calls.assert_awaited_once_with(path="a.py")


@pytest.mark.asyncio
async def test_tool_subtask_unregistered_tool_yields_needs_review():
    subtask = _tool_subtask()
    kernel = strict_kernel()
    kernel.registry.get = MagicMock(side_effect=ComponentRegistryError("not found"))

    result = await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)

    assert result.status == SubtaskStatus.NEEDS_REVIEW
    assert "not registered" in result.error


@pytest.mark.asyncio
async def test_tool_subtask_execution_error_yields_failed():
    subtask = _tool_subtask()
    tool = _RecordingTool(side_effect=ToolExecutionError("bad args"))
    kernel = strict_kernel()
    kernel.registry.get = MagicMock(return_value=tool)

    result = await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)

    assert result.status == SubtaskStatus.FAILED
    # The boundary wraps a run failure exactly as BaseTool.execute() does (the old bare-MagicMock double skipped it).
    assert result.error == "Tool execution failed: bad args"
