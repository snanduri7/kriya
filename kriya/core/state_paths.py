"""The one place Kriya decides where its persistent run history (traces.db)
lives. It is state, not log output: independent of the log directory, and
never derived from the process CWD.

State directory, first match wins:
- ``KRIYA_STATE_DIR`` (operator environment; must be absolute);
- ``paths.state`` (config): canonicalized once at config load by
  kriya/config/config.py::resolve_config_state, where a relative value
  resolves against the config file's own directory. Inside the workspace it
  must sit beneath ``<workspace>/.kriya/``; outside it, SEC-009 path authority
  applies like every other ``paths.*`` field;
- ``~/.kriya/state``.

The trace database is ``<state>/traces.db``. A pre-existing database from
before this move is copied only by the explicit ``kriya traces
--migrate-legacy [--legacy-path <traces.db>]``; the one location detected
without ``--legacy-path`` is the historical packaged default,
``<install dir>/logs/traces.db``. Nothing is ever moved, deleted, or merged.

Workspace run control state (``<workspace>/.kriya/``: run lock, RunRecords,
checkpoints) is separate and stays in the workspace: recovery depends on it.
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


WORKSPACE_STATE_PARENT = ".kriya"


def require_workspace_local_state_under_kriya_dir(resolved: str, workspace_root: str, original: str) -> None:
    """A state directory inside the workspace must be beneath
    ``<workspace>/.kriya/`` (e.g. ``.kriya/state``); ``./state`` or ``./logs``
    would put Kriya state among the repository's own files."""
    workspace = os.path.realpath(workspace_root)
    target = os.path.realpath(resolved)
    if os.path.commonpath([workspace, target]) != workspace:
        return  # outside the workspace: SEC-009 path authority decides
    kriya_dir = os.path.join(workspace, WORKSPACE_STATE_PARENT)
    if target != kriya_dir and os.path.commonpath([kriya_dir, target]) == kriya_dir:
        return
    raise StateDirectoryError(
        f"paths.state {original!r} resolves inside the workspace ({target}) but not beneath "
        f"{kriya_dir}/; use e.g. '.kriya/state', or a directory outside the workspace."
    )


def kriya_install_dir() -> str:
    """The directory the packaged default config's relative paths resolved
    against (kriya/config/config.py's KRIYA_INSTALL_DIR)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def historical_default_trace_db_for(install_dir: str) -> str:
    return os.path.realpath(os.path.join(install_dir, "logs", TRACE_DB_FILENAME))


def historical_default_trace_db() -> str:
    """Where traces.db lived before it moved to the state directory: the
    packaged default ``./logs`` resolved against the install directory."""
    return historical_default_trace_db_for(kriya_install_dir())


def legacy_trace_db_path(cfg, legacy_path: Optional[str] = None) -> Optional[str]:
    """The legacy database to report or migrate, or None.

    With ``legacy_path``, exactly that file (it must exist). Without it, only
    the historical packaged default is considered. Never the current trace
    database itself, and never a CWD-relative guess."""
    if legacy_path is not None:
        candidate = os.path.realpath(os.path.expanduser(legacy_path))
        if not os.path.isabs(os.path.expanduser(legacy_path)):
            raise LegacyTraceMigrationError(f"--legacy-path must be an absolute path (got {legacy_path!r}).")
        if not os.path.isfile(candidate):
            raise LegacyTraceMigrationError(f"No trace database at {candidate}.")
    else:
        candidate = historical_default_trace_db()
        if not os.path.isfile(candidate):
            return None
    current = trace_db_path(cfg)
    if os.path.realpath(current) == candidate or (os.path.exists(current) and os.path.samefile(candidate, current)):
        return None
    return candidate


@dataclass(frozen=True)
class LegacyTraceMigration:
    source: str
    target: str
    runs: int


def migrate_legacy_trace_db(cfg, legacy_path: Optional[str] = None) -> LegacyTraceMigration:
    """Copy the legacy trace database to the canonical location.

    Explicit only. ``legacy_path`` names the exact source; otherwise the
    historical packaged default. Refuses when there is no legacy database or when the target
    already exists (two histories are never merged). Copies with SQLite's
    online backup so a WAL-mode database is copied consistently; the legacy
    file is left exactly where it was."""
    source = legacy_trace_db_path(cfg, legacy_path)
    if source is None:
        raise LegacyTraceMigrationError(
            f"No legacy trace database found at {historical_default_trace_db()}; "
            "pass --legacy-path <traces.db> for any other location."
        )
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
