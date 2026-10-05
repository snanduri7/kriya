"""Append-only, hash-chained writer for one run's attempt evidence.

LR-R1-M1 design §4, §6, §7, §9. One writer per run (one process). The store
lives at ``<state_dir>/attempt-evidence/<run_id>/``. Its file layout is
internal (invariant I-1): consumers use ``reader.py``.

Write discipline (design §6.6):
- every record is passed to ``os.write`` (looped until complete) before
  ``append``/``record`` returns; ``records.jsonl`` is opened append-only and
  never truncated, sought or replaced;
- a content blob is written ``tmp -> fsync -> link`` before the record that
  references it;
- ``records.jsonl`` is fsynced only by ``sync()`` (attempt/unit/run
  boundaries) and before the seal; the seal is ``tmp -> fsync -> rename``,
  then the directory is fsynced.

``record()`` never raises an ``Exception`` (design §9.2): a failure degrades
the writer, is counted, and surfaces in the seal. ``BaseException``
(cancellation, KeyboardInterrupt) always propagates.
"""
from __future__ import annotations

import datetime
import gzip
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional

from kriya.core.attempt_evidence import model

logger = logging.getLogger(__name__)

STORE_DIRNAME = "attempt-evidence"
_RECORDS = "records.jsonl"
_MANIFEST = "manifest.json"
_SEAL = "seal.json"
_BLOBS = "blobs"
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
_IDENTITY_KEYS = ("run_id", "unit_id", "unit_kind", "invocation_seq", "phase", "attempt_number", "call_seq",
                  "wire_seq", "role")


class RecorderUnavailable(Exception):
    """The store could not be opened. Observational only (D5): the caller
    logs RECORDER_UNAVAILABLE and the run continues unchanged."""

    reason_code = "RECORDER_UNAVAILABLE"


def store_root(state_dir: str) -> str:
    return os.path.join(state_dir, STORE_DIRNAME)


def run_directory(state_dir: str, run_id: str) -> str:
    return os.path.join(store_root(state_dir), run_id)


def _mkdir_private(path: str, *, exist_ok: bool) -> None:
    try:
        os.mkdir(path, _DIR_MODE)
    except FileExistsError:
        if not exist_ok:
            raise
        if not os.path.isdir(path):
            raise
    os.chmod(path, _DIR_MODE)  # explicit, independent of the process umask


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _create_private_file(path: str, data: bytes) -> None:
    """Create-exclusive, 0600, fully written and fsynced."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        os.chmod(path, _FILE_MODE)
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return  # platforms without directory handles; the rename itself is atomic
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _now_wall() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AttemptEvidenceWriter:
    """One run's store. Construction opens it or raises RecorderUnavailable."""

    def __init__(self, state_dir: str, run_id: str, *, capture: str, manifest: Mapping[str, Any]) -> None:
        if capture not in model.CAPTURE_MODES or capture == model.CAPTURE_OFF:
            raise ValueError(f"capture mode {capture!r} does not open a store")
        if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
            raise RecorderUnavailable(f"run id {run_id!r} is not a safe directory name")
        self.run_id = run_id
        self.capture = capture
        self.directory = run_directory(state_dir, run_id)
        self._lock = threading.Lock()
        self._seq = 0
        self._prev = model.GENESIS_PREV
        self._blob_count = 0
        self._blobs_enabled = capture in (model.CAPTURE_FULL, model.CAPTURE_FULL_WITH_REASONING)
        self._dead = False
        self._sealed = False
        self.gaps = 0
        self.degraded: List[str] = []
        try:
            os.makedirs(state_dir, exist_ok=True)
            _mkdir_private(store_root(state_dir), exist_ok=True)
            _mkdir_private(self.directory, exist_ok=False)
            _mkdir_private(os.path.join(self.directory, _BLOBS), exist_ok=False)
            header = {
                "schema": model.SCHEMA, "writer_version": model.WRITER_VERSION,
                "redaction_policy_version": model.REDACTION_POLICY_VERSION, "capture": capture,
                "run_id": run_id, **dict(manifest),
            }
            _create_private_file(os.path.join(self.directory, _MANIFEST), model.canonical_bytes(header) + b"\n")
            self._fd = os.open(os.path.join(self.directory, _RECORDS),
                               os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, _FILE_MODE)
            os.chmod(os.path.join(self.directory, _RECORDS), _FILE_MODE)
        except FileExistsError as error:
            raise RecorderUnavailable(f"a store for run {run_id!r} already exists: {error}") from error
        except OSError as error:
            raise RecorderUnavailable(f"{type(error).__name__}: {error}") from error

    # -- properties --------------------------------------------------------

    @property
    def active(self) -> bool:
        return not self._dead and not self._sealed

    @property
    def capture_reasoning(self) -> bool:
        return self.capture == model.CAPTURE_FULL_WITH_REASONING

    @property
    def seq(self) -> int:
        return self._seq

    # -- writing -----------------------------------------------------------

    def _blob(self, data: bytes) -> str:
        ref = model.digest(data)
        hexdigest = ref.split(":", 1)[1]
        shard = os.path.join(self.directory, _BLOBS, hexdigest[:2])
        final = os.path.join(shard, hexdigest + ".gz")
        if os.path.exists(final):
            return ref
        _mkdir_private(shard, exist_ok=True)
        tmp = os.path.join(shard, f".tmp-{uuid.uuid4().hex}")
        try:
            _create_private_file(tmp, gzip.compress(data, mtime=0))
            try:
                os.link(tmp, final)
            except FileExistsError:
                pass  # same digest = same bytes; first writer wins
            else:
                self._blob_count += 1
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        return ref

    def append(self, kind: str, payload: Mapping[str, Any], *, identity: Optional[Mapping[str, Any]] = None,
               provenance: str = model.OBSERVED, content: Optional[Mapping[str, Any]] = None) -> int:
        """Append one record; raises on any failure. Returns its seq."""
        if kind not in model.KINDS:
            raise ValueError(f"unknown record kind {kind!r}")
        if provenance not in model.PROVENANCES:
            raise ValueError(f"unknown provenance {provenance!r}")
        with self._lock:
            if self._dead:
                raise RecorderUnavailable("the store stopped accepting records: " + "; ".join(self.degraded))
            if self._sealed:
                raise RecorderUnavailable("the store is sealed")
            content_digests: Dict[str, Any] = {}
            blobs: Dict[str, str] = {}
            for name, value in (content or {}).items():
                if value is None:
                    continue
                data = model.as_bytes(value)
                content_digests[name] = model.content_digest_entry(data)
                if self._blobs_enabled:
                    try:
                        blobs[name] = self._blob(data)
                    except OSError as error:
                        self._blobs_enabled = False
                        self._note_degraded(f"blob write failed ({type(error).__name__}: {error}); "
                                            "content is recorded as digests from here on")
            ids = dict(identity or {})
            record: Dict[str, Any] = {
                "schema": model.SCHEMA, "seq": self._seq + 1, "prev": self._prev, "kind": kind,
                **{key: ids.get(key) for key in _IDENTITY_KEYS},
                "t_wall": _now_wall(), "t_mono_ms": int(time.monotonic() * 1000),
                "provenance": provenance, "payload": dict(payload),
                "content_digests": content_digests, "blobs": blobs,
            }
            if record["run_id"] is None:
                record["run_id"] = self.run_id
            if record["run_id"] != self.run_id:
                raise ValueError(f"record for run {record['run_id']!r} offered to the store of {self.run_id!r}")
            line = model.canonical_bytes(record)
            try:
                _write_all(self._fd, line + b"\n")
            except OSError as error:
                self._dead = True
                self._note_degraded(f"records write failed ({type(error).__name__}: {error})")
                raise
            self._seq = record["seq"]
            self._prev = model.digest(line)
            return self._seq

    def _note_degraded(self, reason: str) -> None:
        if reason not in self.degraded:
            self.degraded.append(reason)
            logger.warning("Attempt evidence for run %s degraded: %s", self.run_id, reason)

    def record(self, kind: str, payload: Mapping[str, Any], **kwargs: Any) -> Optional[int]:
        """``append`` that never raises an Exception: a failure is counted as
        a gap and degrades the store (design §9.2)."""
        try:
            return self.append(kind, payload, **kwargs)
        except Exception as error:  # observational: never alters the run
            self.gaps += 1
            self._note_degraded(f"{kind} not recorded ({type(error).__name__}: {error})")
            if not self._dead and kind != "recorder.gap":
                try:
                    self.append("recorder.gap", {"lost_kind": kind, "error_type": type(error).__name__,
                                                 "error": str(error)[:500]})
                except Exception:  # the gap record itself failed: the seal still counts it
                    pass
            return None

    def gap(self, lost: str, reason: str, *, identity: Optional[Mapping[str, Any]] = None) -> None:
        """An explicit gap the caller knows about (e.g. no scope)."""
        self.gaps += 1
        self.record("recorder.gap", {"lost_kind": lost, "reason": reason}, identity=identity)

    def sync(self) -> None:
        """fsync records.jsonl (attempt/unit/run boundaries only)."""
        with self._lock:
            if self._dead or self._sealed:
                return
            try:
                os.fsync(self._fd)
            except OSError as error:
                self._note_degraded(f"fsync failed ({type(error).__name__}: {error})")

    def seal(self, closed_reason: str) -> bool:
        """Write seal.json once and close the records file. True when sealed."""
        with self._lock:
            if self._sealed:
                return True
            self._sealed = True
            try:
                if not self._dead:
                    os.fsync(self._fd)
                seal = {
                    "schema": model.SCHEMA, "final_seq": self._seq, "head_digest": self._prev,
                    "record_count": self._seq, "blob_count": self._blob_count,
                    "complete": not self.degraded and self.gaps == 0 and not self._dead,
                    "gaps": self.gaps, "degraded": list(self.degraded), "closed_reason": closed_reason,
                }
                tmp = os.path.join(self.directory, f".{_SEAL}.tmp")
                _create_private_file(tmp, json.dumps(seal, sort_keys=True, indent=1).encode("utf-8") + b"\n")
                os.replace(tmp, os.path.join(self.directory, _SEAL))
                _fsync_directory(self.directory)
                return True
            except OSError as error:
                self._note_degraded(f"seal failed ({type(error).__name__}: {error})")
                return False
            finally:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
