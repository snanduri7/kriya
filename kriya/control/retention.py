"""Reference-safe retention of run records and commit evidence (PRD-008).

Mark and sweep, never count-only. A run record and the commit evidence its
cycles reference are pruned as one unit, so pruning can never delete the
evidence an unsettled cycle still needs. (Before PRD-008 each commit pruned
terminal evidence beyond the newest 50 on its own; a later commit could then
delete the COMMITTED evidence of another run's unsettled cycle, and recovery
would read "no evidence" as "never started".)

Protected run records:
  * non-terminal records (a crashed run's record stays for `kriya runs recover`);
  * records whose commit state is unknown (unsettled or UNCERTAIN cycle);
  * records a resume checkpoint still references;
  * records the caller names (its own run);
  * the newest ``keep_terminal_runs`` terminal records.

Protected commit evidence:
  * IN_PROGRESS/UNCERTAIN or unreadable evidence (it blocks commits until
    recovered, and an unreadable file cannot be proven unreferenced);
  * evidence a retained record references;
  * the newest ``keep_unreferenced_evidence`` terminal files no record
    references (commits made outside a run).

Nothing is pruned while any run record is unreadable: its references are
unknown. The caller must hold the workspace run lock.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

from kriya.control.persistence import run_record_path, scan_run_records
from kriya.workflow.checkpoint import list_checkpoint_run_references
from kriya.workflow.edit_safety import CommitState, list_commit_evidence

logger = logging.getLogger(__name__)

DEFAULT_KEEP_TERMINAL_RUNS = 50
DEFAULT_KEEP_UNREFERENCED_EVIDENCE = 50


@dataclass
class PruneReport:
    pruned_run_ids: List[str] = field(default_factory=list)
    pruned_evidence_ids: List[str] = field(default_factory=list)
    protected_run_ids: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "pruned_run_ids": self.pruned_run_ids,
            "pruned_evidence_ids": self.pruned_evidence_ids,
            "protected_run_ids": self.protected_run_ids,
            "skipped_reason": self.skipped_reason,
            "dry_run": self.dry_run,
        }


def prune_run_state(
    workspace_path: str, *,
    keep_terminal_runs: int = DEFAULT_KEEP_TERMINAL_RUNS,
    keep_unreferenced_evidence: int = DEFAULT_KEEP_UNREFERENCED_EVIDENCE,
    protect_run_ids: Iterable[str] = (),
    dry_run: bool = False,
) -> PruneReport:
    if keep_terminal_runs < 0 or keep_unreferenced_evidence < 0:
        raise ValueError("retention counts must be non-negative")
    report = PruneReport(dry_run=dry_run)
    scan = scan_run_records(workspace_path)
    if scan.unreadable:
        report.skipped_reason = (
            "unreadable run records exist; their evidence references are unknown: "
            + ", ".join(item.path for item in scan.unreadable)
        )
        return report

    protected: Set[str] = set(protect_run_ids) | set(list_checkpoint_run_references(workspace_path))
    terminal = []
    for record in scan.records:
        if not record.terminal or record.commit_state_unknown:
            protected.add(record.run_id)
        elif record.run_id not in protected:
            terminal.append(record)
    terminal.sort(key=lambda record: record.updated_at, reverse=True)
    protected.update(record.run_id for record in terminal[:keep_terminal_runs])
    pruned_records = terminal[keep_terminal_runs:]
    retained_records = [record for record in scan.records if record.run_id in protected]
    report.protected_run_ids = sorted(record.run_id for record in retained_records)

    retained_refs = {
        cycle.get("transaction_id") for record in retained_records for cycle in record.commits
    }
    pruned_refs = {
        cycle.get("transaction_id") for record in pruned_records for cycle in record.commits
    }
    evidence_to_prune: List[str] = []
    unreferenced = []
    for path, evidence, _error in list_commit_evidence(workspace_path):
        if evidence is None or evidence.state not in (CommitState.COMMITTED, CommitState.ROLLED_BACK):
            continue
        if evidence.transaction_id in retained_refs:
            continue
        if evidence.transaction_id in pruned_refs:
            evidence_to_prune.append(path)
        else:
            unreferenced.append((evidence.updated_at_unix, path))
    unreferenced.sort(reverse=True)
    evidence_to_prune.extend(path for _, path in unreferenced[keep_unreferenced_evidence:])

    report.pruned_run_ids = sorted(record.run_id for record in pruned_records)
    report.pruned_evidence_ids = sorted(
        os.path.basename(path)[:-len(".json")] for path in evidence_to_prune
    )
    if dry_run:
        return report
    # Records first: a crash between the two leaves only unreferenced
    # terminal evidence, which the next prune removes.
    for record in pruned_records:
        _unlink(run_record_path(workspace_path, record.run_id))
    for path in evidence_to_prune:
        _unlink(path)
    return report


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def prune_after_run(workspace_path: str, run_id: str) -> None:
    """Best-effort retention at the end of a run; never fails the run."""
    try:
        report = prune_run_state(workspace_path, protect_run_ids=(run_id,))
    except Exception as error:
        logger.warning("Run-state pruning skipped: %s", error)
        return
    if report.pruned_run_ids or report.pruned_evidence_ids:
        logger.info(
            "Pruned %d run record(s) and %d commit evidence file(s).",
            len(report.pruned_run_ids), len(report.pruned_evidence_ids),
        )
    elif report.skipped_reason:
        logger.warning("Run-state pruning skipped: %s", report.skipped_reason)


__all__ = [
    "DEFAULT_KEEP_TERMINAL_RUNS", "DEFAULT_KEEP_UNREFERENCED_EVIDENCE",
    "PruneReport", "prune_after_run", "prune_run_state",
]
