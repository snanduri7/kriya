import asyncio
import os
import subprocess
import sys
import time

import pytest

from kriya.tools.process import ProcessController


def test_process_controller_bounds_captured_output(tmp_path):
    controller = ProcessController(max_output_chars=100)
    result = controller.run(
        [sys.executable, "-c", "print('x' * 1000)"],
        cwd=str(tmp_path), timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout_truncated
    assert result.stdout.endswith("x" * 90 + "\n")


def test_process_controller_marks_timeout_and_returns(tmp_path):
    controller = ProcessController(reap_timeout=1)
    result = controller.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=str(tmp_path), timeout=1,
    )

    assert result.timeout
    assert "[TIMEOUT]" in result.stderr


def test_process_controller_closes_stdin_by_default_instead_of_hanging(tmp_path):
    """Runtime Verification Contract (PRV-06, 2026-08-29) - live incident:
    a generated app blocking on stdin (System.in/readLine()), invoked with
    no stdin_payload, used to inherit this process's own open stdin and
    block for the FULL timeout window before being killed. stdin is now
    ALWAYS an explicit pipe, closed immediately (EOF) when no payload is
    given - a blocking read must see EOF right away, not hang."""
    controller = ProcessController()
    result = controller.run(
        [sys.executable, "-c", "import sys; print('GOT:' + (sys.stdin.readline() or 'EOF'))"],
        cwd=str(tmp_path), timeout=30,
    )
    assert not result.timeout
    assert "GOT:EOF" in result.stdout


def test_process_controller_delivers_stdin_payload_and_closes_it(tmp_path):
    controller = ProcessController()
    result = controller.run(
        [sys.executable, "-c", "import sys; print('GOT:' + sys.stdin.readline().strip())"],
        cwd=str(tmp_path), timeout=30, stdin_payload="kriya-verification-input\n",
    )
    assert not result.timeout
    assert "GOT:kriya-verification-input" in result.stdout


# --- LEAK-OCI-EXCEPTION-RACE-001: an interrupted wait kills and reaps the tree ---

def _pid_writing_sleeper(tmp_path):
    """A real child that records its pid (exec keeps it) and would outlive the test."""
    pid_file = tmp_path / "pid"
    return ["/bin/sh", "-c", f"echo $$ > '{pid_file}'; exec sleep 60"], pid_file


def _wait_for_pid(pid_file, limit=10.0):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        text = pid_file.read_text().strip() if pid_file.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError("child never started")


def _assert_gone(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # killed AND reaped: not running, not a zombie


def test_exception_during_run_kills_and_reaps_the_child_before_it_propagates(tmp_path, monkeypatch):
    command, pid_file = _pid_writing_sleeper(tmp_path)
    real_communicate = subprocess.Popen.communicate

    def interrupted(self, *args, **kwargs):
        if self.args == command:
            _wait_for_pid(pid_file)
            raise RuntimeError("interrupted while waiting")
        return real_communicate(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)

    with pytest.raises(RuntimeError, match="interrupted while waiting"):
        ProcessController(reap_timeout=5).run(command, cwd=str(tmp_path), timeout=60)

    _assert_gone(int(pid_file.read_text()))


@pytest.mark.asyncio
async def test_cancelling_run_async_kills_and_reaps_the_child(tmp_path):
    command, pid_file = _pid_writing_sleeper(tmp_path)
    task = asyncio.ensure_future(ProcessController(reap_timeout=5).run_async(command, cwd=str(tmp_path), timeout=60))
    deadline = time.monotonic() + 10
    while not pid_file.exists() or not pid_file.read_text().strip():
        assert time.monotonic() < deadline, "child never started"
        await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    _assert_gone(int(pid_file.read_text()))
