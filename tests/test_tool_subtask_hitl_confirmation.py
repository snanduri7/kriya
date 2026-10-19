"""Review of the runtime-approval stack (2026-10-15), B3: a planner-emitted TOOL subtask whose tool asks to be confirmed when an operator runs it (ShellTool: `requires_confirmation`)
is NOT executed in human-in-the-loop mode - the CLI asks, a planned step cannot - and is executed unchanged in every other mode. Nothing runs before the refusal."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from _strict_doubles import strict_config, strict_kernel
from pydantic import BaseModel

from kriya.tools.tool import TOOL_MODEL_AUTHORITY_UNDECLARED, BaseTool
from kriya.workflow import subtask_executor
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, Subtask
from kriya.workflow.subtask_executor import TOOL_CONFIRMATION_UNSUPPORTED
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus


def _subtask():
    return Subtask(id="s1", description="run it", execution_method=ExecutionMethod.TOOL, tool_name="shell", tool_arguments={"command": "echo hi"})


class _Args(BaseModel):
    command: str


class _Tool(BaseTool):
    """A real BaseTool double that declares nothing about model-authored calls (S1 refuses non-BaseTool objects)."""

    name = "shell"
    description = "test double"
    arguments_schema = _Args

    def __init__(self, requires_confirmation):
        self._requires_confirmation = requires_confirmation
        self.calls = AsyncMock(return_value="ran")

    @property
    def requires_confirmation(self):
        return self._requires_confirmation

    async def _run(self, args):
        return await self.calls(**args.model_dump())


def _run(mode, requires_confirmation):
    tool = _Tool(requires_confirmation)
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
    tool.calls.assert_not_called()


def test_hitl_refuses_a_tool_without_a_confirmation_flag_too():
    """S1 (owner 2026-10-19: no authority is inferred from a missing confirmation flag): before S1 this case was in the
    'executes unchanged' list below - the defect. A tool that declares nothing about planned calls is refused in
    human-in-the-loop before it runs."""
    result, tool = _run("human-in-the-loop", False)
    assert result.status == SubtaskStatus.NEEDS_REVIEW and result.reason_codes == (TOOL_MODEL_AUTHORITY_UNDECLARED,)
    tool.calls.assert_not_called()


@pytest.mark.parametrize("mode, confirms", [("guardrails", True), ("guardrails", False)])
def test_every_other_case_executes_unchanged(mode, confirms):
    result, tool = _run(mode, confirms)
    assert result.status == SubtaskStatus.COMPLETED and result.tool_output == "ran"
    tool.calls.assert_awaited_once_with(command="echo hi")
