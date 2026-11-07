"""APPLICATION: the run-log projection behind the cockpit's "Kriya Log" (owner ruling 2026-11-07).

The records are Kriya's OWN application log for one run: ``<log_dir>/runs/<run_id>/kriya.log``, written by the one logging owner (kriya/core/logging_setup.py) through ``RedactingFormatter`` (the one redaction
owner, kriya/foundation/redaction.py). This module does not log, tail a path a caller names, or invent structure: it reads that file for a validated run id, in pages, from an opaque byte-offset cursor, and
parses ONLY what the line format guarantees - ``<time> <LEVEL> <logger>: <message>`` - with a banner or continuation line attached to the record before it. No phase is inferred from message text (the cockpit
shows the authoritative phase separately). Every message is redacted AGAIN before it leaves (idempotent), a ``<think>...</think>`` span is replaced (hidden model reasoning is never shown even if a provider
emitted it into a log), and every page is bounded in bytes, records and characters. The file is opened without following a link, must stay inside ``<log_dir>/runs``, and a partial last line (the run is
still writing it) is held back until it is complete.

Cursor: ``o<offset>`` - the byte offset of the next unread complete line. An offset beyond the file (the log was replaced) restarts from 0 with ``reset: true``.
"""
from __future__ import annotations

import os
import re
import stat
from typing import Any, Dict, List, Optional

from kriya.foundation.redaction import redact_text

MAX_PAGE_BYTES = 128 * 1024
DEFAULT_LIMIT = 200
MAX_LIMIT = 500
MAX_MESSAGE_CHARS = 2000
CURSOR_RE = re.compile(r"^o[0-9]{1,12}$")
LINE_RE = re.compile(r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) (?P<logger>[A-Za-z0-9_.\-]+): ?(?P<message>.*)$")
_THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.S | re.I)
HEADER_PREFIX = "# Kriya run "

NO_RUN_LOG = "NO_RUN_LOG"
LOG_DIRECTORY_UNAVAILABLE = "LOG_DIRECTORY_UNAVAILABLE"
RUN_LOG_UNREADABLE = "RUN_LOG_UNREADABLE"


def parse_cursor(cursor: Optional[str]) -> int:
    """The byte offset a cursor names (0 for none); raises ValueError on anything else."""
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not CURSOR_RE.match(cursor):
        raise ValueError("cursor must be a value returned by a previous run.log page")
    return int(cursor[1:])


def _clean(message: str) -> str:
    return redact_text(_THINK_RE.sub("[model reasoning hidden]", message))[:MAX_MESSAGE_CHARS]


def _unavailable(run_id: str, reason: str) -> Dict[str, Any]:
    return {"run_id": run_id, "available": False, "reason": reason, "records": [], "next_cursor": None, "at_end": True, "size_bytes": 0, "reset": False}


def read_run_log(log_directory: str, run_id: str, offset: int = 0, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """One page of the run's log (see the module docstring). ``run_id`` is validated by the caller (a control id: no separator can occur in it)."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    runs_root = os.path.realpath(os.path.join(log_directory, "runs"))
    path = os.path.join(runs_root, run_id, "kriya.log")
    real = os.path.realpath(path)
    if os.path.commonpath([runs_root, real]) != runs_root or os.path.dirname(os.path.dirname(real)) != runs_root:
        return _unavailable(run_id, RUN_LOG_UNREADABLE)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return _unavailable(run_id, NO_RUN_LOG)
    except OSError:
        return _unavailable(run_id, RUN_LOG_UNREADABLE)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            return _unavailable(run_id, RUN_LOG_UNREADABLE)
        size = status.st_size
        reset = offset > size
        start = 0 if reset else offset
        os.lseek(descriptor, start, os.SEEK_SET)
        data = os.read(descriptor, MAX_PAGE_BYTES + 1)
    except OSError:
        return _unavailable(run_id, RUN_LOG_UNREADABLE)
    finally:
        os.close(descriptor)
    chunk = data[:MAX_PAGE_BYTES]
    if len(data) > MAX_PAGE_BYTES or not chunk.endswith(b"\n"):
        cut = chunk.rfind(b"\n")
        chunk = chunk[:cut + 1] if cut >= 0 else b""             # never emit a line that is still being written (or one cut by the page bound)
    records: List[Dict[str, Any]] = []
    consumed = 0
    position = 0
    page_full = False
    for raw in chunk.splitlines(keepends=True):
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        match = LINE_RE.match(text)
        if match is not None:
            if len(records) == limit:
                page_full = True
                break                                           # the page is full: this line starts the next page
            records.append({"offset": start + position, "time": match["time"], "level": match["level"], "logger": match["logger"], "message": match["message"]})
        elif records and not text.startswith(HEADER_PREFIX):
            records[-1]["message"] += "\n" + text               # a banner or continuation line belongs to the record before it
        position += len(raw)
        consumed = position
    for record in records:
        record["message"] = _clean(record["message"])
    next_offset = start + consumed
    at_end = len(data) <= MAX_PAGE_BYTES and not page_full         # the read reached the end of the file (a partial last line, still being written, waits for the next page)
    return {"run_id": run_id, "available": True, "reason": None, "records": records, "next_cursor": f"o{next_offset}", "at_end": at_end, "size_bytes": size, "reset": reset}
