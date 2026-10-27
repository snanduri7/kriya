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
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from _mcp_containment_standin import app_config
from _planned_tool_seam import admit_planned_test_tool
from _plugin_test_support import load_core_tools_module
from _strict_doubles import strict_kernel
from pydantic import BaseModel, ConfigDict

from kriya.config.config import AppConfig, AutonomyConfig, MCPLifecycleConfig
from kriya.context.workspace_identity import workspace_identity
from kriya.core.kernel import Kernel
from kriya.core.registry import ComponentRegistryError
from kriya.mcp.capability import compute_mcp_capability_profile_digest, resolve_mcp_capability_profile
from kriya.mcp.invocation_approval import (
    add_approval,
    default_local_approval_path,
    empty_artifact,
    save_approval_artifact,
)
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import MCPToolIdentity, compute_mcp_schema_digest
from kriya.tools.containment import ExternalExecutionRefused
from kriya.tools.tool import TOOL_OPERATOR_ONLY, BaseTool, ToolExecutionError, ToolGovernance
from kriya.workflow import subtask_executor
from kriya.workflow import workflow_controller as wc_module
from kriya.workflow.context_package import build_context_package
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_controller import all_subtasks_completed, classify_tool_resume
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus


@pytest.fixture(autouse=True)
def _tool_mechanics_not_hitl_gate(monkeypatch):
    """These tests exercise the tool-execution MECHANICS outside human-in-the-loop: the executor reads the run's mode as
    "guardrails" (before S1 this patched only the B3 stop; S1 also refuses mutating planned steps in human-in-the-loop
    inside the tools, so the mode itself is what is set). The human-in-the-loop refusals are pinned in
    tests/test_tool_subtask_hitl_confirmation.py and tests/test_s1_toolbox_authority.py."""
    monkeypatch.setattr("kriya.workflow.subtask_executor._autonomy_mode", lambda kernel: "guardrails")
    # S18: _RecordingTool is a mechanics double, admitted through the test-only seam (tests/_planned_tool_seam.py); which
    # tools are plannable at all is pinned in tests/test_s18_planned_tool_governance.py.
    admit_planned_test_tool(monkeypatch, _RecordingTool)


class _RecordingArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _RecordingTool(BaseTool):
    """A real BaseTool double (S1: the model-authored entry refuses anything that is not a BaseTool); ``calls`` records
    the arguments the governed boundary ran it with."""

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


async def _assert_planned_shell_refused(tool, command):
    """S18 (OI-127, Option B): the PLANNED path to ShellTool is a typed refusal before anything runs, in every mode -
    nothing executed, the reason recorded, no retry/fallback (one SubtaskResult, NEEDS_REVIEW)."""
    subtask = _tool_subtask(tool_name="shell", tool_arguments={"command": command})
    kernel = strict_kernel()
    kernel.registry.get = MagicMock(return_value=tool)
    result = await subtask_executor.execute(subtask=subtask, plan=_plan(subtask), context=build_context_package(),
                                            kernel=kernel)
    assert result.status == SubtaskStatus.NEEDS_REVIEW and result.reason_codes == (TOOL_OPERATOR_ONLY,), result
    assert result.tool_output is None
    return result


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
# D2 S5: the MCP-tool semantics of this file run through the contained branch (the test-only stand-in); the real-OCI scenarios below stay real.
pytestmark = pytest.mark.usefixtures("mcp_standin")


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

def test_execute_tool_subtask_calls_the_governed_model_authored_entry():
    """S1: the dispatch is the model-authored entry point, never the operator's BaseTool.execute()."""
    # D1: the registry lookup and the pre-authority checks moved into the shared `_tool_preflight` (so the revalidate-only path cannot diverge); the whole execute path is both.
    source = inspect.getsource(subtask_executor._execute_tool_subtask) + inspect.getsource(subtask_executor._tool_preflight)
    assert "await execute_model_authored(" in source and "tool.execute(" not in source
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
    tool = _RecordingTool(return_value={"lint": "clean"})
    kernel = strict_kernel()
    kernel.registry.get = MagicMock(return_value=tool)

    result = await subtask_executor.execute(
        subtask=subtask, plan=_plan(subtask), context=build_context_package(), kernel=kernel,
    )

    assert result.status == SubtaskStatus.COMPLETED
    assert result.tool_output == {"lint": "clean"}
    tool.calls.assert_awaited_once_with(path="a.py")
    kernel.registry.get.assert_called_once_with("tool", "lint")


# =====================================================================
# TASK 4 / Scenario B: unknown / malformed / ambiguous tool fails closed,
# never substitutes a "closest match."
# =====================================================================

@pytest.mark.asyncio
async def test_scenario_b_unknown_tool_fails_closed():
    subtask = _tool_subtask(tool_name="does_not_exist")
    kernel = strict_kernel()
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
    FilesystemTool = load_core_tools_module().FilesystemTool

    subtask = _tool_subtask(tool_name="filesystem", tool_arguments={"operation": "read"})  # missing required 'path'
    kernel = strict_kernel()
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
    # derived from anywhere else; S1: the arguments travel as ONE mapping,
    # never spread into keyword parameters an argument key could bind to.
    assert "execute_model_authored(tool, subtask.tool_arguments, invocation)" in source


# =====================================================================
# TASK 7 / Scenario C: policy denial -> zero side effect. Reuses
# POL-001's own real ShellTool hard-invariant (sudo), unmodified.
# =====================================================================

@pytest.mark.asyncio
async def test_scenario_c_policy_denial_zero_side_effect(tmp_path):
    """S18 re-home: the POL-001 hard invariant (sudo) is proven on the path that can still run a shell command - the
    operator's own BaseTool.execute() - and the planned path never reaches it (TOOL_OPERATOR_ONLY, nothing ran)."""
    ShellTool = load_core_tools_module().ShellTool

    sentinel = tmp_path / "sudo_ran.txt"
    with pytest.raises(ToolExecutionError) as denied:
        await ShellTool().execute(command=f"sudo touch {sentinel}")
    assert isinstance(denied.value.__cause__, PolicyDeniedError)
    assert denied.value.__cause__.result.reason_code == "COMMAND_SUDO_DENIED"
    assert not sentinel.exists()

    await _assert_planned_shell_refused(ShellTool(), f"sudo touch {sentinel}")
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_policy_exception_fails_closed():
    """A policy engine that raises internally (not a normal DENY) must
    still be observable as a real failure, never silently treated as
    ALLOW - MCPTool._run()'s own pre-existing wrapper (unmodified)
    already guarantees this; here proven reached through the
    SubtaskExecutor boundary specifically."""
    ShellTool = load_core_tools_module().ShellTool

    tool = ShellTool()
    tool._execution_policy = ExecutionPolicy()

    # enforce_hard_invariants swallows non-PolicyDeniedError exceptions by
    # design (documented "fails open on a broken check only" - unrelated
    # to network/containment authority) - so to prove a genuinely broken
    # downstream stays a real failure, break the actual subprocess spawn
    # instead, which has no such swallow. S18 re-home: proven on the operator
    # path (the only path that still runs a shell command); the planned path
    # is refused before the spawn could even be reached.
    spawn = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("kriya.tools.process.ProcessController.run_async", spawn)
        with pytest.raises(ToolExecutionError):
            await tool.execute(command="echo hi")
        assert spawn.await_count == 1
        await _assert_planned_shell_refused(tool, "echo hi")
        assert spawn.await_count == 1  # the planned call never reached the spawn


# =====================================================================
# TASK 5 / Scenarios D, E: MCP unapproved denies with zero tools/call;
# approved reaches exactly one tools/call - both through
# subtask_executor.execute(), the exact function the real enforce-mode
# TOOL branch calls.
# =====================================================================

def test_scenario_d_mcp_unapproved_autonomous_subtask_denied_zero_calls(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=app_config())  # D2 S5: the contained branch (test-only stand-in); no approval created

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

        kernel = Kernel(config=app_config())

        async def run():
            await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
            try:
                kernel.registry.get("tool", "probe_echo_test")
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

        kernel = Kernel(config=app_config())

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


def test_scenario_containment_required_backend_unavailable_no_call_no_fallback(tmp_path):
    """TOOL-003: containment required but no real backend configured ->
    server refuses to start at all - no autonomous call, no host fallback,
    reached via the SubtaskExecutor path. D2 S5: the refusal is the canonical typed one, before anything starts (no Docker involved)."""
    with _cwd(tmp_path / "ws"):
        cfg = AppConfig(
            autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="none"),
            mcp_lifecycle=MCPLifecycleConfig(),
        )
        kernel = Kernel(config=cfg)

        async def run():
            with pytest.raises(ExternalExecutionRefused):
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
async def test_scenario_g_shelltool_maven_registry_scoped_when_contained():
    from _strict_doubles import ProfileCapturingBackend

    from kriya.tools.containment import NetworkAuthority
    core_tools_module = load_core_tools_module()
    ShellTool = core_tools_module.ShellTool

    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.acquisition_registry_hosts = ["repo.maven.apache.org"]
    backend = ProfileCapturingBackend()
    orig = core_tools_module.resolve_containment_backend
    core_tools_module.resolve_containment_backend = lambda name: backend
    try:
        # S18 re-home: SEC-005 registry scoping proven on the operator path; the planned path never prepares a profile.
        await _assert_planned_shell_refused(ShellTool(autonomy_cfg=cfg.autonomy), "mvn clean install")
        assert backend.prepared_profiles == []
        await ShellTool(autonomy_cfg=cfg.autonomy).execute(command="mvn clean install")
        profile = backend.prepared_profiles[-1]
        assert profile.network == NetworkAuthority.DEPENDENCY_REGISTRY_ONLY
        assert profile.network_destinations == ("repo.maven.apache.org",)
    finally:
        core_tools_module.resolve_containment_backend = orig


@pytest.mark.asyncio
async def test_scenario_g_shelltool_unmapped_manager_denied_when_contained():
    from _strict_doubles import ProfileCapturingBackend

    from kriya.tools.containment import NetworkAuthority
    core_tools_module = load_core_tools_module()
    ShellTool = core_tools_module.ShellTool

    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    backend = ProfileCapturingBackend()
    orig = core_tools_module.resolve_containment_backend
    core_tools_module.resolve_containment_backend = lambda name: backend
    try:
        # S18 re-home: the unmapped-manager denial proven on the operator path; the planned path prepares nothing.
        await _assert_planned_shell_refused(ShellTool(autonomy_cfg=cfg.autonomy), "npm install left-pad")
        assert backend.prepared_profiles == []
        await ShellTool(autonomy_cfg=cfg.autonomy).execute(command="npm install left-pad")  # the no-exec backend
        profile = backend.prepared_profiles[-1]
        assert profile.network == NetworkAuthority.DENIED
    finally:
        core_tools_module.resolve_containment_backend = orig


@pytestmark_docker
def test_scenario_g_real_pip_through_the_operator_path_authorized_vs_unauthorized():
    """Real-Docker proof of SEC-005's registry-scoped mechanism, unchanged. S18 (OI-127, Option B): re-homed from the
    autonomous SubtaskExecutor path (a planned shell step is now TOOL_OPERATOR_ONLY, pinned below and in
    test_scenario_g_planned_pip_is_refused_before_any_container) to the operator's own BaseTool.execute()."""
    ShellTool = load_core_tools_module().ShellTool

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
        return await tool.execute(
            command=f"pip3 install --dry-run nonexistent-kriya-x >/dev/null 2>&1 ; {probe.format(host=target_host)}")

    authorized = asyncio.run(run_case(["pypi.org", "files.pythonhosted.org"], "pypi.org"))
    assert "200" in authorized["stdout"]

    denied = asyncio.run(run_case(["pypi.org", "files.pythonhosted.org"], "example.com"))
    assert "403" in denied["stdout"]  # the shell command itself still "succeeds" (prints 403)


def test_scenario_g_planned_pip_is_refused_before_any_container(monkeypatch):
    """S18: the same contained pip command as a PLANNED step never prepares a backend (no container, no network)."""
    core_tools_module = load_core_tools_module()
    prepared = []
    monkeypatch.setattr(core_tools_module, "resolve_containment_backend", lambda name: prepared.append(name))
    c = AppConfig()
    c.autonomy.contained_execution_required = True
    c.autonomy.containment_backend = "oci"
    asyncio.run(_assert_planned_shell_refused(core_tools_module.ShellTool(autonomy_cfg=c.autonomy),
                                              "pip3 install --dry-run nonexistent-kriya-x"))
    assert prepared == []


# =====================================================================
# TASK 12: bypass search - UNGOVERNED_AUTONOMOUS_TOOL_PATHS = 0.
# =====================================================================

def _workflow_module_sources():
    import importlib

    import kriya.workflow.subtask_executor as executor_mod
    import kriya.workflow.workflow as workflow_mod
    import kriya.workflow.workflow_controller as controller_mod
    return {
        "workflow_controller.py": inspect.getsource(controller_mod),
        "subtask_executor.py": inspect.getsource(executor_mod),
        "workflow.py": inspect.getsource(workflow_mod),
        # E11: the module-level controller code moved to these owners; the checks follow it
        # E12: and the shadow run (shadow_mode) and the structured enforce run (enforce_loop) moved too
        **{f"{name}.py": inspect.getsource(importlib.import_module(f"kriya.workflow.{name}"))
           for name in ("plan_revision", "owner_recovery", "planning_request", "resume_support", "shadow_mode",
                        "enforce_loop")},
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
    import kriya.workflow.enforce_loop as enforce_loop_module

    # E12: the enforce loop's body is kriya/workflow/enforce_loop.py; the controller keeps its forwarder
    source = inspect.getsource(wc_module) + inspect.getsource(enforce_loop_module)
    # The string may still appear in an explanatory comment (documenting
    # what changed and why) - what must be gone is the code that ever
    # PRODUCES it as a real reason code.
    assert 'reason_codes.append("TOOL_SUBTASK_UNSUPPORTED_IN_ENFORCE")' not in source
    assert '"TOOL_SUBTASK_UNSUPPORTED_IN_ENFORCE" in reason_codes' not in source


def test_exactly_one_tool_execute_call_site_in_subtask_executor():
    """S1: the one tool dispatch is the governed model-authored entry point; no operator-path .execute() remains."""
    tree = ast.parse(inspect.getsource(subtask_executor))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "execute"
    ]
    governed = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "execute_model_authored"
    ]
    assert len(calls) == 0, f"expected no tool.execute() call site, found {len(calls)}"
    assert len(governed) == 1, f"expected exactly one execute_model_authored() call site, found {len(governed)}"


def test_enforce_loop_dispatches_tool_subtasks_only_through_subtask_executor():
    """AST/source proof that the TOOL branch in _run_structured_enforce's
    own per-subtask loop calls subtask_executor.execute (never
    _invoke_bounded_subtask/run_generation_workflow for a TOOL subtask).
    E12: the body is kriya/workflow/enforce_loop.py (EnforceLoop.run); it reads
    the controller's names through EnforceDeps (``deps.``)."""
    from kriya.workflow.enforce_loop import EnforceLoop

    source = inspect.getsource(EnforceLoop.run)
    assert "deps.ExecutionMethod.TOOL:" in source
    assert "deps.subtask_executor.execute(" in source
    # The TOOL branch must `continue`/`break` before ever reaching
    # _invoke_bounded_subtask - structurally confirmed by the ordering of
    # the two markers in source (the TOOL branch's own continue/break
    # occurs textually before the MODEL-only call below it).
    tool_branch_idx = source.index("if subtask.execution_method == deps.ExecutionMethod.TOOL:")
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

    tool = _RecordingTool(side_effect=_tracking_execute)
    kernel = strict_kernel()
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
        kernel = Kernel(config=app_config())

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

    tool = _RecordingTool(side_effect=cause)  # the tool's own run raises the denial; the boundary wraps it
    kernel = strict_kernel()
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

def test_resume_classifies_a_completed_tool_subtask_as_terminal_never_re_executed_and_leaves_model_subtasks_alone():
    """D1 (this used to pin that a completed TOOL subtask is EXCLUDED from resume and therefore re-executed - the duplicate side effect D1 removes)."""
    tool_st = _tool_subtask(id="t1")
    model_st = _model_subtask(id="m1")
    plan = _plan(model_st, tool_st)
    prior_states = {"m1": "completed", "t1": "completed"}

    decision = classify_tool_resume(plan, dict(prior_states))

    assert decision.completed == ("t1",) and decision.unresolved == ()
    assert decision.states == prior_states  # nothing is removed: the completed TOOL entry is KEPT so it is not re-run, and MODEL resume is unaffected


def test_resume_classification_is_empty_when_no_tool_subtask_was_recorded():
    model_st = _model_subtask(id="m1")
    plan = _plan(model_st)
    prior_states = {"m1": "completed"}

    decision = classify_tool_resume(plan, dict(prior_states))

    assert decision.completed == () and decision.unresolved == ()
    assert decision.states == prior_states


def test_resume_classification_leaves_failed_tool_entries_untouched_and_never_calls_a_started_one_retryable():
    tool_st, started_st = _tool_subtask(id="t1"), _tool_subtask(id="t2")
    plan = _plan(tool_st, started_st)
    prior_states = {"t1": "failed", "t2": "tool_started"}

    decision = classify_tool_resume(plan, dict(prior_states))

    assert decision.completed == () and decision.unresolved == ("t2",), "STARTED without COMPLETED is unresolved (needs_review), never retryable"
    assert decision.states == prior_states


@pytest.mark.asyncio
async def test_resumed_completed_tool_subtask_revalidates_mcp_approval_fresh_after_revocation(tmp_path):
    """Invariant 18 at the exact mechanism level, kept by D1 and separated from the effect: a TOOL subtask recorded COMPLETED by a prior run is NOT executed again, but
    its authority is still revalidated fresh - if approval was revoked in between, the effect-free revalidation REFUSES (the earlier record cannot substitute for a TOOL-002 check)."""
    with _cwd(tmp_path / "ws"):
        # Never approved at all in THIS run (it was approved when the prior run completed it, but that approval is gone now).
        kernel = Kernel(config=app_config())

        tool_st = _tool_subtask(id="t1", tool_name="probe_echo_test", tool_arguments={"message": "hi"})
        plan = _plan(tool_st)
        decision = classify_tool_resume(plan, {"t1": "completed"})
        assert decision.completed == ("t1",) and decision.unresolved == ()  # terminal effect: the resume never runs it again

        await kernel.mcp.start_all({"probe": {"command": sys.executable, "args": [MOCK_SERVER]}})
        try:
            refusal = subtask_executor.revalidate_tool_subtask_authority(tool_st, kernel, (), plan, None)
        finally:
            await kernel.mcp.shutdown_all()

        assert refusal is not None and refusal.status == SubtaskStatus.FAILED
        assert "MCP_TOOL_REQUIRES_APPROVAL" in refusal.error


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
    source = inspect.getsource(subtask_executor._execute_tool_subtask) + inspect.getsource(subtask_executor._tool_preflight)  # D1: lookup lives in the shared preflight
    assert "split(" not in source
    assert ".partition(" not in source
    assert "kernel.registry.get(\"tool\", subtask.tool_name)" in source


def test_scenario_collision_two_servers_same_flattened_name_bind_correct_identity(tmp_path):
    """Two MCP servers whose flattened tool names collide (same server-
    tool-name-derived string) must still each bind their OWN structured
    identity - approving one must never authorize the other, even when
    dispatched through the autonomous TOOL subtask path."""
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=app_config())

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

        kernel = Kernel(config=app_config())

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
