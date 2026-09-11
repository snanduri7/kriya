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


def test_dependency_registry_only_fails_closed_not_unrestricted(workspace):
    """Task B's own explicit instruction: a mechanism that cannot honestly
    provide registry-scoped network authority must fail closed for it,
    never silently grant unrestricted network instead."""
    backend = OCIContainmentBackend()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY,
    )
    with pytest.raises(BackendUnavailableError, match="DEPENDENCY_REGISTRY_ONLY"):
        backend.prepare(profile, ["python3", "-c", "pass"])


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
