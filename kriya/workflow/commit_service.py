"""The enforce run's terminal commit (PRD-030).

``commit_verified_candidate`` is the only way the enforce controller moves a
verified candidate into the real workspace. It accepts only a
commit-eligible :class:`~kriya.workflow.terminal_gate_service.TerminalGateReport`,
so a candidate that has not passed every terminal gate can never be
committed. It stages the plan's final action for each approved path as one
revision-grounded batch, and commits it through
kriya/workflow/terminal_commit.py (PRD-004/005/007/029). The generation
workflow's own terminal apply uses the same module, so there is a single
commit implementation.

Controlled commit outcomes come back as a typed result, never raised. An
unexpected exception from the commit propagates, as it did before this
module existed.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from kriya.repository.commit import CommitState, list_commit_evidence
from kriya.static_analysis.service import StaticAnalysisCommitGuard
from kriya.workflow.edit_safety import StagedFileWrite
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.plan_schema import EngineeringPlan, FileAction
from kriya.workflow.subtask_checkpoint import topological_subtask_order
from kriya.workflow.terminal_commit import (
    CandidateFile,
    CandidateMaterializationError,
    commit_terminal_candidate,
    materialize_candidate,
)
from kriya.workflow.terminal_gate_service import TerminalGateReport

# (terminal writes, transaction id) -> the PRD-029 ContractRegistry
# transition builder commit_terminal_candidate calls with the candidate hash.
ContractTransitionFor = Callable[[List[StagedFileWrite], str], Callable[..., Any]]


class TerminalCommitNotEligibleError(RuntimeError):
    """A commit was requested for a candidate that did not pass every terminal gate."""


@dataclass(frozen=True)
class TerminalCommitRequest:
    plan: EngineeringPlan
    # The verified candidate; the same path as ``workspace_path`` when the
    # run generated in place (nothing to transfer).
    candidate_root: str
    workspace_path: str
    # Each approved path's revision in the real workspace when the plan
    # started: the base every staged write is grounded on.
    original_plan_revisions: Mapping[str, str]
    approved_plan_hash: str
    obligation_ledger: ObligationLedger
    run_id: str
    contract_transition_for: ContractTransitionFor
    # PRD-031A: built by kriya/static_analysis/service.py::commit_guard from
    # the current configuration and the gate's result.
    static_analysis: StaticAnalysisCommitGuard


@dataclass(frozen=True)
class TerminalCommitResult:
    """``completed`` is True once the real workspace holds the candidate.
    Otherwise ``failure`` says why, and whether the workspace is UNCHANGED
    or UNCERTAIN."""

    completed: bool
    failure: Optional[Dict[str, Any]] = None
    evidence: Optional[Dict[str, Any]] = None
    # PRD-029: the ContractRegistry transition committed with the source.
    contract_registry: Optional[Dict[str, Any]] = None
    # RunRecord persistence failures after the workspace outcome was decided.
    record_errors: Tuple[Dict[str, str], ...] = ()


def plan_terminal_writes(
    plan: EngineeringPlan, candidate_root: str, workspace_path: str, original_plan_revisions: Mapping[str, str],
) -> List[StagedFileWrite]:
    """The verified candidate as one revision-grounded batch, in the final
    action each approved path has after every subtask. The static-analysis
    gate scans this batch; the commit materializes it again and the guard
    proves the two are byte-identical (PRD-031A)."""
    subtasks_by_id = {subtask.id: subtask for subtask in plan.subtasks}
    final_action_by_path: Dict[str, FileAction] = {}
    for sid in topological_subtask_order(plan):
        if sid in subtasks_by_id:
            for planned_file in subtasks_by_id[sid].planned_files:
                final_action_by_path[planned_file.path] = planned_file.action
    return materialize_candidate(candidate_root, workspace_path, [
        CandidateFile(
            relpath=path, expected_base_revision=original_plan_revisions[path],
            expected_base_exists=(action != FileAction.CREATE),
            delete=(action == FileAction.DELETE),
        )
        for path, action in final_action_by_path.items()
    ])


def terminal_writes(request: TerminalCommitRequest) -> List[StagedFileWrite]:
    return plan_terminal_writes(
        request.plan, request.candidate_root, request.workspace_path, request.original_plan_revisions,
    )


def _transaction_prefix(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", run_id).lstrip("-_")[:100] or "run"


def run_commit_states(workspace_path: str, run_id: str) -> Optional[Dict[str, CommitState]]:
    """The durable state of every commit transaction ``run_id`` has started in ``workspace_path``, from the commit evidence journal
    (the transaction's own authority, written before and settled after the bytes move). ``None`` when the journal cannot be read
    (an unreadable entry of this run, or the directory itself): what happened is then unknown, and the caller must not guess."""
    prefix = _transaction_prefix(run_id) + "-"
    states: Dict[str, CommitState] = {}
    try:
        entries = list_commit_evidence(workspace_path)
    except OSError:
        return None
    for path, evidence, _error in entries:
        if not os.path.basename(path).startswith(prefix):
            continue
        if evidence is None:
            return None
        states[evidence.transaction_id] = evidence.state
    return states


def interrupted_commit_outcome(
    workspace_path: str, run_id: str, before: Optional[Dict[str, CommitState]],
) -> Optional[str]:
    """What the journal says about a terminal commit that RAISED (an interrupt included) before the service handed a result back,
    against ``before`` (``run_commit_states`` taken just before the commit): ``"COMMITTED"`` (the workspace holds the candidate),
    ``"UNCERTAIN"`` (started and not proven finished or rolled back, or the journal cannot be read: the workspace may hold part of
    it), or ``None`` (nothing of this commit is durable, or it was proven rolled back)."""
    after = run_commit_states(workspace_path, run_id)
    if after is None or before is None:
        return "UNCERTAIN"
    started = [state for transaction, state in after.items() if transaction not in before]
    if CommitState.COMMITTED in started:
        return "COMMITTED"
    if any(state in (CommitState.IN_PROGRESS, CommitState.UNCERTAIN) for state in started):
        return "UNCERTAIN"
    return None


def commit_verified_candidate(report: TerminalGateReport, request: TerminalCommitRequest) -> TerminalCommitResult:
    """PRD-004: the one real-workspace transaction of an enforce run.

    The commit itself, its durable RunRecord intent and its settled result
    are kriya/workflow/terminal_commit.py's (PRD-007).
    """
    if not report.commit_eligible:
        raise TerminalCommitNotEligibleError(
            "the terminal gates did not pass; the candidate is not eligible for commit")
    if request.candidate_root == request.workspace_path:
        return TerminalCommitResult(completed=True)
    try:
        writes = terminal_writes(request)
    except CandidateMaterializationError as error:
        return TerminalCommitResult(completed=False, failure={
            "reason_code": "CANDIDATE_MATERIALIZATION_FAILED",
            "workspace_state": "UNCHANGED", "error": str(error),
        })
    ledger_revision, ledger_hash = request.obligation_ledger.fingerprint()
    # Unique per cycle: run_id is shared by nested executes (the outer run's
    # id) and reused by resume/trace overrides, and one commit-evidence file
    # must only ever describe one transaction.
    transaction_id = f"{_transaction_prefix(request.run_id)}-{uuid.uuid4().hex[:12]}"
    outcome = commit_terminal_candidate(
        writes, workspace_path=request.workspace_path, transaction_id=transaction_id,
        evidence={
            "approved_plan_hash": request.approved_plan_hash,
            "obligation_ledger_revision": ledger_revision,
            "obligation_ledger_hash": ledger_hash,
            "verification_evidence_ids": [f"commit:{transaction_id}"],
        },
        contract_transition=request.contract_transition_for(writes, transaction_id),
        static_analysis=request.static_analysis,
        verified_candidate=report.verified_candidate,
    )
    if not outcome.committed:
        return TerminalCommitResult(completed=False, failure=outcome.failure_payload())
    # The workspace now holds the verified candidate. Everything after this
    # is persistence/observability and cannot undo that.
    return TerminalCommitResult(
        completed=True,
        evidence=outcome.evidence.to_dict() if outcome.evidence is not None else None,
        contract_registry=outcome.contract_registry,
        record_errors=tuple(outcome.record_errors),
    )
