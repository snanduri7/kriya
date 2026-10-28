"""Wave 2 Slice A, authority sweep (W2-03) and the reachability of the operator control plane.

Structural pins (AST, so a new caller fails here until it is classified) + a real MCPTool through the model-authored path:
  * `_run_model_authored` - the only call that can start a planned TOOL effect - has exactly one caller, `execute_model_authored`, which runs the whole authority phase first;
  * the operator disposition module is imported by the `kriya runs` CLI adapter only, and imports nothing that can execute a tool, call an MCP server or reach a model;
  * a revoked / absent MCP approval is refused PROVEN_PRE_EFFECT with zero tools/call (TOOL-002 revoked-approval refusal preserved)."""
import ast
import os
import sys
import tempfile

import pytest
from _mcp_containment_standin import client_kwargs

from kriya.mcp.mcp import MCPClient, MCPTool
from kriya.policy.errors import PolicyDeniedError
from kriya.tools.tool import (
    ModelToolInvocation,
    ToolEffectBoundary,
    ToolExecutionError,
    effect_boundary_of,
    execute_model_authored,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "tool002_mcp_fixture.py")
SOURCE_ROOTS = ("kriya", "plugins")


def _sources():
    for root in SOURCE_ROOTS:
        for directory, _dirs, files in os.walk(os.path.join(REPO_ROOT, root)):
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(directory, name)
                    yield os.path.relpath(path, REPO_ROOT), ast.parse(open(path, encoding="utf-8").read(), filename=path)


def _call_sites(attribute):
    return sorted({rel for rel, tree in _sources() for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attribute})


def _importers(module):
    found = set()
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module == module or (node.module or "").startswith(module + ".")):
                found.add(rel)
            elif isinstance(node, ast.ImportFrom) and node.module and any(f"{node.module}.{a.name}" == module for a in node.names):
                found.add(rel)
            elif isinstance(node, ast.Import) and any(a.name == module or a.name.startswith(module + ".") for a in node.names):
                found.add(rel)
    return sorted(found)


def test_the_planned_tool_effect_has_exactly_one_caller_and_it_is_the_phase_owner():
    assert _call_sites("_run_model_authored") == ["kriya/tools/tool.py"]
    assert _call_sites("revalidate_authority") == ["kriya/tools/tool.py"]            # the authority phase is the only caller of the call-time authority hook


def test_only_the_subtask_executor_enters_the_planned_tool_boundary():
    callers = {rel for rel, tree in _sources() for node in ast.walk(tree) if isinstance(node, ast.Call)
               and getattr(node.func, "id", getattr(node.func, "attr", None)) in ("execute_model_authored", "revalidate_model_authored")}
    assert callers - {"kriya/tools/tool.py"} == {"kriya/workflow/subtask_executor.py"}


def test_the_operator_disposition_module_is_reachable_from_the_runs_cli_adapter_only():
    assert _importers("kriya.control.tool_disposition") == ["kriya/cli_commands/runs.py"]


def test_the_operator_disposition_module_imports_nothing_that_can_execute_a_tool_a_server_or_a_model():
    forbidden = ("kriya.tools", "kriya.mcp", "kriya.plugins", "kriya.workflow", "kriya.agents", "kriya.core.llm", "kriya.core.kernel", "kriya.policy", "plugins")
    tree = ast.parse(open(os.path.join(REPO_ROOT, "kriya/control/tool_disposition.py"), encoding="utf-8").read())
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert [m for m in imported if any(m == f or m.startswith(f + ".") for f in forbidden)] == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("mcp_standin")
async def test_a_revoked_or_absent_mcp_approval_is_refused_before_any_dispatch_through_the_model_authored_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_approvals"))        # an empty durable-approval store: nothing approved
    with tempfile.TemporaryDirectory() as d:
        call_log = os.path.join(d, "calls.log")
        client = MCPClient(name="fixture", command=sys.executable, args=[FIXTURE], env={"CALL_LOG_FILE": call_log}, **client_kwargs())
        await client.start()
        try:
            echo = next(t for t in await client.list_tools() if t["name"] == "echo")
            tool = MCPTool(client, echo)
            invocation = ModelToolInvocation(roots=(str(tmp_path),), autonomy_mode="guardrails", reads_candidate=False)
            with pytest.raises(ToolExecutionError) as refused:
                await execute_model_authored(tool, {"message": "hi"}, invocation)
            assert isinstance(refused.value.__cause__, PolicyDeniedError)              # the TOOL-002 decision, not a string match
            assert effect_boundary_of(refused.value) == ToolEffectBoundary.PROVEN_PRE_EFFECT
        finally:
            await client.stop()
        assert not os.path.exists(call_log) or open(call_log, encoding="utf-8").read().strip() == "", "tools/call was dispatched"
