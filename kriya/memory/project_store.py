"""The project-derived retrieval stores (C-05 / R-05, slice S3): the STORE MECHANISM of the project-state namespace.

CONFIG (``kriya/config/resolution.py``) owns the policy: which project a configuration is bound to, where its state lives, whether a source
directory or a recorded owner is this project's, and the closed refusal table. RETRIEVAL owns the files: ``dependency_graph.db`` (with the
structural tables), ``vector_index.db`` and the learned-knowledge store (named by its owner, kriya/memory/learned_knowledge.py) under
``paths.memory``, the identity stamp each of them carries (``project_state_owner``), and the only ways one is handed out - ``project_store_path`` to a reader, ``claim_project_store`` to a
writer that fills it from a source directory, ``claim_project_knowledge_store`` to the one writer that does not (``kriya learn``). The
dependency is RETRIEVAL -> CONFIG only; nothing here decides ownership, it reads the stamp and asks.
"""
import logging
import os
import sqlite3
from typing import Optional, Tuple

from kriya.config.resolution import (
    PROJECT_STORE_UNREADABLE,
    ProjectStateError,
    project_identity,
    require_project_source,
    require_store_owner,
)

logger = logging.getLogger(__name__)

PROJECT_OWNER_TABLE = "project_state_owner"

DEPENDENCY_GRAPH_STORE = "dependency_graph.db"
VECTOR_INDEX_STORE = "vector_index.db"


def _recorded_owner(path: str) -> Optional[str]:
    """The identity stamped in the SQLite store at ``path`` (read-only open: inspecting never creates or changes the file), or None."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        present = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (PROJECT_OWNER_TABLE,)).fetchone()
        if present is None:
            return None
        row = connection.execute(f"SELECT identity FROM {PROJECT_OWNER_TABLE} WHERE id = 1").fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def _require_existing_store_is_this_projects(cfg, path: str) -> None:
    """Raise ``ProjectStateError`` unless the EXISTING store at ``path`` is this project's: the stamp is read here, the verdict is CONFIG's."""
    project_identity(cfg)
    try:
        recorded = _recorded_owner(path)
    except sqlite3.Error as error:
        raise ProjectStateError(PROJECT_STORE_UNREADABLE, path=path, detail=str(error)) from error
    require_store_owner(cfg, path, recorded)


_reported_refusals: set = set()


def project_store_path(cfg, filename: str) -> Optional[str]:
    """READ side: the path of this project's store ``filename`` (``dependency_graph.db``, ``vector_index.db``), or None when this project has none.
    A store of another project, or one that cannot be attributed, is None as well (reported once per process): absence is "no index", never
    another project's. A configuration with no project identity raises (``PROJECT_IDENTITY_MISSING``)."""
    path, refusal = project_store_status(cfg, filename)
    if refusal is not None:
        refused = refusal.details.get("path") or filename
        if refused not in _reported_refusals:
            _reported_refusals.add(refused)
            logger.warning("Project store ignored [%s]: %s", refusal.reason_code, refusal)
    return path


def project_store_status(cfg, filename: str) -> Tuple[Optional[str], Optional[ProjectStateError]]:
    """READ side, with the reason: ``(path, None)`` for this project's store, ``(None, None)`` when there is no file, ``(None, refusal)`` when
    a file is there but is not this project's (another project's, unattributable, unreadable) - for a reader that tells the operator why
    nothing was used. Never logs. A configuration with no project identity raises (``PROJECT_IDENTITY_MISSING``)."""
    project_identity(cfg)
    path = os.path.join(cfg.paths.memory, filename)
    if not os.path.exists(path):
        return None, None
    try:
        _require_existing_store_is_this_projects(cfg, path)
    except ProjectStateError as refusal:
        return None, refusal
    return path, None


def claim_project_store(cfg, filename: str, source_root: str) -> str:
    """WRITE side: the path of this project's store ``filename``, stamped with this project's identity (the file and its directory are created when
    absent), for a writer that fills it from ``source_root``. Refused BEFORE anything is created or changed: a source that is not this project's
    (``PROJECT_SOURCE_FOREIGN``), and a store of another project or an unattributable one, which is left byte-for-byte as it was - indexing one
    project never writes into, deletes or replaces another's."""
    project_identity(cfg)
    require_project_source(cfg, source_root)
    return _claim(cfg, filename)


def claim_project_knowledge_store(cfg, filename: str) -> str:
    """WRITE side for a project store that is NOT filled from a source directory: the learned-knowledge store, whose
    content is what the operator taught with ``kriya learn`` while working in this project. Same stamp and the same refusals as
    ``claim_project_store`` (another project's store, or an unattributable one, is left byte-for-byte as it was); there is no source to check."""
    return _claim(cfg, filename)


def _claim(cfg, filename: str) -> str:
    identity = project_identity(cfg)
    path = os.path.join(cfg.paths.memory, filename)
    if os.path.exists(path):
        _require_existing_store_is_this_projects(cfg, path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {PROJECT_OWNER_TABLE} (id INTEGER PRIMARY KEY CHECK (id = 1), identity TEXT NOT NULL)")
            connection.execute(f"INSERT OR IGNORE INTO {PROJECT_OWNER_TABLE} (id, identity) VALUES (1, ?)", (identity,))
    finally:
        connection.close()
    return path
