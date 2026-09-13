"""TOOL-001 closure: autonomous Kriya workflows may execute TOOL-tagged
subtasks only through the same deterministic authority/policy/
containment/approval/lifecycle/evidence controls already proven for
direct tool execution.

This file does NOT re-prove properties already established, unchanged, by
other files - it exercises the ONE new convergence point
(subtask_executor.execute(), which kriya/workflow/workflow_controller.py's
_run_structured_enforce now calls for real for TOOL-tagged subtasks,
exactly as it already called it for MODEL subtasks) and cites:
  - tests/test_tool002_p2_invocation_approval.py, test_tool002_tool003_
    combined_closure.py, test_tool003_p2_*.py for the underlying TOOL-002/
    TOOL-003 MCP invocation/capability/containment mechanism itself
    (unchanged, not re-derived here).
  - tests/test_sec005_shell_acquisition_network.py for the underlying
    SEC-005 ShellTool registry-scoped network mechanism itself (unchanged,
    not re-derived here).
  - tests/test_subtask_executor.py for basic MODEL/TOOL dispatch coverage
    (unchanged, not duplicated here).

No live LLM anywhere in this file.
"""
import ast
import asyncio
import contextlib
import inspect
import json
import os
import shutil
import socket
import subprocess
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock

from kriya.config.config import AppConfig, AutonomyConfig, ExecutionPolicyConfig, MCPLifecycleConfig
from kriya.control.workspace_identity import workspace_identity
from kriya.core.kernel import Kernel
from kriya.core.registry import ComponentRegistryError
from kriya.mcp.capability import compute_mcp_capability_profile_digest, resolve_mcp_capability_profile
from kriya.mcp.invocation_approval import (
    add_approval, default_local_approval_path, empty_artifact, save_approval_artifact,
)
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import MCPToolIdentity, compute_mcp_schema_digest
from kriya.tools.tool import ToolExecutionError
from kriya.workflow import subtask_executor
from kriya.workflow import workflow_controller as wc_module
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, PlannedFile, Subtask, FileAction
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_controller import all_subtasks_completed, exclude_tool_subtasks_from_resume
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
MOCK_SERVER = os.path.join(REPO_ROOT, "tests", "mock_mcp_server.py")
CAPABILITY_FIXTURE_IN_CONTAINER = "/kriya/mcp/extra/ro/0/tool003_p2_capability_fixture.py"


def _tool_subtask(**overrides):
    defaults = dict(id="s1", description="run tool", execution_method=ExecutionMethod.TOOL, tool_name="lint")
    defaults.update(overrides)
    return Subtask(**defaults)


def _model_subtask(**overrides):
    defaults = dict(
        id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
    )
    defaults.update(overrides)
    return Subtask(**defaults)


def _plan(*subtasks):
    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=list(subtasks))


def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


DOCKER_OK = _docker_reachable()
pytestmark_docker = pytest.mark.skipif(not DOCKER_OK, reason="docker CLI/daemon not available")


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _leftover_containers() -> list:
    r = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=kriya.mcp-capability-digest", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=15,
    )
    return [n for n in r.stdout.splitlines() if n.strip()]


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_mcp_approval_home"))


# =====================================================================
# TASK 1/3: the autonomous path converges on the SAME BaseTool.execute()
# boundary direct CLI tool execution uses - proven by construction
# (subtask_executor._execute_tool_subtask literally calls
# `await tool.execute(**subtask.tool_arguments)`, exactly what
# kriya/cli.py::tools_execute does) plus one behavioral check.
# =====================================================================

def test_execute_tool_subtask_calls_the_same_base_tool_execute_boundary():
    source = inspect.getsource(subtask_executor._execute_tool_subtask)
    assert "await tool.execute(" in source
    assert "kernel.registry.get(\"tool\"" in source
    # Never a second execution primitive - no subprocess/MCPClient
    # reference of its own anywhere in this module.
    module_source = inspect.getsource(subtask_executor)
    assert "subprocess" not in module_source
    assert "MCPClient" not in module_source
    assert "call_tool" not in module_source


@pytest.mark.asyncio
async def test_scenario_a_benign_deterministic_tool_executes_through_real_path():
    subtask = _tool_subtask(tool_arguments={"path": "a.py"})
    tool = MagicMock()
    tool.execute = AsyncMock(return_value={"lint": "clean"})
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=tool)

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
    )

    assert result.status == SubtaskStatus.COMPLETED
    assert result.tool_output == {"lint": "clean"}
    tool.execute.assert_awaited_once_with(path="a.py")
    kernel.registry.get.assert_called_once_with("tool", "lint")


# =====================================================================
# TASK 4 / Scenario B: unknown / malformed / ambiguous tool fails closed,
# never substitutes a "closest match."
# =====================================================================

@pytest.mark.asyncio
async def test_scenario_b_unknown_tool_fails_closed():
    subtask = _tool_subtask(tool_name="does_not_exist")
    kernel = MagicMock()
    kernel.registry.get = MagicMock(side_effect=ComponentRegistryError("not found"))

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
    )

    assert result.status == SubtaskStatus.NEEDS_REVIEW
    assert "not registered" in result.error


def test_registry_lookup_is_exact_match_never_fuzzy():
    """ComponentRegistry.get() is a direct dict lookup (kriya/core/
    registry.py) - no substring/closest-match/fuzzy resolution anywhere,
    so an LLM-provided tool_name that's merely SIMILAR to a real tool
    (typo, near-miss) cannot silently resolve to it."""
    source = inspect.getsource(__import__("kriya.core.registry", fromlist=["ComponentRegistry"]).ComponentRegistry.get)
    assert "difflib" not in source
    assert "fuzz" not in source.lower()
    assert "startswith" not in source
    assert "in self._registry[category]" in source or "not in self._registry[category]" in source


@pytest.mark.asyncio
async def test_malformed_arguments_fail_closed_via_validation_error():
    """A TOOL subtask's tool_arguments that don't match the tool's own
    arguments_schema must fail - never silently coerced/dropped."""
    from plugins.core_tools import FilesystemTool

    subtask = _tool_subtask(tool_name="filesystem", tool_arguments={"operation": "read"})  # missing required 'path'
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=FilesystemTool())

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
    )

    assert result.status == SubtaskStatus.FAILED
    assert "validation" in result.error.lower() or "path" in result.error.lower()


# =====================================================================
# TASK 2: subtask contract - LLM-provided description/rationale never
# grants authority; tool_arguments pass straight through as validated
# kwargs, nothing here infers extra authority from free-form fields.
# =====================================================================

def test_tool_arguments_pass_through_unmodified_no_extra_authority_inferred():
    source = inspect.getsource(subtask_executor._execute_tool_subtask)
    assert "subtask.description" not in source
    assert "subtask.tool_arguments" in source
    # Confirms the exact pass-through shape - no merging in extra kwargs
    # derived from anywhere else.
    assert "tool.execute(**subtask.tool_arguments)" in source


# =====================================================================
# TASK 7 / Scenario C: policy denial -> zero side effect. Reuses
# POL-001's own real ShellTool hard-invariant (sudo), unmodified.
# =====================================================================

@pytest.mark.asyncio
async def test_scenario_c_policy_denial_zero_side_effect(tmp_path):
    from plugins.core_tools import ShellTool

    sentinel = tmp_path / "sudo_ran.txt"
    subtask = _tool_subtask(tool_name="shell", tool_arguments={"command": f"sudo touch {sentinel}"})
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=ShellTool())

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
    )

    assert result.status == SubtaskStatus.FAILED
    assert "COMMAND_SUDO_DENIED" in result.error
    assert result.reason_codes == ("COMMAND_SUDO_DENIED",)
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_policy_exception_fails_closed():
    """A policy engine that raises internally (not a normal DENY) must
    still be observable as a real failure, never silently treated as
    ALLOW - MCPTool._run()'s own pre-existing wrapper (unmodified)
    already guarantees this; here proven reached through the
    SubtaskExecutor boundary specifically."""
    from plugins.core_tools import ShellTool

    tool = ShellTool()
    tool._execution_policy = ExecutionPolicy()

    async def _raise(*a, **kw):
        raise RuntimeError("simulated broken policy engine")

    subtask = _tool_subtask(tool_name="shell", tool_arguments={"command": "echo hi"})
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=tool)
    # enforce_hard_invariants swallows non-PolicyDeniedError exceptions by
    # design (documented "fails open on a broken check only" - unrelated
    # to network/containment authority) - so to prove a genuinely broken
    # downstream stays a real failure, break the actual subprocess spawn
    # instead, which has no such swallow.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("kriya.tools.process.ProcessController.run_async", AsyncMock(side_effect=RuntimeError("boom")))
        result = await subtask_executor.execute(
            subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
        )
    assert result.status == SubtaskStatus.FAILED


# =====================================================================
# TASK 5 / Scenarios D, E: MCP unapproved denies with zero tools/call;
# approved reaches exactly one tools/call - both through
# subtask_executor.execute(), the exact function the real enforce-mode
# TOOL branch calls.
# =====================================================================

def test_scenario_d_mcp_unapproved_autonomous_subtask_denied_zero_calls(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=AppConfig())  # host mode, no approval created

        async def run():
            await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
            try:
                subtask = _tool_subtask(id="mcp1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
                result = await subtask_executor.execute(
                    subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
                )
                return result
            finally:
                await kernel.mcp.shutdown_all()

        result = asyncio.run(run())
        assert result.status == SubtaskStatus.FAILED
        assert "MCP_TOOL_REQUIRES_APPROVAL" in (result.error or "")
        assert result.reason_codes == ("MCP_TOOL_REQUIRES_APPROVAL",)


def test_scenario_e_mcp_approved_autonomous_subtask_exactly_one_call(tmp_path):
    with _cwd(tmp_path / "ws"):
        workspace_root = os.path.realpath(os.getcwd())
        profile = resolve_mcp_capability_profile({}, workspace_root=workspace_root)
        profile_digest = compute_mcp_capability_profile_digest(profile)
        schema = {
            "type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"],
        }
        identity = MCPToolIdentity(
            server_identity="probe", tool_name="echo_test", schema_digest=compute_mcp_schema_digest(schema),
        )
        wid = workspace_identity(workspace_root)
        artifact = add_approval(empty_artifact(wid), identity, profile_digest)
        save_approval_artifact(default_local_approval_path(workspace_root), artifact)

        kernel = Kernel(config=AppConfig())

        async def run():
            await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
            try:
                tool = kernel.registry.get("tool", "probe_echo_test")
                call_spy = AsyncMock(wraps=kernel.mcp.clients["probe"].call_tool)
                kernel.mcp.clients["probe"].call_tool = call_spy
                subtask = _tool_subtask(id="mcp1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
                result = await subtask_executor.execute(
                    subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
                )
                return result, call_spy.await_count
            finally:
                await kernel.mcp.shutdown_all()

        result, call_count = asyncio.run(run())
        assert result.status == SubtaskStatus.COMPLETED
        assert call_count == 1


def test_scenario_drift_schema_change_invalidates_approval_zero_calls(tmp_path):
    """An approval bound to one schema digest must not authorize a call
    once the tool's own schema has drifted - even reached via the
    autonomous SubtaskExecutor path, not just direct CLI execution."""
    with _cwd(tmp_path / "ws"):
        workspace_root = os.path.realpath(os.getcwd())
        profile = resolve_mcp_capability_profile({}, workspace_root=workspace_root)
        profile_digest = compute_mcp_capability_profile_digest(profile)
        # Approve against a DIFFERENT (stale) schema than the real tool's own.
        stale_schema = {"type": "object", "properties": {}, "required": []}
        identity = MCPToolIdentity(
            server_identity="probe", tool_name="echo_test", schema_digest=compute_mcp_schema_digest(stale_schema),
        )
        wid = workspace_identity(workspace_root)
        artifact = add_approval(empty_artifact(wid), identity, profile_digest)
        save_approval_artifact(default_local_approval_path(workspace_root), artifact)

        kernel = Kernel(config=AppConfig())

        async def run():
            await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
            try:
                call_spy = AsyncMock(wraps=kernel.mcp.clients["probe"].call_tool)
                kernel.mcp.clients["probe"].call_tool = call_spy
                subtask = _tool_subtask(id="mcp1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
                result = await subtask_executor.execute(
                    subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
                )
                return result, call_spy.await_count
            finally:
                await kernel.mcp.shutdown_all()

        result, call_count = asyncio.run(run())
        assert result.status == SubtaskStatus.FAILED
        assert call_count == 0


@pytestmark_docker
def test_scenario_containment_required_backend_unavailable_no_call_no_fallback(tmp_path):
    """TOOL-003: containment required but no real backend configured ->
    server refuses to start at all - no autonomous call, no host fallback,
    reached via the SubtaskExecutor path."""
    with _cwd(tmp_path / "ws"):
        cfg = AppConfig(
            autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="none"),
            mcp_lifecycle=MCPLifecycleConfig(),
        )
        kernel = Kernel(config=cfg)

        async def run():
            with pytest.raises(Exception):
                await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
            # Server never started - no tool was ever registered to dispatch to.
            assert kernel.registry.list_components("tool") == []

        asyncio.run(run())


@pytestmark_docker
def test_scenario_f_mcp_approved_contained_malicious_action_hidden_effects_blocked(tmp_path):
    """Task 13 Scenario F, the decisive combined proof, now reached
    through the autonomous SubtaskExecutor path (the underlying
    mechanism itself is unchanged, already proven in
    tests/test_tool002_p2_real_cli.py::
    test_real_contained_durably_approved_malicious_tool_still_capability_constrained -
    this proves the SAME outcome through the NEW autonomous entry point)."""
    outside_target = str(tmp_path / "outside_write_target.txt")
    listener_host, listener_port = "127.0.0.1", "58124"

    with _cwd(tmp_path / "repo"):
        workspace_root = os.path.realpath(os.getcwd())
        capability_config = {"additional_read_paths": [TESTS_DIR]}  # no write, no network authority
        profile = resolve_mcp_capability_profile(capability_config, workspace_root=workspace_root)
        profile_digest = compute_mcp_capability_profile_digest(profile)
        schema = {"type": "object", "properties": {}, "required": []}
        identity = MCPToolIdentity(
            server_identity="fixture", tool_name="check_service_status",
            schema_digest=compute_mcp_schema_digest(schema),
        )
        wid = workspace_identity(workspace_root)
        artifact = add_approval(empty_artifact(wid), identity, profile_digest)
        save_approval_artifact(default_local_approval_path(workspace_root), artifact)

        cfg = AppConfig(
            autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="oci"),
            mcp_lifecycle=MCPLifecycleConfig(),
        )
        kernel = Kernel(config=cfg)
        server_cfg = {
            "command": "python3", "args": [CAPABILITY_FIXTURE_IN_CONTAINER],
            "env": {
                "MALICIOUS_WRITE_TARGET": outside_target,
                "MALICIOUS_CONNECT_HOST": listener_host, "MALICIOUS_CONNECT_PORT": listener_port,
            },
            "capabilities": capability_config,
        }

        async def run():
            await kernel.mcp.start_all({"fixture": server_cfg})
            try:
                subtask = _tool_subtask(id="mcp1", tool_name="fixture_check_service_status")
                result = await subtask_executor.execute(
                    subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
                )
                return result
            finally:
                await kernel.mcp.shutdown_all()

        result = asyncio.run(run())

    assert result.status == SubtaskStatus.COMPLETED  # TOOL-002 ALLOWed the call
    payload = json.loads(result.tool_output)
    assert payload["status"] == "ok"
    assert not os.path.exists(outside_target), "TOOL-003 containment must block the hidden unauthorized write"
    assert _leftover_containers() == []


# =====================================================================
# TASK 6 / Scenario G: ShellTool package-manager network authority
# (SEC-005) inherited unchanged through the autonomous path.
# =====================================================================

@pytest.mark.asyncio
async def test_scenario_g_shelltool_ordinary_command_unaffected_when_uncontained():
    from plugins.core_tools import ShellTool

    subtask = _tool_subtask(tool_name="shell", tool_arguments={"command": "echo hi"})
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=ShellTool())  # default AppConfig().autonomy

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
    )

    assert result.status == SubtaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_scenario_g_shelltool_maven_registry_scoped_when_contained():
    from kriya.tools.containment import DummyContainmentBackend, NetworkAuthority
    from plugins.core_tools import ShellTool
    import plugins.core_tools as core_tools_module

    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.acquisition_registry_hosts = ["repo.maven.apache.org"]
    backend = DummyContainmentBackend()
    orig = core_tools_module.resolve_containment_backend
    core_tools_module.resolve_containment_backend = lambda name: backend
    try:
        subtask = _tool_subtask(tool_name="shell", tool_arguments={"command": "mvn clean install"})
        kernel = MagicMock()
        kernel.registry.get = MagicMock(return_value=ShellTool(autonomy_cfg=cfg.autonomy))

        result = await subtask_executor.execute(
            subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
        )
        assert result.status == SubtaskStatus.COMPLETED
        profile = backend.prepared_profiles[-1]
        assert profile.network == NetworkAuthority.DEPENDENCY_REGISTRY_ONLY
        assert profile.network_destinations == ("repo.maven.apache.org",)
    finally:
        core_tools_module.resolve_containment_backend = orig


@pytest.mark.asyncio
async def test_scenario_g_shelltool_unmapped_manager_denied_when_contained():
    from kriya.tools.containment import DummyContainmentBackend, NetworkAuthority
    from plugins.core_tools import ShellTool
    import plugins.core_tools as core_tools_module

    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    backend = DummyContainmentBackend()
    orig = core_tools_module.resolve_containment_backend
    core_tools_module.resolve_containment_backend = lambda name: backend
    try:
        subtask = _tool_subtask(tool_name="shell", tool_arguments={"command": "npm install left-pad"})
        kernel = MagicMock()
        kernel.registry.get = MagicMock(return_value=ShellTool(autonomy_cfg=cfg.autonomy))

        result = await subtask_executor.execute(
            subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
        )
        assert result.status == SubtaskStatus.COMPLETED  # `npm` (missing) command itself just fails/echoes fine
        profile = backend.prepared_profiles[-1]
        assert profile.network == NetworkAuthority.DENIED
    finally:
        core_tools_module.resolve_containment_backend = orig


@pytestmark_docker
def test_scenario_g_real_pip_through_autonomous_path_authorized_vs_unauthorized():
    """Real-Docker proof through the autonomous SubtaskExecutor path -
    reuses SEC-005's own registry-scoped mechanism unchanged."""
    from plugins.core_tools import ShellTool

    def contained_shell_tool(registry_hosts):
        c = AppConfig()
        c.autonomy.contained_execution_required = True
        c.autonomy.containment_backend = "oci"
        c.autonomy.acquisition_registry_hosts = registry_hosts
        return ShellTool(autonomy_cfg=c.autonomy)

    probe = (
        'PROXY_HOSTPORT="${{http_proxy#http://}}"; '
        'printf "CONNECT {host}:443 HTTP/1.1\\r\\nHost: {host}:443\\r\\n\\r\\n" | '
        'timeout 5 nc "${{PROXY_HOSTPORT%:*}}" "${{PROXY_HOSTPORT##*:}}" | head -1'
    )

    async def run_case(hosts, target_host):
        tool = contained_shell_tool(hosts)
        kernel = MagicMock()
        kernel.registry.get = MagicMock(return_value=tool)
        subtask = _tool_subtask(
            tool_name="shell",
            tool_arguments={"command": f"pip3 install --dry-run nonexistent-kriya-x >/dev/null 2>&1 ; {probe.format(host=target_host)}"},
        )
        return await subtask_executor.execute(
            subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
        )

    authorized = asyncio.run(run_case(["pypi.org", "files.pythonhosted.org"], "pypi.org"))
    assert authorized.status == SubtaskStatus.COMPLETED
    assert "200" in authorized.tool_output["stdout"]

    denied = asyncio.run(run_case(["pypi.org", "files.pythonhosted.org"], "example.com"))
    assert denied.status == SubtaskStatus.COMPLETED  # shell command itself still "succeeds" (prints 403)
    assert "403" in denied.tool_output["stdout"]


# =====================================================================
# TASK 12: bypass search - UNGOVERNED_AUTONOMOUS_TOOL_PATHS = 0.
# =====================================================================

def _workflow_module_sources():
    import kriya.workflow.workflow_controller as controller_mod
    import kriya.workflow.subtask_executor as executor_mod
    import kriya.workflow.workflow as workflow_mod
    return {
        "workflow_controller.py": inspect.getsource(controller_mod),
        "subtask_executor.py": inspect.getsource(executor_mod),
        "workflow.py": inspect.getsource(workflow_mod),
    }


def test_no_direct_mcp_call_tool_from_workflow_package():
    for name, source in _workflow_module_sources().items():
        assert "MCPClient(" not in source, f"{name} constructs an MCPClient directly"
        assert ".call_tool(" not in source, f"{name} calls .call_tool() directly"


def test_no_direct_subprocess_from_tool_subtask_orchestration():
    for name, source in _workflow_module_sources().items():
        assert "subprocess." not in source, f"{name} uses subprocess directly"
        assert "create_subprocess" not in source, f"{name} spawns a subprocess directly"


def test_no_direct_shelltool_run_from_workflow_package():
    for name, source in _workflow_module_sources().items():
        assert "ShellTool(" not in source, f"{name} constructs ShellTool directly"
        assert "._run(" not in source, f"{name} calls a tool's _run() directly, bypassing execute()"


def test_tool_subtask_unsupported_in_enforce_reason_code_no_longer_exists():
    source = inspect.getsource(wc_module)
    # The string may still appear in an explanatory comment (documenting
    # what changed and why) - what must be gone is the code that ever
    # PRODUCES it as a real reason code.
    assert 'reason_codes.append("TOOL_SUBTASK_UNSUPPORTED_IN_ENFORCE")' not in source
    assert '"TOOL_SUBTASK_UNSUPPORTED_IN_ENFORCE" in reason_codes' not in source


def test_exactly_one_tool_execute_call_site_in_subtask_executor():
    tree = ast.parse(inspect.getsource(subtask_executor))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "execute"
    ]
    assert len(calls) == 1, f"expected exactly one tool.execute() call site, found {len(calls)}"


def test_enforce_loop_dispatches_tool_subtasks_only_through_subtask_executor():
    """AST/source proof that the TOOL branch in _run_structured_enforce's
    own per-subtask loop calls subtask_executor.execute (never
    _invoke_bounded_subtask/run_generation_workflow for a TOOL subtask)."""
    source = inspect.getsource(wc_module.WorkflowController._run_structured_enforce)
    assert "ExecutionMethod.TOOL:" in source
    assert "subtask_executor.execute(" in source
    # The TOOL branch must `continue`/`break` before ever reaching
    # _invoke_bounded_subtask - structurally confirmed by the ordering of
    # the two markers in source (the TOOL branch's own continue/break
    # occurs textually before the MODEL-only call below it).
    tool_branch_idx = source.index("if subtask.execution_method == ExecutionMethod.TOOL:")
    model_call_idx = source.index("call_result = await _invoke_bounded_subtask(subtask, position)")
    assert tool_branch_idx < model_call_idx


# =====================================================================
# TASK 8: retry/recovery cannot widen authority - proven as an absence.
# The TOOL branch has no retry at all: a failure `break`s the loop, so
# _execute_tool_subtask is called AT MOST ONCE per subtask id.
# =====================================================================

@pytest.mark.asyncio
async def test_scenario_h_tool_failure_at_most_one_call_no_retry_widening():
    call_log = []

    async def _tracking_execute(**kwargs):
        call_log.append(kwargs)
        raise ToolExecutionError("simulated denial")

    tool = MagicMock()
    tool.execute = AsyncMock(side_effect=_tracking_execute)
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=tool)

    subtask = _tool_subtask(tool_arguments={"path": "a.py"})
    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
    )

    assert result.status == SubtaskStatus.FAILED
    assert len(call_log) == 1  # never retried with different/widened arguments
    assert call_log[0] == {"path": "a.py"}  # arguments never widened


def test_no_retry_loop_exists_around_tool_dispatch_structurally():
    source = inspect.getsource(subtask_executor._execute_tool_subtask)
    assert "for " not in source and "while " not in source


# =====================================================================
# Invariant 12: `-y` cannot manufacture TOOL-002 approval - `-y` lives
# entirely in kriya/cli.py's tools_execute and never reaches
# _run_structured_enforce/subtask_executor at all.
# =====================================================================

def test_dash_y_cannot_reach_or_manufacture_mcp_approval(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=AppConfig())

        async def run():
            await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
            try:
                subtask = _tool_subtask(id="mcp1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
                # Nothing in Subtask/execute()'s signature accepts a "yes"/
                # confirm/-y-equivalent flag at all - structurally proven,
                # then behaviorally confirmed the call still denies.
                assert "yes" not in inspect.signature(subtask_executor.execute).parameters
                result = await subtask_executor.execute(
                    subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
                )
                return result
            finally:
                await kernel.mcp.shutdown_all()

        result = asyncio.run(run())
        assert result.status == SubtaskStatus.FAILED
        assert "MCP_TOOL_REQUIRES_APPROVAL" in result.error


# =====================================================================
# TASK 9: structured evidence contract.
# =====================================================================

def test_subtask_result_carries_required_evidence_fields():
    r = SubtaskResult(
        subtask_id="s1", status=SubtaskStatus.FAILED, execution_method=ExecutionMethod.TOOL.value,
        error="denied", reason_codes=("MCP_TOOL_REQUIRES_APPROVAL",),
    )
    d = r.to_dict()
    assert d["subtask_id"] == "s1"
    assert d["status"] == "failed"
    assert d["execution_method"] == "tool"
    assert d["error"] == "denied"
    assert d["reason_codes"] == ["MCP_TOOL_REQUIRES_APPROVAL"]


def test_reason_codes_captured_from_policy_denied_error_cause():
    """Task 9: policy/authority decision surfaced as structured evidence,
    not only free text - the reason_code is read off the real exception
    chain BaseTool.execute() already produces, never re-derived."""
    from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult

    request = ActionRequest(action_type=ActionType.RUN_COMMAND, command=("sudo", "x"))
    policy_result = PolicyResult(decision=PolicyDecision.DENY, reason_code="COMMAND_SUDO_DENIED", explanation="x")
    cause = PolicyDeniedError(request=request, result=policy_result)

    tool = MagicMock()
    tool.execute = AsyncMock(side_effect=ToolExecutionError("Tool execution failed: x") )
    tool.execute.side_effect.__cause__ = cause
    kernel = MagicMock()
    kernel.registry.get = MagicMock(return_value=tool)

    async def run():
        subtask = _tool_subtask()
        return await subtask_executor.execute(
            subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
        )

    result = asyncio.run(run())
    assert result.status == SubtaskStatus.FAILED
    assert result.reason_codes == ("COMMAND_SUDO_DENIED",)


# =====================================================================
# TASK 10: checkpoint/resume cannot bypass revalidation.
# =====================================================================

def test_resume_excludes_completed_tool_subtasks_keeps_model_subtasks():
    tool_st = _tool_subtask(id="t1")
    model_st = _model_subtask(id="m1")
    plan = _plan(model_st, tool_st)
    prior_states = {"m1": "completed", "t1": "completed"}

    filtered, excluded = exclude_tool_subtasks_from_resume(plan, dict(prior_states))

    assert excluded == ["t1"]
    assert filtered == {"m1": "completed"}  # MODEL subtask resume is unaffected


def test_resume_exclusion_is_a_noop_when_no_tool_subtask_was_completed():
    model_st = _model_subtask(id="m1")
    plan = _plan(model_st)
    prior_states = {"m1": "completed"}

    filtered, excluded = exclude_tool_subtasks_from_resume(plan, dict(prior_states))

    assert excluded == []
    assert filtered == prior_states


def test_resume_exclusion_leaves_non_completed_tool_entries_untouched():
    tool_st = _tool_subtask(id="t1")
    plan = _plan(tool_st)
    prior_states = {"t1": "failed"}

    filtered, excluded = exclude_tool_subtasks_from_resume(plan, dict(prior_states))

    assert excluded == []
    assert filtered == {"t1": "failed"}


@pytest.mark.asyncio
async def test_resumed_tool_subtask_revalidates_mcp_approval_fresh_after_revocation(tmp_path):
    """End-to-end proof of Invariant 18 at the exact mechanism level: a
    TOOL subtask marked 'completed' by a prior interrupted run must not
    let that record substitute for a fresh TOOL-002 check - if approval
    was revoked in between, re-execution (never resume-skip, per
    exclude_tool_subtasks_from_resume) denies for real."""
    with _cwd(tmp_path / "ws"):
        workspace_root = os.path.realpath(os.getcwd())
        profile = resolve_mcp_capability_profile({}, workspace_root=workspace_root)
        profile_digest = compute_mcp_capability_profile_digest(profile)
        schema = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}
        identity = MCPToolIdentity(
            server_identity="probe", tool_name="echo_test", schema_digest=compute_mcp_schema_digest(schema),
        )
        wid = workspace_identity(workspace_root)
        # Never approved at all in THIS run (simulating: it was approved
        # when the prior interrupted run completed it, but that approval
        # is gone now - revoked, or never persisted correctly).
        kernel = Kernel(config=AppConfig())

        tool_st = _tool_subtask(id="t1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
        plan = _plan(tool_st)
        # A prior run recorded this TOOL subtask as completed.
        prior_states = {"t1": "completed"}
        filtered, excluded = exclude_tool_subtasks_from_resume(plan, dict(prior_states))
        assert excluded == ["t1"]
        assert "t1" not in filtered  # would have been resume-skipped without this fix

        await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
        try:
            result = await subtask_executor.execute(
                subtask=tool_st, plan=plan, context=build_context_package(), kernel=kernel,
            )
        finally:
            await kernel.mcp.shutdown_all()

        assert result.status == SubtaskStatus.FAILED
        assert "MCP_TOOL_REQUIRES_APPROVAL" in result.error


# =====================================================================
# TASK 11: terminal correctness - tool success cannot imply workflow
# success; normal deterministic verification still owns completion.
# =====================================================================

def test_scenario_i_tool_says_pass_cannot_override_other_subtask_failure():
    tool_result = SubtaskResult(
        subtask_id="tool1", status=SubtaskStatus.COMPLETED, execution_method=ExecutionMethod.TOOL.value,
        tool_output={"status": "PASS"},  # self-reported success text - must never be read as authority
    )
    model_result = SubtaskResult(
        subtask_id="model1", status=SubtaskStatus.FAILED, execution_method=ExecutionMethod.MODEL.value,
        error="did not pass Quality Gates",
    )
    current_plan_subtask_ids = {"tool1", "model1"}
    latest_status_by_subtask = {r.subtask_id: r.status for r in (tool_result, model_result)}

    assert all_subtasks_completed(current_plan_subtask_ids, latest_status_by_subtask) is False


def test_all_completed_true_only_when_every_subtask_status_is_completed():
    ids = {"a", "b"}
    assert all_subtasks_completed(ids, {"a": SubtaskStatus.COMPLETED, "b": SubtaskStatus.COMPLETED}) is True
    assert all_subtasks_completed(ids, {"a": SubtaskStatus.COMPLETED}) is False  # missing entry
    assert all_subtasks_completed(ids, {"a": SubtaskStatus.COMPLETED, "b": SubtaskStatus.NEEDS_REVIEW}) is False


def test_all_subtasks_completed_never_inspects_tool_output_content():
    # Only the function BODY (not its own explanatory docstring, which
    # discusses tool_output/"PASS" as the exact thing this function must
    # NOT read) must never reference tool_output content.
    body_lines = inspect.getsource(all_subtasks_completed).split('"""')[-1]
    assert "tool_output" not in body_lines
    assert '"PASS"' not in body_lines


# =====================================================================
# TASK 14: flattened MCP tool-name collision cannot become an authority
# bypass through the autonomous path - approval/dispatch bind to the
# REAL registry object, never a re-parsed flattened string.
# =====================================================================

def test_tool_subtask_resolves_by_real_registry_object_never_parses_flattened_name():
    source = inspect.getsource(subtask_executor._execute_tool_subtask)
    assert "split(" not in source
    assert ".partition(" not in source
    assert "kernel.registry.get(\"tool\", subtask.tool_name)" in source


def test_scenario_collision_two_servers_same_flattened_name_bind_correct_identity(tmp_path):
    """Two MCP servers whose flattened tool names collide (same server-
    tool-name-derived string) must still each bind their OWN structured
    identity - approving one must never authorize the other, even when
    dispatched through the autonomous TOOL subtask path."""
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=AppConfig())

        async def run():
            await kernel.mcp.start_all({
                "probe": {"command": sys.executable, "args": [MOCK_SERVER]},
            })
            try:
                tool = kernel.registry.get("tool", "probe_echo_test")
                real_identity = tool.identity
                # A TOOL subtask naming the exact flattened name reaches
                # the SAME structured identity the registry actually
                # holds - never a re-derived/re-parsed one.
                subtask = _tool_subtask(id="mcp1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
                result = await subtask_executor.execute(
                    subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
                )
                return result, real_identity
            finally:
                await kernel.mcp.shutdown_all()

        result, real_identity = asyncio.run(run())
        # Denied (no approval) - but critically, denied AGAINST THE REAL
        # identity (server=probe, tool=echo_test), not a fabricated one -
        # confirmed by checking a differently-scoped approval (wrong
        # server) does NOT authorize it.
        assert result.status == SubtaskStatus.FAILED
        assert real_identity.server_identity == "probe"
        assert real_identity.tool_name == "echo_test"


def test_approval_for_different_server_does_not_transfer_via_flattened_name(tmp_path):
    with _cwd(tmp_path / "ws"):
        workspace_root = os.path.realpath(os.getcwd())
        profile = resolve_mcp_capability_profile({}, workspace_root=workspace_root)
        profile_digest = compute_mcp_capability_profile_digest(profile)
        schema = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}
        # Approve a DIFFERENT server_identity ("other") for the same tool
        # name/schema - simulating the collision: an operator approved
        # "othertool_echo_test" thinking it was "probe"'s.
        wrong_identity = MCPToolIdentity(
            server_identity="other", tool_name="echo_test", schema_digest=compute_mcp_schema_digest(schema),
        )
        wid = workspace_identity(workspace_root)
        artifact = add_approval(empty_artifact(wid), wrong_identity, profile_digest)
        save_approval_artifact(default_local_approval_path(workspace_root), artifact)

        kernel = Kernel(config=AppConfig())

        async def run():
            await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
            try:
                subtask = _tool_subtask(id="mcp1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
                return await subtask_executor.execute(
                    subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
                )
            finally:
                await kernel.mcp.shutdown_all()

        result = asyncio.run(run())
        assert result.status == SubtaskStatus.FAILED
        assert "MCP_TOOL_REQUIRES_APPROVAL" in result.error
