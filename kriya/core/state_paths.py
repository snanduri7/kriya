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

from kriya.config.environment import Environment, default_state_dir, expand_user
from kriya.config.environment import state_dir as environment_state_dir

# D3: the paths.state rule and its error are CONFIG's (kriya/config/resolution.py), re-exported here (PARTIAL_FACADES). Removed in P1.
from kriya.config.resolution import WORKSPACE_STATE_PARENT as WORKSPACE_STATE_PARENT
from kriya.config.resolution import StateDirectoryError as StateDirectoryError
from kriya.config.resolution import (
    require_workspace_local_state_under_kriya_dir as require_workspace_local_state_under_kriya_dir,
)

ENV_STATE_DIR = "KRIYA_STATE_DIR"
TRACE_DB_FILENAME = "traces.db"

STATE_DIR_SOURCE_ENV = "env"
STATE_DIR_SOURCE_CONFIG = "config"
STATE_DIR_SOURCE_DEFAULT = "default"


class LegacyTraceMigrationError(RuntimeError):
    """An explicit legacy trace migration was refused."""


def default_state_directory(environment: Optional[Environment] = None) -> str:
    """``~/.kriya/state``, computed at call time (tests point HOME elsewhere)."""
    return default_state_dir(environment)


def _absolute(value: str, origin: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateDirectoryError(f"{origin} must be a non-empty directory path.")
    expanded = expand_user(value)
    if not os.path.isabs(expanded):
        raise StateDirectoryError(
            f"{origin} must be an absolute path here (got {value!r}); Kriya never resolves "
            "a state directory against the current working directory."
        )
    return os.path.realpath(expanded)


def ambient_state_directory(environment: Optional[Environment] = None) -> str:
    """The state directory for code with NO ``AppConfig`` in scope (a gate cache, a JVM acceptance staging root, a model-runtime record store):
    ``KRIYA_STATE_DIR`` (absolute, ``~`` expanded; a relative value is refused, never resolved against the working directory) else ``~/.kriya/state``.
    WAVE8 OI-02: this is the ONE place the environment/default location is decided; ``resolve_state_directory`` composes it and no other module reads
    ``KRIYA_STATE_DIR`` or the default location itself (tripwire: tests/test_wave8_state_directory_authority.py). Code that HAS a configuration asks
    ``resolve_state_directory(cfg)``, which also honours ``paths.state``. Raises ``StateDirectoryError``. Pure."""
    env_value = environment_state_dir(environment)
    if env_value:
        return _absolute(env_value, ENV_STATE_DIR)
    return os.path.realpath(default_state_directory(environment))


def resolve_state_directory(cfg, environment: Optional[Environment] = None) -> Tuple[str, str]:
    """(directory, source) with precedence env > config > default. Pure."""
    if environment_state_dir(environment):
        return ambient_state_directory(environment), STATE_DIR_SOURCE_ENV
    configured = getattr(cfg.paths, "state", None)
    if configured is not None:
        # load_config() already made this absolute (anchored at the config
        # file's directory); a directly built AppConfig must do the same.
        return _absolute(configured, "paths.state"), STATE_DIR_SOURCE_CONFIG
    return ambient_state_directory(environment), STATE_DIR_SOURCE_DEFAULT


def trace_db_path(cfg) -> str:
    return os.path.join(resolve_state_directory(cfg)[0], TRACE_DB_FILENAME)


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
        candidate = os.path.realpath(expand_user(legacy_path))
        if not os.path.isabs(expand_user(legacy_path)):
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
