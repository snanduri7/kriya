"""One owner for bounded, local subprocess execution.

Two lifecycles are supported: `run()` (finite, blocking - Kriya owns the
complete process tree and terminates it before returning, unchanged since
before Managed Runtime Verification) and `start_managed()` (Managed Runtime
Verification, 2026-09-03 - a long-lived service a caller wants to poll for
readiness and probe while it's still running). Both share the exact same
process-group isolation and termination primitive (`_terminate_tree`) - a
managed process is still fully owned and torn down by its caller, just not
synchronously inside a single blocking call the way `run()`'s callers need.
"""
from dataclasses import dataclass
import os
import signal
import subprocess
import threading
from typing import Callable, Dict, List, Optional, Tuple


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

    def __init__(self, popen: subprocess.Popen, *, max_output_chars: int) -> None:
        self._popen = popen
        self._max_output_chars = max_output_chars
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
        try:
            for line in iter(stream.readline, ""):
                with self._lock:
                    buf.append(line)
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

    def terminate(self, *, reap_timeout: int) -> bool:
        """Sends the SAME process-group kill `run()`'s own timeout path
        uses, then waits up to reap_timeout for the OS to confirm the tree
        is actually gone. Returns False (never raises) when it can't
        confirm that within the budget - the caller decides what a
        continued-uncertain process tree means for its own result, this
        method's only job is to try and honestly report whether it
        worked."""
        if self._popen.poll() is not None:
            return True
        ProcessController._terminate_tree(self._popen)
        try:
            self._popen.wait(timeout=reap_timeout)
            return True
        except subprocess.TimeoutExpired:
            return False


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
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE, text=True, env=env, preexec_fn=preexec_fn,
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

    def start_managed(
        self,
        command: List[str],
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        preexec_fn: Optional[Callable[[], None]] = None,
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
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, env=env, preexec_fn=preexec_fn,
            start_new_session=(os.name == "posix"),
        )
        return ManagedProcess(process, max_output_chars=self.max_output_chars)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
