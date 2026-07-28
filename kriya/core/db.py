import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

_orig_connect = sqlite3.connect

def wal_connect(*args, **kwargs):
    kwargs["timeout"] = 30.0
    conn = _orig_connect(*args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception as e:
        logger.debug(f"Failed to execute WAL/synchronous pragmas: {e}")
    return conn

# Globally patch sqlite3.connect
sqlite3.connect = wal_connect

def get_connection(db_path: str) -> sqlite3.Connection:
    """Returns a connection to SQLite with WAL mode and timeout configuration."""
    db_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return sqlite3.connect(db_path)
