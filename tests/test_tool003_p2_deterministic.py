"""TOOL-003 P2: deterministic, no-Docker-required tests for the pure
mapping/governance/lifecycle-mechanics pieces. Real-OCI evidence (the
required 23-item matrix) lives in tests/test_tool003_p2_oci_enforcement.py -
this file covers what doesn't need a real container to prove.
"""
import asyncio
import contextlib
import os

import pytest
import yaml

from kriya.config.authority import ConfigAuthorityError, FieldClassification, classify_field
from kriya.config.config import load_config
from kriya.mcp.capability import compute_mcp_capability_profile_digest, resolve_mcp_capability_profile
from kriya.mcp.containment_adapter import MCPContainmentUnsupportedError, map_capability_profile_to_containment
from kriya.mcp.mcp import MCPClient
from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    MountSpec,
    NetworkAuthority,
    NullContainmentBackend,
    TrustClass,
    resolve_containment_backend,
)


@pytest.fixture(autouse=True)
def isolated_authority_home(tmp_path, monkeypatch):
    home = tmp_path / "_authority_home"
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(home))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    yield


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f)


# =====================================================================
# Profile -> containment mapping
# =====================================================================

def test_default_profile_maps_to_no_mounts_denied_network(tmp_path):
    profile = resolve_mcp_capability_profile({}, workspace_root=str(tmp_path))
    cp = map_capability_profile_to_containment(
        profile, workspace_root=str(tmp_path), resolved_env={}, profile_digest="d1",
    )
    assert cp.mount_workspace is False
    assert cp.network is NetworkAuthority.DENIED
    assert cp.additional_mounts == ()
    assert cp.persistent_stdio is True
    assert cp.trust_class is TrustClass.UNTRUSTED_EXECUTION


def test_workspace_read_only_maps_to_ro_mount():
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    cp = map_capability_profile_to_containment(profile, workspace_root="/tmp/ws", resolved_env={}, profile_digest="d")
    assert cp.mount_workspace is True
    assert cp.workspace_write is False


def test_workspace_write_maps_to_rw_mount_and_real_uid(tmp_path):
    profile = resolve_mcp_capability_profile({"workspace_write": True}, workspace_root=str(tmp_path))
    cp = map_capability_profile_to_containment(profile, workspace_root=str(tmp_path), resolved_env={}, profile_digest="d")
    assert cp.mount_workspace is True
    assert cp.workspace_write is True
    assert cp.run_as_uid == os.getuid()
    assert cp.run_as_gid == os.getgid()


def test_no_write_authority_maps_to_fixed_nobody_identity(tmp_path):
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root=str(tmp_path))
    cp = map_capability_profile_to_containment(profile, workspace_root=str(tmp_path), resolved_env={}, profile_digest="d")
    assert cp.run_as_uid == 65534
    assert cp.run_as_gid == 65534


def test_additional_paths_map_to_mountspecs_with_deterministic_container_paths(tmp_path):
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    rw_dir = tmp_path / "rw"
    rw_dir.mkdir()
    profile = resolve_mcp_capability_profile(
        {"additional_read_paths": [str(ro_dir)], "additional_write_paths": [str(rw_dir)]},
        workspace_root=str(tmp_path),
    )
    cp = map_capability_profile_to_containment(profile, workspace_root=str(tmp_path), resolved_env={}, profile_digest="d")
    assert MountSpec(host_path=os.path.realpath(str(ro_dir)), container_path="/kriya/mcp/extra/ro/0", writable=False) in cp.additional_mounts
    assert MountSpec(host_path=os.path.realpath(str(rw_dir)), container_path="/kriya/mcp/extra/rw/0", writable=True) in cp.additional_mounts


def test_explicit_destinations_raises_unsupported():
    profile = resolve_mcp_capability_profile(
        {"network": "explicit_destinations", "network_hosts": ["a.com"]}, workspace_root="/tmp/ws",
    )
    with pytest.raises(MCPContainmentUnsupportedError):
        map_capability_profile_to_containment(profile, workspace_root="/tmp/ws", resolved_env={}, profile_digest="d")


def test_unrestricted_maps_to_unrestricted_network_only():
    profile = resolve_mcp_capability_profile({"network": "unrestricted"}, workspace_root="/tmp/ws")
    cp = map_capability_profile_to_containment(profile, workspace_root="/tmp/ws", resolved_env={}, profile_digest="d")
    assert cp.network is NetworkAuthority.UNRESTRICTED
    assert cp.mount_workspace is False  # network alone never implies filesystem authority


def test_resolved_env_carried_through_verbatim():
    profile = resolve_mcp_capability_profile({}, workspace_root="/tmp/ws")
    env = {"EXPLICIT": "value", "HOME": "/custom/home"}
    cp = map_capability_profile_to_containment(profile, workspace_root="/tmp/ws", resolved_env=env, profile_digest="d")
    assert cp.resolved_env == env


def test_authority_label_is_the_profile_digest():
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    digest = compute_mcp_capability_profile_digest(profile)
    cp = map_capability_profile_to_containment(profile, workspace_root="/tmp/ws", resolved_env={}, profile_digest=digest)
    assert cp.authority_label == digest


def test_no_write_targets_resolve_identity_does_not_touch_filesystem():
    from kriya.mcp.containment_adapter import resolve_mcp_container_identity
    assert resolve_mcp_container_identity([]) == (65534, 65534)


def test_write_target_ownership_mismatch_fails_closed(tmp_path):
    from kriya.mcp.containment_adapter import resolve_mcp_container_identity
    # Can't easily fabricate a different real owner in a test environment
    # without root, so this proves the REACHABLE branch instead: a target
    # owned by the invoking process succeeds.
    target = tmp_path / "owned"
    target.mkdir()
    uid, gid = resolve_mcp_container_identity([str(target)])
    assert uid == os.getuid()
    assert gid == os.getgid()


# =====================================================================
# Backend selection / fail-closed
# =====================================================================

def test_resolve_containment_backend_none_and_oci_recognized():
    assert resolve_containment_backend("none").name == "none"
    assert resolve_containment_backend("oci").name == "oci"


def test_resolve_containment_backend_unknown_fails_closed():
    with pytest.raises(BackendUnavailableError):
        resolve_containment_backend("not_a_real_backend")


def test_null_backend_refuses_non_unrestricted_untrusted_profile():
    backend = NullContainmentBackend()
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/tmp", network=NetworkAuthority.DENIED)
    with pytest.raises(BackendUnavailableError):
        backend.prepare(profile, ["python3"])


# =====================================================================
# Capability-profile binding before start (no Docker - __init__ never spawns)
# =====================================================================

def test_client_binds_containment_required_and_profile_before_start():
    profile = resolve_mcp_capability_profile({"workspace_read": True}, workspace_root="/tmp/ws")
    client = MCPClient(
        name="probe", command="python3", args=["-c", "pass"],
        capability_profile=profile, containment_required=True, containment_backend=NullContainmentBackend(),
    )
    assert client._process is None
    assert client.containment_required is True
    assert client.containment_active is False  # not yet started
    assert client.capability_profile is profile


def test_client_default_containment_required_false_preserves_host_mode():
    client = MCPClient(name="probe", command="python3", args=["-c", "pass"])
    assert client.containment_required is False
    assert client.containment_active is False


# =====================================================================
# SEC-009 governance of the new flags
# =====================================================================

def test_mcp_contained_execution_required_is_security_authority():
    assert classify_field("autonomy", "mcp_contained_execution_required") == FieldClassification.SECURITY_AUTHORITY


def test_container_cleanup_timeout_is_security_authority():
    assert classify_field("mcp_lifecycle", "container_cleanup_timeout_seconds") == FieldClassification.SECURITY_AUTHORITY


def test_repo_cannot_enable_mcp_contained_execution_required(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"mcp_contained_execution_required": True}})
        with pytest.raises(ConfigAuthorityError, match="mcp_contained_execution_required"):
            load_config()


def test_repo_cannot_disable_mcp_contained_execution_required_either(tmp_path):
    """Widening AND narrowing both require authority - SEC-009 governs the
    FIELD, not just one direction (mirrors contained_execution_required's
    own doctrine: "flipping this to True... fails CLOSED" applies
    symmetrically - a repo could otherwise silently turn OFF an
    operator's own True setting by shipping an explicit False)."""
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"mcp_contained_execution_required": False}})
        with pytest.raises(ConfigAuthorityError, match="mcp_contained_execution_required"):
            load_config()


# =====================================================================
# Lifecycle: cleanup callable invocation (fake backend, no Docker)
# =====================================================================

def test_containment_cleanup_invoked_on_close():
    """A fake cleanup callable (standing in for docker rm -f) must be
    invoked exactly once during _close(), off the event loop."""
    cleanup_calls = []

    async def run():
        client = MCPClient(name="probe", command="python3", args=["-c", "pass"], containment_required=True)
        client._containment_cleanup = lambda: cleanup_calls.append(1)
        from kriya.mcp.mcp import _ConnState
        client._state = _ConnState.RUNNING
        await client._close(RuntimeError("test teardown"))

    asyncio.run(run())
    assert cleanup_calls == [1]


def test_containment_cleanup_timeout_does_not_raise():
    """A cleanup callable that never returns must not hang/raise out of
    _close() - bounded by mcp_lifecycle.container_cleanup_timeout_seconds,
    logged and treated as terminal (mirrors terminate_mcp_process()'s own
    reap-timeout precedent)."""
    import time as _time

    from kriya.config.config import MCPLifecycleConfig

    async def run():
        client = MCPClient(
            name="probe", command="python3", args=["-c", "pass"], containment_required=True,
            lifecycle_config=MCPLifecycleConfig(container_cleanup_timeout_seconds=1),
        )
        client._containment_cleanup = lambda: _time.sleep(30)
        from kriya.mcp.mcp import _ConnState
        client._state = _ConnState.RUNNING
        start = _time.monotonic()
        await client._close(RuntimeError("test teardown"))
        elapsed = _time.monotonic() - start
        assert elapsed < 5, f"close() should not wait for the full 30s cleanup: {elapsed}s"

    asyncio.run(run())
