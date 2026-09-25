"""The one place Kriya decides where its persistent run history (traces.db)
lives. It is state, not log output, so it never follows ``paths.logs`` or the
log directory, and never derives from the process CWD.

State directory, first match wins:
- ``KRIYA_STATE_DIR`` (operator environment; must be absolute);
- ``paths.state`` (config): canonicalized once at config load by
  kriya/config/config.py::resolve_config_state, where a relative value
  resolves against the config file's own directory, and SEC-009-classified
  like every other ``paths.*`` field;
- ``~/.kriya/state``.

The trace database is ``<state>/traces.db``. A pre-existing
``<paths.logs>/traces.db`` is a *legacy* database: reported by ``kriya
traces`` and ``kriya doctor --production``, and copied only by the explicit
``kriya traces --migrate-legacy``. It is never moved, deleted, or merged.

Workspace run control state (``.kriya/``: run lock, RunRecords, checkpoints)
is deliberately not affected: recovery depends on it living in the workspace.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple

ENV_STATE_DIR = "KRIYA_STATE_DIR"
TRACE_DB_FILENAME = "traces.db"

STATE_DIR_SOURCE_ENV = "env"
STATE_DIR_SOURCE_CONFIG = "config"
STATE_DIR_SOURCE_DEFAULT = "default"


class StateDirectoryError(ValueError):
    """The state directory is relative (outside config resolution) or invalid."""


class LegacyTraceMigrationError(RuntimeError):
    """An explicit legacy trace migration was refused."""


def default_state_directory() -> str:
    """``~/.kriya/state``, computed at call time (tests point HOME elsewhere)."""
    return os.path.join(os.path.expanduser("~"), ".kriya", "state")


def _absolute(value: str, origin: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateDirectoryError(f"{origin} must be a non-empty directory path.")
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        raise StateDirectoryError(
            f"{origin} must be an absolute path here (got {value!r}); Kriya never resolves "
            "a state directory against the current working directory."
        )
    return os.path.realpath(expanded)


def resolve_state_directory(cfg) -> Tuple[str, str]:
    """(directory, source) with precedence env > config > default. Pure."""
    env_value = os.environ.get(ENV_STATE_DIR)
    if env_value:
        return _absolute(env_value, ENV_STATE_DIR), STATE_DIR_SOURCE_ENV
    configured = getattr(cfg.paths, "state", None)
    if configured is not None:
        # load_config() already made this absolute (anchored at the config
        # file's directory); a directly built AppConfig must do the same.
        return _absolute(configured, "paths.state"), STATE_DIR_SOURCE_CONFIG
    return os.path.realpath(default_state_directory()), STATE_DIR_SOURCE_DEFAULT


def trace_db_path(cfg) -> str:
    return os.path.join(resolve_state_directory(cfg)[0], TRACE_DB_FILENAME)


def legacy_trace_db_path(cfg) -> Optional[str]:
    """``<paths.logs>/traces.db`` when it exists and is not the current trace
    database; None otherwise. Only an absolute ``paths.logs`` is considered
    (a relative one would be CWD-anchored)."""
    logs = getattr(cfg.paths, "logs", None)
    if not isinstance(logs, str) or not os.path.isabs(os.path.expanduser(logs)):
        return None
    legacy = os.path.realpath(os.path.join(os.path.expanduser(logs), TRACE_DB_FILENAME))
    if not os.path.isfile(legacy):
        return None
    current = trace_db_path(cfg)
    if os.path.exists(current) and os.path.samefile(legacy, current):
        return None
    if os.path.realpath(current) == legacy:
        return None
    return legacy


@dataclass(frozen=True)
class LegacyTraceMigration:
    source: str
    target: str
    runs: int


def migrate_legacy_trace_db(cfg) -> LegacyTraceMigration:
    """Copy the legacy trace database to the canonical location.

    Explicit only. Refuses when there is no legacy database or when the target
    already exists (two histories are never merged). Copies with SQLite's
    online backup so a WAL-mode database is copied consistently; the legacy
    file is left exactly where it was."""
    source = legacy_trace_db_path(cfg)
    if source is None:
        raise LegacyTraceMigrationError("No legacy trace database found under paths.logs.")
    target = trace_db_path(cfg)
    if os.path.exists(target):
        raise LegacyTraceMigrationError(
            f"{target} already exists; refusing to merge it with {source}. "
            "Move one of them aside first if you want the other."
        )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    partial = target + ".migrating"
    if os.path.exists(partial):
        os.unlink(partial)
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(partial)
        try:
            src.backup(dst)
            try:
                runs = dst.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            except sqlite3.Error:
                runs = 0
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(partial, target)
    return LegacyTraceMigration(source=source, target=target, runs=runs)
