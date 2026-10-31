"""W3-8 (PROCESS-ORPHAN-CHILD-OUTLIVES-PARENT-SIGKILL-001 / LEAK-SIGKILL-RECOVERY-GAP-001, owner decision 2026-10-30): a child spawned by Kriya must not outlive its owner.

MEASURED before the fix (W3-0 coordinator, re-measured at the W3-8 start with the isolated observe-only script): a SIGKILLed Kriya parent left its host child alive, which
performed its effect 11.2 s later; under the packaged OCI containment the CONTAINER (owned by the Docker daemon) stayed up and wrote to the bind-mounted workspace 18.9 s
after its owner died. macOS has no PDEATHSIG, so the mechanism is a pipe lifeline to a watchdog sidecar (kriya/platform/posix_owner_watchdog.py) behind the existing
ProcessControlPort/ProcessController seam; the containment backend names the cleanup (``docker rm -f``). Every test here kills a REAL owner process with SIGKILL.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest
from test_d2_authority_namespace import NEEDS_DOCKER

from kriya.platform import services
from kriya.platform.capabilities import PlatformCapabilityUnavailable
from kriya.platform.process_control import UnavailableProcessControl

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the POSIX lifeline provider")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie that nobody reaped (the owner is dead and init has not yet collected it) still answers kill(0)
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait_until(condition, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def _victim(tmp_path, body):
    """Run ``body`` in a separate OWNER process (a fresh interpreter importing this checkout); returns (Popen, readiness-file path)."""
    ready = tmp_path / "ready"
    code = "import sys\nsys.path.insert(0, %r)\n%s\nopen(%r, 'w').write('ok')\nimport time\ntime.sleep(120)\n" % (REPO, textwrap.dedent(body), str(ready))
    owner = subprocess.Popen([sys.executable, "-I", "-c", code], start_new_session=True)
    assert _wait_until(ready.exists, 30), "the owner process never became ready"
    return owner, ready


def test_a_host_child_dies_with_its_sigkilled_owner_and_never_performs_its_effect(tmp_path):
    pid_file, done = tmp_path / "pid", tmp_path / "donefile"
    owner, _ = _victim(tmp_path, f"""
        import subprocess, threading
        from kriya.tools.process import ProcessController
        threading.Thread(target=lambda: ProcessController().run(["sh", "-c", "echo $$ > {pid_file}; sleep 8; echo late > {done}"], cwd={str(tmp_path)!r}, timeout=120), daemon=True).start()
        import time
        while not __import__('os').path.exists({str(pid_file)!r}): time.sleep(0.05)
    """)
    child = int(pid_file.read_text())
    assert _alive(child)
    os.kill(owner.pid, signal.SIGKILL)
    owner.wait()
    assert _wait_until(lambda: not _alive(child), 5), "the child outlived its SIGKILLed owner"
    time.sleep(9)                                                     # past the moment the child would have written
    assert not done.exists()


def test_the_cleanup_commands_the_backend_named_run_when_the_owner_dies(tmp_path):
    marker, pid_file = tmp_path / "cleaned", tmp_path / "pid"
    owner, _ = _victim(tmp_path, f"""
        import subprocess
        from kriya.platform.services import platform_services
        control = platform_services().process_control
        child = subprocess.Popen(["sh", "-c", "echo $$ > {pid_file}; sleep 60"], **control.spawn_options())
        control.attach(child)
        control.guard_owner_death(child, [["touch", {str(marker)!r}]])
        import time
        while not __import__('os').path.exists({str(pid_file)!r}): time.sleep(0.05)
    """)
    child = int(pid_file.read_text())
    os.kill(owner.pid, signal.SIGKILL)
    owner.wait()
    assert _wait_until(lambda: marker.exists(), 8), "the backend's cleanup command never ran"
    assert _wait_until(lambda: not _alive(child), 5)


def test_a_normally_exiting_owner_does_not_leave_a_running_child_behind(tmp_path):
    """Owner death includes a normal exit with the process still running: nothing started by Kriya outlives it (the finalizer does not release at interpreter exit)."""
    pid_file = tmp_path / "pid"
    code = textwrap.dedent(f"""
        import subprocess, sys, time
        sys.path.insert(0, {REPO!r})
        from kriya.platform.services import platform_services
        control = platform_services().process_control
        child = subprocess.Popen(["sh", "-c", "echo $$ > {pid_file}; sleep 60"], **control.spawn_options())
        control.attach(child)
        control.guard_owner_death(child)
        while not __import__('os').path.exists({str(pid_file)!r}): time.sleep(0.05)
    """)
    subprocess.run([sys.executable, "-I", "-c", code], check=True, timeout=60)         # exits normally with the child still running
    child = int(pid_file.read_text())
    assert _wait_until(lambda: not _alive(child), 5)


def test_a_released_guard_stands_down_and_kills_nothing(tmp_path):
    """Negative control: the release byte ``R`` (what the finalizer of a finished process object sends) makes the watchdog exit WITHOUT killing the still-running child or running
    the cleanup. (A still-running Popen is kept alive by subprocess._active until reaped, so its finalizer cannot be used to drive this deterministically.)"""
    from kriya.platform import posix_owner_watchdog as watchdog_module

    marker = tmp_path / "cleaned"
    control = services.platform_services().process_control
    child = subprocess.Popen(["sleep", "30"], **control.spawn_options())
    control.attach(child)
    control.guard_owner_death(child, [["touch", str(marker)]])
    try:
        watchdog_module._release(child._kriya_owner_lifeline)
        assert child._kriya_owner_watchdog.wait(timeout=5) == 0
        assert _alive(child.pid), "a released guard must not kill the process it no longer owns"
        assert not marker.exists()
    finally:
        control.terminate_tree(child)
        child.wait()


def test_the_watchdog_exits_by_itself_when_the_guarded_tree_is_gone(tmp_path):
    """Negative control: it never lingers - once the guarded group is gone it stands down without killing or running anything (the owner is still alive here)."""
    marker = tmp_path / "cleaned"
    control = services.platform_services().process_control
    child = subprocess.Popen(["sh", "-c", "exit 0"], **control.spawn_options())
    control.attach(child)
    control.guard_owner_death(child, [["touch", str(marker)]])
    child.wait()
    assert child._kriya_owner_watchdog.wait(timeout=5) == 0
    assert not marker.exists()


def test_a_guard_that_cannot_be_installed_refuses_the_process_fail_closed(tmp_path):
    from kriya.tools.containment import ContainmentSetupError
    from kriya.tools.process import ProcessController

    real = services.compose(services.POSIX)
    pids = []

    class _GuardFails:
        name = "guard-fails"

        def capability(self):
            return real.process_control.capability()

        def spawn_options(self):
            return real.process_control.spawn_options()

        def attach(self, process):
            pids.append(process.pid)

        def terminate_tree(self, process):
            real.process_control.terminate_tree(process)

        def guard_owner_death(self, process, cleanup=()):
            raise OSError("cannot start the watchdog")

    import dataclasses
    with services.override(dataclasses.replace(real, process_control=_GuardFails())):
        with pytest.raises(ContainmentSetupError, match="attach failed"):
            ProcessController().run(["sleep", "30"], cwd=str(tmp_path), timeout=20)
    assert pids and _wait_until(lambda: not _alive(pids[0]), 5), "a process whose owner-death guard failed was handed out (or left running)"


def test_a_host_without_a_provider_still_refuses_at_spawn():
    with pytest.raises(PlatformCapabilityUnavailable):
        UnavailableProcessControl("no provider").guard_owner_death(object())


@NEEDS_DOCKER
def test_real_oci_a_container_is_removed_when_its_owner_is_sigkilled_and_never_writes_afterwards(tmp_path):
    """The production path: PolymorphicValidator profile -> OCIContainmentBackend -> real Docker. Before the fix the container stayed Up and wrote 18.9 s after the owner died."""
    ws = tmp_path / "ws"
    (ws / ".kriya" / "control").mkdir(parents=True)
    (ws / ".kriya" / "checkpoints").mkdir()
    started, done = ws / "started", ws / "donefile"

    def kriya_containers():
        listing = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
        return {name for name in listing if name.startswith("kriya-oci-")}

    before = kriya_containers()                                         # unrelated containers (another run, a stale one) are not this test's business
    owner, _ = _victim(tmp_path, f"""
        import threading, time
        from kriya.config.config import AutonomyConfig
        from kriya.tools.containment import NetworkAuthority
        from kriya.tools.process import ProcessController
        from kriya.tools.validate import PolymorphicValidator
        validator = PolymorphicValidator({str(ws)!r}, autonomy_cfg=AutonomyConfig(containment_backend="oci", contained_execution_required=True))
        profile, backend = validator.build_containment_profile_and_backend(network=NetworkAuthority.DENIED)
        threading.Thread(target=lambda: ProcessController().run(["sh", "-c", "echo up > started; sleep 12; echo late > donefile"], cwd={str(ws)!r}, timeout=120,
                                                                containment_profile=profile, containment_backend=backend), daemon=True).start()
        while not __import__('os').path.exists({str(started)!r}): time.sleep(0.1)
    """)
    created = kriya_containers() - before
    assert created, "the contained command is running in a container this test started"
    os.kill(owner.pid, signal.SIGKILL)
    owner.wait()
    try:
        assert _wait_until(lambda: not (kriya_containers() & created), 20), "the container outlived its SIGKILLed owner"
        time.sleep(14)                                                  # past the moment the command would have written
        assert not done.exists()
    finally:
        for name in kriya_containers() & created:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_a_command_that_exits_before_the_guard_is_installed_is_not_refused(tmp_path):
    """Regression for a defect of my own first W3-8 draft (found by the adjacent batch, 5 git/shell tests under load): the guard looked the child's process group up with
    ``os.getpgid``, which raises ESRCH on macOS for a child that has ALREADY exited and is not yet reaped - so a fast ``git`` call was refused at spawn ('Process-tree attach
    failed ... No such process'). The state is built deterministically here: the child has exited (a zombie) when the guard is installed. The group id IS the pid (the provider
    starts the child as its own session), and a command that is already gone is a guard with nothing to own."""
    from kriya.tools.process import ProcessController

    control = services.platform_services().process_control
    child = subprocess.Popen(["true"], **control.spawn_options())
    time.sleep(0.5)                                                      # exited, not reaped
    control.attach(child)
    control.guard_owner_death(child, [])                                 # must not raise
    child.wait()                                                         # reaped: only now is the group really gone (a zombie leader still holds it)
    assert child._kriya_owner_watchdog.wait(timeout=5) == 0              # and the sidecar stands down: nothing is left to own
    assert ProcessController().run(["true"], cwd=str(tmp_path), timeout=20).returncode == 0
