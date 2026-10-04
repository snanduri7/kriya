"""Snapshot store: paths, manifests, listing and integrity checks. Pure helpers shared by acquisition and
inspection; only acquisition (kriya/kup/acquire.py) ever writes here."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.request import pathname2url

from kriya.core import db as _db
from kriya.core.state_paths import resolve_state_directory
from kriya.kup import policy

# kriya/core/db.py monkey-patches sqlite3.connect with a WAL pragma (a write) for every caller in the process. KUP
# never uses the patched function: the inspector must not even attempt a write, and the acquisition source
# connection must not change the source's journal mode (gate).
RAW_CONNECT = _db._orig_connect  # pylint: disable=protected-access

SNAPSHOT_ID_RE = re.compile(r"^\d{8}T\d{6}\d{6}Z-[0-9a-f]{8}$")


def read_only_uri(path: str) -> str:
    return "file:" + pathname2url(os.path.abspath(path)) + "?mode=ro"


def connect_read_only(path: str, timeout: float = policy.BUSY_TIMEOUT_SECONDS) -> sqlite3.Connection:
    """A read-only connection with NO write-capable pragma. ``temp_store=MEMORY`` is connection-local and keeps
    SQLite's scratch storage off the filesystem (measured A1)."""
    conn = RAW_CONNECT(read_only_uri(path), uri=True, timeout=timeout)
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def snapshot_directory(cfg) -> str:
    """``<state dir>/kup-snapshots`` - see the package docstring for the authority trace."""
    return os.path.join(resolve_state_directory(cfg)[0], policy.SNAPSHOT_DIRNAME)


def trace_store_path(cfg) -> str:
    return os.path.join(resolve_state_directory(cfg)[0], "traces.db")


def stat_metadata(path: str) -> Optional[Dict[str, Any]]:
    """Source metadata by ``stat`` only (no open, no lock): size, mtime_ns, inode, side-file sizes. None when absent."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        return {"error": f"{type(error).__name__}: {error}"}
    out: Dict[str, Any] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "inode": st.st_ino}
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            out[f"{suffix[1:]}_size"] = os.stat(path + suffix).st_size
        except OSError:
            out[f"{suffix[1:]}_size"] = None
    return out


def metadata_differs(before: Optional[Dict[str, Any]], now: Optional[Dict[str, Any]]) -> Optional[bool]:
    """True when ANY observed metadata differs. Equality means only that the metadata matches - never that
    the contents are unchanged (gate C-3)."""
    if before is None or now is None or "error" in (before or {}) or "error" in (now or {}):
        return None
    keys = ("size", "mtime_ns", "inode", "wal_size", "shm_size", "journal_size")
    return any(before.get(k) != now.get(k) for k in keys)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    directory: str
    db_path: str
    manifest: Dict[str, Any]


class SnapshotError(Exception):
    def __init__(self, code: str, message: str, database_state: Optional[str] = None, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.database_state = database_state
        self.extra = extra


def _read_manifest(directory: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(directory, policy.MANIFEST_FILENAME), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) and manifest.get("manifest_version") == policy.MANIFEST_VERSION else None


def list_snapshots(snapshot_dir: str) -> List[Snapshot]:
    """Published snapshots, newest first (ids sort chronologically). Staging directories are never listed."""
    out: List[Snapshot] = []
    try:
        names = os.listdir(snapshot_dir)
    except FileNotFoundError:
        return out
    for name in sorted(names, reverse=True):
        if not SNAPSHOT_ID_RE.match(name):
            continue
        directory = os.path.join(snapshot_dir, name)
        manifest = _read_manifest(directory)
        if manifest is None:
            continue
        out.append(Snapshot(name, directory, os.path.join(directory, policy.SNAPSHOT_FILENAME), manifest))
    return out


def cheap_integrity(snapshot: Snapshot) -> Optional[str]:
    """Size/mtime/mode of the snapshot file against its manifest (every query). Full digest verification is
    ``verify_digest`` (acquisition and ``--snapshots --verify``). Returns a reason or None."""
    try:
        st = os.stat(snapshot.db_path)
    except OSError as error:
        return f"snapshot file unreadable: {error}"
    want = snapshot.manifest.get("snapshot_file", {})
    if st.st_size != want.get("size") or st.st_mtime_ns != want.get("mtime_ns"):
        return "snapshot file size or mtime differs from its manifest"
    return None


def verify_digest(snapshot: Snapshot) -> Optional[str]:
    want = snapshot.manifest.get("snapshot_file", {}).get("sha256")
    got = sha256_file(snapshot.db_path)
    return None if got == want else f"snapshot digest {got[:16]} differs from manifest {str(want)[:16]}"


def require_snapshot(snapshot_dir: str, snapshot_id: str) -> Snapshot:
    """The published snapshot with exactly this id, cheaply integrity-checked. Never substitutes another one."""
    if not SNAPSHOT_ID_RE.match(snapshot_id or ""):
        raise SnapshotError(policy.INVALID_REQUEST, "snapshot_id has an unexpected form")
    published = list_snapshots(snapshot_dir)
    if not published:
        raise SnapshotError(policy.SNAPSHOT_MISSING, "no published snapshot exists; acquire one first")
    for snap in published:
        if snap.snapshot_id == snapshot_id:
            reason = cheap_integrity(snap)
            if reason:
                raise SnapshotError(policy.SNAPSHOT_CORRUPT, reason, snapshot_id=snapshot_id)
            return snap
    raise SnapshotError(policy.SNAPSHOT_UNAVAILABLE, f"snapshot {snapshot_id} is not published (pruned or never existed)", snapshot_id=snapshot_id)


def snapshot_summary(snap: Snapshot, source_now: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m = snap.manifest
    return {
        "snapshot_id": snap.snapshot_id,
        "acquisition_started_at": m.get("acquisition_started_at"),
        "acquisition_completed_at": m.get("acquisition_completed_at"),
        "source": m.get("source"),
        "source_metadata_at_acquisition": m.get("source_metadata_at_acquisition"),
        "source_metadata_now": source_now,
        "source_metadata_changed": metadata_differs(m.get("source_metadata_at_acquisition"), source_now),
        "rows": m.get("rows"),
        "size": m.get("snapshot_file", {}).get("size"),
        "sqlite_version": m.get("sqlite_version"),
    }
