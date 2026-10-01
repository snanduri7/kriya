"""ProcessControlPort contract (ARCH-PLATFORM-001, PLAT-PROCESS-CONTROL-001).

Every Kriya-owned lifecycle (ProcessController.run/run_async/start_managed
and the MCP lifecycle) spawns through the port and kills the whole tree
through it: a real grandchild dies on timeout and on teardown, not only the
direct child. Every spawn is attached to its tree right after creation; a
failed attach kills and reaps the new process and is a typed, non-retried
ContainmentSetupError. A host without PROCESS_TREE_TERMINATION starts nothing (typed
ContainmentSetupError) and never falls back to killing only the direct child.
"""
import asyncio
import dataclasses
import os
import subprocess
import sys
import textwrap
import time
from unittest.mock import MagicMock

import pytest

from kriya.mcp.lifecycle import spawn_mcp_process, terminate_mcp_process
from kriya.platform import services
from kriya.platform.capabilities import (
    CapabilityStatus,
    PlatformCapability,
    PlatformCapabilityUnavailable,
)
from kriya.platform.process_control import UnavailableProcessControl
from kriya.tools import process as process_module
from kriya.tools.containment import ContainmentSetupError
from kriya.tools.process import ProcessController, terminate_process_tree

pytestmark = pytest.mark.skipif(services.host_family() != services.POSIX, reason="POSIX provider contract")


def _gone(pid: int) -> bool:
    """Dead, or a zombie awaiting its (new) parent's reap."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return state.stdout.strip().startswith("Z") or not state.stdout.strip()


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _gone(pid):
            return True
        time.sleep(0.05)
    return _gone(pid)


def _tree_command(pid_file, *, ignore_sigterm: bool = False):
    """A child that starts a sleeping grandchild, records its pid, then sleeps."""
    code = textwrap.dedent(f"""
        import signal, subprocess, sys, time
        if {ignore_sigterm!r}:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with open({str(pid_file)!r}, "w") as handle:
            handle.write(str(grandchild.pid))
        print("started", flush=True)
        time.sleep(60)
    """)
    return [sys.executable, "-c", code]


def _grandchild(pid_file) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            return int(pid_file.read_text())
        time.sleep(0.05)
    raise AssertionError("the child never started its grandchild")


class _Recording:
    """A strict recording provider that delegates to the real POSIX one."""

    name = "recording"

    def __init__(self, delegate):
        self._delegate = delegate
        self.spawns = 0
        self.attached = []
        self.terminated = []

    def capability(self):
        return self._delegate.capability()

    def spawn_options(self):
        self.spawns += 1
        return self._delegate.spawn_options()

    def attach(self, process):
        self.attached.append(process.pid)
        self._delegate.attach(process)

    def terminate_tree(self, process):
        self.terminated.append(process.pid)
        self._delegate.terminate_tree(process)


@pytest.fixture
def recording():
    real = services.compose(services.POSIX)
    provider = _Recording(real.process_control)
    with services.override(dataclasses.replace(real, process_control=provider)):
        yield provider


class _AttachFails(_Recording):
    """The real POSIX provider, except that attach raises the shape a real
    provider failure has (an OSError, not a platform exception)."""

    def __init__(self, delegate):
        super().__init__(delegate)
        self.processes = []

    def attach(self, process):
        self.attached.append(process.pid)
        self.processes.append(process)
        raise OSError("test: attach refused")


class _AttachAndTreeKillFail(_AttachFails):
    """A provider that owns nothing: it can neither attach nor kill a tree."""

    def terminate_tree(self, process):
        self.terminated.append(process.pid)
        raise OSError("test: no tree to terminate")


@pytest.fixture
def attach_fails():
    real = services.compose(services.POSIX)
    provider = _AttachFails(real.process_control)
    with services.override(dataclasses.replace(real, process_control=provider)):
        yield provider


@pytest.fixture
def unavailable():
    real = services.compose(services.POSIX)
    provider = UnavailableProcessControl("test: no process-tree provider")
    with services.override(dataclasses.replace(real, process_control=provider)):
        yield provider


def test_the_posix_provider_reports_enforced_tree_termination_and_windows_reports_it_unavailable():
    posix = services.compose(services.POSIX)
    report = posix.process_control.capability()
    assert (report.capability, report.status) == (PlatformCapability.PROCESS_TREE_TERMINATION,
                                                  CapabilityStatus.ENFORCED)
    assert posix.identity()["providers"]["process_control"] == "posix-process-group"
    windows = services.compose(services.WINDOWS).process_control.capability()
    assert windows.status is CapabilityStatus.UNAVAILABLE
    assert windows.capability is PlatformCapability.PROCESS_TREE_TERMINATION


def test_posix_attach_is_a_no_op_that_touches_nothing():
    child = MagicMock(spec=subprocess.Popen)
    assert services.compose(services.POSIX).process_control.attach(child) is None
    assert child.mock_calls == []


def test_a_spawned_child_leads_its_own_session_and_process_group(tmp_path, recording):
    probe = "import os; print(os.getsid(0) == os.getpid(), os.getpgid(0) == os.getpid())"
    result = ProcessController().run([sys.executable, "-c", probe], cwd=str(tmp_path), timeout=30)
    assert (result.returncode, result.stdout.split()) == (0, ["True", "True"])
    assert recording.spawns == 1 and len(recording.attached) == 1


def test_normal_completion_is_unchanged_and_kills_nothing(tmp_path, recording):
    result = ProcessController().run([sys.executable, "-c", "print('ok')"], cwd=str(tmp_path), timeout=30)
    assert (result.returncode, result.stdout, result.timeout) == (0, "ok\n", False)
    assert len(recording.attached) == 1 and recording.terminated == []


def test_run_timeout_kills_the_grandchild(tmp_path, recording):
    pid_file = tmp_path / "grandchild.pid"
    result = ProcessController(reap_timeout=5).run(_tree_command(pid_file), cwd=str(tmp_path), timeout=2)
    assert result.timeout is True and len(recording.terminated) == 1
    assert recording.attached == recording.terminated
    assert _wait_gone(_grandchild(pid_file))


def test_run_async_timeout_kills_the_grandchild(tmp_path, recording):
    pid_file = tmp_path / "grandchild.pid"
    result = asyncio.run(ProcessController(reap_timeout=5).run_async(
        _tree_command(pid_file), cwd=str(tmp_path), timeout=2))
    assert result.timeout is True and len(recording.terminated) == 1
    assert recording.attached == recording.terminated
    assert _wait_gone(_grandchild(pid_file))


def test_managed_process_teardown_kills_the_grandchild(tmp_path, recording):
    pid_file = tmp_path / "grandchild.pid"
    managed = ProcessController().start_managed(_tree_command(pid_file), cwd=str(tmp_path))
    grandchild = _grandchild(pid_file)
    assert managed.terminate(reap_timeout=5) is True
    assert recording.attached == recording.terminated == [managed.pid]
    assert _wait_gone(grandchild)


def test_mcp_spawn_and_forced_teardown_use_the_port_and_kill_the_grandchild(tmp_path, recording):
    pid_file = tmp_path / "grandchild.pid"

    async def scenario():
        command = _tree_command(pid_file, ignore_sigterm=True)
        process = await spawn_mcp_process(command[0], command[1:], None, cpu_seconds=None, memory_mb=None,
                                          max_stdout_line_bytes=65536)
        grandchild = await asyncio.to_thread(_grandchild, pid_file)
        await terminate_mcp_process(process, grace_seconds=0.5, reap_seconds=5)
        return process.pid, grandchild

    pid, grandchild = asyncio.run(scenario())
    assert recording.spawns == 1 and recording.attached == recording.terminated == [pid]
    assert _wait_gone(grandchild)


def _reaped(pid: int) -> bool:
    """Fully gone: not running and not a zombie awaiting a reap."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


_SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


def _refusals(tmp_path):
    """Every lifecycle spawn path, each expected to raise the attach refusal."""
    yield lambda: ProcessController().run(_SLEEP, cwd=str(tmp_path), timeout=30)
    yield lambda: asyncio.run(ProcessController().run_async(_SLEEP, cwd=str(tmp_path), timeout=30))
    yield lambda: ProcessController().start_managed(_SLEEP, cwd=str(tmp_path))
    yield lambda: asyncio.run(spawn_mcp_process(_SLEEP[0], _SLEEP[1:], None, cpu_seconds=None, memory_mb=None,
                                                max_stdout_line_bytes=65536))


def test_a_failed_attach_kills_and_reaps_the_new_process_on_every_spawn_path(tmp_path, attach_fails):
    for spawn in _refusals(tmp_path):
        with pytest.raises(ContainmentSetupError, match="attach failed after spawn") as raised:
            spawn()
        assert isinstance(raised.value.__cause__, OSError)
        pid = attach_fails.attached[-1]
        assert attach_fails.terminated[-1] == pid
        assert _reaped(pid)
    assert len(attach_fails.attached) == len(set(attach_fails.attached)) == 4


def test_a_failed_async_attach_is_reaped_before_the_refusal_reaches_the_caller(attach_fails):
    """asyncio's child watcher would reap the killed child eventually; the
    refusal must not reach the caller before it is reaped."""

    async def scenario():
        with pytest.raises(ContainmentSetupError):
            await process_module.spawn_subprocess_exec_fail_closed(*_SLEEP)
        return attach_fails.processes[-1].returncode

    assert asyncio.run(scenario()) is not None


def test_when_the_provider_cannot_kill_what_it_failed_to_attach_the_direct_child_is_killed(tmp_path):
    real = services.compose(services.POSIX)
    provider = _AttachAndTreeKillFail(real.process_control)
    with services.override(dataclasses.replace(real, process_control=provider)):
        with pytest.raises(ContainmentSetupError, match="the direct child was killed") as raised:
            ProcessController().run(_SLEEP, cwd=str(tmp_path), timeout=30)
    assert isinstance(raised.value.__cause__, OSError)
    assert provider.terminated == provider.attached and _reaped(provider.attached[0])


def test_a_failed_attach_is_a_typed_containment_stop_that_is_not_retried(tmp_path, attach_fails):
    from kriya.workflow.recovery_coordinator import classify_attempt_exception

    with pytest.raises(ContainmentSetupError) as raised:
        ProcessController().run(_SLEEP, cwd=str(tmp_path), timeout=30)
    assert len(attach_fails.attached) == 1
    classified = classify_attempt_exception(raised.value, None, last_attempt_mode=None,
                                            ground_scope_denial=lambda _exc, _ctx: False)
    assert classified.containment_setup_failure is True
    assert classified.failure.type == "containment_setup_failed"


def test_without_tree_termination_run_starts_nothing(tmp_path, unavailable):
    marker = tmp_path / "started"
    with pytest.raises(ContainmentSetupError, match="PLATFORM_CAPABILITY_UNAVAILABLE") as raised:
        ProcessController().run([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
                                cwd=str(tmp_path), timeout=30)
    assert isinstance(raised.value.__cause__, PlatformCapabilityUnavailable)
    assert not marker.exists()


def test_without_tree_termination_async_and_managed_spawns_start_nothing(tmp_path, unavailable):
    marker = tmp_path / "started"
    command = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
    with pytest.raises(ContainmentSetupError):
        asyncio.run(ProcessController().run_async(command, cwd=str(tmp_path), timeout=30))
    with pytest.raises(ContainmentSetupError):
        ProcessController().start_managed(command, cwd=str(tmp_path))
    with pytest.raises(ContainmentSetupError):
        asyncio.run(spawn_mcp_process(command[0], command[1:], None, cpu_seconds=None, memory_mb=None,
                                      max_stdout_line_bytes=65536))
    assert not marker.exists()


def test_without_tree_termination_there_is_no_direct_child_kill_fallback(unavailable):
    child = MagicMock(spec=subprocess.Popen)
    with pytest.raises(PlatformCapabilityUnavailable):
        terminate_process_tree(child)
    child.kill.assert_not_called()
    child.terminate.assert_not_called()


def test_a_caller_cannot_override_the_providers_spawn_options(tmp_path):
    with pytest.raises(ValueError, match="start_new_session"):
        process_module._spawn_popen([sys.executable, "-c", "pass"], cwd=str(tmp_path), start_new_session=False)


def test_terminating_an_already_exited_tree_is_not_an_error(tmp_path):
    finished = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    finished.wait()
    terminate_process_tree(finished)
