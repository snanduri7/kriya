"""The one assessment of a workspace's prior commit state (PRD-008).

A workspace whose earlier run may have left a partial real-workspace commit
must not be mutated, planned against, or resumed until that state is settled
by explicit, evidence-based recovery (``kriya runs recover``). Two durable
stores can say so:

* a RunRecord with an unsettled or UNCERTAIN commit cycle, or one that exists
  but cannot be read (PRD-007: never treated as absent);
* commit evidence left IN_PROGRESS/UNCERTAIN, or unreadable (PRD-005).

``begin_mutating_run`` calls this after taking the workspace lock and before
creating the new run's own record, so every mutating entry point refuses the
same way and the new record can never contaminate the assessment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kriya.control.persistence import scan_run_records
from kriya.workflow.edit_safety import find_uncertain_commit_evidence

RECOVERY_COMMAND = "kriya runs recover"

REASON_UNCERTAIN_RUN_RECORD = "UNCERTAIN_RUN_RECORD_COMMIT_STATE"
REASON_RUN_RECORD_UNREADABLE = "RUN_RECORD_UNREADABLE"
REASON_UNCERTAIN_COMMIT = "UNCERTAIN_COMMIT_STATE"


@dataclass(frozen=True)
class WorkspaceCommitAssessment:
    uncertain_run_ids: Tuple[str, ...] = ()
    unreadable_run_records: Tuple[Tuple[str, str], ...] = ()
    uncertain_commit_ids: Tuple[str, ...] = ()
    commit_evidence_error: Optional[str] = None

    @property
    def safe(self) -> bool:
        return not self.reason_codes

    @property
    def reason_codes(self) -> List[str]:
        codes = []
        if self.uncertain_run_ids:
            codes.append(REASON_UNCERTAIN_RUN_RECORD)
        if self.unreadable_run_records:
            codes.append(REASON_RUN_RECORD_UNREADABLE)
        if self.uncertain_commit_ids or self.commit_evidence_error:
            codes.append(REASON_UNCERTAIN_COMMIT)
        return codes

    def describe(self) -> str:
        parts = []
        if self.uncertain_run_ids:
            parts.append(f"runs with unsettled/uncertain commits: {', '.join(self.uncertain_run_ids)}")
        if self.unreadable_run_records:
            parts.append(f"unreadable run records: {', '.join(p for p, _ in self.unreadable_run_records)}")
        if self.uncertain_commit_ids:
            parts.append(f"in-progress/uncertain commits: {', '.join(self.uncertain_commit_ids)}")
        if self.commit_evidence_error:
            parts.append(f"commit evidence unreadable: {self.commit_evidence_error}")
        return (
            "The workspace may hold a partial commit from an earlier run ("
            + "; ".join(parts)
            + f"). Run `{RECOVERY_COMMAND}` to settle it from the durable evidence."
        )

    def to_payload(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """The structured refusal every workflow entry point returns."""
        return {
            "status": "needs_review",
            "quality_gates_passed": False,
            "files": [],
            "reason_codes": self.reason_codes,
            "uncertain_run_ids": list(self.uncertain_run_ids),
            "unreadable_run_records": [
                {"path": path, "reason": reason} for path, reason in self.unreadable_run_records
            ],
            "uncertain_commit_ids": list(self.uncertain_commit_ids),
            "error": self.commit_evidence_error,
            "recovery_command": RECOVERY_COMMAND,
            "run_id": run_id,
        }


class UncertainWorkspaceStateError(RuntimeError):
    """A mutating run was refused because prior commit state is uncertain."""

    def __init__(self, assessment: WorkspaceCommitAssessment) -> None:
        super().__init__(assessment.describe())
        self.assessment = assessment


def assess_workspace_commit_state(
    workspace_path: str, *, exclude_run_ids: Iterable[Optional[str]] = (),
) -> WorkspaceCommitAssessment:
    """Read-only. ``exclude_run_ids`` names runs that belong to the caller
    itself (its own in-flight cycle is legitimately unsettled mid-commit)."""
    excluded = {run_id for run_id in exclude_run_ids if run_id}
    scan = scan_run_records(workspace_path)
    uncertain_runs = tuple(
        record.run_id for record in scan.records
        if record.run_id not in excluded and record.commit_state_unknown
    )
    unreadable = tuple((item.path, item.reason) for item in scan.unreadable)
    try:
        evidence = find_uncertain_commit_evidence(workspace_path)
    except Exception as error:
        commit_ids: Tuple[str, ...] = ()
        evidence_error: Optional[str] = str(error)
    else:
        commit_ids = tuple(item.transaction_id for item in evidence)
        evidence_error = None
    return WorkspaceCommitAssessment(
        uncertain_run_ids=uncertain_runs,
        unreadable_run_records=unreadable,
        uncertain_commit_ids=commit_ids,
        commit_evidence_error=evidence_error,
    )
