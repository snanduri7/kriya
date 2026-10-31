"""The owner-death watchdog of the POSIX ProcessControlPort provider (W3-8, PROCESS-ORPHAN-CHILD-OUTLIVES-PARENT-SIGKILL-001).

When the Kriya process is SIGKILLed nothing in it can run, so a spawned child (or, under the packaged OCI containment, a container owned by the Docker daemon) used to
carry on and perform its effect against the workspace after its owner was dead (MEASURED: a host child 11.2 s later, a container 18.9 s later). macOS has no
``PR_SET_PDEATHSIG``; the portable lifeline is a pipe: the owner keeps the write end open for as long as it lives, the kernel closes it when the owner dies - however it
dies - and a tiny sidecar process that holds the read end sees EOF. The sidecar then kills the guarded process group and runs the cleanup commands the containment backend
named (``docker rm -f <container>``).

The sidecar is its own session (it is not in the guarded group and survives the owner), imports only the standard library, and owns nothing else:
- EOF with no release byte        -> the owner is gone: kill the group, run the cleanup commands, exit;
- the release byte ``R``           -> the owner finished with the process (the Popen object was finalized): exit, kill nothing;
- the guarded group no longer exists -> nothing is left to own: exit, kill nothing (so it never lingers and never signals a recycled group id long after).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import weakref
from typing import Any, Sequence

POLL_SECONDS = 0.5
CLEANUP_TIMEOUT_SECONDS = 30

WATCHDOG_SOURCE = r'''
import json, os, select, signal, subprocess, sys
pgid = int(sys.argv[1]); cleanup = json.loads(sys.argv[2]); fd = 0; poll = float(sys.argv[3]); cleanup_timeout = float(sys.argv[4])  # the lifeline is the sidecar's STDIN: no descriptor is passed to it
def group_exists():
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
while True:
    ready, _, _ = select.select([fd], [], [], poll)
    if ready:
        data = os.read(fd, 64)
        if not data:
            break
        if b"R" in data:
            sys.exit(0)
        continue
    if not group_exists():
        sys.exit(0)
try:
    os.killpg(pgid, signal.SIGKILL)
except (ProcessLookupError, PermissionError):
    pass
for argv in cleanup:
    try:
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=cleanup_timeout)
    except Exception:
        pass
'''


def _release(write_fd: int) -> None:
    """The owner finished with the process: tell the sidecar to stand down, then close our end. Best effort (the sidecar also stands down on its own when the group is gone)."""
    try:
        os.write(write_fd, b"R")
    except OSError:
        pass
    try:
        os.close(write_fd)
    except OSError:
        pass


def start_owner_watchdog(process: Any, cleanup: Sequence[Sequence[str]]) -> None:
    """Guard ``process`` (the root of its own process group, ``start_new_session``) against the death of the calling process. Raises on any failure so the spawn point can
    refuse the process (an unguarded process must not be handed to a caller)."""
    pgid = process.pid                                  # the provider's spawn_options start the child as its own session, so its process group id IS its pid. (os.getpgid raises ESRCH on macOS for a child that already exited unreaped - a fast command must not be refused.)
    read_fd, write_fd = os.pipe()                       # both ends are non-inheritable: the guarded child can never hold the lifeline open
    try:
        watchdog = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", WATCHDOG_SOURCE, str(pgid), json.dumps([list(argv) for argv in cleanup]),
             str(POLL_SECONDS), str(CLEANUP_TIMEOUT_SECONDS)],
            stdin=read_fd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,    # stdin IS the lifeline: nothing is passed by descriptor number (D2: no inheritable capability)
        )
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    process._kriya_owner_lifeline = write_fd            # the lifeline lives as long as the process object (and, at the latest, the owner)
    process._kriya_owner_watchdog = watchdog            # observable by tests; also keeps the sidecar's Popen (and so its reaping) tied to the guarded process object
    finalizer = weakref.finalize(process, _release, write_fd)
    finalizer.atexit = False                           # a normal owner exit closes the pipe WITHOUT the release byte: anything still running is stopped with its owner
