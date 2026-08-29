"""One owner for bounded, local subprocess execution.

Kriya deliberately does not support detached commands here. Validation owns
the complete process tree and must terminate it before returning.
"""
from dataclasses import dataclass
import os
import signal
import subprocess
from typing import Callable, Dict, List, Optional


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

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
