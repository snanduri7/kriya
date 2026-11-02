"""D2 M2 slice 5: REAL-child negative control for the private review channel.

The question that decides the whole design: can a command, tool or grandchild that Kriya launches inherit, enumerate-for-use, or write to the review socket? A host process holds a real
socket pair and starts a waiter process with one end INHERITABLE at fd 3 (what Node's extra stdio entry does - MEASURED, closure/d2/m2_node_fd3_probe.txt). The waiter takes the descriptor
over exactly as ``kriya generate --launch-id`` does, then launches real children through Kriya's own spawn paths (tests/_fd_probe_waiter.py); each child and grandchild enumerates its
descriptor table and tries to write to, duplicate and re-open fd 3.

Two independent barriers are proven: ``close_fds=True`` (the default every Kriya spawn uses) and the non-inheritable flag the takeover sets (so even a forbidden ``close_fds=False`` cannot leak
it). The controls prove the probe has teeth: a deliberate ``pass_fds`` leaks, and so does an un-taken-over (still inheritable) descriptor. What this does NOT cover: code inside the waiter's own
process, same-user ptrace / process-memory inspection, and Linux ``/proc/<pid>/fd`` (see the last test: it only runs on a Linux host and is the mandatory measurement before production
qualification)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

WAITER = str(Path(__file__).with_name("_fd_probe_waiter.py"))
ROOT = str(Path(__file__).resolve().parents[1])


def _run_waiter(mode: str):
    """Starts the waiter with the socket at fd 3 of the CHILD only (the test process's own fd 3 is never touched). Returns (report, bytes the host end received)."""
    host_end, waiter_end = socket.socketpair()
    source = waiter_end.fileno()

    def place():  # runs in the child between fork and exec: dup the socket onto fd 3, INHERITABLE
        os.dup2(source, 3, inheritable=True)

    try:
        process = subprocess.Popen([sys.executable, WAITER, mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, pass_fds=(3,), preexec_fn=place,
                                   env={**os.environ, "PYTHONPATH": ROOT})
        waiter_end.close()  # the host keeps only its own end
        out, err = process.communicate(timeout=240)
        assert process.returncode == 0, err[-2000:]
        host_end.setblocking(False)
        try:
            received = host_end.recv(100)
        except BlockingIOError:
            received = b""
        line = next(row for row in out.splitlines() if row.startswith("REPORT "))
        return json.loads(line[7:]), received
    finally:
        host_end.close()


def _sees_the_channel(probe) -> bool:
    return any(fd == 3 for fd, _kind in probe["fds_above_2"]) or probe["write_fd3"] == "WROTE" or probe["dup_fd3"] == "DUPED" or probe["reopen_dev_fd3"] == "OPENED"


def _assert_blind(probe, label):
    assert probe["fds_above_2"] == [], f"{label}: sees descriptors above 2: {probe['fds_above_2']}"
    assert (probe["write_fd3"], probe["dup_fd3"]) == ("OSError", "OSError"), f"{label}: {probe}"
    assert not _sees_the_channel(probe), label


def test_the_waiter_starts_with_an_inheritable_socket_and_the_takeover_makes_it_non_inheritable():
    report, _ = _run_waiter("paths")
    assert report["fd3_is_socket_at_start"] is True and report["fd3_inheritable_at_start"] is True, "this is exactly what the host hands the waiter"
    assert report["fd3_inheritable_after_takeover"] is False


def test_no_child_or_grandchild_on_any_of_the_five_spawn_paths_can_see_write_duplicate_or_reopen_the_channel():
    report, received = _run_waiter("paths")
    expected = {"ProcessController.run", "ProcessController.run + resource-limit preexec_fn", "ProcessController.run_async", "spawn_subprocess_exec_fail_closed", "ProcessController.start_managed"}
    assert set(report["results"]) == expected, "all five production spawn paths were exercised"
    for path, probe in report["results"].items():
        _assert_blind(probe, path)
        if "grandchild" in probe:
            _assert_blind(probe["grandchild"], f"{path} / grandchild")
    assert received == b"", "nothing any command did ever reached the host's end of the socket"


def test_even_a_forbidden_close_fds_false_cannot_leak_it_because_the_descriptor_is_non_inheritable():
    report, received = _run_waiter("belt")
    probe = report["results"]["close_fds=False"]
    _assert_blind(probe, "close_fds=False after the takeover")
    _assert_blind(probe["grandchild"], "close_fds=False / grandchild")
    assert received == b""


@pytest.mark.parametrize(("mode", "label"), [("raw_belt", "close_fds=False"), ("pass_fds", "pass_fds=(3,)")])
def test_the_probe_has_teeth_a_descriptor_that_is_still_inheritable_or_explicitly_passed_is_seen_and_used(mode, label):
    report, received = _run_waiter(mode)
    probe = next(iter(report["results"].values()))
    assert _sees_the_channel(probe), f"{label}: the control must LEAK, or the blind results above prove nothing: {probe}"
    assert probe["write_fd3"] == "WROTE" and received == b"X", "the child really wrote into the host's end"


@pytest.mark.requires_platform("linux")  # PRD-034: mandatory Linux-HOST evidence; PENDING (never a pass) anywhere else
@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="MANDATORY LINUX-HOST MEASUREMENT (owner 2026-10-23): /proc/<pid>/fd access to the review socket is INFERRED, not measured, on this platform; "
                                                              "run this test on a Linux host before final production qualification")
def test_linux_a_child_cannot_reopen_the_waiters_socket_through_proc():
    """Linux only: a same-UID child can open /proc/<waiter pid>/fd/N of a PIPE but not of a SOCKET (ENXIO). The channel is a socket for that reason."""
    host_end, waiter_end = socket.socketpair()
    os.set_inheritable(waiter_end.fileno(), False)
    try:
        child = subprocess.run([sys.executable, "-c", f"import os\ntry:\n    os.close(os.open('/proc/{os.getpid()}/fd/{waiter_end.fileno()}', os.O_RDWR))\n    print('OPENED')\nexcept OSError as e:\n    print(type(e).__name__, e.errno)"],
                               capture_output=True, text=True)
        assert child.stdout.strip().startswith("OSError") and "OPENED" not in child.stdout, child.stdout
    finally:
        host_end.close()
        waiter_end.close()
