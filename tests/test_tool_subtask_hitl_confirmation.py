"""Review of the runtime-approval stack (2026-10-15), B3: a planner-emitted TOOL subtask whose tool asks to be confirmed when an operator runs it (ShellTool: `requires_confirmation`)
is NOT executed in human-in-the-loop mode - the CLI asks, a planned step cannot - and is executed unchanged in every other mode. Nothing runs before the refusal."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from _strict_doubles import strict_config, strict_kernel

from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, Subtask
from kriya.workflow.subtask_executor import TOOL_CONFIRMATION_UNSUPPORTED
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus


def _subtask():
    return Subtask(id="s1", description="run it", execution_method=ExecutionMethod.TOOL, tool_name="shell", tool_arguments={"command": "echo hi"})


def _run(mode, requires_confirmation):
    tool = MagicMock()
    tool.requires_confirmation = requires_confirmation
    tool.execute = AsyncMock(return_value="ran")
    kernel = strict_kernel(strict_config(autonomy={"mode": mode}))
    kernel.registry.get = MagicMock(return_value=tool)
    subtask = _subtask()
    import asyncio
    result = asyncio.run(subtask_executor.execute(subtask=subtask, plan=EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[subtask]), context=build_context_package(), kernel=kernel))
    return result, tool


def test_hitl_refuses_a_confirmation_requiring_tool_before_it_runs():
    result, tool = _run("human-in-the-loop", True)
    assert result.status == SubtaskStatus.NEEDS_REVIEW and result.reason_codes == (TOOL_CONFIRMATION_UNSUPPORTED,)
    assert TOOL_CONFIRMATION_UNSUPPORTED in result.error and "Nothing was run" in result.error
    tool.execute.assert_not_called()


@pytest.mark.parametrize("mode, confirms", [("guardrails", True), ("human-in-the-loop", False), ("guardrails", False)])
def test_every_other_case_executes_unchanged(mode, confirms):
    result, tool = _run(mode, confirms)
    assert result.status == SubtaskStatus.COMPLETED and result.tool_output == "ran"
    tool.execute.assert_awaited_once_with(command="echo hi")
