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

from kriya.context.workspace_identity import OWNERSHIP_REMEDIATION
from kriya.control.persistence import inspect_control_state, nested_control_state, scan_run_records
from kriya.repository.commit import find_uncertain_commit_evidence

RECOVERY_COMMAND = "kriya runs recover"

REASON_UNCERTAIN_RUN_RECORD = "UNCERTAIN_RUN_RECORD_COMMIT_STATE"
REASON_RUN_RECORD_UNREADABLE = "RUN_RECORD_UNREADABLE"
REASON_UNCERTAIN_COMMIT = "UNCERTAIN_COMMIT_STATE"
# RDCG-B-001: a run's own result claimed success but the RunRecord that is the authority for that run ended in a different terminal state (not UNCERTAIN: that is the code above).
REASON_RESULT_CONTRADICTS_RUN_RECORD = "RESULT_CONTRADICTS_RUN_RECORD"
# D-001: unsettled control state exists under a sub-directory of the repository (written when that directory was its own workspace).
REASON_NESTED_WORKSPACE_STATE = "NESTED_WORKSPACE_CONTROL_STATE"
# D1: the persisted ControlState exists but cannot be read - never treated as absent (it may hold an unresolved TOOL STARTED record).
REASON_CONTROL_STATE_UNREADABLE = "CONTROL_STATE_UNREADABLE"
# D1: a TOOL subtask was durably STARTED and never durably COMPLETED - its external effect may or may not have happened. A person reviews it; Kriya neither retries it nor starts over it.
REASON_UNRESOLVED_TOOL_EXECUTION = "UNRESOLVED_TOOL_EXECUTION"


@dataclass(frozen=True)
class WorkspaceCommitAssessment:
    uncertain_run_ids: Tuple[str, ...] = ()
    unreadable_run_records: Tuple[Tuple[str, str], ...] = ()
    uncertain_commit_ids: Tuple[str, ...] = ()
    commit_evidence_error: Optional[str] = None
    # D-001: ((directory, reason), ...) of unsettled/unreadable control state under a directory BELOW the authority root (the pre-authority-root layout).
    nested_workspace_state: Tuple[Tuple[str, str], ...] = ()
    nested_scan_complete: bool = True
    # D2 M4 follow-up: ((path, WORKSPACE_OWNERSHIP_*), ...) of the unreadable run records refused for their ownership stamp (a subset of
    # unreadable_run_records), and the same code when the commit evidence was. Ownership was never established for these, so they are neither
    # "unreadable" nor "uncertain" in the recoverable sense: `kriya runs recover` cannot settle them.
    run_record_ownership_refusals: Tuple[Tuple[str, str], ...] = ()
    commit_evidence_ownership_code: Optional[str] = None
    # D1: (path, reason) of an existing control state that cannot be read. Not settleable by `kriya runs recover` (nothing durable to settle from): a person inspects it.
    unreadable_control_state: Optional[Tuple[str, str]] = None
    # D1: ((run_id, (subtask_id, ...)),) of TOOL executions durably STARTED and never durably COMPLETED, read from the persisted ControlState. The ControlState is ONE document per
    # workspace, so a fresh run would silently overwrite this evidence: the gate refuses instead. `kriya runs recover` cannot settle it (nothing durable to settle from).
    unresolved_tool_executions: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    # W2-02: the run ids among ``unresolved_tool_executions`` that are DIRECT `kriya tools execute` runs (evidence: their RunRecord, not the ControlState), so the message names the
    # command that can see each kind. Not part of the payload.
    direct_tool_run_ids: Tuple[str, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.reason_codes

    @property
    def ownership_reason_codes(self) -> List[str]:
        codes = {code for _path, code in self.run_record_ownership_refusals}
        if self.commit_evidence_ownership_code:
            codes.add(self.commit_evidence_ownership_code)
        return sorted(codes)

    def _unreadable_for_other_reasons(self) -> Tuple[Tuple[str, str], ...]:
        owned_out = {path for path, _code in self.run_record_ownership_refusals}
        return tuple(item for item in self.unreadable_run_records if item[0] not in owned_out)

    @property
    def recoverable(self) -> bool:
        """Whether any part of the refusal is something `kriya runs recover` can settle (everything except ownership)."""
        return bool(
            self.uncertain_run_ids or self._unreadable_for_other_reasons() or self.uncertain_commit_ids
            or (self.commit_evidence_error and not self.commit_evidence_ownership_code) or self.nested_workspace_state
        )

    @property
    def reason_codes(self) -> List[str]:
        codes = []
        if self.uncertain_run_ids:
            codes.append(REASON_UNCERTAIN_RUN_RECORD)
        if self._unreadable_for_other_reasons():
            codes.append(REASON_RUN_RECORD_UNREADABLE)
        if self.uncertain_commit_ids or (self.commit_evidence_error and not self.commit_evidence_ownership_code):
            codes.append(REASON_UNCERTAIN_COMMIT)
        codes.extend(self.ownership_reason_codes)
        if self.nested_workspace_state:
            codes.append(REASON_NESTED_WORKSPACE_STATE)
        if self.unreadable_control_state:
            codes.append(REASON_CONTROL_STATE_UNREADABLE)
        if self.unresolved_tool_executions:
            codes.append(REASON_UNRESOLVED_TOOL_EXECUTION)
        return codes

    def describe(self) -> str:
        parts = []
        if self.uncertain_run_ids:
            parts.append(f"runs with unsettled/uncertain commits: {', '.join(self.uncertain_run_ids)}")
        other_unreadable = self._unreadable_for_other_reasons()
        if other_unreadable:
            parts.append(f"unreadable run records: {', '.join(p for p, _ in other_unreadable)}")
        for code in sorted({code for _path, code in self.run_record_ownership_refusals}):
            paths = ", ".join(path for path, item_code in self.run_record_ownership_refusals if item_code == code)
            parts.append(f"run records refused for their ownership stamp ({code}): {paths}. {OWNERSHIP_REMEDIATION}")
        if self.uncertain_commit_ids:
            parts.append(f"in-progress/uncertain commits: {', '.join(self.uncertain_commit_ids)}")
        if self.commit_evidence_error:
            parts.append(f"commit evidence unreadable: {self.commit_evidence_error}")
        if self.nested_workspace_state:
            parts.append("unsettled state left under a sub-directory of this repository by an earlier Kriya that treated that directory as its own workspace: "
                         + "; ".join(f"{directory} ({reason})" for directory, reason in self.nested_workspace_state))
        if self.unresolved_tool_executions:
            plan_entries = [(run_id, ids) for run_id, ids in self.unresolved_tool_executions if run_id not in self.direct_tool_run_ids]
            direct_entries = [(run_id, ids) for run_id, ids in self.unresolved_tool_executions if run_id in self.direct_tool_run_ids]
            guidance = []
            if plan_entries:
                parts.append("plan TOOL execution(s) STARTED and never durably COMPLETED, or failed after their effect may have begun (their side effect may or may not have happened): "
                             + "; ".join(f"run {run_id}: {', '.join(ids)}" for run_id, ids in plan_entries))
                guidance.append("for a plan TOOL subtask, inspect what it did with `kriya runs tool-inspect`, then abandon it or re-arm it as a new attempt with `kriya runs tool-abandon` / "
                                "`kriya runs tool-retry` (the control state is the only evidence; do not delete it)")
            if direct_entries:
                parts.append("direct `kriya tools execute` run(s) STARTED and never durably resolved (their side effect may or may not have happened): "
                             + "; ".join(f"run {run_id}: {', '.join(ids)}" for run_id, ids in direct_entries))
                guidance.append("for a direct tool run, inspect it with `kriya runs tool-inspect --run RUN_ID`, then `kriya runs tool-abandon --run RUN_ID --confirm` "
                                "(its run record is the evidence; `kriya runs recover` does not settle it; do not delete it)")
            if not self.recoverable and not self.ownership_reason_codes and not self.unreadable_control_state:
                return ("The workspace holds an unresolved TOOL execution (" + "; ".join(parts) + "). Kriya will not retry it and will not start over it: " + "; ".join(guidance) + ".")
        if self.unreadable_control_state:
            parts.append(f"control state unreadable and left untouched: {self.unreadable_control_state[0]} ({self.unreadable_control_state[1]})")
            if not self.recoverable and not self.ownership_reason_codes:
                return ("The workspace's Kriya control state cannot be read and was left untouched (" + "; ".join(parts) + "). Kriya will neither resume from it nor "
                        "start fresh over it: inspect it, then move it aside yourself.")
        if not self.recoverable:
            # Every item already carries the ownership remediation (workspace_identity.OWNERSHIP_REMEDIATION); recovery is not advice here.
            return "The workspace's Kriya control state is not provably this workspace's (" + "; ".join(parts) + ")."
        tail = f"). Run `{RECOVERY_COMMAND}` to settle it from the durable evidence."
        if self.ownership_reason_codes:
            tail = f"). `{RECOVERY_COMMAND}` settles only the state that is provably this workspace's; the ownership refusals above need the preservation step they name."
        return "The workspace may hold a partial commit from an earlier run (" + "; ".join(parts) + tail

    def to_payload(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """The structured refusal every workflow entry point returns."""
        payload = {
            "status": "needs_review",
            "quality_gates_passed": False,
            "files": [],
            "reason_codes": self.reason_codes,
            "uncertain_run_ids": list(self.uncertain_run_ids),
            "unreadable_run_records": [
                {"path": path, "reason": reason} for path, reason in self.unreadable_run_records
            ],
            "uncertain_commit_ids": list(self.uncertain_commit_ids),
            "nested_workspace_state": [{"directory": directory, "reason": reason} for directory, reason in self.nested_workspace_state],
            "nested_scan_complete": self.nested_scan_complete,
            "error": self.commit_evidence_error,
            "recovery_command": RECOVERY_COMMAND if self.recoverable else None,
            "run_id": run_id,
        }
        if self.unresolved_tool_executions:  # additive (D1)
            payload["unresolved_tool_executions"] = [{"run_id": run_id, "subtask_ids": list(ids)} for run_id, ids in self.unresolved_tool_executions]
        if self.unreadable_control_state:  # additive (D1)
            payload["unreadable_control_state"] = {"path": self.unreadable_control_state[0], "reason": self.unreadable_control_state[1]}
        if self.run_record_ownership_refusals:  # additive: a refusal without ownership findings keeps its exact earlier shape
            payload["ownership_findings"] = [{"path": path, "reason_code": code} for path, code in self.run_record_ownership_refusals]
        return payload


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
    run_record_ownership = tuple((item.path, item.reason_code) for item in scan.unreadable if item.reason_code)
    evidence_ownership_code: Optional[str] = None
    try:
        evidence = find_uncertain_commit_evidence(workspace_path)
    except Exception as error:
        commit_ids: Tuple[str, ...] = ()
        evidence_error: Optional[str] = str(error)
        evidence_ownership_code = getattr(error, "ownership_reason_code", None)
    else:
        commit_ids = tuple(item.transaction_id for item in evidence)
        evidence_error = None
    nested, nested_complete = nested_control_state(workspace_path)
    control_state, unreadable_state = inspect_control_state(workspace_path)
    unresolved_tools = []
    if control_state is not None and control_state.run_id not in excluded and control_state.undisposed_tool_subtask_ids:
        unresolved_tools.append((control_state.run_id, control_state.undisposed_tool_subtask_ids))  # Wave 2 Slice A: STARTED-never-COMPLETED and failed-after-effect-may-have-begun
    # D1: a direct `kriya tools execute` whose RunRecord says "started" and never durably finished (whatever the record's lifecycle now says).
    direct_unresolved = [(record.run_id, (record.unresolved_tool_execution,)) for record in scan.records
                         if record.run_id not in excluded and record.unresolved_tool_execution]
    unresolved_tools.extend(direct_unresolved)
    return WorkspaceCommitAssessment(
        uncertain_run_ids=uncertain_runs,
        unreadable_run_records=unreadable,
        uncertain_commit_ids=commit_ids,
        commit_evidence_error=evidence_error,
        nested_workspace_state=nested,
        nested_scan_complete=nested_complete,
        run_record_ownership_refusals=run_record_ownership,
        commit_evidence_ownership_code=evidence_ownership_code,
        unreadable_control_state=unreadable_state,
        unresolved_tool_executions=tuple(unresolved_tools),
        direct_tool_run_ids=tuple(run_id for run_id, _ in direct_unresolved),
    )
