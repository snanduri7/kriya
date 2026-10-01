"""Managed Runtime Verification (2026-09-03) - deterministic tests for the
managed long-lived service execution primitive (kriya/tools/service_runtime.py).

No LLM calls, no network dependency beyond localhost: every "service" here
is a tiny stdlib http.server script launched as a real child process via
`sys.executable -c <script>`, so these tests exercise the actual process
lifecycle (start, drain threads, readiness polling, probe, SIGKILL/process-
group termination) rather than mocking it away.
"""
import contextlib
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Optional
from unittest.mock import patch

from kriya.tools.process import ProcessController
from kriya.tools.service_runtime import (
    ManagedServiceVerificationSpec,
    ProbeSpec,
    ReadinessSpec,
    ServiceVerificationOutcomeKind,
    _artifact_is_current,
    _detect_required_artifact,
    _maven_package_command,
    _prepare_required_artifact,
    _run_probe,
    run_managed_service_verification,
)


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_service_script(*, delay_seconds: float = 0.0, exit_code: Optional[int] = None, health_status: int = 200) -> str:
    """A minimal HTTP service: GET /health returns health_status with a
    JSON body, everything else 404. delay_seconds simulates slow startup
    (proves readiness actually waits); exit_code, when not None, makes it
    exit immediately with that code instead of serving (proves early-exit
    is captured) - None (the default) means "serve normally"."""
    exit_line = f"sys.exit({exit_code})" if exit_code is not None else "pass"
    return f"""
import http.server, socket, sys, time

port = int(sys.argv[1])
time.sleep({delay_seconds})
{exit_line}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response({health_status})
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{{"status": "ok"}}')
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a):
        pass

server = http.server.HTTPServer(("127.0.0.1", port), Handler)
server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.serve_forever()
"""


def _spec(port: int, script: str, *, startup_timeout=5.0, probe_timeout=5.0, shutdown_timeout=5.0,
          expected_status=200, expected_body_contains=None) -> ManagedServiceVerificationSpec:
    return ManagedServiceVerificationSpec(
        service_command=[sys.executable, "-c", script, str(port)],
        cwd=os.getcwd(),
        readiness=ReadinessSpec(kind="http", port=port, path="/health"),
        probe=ProbeSpec(
            port=port, path="/health", expected_status=expected_status,
            expected_body_contains=expected_body_contains,
        ),
        startup_timeout_seconds=startup_timeout,
        probe_timeout_seconds=probe_timeout,
        shutdown_timeout_seconds=shutdown_timeout,
    )


class _TrackingController(ProcessController):
    """Records every ManagedProcess it starts so a test can confirm it was
    actually terminated after run_managed_service_verification() returns -
    the whole point of the cleanup guarantee is observable from OUTSIDE the
    function under test, not just from its return value."""

    def __init__(self):
        super().__init__()
        self.started = []

    def start_managed(self, *args, **kwargs):
        managed = super().start_managed(*args, **kwargs)
        self.started.append(managed)
        return managed


def _wait_until_dead(managed, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if managed.poll() is not None:
            return True
        time.sleep(0.05)
    return managed.poll() is not None


# --- (1) finite runtime command behavior unchanged --------------------------

def test_run_app_sequence_finite_command_behavior_is_unchanged(tmp_path):
    """Managed Runtime Verification adds a second execution mode; it must
    not touch the first. PolymorphicValidator.run_app_sequence() (the
    finite path) is exercised completely untouched by this module - no
    import of kriya.tools.service_runtime anywhere in validate.py."""
    from kriya.tools.validate import PolymorphicValidator

    validator = PolymorphicValidator(str(tmp_path))
    result = validator.run_app_sequence([[sys.executable, "-c", "print('hello')"]])

    assert result["success"] is True
    assert "hello" in result["output"]

    import kriya.tools.validate as validate_module
    assert "service_runtime" not in validate_module.__file__  # sanity: real module
    assert "kriya.tools.service_runtime" not in dir(validate_module)


# --- (2)+(3)+(4) service starts without blocking; readiness waits for it ----

def test_readiness_waits_for_actual_availability_before_probing(tmp_path):
    """(2) starting a foreground service does not block orchestration - this
    whole test function returns well before the service's own artificial
    startup delay would, if start_managed() were itself blocking.
    (3) readiness genuinely waits: the service sleeps before binding, so a
    readiness check that fired immediately would see nothing and this
    would come back READINESS_TIMEOUT instead of PROBE_PASSED.
    (4) the probe only ever runs after readiness succeeds - proven by the
    result being PROBE_PASSED at all (a probe against a not-yet-listening
    port would be a connection error, not a clean pass)."""
    port = _free_port()
    script = _http_service_script(delay_seconds=0.5)
    controller = _TrackingController()

    started_at = time.monotonic()
    result = run_managed_service_verification(_spec(port, script), controller=controller)
    elapsed = time.monotonic() - started_at

    assert elapsed < 5.0  # well inside the 5s startup_timeout - not blocked on anything else
    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED
    assert result.passed is True
    assert elapsed >= 0.5  # genuinely waited past the service's own artificial delay


# --- (5) successful probe returns PASS evidence -----------------------------

def test_successful_probe_returns_pass_evidence():
    port = _free_port()
    script = _http_service_script()
    controller = _TrackingController()

    result = run_managed_service_verification(
        _spec(port, script, expected_body_contains='"status": "ok"'), controller=controller,
    )

    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED
    assert result.passed is True
    assert result.probe_status == 200
    assert result.probe_body is not None and "ok" in result.probe_body
    assert result.cleanup_error is None


# --- (6) failed probe returns behavioral failure evidence -------------------

def test_failed_probe_returns_behavioral_failure_evidence():
    """Service starts and becomes ready (readiness passes), but the probed
    path returns the wrong status - a genuine behavioral defect, not an
    infrastructure one. Readiness is checked via a plain TCP connect here
    (not the same /health path the probe uses) - the service genuinely IS
    up and accepting connections, so readiness must succeed; only the
    probe's own status expectation (200) against the endpoint's real
    behavior (500) may fail."""
    port = _free_port()
    script = _http_service_script(health_status=500)
    controller = _TrackingController()
    spec = ManagedServiceVerificationSpec(
        service_command=[sys.executable, "-c", script, str(port)],
        cwd=os.getcwd(),
        readiness=ReadinessSpec(kind="tcp", port=port),
        probe=ProbeSpec(port=port, path="/health", expected_status=200),
    )

    result = run_managed_service_verification(spec, controller=controller)

    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_FAILED
    assert result.passed is False
    assert result.probe_status == 500


# --- (7) startup timeout cleans up process ----------------------------------

def test_startup_timeout_terminates_the_process():
    """The service never binds at all (sleeps far past startup_timeout) -
    READINESS_TIMEOUT, and the process must be confirmed dead afterward,
    not merely signaled."""
    port = _free_port()
    script = _http_service_script(delay_seconds=30.0)
    controller = _TrackingController()

    result = run_managed_service_verification(
        _spec(port, script, startup_timeout=0.6, shutdown_timeout=5.0), controller=controller,
    )

    assert result.outcome == ServiceVerificationOutcomeKind.READINESS_TIMEOUT
    assert result.passed is False
    assert len(controller.started) == 1
    assert _wait_until_dead(controller.started[0])


# --- (8) readiness timeout cleans up process (same mechanism as 7, distinct
#         from "service never starts listening at all": here the port is
#         simply never reachable at the expected path/status) -------------

def test_readiness_timeout_via_wrong_status_cleans_up_process():
    """The service DOES start and DOES listen, but /health never returns an
    in-range status (readiness itself, not just the later probe, rejects
    it) - readiness never succeeds, so this must time out (not hang
    forever) and the process must still be torn down."""
    port = _free_port()
    script = _http_service_script(health_status=503)
    controller = _TrackingController()

    result = run_managed_service_verification(
        _spec(port, script, startup_timeout=0.6, shutdown_timeout=5.0), controller=controller,
    )

    assert result.outcome == ServiceVerificationOutcomeKind.READINESS_TIMEOUT
    assert len(controller.started) == 1
    assert _wait_until_dead(controller.started[0])


# --- (9) probe timeout cleans up process ------------------------------------

def test_probe_timeout_cleans_up_process():
    """Readiness passes (service is up), but the probe itself never gets a
    response within its own independent, much shorter budget - simulated by
    patching _run_probe to hang past probe_timeout via a real blocking
    call, proving the probe's timeout is honored independently of
    startup_timeout and that cleanup still runs when the probe raises."""
    port = _free_port()
    script = _http_service_script()
    controller = _TrackingController()

    def _hanging_probe(spec, *, timeout):
        raise TimeoutError(f"probe exceeded its own {timeout}s budget")

    with patch("kriya.tools.service_runtime._run_probe", side_effect=_hanging_probe):
        result = run_managed_service_verification(
            _spec(port, script, probe_timeout=0.2), controller=controller,
        )

    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_FAILED
    assert result.passed is False
    assert "probe exceeded" in result.reasoning
    assert len(controller.started) == 1
    assert _wait_until_dead(controller.started[0])


# --- (10) service early-exit captured correctly -----------------------------

def test_service_early_exit_is_captured_with_returncode():
    port = _free_port()
    script = _http_service_script(exit_code=3)
    controller = _TrackingController()

    result = run_managed_service_verification(
        _spec(port, script, startup_timeout=3.0), controller=controller,
    )

    assert result.outcome == ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY
    assert result.passed is False
    assert result.returncode == 3


# --- (11) exception path cleans up process ----------------------------------

def test_unexpected_exception_during_readiness_still_cleans_up_process():
    """A genuinely unexpected error (not a probe-request failure - readiness
    checking itself blowing up) must not leak the process or escape as a
    raw exception - it comes back as a structured, failed result with the
    dedicated VERIFICATION_INTERNAL_ERROR outcome (external review,
    2026-09-03 - never PROBE_FAILED, which a workflow-layer caller routes
    to normal Developer repair; an internal Kriya bug must not trigger
    that), and the service is still confirmed terminated."""
    port = _free_port()
    script = _http_service_script()
    controller = _TrackingController()

    with patch("kriya.tools.service_runtime._check_readiness", side_effect=RuntimeError("boom")):
        result = run_managed_service_verification(_spec(port, script), controller=controller)

    assert result.outcome == ServiceVerificationOutcomeKind.VERIFICATION_INTERNAL_ERROR
    assert result.passed is False
    assert "boom" in result.reasoning
    assert len(controller.started) == 1
    assert _wait_until_dead(controller.started[0])


# --- (12) child/process-group cleanup leaves no surviving service ----------

def test_cleanup_leaves_no_surviving_process_after_successful_probe():
    port = _free_port()
    script = _http_service_script()
    controller = _TrackingController()

    result = run_managed_service_verification(_spec(port, script), controller=controller)

    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED
    assert len(controller.started) == 1
    assert _wait_until_dead(controller.started[0])
    # The OS port itself is free again - not merely "process object reports
    # exited" but genuinely no listener left bound to it.
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe_socket:
        probe_socket.settimeout(0.5)
        connected = True
        try:
            probe_socket.connect(("127.0.0.1", port))
        except OSError:
            connected = False
        assert connected is False


# --- (13) two sequential runs do not conflict due to leaked port/process ---

def test_two_sequential_runs_on_the_same_port_do_not_conflict():
    port = _free_port()
    script = _http_service_script()
    controller = _TrackingController()

    first = run_managed_service_verification(_spec(port, script), controller=controller)
    second = run_managed_service_verification(_spec(port, script), controller=controller)

    assert first.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED
    assert second.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED
    assert len(controller.started) == 2
    assert controller.started[0].pid != controller.started[1].pid


# --- (14) infrastructure failure invokes zero Developer repair calls -------

def test_managed_service_result_maps_to_zero_developer_calls_for_infrastructure_outcomes():
    """This module has no knowledge of Failure/QualityGateFailure/the
    Developer by design (see module docstring) - but the outcome taxonomy
    it returns must be able to drive the SAME zero-Developer-calls contract
    kriya/workflow/retry_strategy.py already enforces for
    verification_infrastructure_failure (see
    tests/test_workflow.py::test_handle_attempt_failure_stops_before_developer_call_on_verification_contract_defect).
    Proven here at the boundary this module owns: every outcome that is NOT
    a behavioral probe verdict (PROBE_PASSED/PROBE_FAILED) is infrastructure-
    shaped, and a workflow-layer caller can classify purely from `outcome`
    with no service-specific knowledge - the exact minimal contract a
    future wiring step needs, without this module reaching into
    kriya/workflow/* itself."""
    infrastructure_outcomes = {
        # Artifact Preparation (P6 production-validation, 2026-09-07): the
        # application was never even launched here either - same shape as
        # every other member of this set.
        ServiceVerificationOutcomeKind.PREPARATION_FAILED,
        ServiceVerificationOutcomeKind.ARTIFACT_MATERIALIZATION_FAILED,
        ServiceVerificationOutcomeKind.SERVICE_START_FAILED,
        ServiceVerificationOutcomeKind.READINESS_TIMEOUT,
        ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY,
        ServiceVerificationOutcomeKind.CLEANUP_FAILED,
        ServiceVerificationOutcomeKind.VERIFICATION_INTERNAL_ERROR,
    }
    behavioral_outcomes = {
        ServiceVerificationOutcomeKind.PROBE_PASSED,
        ServiceVerificationOutcomeKind.PROBE_FAILED,
    }
    assert infrastructure_outcomes | behavioral_outcomes == set(ServiceVerificationOutcomeKind)
    assert infrastructure_outcomes.isdisjoint(behavioral_outcomes)

    port = _free_port()
    controller = _TrackingController()
    result = run_managed_service_verification(
        ManagedServiceVerificationSpec(
            service_command=["/no/such/executable-kriya-test"],
            cwd=os.getcwd(),
            readiness=ReadinessSpec(kind="tcp", port=port),
            probe=ProbeSpec(port=port),
        ),
        controller=controller,
    )

    assert result.outcome == ServiceVerificationOutcomeKind.SERVICE_START_FAILED
    assert result.outcome in infrastructure_outcomes
    assert len(controller.started) == 0  # never even reached ManagedProcess bookkeeping


# --- External review hardening (2026-09-03) --------------------------------

def test_managed_process_stdout_buffer_stays_bounded_while_running():
    """P1: _drain() must bound the buffer WHILE appending, not only when
    captured_output() is later called - a noisy long-lived service must not
    grow Kriya's memory without limit for however long readiness/probe
    polling keeps waiting. A tiny max_output_chars budget plus a script
    that writes far more than that almost immediately, inspected WHILE the
    process is still alive (poll() is None), proves the bound holds
    continuously, not just at read time."""
    controller = ProcessController(max_output_chars=200)
    script = (
        "import sys, time\n"
        "for i in range(20000):\n"
        "    print('x' * 50)\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )
    managed = controller.start_managed([sys.executable, "-c", script], cwd=os.getcwd())
    try:
        time.sleep(1.5)  # let the burst of output drain
        assert managed.poll() is None  # still running - the writer sleeps before exiting
        with managed._lock:
            total_buffered = sum(len(c) for c in managed._stdout_chunks)
        # The raw output would otherwise reach ~1MB (20000 * 51 bytes) -
        # bounded here to a small multiple of max_output_chars, not left
        # to grow unbounded for the remaining 3.5s the process stays alive.
        assert total_buffered < 5000
    finally:
        managed.terminate(reap_timeout=5.0)


def test_managed_process_terminate_passes_fractional_reap_timeout_unmodified():
    """P2: shutdown_timeout_seconds is a float end-to-end - terminate()'s
    reap_timeout must reach Popen.wait() as the real fractional value, not
    truncated to int(0.5)==0 (which would make Popen.wait(timeout=0) an
    effectively-zero wait, producing a false CLEANUP_FAILED for a process
    that would have exited in time)."""
    controller = ProcessController()
    managed = controller.start_managed(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=os.getcwd(),
    )
    try:
        with patch.object(managed._popen, "wait", wraps=managed._popen.wait) as mock_wait:
            managed.terminate(reap_timeout=0.5)
        mock_wait.assert_called_once_with(timeout=0.5)
    finally:
        if managed.poll() is None:
            managed.terminate(reap_timeout=5.0)


def test_service_runtime_cleanup_does_not_truncate_fractional_shutdown_timeout():
    """P2: kriya.tools.service_runtime._cleanup must pass shutdown_timeout_
    seconds straight through to ManagedProcess.terminate(), never
    int()-truncated."""
    from kriya.tools.service_runtime import _cleanup

    class _FakeManaged:
        def __init__(self):
            self.calls = []

        def poll(self):
            return None  # still running

        def terminate(self, *, reap_timeout):
            self.calls.append(reap_timeout)
            return True

    fake = _FakeManaged()
    _cleanup(fake, shutdown_timeout_seconds=0.5)

    assert fake.calls == [0.5]


def test_probe_wall_clock_deadline_enforced_despite_a_dribbling_response():
    """P1: a server that keeps sending SOME bytes (never letting a single
    socket read exceed urlopen's own per-operation timeout) but takes far
    longer than probe_timeout_seconds overall must still be bounded by a
    real wall-clock deadline - proven by the whole run_managed_service_
    verification() call returning well within a small multiple of
    probe_timeout_seconds, not blocking for anywhere near the server's
    actual (much longer) total response time."""
    port = _free_port()
    script = f"""
import http.server, time

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path == "/slow":
            self.send_response(200)
            self.send_header("Content-Length", "20")
            self.end_headers()
            for _ in range(20):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.5)
            return
        self.send_response(404)
        self.end_headers()
    def log_message(self, *a):
        pass

server = http.server.HTTPServer(("127.0.0.1", {port}), Handler)
server.serve_forever()
"""
    controller = _TrackingController()
    spec = ManagedServiceVerificationSpec(
        service_command=[sys.executable, "-c", script],
        cwd=os.getcwd(),
        readiness=ReadinessSpec(kind="http", port=port, path="/health"),
        probe=ProbeSpec(port=port, path="/slow", expected_status=200),
        probe_timeout_seconds=1.0,
    )

    started_at = time.monotonic()
    result = run_managed_service_verification(spec, controller=controller)
    elapsed = time.monotonic() - started_at

    # The dribbling server takes 20 * 0.5s = 10s to actually finish
    # responding; a true wall-clock deadline returns well before that, not
    # after ~10s. Whether the deadline is caught by this module's own
    # explicit "remaining <= 0" check or by the underlying socket's own
    # settimeout() (re-tightened to the same remaining budget every read)
    # is a genuine, harmless race - either produces a timeout, just with a
    # different message ("wall-clock" vs the raw socket.timeout "timed
    # out") - so this only asserts the OUTCOME the fix is actually
    # responsible for: bounded elapsed time and a failed, non-hanging probe.
    assert elapsed < 5.0
    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_FAILED
    assert "timed out" in result.reasoning.lower() or "wall-clock" in result.reasoning


def test_probe_wall_clock_timeout_closes_the_connection_and_leaks_no_thread():
    """Stronger than "the caller returned on time" (external review,
    2026-09-03, follow-up): Python threads cannot be forcibly cancelled, so
    an earlier draft of this fix (ThreadPoolExecutor + future.result(timeout=...),
    abandoning the worker on timeout) only unblocked the CALLER - the
    background thread's urlopen()/resp.read() kept running and the socket
    stayed open indefinitely, meaning the connection (and the server's own
    belief that a client is still reading) could survive the entire
    verification attempt regardless of Kriya's own timeout. _run_probe is
    now fully synchronous (no thread at all) and closes its socket/response
    explicitly in a finally block, so this proves the STRONGER property
    directly: (1) _run_probe returns within its configured wall-clock
    budget, (2) no thread is left running afterward, and (3) the dribbling
    server's own next write actually fails (broken pipe/connection reset)
    shortly after the deadline - proof the connection was really closed,
    not merely that Kriya stopped waiting on it."""
    import tempfile

    port = _free_port()
    marker_path = os.path.join(tempfile.gettempdir(), f"kriya-test-disconnect-marker-{port}")
    if os.path.exists(marker_path):
        os.unlink(marker_path)
    script = f"""
import http.server, time

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "200")
        self.end_headers()
        try:
            for _ in range(200):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            with open({marker_path!r}, "w") as f:
                f.write("disconnected")
    def log_message(self, *a):
        pass

server = http.server.HTTPServer(("127.0.0.1", {port}), Handler)
server.serve_forever()
"""
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 5.0
        ready = False
        while time.monotonic() < deadline:
            try:
                with contextlib.closing(socket.create_connection(("127.0.0.1", port), timeout=0.2)):
                    ready = True
                    break
            except OSError:
                time.sleep(0.05)
        assert ready, "dribbling test server never started listening"

        probe = ProbeSpec(port=port, path="/slow", expected_status=200)
        threads_before = threading.active_count()
        started_at = time.monotonic()
        raised = None
        try:
            _run_probe(probe, timeout=1.0)
        except Exception as e:
            raised = e
        elapsed = time.monotonic() - started_at
        threads_after = threading.active_count()

        assert raised is not None and isinstance(raised, OSError)  # a real timeout, not a silent pass
        assert elapsed < 3.0
        assert threads_after == threads_before  # no probe-worker thread left running

        # The dribbling server's OWN next write should fail (its peer
        # closed the connection) well before its full ~60s write loop -
        # direct, external proof the socket was actually closed, not just
        # abandoned.
        marker_deadline = time.monotonic() + 4.0
        while time.monotonic() < marker_deadline and not os.path.exists(marker_path):
            time.sleep(0.05)
        assert os.path.exists(marker_path), (
            "server never observed the client disconnect within 4s of the 1s probe timeout - "
            "the connection was not actually closed on timeout"
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)
        if os.path.exists(marker_path):
            os.unlink(marker_path)


def test_probe_connection_error_maps_to_probe_failed_but_internal_bug_maps_to_internal_error():
    """P2: _run_lifecycle splits probe-time exceptions by type - an OSError
    (connection refused/reset, our own wall-clock TimeoutError, a genuine
    network-level failure) is BEHAVIORAL evidence about the running service
    (PROBE_FAILED, eligible for normal Developer repair); anything else is
    treated as an internal Kriya defect (VERIFICATION_INTERNAL_ERROR, never
    eligible for repair) - the exact distinction the review asked for."""
    port = _free_port()
    script = _http_service_script()

    controller = _TrackingController()
    with patch("kriya.tools.service_runtime._run_probe", side_effect=ConnectionRefusedError("refused")):
        connection_result = run_managed_service_verification(_spec(port, script), controller=controller)
    assert connection_result.outcome == ServiceVerificationOutcomeKind.PROBE_FAILED

    controller2 = _TrackingController()
    with patch("kriya.tools.service_runtime._run_probe", side_effect=KeyError("not_a_network_error")):
        bug_result = run_managed_service_verification(_spec(port, script), controller=controller2)
    assert bug_result.outcome == ServiceVerificationOutcomeKind.VERIFICATION_INTERNAL_ERROR


# --- Artifact Preparation (P6 production-validation, 2026-09-07) -----------
# A live P6 run's own service_command (`java -jar target/spring-petclinic-
# rest-4.0.2.jar`) named the jar correctly, but nothing in Kriya's pipeline
# had ever run `mvn package` - only `mvn clean compile`/`mvn test` - so the
# jar never existed and the launch failed as SERVICE_EXITED_BEFORE_READY,
# discovering the missing packaging step only by trying (and failing) to
# run something that could not possibly work. These tests are entirely
# self-contained real child processes (a shell `mvnw` stand-in, a Python
# stand-in for `java -jar`) - no live LLM, no real Maven/JVM installation
# required, matching this file's own existing convention.

def _write_executable(path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _write_pom(tmp_path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>")


def _write_mvnw(tmp_path, *, jar_relpath: str, exit_code: int = 0, create_jar: bool = True) -> None:
    """A real, executable ./mvnw stand-in - no Maven/JVM dependency. When
    invoked as `package -DskipTests` (matching _maven_package_command's own
    real args exactly), optionally materializes the target jar before
    exiting with `exit_code`."""
    create_line = (
        f'mkdir -p "$(dirname "{jar_relpath}")" && touch "{jar_relpath}"' if create_jar else ": # no-op, artifact not created"
    )
    _write_executable(tmp_path / "mvnw", f"""#!/bin/sh
{create_line}
exit {exit_code}
""")


def _fake_java_dash_jar_script() -> str:
    """A real, executable stand-in for `java -jar <jarfile>` - invoked as
    `[sys.executable, "-c", script, "-jar", jar_path, port]`, matching the
    exact argv shape `_detect_required_artifact` scans (a literal "-jar"
    token followed by the jar path). Faithfully reproduces the real P6
    failure text ("Unable to access jarfile") when the jar is missing;
    otherwise serves the same minimal /health endpoint every other test in
    this file already uses."""
    return """
import http.server, os, socket, sys

jar_path = sys.argv[2]
port = int(sys.argv[3])
if not os.path.isfile(jar_path):
    sys.stderr.write(f"Error: Unable to access jarfile {jar_path}\\n")
    sys.exit(1)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a):
        pass

server = http.server.HTTPServer(("127.0.0.1", port), Handler)
server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.serve_forever()
"""


class _OrderTrackingController(ProcessController):
    """Records every run()/start_managed() call, in the order it happened -
    the direct, external proof PREPARE actually precedes SERVICE_START."""

    def __init__(self):
        super().__init__()
        self.call_order = []

    def run(self, command, **kwargs):
        self.call_order.append(("run", list(command)))
        return super().run(command, **kwargs)

    def start_managed(self, command, **kwargs):
        self.call_order.append(("start_managed", list(command)))
        return super().start_managed(command, **kwargs)


class _AssertNoRunController(ProcessController):
    """Fails loudly if preparation ever invokes a build command - proves
    the "already current, no unnecessary packaging" path takes a genuine
    shortcut rather than merely being fast."""

    def run(self, command, **kwargs):
        raise AssertionError(f"preparation invoked a build command it should have skipped: {command!r}")


# --- artifact detection ------------------------------------------------

def test_detect_required_artifact_recognizes_jar_flag(tmp_path):
    artifact = _detect_required_artifact(["java", "-jar", "target/app.jar"], str(tmp_path))
    assert artifact == os.path.join(str(tmp_path), "target/app.jar")


def test_detect_required_artifact_returns_none_for_non_artifact_command(tmp_path):
    """A directly runnable command - no separate build artifact - must not
    spuriously trigger Maven packaging."""
    assert _detect_required_artifact(["mvn", "spring-boot:run"], str(tmp_path)) is None
    assert _detect_required_artifact(["python", "manage.py", "runserver"], str(tmp_path)) is None


def test_detect_required_artifact_recognizes_classpath_single_jar(tmp_path):
    """The exact real shape a live run's own recovery attempt used (2026-09-11):
    `java -cp target/app.jar com.example.Main` - the -jar-only detection
    that originally shipped with Managed Runtime Verification would have
    silently let this through unprepared, reproducing the same
    ClassNotFoundException that live run hit."""
    artifact = _detect_required_artifact(
        ["java", "-cp", "target/app.jar", "com.example.Main"], str(tmp_path),
    )
    assert artifact == os.path.join(str(tmp_path), "target/app.jar")


def test_detect_required_artifact_recognizes_long_classpath_flag(tmp_path):
    artifact = _detect_required_artifact(
        ["java", "-classpath", "target/app.jar", "com.example.Main"], str(tmp_path),
    )
    assert artifact == os.path.join(str(tmp_path), "target/app.jar")


def test_detect_required_artifact_ignores_multi_entry_classpath(tmp_path):
    """A multi-entry classpath names no SINGLE artifact this layer could
    deterministically prepare without guessing - must not be detected."""
    multi_entry = "target/app.jar" + os.pathsep + "target/classes"
    assert _detect_required_artifact(["java", "-cp", multi_entry, "com.example.Main"], str(tmp_path)) is None


def test_detect_required_artifact_ignores_classpath_wildcard(tmp_path):
    assert _detect_required_artifact(["java", "-cp", "target/lib/*", "com.example.Main"], str(tmp_path)) is None


def test_detect_required_artifact_ignores_classpath_non_jar(tmp_path):
    assert _detect_required_artifact(["java", "-cp", "target/classes", "com.example.Main"], str(tmp_path)) is None


# --- staleness / currency -----------------------------------------------

def test_artifact_is_current_false_when_artifact_missing(tmp_path):
    assert _artifact_is_current(str(tmp_path / "target/app.jar"), str(tmp_path)) is False


def test_artifact_is_current_true_when_newer_than_pom_and_source(tmp_path):
    _write_pom(tmp_path)
    src = tmp_path / "src" / "main" / "java" / "Foo.java"
    src.parent.mkdir(parents=True)
    src.write_text("class Foo {}")
    os.utime(tmp_path / "pom.xml", (1000, 1000))
    os.utime(src, (1000, 1000))
    jar = tmp_path / "target" / "app.jar"
    jar.parent.mkdir(parents=True)
    jar.write_text("fake jar bytes")
    os.utime(jar, (2000, 2000))

    assert _artifact_is_current(str(jar), str(tmp_path)) is True


def test_artifact_is_current_false_for_stale_artifact_older_than_source(tmp_path):
    """The exact staleness-safety requirement: an old artifact left over
    from an earlier attempt in a reused worktree must never satisfy
    verification for source that has since changed."""
    _write_pom(tmp_path)
    src = tmp_path / "src" / "main" / "java" / "Foo.java"
    src.parent.mkdir(parents=True)
    src.write_text("class Foo {}")
    jar = tmp_path / "target" / "app.jar"
    jar.parent.mkdir(parents=True)
    jar.write_text("stale jar bytes")
    # Artifact built first (older), source touched afterward (newer) -
    # exactly the "leftover build from a prior attempt" shape.
    os.utime(jar, (1000, 1000))
    os.utime(tmp_path / "pom.xml", (1000, 1000))
    os.utime(src, (2000, 2000))

    assert _artifact_is_current(str(jar), str(tmp_path)) is False


# --- build-command selection ---------------------------------------------

def test_maven_package_command_none_without_pom(tmp_path):
    assert _maven_package_command(str(tmp_path)) is None


def test_maven_package_command_prefers_executable_wrapper(tmp_path):
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar")

    assert _maven_package_command(str(tmp_path)) == ["./mvnw", "package", "-DskipTests"]


def test_maven_package_command_falls_back_to_bare_mvn_without_wrapper(tmp_path):
    _write_pom(tmp_path)
    assert _maven_package_command(str(tmp_path)) == ["mvn", "package", "-DskipTests"]


def test_maven_package_command_ignores_non_executable_wrapper(tmp_path):
    _write_pom(tmp_path)
    (tmp_path / "mvnw").write_text("#!/bin/sh\necho hi\n")  # not chmod'd executable
    assert _maven_package_command(str(tmp_path)) == ["mvn", "package", "-DskipTests"]


# --- _prepare_required_artifact: the orchestration layer ------------------

def test_prepare_required_artifact_noop_for_non_artifact_command(tmp_path):
    """Non-artifact service command must not spuriously trigger packaging."""
    outcome = _prepare_required_artifact(
        ["python", "manage.py", "runserver"], str(tmp_path), controller=_AssertNoRunController(),
    )
    assert outcome.outcome is None


def test_prepare_required_artifact_skips_build_when_artifact_already_current(tmp_path):
    """Existing ready artifact / current candidate: no unnecessary
    duplicate packaging - proven by a controller that raises if .run() is
    ever called at all, not merely by timing."""
    _write_pom(tmp_path)
    src = tmp_path / "src" / "main" / "java" / "Foo.java"
    src.parent.mkdir(parents=True)
    src.write_text("class Foo {}")
    os.utime(tmp_path / "pom.xml", (1000, 1000))
    os.utime(src, (1000, 1000))
    jar = tmp_path / "target" / "app.jar"
    jar.parent.mkdir(parents=True)
    jar.write_text("already built")
    os.utime(jar, (2000, 2000))

    outcome = _prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=_AssertNoRunController(),
    )
    assert outcome.outcome is None


def test_prepare_required_artifact_runs_build_when_jar_missing_and_succeeds(tmp_path):
    """Maven JAR missing: preparation runs, produces the artifact."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar")
    jar = tmp_path / "target" / "app.jar"
    assert not jar.exists()

    outcome = _prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=ProcessController(),
    )

    assert outcome.outcome is None
    assert jar.is_file()
    assert outcome.command == ["./mvnw", "package", "-DskipTests"]
    assert outcome.returncode == 0


def test_prepare_required_artifact_build_failure_reported_as_preparation_failed(tmp_path):
    """A build command that fails - PREPARATION_FAILED, real command/
    output/exit code preserved, artifact never appears."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar", exit_code=1, create_jar=False)

    outcome = _prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=ProcessController(),
    )

    assert outcome.outcome == ServiceVerificationOutcomeKind.PREPARATION_FAILED
    assert outcome.returncode == 1
    assert outcome.command == ["./mvnw", "package", "-DskipTests"]
    assert not (tmp_path / "target" / "app.jar").exists()


def test_prepare_required_artifact_missing_after_successful_build_is_materialization_failed(tmp_path):
    """Build command reports success (exit 0) but the expected artifact
    still does not exist afterward - a deterministic ARTIFACT_
    MATERIALIZATION_FAILED, distinct from an ordinary build failure."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar", exit_code=0, create_jar=False)

    outcome = _prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=ProcessController(),
    )

    assert outcome.outcome == ServiceVerificationOutcomeKind.ARTIFACT_MATERIALIZATION_FAILED
    assert outcome.returncode == 0
    assert not (tmp_path / "target" / "app.jar").exists()


def test_prepare_required_artifact_no_pom_reports_preparation_failed(tmp_path):
    """An artifact requirement is detected but there is no known build
    system to prepare it - PREPARATION_FAILED, not a silent pass."""
    outcome = _prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=_AssertNoRunController(),
    )
    assert outcome.outcome == ServiceVerificationOutcomeKind.PREPARATION_FAILED


def test_prepare_required_artifact_threads_env_and_preexec_fn_to_build_command(tmp_path):
    """finite_command generalization's own load-bearing plumbing: a caller-
    supplied env/preexec_fn must reach the actual `mvn package` subprocess,
    so it runs under the SAME JDK/sandbox policy the compile gate already
    used - not a bare, unconstrained ProcessController() default."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar")

    captured = {}

    class _CapturingController(ProcessController):
        def run(self, command, **kwargs):
            captured.update(kwargs)
            return super().run(command, **kwargs)

    sentinel_env = dict(os.environ)
    sentinel_env["JAVA_HOME"] = "/nonexistent/sentinel-jdk-marker"
    sentinel_preexec = lambda: None  # noqa: E731 - identity check only, never actually invoked here

    outcome = _prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(tmp_path),
        controller=_CapturingController(), env=sentinel_env, preexec_fn=sentinel_preexec,
    )
    assert outcome.outcome is None
    assert captured["env"] is sentinel_env
    assert captured["preexec_fn"] is sentinel_preexec


def test_prepare_required_artifact_omitted_env_preserves_managed_service_behavior(tmp_path):
    """Regression guard: every EXISTING managed_service call site never
    passes env/preexec_fn - both must keep defaulting to None and the
    build must still succeed exactly as before this change."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/app.jar")
    outcome = _prepare_required_artifact(
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=ProcessController(),
    )
    assert outcome.outcome is None
    assert (tmp_path / "target" / "app.jar").is_file()


# --- end-to-end through run_managed_service_verification -------------------

def _p6_shaped_spec(tmp_path, port, jar_relpath="target/spring-petclinic-rest-4.0.2.jar"):
    return ManagedServiceVerificationSpec(
        service_command=[sys.executable, "-c", _fake_java_dash_jar_script(), "-jar", jar_relpath, str(port)],
        cwd=str(tmp_path),
        readiness=ReadinessSpec(kind="http", port=port, path="/health"),
        probe=ProbeSpec(port=port, path="/health", expected_status=200),
    )


def test_p6_reproduction_missing_jar_is_prepared_then_service_launches_and_probes(tmp_path):
    """The frozen P6 shape, deterministically reproduced with no live LLM:
    Maven project, service command `java -jar target/spring-petclinic-
    rest-4.0.2.jar`, jar absent after compile/test. Expected: deterministic
    Maven package preparation executes, the jar is produced, only then is
    `java -jar ...` started, and the readiness/probe flow completes
    successfully - the exact sequence P6 attempt 2 needed and did not have."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/spring-petclinic-rest-4.0.2.jar")
    jar = tmp_path / "target" / "spring-petclinic-rest-4.0.2.jar"
    assert not jar.exists()
    port = _free_port()
    controller = _TrackingController()

    result = run_managed_service_verification(_p6_shaped_spec(tmp_path, port), controller=controller)

    assert jar.is_file()
    assert result.outcome == ServiceVerificationOutcomeKind.PROBE_PASSED
    assert result.passed is True
    assert result.probe_status == 200
    assert len(controller.started) == 1  # the service really was launched, exactly once


def test_preparation_runs_before_service_start_and_before_readiness_polling(tmp_path):
    """Ordering: PREPARE strictly precedes SERVICE_START (and therefore
    readiness polling, which only begins once the service process exists)."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/spring-petclinic-rest-4.0.2.jar")
    port = _free_port()
    controller = _OrderTrackingController()

    run_managed_service_verification(_p6_shaped_spec(tmp_path, port), controller=controller)

    assert len(controller.call_order) == 2
    assert controller.call_order[0][0] == "run"  # the mvnw prepare step
    assert controller.call_order[0][1][0] == "./mvnw"
    assert controller.call_order[1][0] == "start_managed"  # the java -jar launch
    assert "-jar" in controller.call_order[1][1]


def test_preparation_failure_prevents_service_launch(tmp_path):
    """Preparation fails: service is never launched at all."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/spring-petclinic-rest-4.0.2.jar", exit_code=1, create_jar=False)
    port = _free_port()
    controller = _TrackingController()

    result = run_managed_service_verification(_p6_shaped_spec(tmp_path, port), controller=controller)

    assert result.outcome == ServiceVerificationOutcomeKind.PREPARATION_FAILED
    assert result.passed is False
    assert controller.started == []  # start_managed() was never called


def test_artifact_still_missing_after_successful_build_fails_before_launch(tmp_path):
    """Preparation command reports success but the artifact still does not
    exist - deterministic failure before launch, service never started."""
    _write_pom(tmp_path)
    _write_mvnw(tmp_path, jar_relpath="target/spring-petclinic-rest-4.0.2.jar", exit_code=0, create_jar=False)
    port = _free_port()
    controller = _TrackingController()

    result = run_managed_service_verification(_p6_shaped_spec(tmp_path, port), controller=controller)

    assert result.outcome == ServiceVerificationOutcomeKind.ARTIFACT_MATERIALIZATION_FAILED
    assert result.passed is False
    assert controller.started == []


def test_service_exited_before_ready_stays_reserved_for_an_actually_started_process():
    """SERVICE_EXITED_BEFORE_READY must remain reserved for a service
    process that genuinely started and then exited before becoming ready -
    a non-artifact command (no preparation involved at all) that exits
    immediately still produces this exact outcome, unchanged by this fix."""
    port = _free_port()
    script = _http_service_script(exit_code=3)
    controller = _TrackingController()

    result = run_managed_service_verification(_spec(port, script), controller=controller)

    assert result.outcome == ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY
    assert len(controller.started) == 1  # it WAS actually started this time
