"""SEC-001-P6: managed-service containment (Stage 4) - the actual LAUNCHED,
HTTP-probed service runs inside a real, network=DENIED Docker container,
with readiness/probe issued via `docker exec` into the container's own
loopback (no port ever published to the host). Real Docker daemon
throughout, skipped entirely if unavailable."""
import shutil
import subprocess
import time

import pytest

from kriya.tools.containment import ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import OCIContainmentBackend
from kriya.tools.process import ProcessController
from kriya.tools.service_runtime import (
    ManagedServiceVerificationSpec,
    ProbeSpec,
    ReadinessSpec,
    ServiceVerificationOutcomeKind,
    run_managed_service_verification,
)

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _docker_reachable() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


if not _docker_reachable():
    pytestmark = pytest.mark.skip(reason="docker daemon not reachable")


_HEALTH_SERVICE_SCRIPT = """
import http.server, socket, sys

port = int(sys.argv[1])

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        elif self.path == "/egress-check":
            import socket as sk
            try:
                sk.create_connection(("8.8.8.8", 53), timeout=3)
                body = b"OUTBOUND-SUCCEEDED"
            except OSError as e:
                body = ("OUTBOUND-BLOCKED:" + str(e)).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a):
        pass

server = http.server.HTTPServer(("127.0.0.1", port), Handler)
server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.serve_forever()
"""


def _contained_spec(cwd: str, port: int = 8000) -> ManagedServiceVerificationSpec:
    return ManagedServiceVerificationSpec(
        service_command=["python3", "-c", _HEALTH_SERVICE_SCRIPT, str(port)],
        cwd=cwd,
        readiness=ReadinessSpec(kind="http", port=port, path="/health"),
        probe=ProbeSpec(port=port, path="/health", expected_status=200),
        startup_timeout_seconds=15.0, probe_timeout_seconds=10.0, shutdown_timeout_seconds=10.0,
    )


def _profile(workspace: str) -> ContainmentProfile:
    return ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=workspace, network=NetworkAuthority.DENIED,
    )


def test_contained_managed_service_passes_real_readiness_and_probe(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller = ProcessController()
    result = run_managed_service_verification(
        _contained_spec(str(workspace)), controller=controller,
        containment_profile=_profile(str(workspace)), containment_backend=OCIContainmentBackend(),
    )
    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED, result.reasoning
    assert result.passed is True
    assert result.probe_status == 200


def test_contained_managed_service_cannot_reach_outbound_network(tmp_path):
    """Adversarial: the SAME running, containment-verified service cannot
    make a prohibited outbound connection - probed via a second request to
    its own /egress-check endpoint (still reached via docker exec, never a
    published port), proving network=DENIED applies to the long-lived
    service process itself, not just the build-prep step."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller = ProcessController()
    spec = _contained_spec(str(workspace))
    egress_probe = ProbeSpec(port=spec.probe.port, path="/egress-check", expected_status=200)
    spec = ManagedServiceVerificationSpec(
        service_command=spec.service_command, cwd=spec.cwd, readiness=spec.readiness, probe=egress_probe,
        startup_timeout_seconds=spec.startup_timeout_seconds, probe_timeout_seconds=spec.probe_timeout_seconds,
        shutdown_timeout_seconds=spec.shutdown_timeout_seconds,
    )
    result = run_managed_service_verification(
        spec, controller=controller, containment_profile=_profile(str(workspace)),
        containment_backend=OCIContainmentBackend(),
    )
    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED, result.reasoning
    assert result.probe_body is not None and result.probe_body.startswith("OUTBOUND-BLOCKED")


def test_contained_managed_service_not_reachable_via_any_published_host_port(tmp_path):
    """No port is ever published to the host at all under this design -
    proven by attempting a direct host-side connect to the service's own
    port number and confirming it fails, while the docker-exec-based probe
    (same test, via the other functions) succeeds against the identical
    service."""
    import socket
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller = ProcessController()
    spec = _contained_spec(str(workspace), port=8123)
    result = run_managed_service_verification(
        spec, controller=controller, containment_profile=_profile(str(workspace)),
        containment_backend=OCIContainmentBackend(),
    )
    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED, result.reasoning

    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", 8123), timeout=1):
            pass


def test_contained_managed_service_leaves_no_surviving_container(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller = ProcessController()
    result = run_managed_service_verification(
        _contained_spec(str(workspace)), controller=controller,
        containment_profile=_profile(str(workspace)), containment_backend=OCIContainmentBackend(),
    )
    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED, result.reasoning

    time.sleep(2)
    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=kriya-oci-", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    assert ps.stdout.strip() == "", f"container(s) survived cleanup: {ps.stdout!r}"


def test_contained_managed_service_start_failure_still_cleans_up(tmp_path):
    """A service that exits immediately (bad command) must still be torn
    down cleanly - no leaked container even on the SERVICE_EXITED_BEFORE_READY
    path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller = ProcessController()
    spec = ManagedServiceVerificationSpec(
        service_command=["python3", "-c", "import sys; sys.exit(1)"],
        cwd=str(workspace),
        readiness=ReadinessSpec(kind="http", port=9999, path="/health"),
        probe=ProbeSpec(port=9999, path="/health"),
        startup_timeout_seconds=5.0, probe_timeout_seconds=5.0, shutdown_timeout_seconds=5.0,
    )
    result = run_managed_service_verification(
        spec, controller=controller, containment_profile=_profile(str(workspace)),
        containment_backend=OCIContainmentBackend(),
    )
    assert result.outcome == ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY

    time.sleep(2)
    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=kriya-oci-", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    assert ps.stdout.strip() == "", f"container(s) survived a start-failure path: {ps.stdout!r}"
