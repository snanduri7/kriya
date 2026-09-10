"""Repository/workspace run-ownership guard (CONC-001).

At most one mutating Kriya run may own a workspace at a time. Ownership is
proven by an OS advisory file lock (fcntl.flock(LOCK_EX | LOCK_NB)) held for
the acquiring process's lifetime, on a fixed file inside the workspace's own
`.kriya/` bookkeeping directory:

    <workspace_path>/.kriya/run.lock

The kernel lock is the sole correctness authority. Its automatic,
unconditional release the instant every file descriptor referencing it closes
- including on an unhandled crash or SIGKILL, with zero cleanup code required
- is exactly why this primitive was chosen over a hand-rolled PID/heartbeat
staleness scheme (see docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md,
CONC-001). The lock file's JSON *contents* (run_id/pid/hostname/acquired_at
plus the existing `workspace_identity()` stamp) are diagnostics only, read
best-effort to build a clearer contention error message - never consulted to
decide ownership. Corrupt, unreadable, or stale-looking content therefore
never blocks or wrongly grants acquisition; only the kernel lock does. PID
values are never used to decide staleness either, for the same reason: a
reused PID cannot inherit a lock it never itself opened.

Identity / worktree semantics:
  - Ownership key is the `workspace_path` as given, not a git-level "common
    directory" or toplevel. The lock file lives *inside* that directory tree,
    so any alias reaching the same real directory - a symlink, a relative
    path, a different cwd - opens the identical inode and contends for the
    identical lock automatically, with no canonicalization step required for
    correctness. `workspace_identity()` (kriya.control.workspace_identity,
    the same primitive `checkpoint.py` already uses) is still recorded in the
    diagnostic content purely so a contention error can name the canonical
    workspace - not a second identity model.
  - Two independent `git worktree` checkouts of the same repository are two
    different directories and therefore get two different, independently
    acquirable locks - correct, because git itself already refuses to check
    out the same branch into two worktrees at once, so two legitimate
    worktrees necessarily mutate disjoint working-tree file sets. Git's own
    internal locking (`.git/index.lock`, ref-transaction locks) already
    protects the object/ref database they share; this module does not
    duplicate that.

Architectural invariant: any future non-CLI entry point capable of
repository/workspace mutation MUST call `acquire_run_lock()` before its first
possible mutation, the same way `kriya/cli.py`'s generate/fix/proposal-execute
/milestone-execution paths do. Do not add nested locking or an in-process
registry, and do not wire this into `run_generation_workflow()` itself merely
to cover a hypothetical future caller - out of scope for this slice.

Deployment envelope: macOS + Linux CI (POSIX `fcntl.flock`, no Windows
support), single host only - this is not a distributed lock.
"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

from kriya.control.workspace_identity import ownership_metadata

_LOCK_RELPATH = os.path.join(".kriya", "run.lock")


class WorkspaceLockHeldError(RuntimeError):
    """Another Kriya mutating run already owns this workspace."""


def _lock_path(workspace_path: str) -> str:
    return os.path.join(workspace_path, _LOCK_RELPATH)


def _diagnostic_payload(workspace_path: str, run_id: str) -> bytes:
    payload = {
        "run_id": run_id,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "acquired_at": time.time(),
        "_workspace": ownership_metadata(workspace_path),
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def _describe_current_owner(fd: int) -> str:
    """Best-effort, diagnostics-only description of who holds the lock.

    Never used to decide ownership - only to make a contention error more
    readable. Any read/parse failure (including a torn write from a
    concurrently-updating holder) falls back to a generic message.
    """
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        payload = json.loads(os.read(fd, 65536).decode("utf-8"))
        run_id = payload.get("run_id", "unknown")
        pid = payload.get("pid", "unknown")
        acquired_at = payload.get("acquired_at")
        when = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(acquired_at))
            if isinstance(acquired_at, (int, float)) else "unknown time"
        )
        return f"run_id={run_id}, pid={pid}, acquired {when}"
    except Exception:
        return "owner details unavailable"


def _new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


@contextmanager
def acquire_run_lock(workspace_path: str, run_id: Optional[str] = None) -> Iterator[str]:
    """Acquire exclusive, process-lifetime ownership of `workspace_path`.

    Raises `WorkspaceLockHeldError` immediately (never blocks, never polls)
    if another process already holds it. Propagates `OSError`/
    `PermissionError` unchanged - fail closed, never silently proceeds - if
    the lock directory/file can't be created or opened. Always releases on
    normal exit or any exception via the context manager; released by the OS
    automatically even if the process is killed before that ever runs.
    """
    run_id = run_id or _new_run_id()
    lock_dir = os.path.join(workspace_path, ".kriya")
    os.makedirs(lock_dir, exist_ok=True)

    fd = os.open(_lock_path(workspace_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            owner = _describe_current_owner(fd)
            raise WorkspaceLockHeldError(
                f"workspace '{workspace_path}' is already owned by another Kriya "
                f"mutating run ({owner}). Wait for it to finish, or confirm no "
                f"Kriya process is actually running before retrying - the lock "
                f"releases automatically the instant that process exits."
            ) from None

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, _diagnostic_payload(workspace_path, run_id))
        except OSError:
            pass  # diagnostics only - never fail acquisition over this

        yield run_id
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
