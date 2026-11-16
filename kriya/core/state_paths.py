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

from kriya.config.environment import Environment, expand_user

# D3 + S3 (C-05, owner rulings 2026-11-16): the paths.state rule, the state-directory composition and the project-state POLICY (identity binding,
# project-state paths, the source and store-owner verdicts, the refusal table) are CONFIG's (kriya/config/resolution.py: CONFIG resolves the
# paths.memory default and may not import STATE). This module re-exports those pure names (PARTIAL_FACADES) and keeps the AppConfig-shaped
# ``resolve_state_directory``. The store MECHANISM (the SQLite stamp, project_store_path, claim_project_store) is RETRIEVAL's,
# kriya/memory/project_store.py, and is deliberately NOT re-exported here: STATE does not import RETRIEVAL. Removed in P1.
from kriya.config.resolution import (
    ENV_STATE_DIR as ENV_STATE_DIR,
)
from kriya.config.resolution import (
    PROJECT_IDENTITY_MISSING as PROJECT_IDENTITY_MISSING,
)
from kriya.config.resolution import (
    PROJECT_MEMORY_DIRECTORY as PROJECT_MEMORY_DIRECTORY,
)
from kriya.config.resolution import (
    PROJECT_SOURCE_FOREIGN as PROJECT_SOURCE_FOREIGN,
)
from kriya.config.resolution import (
    PROJECT_STATE_PARENT as PROJECT_STATE_PARENT,
)
from kriya.config.resolution import (
    PROJECT_STATE_REFUSALS as PROJECT_STATE_REFUSALS,
)
from kriya.config.resolution import (
    PROJECT_STORE_FOREIGN as PROJECT_STORE_FOREIGN,
)
from kriya.config.resolution import (
    PROJECT_STORE_UNATTRIBUTED as PROJECT_STORE_UNATTRIBUTED,
)
from kriya.config.resolution import (
    PROJECT_STORE_UNREADABLE as PROJECT_STORE_UNREADABLE,
)
from kriya.config.resolution import (
    STATE_DIR_SOURCE_CONFIG as STATE_DIR_SOURCE_CONFIG,
)
from kriya.config.resolution import (
    STATE_DIR_SOURCE_DEFAULT as STATE_DIR_SOURCE_DEFAULT,
)
from kriya.config.resolution import (
    STATE_DIR_SOURCE_ENV as STATE_DIR_SOURCE_ENV,
)
from kriya.config.resolution import (
    WORKSPACE_STATE_PARENT as WORKSPACE_STATE_PARENT,
)
from kriya.config.resolution import (
    ProjectStateError as ProjectStateError,
)
from kriya.config.resolution import (
    StateDirectoryError as StateDirectoryError,
)
from kriya.config.resolution import (
    ambient_state_directory as ambient_state_directory,
)
from kriya.config.resolution import (
    bind_project_identity as bind_project_identity,
)
from kriya.config.resolution import (
    default_project_memory_directory as default_project_memory_directory,
)
from kriya.config.resolution import (
    default_state_directory as default_state_directory,
)
from kriya.config.resolution import (
    project_identity as project_identity,
)
from kriya.config.resolution import (
    project_state_root as project_state_root,
)
from kriya.config.resolution import (
    require_project_source as require_project_source,
)
from kriya.config.resolution import (
    require_store_owner as require_store_owner,
)
from kriya.config.resolution import (
    require_workspace_local_state_under_kriya_dir as require_workspace_local_state_under_kriya_dir,
)
from kriya.config.resolution import (
    state_directory_for as state_directory_for,
)

TRACE_DB_FILENAME = "traces.db"


class LegacyTraceMigrationError(RuntimeError):
    """An explicit legacy trace migration was refused."""


def resolve_state_directory(cfg, environment: Optional[Environment] = None) -> Tuple[str, str]:
    """(directory, source) with precedence env > config > default. Pure."""
    return state_directory_for(getattr(cfg.paths, "state", None), environment)


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


# C-05 (S3): before the project-state namespace, the packaged ``paths.memory`` ("./memory") resolved to ``<install>/memory`` for EVERY project,
# so these files may hold several projects' state mixed together and record no owner. They are reported, never read, adopted, migrated or
# deleted: a project rebuilds its own index with ``kriya analyze`` and re-teaches with ``kriya learn``.
LEGACY_SHARED_MEMORY_SUFFIX = ".db"


def legacy_shared_memory_stores(cfg, install_dir: Optional[str] = None) -> Tuple[str, ...]:
    """The store files (every ``*.db``: the code index, the vector index, learned knowledge, the release cache) left in the historical shared
    memory directory (``<install>/memory``), sorted; () when there are none or when ``cfg`` names that very directory as its explicit
    ``paths.memory`` (then they are simply its configured stores, each still bound by its own stamp). Pure inspection: nothing is opened."""
    directory = os.path.realpath(os.path.join(install_dir if install_dir is not None else kriya_install_dir(), "memory"))
    configured = getattr(getattr(cfg, "paths", None), "memory", None)
    if isinstance(configured, str) and os.path.realpath(configured) == directory:
        return ()
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return ()
    return tuple(path for path in (os.path.join(directory, name) for name in names if name.endswith(LEGACY_SHARED_MEMORY_SUFFIX))
                 if os.path.isfile(path))


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
