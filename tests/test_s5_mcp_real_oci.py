"""D2 EXEC-LAYER rebuild S5: the focused REAL-OCI acceptance tier for MCP containment. Real Docker throughout; side effects verified from outside the container.

The deterministic semantics (the owner's decision, the refusals, the evidence rule) are tested with a test-only stand-in in tests/test_s5_mcp_containment.py -
the stand-in proves no isolation. THIS file proves, against the real backend, the claims the stand-in cannot:

- a server started under required containment is actually inside a container (one exists, labelled with its capability digest, while it runs) and
  ``containment_active`` is true for it - and only for it;
- the DEFAULT grant mounts no workspace at all;
- a granted writable workspace is writable EXCEPT Kriya's authority paths (``.kriya/control``), which the S1 masks protect;
- an ``unrestricted`` network grant is valid inside the container (containment owns where it runs, the grant owns the network) and the filesystem
  boundary is unchanged by it;
- required containment with the Null backend is refused before any process or container exists (no host fallback), on a Docker-equipped host too.

Complementary tests of the same tier (undeclared host paths, symlink escapes, credential sentinels, child inheritance, zero residue, backend unavailable,
network denied/unrestricted) are tests/test_tool003_p2_oci_enforcement.py.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys

import pytest
from test_tool003_p2_oci_enforcement import TESTS_DIR, _approved, _cwd, _leftover_containers, _oci_config, _server_cfg

from kriya.config.config import AppConfig, AutonomyConfig
from kriya.core.kernel import Kernel
from kriya.mcp.mcp import MCPManager
from kriya.tools.containment import ExternalExecutionRefused


def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")


def test_a_server_under_required_containment_is_actually_in_a_container_and_only_then_reports_active(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "echo", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(capabilities={"additional_read_paths": [TESTS_DIR]})})
            try:
                client = manager.clients["fixture"]
                assert (client.containment_required, client.containment_active, client.containment_backend_name) == (True, True, "oci")
                assert len(_leftover_containers()) == 1, "exactly one labelled container exists while the server runs"
                host_only = tmp_path / "host_only.txt"
                host_only.write_text("only the host has this path")
                seen = await kernel.registry.get("tool", "fixture_read_path").execute(path=str(host_only))
                assert "only the host has this path" not in seen, "the server is not on the host: the host path does not exist where it runs"
            finally:
                await manager.shutdown_all()
            assert manager.clients == {} and _leftover_containers() == []

        asyncio.run(run())


def test_the_default_grant_mounts_no_workspace_at_all(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "present.txt").write_text("in the operator workspace")
    with _cwd(ws):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(capabilities={"additional_read_paths": [TESTS_DIR]})})
            try:
                result = await kernel.registry.get("tool", "fixture_read_path").execute(path="/kriya/workspace/present.txt")
                assert "in the operator workspace" not in result, result
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


def test_a_writable_workspace_grant_cannot_reach_kriyas_authority_paths(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with _cwd(ws):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "write_path", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "denied", "workspace_write": True, "additional_read_paths": [TESTS_DIR]})})
            try:
                write = kernel.registry.get("tool", "fixture_write_path")
                assert await write.execute(path="/kriya/workspace/ordinary.txt", content="ok") == "write_succeeded"
                denied = await write.execute(path="/kriya/workspace/.kriya/control/forged.json", content="forged")
                assert "write_failed" in denied, denied
            finally:
                await manager.shutdown_all()

        asyncio.run(run())
    assert (ws / "ordinary.txt").read_text() == "ok"
    assert not (ws / ".kriya" / "control" / "forged.json").exists(), "the authority path was not written from inside the container"


def test_an_unrestricted_network_grant_is_valid_in_the_container_and_the_filesystem_boundary_is_unchanged(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("host content outside every grant")
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=_oci_config())
        manager = MCPManager(kernel, execution_policy=_approved("fixture", "read_path"))

        async def run():
            await manager.start_all({"fixture": _server_cfg(
                capabilities={"network": "unrestricted", "additional_read_paths": [TESTS_DIR]})})
            try:
                client = manager.clients["fixture"]
                assert client.containment_required is True and client.containment_active is True
                result = await kernel.registry.get("tool", "fixture_read_path").execute(path=str(outside))
                assert "host content outside every grant" not in result
            finally:
                await manager.shutdown_all()

        asyncio.run(run())


def test_required_containment_with_the_null_backend_is_refused_before_any_container_or_process_on_a_docker_host_too(tmp_path):
    with _cwd(tmp_path / "ws"):
        kernel = Kernel(config=AppConfig(autonomy=AutonomyConfig(mcp_contained_execution_required=True, containment_backend="none")))
        manager = MCPManager(kernel)
        with pytest.raises(ExternalExecutionRefused):
            asyncio.run(manager.start_all({"fixture": _server_cfg(
                command=sys.executable, capabilities={"network": "unrestricted"})}))
        assert manager.clients == {} and _leftover_containers() == []
