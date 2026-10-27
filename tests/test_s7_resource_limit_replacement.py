"""S7 -> S8: the contained replacement for the host resource-limit characterizations S8 deletes (tests/test_sec004_mcp_lifecycle.py: ``test_cpu_over_limit_process_killed_by_os``,
``test_memory_over_limit_outcome_platform_honest``, ``test_resource_setup_failure_fails_closed``, still marked ``legacy_host_mcp``).

Those three characterize host RLIMIT_CPU / RLIMIT_AS on an MCP child started WITHOUT a backend - a path the secure default removes. What replaces them is the container's own bound:
``profile.memory_mb`` becomes a cgroup-enforced ``--memory`` / ``--memory-swap`` hard limit (really enforced: tests/test_containment_oci.py::test_memory_limit_is_enforced) and
``profile.cpu_seconds`` becomes a ``--cpus`` rate cap (MEASURED 2026-10-26: NO test pinned that mapping before this file), and an MCP server's lifecycle limits reach the profile that
carries them. A resource setup that cannot be established stays a refusal before anything starts (tests/test_process_controller_containment.py)."""
import pytest
from _real_oci import REAL_OCI_DAEMON

from kriya.mcp.containment_adapter import map_capability_profile_to_containment
from kriya.tools import containment_oci
from kriya.tools.containment import ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import OCIContainmentBackend


def _prepare(tmp_path, monkeypatch, *, acquisition=False, **limits):
    """The argv `docker run` is built with, for the ordinary denied-network path or the registry-scoped acquisition path (the two places the flags are emitted)."""
    backend = OCIContainmentBackend()
    monkeypatch.setattr(backend, "_require_docker", lambda: "docker")
    monkeypatch.setattr(backend, "_probe_daemon", lambda _docker: None)
    network = dict(network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, network_destinations=("repo.maven.apache.org",)) if acquisition else dict(network=NetworkAuthority.DENIED)
    if acquisition:
        monkeypatch.setattr(containment_oci, "_ensure_image", lambda *args, **kwargs: None)
        monkeypatch.setattr(containment_oci, "_check_net_admin_available", lambda *args, **kwargs: None)
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path), **network, **limits)
    prepared = backend.prepare(profile, ["mvn", "compile"] if acquisition else ["python3", "-c", "pass"])
    try:
        return list(prepared.command_prefix)
    finally:
        # the registry-scoped `prepare` is NOT pure: it creates the run's network and starts its proxy container, released by the prepared command's own cleanup (ProcessController runs it
        # in `finally`). A test that prepares without running must run it too - the first version of this file leaked a proxy container and a network per call (found by the real-OCI
        # tier's leak checks, 2026-10-26)
        if prepared.cleanup is not None:
            prepared.cleanup()


# the registry-scoped path really creates a docker network and starts the proxy container, so that case needs a reachable daemon (its skip reason names docker: pinned to the docker worker)
ACQUISITION = pytest.mark.parametrize("acquisition", [False, pytest.param(True, marks=REAL_OCI_DAEMON)], ids=["denied-network", "registry-scoped-acquisition"])


@ACQUISITION
def test_a_memory_budget_becomes_a_hard_cgroup_limit_with_no_swap_headroom(tmp_path, monkeypatch, acquisition):
    argv = _prepare(tmp_path, monkeypatch, acquisition=acquisition, memory_mb=256)
    assert argv[argv.index("--memory") + 1] == "256m" and argv[argv.index("--memory-swap") + 1] == "256m"


@ACQUISITION
def test_a_cpu_budget_becomes_the_documented_rate_cap(tmp_path, monkeypatch, acquisition):
    argv = _prepare(tmp_path, monkeypatch, acquisition=acquisition, cpu_seconds=30)
    assert argv[argv.index("--cpus") + 1] == containment_oci._CPU_RATE_CAP         # pylint: disable=protected-access


@ACQUISITION
def test_no_budget_adds_no_resource_flag(tmp_path, monkeypatch, acquisition):
    argv = _prepare(tmp_path, monkeypatch, acquisition=acquisition)
    assert "--memory" not in argv and "--memory-swap" not in argv and "--cpus" not in argv


@pytest.mark.parametrize("cpu_seconds, memory_mb", [(7, 128), (None, 64), (3, None), (None, None)])
def test_an_mcp_servers_lifecycle_limits_reach_the_profile_that_carries_them(tmp_path, cpu_seconds, memory_mb):
    from kriya.mcp.capability import resolve_mcp_capability_profile

    profile = resolve_mcp_capability_profile({}, workspace_root=str(tmp_path))
    mapped = map_capability_profile_to_containment(profile, workspace_root=str(tmp_path), resolved_env={}, profile_digest="d", cpu_seconds=cpu_seconds, memory_mb=memory_mb)
    assert (mapped.cpu_seconds, mapped.memory_mb) == (cpu_seconds, memory_mb) and mapped.trust_class is TrustClass.UNTRUSTED_EXECUTION
