"""The one real-workspace commit seam for a verified candidate (PRD-004/005/007).

Both terminal commit sites - the enforce controller's plan-level candidate
and the generation workflow's per-run sandbox - go through this module, so
they share one set of guarantees:

* the candidate's exact verified bytes and file mode are what gets committed
  (never a text-decoded copy);
* when an active run owns the target workspace, durable commit intent is
  recorded in its RunRecord BEFORE the first workspace byte changes, and a
  commit whose intent cannot be persisted is refused;
* every outcome settles the run's commit cycle from the commit evidence -
  COMMITTED, ROLLED_BACK/NOT_COMMITTED (workspace unchanged), or UNCERTAIN.

Callers map the returned TerminalCommitOutcome into their own result shape.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from kriya.control.run_coordinator import begin_run_commit, settle_run_commit
from kriya.control.run_record import (
    COMMIT_COMMITTED,
    COMMIT_NOT_COMMITTED,
    COMMIT_ROLLED_BACK,
    COMMIT_UNCERTAIN,
)
from kriya.workflow.edit_safety import (
    BatchCommitError,
    CommitEvidence,
    CommitState,
    FileRevisionConflict,
    StagedFileWrite,
    UncertainCommitError,
    candidate_digest,
    commit_revision_grounded_batch,
    commit_state_for_transaction,
)


class CandidateMaterializationError(RuntimeError):
    """An approved candidate file is missing or unreadable; nothing was committed."""


@dataclass(frozen=True)
class CandidateFile:
    """One approved path of a verified candidate and the base it was built on."""

    relpath: str
    expected_base_revision: str
    # None = revision-only identity (callers that cannot tell an empty
    # existing file from a missing one).
    expected_base_exists: Optional[bool] = None
    delete: bool = False


def materialize_candidate(
    candidate_root: str, workspace_root: str, files: Iterable[CandidateFile],
) -> List[StagedFileWrite]:
    """Read the candidate's exact bytes and mode into a guarded batch.

    Reads only the candidate; the real workspace is touched later, by the
    revision-grounded commit, which checks each recorded base revision."""
    writes: List[StagedFileWrite] = []
    for item in files:
        candidate_path = os.path.join(candidate_root, item.relpath)
        target_path = os.path.join(workspace_root, item.relpath)
        if item.delete:
            if os.path.lexists(candidate_path):
                raise CandidateMaterializationError(
                    f"Verified candidate did not delete approved file {item.relpath!r}"
                )
            writes.append(StagedFileWrite(
                target_path=target_path, content="", base_path=target_path,
                expected_base_revision=item.expected_base_revision, delete=True,
                expected_base_exists=True,
            ))
            continue
        try:
            with open(candidate_path, "rb") as handle:
                candidate_bytes = handle.read()
            candidate_mode = os.stat(candidate_path).st_mode & 0o7777
            # The decoded text is only the revision identity, matching
            # read_file_revision(); the bytes are what reach disk.
            with open(candidate_path, "r", encoding="utf-8", errors="replace") as handle:
                candidate_text = handle.read()
        except OSError as error:
            raise CandidateMaterializationError(
                f"Verified candidate is missing approved file {item.relpath!r}: {error}"
            ) from error
        writes.append(StagedFileWrite(
            target_path=target_path, content=candidate_text, base_path=target_path,
            expected_base_revision=item.expected_base_revision,
            expected_base_exists=item.expected_base_exists,
            content_bytes=candidate_bytes, mode=candidate_mode,
        ))
    return writes


@dataclass
class TerminalCommitOutcome:
    committed: bool
    # "COMMITTED" | "UNCHANGED" | "UNCERTAIN" - the real workspace, not the run.
    workspace_state: str
    commit_result: str
    transaction_id: str
    reason_code: Optional[str] = None
    evidence: Optional[CommitEvidence] = None
    error: Optional[BaseException] = None
    # Record persistence failures AFTER the workspace outcome was decided.
    record_errors: List[Dict[str, str]] = field(default_factory=list)

    def failure_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "reason_code": self.reason_code, "workspace_state": self.workspace_state,
            "error": str(self.error) if self.error is not None else None,
            "commit_transaction_id": self.transaction_id,
        }
        if self.workspace_state == "UNCHANGED":
            payload["commit_result"] = self.commit_result
        return payload


def _settled_state(workspace_path: str, transaction_id: str) -> str:
    """The cycle result the durable commit evidence proves."""
    try:
        state = commit_state_for_transaction(workspace_path, transaction_id)
    except Exception:
        return COMMIT_UNCERTAIN
    if state == CommitState.COMMITTED:
        return COMMIT_COMMITTED
    if state == CommitState.ROLLED_BACK:
        return COMMIT_ROLLED_BACK
    if state == CommitState.NOT_STARTED:
        return COMMIT_NOT_COMMITTED
    return COMMIT_UNCERTAIN


def commit_terminal_candidate(
    writes: List[StagedFileWrite], *, workspace_path: str, transaction_id: str,
    evidence: Optional[Dict[str, Any]] = None,
) -> TerminalCommitOutcome:
    """Commit a materialized, verified candidate into the real workspace.

    Controlled commit outcomes are returned, never raised. Any other
    exception settles the cycle from the durable evidence and propagates.
    """
    if not writes:
        return TerminalCommitOutcome(
            committed=True, workspace_state="UNCHANGED", commit_result="NO_CHANGES",
            transaction_id=transaction_id,
        )
    try:
        run = begin_run_commit(
            workspace_path, transaction_id,
            candidate_hash=candidate_digest(writes, workspace_path), **(evidence or {}),
        )
    except Exception as error:
        # Without durable intent a crash inside the commit could never be
        # recognized later, so the commit must not start.
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=transaction_id, reason_code="RUN_RECORD_INTENT_NOT_PERSISTED",
            error=error,
        )

    def settle(result: str, outcome: TerminalCommitOutcome) -> TerminalCommitOutcome:
        record_error = settle_run_commit(run, result)
        if record_error is not None:
            outcome.record_errors.append({
                "operation": f"run_record_commit_{result.lower()}", "error": record_error,
            })
        return outcome

    try:
        result = commit_revision_grounded_batch(
            writes, workspace_path=workspace_path, transaction_id=transaction_id,
        )
    except UncertainCommitError as error:
        return settle(COMMIT_UNCERTAIN, TerminalCommitOutcome(
            committed=False, workspace_state="UNCERTAIN", commit_result=COMMIT_UNCERTAIN,
            transaction_id=transaction_id, reason_code="WORKSPACE_COMMIT_UNCERTAIN", error=error,
        ))
    except (FileRevisionConflict, BatchCommitError) as error:
        settled = _settled_state(workspace_path, transaction_id)
        if settled == COMMIT_UNCERTAIN:
            return settle(COMMIT_UNCERTAIN, TerminalCommitOutcome(
                committed=False, workspace_state="UNCERTAIN", commit_result=COMMIT_UNCERTAIN,
                transaction_id=transaction_id, reason_code="WORKSPACE_COMMIT_UNCERTAIN",
                error=error,
            ))
        conflict = isinstance(error, FileRevisionConflict) or isinstance(
            error.__cause__, FileRevisionConflict,
        )
        return settle(settled, TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=settled,
            transaction_id=transaction_id,
            reason_code="WORKSPACE_REVISION_CONFLICT" if conflict else "WORKSPACE_COMMIT_FAILED",
            error=error,
        ))
    except BaseException:
        # Unexpected (including KeyboardInterrupt mid-commit): settle what
        # the evidence proves - IN_PROGRESS becomes UNCERTAIN - then raise.
        settle_run_commit(run, _settled_state(workspace_path, transaction_id))
        raise

    return settle(COMMIT_COMMITTED, TerminalCommitOutcome(
        committed=True, workspace_state="COMMITTED", commit_result=COMMIT_COMMITTED,
        transaction_id=transaction_id, evidence=result.evidence,
    ))
