"""Milestone completion proofs and reuse decisions (PRD-008 S4b).

A milestone in ``completed_milestone_ids`` is skipped on a later run only
when durable evidence proves its output is still what it committed. The
milestone sidecar is an index, never the authority:

    completion proof -> commit ledger entry -> RunRecord cycle (COMMITTED)
        -> commit evidence (COMMITTED, same candidate hash, same operations)
        -> the current workspace bytes and mode

The ledger records every COMMITTED real-workspace cycle the milestone driver
made, in order: each milestone's own cycles (retries included), cycles of
milestones later abandoned, and the integration pass. Milestones share files
(pom.xml is the canonical case), so a path's expected state is the post-state
of its LATEST verified writer, and a mismatch is charged to that writer: M1 is
not stale merely because M2 legitimately modified a file M1 created.

A completed milestone is then:
  * MATCH (skip) - every link verifies and every path it still owns matches;
  * CHANGED (rerun) - a path it owns no longer has the committed state, its
    own definition in the (hand-editable) plan changed, or a milestone it
    depends on, or an unrelated milestone sharing one of its
    paths, reruns;
  * UNVERIFIED (rerun) - any required evidence is missing, unreadable,
    pruned, legacy or inconsistent. Nothing is reconstructed from the
    sidecar alone.

The status vocabulary is PRD-008's FingerprintStatus, so direct resume and
milestone reuse share one freshness model. No model call participates.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from kriya.control.persistence import load_run_record
from kriya.control.recovery import (
    canonical_workspace,
    contained_workspace_path,
    matches_file_state,
    observe_path_state,
)
from kriya.control.run_coordinator import owning_run_commits
from kriya.control.run_record import COMMIT_COMMITTED
from kriya.workflow.edit_safety import CommitState, commit_evidence_dir, load_commit_evidence
from kriya.workflow.resume_fingerprints import FingerprintStatus

MILESTONE_COMPLETION_SCHEMA_VERSION = 1
MILESTONE_SIDECAR_RELATIVE_DIR = os.path.join(".kriya", "milestones")

ACTION_SKIP = "SKIP"
ACTION_RERUN = "RERUN"

# Reason codes (machine-readable; the decision is never only log text).
COMPLETION_PROOF_MISSING = "COMPLETION_PROOF_MISSING"
LEGACY_STATE_UNVERIFIED = "LEGACY_STATE_UNVERIFIED"
NO_COMMITTED_OUTPUT = "NO_COMMITTED_OUTPUT"
TRANSACTION_MISMATCH = "TRANSACTION_MISMATCH"
RUN_RECORD_MISSING = "RUN_RECORD_MISSING"
RUN_RECORD_UNREADABLE = "RUN_RECORD_UNREADABLE"
COMMIT_NOT_COMMITTED = "COMMIT_NOT_COMMITTED"
COMMIT_EVIDENCE_MISSING = "COMMIT_EVIDENCE_MISSING"
COMMIT_EVIDENCE_UNREADABLE = "COMMIT_EVIDENCE_UNREADABLE"
COMMIT_EVIDENCE_LEGACY = "COMMIT_EVIDENCE_LEGACY"
PROOF_EVIDENCE_MISMATCH = "PROOF_EVIDENCE_MISMATCH"
OUTPUT_MISSING = "OUTPUT_MISSING"
OUTPUT_CHANGED = "OUTPUT_CHANGED"
DELETED_PATH_RECREATED = "DELETED_PATH_RECREATED"
MODE_CHANGED = "MODE_CHANGED"
MILESTONE_DEFINITION_CHANGED = "MILESTONE_DEFINITION_CHANGED"
UPSTREAM_INVALIDATED = "UPSTREAM_INVALIDATED"
SHARED_PATH_WITH_RERUN = "SHARED_PATH_WITH_RERUN"

_TRANSACTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_OPERATION_KINDS = frozenset({"CREATE", "MODIFY", "DELETE"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- records

@dataclass(frozen=True)
class CommittedOperation:
    """One path's committed post-state, copied from the commit evidence."""

    path: str
    kind: str  # CREATE | MODIFY | DELETE
    post_state: Dict[str, Any]  # {"exists", "sha256", "mode"}

    def key(self) -> Tuple[str, str, str]:
        return self.path, self.kind, json.dumps(self.post_state, sort_keys=True)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "operation": self.kind, "post_state": dict(self.post_state)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommittedOperation":
        post = data["post_state"]
        if not isinstance(data["path"], str) or not isinstance(post, dict):
            raise ValueError("malformed committed operation")
        return cls(path=data["path"], kind=str(data["operation"]), post_state={
            "exists": post.get("exists"), "sha256": post.get("sha256"), "mode": post.get("mode"),
        })


@dataclass(frozen=True)
class MilestoneCommitEntry:
    """One COMMITTED real-workspace cycle made by the milestone driver.
    ``milestone_id`` None marks the integration pass."""

    milestone_id: Optional[str]
    run_id: str
    transaction_id: str
    candidate_hash: Optional[str]
    operations: Tuple[CommittedOperation, ...]
    recorded_at: str
    # Set when the evidence could not be read at record time; the entry can
    # then never verify.
    evidence_error: Optional[str] = None

    @property
    def paths(self) -> Set[str]:
        return {operation.path for operation in self.operations}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id, "run_id": self.run_id,
            "transaction_id": self.transaction_id, "candidate_hash": self.candidate_hash,
            "operations": [operation.to_dict() for operation in self.operations],
            "recorded_at": self.recorded_at, "evidence_error": self.evidence_error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MilestoneCommitEntry":
        return cls(
            milestone_id=data.get("milestone_id"), run_id=str(data["run_id"]),
            transaction_id=str(data["transaction_id"]), candidate_hash=data.get("candidate_hash"),
            operations=tuple(CommittedOperation.from_dict(item) for item in data["operations"]),
            recorded_at=str(data.get("recorded_at", "")), evidence_error=data.get("evidence_error"),
        )


def milestone_definition_digest(milestone: Any) -> str:
    """The milestone's own definition (goal, criterion, depends_on,
    provides, consumes, ...) - the milestone counterpart of direct resume's
    goal fingerprint. Deliberately not the rendered goal text, which embeds
    position/total and would change whenever another milestone is added."""
    payload = milestone.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class MilestoneCompletionProof:
    """What a completed milestone committed: its ledger transactions, in
    order, and the definition it completed under. The post-states live in
    those ledger entries."""

    milestone_id: str
    run_id: str
    transaction_ids: Tuple[str, ...]
    completed_at: str
    definition_digest: str
    schema_version: int = MILESTONE_COMPLETION_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version, "milestone_id": self.milestone_id,
            "run_id": self.run_id, "transaction_ids": list(self.transaction_ids),
            "completed_at": self.completed_at, "definition_digest": self.definition_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MilestoneCompletionProof":
        if data.get("schema_version") != MILESTONE_COMPLETION_SCHEMA_VERSION:
            raise ValueError(f"unsupported completion proof schema {data.get('schema_version')!r}")
        transaction_ids = data["transaction_ids"]
        if not isinstance(transaction_ids, list):
            raise ValueError("malformed completion proof transaction_ids")
        return cls(
            milestone_id=str(data["milestone_id"]), run_id=str(data["run_id"]),
            transaction_ids=tuple(str(item) for item in transaction_ids),
            completed_at=str(data.get("completed_at", "")),
            definition_digest=str(data["definition_digest"]),
        )


# ---------------------------------------------------------------- recording

def owning_run_commit_count(workspace_path: str) -> int:
    """How many commit cycles the owning run has so far (0 without one)."""
    owned = owning_run_commits(workspace_path)
    return len(owned[1]) if owned is not None else 0


def _evidence_file(workspace: str, transaction_id: str) -> Optional[str]:
    if not _TRANSACTION_ID.fullmatch(transaction_id):
        return None
    return os.path.join(commit_evidence_dir(workspace), f"{transaction_id}.json")


def _operations_from_evidence(evidence: Any) -> Tuple[CommittedOperation, ...]:
    operations = []
    for operation in evidence.operations:
        after = operation.get("after")
        kind = operation.get("kind")
        path = operation.get("target_path")
        if not isinstance(after, dict) or kind not in _OPERATION_KINDS or not isinstance(path, str):
            raise ValueError("commit evidence has no exact post-state for every operation")
        operations.append(CommittedOperation(path=path, kind=kind, post_state={
            "exists": after.get("exists"), "sha256": after.get("sha256"), "mode": after.get("mode"),
        }))
    return tuple(operations)


def record_milestone_commits(
    workspace_path: str, milestone_id: Optional[str], cycles_before: int,
) -> List[MilestoneCommitEntry]:
    """Ledger entries for the owning run's COMMITTED cycles since
    ``cycles_before``, derived from the RunRecord and commit evidence (never
    from a workflow's reported file list)."""
    owned = owning_run_commits(workspace_path)
    if owned is None:
        return []
    run_id, cycles = owned
    workspace = canonical_workspace(workspace_path)
    entries = []
    for cycle in cycles[cycles_before:]:
        if cycle.get("result") != COMMIT_COMMITTED:
            continue
        transaction_id = str(cycle.get("transaction_id"))
        operations: Tuple[CommittedOperation, ...] = ()
        error = None
        try:
            path = _evidence_file(workspace, transaction_id)
            if path is None:
                raise ValueError(f"invalid transaction id {transaction_id!r}")
            operations = _operations_from_evidence(load_commit_evidence(path))
        except Exception as exc:  # recorded; the entry then never verifies
            error = f"{type(exc).__name__}: {exc}"
        entries.append(MilestoneCommitEntry(
            milestone_id=milestone_id, run_id=run_id, transaction_id=transaction_id,
            candidate_hash=cycle.get("candidate_hash"), operations=operations,
            recorded_at=_now(), evidence_error=error,
        ))
    return entries


def completion_proof_for(
    milestone: Any, entries: Sequence[MilestoneCommitEntry], run_id: Optional[str],
) -> MilestoneCompletionProof:
    return MilestoneCompletionProof(
        milestone_id=milestone.id,
        run_id=run_id or (entries[0].run_id if entries else ""),
        transaction_ids=tuple(entry.transaction_id for entry in entries),
        completed_at=_now(),
        definition_digest=milestone_definition_digest(milestone),
    )


# ---------------------------------------------------------------- assessment

def _reason(code: str, detail: str, **extra: Any) -> Dict[str, Any]:
    return {"code": code, "detail": detail, **{k: v for k, v in extra.items() if v is not None}}


@dataclass
class MilestoneReuseDecision:
    milestone_id: str
    status: FingerprintStatus
    reasons: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def action(self) -> str:
        return ACTION_SKIP if self.status is FingerprintStatus.MATCH else ACTION_RERUN

    @property
    def reason_codes(self) -> List[str]:
        return [reason["code"] for reason in self.reasons]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id, "status": self.status.value,
            "action": self.action, "reasons": list(self.reasons),
        }


@dataclass
class MilestoneReuseAssessment:
    decisions: List[MilestoneReuseDecision] = field(default_factory=list)
    assessed_at: str = field(default_factory=_now)
    # The run that made the decision: one assessment per run, so a second
    # entry point in the same run reuses it instead of re-deciding over the
    # already-pruned completed list (and losing why milestones reran).
    run_id: Optional[str] = None

    def get(self, milestone_id: str) -> Optional[MilestoneReuseDecision]:
        return next((item for item in self.decisions if item.milestone_id == milestone_id), None)

    @property
    def rerun_ids(self) -> Set[str]:
        return {item.milestone_id for item in self.decisions if item.action == ACTION_RERUN}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": MILESTONE_COMPLETION_SCHEMA_VERSION,
            "assessed_at": self.assessed_at,
            "run_id": self.run_id,
            "decisions": [item.to_dict() for item in self.decisions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MilestoneReuseAssessment":
        return cls(
            decisions=[
                MilestoneReuseDecision(
                    milestone_id=item["milestone_id"], status=FingerprintStatus(item["status"]),
                    reasons=list(item.get("reasons", [])),
                )
                for item in data.get("decisions", [])
            ],
            assessed_at=str(data.get("assessed_at", "")), run_id=data.get("run_id"),
        )


def _verify_entry(
    workspace: str, entry: MilestoneCommitEntry, records: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """None when the entry is bound to durable COMMITTED evidence; else the
    UNVERIFIED reason."""
    txid = entry.transaction_id
    evidence_path = _evidence_file(workspace, txid)
    if evidence_path is None:
        return _reason(TRANSACTION_MISMATCH, "invalid transaction id", transaction_id=txid)
    if entry.run_id not in records:
        try:
            records[entry.run_id] = load_run_record(workspace, entry.run_id)
        except Exception as error:
            records[entry.run_id] = error
    record = records[entry.run_id]
    if isinstance(record, Exception):
        return _reason(RUN_RECORD_UNREADABLE, f"{type(record).__name__}: {record}", transaction_id=txid)
    if record is None:
        return _reason(
            RUN_RECORD_MISSING, f"run record {entry.run_id!r} is missing (pruned or deleted)",
            transaction_id=txid,
        )
    cycle = next((c for c in record.commits if c.get("transaction_id") == txid), None)
    if cycle is None:
        return _reason(
            TRANSACTION_MISMATCH, f"run {entry.run_id!r} has no commit cycle {txid!r}", transaction_id=txid,
        )
    if cycle.get("result") != COMMIT_COMMITTED:
        return _reason(
            COMMIT_NOT_COMMITTED, f"cycle result is {cycle.get('result')!r}", transaction_id=txid,
        )
    if not os.path.exists(evidence_path):
        return _reason(COMMIT_EVIDENCE_MISSING, "commit evidence file is missing", transaction_id=txid)
    try:
        evidence = load_commit_evidence(evidence_path)
    except Exception as error:
        return _reason(COMMIT_EVIDENCE_UNREADABLE, f"{type(error).__name__}: {error}", transaction_id=txid)
    if evidence.schema_version < 2:
        return _reason(
            COMMIT_EVIDENCE_LEGACY, "schema-1 evidence has no exact post-state", transaction_id=txid,
        )
    if evidence.state != CommitState.COMMITTED:
        return _reason(
            COMMIT_NOT_COMMITTED, f"evidence state is {evidence.state.value}", transaction_id=txid,
        )
    if not evidence.candidate_hash or not (
        evidence.candidate_hash == cycle.get("candidate_hash") == entry.candidate_hash
    ):
        return _reason(
            PROOF_EVIDENCE_MISMATCH, "candidate hash differs between proof, run record and evidence",
            transaction_id=txid,
        )
    try:
        authoritative = _operations_from_evidence(evidence)
    except ValueError as error:
        return _reason(PROOF_EVIDENCE_MISMATCH, str(error), transaction_id=txid)
    if entry.evidence_error or sorted(op.key() for op in authoritative) != sorted(
        op.key() for op in entry.operations
    ):
        return _reason(
            PROOF_EVIDENCE_MISMATCH, "proof operations differ from the commit evidence", transaction_id=txid,
        )
    return None


def _output_mismatch(workspace: str, operation: CommittedOperation) -> Optional[Dict[str, Any]]:
    target = contained_workspace_path(workspace, operation.path)
    if target is None:
        return _reason(OUTPUT_CHANGED, "path is not inside the workspace", path=operation.path)
    current = observe_path_state(target)
    expected = operation.post_state
    if matches_file_state(current, expected):
        return None
    if not expected.get("exists"):
        return _reason(DELETED_PATH_RECREATED, "a path the milestone deleted exists again", path=operation.path)
    if not current["exists"]:
        return _reason(OUTPUT_MISSING, "committed file no longer exists", path=operation.path)
    if current.get("not_a_regular_file"):
        return _reason(OUTPUT_CHANGED, "committed file is no longer a regular file", path=operation.path)
    if current["sha256"] == expected.get("sha256"):
        return _reason(
            MODE_CHANGED, f"mode {current['mode']:o} differs from the committed mode",
            path=operation.path,
        )
    return _reason(OUTPUT_CHANGED, "committed bytes changed", path=operation.path)


def _parse_ledger(ledger: Iterable[Any]) -> Tuple[List[MilestoneCommitEntry], List[Dict[str, Any]]]:
    entries, malformed = [], []
    for raw in ledger:
        try:
            entries.append(MilestoneCommitEntry.from_dict(raw))
        except Exception as error:
            malformed.append({"entry": raw, "error": f"{type(error).__name__}: {error}"})
    return entries, malformed


def _ancestors(milestones: Sequence[Any], milestone_id: str) -> Set[str]:
    by_id = {milestone.id: milestone for milestone in milestones}
    seen: Set[str] = set()
    stack = list(getattr(by_id.get(milestone_id), "depends_on", []) or [])
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(getattr(by_id.get(current), "depends_on", []) or [])
    return seen


def assess_completed_milestone_reuse(
    workspace_path: str,
    milestones: Sequence[Any],
    completed_ids: Sequence[str],
    proofs: Mapping[str, Any],
    ledger: Sequence[Any],
    *,
    legacy_state: bool = False,
) -> MilestoneReuseAssessment:
    """Decide, for every completed milestone, whether it may be skipped.

    ``legacy_state`` marks a sidecar written before completion proofs
    existed: its completed entries are UNVERIFIED, never grandfathered."""
    workspace = canonical_workspace(workspace_path)
    plan_ids = [milestone.id for milestone in milestones]
    completed = [mid for mid in plan_ids if mid in set(completed_ids)]
    entries, malformed = _parse_ledger(ledger)
    records: Dict[str, Any] = {}
    failures = {id(entry): _verify_entry(workspace, entry, records) for entry in entries}

    # The latest VERIFIED writer of each path owns its expected state.
    owner: Dict[str, Tuple[MilestoneCommitEntry, CommittedOperation]] = {}
    for entry in entries:
        if failures[id(entry)] is None:
            for operation in entry.operations:
                owner[operation.path] = (entry, operation)

    by_id = {milestone.id: milestone for milestone in milestones}
    decisions: Dict[str, MilestoneReuseDecision] = {}
    for milestone_id in completed:
        decisions[milestone_id] = _assess_one(
            workspace, by_id[milestone_id], proofs.get(milestone_id), entries, failures, owner,
            legacy_state=legacy_state, malformed_ledger=bool(malformed),
        )

    # Everything that will run this time: completed milestones that failed
    # the check, and every milestone not completed at all.
    paths_by_milestone: Dict[str, Set[str]] = {}
    for entry in entries:
        if entry.milestone_id is not None:
            paths_by_milestone.setdefault(entry.milestone_id, set()).update(entry.paths)
    rerun = {mid for mid in plan_ids if mid not in decisions or decisions[mid].action == ACTION_RERUN}
    changed = True
    while changed:
        changed = False
        for milestone in milestones:
            decision = decisions.get(milestone.id)
            if decision is None or decision.action == ACTION_RERUN:
                continue
            upstream = sorted(_ancestors(milestones, milestone.id) & rerun)
            if upstream:
                decision.status = FingerprintStatus.CHANGED
                decision.reasons.append(_reason(
                    UPSTREAM_INVALIDATED, "a milestone it depends on reruns", upstream=upstream,
                ))
            else:
                own_paths = paths_by_milestone.get(milestone.id, set())
                sharing = sorted(
                    other for other in rerun
                    if own_paths & paths_by_milestone.get(other, set())
                    and milestone.id not in _ancestors(milestones, other)
                )
                if not sharing:
                    continue
                # A rerun that is not built on this milestone could rewrite
                # a path this milestone still owns, after it was skipped.
                decision.status = FingerprintStatus.CHANGED
                decision.reasons.append(_reason(
                    SHARED_PATH_WITH_RERUN, "an unrelated rerunning milestone wrote the same paths",
                    upstream=sharing,
                ))
            rerun.add(milestone.id)
            changed = True
    return MilestoneReuseAssessment(decisions=[decisions[mid] for mid in completed])


def _assess_one(
    workspace: str,
    milestone: Any,
    raw_proof: Any,
    entries: Sequence[MilestoneCommitEntry],
    failures: Mapping[int, Optional[Dict[str, Any]]],
    owner: Mapping[str, Tuple[MilestoneCommitEntry, CommittedOperation]],
    *,
    legacy_state: bool,
    malformed_ledger: bool,
) -> MilestoneReuseDecision:
    milestone_id = milestone.id
    decision = MilestoneReuseDecision(milestone_id, FingerprintStatus.UNVERIFIED)
    if raw_proof is None:
        decision.reasons.append(_reason(
            LEGACY_STATE_UNVERIFIED if legacy_state else COMPLETION_PROOF_MISSING,
            "completed without a completion proof; completed_milestone_ids alone never authorizes a skip",
        ))
        return decision
    try:
        proof = MilestoneCompletionProof.from_dict(raw_proof)
    except Exception as error:
        decision.reasons.append(_reason(COMPLETION_PROOF_MISSING, f"unreadable proof: {error}"))
        return decision
    if proof.milestone_id != milestone_id:
        decision.reasons.append(_reason(COMPLETION_PROOF_MISSING, "proof names a different milestone"))
        return decision
    if proof.definition_digest != milestone_definition_digest(milestone):
        decision.status = FingerprintStatus.CHANGED
        decision.reasons.append(_reason(
            MILESTONE_DEFINITION_CHANGED, "the milestone's definition in the plan changed since it completed",
        ))
        return decision
    if not proof.transaction_ids:
        decision.reasons.append(_reason(
            NO_COMMITTED_OUTPUT, "the milestone committed nothing, so no durable commit proves it",
        ))
        return decision
    own_entries = []
    for txid in proof.transaction_ids:
        entry = next((
            item for item in entries
            if item.transaction_id == txid and item.milestone_id == milestone_id and item.run_id == proof.run_id
        ), None)
        if entry is None:
            code = PROOF_EVIDENCE_MISMATCH if malformed_ledger else TRANSACTION_MISMATCH
            decision.reasons.append(_reason(code, "proof transaction is not in the commit ledger", transaction_id=txid))
            return decision
        failure = failures[id(entry)]
        if failure is not None:
            decision.reasons.append(failure)
            return decision
        own_entries.append(entry)

    mismatches = []
    for entry in own_entries:
        for operation in entry.operations:
            latest = owner.get(operation.path)
            if latest is None or latest[0] is not entry:
                continue  # a later verified commit took this path over
            mismatch = _output_mismatch(workspace, operation)
            if mismatch is not None:
                mismatches.append(dict(mismatch, transaction_id=entry.transaction_id))
    if mismatches:
        decision.status = FingerprintStatus.CHANGED
        decision.reasons.extend(mismatches)
    else:
        decision.status = FingerprintStatus.MATCH
    return decision


# ---------------------------------------------------------------- retention

def milestone_ledger_run_references(workspace_path: str) -> List[str]:
    """Run ids the milestone sidecars' commit ledgers reference (retention
    protects them, or every completed milestone would silently become
    UNVERIFIED once its record aged out). An unreadable sidecar's proofs are
    unusable anyway, so it contributes nothing."""
    directory = os.path.join(workspace_path, MILESTONE_SIDECAR_RELATIVE_DIR)
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
    except (FileNotFoundError, NotADirectoryError):
        return []
    references: Set[str] = set()
    for name in names:
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            for raw in payload.get("commit_ledger", []) or []:
                if isinstance(raw, dict) and isinstance(raw.get("run_id"), str):
                    references.add(raw["run_id"])
        except Exception:
            continue
    return sorted(references)
