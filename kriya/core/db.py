import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

_orig_connect = sqlite3.connect

def wal_connect(*args, **kwargs):
    kwargs["timeout"] = 30.0
    conn = _orig_connect(*args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception as e:
        logger.warning(f"Failed to execute WAL/synchronous pragmas (concurrent access may see 'database is locked' errors): {e}")
    return conn

# Globally patch sqlite3.connect
sqlite3.connect = wal_connect

def get_connection(db_path: str) -> sqlite3.Connection:
    """Returns a connection to SQLite with WAL mode and timeout configuration.

    ":memory:" (PRV-11, 2026-08-31) is sqlite3's own special connection
    string for a private, ephemeral, in-process database - never a real
    filesystem path. Passed through verbatim, never touched by abspath/
    makedirs: os.path.abspath(":memory:") does NOT preserve it (it resolves
    to a literal path ending in "/:memory:"), and the makedirs()/connect()
    below would then create real directories and a real on-disk file with
    that exact name - found live: a bounded, in-memory-only dependency
    graph (kriya/workflow/workflow_controller.py's build_planning_
    structural_evidence()) ended up creating and committing a real
    ":memory:" file into the generated workspace, this function's own
    caller-agnostic os.path.abspath() being the actual root cause (every
    real, persisted db_path this function has ever been called with before
    is completely unaffected)."""
    if db_path == ":memory:":
        return sqlite3.connect(db_path)
    db_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return sqlite3.connect(db_path)
