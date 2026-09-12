"""SEC-001-P6: OCIContainmentBackend adversarial + legitimate acceptance -
real Docker throughout (no mocked subprocess/docker primitives). Side
effects are verified EXTERNALLY (host filesystem checks, `docker ps`), not
from the contained process's own stdout claims, per the task's own
"Verify side effects externally" instruction.

Requires a real, reachable Docker daemon - skipped entirely otherwise (this
module's own contribution to "focused tests during implementation", not a
substitute for a CI-level Docker dependency decision, which is out of
scope for this package)."""
import os
import shutil
import subprocess
import time

import pytest

from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    ContainmentSetupError,
    NetworkAuthority,
    TrustClass,
)
from kriya.tools.containment_oci import OCIContainmentBackend
from kriya.tools.process import ProcessController

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _docker_reachable() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


if not _docker_reachable():
    pytestmark = pytest.mark.skip(reason="docker daemon not reachable")


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# --- legitimate execution: authorized workspace read/write ---

def test_authorized_workspace_write_then_read(workspace):
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    write_result = controller.run(
        ["/bin/sh", "-c", "echo hello-from-container > out.txt"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert write_result.returncode == 0, write_result.stderr

    host_file = workspace / "out.txt"
    assert host_file.exists()
    assert host_file.read_text().strip() == "hello-from-container"


def test_authorized_java_compile_and_run(workspace):
    (workspace / "Hello.java").write_text(
        "public class Hello { public static void main(String[] a) { System.out.println(\"java-ok\"); } }\n"
    )
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    result = controller.run(
        ["/bin/sh", "-c", "javac Hello.java && java Hello"],
        cwd=".", timeout=120, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert result.returncode == 0, result.stderr
    assert "java-ok" in result.stdout


def test_authorized_python_run(workspace):
    (workspace / "script.py").write_text("print('python-ok')\n")
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    result = controller.run(
        ["python3", "script.py"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert result.returncode == 0, result.stderr
    assert "python-ok" in result.stdout


# --- adversarial: hostile code cannot read a protected host sentinel ---

def test_hostile_code_cannot_read_host_sentinel_outside_workspace(tmp_path, workspace):
    secret_dir = tmp_path / "host_secret_area"
    secret_dir.mkdir()
    sentinel = secret_dir / "sentinel.txt"
    sentinel.write_text("HOST-SECRET-DO-NOT-LEAK")

    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    result = controller.run(
        ["/bin/sh", "-c", f"cat {sentinel} 2>&1; echo EXIT:$?"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "HOST-SECRET-DO-NOT-LEAK" not in result.stdout
    assert "EXIT:0" not in result.stdout  # cat must have failed (file not found in container)


# --- adversarial: hostile code cannot write outside authorized boundaries ---

def test_hostile_code_cannot_write_sentinel_outside_workspace(tmp_path, workspace):
    outside_target = tmp_path / "should_not_be_created.txt"

    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    # The host path doesn't even exist inside the container's mount
    # namespace - any attempt to write there must fail, proven by checking
    # the HOST filesystem afterward, not the container's own exit code.
    controller.run(
        ["/bin/sh", "-c", f"echo pwned > {outside_target} 2>/dev/null"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert not outside_target.exists()


# --- adversarial: prohibited host environment secret never reaches the container ---

def test_hostile_code_cannot_receive_unallowlisted_host_secret(monkeypatch, workspace):
    monkeypatch.setenv("KRIYA_TEST_HOST_SECRET", "super-secret-value-must-not-leak")
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
        env_allowlist=[],  # nothing allowlisted, including the secret above
    )
    result = controller.run(
        ["/bin/sh", "-c", "echo VALUE:[$KRIYA_TEST_HOST_SECRET]"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "super-secret-value-must-not-leak" not in result.stdout
    assert "VALUE:[]" in result.stdout


def test_allowlisted_env_var_does_reach_the_container(monkeypatch, workspace):
    """Positive control for the test above - proves the allowlist actually
    forwards what it's supposed to, not just that it blocks by accident."""
    monkeypatch.setenv("KRIYA_TEST_ALLOWED_VAR", "should-be-visible")
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
        env_allowlist=["KRIYA_TEST_ALLOWED_VAR"],
    )
    result = controller.run(
        ["/bin/sh", "-c", "echo VALUE:[$KRIYA_TEST_ALLOWED_VAR]"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "VALUE:[should-be-visible]" in result.stdout


def test_host_only_env_vars_never_leak_even_when_allowlisted(monkeypatch, workspace):
    """Found live, 2026-09-11: AutonomyConfig's own packaged
    sandbox_env_allowlist default includes JAVA_HOME - a real host JDK
    path this exact developer machine has set - which broke Maven's own
    launcher when forwarded verbatim into a container ("JAVA_HOME
    environment variable is not defined correctly"). JAVA_HOME/HOME/
    TMPDIR/etc. must never reach the container even when a caller's own
    env_allowlist explicitly names them (Invariant: host paths/
    interpreters must not leak accidentally into container execution) -
    the container keeps whatever value its OWN image already sets."""
    monkeypatch.setenv("JAVA_HOME", "/definitely/not/a/real/container/path")
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED, env_allowlist=["JAVA_HOME"],
    )
    result = controller.run(
        ["/bin/sh", "-c", "echo VALUE:[$JAVA_HOME]"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "/definitely/not/a/real/container/path" not in result.stdout


# --- adversarial: no prohibited network connection under DENIED ---

def test_network_denied_blocks_outbound_connection(workspace):
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    result = controller.run(
        ["python3", "-c", "import socket; socket.create_connection(('8.8.8.8', 53), timeout=5)"],
        cwd=".", timeout=30, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert result.returncode != 0


def test_network_unrestricted_allows_outbound_connection(workspace):
    """Positive control - proves DENIED above is actually the network
    control doing the blocking, not some other unrelated failure."""
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.UNRESTRICTED,
    )
    result = controller.run(
        ["python3", "-c", "import socket; socket.create_connection(('8.8.8.8', 53), timeout=5); print('CONNECTED')"],
        cwd=".", timeout=30, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert result.returncode == 0, result.stderr
    assert "CONNECTED" in result.stdout


def test_dependency_registry_only_with_empty_destinations_fails_closed(workspace):
    """SEC-006: DEPENDENCY_REGISTRY_ONLY is now a real, enforced mechanism
    (see the registry-scoped tests below) - but a profile that asks for it
    with NO authorized destinations at all must still fail closed rather
    than silently behave like DENIED or UNRESTRICTED."""
    backend = OCIContainmentBackend()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, network_destinations=(),
    )
    with pytest.raises(BackendUnavailableError, match="non-empty"):
        backend.prepare(profile, ["python3", "-c", "pass"])


# --- SEC-006: registry-scoped egress, real Docker end-to-end ---

def _registry_profile(workspace, hosts):
    return ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, network_destinations=tuple(hosts),
    )


def test_registry_scoped_denies_unauthorized_https_connect(workspace):
    """Adversarial D: an HTTPS CONNECT to a host NOT on the authorized
    list must be denied by the proxy's own ACL (403), independent of the
    firewall (which would also block a DIRECT attempt - this proves the
    PROXY PATH itself is destination-scoped, not merely reachable)."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    script = (
        'PROXY_HOSTPORT="${http_proxy#http://}"; '
        'printf "CONNECT example.com:443 HTTP/1.1\\r\\nHost: example.com:443\\r\\n\\r\\n" | '
        'timeout 5 nc "${PROXY_HOSTPORT%:*}" "${PROXY_HOSTPORT##*:}" | head -1'
    )
    result = controller.run(
        ["/bin/sh", "-c", script], cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "403" in result.stdout, result.stdout + result.stderr


def test_registry_scoped_allows_authorized_https_connect(workspace):
    """Positive control for the test above - proves the 403 is real ACL
    enforcement, not the proxy being broken/unreachable."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    script = (
        'PROXY_HOSTPORT="${http_proxy#http://}"; '
        'printf "CONNECT repo.maven.apache.org:443 HTTP/1.1\\r\\nHost: repo.maven.apache.org:443\\r\\n\\r\\n" | '
        'timeout 5 nc "${PROXY_HOSTPORT%:*}" "${PROXY_HOSTPORT##*:}" | head -1'
    )
    result = controller.run(
        ["/bin/sh", "-c", script], cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "200" in result.stdout, result.stdout + result.stderr


def test_registry_scoped_blocks_direct_egress_bypassing_proxy(workspace):
    """Adversarial E/F/H: even explicitly ignoring the proxy
    (`--noproxy`-equivalent - a raw direct connection attempt), the
    acquisition container's own firewall must block it. Uses `nc`
    (baked into the acquisition image) rather than `curl` (not present in
    the plain default image this test's command falls back to)."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    result = controller.run(
        ["/bin/sh", "-c", "nc -z -w3 1.1.1.1 80 && echo BYPASS_BAD || echo BLOCKED_GOOD"],
        cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "BLOCKED_GOOD" in result.stdout
    assert "BYPASS_BAD" not in result.stdout


def test_registry_scoped_blocks_client_dns_resolution(workspace):
    """Adversarial G: the client itself must never be able to resolve
    ANY hostname (not even the authorized one) - the proxy resolves on
    its own behalf, so the client has no legitimate need to, and DNS is
    a potential independent bypass channel if left open."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    result = controller.run(
        ["/bin/sh", "-c", "getent hosts repo.maven.apache.org && echo DNS_WORKED_BAD || echo DNS_BLOCKED_GOOD"],
        cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "DNS_BLOCKED_GOOD" in result.stdout


def test_registry_scoped_blocks_ipv6_egress(workspace):
    """Adversarial I: a live IPv6 self-test, not an assumption from the
    absence of IPv6 routing (the design investigation's own explicitly
    flagged gap - this closes it)."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    result = controller.run(
        ["/bin/sh", "-c", "nc -6 -z -w3 2606:4700:4700::1111 443 && echo IPV6_WORKED_BAD || echo IPV6_BLOCKED_GOOD"],
        cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "IPV6_BLOCKED_GOOD" in result.stdout


def test_registry_scoped_untrusted_process_cannot_alter_firewall(workspace):
    """Adversarial L: the privilege-dropped acquisition process itself
    must not be able to flush/change the rules that constrain it."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    result = controller.run(
        ["/bin/sh", "-c", "iptables -F 2>&1 && echo ALTERED_BAD || echo DENIED_GOOD"],
        cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert "DENIED_GOOD" in result.stdout
    assert "ALTERED_BAD" not in result.stdout


def test_registry_scoped_untrusted_process_has_no_residual_capabilities(workspace):
    """Adversarial M: after privilege drop, the untrusted process's own
    effective/permitted capability sets must be empty - not merely
    "running as a non-root UID" (which alone would not prevent regaining
    capabilities if any were left in the process's capability sets)."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    result = controller.run(
        ["/bin/sh", "-c", "id -u; grep -E '^Cap(Prm|Eff)' /proc/self/status"],
        cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    # Real command output only - the setup script's own KRIYA_STEP_* lines
    # precede it (this backend never returns just the wrapped command's
    # own stdout in isolation).
    lines = [l for l in result.stdout.strip().splitlines() if not l.startswith("KRIYA_STEP")]
    assert lines[0].strip() != "0", "acquisition command ran as root"
    for line in lines[1:]:
        assert "0000000000000000" in line, result.stdout


def test_registry_scoped_teardown_leaves_no_surviving_resources(workspace):
    """Cleanup must remove the acquisition container, the per-run proxy,
    AND the per-run network - not just the acquisition container (which
    already has --rm)."""
    controller = ProcessController()
    profile = _registry_profile(workspace, ["repo.maven.apache.org"])
    controller.run(
        ["/bin/sh", "-c", "true"], cwd=str(workspace), timeout=60,
        containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    time.sleep(1)
    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=kriya-acq-", "--filter", "name=kriya-oci-",
         "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    assert ps.stdout.strip() == "", f"resource(s) survived teardown: {ps.stdout!r}"
    nets = subprocess.run(
        ["docker", "network", "ls", "--filter", "name=kriya-acq-net-", "--format", "{{.Name}}"],
        capture_output=True, text=True, timeout=10,
    )
    assert nets.stdout.strip() == "", f"network(s) survived teardown: {nets.stdout!r}"


def test_registry_scoped_concurrent_authorities_do_not_cross_grant(tmp_path):
    """Mandatory concurrency test: two acquisitions with DIFFERENT
    authorities, run truly concurrently, must each reach only their own
    authorized host and be denied the other's - proving
    authority(A) != authority(B) does not let A reach B's destination."""
    import threading

    def run_one(hosts, own_target, other_target, results, key):
        ws = tmp_path / key
        ws.mkdir()
        controller = ProcessController()
        profile = _registry_profile(ws, hosts)
        script = (
            'PROXY_HOSTPORT="${http_proxy#http://}"; '
            f'for t in {own_target} {other_target}; do '
            'printf "CONNECT ${t}:443 HTTP/1.1\\r\\nHost: ${t}:443\\r\\n\\r\\n" | '
            'timeout 5 nc "${PROXY_HOSTPORT%:*}" "${PROXY_HOSTPORT##*:}" | head -1; done'
        )
        results[key] = controller.run(
            ["/bin/sh", "-c", script], cwd=str(ws), timeout=90,
            containment_profile=profile, containment_backend=OCIContainmentBackend(),
        )

    results = {}
    t_a = threading.Thread(target=run_one, args=(
        ["repo.maven.apache.org"], "repo.maven.apache.org", "pypi.org", results, "authority_a",
    ))
    t_b = threading.Thread(target=run_one, args=(
        ["pypi.org"], "pypi.org", "repo.maven.apache.org", results, "authority_b",
    ))
    t_a.start()
    t_b.start()
    t_a.join(timeout=120)
    t_b.join(timeout=120)

    # Real probe output only - strip the setup script's own preamble.
    out_a = [l for l in results["authority_a"].stdout.splitlines() if not l.startswith("KRIYA_STEP")]
    out_b = [l for l in results["authority_b"].stdout.splitlines() if not l.startswith("KRIYA_STEP")]
    assert "200" in out_a[0], out_a  # A reaches its own host
    assert "403" in out_a[1], out_a  # A denied B's host
    assert "200" in out_b[0], out_b  # B reaches its own host
    assert "403" in out_b[1], out_b  # B denied A's host


# --- adversarial: no surviving child process after timeout ---

def test_timeout_leaves_no_surviving_container(workspace):
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED,
    )
    result = controller.run(
        ["/bin/sh", "-c", "sleep 120"],
        cwd=".", timeout=3, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert result.timeout is True

    # Give docker a moment to finish tearing the container down, then check
    # from the HOST - not from the container's own (killed) exit status -
    # that nothing with Kriya's OCI naming convention is still running.
    time.sleep(2)
    ps = subprocess.run(
        ["docker", "ps", "--filter", "name=kriya-oci-", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    assert ps.stdout.strip() == "", f"container(s) survived timeout: {ps.stdout!r}"


# --- adversarial: enforced resource limit terminates the process ---

def test_memory_limit_is_enforced(workspace):
    controller = ProcessController()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DENIED, memory_mb=64,
    )
    result = controller.run(
        ["python3", "-c", "x = bytearray(500 * 1024 * 1024)"],  # 500MB against a 64MB cap
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend(),
    )
    assert result.returncode != 0


# --- backend-unavailable / setup-failure adversarial cases ---

def test_missing_workspace_directory_blocks_execution(tmp_path):
    controller = ProcessController()
    missing = tmp_path / "does_not_exist"
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(missing),
        network=NetworkAuthority.DENIED,
    )
    with pytest.raises(ContainmentSetupError):
        controller.run(
            ["/bin/sh", "-c", "echo should-not-run"],
            cwd=".", timeout=10, containment_profile=profile, containment_backend=OCIContainmentBackend(),
        )
