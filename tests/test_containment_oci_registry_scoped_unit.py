"""SEC-006: deterministic, no-Docker-required unit coverage for
NetworkAuthority.DEPENDENCY_REGISTRY_ONLY - authority identity, the
sentinel-based setup-failure detection (`finalize_registry_acquisition_result`),
and the invariant that `DENIED`/`UNRESTRICTED` argv shapes are unaffected by
this risk's own changes. Real end-to-end proxy/firewall/privilege-drop
behavior lives in tests/test_containment_oci.py (requires real Docker).
"""
import os

import pytest

from kriya.tools.containment import BackendUnavailableError, ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import (
    RegistryAcquisitionSetupError,
    _SETUP_FAILURE_EXIT_CODE,
    _render_setup_script,
    _resolve_acquisition_identity,
    compute_authority_id,
    finalize_registry_acquisition_result,
)
from kriya.tools.process import ProcessResult


# --- authority identity ---

def test_authority_id_is_order_and_case_independent():
    assert compute_authority_id(("pypi.org", "files.pythonhosted.org")) == compute_authority_id(
        ("Files.PythonHosted.org.", "PyPI.org")
    )


def test_authority_id_differs_for_different_host_sets():
    assert compute_authority_id(("pypi.org",)) != compute_authority_id(("repo.maven.apache.org",))


# --- finalize_registry_acquisition_result: sentinel-based setup-failure detection ---

def test_finalize_raises_on_sentinel_exit_code():
    result = ProcessResult(returncode=_SETUP_FAILURE_EXIT_CODE, stdout="", stderr="", timeout=False)
    with pytest.raises(RegistryAcquisitionSetupError):
        finalize_registry_acquisition_result(result)


def test_finalize_raises_on_sentinel_marker_even_with_different_exit_code():
    """The marker string is checked independently of the exact exit code -
    belt-and-braces in case a future script change reports the failure
    through a different (but still clearly-marked) exit path."""
    result = ProcessResult(
        returncode=1, stdout="", stderr="KRIYA_CONTAINMENT_SETUP_FAILED: IPv6 could not be closed",
        timeout=False,
    )
    with pytest.raises(RegistryAcquisitionSetupError, match="IPv6 could not be closed"):
        finalize_registry_acquisition_result(result)


def test_finalize_does_not_raise_on_an_ordinary_command_failure():
    """A real `mvn`/`pip` failure (missing dependency, compile error, etc.)
    must never be misclassified as a containment-setup failure - only the
    fixed sentinel exit code/marker triggers this."""
    result = ProcessResult(returncode=1, stdout="", stderr="[ERROR] BUILD FAILURE", timeout=False)
    finalize_registry_acquisition_result(result)  # must not raise


def test_finalize_does_not_raise_on_success_with_step_markers():
    result = ProcessResult(
        returncode=0,
        stdout="KRIYA_STEP_PROXY_CONNECT=OK\nKRIYA_STEP_FIREWALL=OK\nKRIYA_STEP_IPV6_DISABLE=OK\n"
               "KRIYA_STEP_SELFTEST=OK\nKRIYA_STEP_PRIVDROP=OK\nBUILD SUCCESS",
        stderr="", timeout=False,
    )
    finalize_registry_acquisition_result(result)  # must not raise


# --- SEC-008: acquisition identity must match real host ownership, never a
# fixed value, and must fail closed rather than mutate host permissions ---

def test_render_setup_script_contains_no_chmod_of_host_mounts():
    """SEC-008's own regression guard: the rendered script must never
    contain a recursive/broadening chmod of /kriya/workspace or
    /kriya/cache (the bind-mounted host content) - only its own
    container-private tmpfs scratch dir may be chmod'd."""
    script = _render_setup_script("172.19.0.2", 501, 20)
    assert "chmod -R" not in script
    assert "chown" not in script
    assert "/kriya/workspace" not in script  # only ever mounted, never touched by the setup script
    chmod_lines = [l for l in script.splitlines() if l.strip().startswith("chmod")]
    assert chmod_lines, "expected the tmpfs-only chmod line to still be present"
    assert all("/kriya/tmp/acqhome" in l for l in chmod_lines)  # the one allowed chmod targets tmpfs only


def test_render_setup_script_uses_the_given_uid_and_gid_not_a_fixed_constant():
    script = _render_setup_script("172.19.0.2", 4242, 4343)
    assert "setpriv --reuid=4242 --regid=4343" in script


def test_resolve_acquisition_identity_matches_the_invoking_process(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    uid, gid = _resolve_acquisition_identity(str(ws), [])
    assert (uid, gid) == (os.getuid(), os.getgid())


def test_resolve_acquisition_identity_accepts_matching_cache_paths(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    uid, gid = _resolve_acquisition_identity(str(ws), [str(cache)])
    assert (uid, gid) == (os.getuid(), os.getgid())


def test_resolve_acquisition_identity_fails_closed_on_ownership_mismatch(tmp_path):
    """A workspace/cache path owned by a DIFFERENT real uid than the
    invoking Kriya process must fail closed, never guess/fall back to a
    fixed identity that could either fail to write or silently
    impersonate a different real user. `/usr` is a real, always-present
    root-owned directory - a reliable non-matching-owner fixture without
    needing root privileges to construct one."""
    if os.getuid() == 0:
        pytest.skip("running as root - no non-matching-owner path available for this check")
    with pytest.raises(BackendUnavailableError, match="does not match"):
        _resolve_acquisition_identity("/usr", [])


def test_resolve_acquisition_identity_fails_closed_when_invoking_process_is_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(os, "getuid", lambda: 0)
    with pytest.raises(BackendUnavailableError, match="root"):
        _resolve_acquisition_identity(str(ws), [])


# --- Invariant: DENIED profiles are unaffected by SEC-006 (test R) ---

def test_denied_network_argv_has_no_cap_admin_or_net_admin(tmp_path):
    """Adversarial R: target/execution containers (network=DENIED) must
    never gain NET_ADMIN - SEC-006 only ever adds that capability on the
    DEPENDENCY_REGISTRY_ONLY path."""
    from kriya.tools.containment_oci import OCIContainmentBackend
    import shutil

    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available (only used to construct the backend object, no daemon call)")

    backend = OCIContainmentBackend()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    # _probe_daemon would need a real daemon - bypass it via the same
    # pattern _prepare_registry_scoped uses internally is not exposed
    # here, so this test only inspects argv construction that does not
    # require a live daemon; skip cleanly if none is reachable.
    import subprocess as sp
    try:
        reachable = sp.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        reachable = False
    if not reachable:
        pytest.skip("docker daemon not reachable")

    prepared = backend.prepare(profile, ["/bin/sh", "-c", "true"])
    argv = prepared.command_prefix
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "NET_ADMIN" not in argv
    assert "--cap-add" not in argv
