"""MA6.5: SubtaskExecutor (kriya/workflow/subtask_executor.py) - first real
pytest coverage for this module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.core.registry import ComponentRegistryError
from kriya.tools.tool import ToolExecutionError
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus


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
    tool = MagicMock()
    tool.execute = AsyncMock(return_value={"lint": "clean"})
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=tool)

    result = await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)

    assert result.status == SubtaskStatus.COMPLETED
    assert result.execution_method == ExecutionMethod.TOOL.value
    assert result.tool_output == {"lint": "clean"}
    tool.execute.assert_awaited_once_with(path="a.py")


@pytest.mark.asyncio
async def test_tool_subtask_unregistered_tool_yields_needs_review():
    subtask = _tool_subtask()
    kernel = MagicMock()
    kernel.registry.get = MagicMock(side_effect=ComponentRegistryError("not found"))

    result = await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)

    assert result.status == SubtaskStatus.NEEDS_REVIEW
    assert "not registered" in result.error


@pytest.mark.asyncio
async def test_tool_subtask_execution_error_yields_failed():
    subtask = _tool_subtask()
    tool = MagicMock()
    tool.execute = AsyncMock(side_effect=ToolExecutionError("bad args"))
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=tool)

    result = await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel)

    assert result.status == SubtaskStatus.FAILED
    assert result.error == "bad args"
