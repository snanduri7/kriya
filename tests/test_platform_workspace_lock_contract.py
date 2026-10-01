"""WorkspaceLockPort contract (ARCH-PLATFORM-001, PLAT-003).

The POSIX provider's contract (non-blocking contention, shared probe,
release by the kernel when the holder dies), the composition root, and the
fail-closed behaviour on a host without a crash-safe provider: every use is
a typed PLATFORM_CAPABILITY_UNAVAILABLE refusal, never an unlocked run.
"""
import os
import subprocess
import sys
import textwrap

import pytest
from _strict_doubles import strict_config

from kriya import production_doctor
from kriya.control.run_ownership import WorkspaceLockHeldError, acquire_run_lock, probe_run_lock
from kriya.platform import services
from kriya.platform.capabilities import (
    PLATFORM_CAPABILITY_UNAVAILABLE,
    CapabilityStatus,
    PlatformCapability,
    PlatformCapabilityUnavailable,
    require,
)

posix_only = pytest.mark.skipif(services.host_family() != services.POSIX, reason="POSIX provider contract")


@pytest.fixture
def lock_file(tmp_path):
    path = tmp_path / "run.lock"
    path.write_bytes(b"")
    return str(path)


def _open(path):
    return os.open(path, os.O_RDWR)


@posix_only
def test_exclusive_contention_is_nonblocking_and_released(lock_file):
    lock = services.compose(services.POSIX).workspace_lock
    first, second = _open(lock_file), _open(lock_file)
    try:
        assert lock.try_exclusive(first) is True
        assert lock.try_exclusive(second) is False
        assert lock.try_shared(second) is False
        lock.release(first)
        assert lock.try_shared(second) is True
        assert lock.try_exclusive(first) is False
        lock.release(second)
        lock.release(second)  # releasing an unlocked descriptor is not an error
        assert lock.try_exclusive(first) is True
    finally:
        os.close(first)
        os.close(second)


@posix_only
def test_the_lock_is_released_when_the_holder_dies(lock_file):
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import os, sys, time
        from kriya.platform.posix_locking import PosixFlockLock
        fd = os.open({lock_file!r}, os.O_RDWR)
        assert PosixFlockLock().try_exclusive(fd)
        print("held", flush=True)
        time.sleep(60)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        lock = services.compose(services.POSIX).workspace_lock
        fd = _open(lock_file)
        try:
            assert lock.try_exclusive(fd) is False
            holder.kill()
            holder.wait(timeout=10)
            assert lock.try_exclusive(fd) is True
        finally:
            os.close(fd)
    finally:
        holder.kill()
        holder.wait(timeout=10)


@posix_only
def test_the_posix_composition_reports_an_enforced_crash_safe_lock():
    composed = services.compose(services.POSIX)
    report = composed.workspace_lock.capability()
    assert (report.capability, report.status, report.provider) == (
        PlatformCapability.FILE_LOCK_CRASH_SAFE, CapabilityStatus.ENFORCED, "posix-flock")
    assert require(report) is report
    identity = composed.identity()
    assert identity["family"] == services.POSIX
    assert identity["providers"]["workspace_lock"] == "posix-flock"
    lock_rows = [row for row in identity["capabilities"] if row["capability"] == "file_lock_crash_safe"]
    assert [row["status"] for row in lock_rows] == ["enforced"]


@pytest.mark.parametrize("name,family", [("linux", "posix"), ("darwin", "posix"), ("win32", "windows"),
                                         ("cygwin", "windows"), ("freebsd14", "posix")])
def test_host_family_classification(name, family):
    assert services.host_family(name) == family


def test_a_host_without_a_provider_refuses_every_lock_use(tmp_path, lock_file):
    composed = services.compose(services.WINDOWS)
    report = composed.workspace_lock.capability()
    assert report.status is CapabilityStatus.UNAVAILABLE
    with pytest.raises(PlatformCapabilityUnavailable) as raised:
        require(report)
    assert raised.value.reason_code == PLATFORM_CAPABILITY_UNAVAILABLE
    fd = _open(lock_file)
    try:
        for use in (composed.workspace_lock.try_exclusive, composed.workspace_lock.try_shared,
                    composed.workspace_lock.release):
            with pytest.raises(PlatformCapabilityUnavailable):
                use(fd)
    finally:
        os.close(fd)


def test_run_ownership_fails_closed_without_a_provider(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    before = set(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None
    ran = []
    with services.override(services.compose(services.WINDOWS)):
        with pytest.raises(PlatformCapabilityUnavailable):
            with acquire_run_lock(str(workspace)):
                ran.append(True)
        (workspace / ".kriya" / "run.lock").write_bytes(b"")
        with pytest.raises(PlatformCapabilityUnavailable):
            probe_run_lock(str(workspace))
    assert ran == []  # the guarded body never runs unlocked
    if before is not None:
        assert set(os.listdir("/dev/fd")) == before  # no descriptor leaked


@posix_only
def test_run_ownership_contention_still_raises_the_typed_held_error(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with acquire_run_lock(str(workspace), run_id="first"):
        assert "first" in (probe_run_lock(str(workspace)) or "")
        with pytest.raises(WorkspaceLockHeldError):
            with acquire_run_lock(str(workspace), run_id="second"):
                pytest.fail("a second owner entered the guarded body")
    assert probe_run_lock(str(workspace)) is None


def test_the_doctor_reports_an_unavailable_workspace_lock(tmp_path):
    ctx = production_doctor._Context(cfg=strict_config(), workspace=str(tmp_path))
    with services.override(services.compose(services.WINDOWS)):
        check = production_doctor._check_identity_lock(ctx)
    assert check.status is production_doctor.CheckStatus.FAIL
    assert check.evidence["lock_capability"]["status"] == "unavailable"


@posix_only
def test_the_doctor_passes_with_an_enforced_workspace_lock(tmp_path):
    ctx = production_doctor._Context(cfg=strict_config(), workspace=str(tmp_path))
    check = production_doctor._check_identity_lock(ctx)
    assert check.status is production_doctor.CheckStatus.PASS
    assert check.evidence["lock_capability"]["status"] == "enforced"
