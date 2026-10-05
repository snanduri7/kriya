"""Retention of attempt-evidence stores (LR-R1-M1 design §10; test T12).

Mark and sweep over ``<state>/attempt-evidence/``, mirroring
``kriya/control/retention.py``'s rules. Protected, never pruned:
  * the runs the caller names (its own, the active run) and the runs the
    caller's workspace still references (resume checkpoints, the persisted
    ControlState, milestone commit ledgers);
  * an unsealed store younger than ``UNSEALED_GRACE_SECONDS`` (a run in
    progress, possibly in another workspace or process);
  * a store whose age cannot be read.
Of the rest, the newest ``keep_runs`` are kept; then, oldest first, kept
runs are dropped until the retained total is at most ``max_bytes``
(protected runs are never dropped for size). An unsealed store older than
the grace period is a crashed run and is pruned like a sealed one.

A pruned store is first renamed to a dot-prefixed name (atomically leaving
the readable namespace, so a partial delete is never read as a run), then
removed. Pruning only ever touches the store root; it never alters a run.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from kriya.core.attempt_evidence.writer import _MANIFEST, _SEAL, store_root

logger = logging.getLogger(__name__)

UNSEALED_GRACE_SECONDS = 24 * 60 * 60
_PRUNING_PREFIX = ".pruning-"


@dataclass
class EvidencePruneReport:
    pruned: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)
    protected: Dict[str, str] = field(default_factory=dict)   # run id -> reason
    retained_bytes: int = 0
    freed_bytes: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {"pruned": self.pruned, "kept": self.kept, "protected": self.protected,
                "retained_bytes": self.retained_bytes, "freed_bytes": self.freed_bytes, "dry_run": self.dry_run}


@dataclass(frozen=True)
class _Store:
    run_id: str
    path: str
    sealed: bool
    # Seal time for a sealed store, creation (manifest) time otherwise; None
    # when unreadable. Both files are written once and never modified.
    stamp: Optional[float]
    size: int


def _directory_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def _stamp(path: str) -> Optional[float]:
    try:
        return os.lstat(path).st_mtime
    except OSError:
        return None


def _scan(root: str) -> List[_Store]:
    stores = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if name.startswith(".") or os.path.islink(path) or not os.path.isdir(path):
            continue
        sealed = os.path.isfile(os.path.join(path, _SEAL))
        stamp = _stamp(os.path.join(path, _SEAL if sealed else _MANIFEST))
        stores.append(_Store(name, path, sealed, stamp, _directory_size(path)))
    return stores


def prune_evidence(state_dir: str, *, keep_runs: int, max_bytes: int, protect_run_ids: Iterable[str] = (),
                   dry_run: bool = False, now: Optional[float] = None) -> EvidencePruneReport:
    if keep_runs < 0 or max_bytes < 0:
        raise ValueError("retention bounds must be non-negative")
    report = EvidencePruneReport(dry_run=dry_run)
    root = store_root(state_dir)
    if not os.path.isdir(root):
        return report
    if not dry_run:
        remove_abandoned_staging(state_dir)
    now = time.time() if now is None else now
    named = set(protect_run_ids)
    candidates: List[_Store] = []
    retained: List[_Store] = []
    for store in _scan(root):
        if store.run_id in named:
            report.protected[store.run_id] = "named"
        elif store.stamp is None:
            report.protected[store.run_id] = "age_unreadable"
        elif not store.sealed and now - store.stamp < UNSEALED_GRACE_SECONDS:
            report.protected[store.run_id] = "unsealed_recent"
        else:
            candidates.append(store)
            continue
        retained.append(store)
    candidates.sort(key=lambda store: (store.stamp, store.run_id), reverse=True)   # newest first
    kept, pruned = candidates[:keep_runs], candidates[keep_runs:]
    total = sum(store.size for store in retained + kept)
    while kept and total > max_bytes:
        oldest = kept.pop()
        pruned.append(oldest)
        total -= oldest.size
    report.kept = sorted(store.run_id for store in kept)
    report.pruned = sorted(store.run_id for store in pruned)
    report.retained_bytes = total
    report.freed_bytes = sum(store.size for store in pruned)
    if not dry_run:
        for store in pruned:
            _remove(root, store)
    return report


def _remove(root: str, store: _Store) -> None:
    staging = os.path.join(root, f"{_PRUNING_PREFIX}{store.run_id}-{uuid.uuid4().hex[:8]}")
    try:
        os.rename(store.path, staging)
    except FileNotFoundError:
        return            # pruned concurrently
    shutil.rmtree(staging, ignore_errors=True)


def remove_abandoned_staging(state_dir: str) -> Tuple[str, ...]:
    """Remove ``.pruning-*`` directories an interrupted prune left behind."""
    root = store_root(state_dir)
    if not os.path.isdir(root):
        return ()
    removed = []
    for name in os.listdir(root):
        if name.startswith(_PRUNING_PREFIX) and not os.path.islink(os.path.join(root, name)):
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
            removed.append(name)
    return tuple(sorted(removed))
