"""Snapshot acquisition, retention and cleanup (gate C-1, C-2, C-4): the only module under kriya/kup that writes.

Writes, all inside ``<state dir>/kup-snapshots`` (owned by acquisition) plus SQLite's own ``-wal``/``-shm`` of the
source (permitted by the gate; a side effect of opening a WAL store, never a page of the main file):
``.acquire.lock`` (the exclusive-acquirer lock file), ``staging-<id>/`` (the copy in progress; destination-only
pragmas), ``<id>/`` (the published snapshot: ``traces.snapshot.db`` 0400, ``manifest.json`` 0400, directory 0700),
and the deletion of surplus published snapshots and of orphaned staging directories.

Concurrency: one acquirer at a time, by an exclusive advisory lock through the platform lock port (the same
crash-safe primitive the run lock uses: released by the OS when the process dies). While the lock is held no other
acquisition or prune can run, so a staging directory found under the lock is PROVEN abandoned (its owner is gone)
and may be removed. A second caller gets ``ACQUISITION_IN_PROGRESS`` immediately; it never waits or steals.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

from kriya.kup import policy
from kriya.kup.store import (
    RAW_CONNECT,
    Snapshot,
    SnapshotError,
    list_snapshots,
    read_only_uri,
    sha256_file,
    stat_metadata,
    verify_digest,
)
from kriya.platform.services import platform_services

StepHook = Optional[Callable[[Dict[str, Any]], None]]


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _new_snapshot_id() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "Z-" + secrets.token_hex(4)


@contextmanager
def acquisition_lock(snapshot_dir: str) -> Iterator[None]:
    os.makedirs(snapshot_dir, mode=0o700, exist_ok=True)
    try:
        os.chmod(snapshot_dir, 0o700)
    except OSError:
        pass
    lock_path = os.path.join(snapshot_dir, policy.LOCK_FILENAME)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    lock = platform_services().workspace_lock
    try:
        if not lock.try_exclusive(fd):
            raise SnapshotError(policy.ACQUISITION_IN_PROGRESS, "another acquisition or prune holds the snapshot directory")
        try:
            yield
        finally:
            lock.release(fd)
    finally:
        os.close(fd)


def _remove_orphaned_staging(snapshot_dir: str) -> List[str]:
    """Under the lock: every staging directory belongs to a dead acquirer. Only our own prefix, inside our own dir."""
    removed = []
    for name in sorted(os.listdir(snapshot_dir)):
        if name.startswith(policy.STAGING_PREFIX):
            shutil.rmtree(os.path.join(snapshot_dir, name), ignore_errors=True)
            removed.append(name)
    return removed


def _classify_open_error(error: sqlite3.Error, source: str) -> SnapshotError:
    name = getattr(error, "sqlite_errorname", "") or ""
    if not os.path.exists(source):
        return SnapshotError(policy.READ_ONLY_UNAVAILABLE, "no trace database exists at the resolved store path", policy.DB_STATE_MISSING)
    if name == "SQLITE_READONLY_ROLLBACK":
        return SnapshotError(policy.STORE_BUSY, "the store has a hot rollback journal; acquisition never recovers another writer's transaction", policy.DB_STATE_HOT_JOURNAL)
    if name in ("SQLITE_BUSY", "SQLITE_LOCKED"):
        return SnapshotError(policy.STORE_BUSY, f"the store is locked ({name}) beyond the {policy.BUSY_TIMEOUT_SECONDS}s busy timeout", policy.DB_STATE_LOCKED)
    if name in ("SQLITE_READONLY_DIRECTORY", "SQLITE_CANTOPEN"):
        return SnapshotError(policy.READ_ONLY_UNAVAILABLE, f"the store cannot be opened read-only ({name}: {error})", policy.DB_STATE_READONLY_DIRECTORY if name == "SQLITE_READONLY_DIRECTORY" else policy.DB_STATE_UNREADABLE)
    if name == "SQLITE_NOTADB":
        return SnapshotError(policy.READ_ONLY_UNAVAILABLE, "the file at the store path is not a SQLite database", policy.DB_STATE_NOT_A_DATABASE)
    return SnapshotError(policy.READ_ONLY_UNAVAILABLE, f"{name or type(error).__name__}: {error}", policy.DB_STATE_UNREADABLE)


def _free_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def acquire_snapshot(source: str, snapshot_dir: str, *, step_hook: StepHook = None,
                     deadline_seconds: float = policy.ACQUISITION_DEADLINE_SECONDS,
                     max_bytes: int = policy.MAX_SNAPSHOT_BYTES, retain: int = policy.RETAIN_SNAPSHOTS,
                     run_active_check: Optional[Callable[[], Optional[str]]] = None,
                     step_pages: int = policy.BACKUP_STEP_PAGES) -> Dict[str, Any]:
    """Acquire one consistent snapshot of ``source`` into ``snapshot_dir`` and publish it. Returns the summary.

    ``step_hook`` (tests only) is called after every backup step with progress; production passes None.
    ``run_active_check`` returns a description when a run is active for the selected workspace -> refused.
    """
    started_at = _utc_now()
    t0 = time.monotonic()
    if run_active_check is not None:
        active = run_active_check()
        if active:
            raise SnapshotError(policy.ACQUISITION_REFUSED_RUN_ACTIVE, f"a run is active for the selected workspace ({active}); acquisition is refused by policy")
    # A missing store is refused BEFORE the snapshot directory or the lock file exist: nothing is created for a
    # store that cannot be acquired.
    if stat_metadata(source) is None:
        raise SnapshotError(policy.READ_ONLY_UNAVAILABLE, "no trace database exists at the resolved store path", policy.DB_STATE_MISSING)
    with acquisition_lock(snapshot_dir):
        orphans = _remove_orphaned_staging(snapshot_dir)
        source_before = stat_metadata(source)
        if source_before is None:
            raise SnapshotError(policy.READ_ONLY_UNAVAILABLE, "no trace database exists at the resolved store path", policy.DB_STATE_MISSING)
        snapshot_id = _new_snapshot_id()
        staging = os.path.join(snapshot_dir, policy.STAGING_PREFIX + snapshot_id)
        final_dir = os.path.join(snapshot_dir, snapshot_id)
        dest_path = os.path.join(staging, policy.SNAPSHOT_FILENAME)
        os.mkdir(staging, 0o700)
        src = dst = None
        try:
            try:
                src = RAW_CONNECT(read_only_uri(source), uri=True, timeout=policy.BUSY_TIMEOUT_SECONDS)
                src.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
                src.execute("PRAGMA wal_autocheckpoint=0")
                src.execute("PRAGMA temp_store=MEMORY")
                # ONE read transaction for the size check and the whole backup: the backup reads through this
                # connection's pager, so every step sees the same committed image (verified by the generation
                # fixtures in tests/test_kup_acquisition.py).
                src.execute("BEGIN")
                page_count, page_size = src.execute("SELECT (SELECT page_count FROM pragma_page_count()), (SELECT page_size FROM pragma_page_size())").fetchone()
                journal_mode = src.execute("PRAGMA journal_mode").fetchone()[0]
            except sqlite3.Error as error:
                raise _classify_open_error(error, source) from error
            expected = int(page_count) * int(page_size)
            if expected > max_bytes:
                raise SnapshotError(policy.SNAPSHOT_TOO_LARGE, f"the store image is {expected} bytes; the per-snapshot bound is {max_bytes} bytes", expected_bytes=expected)
            free = _free_bytes(snapshot_dir)
            if free < expected + policy.FREE_SPACE_MARGIN_BYTES:
                raise SnapshotError(policy.SNAPSHOT_FAILED, f"insufficient free space: {free} bytes available, {expected + policy.FREE_SPACE_MARGIN_BYTES} needed", reason="insufficient_space")
            dst = RAW_CONNECT(dest_path)
            progress = _StepGuard(dest_path, max_bytes, expected, deadline_seconds, t0, step_hook)
            # Python's Connection.backup loops over sqlite3_backup_step(pages) and calls ``progress`` after every
            # step; an exception raised there aborts the backup. That callback is where the destination-growth
            # bound, the deadline and the test barrier hook run - between steps, never inside one.
            src.backup(dst, pages=step_pages, progress=progress, sleep=0.0)
            steps = progress.steps
            src.execute("COMMIT")
            source_after_backup = stat_metadata(source)
            # Destination-only: rollback journal (A1 C01 - the state that reads with zero writes), verification.
            journal_dest = dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            quick = dst.execute("PRAGMA quick_check").fetchone()[0]
            rows = dst.execute("SELECT COUNT(*) FROM runs").fetchone()[0] if dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'").fetchone() else None
            dst.close()
            dst = None
            src.close()
            src = None
            if journal_dest.lower() != "delete":
                raise SnapshotError(policy.SNAPSHOT_FAILED, f"destination journal mode is {journal_dest!r}, not delete", reason="journal_mode")
            if quick != "ok":
                raise SnapshotError(policy.SNAPSHOT_CORRUPT, f"quick_check on the copy: {quick}")
            digest = sha256_file(dest_path)
            os.chmod(dest_path, 0o400)
            st = os.stat(dest_path)
            manifest = {
                "manifest_version": policy.MANIFEST_VERSION,
                "snapshot_id": snapshot_id,
                "acquisition_started_at": started_at,
                "acquisition_completed_at": _utc_now(),
                "source": os.path.abspath(source),
                "source_metadata_at_acquisition": source_before,
                "source_metadata_after_backup": source_after_backup,
                "source_journal_mode": journal_mode,
                "source_page_count": int(page_count), "source_page_size": int(page_size),
                "backup_steps": steps,
                "snapshot_file": {"name": policy.SNAPSHOT_FILENAME, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest},
                "rows": rows,
                "quick_check": quick,
                "sqlite_version": sqlite3.sqlite_version,
                "implementation": policy.PROTOCOL_IMPLEMENTATION,
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
            }
            manifest_path = os.path.join(staging, policy.MANIFEST_FILENAME)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(manifest_path, 0o400)
            os.rename(staging, final_dir)  # atomic publish
            dir_fd = os.open(snapshot_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            for conn in (dst, src):
                try:
                    if conn is not None:
                        conn.close()
                except sqlite3.Error:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            raise
        pruned = _retain(snapshot_dir, retain)
    published = Snapshot(snapshot_id, final_dir, os.path.join(final_dir, policy.SNAPSHOT_FILENAME), manifest)
    bad = verify_digest(published)
    if bad:
        raise SnapshotError(policy.SNAPSHOT_CORRUPT, bad, snapshot_id=snapshot_id)
    return {"snapshot": published, "orphans_removed": orphans, "pruned": pruned, "manifest": manifest}


class _StepGuard:
    """Per-step checks for the backup (gate C-4): destination growth, deadline, optional test hook."""

    def __init__(self, dest_path: str, max_bytes: int, expected: int, deadline_seconds: float, t0: float, step_hook: StepHook) -> None:
        self.dest_path, self.max_bytes, self.expected, self.deadline_seconds, self.t0, self.step_hook = dest_path, max_bytes, expected, deadline_seconds, t0, step_hook
        self.steps = 0

    def __call__(self, _status: int, remaining: int, total: int) -> None:
        self.steps += 1
        grown = os.path.getsize(self.dest_path) if os.path.exists(self.dest_path) else 0
        if grown > self.max_bytes:
            raise SnapshotError(policy.SNAPSHOT_TOO_LARGE, f"destination grew to {grown} bytes (bound {self.max_bytes})", expected_bytes=self.expected)
        if time.monotonic() - self.t0 > self.deadline_seconds:
            raise SnapshotError(policy.SNAPSHOT_FAILED, f"acquisition exceeded its {self.deadline_seconds}s deadline", reason="deadline_exceeded")
        if self.step_hook is not None:
            self.step_hook({"step": self.steps, "remaining_pages": remaining, "total_pages": total, "destination_bytes": grown})


def _retain(snapshot_dir: str, retain: int) -> List[str]:
    """Keep the newest ``retain`` published snapshots; remove the rest (whole directories, owned artifacts only)."""
    removed = []
    for snap in list_snapshots(snapshot_dir)[max(retain, 0):]:
        _remove_snapshot_dir(snap.directory)
        removed.append(snap.snapshot_id)
    return removed


def _remove_snapshot_dir(directory: str) -> None:
    for name in (policy.SNAPSHOT_FILENAME, policy.MANIFEST_FILENAME):
        p = os.path.join(directory, name)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    shutil.rmtree(directory, ignore_errors=True)


def prune_snapshots(snapshot_dir: str, keep: int = policy.RETAIN_SNAPSHOTS) -> Dict[str, Any]:
    """Explicit prune: keep the newest ``keep`` (0 removes every snapshot, e.g. to delete duplicated prompts),
    remove orphaned staging. Runs under the same lock as acquisition, so it never races one."""
    if not os.path.isdir(snapshot_dir):
        return {"removed": [], "orphans_removed": [], "kept": []}
    with acquisition_lock(snapshot_dir):
        orphans = _remove_orphaned_staging(snapshot_dir)
        removed = _retain(snapshot_dir, keep)
        kept = [s.snapshot_id for s in list_snapshots(snapshot_dir)]
    return {"removed": removed, "orphans_removed": orphans, "kept": kept}
