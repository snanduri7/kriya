"""Managed long-lived service runtime verification (Managed Runtime
Verification, 2026-09-03).

Kriya's only runtime-verification execution mode until now was
PolymorphicValidator.run_app_sequence() / ProcessController.run() - a
strictly finite, blocking command sequence. That cannot correctly verify a
foreground service: `python manage.py runserver ... && curl ...` blocks on
the first (never-exiting) command forever, so the probe never runs -
confirmed empirically in PRV-17 hardening (see
tests/test_workflow.py::test_run_app_sequence_foreground_service_start_blocks_the_subsequent_probe,
which pins down that this module does NOT change run_app_sequence's own,
still-correct-for-finite-commands behavior).

This module adds the second execution mode: start service -> wait for
readiness -> execute a bounded probe -> collect evidence -> terminate the
service/process group -> cleanup. It is a pure execution-layer primitive -
it decides HOW an already-approved runtime verification runs, never WHETHER
one is due (that stays entirely a workflow-layer/MA6 ownership decision, see
kriya/workflow/attempt.py). It has no knowledge of Failure, QualityGateFailure,
retries, or MA8/MA9 - it returns a plain, structured
ManagedServiceVerificationResult and nothing else; a workflow-layer caller
maps that result onto Kriya's existing failure/recovery architecture exactly
the way it already maps a run_app_sequence() dict today. Deliberately not
wired to RunVerifierAgent/the Planner yet - see this module's own tests for
the standalone lifecycle guarantees; representing `service`/`readiness`/
`probe` in the judge's own output is a separate, later change.

Process lifecycle is built entirely on ProcessController.start_managed()/
ManagedProcess (kriya/tools/process.py) - the SAME process-group isolation
and kill primitive run()'s own timeout path already uses, just observed
while still running instead of waited on synchronously. Readiness and probe
support HTTP today (kind="http") plus a bare TCP-connect readiness check
(kind="tcp") for a service with no HTTP surface at all; both are a single
dispatch point (_check_readiness/_run_probe) so a future adapter (a process-
output marker, a gRPC health check) is a new branch, not a redesign.
"""
from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from socket import create_connection
from typing import Dict, List, Optional, Tuple

from kriya.tools.process import ManagedProcess, ProcessController


class ServiceVerificationOutcomeKind(str, Enum):
    SERVICE_START_FAILED = "SERVICE_START_FAILED"
    READINESS_TIMEOUT = "READINESS_TIMEOUT"
    SERVICE_EXITED_BEFORE_READY = "SERVICE_EXITED_BEFORE_READY"
    PROBE_FAILED = "PROBE_FAILED"
    PROBE_PASSED = "PROBE_PASSED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    # External review, 2026-09-03: a genuinely unexpected internal/
    # orchestration error (not a probe-request failure - see _run_lifecycle's
    # own split below) must never be reported as PROBE_FAILED. PROBE_FAILED
    # is a workflow-layer signal that routes to normal Developer repair
    # (kriya/workflow/attempt.py); an internal Kriya defect getting that
    # treatment would start rewriting application code to "fix" a bug that
    # was never in the application at all.
    VERIFICATION_INTERNAL_ERROR = "VERIFICATION_INTERNAL_ERROR"


@dataclass(frozen=True)
class ReadinessSpec:
    """How to tell the service is up enough to probe. kind="tcp" only
    proves a listener exists on the port; kind="http" additionally proves
    it's answering HTTP requests within the expected status range - use
    "tcp" for a non-HTTP service, "http" whenever the service under test
    is itself an HTTP server (matches the probe it's about to receive)."""

    kind: str  # "tcp" | "http"
    host: str = "127.0.0.1"
    port: int = 0
    path: str = "/"
    expected_status_min: int = 200
    expected_status_max: int = 399
    poll_interval_seconds: float = 0.1
    request_timeout_seconds: float = 1.0


@dataclass(frozen=True)
class ProbeSpec:
    """The bounded, single behavioral check run once the service is ready.
    Only kind="http" is implemented - explicitly sufficient for the first
    implementation per the Managed Runtime Verification spec."""

    kind: str = "http"
    method: str = "GET"
    host: str = "127.0.0.1"
    port: int = 0
    path: str = "/"
    expected_status: int = 200
    expected_body_contains: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[str] = None


@dataclass(frozen=True)
class ManagedServiceVerificationSpec:
    """The smallest execution-layer structure representing a managed
    service verification request - deliberately independent of
    kriya/workflow/plan_schema.py's VerificationMethod (that schema answers
    WHETHER/WHAT a subtask must verify; this one answers HOW to actually
    run a specific already-approved service check) and free of any
    Django/framework-specific field."""

    service_command: List[str]
    cwd: str
    readiness: ReadinessSpec
    probe: ProbeSpec
    env: Optional[Dict[str, str]] = None
    startup_timeout_seconds: float = 20.0
    probe_timeout_seconds: float = 10.0
    shutdown_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class ManagedServiceVerificationResult:
    outcome: ServiceVerificationOutcomeKind
    passed: bool
    reasoning: str
    stdout: str
    stderr: str
    returncode: Optional[int] = None
    probe_status: Optional[int] = None
    probe_body: Optional[str] = None
    cleanup_error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "passed": self.passed,
            "reasoning": self.reasoning,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "probe_status": self.probe_status,
            "probe_body": self.probe_body,
            "cleanup_error": self.cleanup_error,
        }


def _check_readiness(spec: ReadinessSpec) -> bool:
    if spec.kind == "tcp":
        try:
            with create_connection((spec.host, spec.port), timeout=spec.request_timeout_seconds):
                return True
        except OSError:
            return False
    if spec.kind == "http":
        url = f"http://{spec.host}:{spec.port}{spec.path}"
        try:
            with urllib.request.urlopen(url, timeout=spec.request_timeout_seconds) as resp:
                return spec.expected_status_min <= resp.status <= spec.expected_status_max
        except urllib.error.HTTPError as e:
            return spec.expected_status_min <= e.code <= spec.expected_status_max
        except OSError:
            return False
    raise ValueError(f"Unsupported readiness kind: {spec.kind!r}")


_PROBE_READ_CHUNK_BYTES = 65536


def _run_probe(spec: ProbeSpec, *, timeout: float) -> Tuple[Optional[int], Optional[str], bool, str]:
    """Correctness fix (2026-09-03, post-hardening review): the first draft
    of this function bounded the overall probe with a ThreadPoolExecutor +
    future.result(timeout=...) - that stops the CALLER waiting, but Python
    threads cannot be forcibly cancelled, so the abandoned worker's
    urlopen()/resp.read() call kept running and the socket stayed open
    indefinitely, exactly the "probe worker survives verification" leak a
    review caught. Rebuilt synchronously instead: a single http.client.
    HTTPConnection, read in bounded chunks, re-tightening the socket's own
    timeout to the REMAINING wall-clock budget before every blocking
    operation (connect, each read) - and unconditionally conn.close()'d in
    a finally block. Because this never spawns a thread, closing the
    connection is a real, synchronous cancellation: the moment this
    function returns (success or timeout), the socket is already closed in
    the SAME call stack, not "abandoned and hopefully cleaned up later."
    A server dribbling single bytes forever therefore sees its OWN next
    write fail (broken pipe/connection reset) shortly after the deadline,
    instead of continuing to believe a client is still reading."""
    if spec.kind != "http":
        raise ValueError(f"Unsupported probe kind: {spec.kind!r}")
    url = f"http://{spec.host}:{spec.port}{spec.path}"
    data = spec.body.encode("utf-8") if spec.body is not None else None
    deadline = time.monotonic() + timeout

    conn = http.client.HTTPConnection(spec.host, spec.port, timeout=timeout)
    sock = None
    resp = None
    try:
        conn.request(spec.method, spec.path, body=data, headers=dict(spec.headers))
        # Captured ONCE, right after the request is sent, and reused for
        # every settimeout() call below - never re-read from conn.sock
        # later. HTTPConnection.getresponse() itself sets conn.sock back to
        # None for a will-close response (this module's own test server, a
        # bare http.server.BaseHTTPRequestHandler, defaults to HTTP/1.0 -
        # no keep-alive - so this is the COMMON case, not an edge case) even
        # though the underlying OS socket stays open and readable via the
        # response's own file object (makefile() holds an independent
        # reference) - re-fetching conn.sock after getresponse() crashes
        # with AttributeError on a None, confirmed live while hardening
        # this fix.
        sock = conn.sock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"probe against {url} did not complete within its {timeout}s wall-clock budget "
                "(request send)"
            )
        sock.settimeout(remaining)
        resp = conn.getresponse()

        # Read ONE byte per iteration, not a large chunk size. resp.read(n)
        # for n > 1 can satisfy the request via MULTIPLE internal low-level
        # socket reads before returning control here at all (io.BufferedReader's
        # documented behavior: "multiple raw reads may be issued to satisfy
        # the byte count") - a dribbling server sending faster than any
        # single read's own timeout, but far slower overall than the probe
        # budget, would silently reproduce the exact cumulative-block gap
        # this rewrite exists to close, just one level deeper. Reading 1
        # byte at a time means every single socket read is its own loop
        # iteration, so the wall-clock deadline below is checked between
        # every byte - probe bodies are small health/status payloads, not
        # bulk transfers, so the added per-byte call overhead is immaterial.
        chunks = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"probe against {url} did not complete within its {timeout}s wall-clock "
                    "budget while reading the response body"
                )
            sock.settimeout(remaining)
            # Read one byte at a time INTENTIONALLY - do not "optimize" this
            # to a larger size. The socket timeout above is reset to the
            # remaining wall-clock budget before each read; read(n > 1) may
            # perform multiple underlying socket reads internally and can
            # therefore exceed the overall probe deadline when the peer
            # continuously dribbles response data.
            chunk = resp.read(1)
            if not chunk:
                break
            chunks.append(chunk)
        status = resp.status
        body = b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        # Explicit, unconditional close of every handle this function
        # opened, regardless of conn's own internal close/will-close
        # bookkeeping - the property under test is "no lingering resource
        # survives this call," not "conn.close() was called."
        if resp is not None:
            resp.close()
        if sock is not None:
            sock.close()
        conn.close()

    passed = status == spec.expected_status
    if passed and spec.expected_body_contains is not None:
        passed = spec.expected_body_contains in body
    reasoning = f"probe {spec.method} {url} -> status={status}, expected={spec.expected_status}"
    if spec.expected_body_contains is not None:
        reasoning += (
            f", expected_body_contains={spec.expected_body_contains!r} "
            f"present={spec.expected_body_contains in body}"
        )
    return status, body, passed, reasoning


def _wait_for_ready_or_exit(
    managed: ManagedProcess, readiness: ReadinessSpec, *, startup_timeout_seconds: float,
) -> Tuple[bool, bool, Optional[int]]:
    """Returns (ready, exited_before_ready, returncode). Bounded entirely by
    startup_timeout_seconds - readiness and process-exit are checked every
    poll_interval_seconds, whichever happens first wins."""
    deadline = time.monotonic() + startup_timeout_seconds
    while True:
        returncode = managed.poll()
        if returncode is not None:
            return False, True, returncode
        if _check_readiness(readiness):
            return True, False, None
        if time.monotonic() >= deadline:
            return False, False, None
        time.sleep(readiness.poll_interval_seconds)


def _run_lifecycle(
    managed: ManagedProcess, spec: ManagedServiceVerificationSpec,
) -> Tuple[ServiceVerificationOutcomeKind, bool, str, Optional[int], Optional[int], Optional[str]]:
    """(outcome, passed, reasoning, returncode, probe_status, probe_body) -
    everything BEFORE cleanup, which the caller always runs afterward
    regardless of what this returns or raises."""
    ready, exited_before_ready, returncode = _wait_for_ready_or_exit(
        managed, spec.readiness, startup_timeout_seconds=spec.startup_timeout_seconds,
    )
    if exited_before_ready:
        return (
            ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY, False,
            f"Service process exited with code {returncode} before becoming ready.",
            returncode, None, None,
        )
    if not ready:
        return (
            ServiceVerificationOutcomeKind.READINESS_TIMEOUT, False,
            f"Service did not become ready within {spec.startup_timeout_seconds}s.",
            None, None, None,
        )
    try:
        probe_status, probe_body, probe_passed, reasoning = _run_probe(
            spec.probe, timeout=spec.probe_timeout_seconds,
        )
    except OSError as e:
        # External review, 2026-09-03: expected, BEHAVIORAL probe-request
        # failures only - connection refused/reset, DNS failure, our own
        # wall-clock TimeoutError above (a builtin OSError subclass). The
        # service already passed readiness, so a probe-time connection
        # failure is itself evidence about the running service (e.g. it
        # crashed handling this specific request), not a Kriya defect -
        # eligible for normal Developer repair, same as an HTTP 500.
        return (
            ServiceVerificationOutcomeKind.PROBE_FAILED, False,
            f"Probe execution raised: {e}", None, None, None,
        )
    except Exception as e:
        # Anything else is a genuinely unexpected internal/orchestration
        # error (a bug in this module, a malformed spec field that slipped
        # past validation) - never a behavioral verdict, never eligible for
        # Developer repair.
        return (
            ServiceVerificationOutcomeKind.VERIFICATION_INTERNAL_ERROR, False,
            f"Unexpected internal error during probe execution: {e}", None, None, None,
        )
    outcome = (
        ServiceVerificationOutcomeKind.PROBE_PASSED if probe_passed
        else ServiceVerificationOutcomeKind.PROBE_FAILED
    )
    return outcome, probe_passed, reasoning, None, probe_status, probe_body


def _cleanup(managed: ManagedProcess, *, shutdown_timeout_seconds: float) -> Optional[str]:
    if managed.poll() is not None:
        return None
    try:
        died = managed.terminate(reap_timeout=shutdown_timeout_seconds)
    except Exception as e:
        return f"terminate() raised: {e}"
    if not died:
        return f"process did not exit within {shutdown_timeout_seconds}s after termination signal"
    return None


def run_managed_service_verification(
    spec: ManagedServiceVerificationSpec, *, controller: Optional[ProcessController] = None,
) -> ManagedServiceVerificationResult:
    """The whole start -> readiness -> probe -> evidence -> terminate ->
    cleanup lifecycle, run synchronously (the caller decides concurrency,
    same convention as PolymorphicValidator.run_app_sequence()). Guarantees:
    no process/port leak survives this call, on ANY exit path - success,
    every typed failure, an unexpected exception, or the process already
    being gone. terminate()/cleanup runs exactly once, unconditionally,
    after the lifecycle is fully decided (success or failure), never
    skipped by an early return."""
    controller = controller or ProcessController()
    try:
        managed = controller.start_managed(spec.service_command, cwd=spec.cwd, env=spec.env)
    except Exception as e:
        return ManagedServiceVerificationResult(
            outcome=ServiceVerificationOutcomeKind.SERVICE_START_FAILED,
            passed=False, reasoning=f"Failed to start service process: {e}",
            stdout="", stderr="", returncode=None,
        )

    try:
        outcome, passed, reasoning, returncode, probe_status, probe_body = _run_lifecycle(managed, spec)
    except Exception as e:
        # A genuinely unexpected failure somewhere in readiness-checking
        # itself (not the probe's own request, already split into
        # behavioral-vs-internal above) - still not a pass, still must not
        # escape without tearing the service down, and (external review,
        # 2026-09-03) must never be reported as PROBE_FAILED - that outcome
        # is eligible for normal Developer repair, and an internal Kriya
        # defect getting that treatment would start rewriting application
        # code to "fix" a bug that was never in the application.
        outcome = ServiceVerificationOutcomeKind.VERIFICATION_INTERNAL_ERROR
        passed = False
        reasoning = f"Unexpected internal error during managed service verification: {e}"
        returncode = None
        probe_status = None
        probe_body = None
    finally:
        cleanup_error = _cleanup(managed, shutdown_timeout_seconds=spec.shutdown_timeout_seconds)

    stdout, stderr = managed.captured_output()
    if cleanup_error is not None:
        return ManagedServiceVerificationResult(
            outcome=ServiceVerificationOutcomeKind.CLEANUP_FAILED,
            passed=False,
            reasoning=f"{reasoning} | cleanup also failed: {cleanup_error}",
            stdout=stdout, stderr=stderr, returncode=returncode,
            probe_status=probe_status, probe_body=probe_body, cleanup_error=cleanup_error,
        )
    return ManagedServiceVerificationResult(
        outcome=outcome, passed=passed, reasoning=reasoning,
        stdout=stdout, stderr=stderr, returncode=returncode,
        probe_status=probe_status, probe_body=probe_body,
    )
