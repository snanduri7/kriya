"""TOOL-002 + TOOL-003 combined adversarial closure.

This file does NOT re-prove properties already established, unchanged, by
existing files - it cites them (see the closure RETURN report) and adds
only the genuinely NEW combined scenarios: cases where TOOL-002's durable
approval layer and TOOL-003's real OCI containment layer interact, which
no single prior test file exercised together.

Existing evidence this closure package relies on without duplicating:
  - tests/test_tool002_mcp_invocation_authority.py::test_no_production_direct_call_tool_bypass
    (structural: exactly one production .call_tool() call site)
  - tests/test_tool002_p2_invocation_approval.py (34 tests: canonicalization,
    persistence, all five drift axes, corrupt/unknown-format/missing/revoked
    fail-closed, SEC-009/capability-authorization non-substitution, resolver
    freshness, structural "static set is test-only")
  - tests/test_tool002_p2_real_cli.py (5 tests: real-CLI A-F lifecycle,
    fresh-process durability, revoke-without-restart, real-Docker Task 14
    decisive combined proof, capability-profile-drift through the real CLI,
    host-mode explicit non-contained labeling)
  - tests/test_tool003_p2_deterministic.py + test_tool003_p2_oci_enforcement.py
    (22 + 19 tests: the full 23-item real-OCI matrix - hostile startup,
    workspace/extra path RO-RW, symlink escape, credential-sentinel
    inaccessibility, EXPLICIT_DESTINATIONS fail-closed, child-process
    containment inheritance, ambient-secret-absent/explicit-present,
    backend-unavailable/unknown-backend fail-closed with zero containers,
    startup/normal/forced-shutdown zero-container cleanup, grandchild
    residue)
  - tests/test_sec003_mcp_env_isolation.py, test_sec004_mcp_lifecycle.py
    (env isolation, lifecycle bounds - unchanged by this closure, re-run
    as part of this session's regression sweep, not re-proven here)

No live LLM anywhere in this file.
"""
import asyncio
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import threading

import pytest

from kriya.config.config import AppConfig, AutonomyConfig, MCPLifecycleConfig
from kriya.control.workspace_identity import workspace_identity
from kriya.core.kernel import Kernel
from kriya.mcp.capability import compute_mcp_capability_profile_digest, resolve_mcp_capability_profile
from kriya.mcp.invocation_approval import (
    add_approval,
    default_local_approval_path,
    empty_artifact,
    load_approval_artifact,
    save_approval_artifact,
)
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, MCPToolIdentity, compute_mcp_schema_digest
from kriya.policy.telemetry import build_decision_record
from kriya.tools.tool import ToolExecutionError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
HOSTILE_STARTUP_IN_CONTAINER = "/kriya/mcp/extra/ro/0/tool003_p2_hostile_startup_fixture.py"
MOCK_SERVER = os.path.join(REPO_ROOT, "tests", "mock_mcp_server.py")

pytestmark_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _docker_reachable() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


DOCKER_OK = shutil.which("docker") is not None and _docker_reachable()


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _leftover_containers() -> list:
    r = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=kriya.mcp-capability-digest", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=15,
    )
    return [n for n in r.stdout.splitlines() if n.strip()]


def _oci_config(**autonomy_kwargs) -> AppConfig:
    return AppConfig(
        autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="oci", **autonomy_kwargs),
        mcp_lifecycle=MCPLifecycleConfig(),
    )


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.setenv("KRIYA_MCP_APPROVAL_HOME", str(tmp_path / "_mcp_approval_home"))


# =====================================================================
# PHASE 3.A + PHASE 4 (combined, real Docker): unapproved + containment
# enabled - server starts under real containment, hostile pre-handshake
# behavior is blocked, invocation is denied with zero tools/call.
# =====================================================================

@pytestmark_docker
@pytest.mark.skipif(not DOCKER_OK, reason="docker daemon not reachable")
def test_unapproved_containment_enabled_starts_contained_blocks_startup_attack_denies_invocation(tmp_path):
    outside_target = tmp_path / "outside_sentinel.txt"
    port = _free_port()
    connections_received = []

    def _listener():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(1)
            srv.settimeout(20)
            try:
                conn, _addr = srv.accept()
                connections_received.append(True)
                conn.close()
            except socket.timeout:
                pass

    t = threading.Thread(target=_listener, daemon=True)
    t.start()

    with _cwd(tmp_path / "ws"):
        # Production default: MCPManager(kernel) with NO explicit
        # execution_policy - the real durable resolver, real capability
        # resolution, real containment decision. NO approval is ever
        # created in this test.
        kernel = Kernel(config=_oci_config())

        async def run():
            await kernel.mcp.start_all({"fixture": {
                "command": "python3", "args": [HOSTILE_STARTUP_IN_CONTAINER],
                "env": {
                    "OUTSIDE_WRITE_TARGET": "/kriya/host_outside_target.txt",
                    "HOST_LOCAL_HOST": "host.docker.internal",
                    "HOST_LOCAL_PORT": str(port),
                },
                "capabilities": {"network": "denied", "additional_read_paths": [TESTS_DIR]},
            }})
            try:
                # Server started successfully under real containment -
                # confirms containment itself never blocks legitimate
                # startup, only the hostile side effects within it.
                client = kernel.mcp.clients["fixture"]
                assert client.containment_required is True
                assert client.containment_active is True

                tool = kernel.registry.get("tool", "fixture_get_startup_evidence")
                with pytest.raises(ToolExecutionError) as exc_info:
                    await tool.execute()
                assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
                assert "MCP_TOOL_REQUIRES_APPROVAL" in str(exc_info.value)
            finally:
                await kernel.mcp.shutdown_all()

        asyncio.run(run())

    t.join(timeout=25)
    assert not outside_target.exists(), "hostile pre-handshake write must never reach a real host path"
    assert connections_received == [], "hostile pre-handshake connect must never reach a real host-local listener"
    assert _leftover_containers() == []


# =====================================================================
# PHASE 3.D: capability profile authorized only (wide-open profile bound
# to a real, running client) still denies invocation - through the real
# production stack (MCPManager + real MCP subprocess), host mode.
# =====================================================================

def test_capability_profile_authorized_only_denies_invocation_real_stack(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=AppConfig())  # containment disabled (default) - host mode

        async def run():
            await kernel.mcp.start_all({"probe": {
                "command": sys.executable, "args": [MOCK_SERVER],
                # Deliberately WIDE capability authority - workspace
                # read+write, unrestricted network. Invariant 5: capability
                # authority must never substitute for invocation approval,
                # no matter how wide.
                "capabilities": {"workspace_read": True, "workspace_write": True, "network": "unrestricted"},
            }})
            try:
                tool = kernel.registry.get("tool", "probe_echo_test")
                assert tool.capability_profile_identity.profile_digest  # real, non-trivial digest bound
                with pytest.raises(ToolExecutionError) as exc_info:
                    await tool.execute(message="hi")
                assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
                assert "MCP_TOOL_REQUIRES_APPROVAL" in str(exc_info.value)
            finally:
                await kernel.mcp.shutdown_all()

        asyncio.run(run())


# =====================================================================
# PHASE 8 (combined, real Docker): approval-store corruption under REAL
# containment must still fail closed - not merely at the isolated
# ExecutionPolicy unit-test level (already proven in
# test_tool002_p2_invocation_approval.py), but through the real
# production stack with a real container already running.
# =====================================================================

@pytestmark_docker
@pytest.mark.skipif(not DOCKER_OK, reason="docker daemon not reachable")
def test_corrupted_approval_store_fails_closed_under_real_containment(tmp_path):
    """A corrupt approval-store file is handled INSIDE
    kriya/mcp/invocation_approval.py's own resolver (`_load_fail_closed()`
    catches `MCPApprovalArtifactError` and returns a plain `False`) - it
    never even needs to reach MCPTool._run()'s outer exception-to-DENY
    wrapper. This test therefore correctly observes the ORDINARY
    MCP_TOOL_REQUIRES_APPROVAL denial, not MCP_POLICY_EVALUATION_FAILED -
    a stronger closure property than "an exception gets caught somewhere":
    corruption degrades gracefully to "no valid approval" at the source,
    with no exception ever raised at all. The genuinely-unhandled-exception
    path (a resolver bug, not an ordinary corrupt file) is exercised
    separately below, combined with real containment for the first time."""
    with _cwd(tmp_path / "ws"):
        workspace_root = os.path.realpath(os.getcwd())
        path = default_local_approval_path(workspace_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json at all")

        kernel = Kernel(config=_oci_config())

        async def run():
            await kernel.mcp.start_all({"fixture": {
                "command": "python3", "args": [HOSTILE_STARTUP_IN_CONTAINER],
                "capabilities": {"additional_read_paths": [TESTS_DIR]},
            }})
            try:
                assert kernel.mcp.clients["fixture"].containment_active is True
                tool = kernel.registry.get("tool", "fixture_get_startup_evidence")
                with pytest.raises(ToolExecutionError) as exc_info:
                    await tool.execute()
                assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
                assert "MCP_TOOL_REQUIRES_APPROVAL" in str(exc_info.value)
            finally:
                await kernel.mcp.shutdown_all()

        asyncio.run(run())

    assert _leftover_containers() == []


@pytestmark_docker
@pytest.mark.skipif(not DOCKER_OK, reason="docker daemon not reachable")
def test_genuinely_unhandled_resolver_exception_fails_closed_under_real_containment(tmp_path):
    """Distinct from the corrupted-file case above: a resolver that raises
    an exception NOT caught by invocation_approval.py's own internal
    handling (simulating a genuine bug, not an ordinary corrupt file) must
    still be denied - via MCPTool._run()'s pre-existing, unmodified
    fail-closed wrapper - even with a real container already running."""
    def _raising_resolver(identity, capability_identity):
        raise RuntimeError("simulated unexpected resolver failure")

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        kernel.mcp._execution_policy = ExecutionPolicy(mcp_invocation_approval_resolver=_raising_resolver)

        async def run():
            await kernel.mcp.start_all({"fixture": {
                "command": "python3", "args": [HOSTILE_STARTUP_IN_CONTAINER],
                "capabilities": {"additional_read_paths": [TESTS_DIR]},
            }})
            try:
                assert kernel.mcp.clients["fixture"].containment_active is True
                tool = kernel.registry.get("tool", "fixture_get_startup_evidence")
                with pytest.raises(ToolExecutionError) as exc_info:
                    await tool.execute()
                assert isinstance(exc_info.value.__cause__, PolicyDeniedError)
                assert "MCP_POLICY_EVALUATION_FAILED" in str(exc_info.value)
            finally:
                await kernel.mcp.shutdown_all()

        asyncio.run(run())

    assert _leftover_containers() == []


# =====================================================================
# PHASE 10: telemetry truthfulness for a representative DENY run (ALLOW
# cases already covered in test_tool002_p2_invocation_approval.py).
# =====================================================================

def test_telemetry_deny_case_still_carries_full_identity_binding():
    identity = MCPToolIdentity(server_identity="serverA", tool_name="toolA", schema_digest=compute_mcp_schema_digest({}))
    request = ActionRequest(action_type=ActionType.MCP_TOOL_CALL, metadata={"mcp_tool_identity": identity})
    result = ExecutionPolicy().evaluate(request)
    record = build_decision_record(request, result, enforced=True)
    assert record.decision == "require_approval"
    assert record.reason_code == "MCP_TOOL_REQUIRES_APPROVAL"
    # Identity is visible in telemetry EVEN ON DENY - an auditor must be
    # able to see WHICH tool was denied, not just that something was.
    assert record.mcp_server_identity == "serverA"
    assert record.mcp_tool_name_summary == "toolA"
    assert record.mcp_schema_digest_short is not None


# =====================================================================
# PHASE 6: revocation operational-gap investigation - a durable approval
# for a server that has become unstartable.
# =====================================================================

def test_revocation_gap_approval_persists_cannot_be_exercised_reactivates_if_identical(tmp_path):
    with _cwd(tmp_path / "ws"):
        workspace_root = os.path.realpath(os.getcwd())
        wid = workspace_identity(workspace_root)

        # A real, valid approval - as if `kriya mcp approve` had run
        # successfully while the server was still startable.
        profile = resolve_mcp_capability_profile({}, workspace_root=workspace_root)
        profile_digest = compute_mcp_capability_profile_digest(profile)
        identity = MCPToolIdentity(server_identity="probe", tool_name="echo_test", schema_digest="x" * 64)
        artifact = add_approval(empty_artifact(wid), identity, profile_digest)
        path = default_local_approval_path(workspace_root)
        save_approval_artifact(path, artifact)

        # Q1: Can approval remain persisted? Yes - nothing about a server
        # becoming unstartable touches this file at all.
        reloaded = load_approval_artifact(path)
        assert reloaded is not None and len(reloaded.records) == 1

        # Q2: Can it be exercised while the server is unstartable? No -
        # invocation approval is only ever CONSULTED from inside
        # _check_mcp_invocation, reached only via MCPTool._run(), reached
        # only via a REGISTERED MCPTool - and a server that never starts
        # is never registered at all (SEC-004 atomicity: start_all() never
        # partially registers a failed server's tools). Proven here with
        # a genuinely broken command (not "server unhealthy", but
        # "server literally cannot start").
        kernel = Kernel(config=AppConfig())

        async def run():
            with pytest.raises(Exception):
                await kernel.mcp.start_all({"probe": {
                    "command": "/nonexistent/definitely-not-a-real-binary", "args": [],
                }})
            assert "probe" not in kernel.mcp.clients
            assert kernel.registry.list_components("tool") == []

        asyncio.run(run())

        # Q3: If the server later becomes startable again with an
        # IDENTICAL full identity (same server/tool/schema/capability
        # profile), does the approval become usable again? Yes - and this
        # is CORRECT anti-drift behavior, not a bug: identical identity
        # means identical authorized entity, so the SAME operator
        # decision still applies. Proven by resolving the durable
        # resolver directly against the persisted artifact.
        from kriya.mcp.invocation_approval import resolve_mcp_invocation_approval
        from kriya.policy.model import MCPCapabilityProfileIdentity

        resolver = resolve_mcp_invocation_approval(workspace_root)
        cap_identity = MCPCapabilityProfileIdentity(server_identity="probe", profile_digest=profile_digest)
        assert resolver(identity, cap_identity) is True

        # Q4: Can the operator remove the approval WITHOUT starting the
        # server, through an existing trusted mechanism? `kriya mcp
        # revoke` itself cannot (it resolves identity via a live registry
        # lookup - the same documented limitation this test investigates).
        # But the store is a single, fully-documented plain JSON file at a
        # known, fixed path (`default_local_approval_path()` /
        # `KRIYA_MCP_APPROVAL_HOME`) that the operator already has
        # filesystem authority over (it is written to their own home
        # directory, never anywhere Kriya-privileged) - deleting or
        # editing that file directly is a real, available, if manual,
        # trusted removal path. Proven here: the file is a plain,
        # human-readable JSON document the operator can act on directly.
        os.remove(path)
        assert load_approval_artifact(path) is None


# =====================================================================
# PHASE 11: no repository-controlled durable approval source - structural
# confirmation that `kriya mcp approve`/`revoke` accept no path/config
# override at all (unlike `kriya authority approve --out`, which
# deliberately DOES allow an operator-supplied path, itself still
# validated outside the workspace).
# =====================================================================

def test_mcp_approve_revoke_cli_commands_accept_no_path_override():
    import inspect

    import kriya.cli as cli_module

    approve_params = set(inspect.signature(cli_module.mcp_approve.callback).parameters)
    revoke_params = set(inspect.signature(cli_module.mcp_revoke.callback).parameters)
    assert approve_params == {"ctx", "tool_name", "confirm"}
    assert revoke_params == {"ctx", "tool_name"}
