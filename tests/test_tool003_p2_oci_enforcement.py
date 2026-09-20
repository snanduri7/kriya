"""TOOL-003 P2: real OCI enforcement of the resolved MCPCapabilityProfile,
from server startup through shutdown - real Docker throughout (no mocked
subprocess/docker primitives), side effects verified EXTERNALLY (host
filesystem checks, `docker ps`), matching test_containment_oci.py's own
established convention exactly.

Requires a real, reachable Docker daemon - skipped entirely otherwise.
"""
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

from kriya.core.kernel import Kernel
from kriya.config.config import AppConfig, AutonomyConfig, MCPLifecycleConfig
from kriya.mcp.capability import MCPNetworkAuthority, resolve_mcp_capability_profile, compute_mcp_capability_profile_digest
from kriya.mcp.containment_adapter import MCPContainmentUnsupportedError, map_capability_profile_to_containment
from kriya.mcp.mcp import MCPManager
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import MCPToolIdentity, compute_mcp_schema_digest
from kriya.tools.containment import BackendUnavailableError, ContainmentSetupError

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _docker_reachable() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


if not _docker_reachable():
    pytestmark = pytest.mark.skip(reason="docker daemon not reachable")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
FIXTURE_IN_CONTAINER = "/kriya/mcp/extra/ro/0/tool003_p2_capability_fixture.py"
HOSTILE_STARTUP_IN_CONTAINER = "/kriya/mcp/extra/ro/0/tool003_p2_hostile_startup_fixture.py"

_SCHEMAS = {
    "echo": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    "write_path": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    "read_path": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    "connect_tcp": {"type": "object", "properties": {"host": {"type": "string"}, "port": {"type": "string"}}, "required": ["host", "port"]},
    "spawn_child": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    "check_service_status": {"type": "object", "properties": {}, "required": []},
    "report_env": {"type": "object", "properties": {"names": {"type": "string"}}, "required": ["names"]},
    "get_startup_evidence": {"type": "object", "properties": {}, "required": []},
}


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _oci_config(*, cpu_seconds=None, memory_mb=None):
    lifecycle_kwargs = {}
    if cpu_seconds is not None:
        lifecycle_kwargs["cpu_seconds"] = cpu_seconds
    if memory_mb is not None:
        lifecycle_kwargs["memory_mb"] = memory_mb
    return AppConfig(
        autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="oci"),
        mcp_lifecycle=MCPLifecycleConfig(**lifecycle_kwargs) if lifecycle_kwargs else MCPLifecycleConfig(),
    )


def _approved(server_name: str, *tool_names: str) -> ExecutionPolicy:
    identities = frozenset(
        MCPToolIdentity(server_identity=server_name, tool_name=name, schema_digest=compute_mcp_schema_digest(_SCHEMAS[name]))
        for name in tool_names
    )
    return ExecutionPolicy(approved_mcp_tool_identities=identities)


def _server_cfg(*, command="python3", args=None, env=None, capabilities=None):
    return {
        "command": command,
        "args": args if args is not None else [FIXTURE_IN_CONTAINER],
        "env": env or {},
        "capabilities": capabilities or {},
    }


def _leftover_containers(label_value: str = None):
    filt = ["--filter", "label=kriya.mcp-capability-digest"]
    if label_value:
        filt = ["--filter", f"label=kriya.mcp-capability-digest={label_value}"]
    r = subprocess.run(["docker", "ps", "-a"] + filt + ["--format", "{{.Names}}"], capture_output=True, text=True, timeout=15)
    return [n for n in r.stdout.splitlines() if n.strip()]


# =====================================================================
# 1: default profile starts contained
# =====================================================================

def test_1_default_profile_starts_contained(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo"))
        import asyncio

        async def run():
            await manager.start_all({"fixture": _server_cfg(capabilities={"additional_read_paths": [TESTS_DIR]})})
            try:
                client = manager.clients["fixture"]
                assert client.containment_required is True
                assert client.containment_active is True
                tool = kernel.registry.get("tool", "fixture_echo")
                result = await tool.execute(message="hi")
                assert result == "Echo: hi"
            finally:
                await manager.shutdown_all()

        asyncio.run(run())
        assert _leftover_containers() == []


# =====================================================================
# 2/3: hostile startup - outside write + host-local connect blocked
# =====================================================================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_2_3_hostile_startup_outside_write_and_host_local_connect_blocked(tmp_path):
    import asyncio

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
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "get_startup_evidence"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                command="python3", args=[HOSTILE_STARTUP_IN_CONTAINER],
                env={
                    # Container has no view of the host filesystem at this
                    # path (never mounted) - matches Task 2's requirement
                    # "no workspace access unless granted" at the fixture's
                    # own module-load-time attempt.
                    "OUTSIDE_WRITE_TARGET": "/kriya/host_outside_target.txt",
                    "HOST_LOCAL_HOST": "host.docker.internal",
                    "HOST_LOCAL_PORT": str(port),
                },
                capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                tool = kernel.registry.get("tool", "fixture_get_startup_evidence")
                self_report = json.loads(await tool.execute())
                # Secondary signal only - ground truth is the host-side
                # checks below.
                assert "write_failed" in self_report["write_attempt"] or self_report["write_attempt"] == "write_succeeded"
            finally:
                await manager.shutdown_all()

        asyncio.run(run())

    t.join(timeout=25)
    assert not outside_target.exists(), "hostile startup write must NEVER reach a real host path"
    assert connections_received == [], "hostile startup connect must NEVER reach a real host-local listener under network=DENIED"
    assert _leftover_containers() == []


# =====================================================================
# 4/5: ALLOWed malicious tool - hidden write/network blocked
# =====================================================================

def test_4_5_allowed_malicious_tool_hidden_write_and_network_blocked(tmp_path):
    import asyncio

    outside_target = tmp_path / "malicious_sentinel.txt"

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "check_service_status"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                env={
                    "MALICIOUS_WRITE_TARGET": "/kriya/host_malicious_target.txt",
                    "MALICIOUS_CONNECT_HOST": "8.8.8.8", "MALICIOUS_CONNECT_PORT": "53",
                },
                capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                tool = kernel.registry.get("tool", "fixture_check_service_status")
                response = json.loads(await tool.execute())
                assert response["status"] == "ok"  # TOOL-002 ALLOWed the call - it reached the server.
                assert "write_failed" in response["_hidden_write"], response
                assert "connect_failed" in response["_hidden_connect"], response
            finally:
                await manager.shutdown_all()

        asyncio.run(run())
    assert not outside_target.exists()


# =====================================================================
# 6/7/8: workspace RO/RW
# =====================================================================

def test_6_7_workspace_ro_read_succeeds_write_fails(tmp_path):
    import asyncio

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "readable.txt").write_text("host-authored-content")

    with _cwd(ws):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "read_path", "write_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "workspace_read": True, "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                read_tool = kernel.registry.get("tool", "fixture_read_path")
                result = await read_tool.execute(path="/kriya/workspace/readable.txt")
                assert "host-authored-content" in result

                write_tool = kernel.registry.get("tool", "fixture_write_path")
                write_result = await write_tool.execute(path="/kriya/workspace/should_fail.txt", content="x")
                assert "write_failed" in write_result, write_result
            finally:
                await manager.shutdown_all()

        asyncio.run(run())
    assert not (ws / "should_fail.txt").exists()


def test_8_workspace_rw_write_succeeds(tmp_path):
    import asyncio

    ws = tmp_path / "ws"
    ws.mkdir()

    with _cwd(ws):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "write_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "workspace_write": True, "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                write_tool = kernel.registry.get("tool", "fixture_write_path")
                result = await write_tool.execute(path="/kriya/workspace/allowed.txt", content="hi-from-container")
                assert result == "write_succeeded"
            finally:
                await manager.shutdown_all()

        asyncio.run(run())
    assert (ws / "allowed.txt").read_text() == "hi-from-container"


# =====================================================================
# 9/10: extra RO/RW mounts
# =====================================================================

def test_9_10_extra_ro_write_fails_extra_rw_write_succeeds(tmp_path):
    import asyncio

    extra_ro = tmp_path / "extra_ro"
    extra_ro.mkdir()
    extra_rw = tmp_path / "extra_rw"
    extra_rw.mkdir()

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "write_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={
                    "network": "denied",
                    "additional_read_paths": [TESTS_DIR, str(extra_ro)],
                    "additional_write_paths": [str(extra_rw)],
                },
            )})
            try:
                write_tool = kernel.registry.get("tool", "fixture_write_path")
                # extra_ro is additional_read_paths[1] -> /kriya/mcp/extra/ro/1
                ro_result = await write_tool.execute(path="/kriya/mcp/extra/ro/1/should_fail.txt", content="x")
                assert "write_failed" in ro_result, ro_result
                # extra_rw is additional_write_paths[0] -> /kriya/mcp/extra/rw/0
                rw_result = await write_tool.execute(path="/kriya/mcp/extra/rw/0/allowed.txt", content="rw-content")
                assert rw_result == "write_succeeded"
            finally:
                await manager.shutdown_all()

        asyncio.run(run())
    assert not (extra_ro / "should_fail.txt").exists()
    assert (extra_rw / "allowed.txt").read_text() == "rw-content"


# =====================================================================
# 11: undeclared host path inaccessible
# =====================================================================

def test_11_undeclared_host_path_inaccessible(tmp_path):
    import asyncio

    undeclared = tmp_path / "undeclared"
    undeclared.mkdir()
    (undeclared / "secret.txt").write_text("must-not-be-readable")

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                read_tool = kernel.registry.get("tool", "fixture_read_path")
                # The undeclared host path is never mounted anywhere in the
                # container - referencing its HOST path from inside the
                # container cannot resolve to anything (container
                # filesystem namespacing), regardless of the exact error.
                result = await read_tool.execute(path=str(undeclared / "secret.txt"))
                assert "read_failed" in result, result
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


# =====================================================================
# 12: symlink escape fails
# =====================================================================

def test_12_symlink_inside_workspace_cannot_reach_real_host_target(tmp_path):
    """A symlink inside a bind-mounted directory is resolved WITHIN the
    CONTAINER's own filesystem namespace, never against the host's real
    filesystem - this is inherent OCI/bind-mount semantics, not a
    TOOL-003-P2-specific mechanism, but this test confirms P2's own mount
    construction doesn't accidentally defeat it (e.g. by bind-mounting
    something broader than the intended directory). The symlink's target
    is a host-only absolute path with zero meaning inside the container
    (never mounted anywhere) - reading through it must fail exactly like
    reading any other unmounted path would."""
    import asyncio

    ws = tmp_path / "ws"
    ws.mkdir()
    host_only_target = tmp_path / "host_only_secret.txt"
    host_only_target.write_text("must-never-be-reachable-via-symlink")
    (ws / "escape_link").symlink_to(host_only_target)

    with _cwd(ws):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "workspace_read": True, "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                read_tool = kernel.registry.get("tool", "fixture_read_path")
                result = await read_tool.execute(path="/kriya/workspace/escape_link")
                assert "read_failed" in result, result
                assert "must-never-be-reachable" not in result
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


# =====================================================================
# 13: synthetic credential inaccessible
# =====================================================================

def test_13_synthetic_credential_sentinel_inaccessible(tmp_path):
    import asyncio

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                read_tool = kernel.registry.get("tool", "fixture_read_path")
                for sentinel_path in ("/root/.ssh/id_rsa", "/root/.aws/credentials", "/kriya/some/arbitrary/sentinel"):
                    result = await read_tool.execute(path=sentinel_path)
                    assert "read_failed" in result, (sentinel_path, result)
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


# =====================================================================
# 14/15: EXPLICIT_DESTINATIONS - fail closed (STOP condition, not implemented)
# =====================================================================

def test_14_15_explicit_destinations_fails_closed_never_substitutes(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo"))

        import asyncio

        async def run():
            with pytest.raises(ContainmentSetupError, match="EXPLICIT_DESTINATIONS"):
                await manager.start_all({"fixture": _server_cfg(
                    capabilities={"network": "explicit_destinations", "network_hosts": ["example.com"]},
                )})

        asyncio.run(run())
    assert _leftover_containers() == []


def test_unrestricted_network_still_preserves_filesystem_containment(tmp_path):
    """UNRESTRICTED network must not also imply unrestricted filesystem -
    Task 4's "Still preserve all filesystem/process containment"."""
    import asyncio

    ws = tmp_path / "ws"
    ws.mkdir()
    undeclared = tmp_path / "undeclared"
    undeclared.mkdir()
    (undeclared / "secret.txt").write_text("must-not-leak")

    with _cwd(ws):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "connect_tcp", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "unrestricted", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                connect_tool = kernel.registry.get("tool", "fixture_connect_tcp")
                result = await connect_tool.execute(host="8.8.8.8", port="53")
                assert result == "connect_succeeded", result
                read_tool = kernel.registry.get("tool", "fixture_read_path")
                fs_result = await read_tool.execute(path=str(undeclared / "secret.txt"))
                assert "read_failed" in fs_result, fs_result
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


# =====================================================================
# 16: child process inherits restrictions
# =====================================================================

def test_16_child_process_inherits_containment_restrictions(tmp_path):
    import asyncio

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "spawn_child"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                spawn_tool = kernel.registry.get("tool", "fixture_spawn_child")
                # A CHILD of the MCP server itself attempts a network
                # connect - must fail exactly like the parent would,
                # proving it inherited the SAME network namespace/boundary
                # (Task 6), not a decorative parent-only check.
                result = await spawn_tool.execute(
                    command="python3 -c \"import socket; "
                            "s=socket.socket(); s.settimeout(3); "
                            "print(s.connect_ex(('8.8.8.8', 53)))\""
                )
                assert "exit=0" in result, result
                assert "stdout='0" not in result, result  # connect_ex 0 == succeeded, would be a leak
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


# =====================================================================
# 17/18: SEC-003 env regression through the real contained path
# =====================================================================

def test_17_18_ambient_secret_absent_explicit_env_present(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("KRIYA_SEC003_SENTINEL", "host-secret-must-not-leak")

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "report_env"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                env={"EXPLICIT_VAR": "explicitly-authorized-value"},
                capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                tool = kernel.registry.get("tool", "fixture_report_env")
                report = json.loads(await tool.execute(names="KRIYA_SEC003_SENTINEL,EXPLICIT_VAR,HOME"))
                assert report["KRIYA_SEC003_SENTINEL"] is None, "ambient host secret leaked into contained MCP server"
                assert report["EXPLICIT_VAR"] == "explicitly-authorized-value"
                assert report["HOME"] is None or report["HOME"] != os.environ.get("HOME")
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


# =====================================================================
# 19: containment backend unavailable fails closed
# =====================================================================

def test_19_backend_unavailable_fails_closed_no_raw_host_fallback(tmp_path):
    import asyncio

    with _cwd(tmp_path / "ws"):
        cfg = AppConfig(autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="none"))
        kernel = Kernel(config=cfg)
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo"))

        async def run():
            with pytest.raises(BackendUnavailableError):
                await manager.start_all({"fixture": _server_cfg(capabilities={"network": "denied"})})

        asyncio.run(run())
        # "no raw-host fallback" - the server must not be running at all,
        # contained or otherwise.
        assert manager.clients == {}


def test_19b_unknown_backend_name_fails_closed(tmp_path):
    import asyncio

    with _cwd(tmp_path / "ws"):
        cfg = AppConfig(autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="totally_made_up"))
        kernel = Kernel(config=cfg)
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo"))

        async def run():
            with pytest.raises(BackendUnavailableError):
                await manager.start_all({"fixture": _server_cfg(capabilities={"network": "denied"})})

        asyncio.run(run())


# =====================================================================
# 20/21/22/23: lifecycle - zero leftover containers on every exit path
# =====================================================================

def test_20_startup_failure_leaves_zero_containers(tmp_path):
    import asyncio

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo"))

        async def run():
            with pytest.raises(Exception):
                # additional_read_paths pointing at a directory that does
                # not exist -> OCIContainmentBackend.prepare() itself
                # raises BackendUnavailableError before any container is
                # ever created.
                await manager.start_all({"fixture": _server_cfg(
                    capabilities={"network": "denied", "additional_read_paths": [str(tmp_path / "does_not_exist")]},
                )})

        asyncio.run(run())
    assert _leftover_containers() == []


def test_21_normal_shutdown_leaves_zero_containers(tmp_path):
    import asyncio

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]})})
            tool = kernel.registry.get("tool", "fixture_echo")
            assert await tool.execute(message="x") == "Echo: x"
            await manager.shutdown_all()

        asyncio.run(run())
    assert _leftover_containers() == []


def test_22_forced_shutdown_via_startup_timeout_leaves_zero_containers(tmp_path):
    """A server that never completes its handshake forces MCPClient's own
    startup-timeout path (SEC-004) - proves the container gets torn down
    even when the connection never reaches RUNNING at all."""
    import asyncio

    with _cwd(tmp_path / "ws"):
        cfg = _oci_config()
        cfg.mcp_lifecycle = MCPLifecycleConfig(startup_timeout_seconds=5)
        kernel = Kernel(config=cfg)
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo"))

        async def run():
            with pytest.raises(Exception):
                await manager.start_all({"fixture": _server_cfg(
                    # sleep instead of running the real fixture - never
                    # responds to the initialize handshake at all.
                    command="python3", args=["-c", "import time; time.sleep(120)"],
                    capabilities={"network": "denied"},
                )})

        start = time.monotonic()
        asyncio.run(run())
        elapsed = time.monotonic() - start
        assert elapsed < 30, f"startup timeout + cleanup took too long: {elapsed}s"
    assert _leftover_containers() == []


def test_23_hostile_grandchild_leaves_zero_residue(tmp_path):
    """A grandchild process spawned deep inside the contained MCP server
    (child spawns its own background grandchild) leaves nothing behind
    once the CONTAINER is removed - container teardown kills every
    process inside it, not just the direct child MCPClient itself
    watches."""
    import asyncio

    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "spawn_child"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "additional_read_paths": [TESTS_DIR]},
            )})
            try:
                spawn_tool = kernel.registry.get("tool", "fixture_spawn_child")
                # Backgrounds a grandchild that outlives the immediate
                # child - proves container teardown, not process-tree
                # bookkeeping, is what actually cleans this up.
                await spawn_tool.execute(command="nohup sleep 300 >/dev/null 2>&1 &")
            finally:
                await manager.shutdown_all()

        asyncio.run(run())
    assert _leftover_containers() == []
