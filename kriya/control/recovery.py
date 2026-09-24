"""Explicit, evidence-based recovery of interrupted runs and commits (PRD-008).

``kriya runs status`` (read-only) and ``kriya runs recover`` use this module.
A workspace whose earlier run may have left a partial commit is blocked by
kriya/control/commit_state.py until recovery settles it; recovery only ever
settles what durable evidence and the bytes on disk PROVE, and refuses
everything else with the exact per-file state an operator needs.

What recovery can prove, per interrupted commit transaction (evidence left
IN_PROGRESS or UNCERTAIN), by comparing each operation's recorded exact
before/after byte state (commit evidence schema 2) with the file on disk:

* every operation APPLIED       -> COMMITTED (the process died after the last
  replace, before the terminal evidence write);
* every operation NOT_APPLIED   -> ROLLED_BACK (workspace unchanged; the same
  terminal state an in-process staging failure records);
* a mix                         -> PARTIAL: settled only by an explicit
  ``--complete-partial`` roll-forward, and only when the run's own RunRecord
  holds the open commit cycle for this transaction (proof it durably reached
  COMMIT_ELIGIBLE) and that cycle, the evidence and the bytes actually on disk
  (applied targets + staged temp files) all hash to the same candidate;
* anything else (a file matching neither state, schema-1 text-only evidence
  that cannot tell the states apart, unreadable evidence) -> manual action.

There is no crash rollback: base bytes are not durable, so a partially
applied commit can only be completed or repaired by hand. (The in-process
rollback of a controlled commit failure, edit_safety.py, is unchanged.)

Per RunRecord: an open commit cycle with no evidence at all is NOT_COMMITTED
(IN_PROGRESS evidence is persisted before any byte, including a staged temp
file, reaches the workspace; retention never prunes evidence a record with an
unknown commit state references). A record is settled to RECOVERED
(RunRecord.recover) only when every open cycle is proven; RECOVERED is never
SUCCESS. A non-terminal record is a dead run only while the caller holds the
workspace lock, so ``recover`` takes the lock and ``status`` reports
RUN_ACTIVE instead of classifying anything while a live run holds it.
"""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kriya import __version__
from kriya.control.persistence import save_run_record, scan_run_records
from kriya.control.run_ownership import acquire_run_lock, probe_run_lock
from kriya.control.run_record import (
    COMMIT_COMMITTED,
    COMMIT_NOT_COMMITTED,
    COMMIT_ROLLED_BACK,
    RunRecord,
)
from kriya.workflow.edit_safety import (
    CommitEvidence,
    CommitState,
    candidate_digest_of_entries,
    content_revision,
    list_commit_evidence,
    read_file_revision,
    settle_recovered_commit_evidence,
    stage_file_prefix,
)

RECOVERY_TOOL = "kriya runs recover"

# Per-operation classification.
OP_APPLIED = "APPLIED"
OP_NOT_APPLIED = "NOT_APPLIED"
OP_FOREIGN = "FOREIGN"
OP_AMBIGUOUS = "AMBIGUOUS"

# Per-transaction outcome.
OUTCOME_COMMITTED = "COMMITTED"
OUTCOME_NOT_APPLIED = "NOT_APPLIED"
OUTCOME_PARTIAL = "PARTIAL"
OUTCOME_MANUAL = "MANUAL"
# Terminal evidence that only has leftover staged temp files to remove.
OUTCOME_SETTLED = "SETTLED"

# Workspace status.
STATUS_CLEAN = "CLEAN"
STATUS_RUN_ACTIVE = "RUN_ACTIVE"
STATUS_RECOVERY_AVAILABLE = "RECOVERY_AVAILABLE"
STATUS_COMPLETE_PARTIAL_REQUIRED = "COMPLETE_PARTIAL_REQUIRED"
STATUS_MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"

_OPEN_EVIDENCE_STATES = (CommitState.IN_PROGRESS, CommitState.UNCERTAIN)
_EVIDENCE_TO_CYCLE = {
    OUTCOME_COMMITTED: COMMIT_COMMITTED,
    OUTCOME_NOT_APPLIED: COMMIT_ROLLED_BACK,
}


def canonical_workspace(workspace_path: str) -> str:
    """The same canonical form begin_mutating_run locks and records."""
    return os.path.normcase(os.path.realpath(os.path.abspath(workspace_path)))


# ---------------------------------------------------------------- findings

@dataclass(frozen=True)
class OperationFinding:
    index: int
    target_path: str
    kind: str
    classification: str
    expected_before: Dict[str, Any]
    expected_after: Dict[str, Any]
    current: Dict[str, Any]
    staged_files: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_path": self.target_path, "kind": self.kind,
            "classification": self.classification,
            "expected_before": self.expected_before, "expected_after": self.expected_after,
            "current": self.current, "staged_files": list(self.staged_files),
        }


@dataclass(frozen=True)
class EvidenceFinding:
    transaction_id: str
    path: str
    outcome: str
    state: Optional[str] = None
    schema_version: Optional[int] = None
    candidate_hash: Optional[str] = None
    operations: Tuple[OperationFinding, ...] = ()
    owner_run_id: Optional[str] = None
    # Why --complete-partial cannot finish this PARTIAL commit (None = it can).
    roll_forward_refusal: Optional[str] = None
    leftover_staged_files: Tuple[str, ...] = ()
    error: Optional[str] = None
    evidence: Optional[CommitEvidence] = field(default=None, compare=False, repr=False)

    @property
    def open(self) -> bool:
        return self.state in {state.value for state in _OPEN_EVIDENCE_STATES}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id, "path": self.path, "outcome": self.outcome,
            "state": self.state, "schema_version": self.schema_version,
            "candidate_hash": self.candidate_hash, "owner_run_id": self.owner_run_id,
            "operations": [item.to_dict() for item in self.operations],
            "roll_forward_refusal": self.roll_forward_refusal,
            "leftover_staged_files": list(self.leftover_staged_files), "error": self.error,
        }


@dataclass(frozen=True)
class RecordFinding:
    run_id: str
    lifecycle_state: str
    commit_result: Optional[str]
    # Every open cycle -> the result recovery proved, or None when unproven.
    cycle_results: Dict[str, Optional[str]]
    blocked_reasons: Tuple[str, ...] = ()
    # PARTIAL transactions --complete-partial could settle for this record.
    completable_transactions: Tuple[str, ...] = ()
    proposed_commit_result: Optional[str] = None
    proposed_terminal_status: Optional[str] = None
    record: Optional[RunRecord] = field(default=None, compare=False, repr=False)

    @property
    def recoverable(self) -> bool:
        return not self.blocked_reasons and not self.completable_transactions

    @property
    def proven_cycle_results(self) -> Dict[str, str]:
        if not self.recoverable:
            raise ValueError(f"run {self.run_id!r} has unproven commit cycles")
        return {txid: str(result) for txid, result in self.cycle_results.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id, "lifecycle_state": self.lifecycle_state,
            "commit_result": self.commit_result, "cycle_results": dict(self.cycle_results),
            "blocked_reasons": list(self.blocked_reasons),
            "completable_transactions": list(self.completable_transactions),
            "proposed_lifecycle_state": "RECOVERED" if self.recoverable else None,
            "proposed_commit_result": self.proposed_commit_result,
            "proposed_terminal_status": self.proposed_terminal_status,
        }


@dataclass(frozen=True)
class RecoveryAssessment:
    workspace_path: str
    run_active: Optional[str] = None
    records: Tuple[RecordFinding, ...] = ()
    evidence: Tuple[EvidenceFinding, ...] = ()
    unreadable_records: Tuple[Tuple[str, str], ...] = ()
    evidence_error: Optional[str] = None

    @property
    def status(self) -> str:
        if self.run_active is not None:
            return STATUS_RUN_ACTIVE
        if self.unreadable_records or self.evidence_error or any(
            item.outcome == OUTCOME_MANUAL
            or (item.outcome == OUTCOME_PARTIAL and item.roll_forward_refusal is not None)
            for item in self.evidence
        ) or any(item.blocked_reasons for item in self.records):
            return STATUS_MANUAL_ACTION_REQUIRED
        if any(item.outcome == OUTCOME_PARTIAL for item in self.evidence):
            return STATUS_COMPLETE_PARTIAL_REQUIRED
        if self.records or self.evidence:
            return STATUS_RECOVERY_AVAILABLE
        return STATUS_CLEAN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_path": self.workspace_path, "status": self.status,
            "run_active": self.run_active,
            "records": [item.to_dict() for item in self.records],
            "evidence": [item.to_dict() for item in self.evidence],
            "unreadable_records": [
                {"path": path, "reason": reason} for path, reason in self.unreadable_records
            ],
            "evidence_error": self.evidence_error,
        }


# ---------------------------------------------------------------- observation

def _observe(path: str) -> Dict[str, Any]:
    """Exact state of a path, in the commit evidence's before/after shape.
    A symlink or directory is recorded as such: no commit ever creates one."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"exists": False, "sha256": None, "mode": None}
    if not stat.S_ISREG(info.st_mode):
        return {"exists": True, "sha256": None, "mode": None, "not_a_regular_file": True}
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return {"exists": True, "sha256": digest, "mode": info.st_mode & 0o7777}


def _same_state(current: Dict[str, Any], expected: Any) -> bool:
    if not isinstance(expected, dict) or current.get("not_a_regular_file"):
        return False
    if not expected.get("exists"):
        return not current["exists"]
    return (
        current["exists"] and current["sha256"] == expected.get("sha256")
        and current["mode"] == expected.get("mode")
    )


def _inside(workspace: str, path: str) -> bool:
    real = os.path.realpath(path)
    try:
        return os.path.commonpath((workspace, real)) == workspace and real != workspace
    except ValueError:
        return False


def _target(workspace: str, relpath: Any) -> Optional[str]:
    if not isinstance(relpath, str) or not relpath or os.path.isabs(relpath):
        return None
    path = os.path.join(workspace, relpath)
    return path if _inside(workspace, path) else None


def _staged_files(workspace: str, evidence: CommitEvidence) -> Dict[int, List[str]]:
    """Every staged temp file of this transaction, by operation index.

    Staging used the nearest EXISTING parent of each target, so the search
    walks from each target's directory up to the workspace root. Only names
    carrying this transaction's own prefix match."""
    prefix = stage_file_prefix(evidence.transaction_id)
    directories = set()
    for operation in evidence.operations:
        target = _target(workspace, operation.get("target_path"))
        cursor = os.path.dirname(target) if target else None
        while cursor and (cursor == workspace or _inside(workspace, cursor)):
            directories.add(cursor)
            if cursor == workspace:
                break
            cursor = os.path.dirname(cursor)
    found: Dict[int, List[str]] = {}
    for directory in sorted(directories):
        try:
            names = os.listdir(directory)
        except (FileNotFoundError, NotADirectoryError):
            continue
        for name in names:
            if not name.startswith(prefix):
                continue
            index = name[len(prefix):].split("-", 1)[0]
            if index.isdigit():
                found.setdefault(int(index), []).append(os.path.join(directory, name))
    return found


def _classify_v2(current: Dict[str, Any], operation: Dict[str, Any]) -> str:
    if _same_state(current, operation.get("after")):
        return OP_APPLIED
    if _same_state(current, operation.get("before")):
        return OP_NOT_APPLIED
    return OP_FOREIGN


def _classify_v1(target: str, operation: Dict[str, Any]) -> str:
    """Schema-1 evidence only has text revisions (errors="replace" decoding,
    a missing file reads like an empty one): weaker evidence, and AMBIGUOUS
    whenever it cannot tell the two states apart."""
    exists = os.path.lexists(target)
    if exists and not os.path.isfile(target):
        return OP_FOREIGN
    revision = read_file_revision(target)
    base = operation.get("expected_base_revision")
    if operation.get("operation") == "delete":
        if not exists:
            return OP_APPLIED
        return OP_NOT_APPLIED if revision == base else OP_FOREIGN
    candidate = operation.get("candidate_revision")
    if candidate == base:
        return OP_AMBIGUOUS
    if exists and revision == candidate:
        return OP_APPLIED
    if revision == base and (exists or base == content_revision("")):
        return OP_NOT_APPLIED
    return OP_FOREIGN


def _operation_findings(
    workspace: str, evidence: CommitEvidence, staged: Dict[int, List[str]],
) -> Tuple[OperationFinding, ...]:
    findings = []
    for index, operation in enumerate(evidence.operations):
        target = _target(workspace, operation.get("target_path"))
        if target is None:
            classification, current = OP_FOREIGN, {"outside_workspace": True}
        else:
            current = _observe(target)
            classification = (
                _classify_v1(target, operation) if evidence.schema_version < 2
                else _classify_v2(current, operation)
            )
        findings.append(OperationFinding(
            index=index, target_path=str(operation.get("target_path")),
            kind=str(operation.get("kind") or operation.get("operation")),
            classification=classification,
            expected_before=operation.get("before") or {"text_revision": operation.get("expected_base_revision")},
            expected_after=operation.get("after") or {"text_revision": operation.get("candidate_revision")},
            current=current, staged_files=tuple(sorted(staged.get(index, ()))),
        ))
    return tuple(findings)


def _outcome(operations: Tuple[OperationFinding, ...]) -> str:
    classes = {item.classification for item in operations}
    if classes & {OP_FOREIGN, OP_AMBIGUOUS}:
        return OUTCOME_MANUAL
    if classes <= {OP_APPLIED}:
        return OUTCOME_COMMITTED
    if classes == {OP_NOT_APPLIED}:
        return OUTCOME_NOT_APPLIED
    return OUTCOME_PARTIAL


def _cycle_owners(records: List[RunRecord]) -> Dict[str, List[Tuple[RunRecord, Dict[str, Any]]]]:
    owners: Dict[str, List[Tuple[RunRecord, Dict[str, Any]]]] = {}
    for record in records:
        for cycle in record.commits:
            owners.setdefault(cycle.get("transaction_id"), []).append((record, cycle))
    return owners


def _roll_forward_refusal(
    workspace: str, evidence: CommitEvidence, operations: Tuple[OperationFinding, ...],
    owners: List[Tuple[RunRecord, Dict[str, Any]]],
) -> Optional[str]:
    """None when --complete-partial may finish this PARTIAL transaction."""
    if evidence.schema_version < 2:
        return "schema-1 commit evidence records no byte-exact candidate state"
    if not evidence.candidate_hash:
        return "commit evidence records no candidate hash"
    if len(owners) != 1:
        return (
            "no run record holds this commit cycle, so there is no durable proof the candidate "
            "reached COMMIT_ELIGIBLE" if not owners
            else f"{len(owners)} run records claim this transaction"
        )
    record, cycle = owners[0]
    if not record.needs_recovery or cycle not in record.open_commit_cycles:
        return f"run {record.run_id!r} does not hold this transaction as an open commit cycle"
    if cycle.get("candidate_hash") != evidence.candidate_hash:
        return (
            f"run {record.run_id!r} made candidate {cycle.get('candidate_hash')!r} commit-eligible, "
            f"but the evidence describes {evidence.candidate_hash!r}"
        )
    entries = []
    for item, operation in zip(operations, evidence.operations, strict=True):
        if operation.get("operation") == "delete":
            entries.append((item.target_path, "delete", None))
            continue
        if item.classification == OP_APPLIED:
            observed = item.current
        elif len(item.staged_files) != 1:
            return (
                f"{item.target_path}: expected exactly one staged candidate file, "
                f"found {len(item.staged_files)}"
            )
        else:
            observed = _observe(item.staged_files[0])
            if not _same_state(observed, operation.get("after")):
                return f"{item.target_path}: staged file does not hold the candidate bytes and mode"
        entries.append((item.target_path, observed["sha256"], observed["mode"]))
    if candidate_digest_of_entries(entries) != evidence.candidate_hash:
        return "the bytes on disk do not hash to the commit-eligible candidate"
    return None


def _evidence_findings(
    workspace: str, records: List[RunRecord],
) -> Tuple[Tuple[EvidenceFinding, ...], Optional[str], Dict[str, EvidenceFinding]]:
    """(findings needing action, listing error, every evidence by txid)."""
    try:
        listed = list_commit_evidence(workspace)
    except OSError as error:
        return (), f"{type(error).__name__}: {error}", {}
    owners = _cycle_owners(records)
    findings: List[EvidenceFinding] = []
    by_id: Dict[str, EvidenceFinding] = {}
    for path, evidence, error in listed:
        transaction_id = os.path.basename(path)[:-len(".json")]
        if evidence is None:
            finding = EvidenceFinding(transaction_id, path, OUTCOME_MANUAL, error=error)
            findings.append(finding)
            by_id[transaction_id] = finding
            continue
        staged = _staged_files(workspace, evidence)
        leftovers = tuple(sorted(item for paths in staged.values() for item in paths))
        owner_list = owners.get(evidence.transaction_id, [])
        common = dict(
            transaction_id=evidence.transaction_id, path=path, state=evidence.state.value,
            schema_version=evidence.schema_version, candidate_hash=evidence.candidate_hash,
            owner_run_id=owner_list[0][0].run_id if len(owner_list) == 1 else None,
            leftover_staged_files=leftovers, evidence=evidence,
        )
        if evidence.state not in _OPEN_EVIDENCE_STATES:
            finding = EvidenceFinding(outcome=OUTCOME_SETTLED, **common)
            by_id[evidence.transaction_id] = finding
            if leftovers:
                findings.append(finding)
            continue
        operations = _operation_findings(workspace, evidence, staged)
        outcome = _outcome(operations)
        refusal = (
            _roll_forward_refusal(workspace, evidence, operations, owner_list)
            if outcome == OUTCOME_PARTIAL else None
        )
        finding = EvidenceFinding(
            outcome=outcome, operations=operations, roll_forward_refusal=refusal, **common,
        )
        findings.append(finding)
        by_id[evidence.transaction_id] = finding
    return tuple(findings), None, by_id


def _record_finding(
    record: RunRecord, evidence_by_id: Dict[str, EvidenceFinding], evidence_error: Optional[str],
) -> RecordFinding:
    results: Dict[str, Optional[str]] = {}
    blocked: List[str] = []
    completable: List[str] = []
    for cycle in record.open_commit_cycles:
        transaction_id = cycle["transaction_id"]
        finding = evidence_by_id.get(transaction_id)
        result: Optional[str] = None
        if finding is None and evidence_error is None:
            result = COMMIT_NOT_COMMITTED
        elif finding is None:
            blocked.append(f"{transaction_id}: commit evidence could not be listed: {evidence_error}")
        elif finding.evidence is None:
            blocked.append(f"{transaction_id}: commit evidence is unreadable: {finding.error}")
        elif finding.evidence.state == CommitState.COMMITTED:
            result = COMMIT_COMMITTED
        elif finding.evidence.state == CommitState.ROLLED_BACK:
            result = COMMIT_ROLLED_BACK
        elif finding.outcome in _EVIDENCE_TO_CYCLE:
            result = _EVIDENCE_TO_CYCLE[finding.outcome]
        elif finding.outcome == OUTCOME_PARTIAL and finding.roll_forward_refusal is None:
            completable.append(transaction_id)
        elif finding.outcome == OUTCOME_PARTIAL:
            blocked.append(f"{transaction_id}: partially applied and cannot be completed: "
                           f"{finding.roll_forward_refusal}")
        else:
            blocked.append(f"{transaction_id}: workspace files match neither the recorded "
                           "before nor after state")
        results[transaction_id] = result
    proposed_result = proposed_status = None
    if not blocked and not completable:
        preview = record.recover({txid: str(result) for txid, result in results.items()}, {})
        proposed_result, proposed_status = preview.commit_result, preview.terminal_status
    return RecordFinding(
        run_id=record.run_id, lifecycle_state=record.lifecycle_state.value,
        commit_result=record.commit_result, cycle_results=results,
        blocked_reasons=tuple(blocked), completable_transactions=tuple(completable),
        proposed_commit_result=proposed_result, proposed_terminal_status=proposed_status,
        record=record,
    )


def _assess_locked(workspace: str) -> RecoveryAssessment:
    """Classification that is only sound while no live run can be writing:
    the caller holds the lock, or has just probed that nobody does."""
    scan = scan_run_records(workspace)
    evidence, evidence_error, by_id = _evidence_findings(workspace, scan.records)
    records = tuple(
        _record_finding(record, by_id, evidence_error)
        for record in scan.records if record.needs_recovery
    )
    return RecoveryAssessment(
        workspace_path=workspace, records=records, evidence=evidence,
        unreadable_records=tuple((item.path, item.reason) for item in scan.unreadable),
        evidence_error=evidence_error,
    )


def assess_recovery(workspace_path: str) -> RecoveryAssessment:
    """``kriya runs status``: read-only; never creates, locks or writes."""
    workspace = canonical_workspace(workspace_path)
    owner = probe_run_lock(workspace)
    if owner is not None:
        return RecoveryAssessment(workspace_path=workspace, run_active=owner)
    return _assess_locked(workspace)


# ---------------------------------------------------------------- recovery

@dataclass
class RecoveryReport:
    before: RecoveryAssessment
    after: RecoveryAssessment
    settled_evidence: List[Dict[str, Any]] = field(default_factory=list)
    rolled_forward: List[str] = field(default_factory=list)
    removed_staged_files: List[str] = field(default_factory=list)
    recovered_runs: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.after.status,
            "settled_evidence": self.settled_evidence, "rolled_forward": self.rolled_forward,
            "removed_staged_files": self.removed_staged_files,
            "recovered_runs": self.recovered_runs, "errors": self.errors,
            "remaining": self.after.to_dict(),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fsync_directory(path: str) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _roll_forward(workspace: str, finding: EvidenceFinding) -> List[str]:
    """Finish every NOT_APPLIED operation of an eligible PARTIAL commit.
    Each step re-checks the target and staged file immediately before it
    acts; a crash part-way leaves more operations APPLIED, which a later
    recovery classifies the same way."""
    evidence = finding.evidence
    assert evidence is not None
    completed = []
    for item, operation in zip(finding.operations, evidence.operations, strict=True):
        if item.classification != OP_NOT_APPLIED:
            continue
        target = _target(workspace, operation.get("target_path"))
        if target is None or not _same_state(_observe(target), operation.get("before")):
            raise RuntimeError(f"{item.target_path} changed during recovery; nothing further applied")
        if operation.get("operation") == "delete":
            os.unlink(target)
        else:
            staged = item.staged_files[0]
            if not _same_state(_observe(staged), operation.get("after")):
                raise RuntimeError(f"staged file for {item.target_path} changed during recovery")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(staged, target)
        _fsync_directory(os.path.dirname(target))
        completed.append(item.target_path)
    return completed


def _result_revisions(workspace: str, evidence: CommitEvidence) -> Dict[str, str]:
    revisions = {}
    for operation in evidence.operations:
        relpath = operation.get("target_path")
        revisions[relpath] = (
            content_revision("") if operation.get("operation") == "delete"
            else read_file_revision(os.path.join(workspace, relpath))
        )
    return revisions


def _settle_evidence(
    workspace: str, finding: EvidenceFinding, outcome: str, provenance: Dict[str, Any],
    rolled_forward: List[str],
) -> Dict[str, Any]:
    evidence = finding.evidence
    assert evidence is not None
    state = CommitState.COMMITTED if outcome == OUTCOME_COMMITTED else CommitState.ROLLED_BACK
    recovery = {
        **provenance,
        "outcome": "ROLLED_FORWARD" if rolled_forward else outcome,
        "prior_state": evidence.state.value,
        "operations": [
            {"target_path": item.target_path, "classification": item.classification}
            for item in finding.operations
        ],
        "rolled_forward": rolled_forward,
    }
    settle_recovered_commit_evidence(
        workspace, evidence, state=state, recovery=recovery,
        result_revisions=_result_revisions(workspace, evidence) if state == CommitState.COMMITTED else {},
    )
    return {"transaction_id": evidence.transaction_id, "state": state.value, "outcome": recovery["outcome"]}


def _remove_staged(paths: Tuple[str, ...], report: RecoveryReport) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except FileNotFoundError:
            continue
        except OSError as error:
            report.errors.append(f"could not remove staged file {path}: {error}")
            continue
        report.removed_staged_files.append(path)


def recover_workspace(workspace_path: str, *, complete_partial: bool = False) -> RecoveryReport:
    """``kriya runs recover``: settle everything the evidence proves.

    Takes the workspace lock (WorkspaceLockHeldError while a run is live).
    Evidence is settled before the records that reference it, so a crash in
    between leaves a record whose next recovery reads the settled evidence.
    Source files change only for an eligible PARTIAL commit and only with
    ``complete_partial``; staged temp files are removed only once their
    transaction is settled."""
    workspace = canonical_workspace(workspace_path)
    with acquire_run_lock(workspace, run_id=f"recover-{uuid.uuid4().hex[:12]}"):
        before = _assess_locked(workspace)
        report = RecoveryReport(before=before, after=before)
        provenance = {
            "tool": RECOVERY_TOOL, "kriya_version": __version__, "recovered_at": _now(),
            "complete_partial": complete_partial,
        }
        for finding in before.evidence:
            if finding.evidence is None:
                continue
            if finding.outcome == OUTCOME_SETTLED:
                _remove_staged(finding.leftover_staged_files, report)
                continue
            outcome, rolled = finding.outcome, []
            if outcome == OUTCOME_PARTIAL:
                if not complete_partial or finding.roll_forward_refusal is not None:
                    continue
                try:
                    rolled = _roll_forward(workspace, finding)
                except Exception as error:
                    report.errors.append(f"{finding.transaction_id}: roll-forward stopped: {error}")
                    continue
                report.rolled_forward.extend(rolled)
                refreshed = _operation_findings(
                    workspace, finding.evidence, _staged_files(workspace, finding.evidence),
                )
                if _outcome(refreshed) != OUTCOME_COMMITTED:
                    report.errors.append(
                        f"{finding.transaction_id}: not every operation is applied after roll-forward"
                    )
                    continue
                finding = replace(finding, operations=refreshed)
                outcome = OUTCOME_COMMITTED
            elif outcome not in _EVIDENCE_TO_CYCLE:
                continue
            try:
                report.settled_evidence.append(
                    _settle_evidence(workspace, finding, outcome, provenance, rolled),
                )
            except Exception as error:
                report.errors.append(f"{finding.transaction_id}: evidence not settled: {error}")
                continue
            _remove_staged(
                tuple(sorted(p for paths in _staged_files(workspace, finding.evidence).values() for p in paths)),
                report,
            )

        # Records second, from the evidence as it now stands.
        for finding in _assess_locked(workspace).records:
            if not finding.recoverable or finding.record is None:
                continue
            record = finding.record
            try:
                recovered = record.recover(finding.proven_cycle_results, provenance)
                save_run_record(workspace, recovered, expected_revision=record.revision)
            except Exception as error:
                report.errors.append(f"run {record.run_id}: not recovered: {error}")
                continue
            report.recovered_runs.append({
                "run_id": record.run_id, "prior_lifecycle_state": record.lifecycle_state.value,
                "commit_result": recovered.commit_result,
                "terminal_status": recovered.terminal_status,
            })
        report.after = _assess_locked(workspace)
        return report


__all__ = [
    "OP_AMBIGUOUS", "OP_APPLIED", "OP_FOREIGN", "OP_NOT_APPLIED",
    "OUTCOME_COMMITTED", "OUTCOME_MANUAL", "OUTCOME_NOT_APPLIED", "OUTCOME_PARTIAL",
    "OUTCOME_SETTLED", "RECOVERY_TOOL",
    "STATUS_CLEAN", "STATUS_COMPLETE_PARTIAL_REQUIRED", "STATUS_MANUAL_ACTION_REQUIRED",
    "STATUS_RECOVERY_AVAILABLE", "STATUS_RUN_ACTIVE",
    "EvidenceFinding", "OperationFinding", "RecordFinding", "RecoveryAssessment", "RecoveryReport",
    "assess_recovery", "canonical_workspace", "recover_workspace",
]
