"""One owner for bounded, local subprocess execution - the SEC-001 common
execution boundary (docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md
§6/§11, SEC-001-P3a/P3b/P5).

Three lifecycles are supported: `run()`/`run_async()` (finite, blocking or
awaited - Kriya owns the complete process tree and terminates it before
returning) and `start_managed()` (Managed Runtime Verification, 2026-09-03 -
a long-lived service a caller wants to poll for readiness and probe while
it's still running). All three share the exact same process-group
isolation and termination primitive (`_terminate_tree`), the exact same
`ContainmentProfile`/`ContainmentBackend` composition (`_prepare_env_and_preexec`),
and the exact same fail-closed classification of a resource/containment
setup failure (`_spawn_popen`/`_spawn_subprocess_exec` both convert the one
`subprocess.SubprocessError` shape a broken `preexec_fn` produces into a
real, typed `ContainmentSetupError` - see kriya/tools/containment.py and
kriya/tools/sandbox.py's own 2026-09-11 fail-closed correction) -
per Invariant 14 ("prefer one execution-control abstraction over scattered
sandbox logic"), this is deliberately ONE set of security semantics shared
by sync and async callers, not two parallel implementations that happen to
look similar."""
import asyncio
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.tools.containment import (
    ContainmentBackend,
    ContainmentProfile,
    ContainmentSetupError,
    PreparedContainment,
)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timeout: bool
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timeout": self.timeout,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
        }


def _bounded_tail(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    omitted = len(value) - limit
    return f"[... {omitted} earlier characters omitted ...]\n" + value[-limit:], True


class ManagedProcess:
    """A started-but-not-yet-awaited child process (Managed Runtime
    Verification, 2026-09-03). Unlike `ProcessController.run()`'s single
    blocking `communicate()`, a caller here needs to observe the process
    WHILE it keeps running (poll for exit, poll for readiness) - so stdout/
    stderr are drained continuously by background threads into bounded
    buffers instead, the standard fix for the deadlock `communicate()`
    itself exists to avoid (an unread full pipe blocks the child's own
    write() call, which would otherwise stall out a server that's actually
    healthy). Every method here is safe to call from the thread that
    started the process; the drain threads never touch anything but their
    own buffer."""

    def __init__(
        self, popen: subprocess.Popen, *, max_output_chars: int, cleanup: Optional[Callable[[], None]] = None,
    ) -> None:
        self._popen = popen
        self._max_output_chars = max_output_chars
        self._cleanup = cleanup
        self._stdout_chunks: List[str] = []
        self._stderr_chunks: List[str] = []
        self._lock = threading.Lock()
        self._stdout_thread = threading.Thread(
            target=self._drain, args=(popen.stdout, self._stdout_chunks), daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._drain, args=(popen.stderr, self._stderr_chunks), daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _drain(self, stream, buf: List[str]) -> None:
        # Bounded WHILE writing (external review, 2026-09-03), not just at
        # captured_output() read time - a noisy long-lived service can
        # otherwise grow this buffer without limit for the entire duration
        # Kriya waits on readiness/probe, since captured_output() (the only
        # place _bounded_tail was previously applied) may not be called
        # again for a long time. Each drain thread tracks its own running
        # character count and drops the oldest chunks once over budget, the
        # same tail-keeping policy _bounded_tail applies at read time - so
        # this is a strict tightening of an existing bound, not a new one.
        total_chars = 0
        try:
            for line in iter(stream.readline, ""):
                with self._lock:
                    buf.append(line)
                    total_chars += len(line)
                    while total_chars > self._max_output_chars and len(buf) > 1:
                        total_chars -= len(buf.pop(0))
        except (ValueError, OSError):
            # Stream closed out from under the reader by terminate()/reap -
            # the buffer already holds everything produced before that.
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    @property
    def pid(self) -> int:
        return self._popen.pid

    def poll(self) -> Optional[int]:
        """None while still running, the real exit code once it isn't -
        exactly subprocess.Popen.poll()'s own contract, since every caller
        here already reasons in those terms."""
        return self._popen.poll()

    def captured_output(self) -> Tuple[str, str]:
        with self._lock:
            stdout = "".join(self._stdout_chunks)
            stderr = "".join(self._stderr_chunks)
        stdout, _ = _bounded_tail(stdout, self._max_output_chars)
        stderr, _ = _bounded_tail(stderr, self._max_output_chars)
        return stdout, stderr

    def terminate(self, *, reap_timeout: float) -> bool:
        """Sends the SAME process-group kill `run()`'s own timeout path
        uses, then waits up to reap_timeout for the OS to confirm the tree
        is actually gone. Returns False (never raises) when it can't
        confirm that within the budget - the caller decides what a
        continued-uncertain process tree means for its own result, this
        method's only job is to try and honestly report whether it
        worked."""
        try:
            if self._popen.poll() is not None:
                return True
            ProcessController._terminate_tree(self._popen)
            try:
                self._popen.wait(timeout=reap_timeout)
                return True
            except subprocess.TimeoutExpired:
                return False
        finally:
            if self._cleanup is not None:
                self._cleanup()


@dataclass(frozen=True)
class _ResolvedExecution:
    env: Optional[Dict[str, str]]
    preexec_fn: Optional[Callable[[], None]]
    command: List[str]
    cleanup: Optional[Callable[[], None]] = None


def _prepare_env_and_preexec(
    *,
    command: List[str],
    containment_profile: Optional[ContainmentProfile],
    containment_backend: Optional[ContainmentBackend],
    env: Optional[Dict[str, str]],
    preexec_fn: Optional[Callable[[], None]],
) -> _ResolvedExecution:
    """The single place `env`/`preexec_fn`/`command` get resolved, for
    every caller of every lifecycle in this module. A caller passes EITHER
    a raw `env`/`preexec_fn` pair directly (every pre-SEC-001 caller,
    unchanged behavior) OR a `ContainmentProfile` + `ContainmentBackend`
    (new, SEC-001-aware callers) - never both; a caller supplying a profile
    without a backend (or vice versa) is a programming error, not a
    silent no-op. `backend.prepare()` raising `ContainmentSetupError` is
    NOT caught here - it propagates to the caller, which must fail the
    command closed rather than run it (Invariant: containment/resource
    setup failure must block execution). SEC-001-P6: a backend may also
    return a `command_prefix` (e.g. `docker run ...` ahead of the real
    command, for a container backend) - composed onto `command` HERE, the
    one place every caller's actual spawned argv is decided, and a
    `cleanup` hook the caller must run in `finally` regardless of outcome."""
    if containment_profile is None and containment_backend is None:
        return _ResolvedExecution(env=env, preexec_fn=preexec_fn, command=command)
    if containment_profile is None or containment_backend is None:
        raise ValueError(
            "containment_profile and containment_backend must both be provided together, or neither."
        )
    if env is not None or preexec_fn is not None:
        raise ValueError(
            "Pass either (env, preexec_fn) or (containment_profile, containment_backend), never both."
        )
    prepared: PreparedContainment = containment_backend.prepare(containment_profile, command)
    effective_command = (list(prepared.command_prefix) + command) if prepared.command_prefix else command
    return _ResolvedExecution(
        env=prepared.env, preexec_fn=prepared.preexec_fn, command=effective_command, cleanup=prepared.cleanup,
    )


def _spawn_popen(*args: Any, **kwargs: Any) -> subprocess.Popen:
    """`subprocess.Popen` construction, with the one real fail-closed
    correction this module makes: a `preexec_fn` that raises (SEC-001's
    fail-closed resource-limit fix, kriya/tools/sandbox.py) surfaces here
    as a generic `subprocess.SubprocessError("Exception occurred in
    preexec_fn.")` - confirmed empirically that CPython does not preserve
    the original exception's type or message across the errpipe, only
    this generic shape. Converted here into a real, typed
    `ContainmentSetupError` so callers can distinguish "the command itself
    failed" from "we refused to even start it uncontained"."""
    try:
        return subprocess.Popen(*args, **kwargs)
    except subprocess.SubprocessError as e:
        raise ContainmentSetupError(
            f"Resource-limit/containment setup failed before the command could start: {e}"
        ) from e


async def _spawn_subprocess_exec(*args: Any, **kwargs: Any) -> "asyncio.subprocess.Process":
    """Async sibling of `_spawn_popen` - identical fail-closed conversion,
    confirmed empirically to raise the same `subprocess.SubprocessError`
    shape for a failing `preexec_fn` under `asyncio.create_subprocess_exec`."""
    try:
        return await asyncio.create_subprocess_exec(*args, **kwargs)
    except subprocess.SubprocessError as e:
        raise ContainmentSetupError(
            f"Resource-limit/containment setup failed before the command could start: {e}"
        ) from e


class ProcessController:
    def __init__(self, *, max_output_chars: int = 2_000_000, reap_timeout: int = 10) -> None:
        self.max_output_chars = max_output_chars
        self.reap_timeout = reap_timeout

    def run(
        self,
        command: List[str],
        *,
        cwd: str,
        timeout: int,
        env: Optional[Dict[str, str]] = None,
        preexec_fn: Optional[Callable[[], None]] = None,
        stdin_payload: Optional[str] = None,
        containment_profile: Optional[ContainmentProfile] = None,
        containment_backend: Optional[ContainmentBackend] = None,
    ) -> ProcessResult:
        # Runtime Verification Contract (PRV-06, 2026-08-29): stdin is now
        # ALWAYS an explicit pipe, never left as the default (which inherits
        # THIS process's own stdin unchanged) - a real live incident this
        # closes: a generated app's own blocking `System.in`/readLine() call,
        # invoked with no stdin_payload, blocked for the FULL timeout window
        # before this class's own timeout/kill logic finally ended it,
        # wasting the entire budget on a hang the caller had no way to
        # prevent. communicate(input=...) with an empty string still closes
        # stdin immediately (EOF) exactly like DEVNULL would for a command
        # that never reads it, so this is a strict improvement for every
        # existing caller (compile/test/pom-validate/version-check), not
        # just the one that supplies a real payload.
        resolved = _prepare_env_and_preexec(
            command=command, containment_profile=containment_profile, containment_backend=containment_backend,
            env=env, preexec_fn=preexec_fn,
        )
        try:
            process = _spawn_popen(
                resolved.command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.PIPE, text=True, env=resolved.env, preexec_fn=resolved.preexec_fn,
                start_new_session=(os.name == "posix"),
            )
            timed_out = False
            try:
                stdout, stderr = process.communicate(input=stdin_payload or "", timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_tree(process)
                try:
                    stdout, stderr = process.communicate(timeout=self.reap_timeout)
                except subprocess.TimeoutExpired as reap_ex:
                    stdout = reap_ex.output or ""
                    stderr = (reap_ex.stderr or "") + (
                        "\n[REAP TIMEOUT] Process tree did not exit after termination."
                    )
        finally:
            # SEC-001-P6: a container backend's own authoritative teardown
            # (e.g. `docker rm -f`) - not optional even on the happy path,
            # see PreparedContainment.cleanup's own docstring for why
            # `_terminate_tree`'s host-side process-group kill alone is not
            # sufficient for a VM-mediated container runtime.
            if resolved.cleanup is not None:
                resolved.cleanup()
        stdout, stdout_truncated = _bounded_tail(stdout or "", self.max_output_chars)
        stderr, stderr_truncated = _bounded_tail(stderr or "", self.max_output_chars)
        if timed_out:
            stderr += f"\n[TIMEOUT] Command timed out after {timeout} seconds."
        return ProcessResult(
            returncode=-1 if timed_out else (
                process.returncode if process.returncode is not None else -1
            ),
            stdout=stdout,
            stderr=stderr,
            timeout=timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    async def run_async(
        self,
        command: List[str],
        *,
        cwd: str,
        timeout: int,
        env: Optional[Dict[str, str]] = None,
        preexec_fn: Optional[Callable[[], None]] = None,
        stdin_payload: Optional[str] = None,
        containment_profile: Optional[ContainmentProfile] = None,
        containment_backend: Optional[ContainmentBackend] = None,
    ) -> ProcessResult:
        """Async sibling of `run()` - the SEC-001-P3a design decision
        (docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md,
        SEC-001-P3a/P3b): a thin async-native method on THIS SAME class,
        sharing `_prepare_env_and_preexec`/`_terminate_tree`/`_bounded_tail`/
        `ProcessResult` with `run()` rather than a parallel implementation.
        Only the low-level spawn/wait/kill-on-timeout primitives differ
        (asyncio vs. blocking I/O) - not a duplicated security semantic,
        per Invariant 14. This is what `ShellTool`/`MCPClient` (both async
        callers) route through instead of their own raw
        `asyncio.create_subprocess_*` calls."""
        resolved = _prepare_env_and_preexec(
            command=command, containment_profile=containment_profile, containment_backend=containment_backend,
            env=env, preexec_fn=preexec_fn,
        )
        try:
            process = await _spawn_subprocess_exec(
                *resolved.command, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE, env=resolved.env, preexec_fn=resolved.preexec_fn,
                start_new_session=(os.name == "posix"),
            )
            timed_out = False
            stdin_bytes = (stdin_payload or "").encode("utf-8")
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(input=stdin_bytes), timeout=timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
                self._terminate_tree(process)
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        process.communicate(), timeout=self.reap_timeout,
                    )
                except asyncio.TimeoutError:
                    stdout_b, stderr_b = b"", b"[REAP TIMEOUT] Process tree did not exit after termination.".encode()
        finally:
            if resolved.cleanup is not None:
                resolved.cleanup()
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        stdout, stdout_truncated = _bounded_tail(stdout, self.max_output_chars)
        stderr, stderr_truncated = _bounded_tail(stderr, self.max_output_chars)
        if timed_out:
            stderr += f"\n[TIMEOUT] Command timed out after {timeout} seconds."
        return ProcessResult(
            returncode=-1 if timed_out else (
                process.returncode if process.returncode is not None else -1
            ),
            stdout=stdout,
            stderr=stderr,
            timeout=timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    def start_managed(
        self,
        command: List[str],
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        preexec_fn: Optional[Callable[[], None]] = None,
        containment_profile: Optional[ContainmentProfile] = None,
        containment_backend: Optional[ContainmentBackend] = None,
    ) -> ManagedProcess:
        """Starts a process the caller will observe WHILE it keeps running
        (Managed Runtime Verification, 2026-09-03) - the one thing this
        module's own docstring used to say Kriya "deliberately does not
        support": `run()` always owns a command's complete, synchronous
        lifecycle, but a service under test (a dev server, anything meant
        to keep listening) never exits on its own, so there is nothing for
        `run()`'s `communicate(timeout=...)` to wait ON. This method starts
        it under the exact same process-group isolation `run()` already
        uses (`start_new_session`) and returns immediately - the CALLER
        (not this method) still owns tearing it down, via the returned
        ManagedProcess.terminate(), exactly as strictly as `run()` already
        tears its own command down internally. stdin is DEVNULL rather than
        an explicit closed pipe (run()'s own fix for the same hang class) -
        a managed service is never fed a payload the way a finite
        command's last step can be, so there's nothing to close after."""
        resolved = _prepare_env_and_preexec(
            command=command, containment_profile=containment_profile, containment_backend=containment_backend,
            env=env, preexec_fn=preexec_fn,
        )
        # Unlike run()/run_async(), a managed process is still running when
        # this method returns - cleanup can't fire in a finally here. It's
        # handed to ManagedProcess instead, to run when the CALLER eventually
        # tears the service down via ManagedProcess.terminate().
        process = _spawn_popen(
            resolved.command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, env=resolved.env, preexec_fn=resolved.preexec_fn,
            start_new_session=(os.name == "posix"),
        )
        return ManagedProcess(process, max_output_chars=self.max_output_chars, cleanup=resolved.cleanup)

    @staticmethod
    def _terminate_tree(process: Any) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
