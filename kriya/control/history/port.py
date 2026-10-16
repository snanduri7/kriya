"""``HistoryStore`` (full modularization D2; MODULE-CONTRACTS §6): the history store's use-case surface over the snapshot store
(``kriya/control/history/__init__.py``, read side) and its acquisition (``kriya/control/history/acquire.py``, the only module that writes;
imported here only when a snapshot is acquired or pruned). Every function is looked up on its module at call time, so a patched module
function is what runs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from kriya.control import history
from kriya.control.history import Snapshot, SnapshotTrace


@dataclass(frozen=True)
class HistoryLocation:
    """Where the history of a configuration lives: the state directory, its trace store and the snapshot directory."""

    state_directory: str
    trace_database: str
    snapshot_directory: str


class HistoryStore:
    """MODULE-CONTRACTS §6 ``HistoryStore`` over the snapshot store's functions. Acquisition and pruning import the write-capable module
    only when called."""

    def location(self, cfg) -> HistoryLocation:
        trace = history.trace_store_path(cfg)
        return HistoryLocation(state_directory=os.path.dirname(trace), trace_database=trace, snapshot_directory=history.snapshot_directory(cfg))

    def require(self, cfg, snapshot_id: str) -> Snapshot:
        return history.require_snapshot(history.snapshot_directory(cfg), snapshot_id)

    def published(self, cfg) -> List[Snapshot]:
        return history.list_snapshots(history.snapshot_directory(cfg))

    def source_metadata(self, cfg) -> Optional[Dict[str, Any]]:
        """The source trace store's metadata observed NOW (by ``stat`` only: no open, no lock)."""
        return history.stat_metadata(history.trace_store_path(cfg))

    def summary_of(self, snapshot: Snapshot, source_now: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return history.snapshot_summary(snapshot, source_now)

    def summary(self, cfg, snapshot: Snapshot) -> Dict[str, Any]:
        """The snapshot's manifest summary with the source store's metadata observed now."""
        return history.snapshot_summary(snapshot, history.stat_metadata(history.trace_store_path(cfg)))

    def verify(self, snapshot: Snapshot) -> Optional[str]:
        return history.verify_digest(snapshot)

    def trace(self, snapshot: Snapshot) -> "SnapshotTrace":
        return SnapshotTrace(snapshot)

    def acquire(self, cfg, *, run_active_check: Optional[Callable[[], Optional[str]]] = None) -> Dict[str, Any]:
        from kriya.control.history import acquire  # the only write-capable module of the store

        return acquire.acquire_snapshot(history.trace_store_path(cfg), history.snapshot_directory(cfg), run_active_check=run_active_check)

    def prune(self, cfg, *, keep: int) -> Dict[str, Any]:
        from kriya.control.history import acquire

        return acquire.prune_snapshots(history.snapshot_directory(cfg), keep=keep)


__all__ = ["HistoryLocation", "HistoryStore"]
