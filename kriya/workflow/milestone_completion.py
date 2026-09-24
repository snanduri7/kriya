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

from kriya.control.persistence import load_run_record, scan_run_records
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
# The spec's SHARED_WRITE_INVALIDATION (kept under its S4b name).
SHARED_PATH_WITH_RERUN = "SHARED_PATH_WITH_RERUN"
# PRD-008 S4c
COMMIT_LINEAGE_UNVERIFIED = "COMMIT_LINEAGE_UNVERIFIED"
VERIFIED_NO_CHANGE_INVALIDATED = "VERIFIED_NO_CHANGE_INVALIDATED"
COMPLETION_RECONSTRUCTED = "COMPLETION_RECONSTRUCTED"
COMPLETION_RECONSTRUCTION_UNVERIFIED = "COMPLETION_RECONSTRUCTION_UNVERIFIED"
CHECKPOINT_IDENTITY_MISMATCH = "CHECKPOINT_IDENTITY_MISMATCH"
NO_COMPATIBLE_MILESTONE_CHECKPOINT = "NO_COMPATIBLE_MILESTONE_CHECKPOINT"
MILESTONE_CHECKPOINT_SELECTED = "MILESTONE_CHECKPOINT_SELECTED"
COMMIT_SETTLED_BY_RECOVERY = "COMMIT_SETTLED_BY_RECOVERY"

# Completion kinds. A VERIFIED_NO_CHANGE completion committed nothing and is
# proven only by deterministic gates that actually ran and passed.
COMMITTED_CHANGE = "COMMITTED_CHANGE"
VERIFIED_NO_CHANGE = "VERIFIED_NO_CHANGE"
# Gates whose pass says something about behaviour. Compile alone never
# proves a milestone needed no change.
BEHAVIOURAL_GATES = frozenset({"test", "targeted_test", "regression_test", "run_verification"})

LOOP_PASSED = "passed"
LOOP_FAILED = "failed"

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
    # S4c: workspace content (git index hash, .kriya excluded) right after
    # this unit's commits - a VERIFIED_NO_CHANGE proof's lineage anchor.
    workspace_content_hash_after: Optional[str] = None
    # S4c: how the milestone loop that made the commit ended: "passed",
    # "failed", or None (integration / unknown). A failed loop's commits are
    # history, never a completion to reconstruct.
    loop_outcome: Optional[str] = None

    @property
    def paths(self) -> Set[str]:
        return {operation.path for operation in self.operations}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id, "run_id": self.run_id,
            "transaction_id": self.transaction_id, "candidate_hash": self.candidate_hash,
            "operations": [operation.to_dict() for operation in self.operations],
            "recorded_at": self.recorded_at, "evidence_error": self.evidence_error,
            "workspace_content_hash_after": self.workspace_content_hash_after,
            "loop_outcome": self.loop_outcome,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MilestoneCommitEntry":
        return cls(
            milestone_id=data.get("milestone_id"), run_id=str(data["run_id"]),
            transaction_id=str(data["transaction_id"]), candidate_hash=data.get("candidate_hash"),
            operations=tuple(CommittedOperation.from_dict(item) for item in data["operations"]),
            recorded_at=str(data.get("recorded_at", "")), evidence_error=data.get("evidence_error"),
            workspace_content_hash_after=data.get("workspace_content_hash_after"),
            loop_outcome=data.get("loop_outcome"),
        )


def milestone_definition_digest(milestone: Any) -> str:
    """The milestone's own definition (goal, criterion, depends_on,
    provides, consumes, ...) - the milestone counterpart of direct resume's
    goal fingerprint. Deliberately not the rendered goal text, which embeds
    position/total and would change whenever another milestone is added."""
    payload = milestone.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def milestone_work_unit(group_id: str, milestone: Any) -> Dict[str, Any]:
    """The durable identity of one milestone's execution (RunRecord
    active_work_unit -> each commit cycle and checkpoint)."""
    return {
        "kind": "milestone", "group_id": group_id, "milestone_id": milestone.id,
        "definition_digest": milestone_definition_digest(milestone),
    }


def integration_work_unit(group_id: str, plan_digest: str) -> Dict[str, Any]:
    return {"kind": "integration", "group_id": group_id, "milestone_id": None, "definition_digest": plan_digest}


def _same_unit(candidate: Any, unit: Mapping[str, Any]) -> bool:
    return isinstance(candidate, dict) and all(candidate.get(key) == unit.get(key) for key in (
        "kind", "group_id", "milestone_id", "definition_digest",
    ))


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
    kind: str = COMMITTED_CHANGE
    # VERIFIED_NO_CHANGE only: what the no-change verdict is bound to
    # (gate evidence, verification policy, upstream proofs, workspace
    # content and its position in the commit ledger).
    verification: Optional[Dict[str, Any]] = None
    # Set when the proof was rebuilt from durable evidence after a crash.
    reconstructed_from: Optional[Dict[str, Any]] = None
    schema_version: int = MILESTONE_COMPLETION_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version, "milestone_id": self.milestone_id,
            "run_id": self.run_id, "transaction_ids": list(self.transaction_ids),
            "completed_at": self.completed_at, "definition_digest": self.definition_digest,
            "kind": self.kind, "verification": self.verification,
            "reconstructed_from": self.reconstructed_from,
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
            kind=str(data.get("kind", COMMITTED_CHANGE)),
            verification=data.get("verification"),
            reconstructed_from=data.get("reconstructed_from"),
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


def workspace_content_hash(workspace_path: str) -> Optional[str]:
    """Exact workspace content (git index hash of every non-ignored file,
    .kriya excluded), independent of HEAD; None outside git."""
    from kriya.workflow.checkpoint import compute_workspace_content_hash

    try:
        return compute_workspace_content_hash(workspace_path)
    except Exception:
        return None


def entries_from_cycles(
    workspace_path: str, milestone_id: Optional[str], run_id: str, cycles: Iterable[Mapping[str, Any]],
    *, loop_outcome: Optional[str], content_hash: Optional[str],
) -> List[MilestoneCommitEntry]:
    """Ledger entries for COMMITTED cycles, derived from the RunRecord and
    commit evidence (never from a workflow's reported file list)."""
    workspace = canonical_workspace(workspace_path)
    entries = []
    for cycle in cycles:
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
            workspace_content_hash_after=content_hash, loop_outcome=loop_outcome,
        ))
    return entries


def record_milestone_commits(
    workspace_path: str, milestone_id: Optional[str], cycles_before: int,
    *, loop_outcome: Optional[str] = None,
) -> List[MilestoneCommitEntry]:
    """Ledger entries for the owning run's COMMITTED cycles since
    ``cycles_before``."""
    owned = owning_run_commits(workspace_path)
    if owned is None:
        return []
    run_id, cycles = owned
    new_cycles = cycles[cycles_before:]
    if not any(cycle.get("result") == COMMIT_COMMITTED for cycle in new_cycles):
        return []
    return entries_from_cycles(
        workspace_path, milestone_id, run_id, new_cycles,
        loop_outcome=loop_outcome, content_hash=workspace_content_hash(workspace_path),
    )


def completion_proof_for(
    milestone: Any, entries: Sequence[MilestoneCommitEntry], run_id: Optional[str],
    *, verification: Optional[Dict[str, Any]] = None,
    reconstructed_from: Optional[Dict[str, Any]] = None,
) -> MilestoneCompletionProof:
    """A COMMITTED_CHANGE proof, or - with no commits and deterministic
    ``verification`` (see no_change_verification) - a VERIFIED_NO_CHANGE one."""
    no_change = not entries and verification is not None
    return MilestoneCompletionProof(
        milestone_id=milestone.id,
        run_id=run_id or (entries[0].run_id if entries else ""),
        transaction_ids=tuple(entry.transaction_id for entry in entries),
        completed_at=_now(),
        definition_digest=milestone_definition_digest(milestone),
        kind=VERIFIED_NO_CHANGE if no_change else COMMITTED_CHANGE,
        verification=verification if no_change else None,
        reconstructed_from=reconstructed_from,
    )


def verification_policy_fingerprint(config: Any) -> Optional[str]:
    """PRD-008's verification_policy fingerprint (config-owned part) - the
    same freshness model direct resume uses, not a second one."""
    if config is None:
        return None
    try:
        from kriya.workflow.resume_fingerprints import _digest, split_config_by_owner

        return _digest(split_config_by_owner(config.model_dump())["verification_policy"])
    except Exception:
        return None


def upstream_proof_identity(milestone: Any, proofs: Mapping[str, Any]) -> str:
    """Identity of the completions this milestone was verified on top of."""
    upstream = {
        dependency: (
            {key: proofs[dependency].get(key) for key in ("kind", "transaction_ids", "definition_digest")}
            if isinstance(proofs.get(dependency), dict) else None
        )
        for dependency in sorted(getattr(milestone, "depends_on", []) or [])
    }
    return hashlib.sha256(json.dumps(upstream, sort_keys=True).encode()).hexdigest()


def no_change_verification(
    workspace_path: str, milestone: Any, result: Mapping[str, Any], *,
    run_id: Optional[str], config: Any, proofs: Mapping[str, Any], ledger_length: int,
) -> Optional[Dict[str, Any]]:
    """The deterministic binding for a milestone that committed nothing, or
    None when it cannot be proven. Model output (an empty diff, "no change
    needed", a review verdict) is never evidence: only behavioural quality
    gates that actually executed and passed (the workflow's
    deterministic_gate_evidence, which excludes skipped/unconfirmed gates)."""
    if not result.get("quality_gates_passed"):
        return None
    gates = [
        item for item in (result.get("deterministic_gate_evidence") or [])
        if isinstance(item, dict) and item.get("passed") is True
    ]
    if not any(item.get("type") in BEHAVIOURAL_GATES for item in gates):
        return None
    policy = verification_policy_fingerprint(config)
    content = workspace_content_hash(workspace_path)
    if policy is None or content is None:
        return None
    return {
        "gate_evidence": gates,
        "verification_evidence_ids": [f"run:{run_id}:gate:{item.get('type')}" for item in gates],
        "verification_policy": policy,
        "upstream": upstream_proof_identity(milestone, proofs),
        "workspace_content_hash": content,
        "ledger_position": ledger_length,
        "zero_mutations": True,
        # The milestone driver passes no obligation ledger to its workflow
        # calls, so there is none to bind.
        "obligations": FingerprintStatus.NOT_APPLICABLE.value,
    }


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
    # S4c: per-unit checkpoint selections and completion reconstructions
    # made during this run, in order (machine-readable, like decisions).
    events: List[Dict[str, Any]] = field(default_factory=list)

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
            "events": list(self.events),
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
            events=list(data.get("events", [])),
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


def _lineage_order_failures(
    entries: Sequence[MilestoneCommitEntry], failures: Dict[int, Optional[Dict[str, Any]]],
    records: Mapping[str, Any],
) -> None:
    """The ledger is append-ordered by the driver; prove that order against
    the RunRecords. Within one run, ledger order must follow the cycle order
    in RunRecord.commits. Across runs the workspace lock serializes runs, so
    RunRecord.created_at is the only ordering evidence. An entry out of order
    cannot explain who wrote a path last: COMMIT_LINEAGE_UNVERIFIED."""
    last_index: Dict[str, int] = {}
    last_created: Optional[str] = None
    last_run: Optional[str] = None
    for entry in entries:
        if failures[id(entry)] is not None:
            continue
        record = records.get(entry.run_id)
        index = next(
            (i for i, cycle in enumerate(record.commits) if cycle.get("transaction_id") == entry.transaction_id),
            -1,
        )
        if entry.run_id != last_run and entry.run_id in last_index:
            ordered = False  # a run reappearing after another run's entries
        elif entry.run_id == last_run:
            ordered = index > last_index[entry.run_id]
        else:
            ordered = last_created is None or str(record.created_at) >= last_created
        if not ordered:
            failures[id(entry)] = _reason(
                COMMIT_LINEAGE_UNVERIFIED, "ledger order contradicts the run records' commit order",
                transaction_id=entry.transaction_id,
            )
            continue
        last_index[entry.run_id] = index
        last_created = str(record.created_at)
        last_run = entry.run_id


def assess_completed_milestone_reuse(
    workspace_path: str,
    milestones: Sequence[Any],
    completed_ids: Sequence[str],
    proofs: Mapping[str, Any],
    ledger: Sequence[Any],
    *,
    legacy_state: bool = False,
    verification_policy: Optional[str] = None,
) -> MilestoneReuseAssessment:
    """Decide, for every completed milestone, whether it may be skipped.

    ``legacy_state`` marks a sidecar written before completion proofs
    existed: its completed entries are UNVERIFIED, never grandfathered.
    ``verification_policy`` is the current verification_policy fingerprint
    a VERIFIED_NO_CHANGE proof must still match."""
    workspace = canonical_workspace(workspace_path)
    plan_ids = [milestone.id for milestone in milestones]
    completed = [mid for mid in plan_ids if mid in set(completed_ids)]
    entries, malformed = _parse_ledger(ledger)
    records: Dict[str, Any] = {}
    failures = {id(entry): _verify_entry(workspace, entry, records) for entry in entries}
    _lineage_order_failures(entries, failures, records)

    # The latest VERIFIED writer of each path owns its expected state; a
    # later writer that did not verify is remembered, because an earlier
    # milestone's mismatch on that path is then unprovable, not a user edit.
    owner: Dict[str, Tuple[MilestoneCommitEntry, CommittedOperation]] = {}
    unverified_after: Dict[str, List[Tuple[int, MilestoneCommitEntry]]] = {}
    for position, entry in enumerate(entries):
        for operation in entry.operations:
            if failures[id(entry)] is None:
                owner[operation.path] = (entry, operation)
            else:
                unverified_after.setdefault(operation.path, []).append((position, entry))
    context = _AssessmentContext(
        workspace=workspace, entries=entries, failures=failures, owner=owner,
        unverified_after=unverified_after, legacy_state=legacy_state,
        malformed_ledger=bool(malformed), proofs=proofs, verification_policy=verification_policy,
    )

    by_id = {milestone.id: milestone for milestone in milestones}
    decisions: Dict[str, MilestoneReuseDecision] = {}
    for milestone_id in completed:
        decisions[milestone_id] = _assess_one(context, by_id[milestone_id], proofs.get(milestone_id))

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


@dataclass
class _AssessmentContext:
    workspace: str
    entries: Sequence[MilestoneCommitEntry]
    failures: Mapping[int, Optional[Dict[str, Any]]]
    owner: Mapping[str, Tuple[MilestoneCommitEntry, CommittedOperation]]
    unverified_after: Mapping[str, List[Tuple[int, MilestoneCommitEntry]]]
    legacy_state: bool
    malformed_ledger: bool
    proofs: Mapping[str, Any]
    verification_policy: Optional[str]
    _content_hash: Any = None

    def content_hash(self) -> Optional[str]:
        if self._content_hash is None:
            self._content_hash = (workspace_content_hash(self.workspace),)
        return self._content_hash[0]


def _assess_one(context: _AssessmentContext, milestone: Any, raw_proof: Any) -> MilestoneReuseDecision:
    milestone_id = milestone.id
    decision = MilestoneReuseDecision(milestone_id, FingerprintStatus.UNVERIFIED)
    if raw_proof is None:
        decision.reasons.append(_reason(
            LEGACY_STATE_UNVERIFIED if context.legacy_state else COMPLETION_PROOF_MISSING,
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
    if proof.reconstructed_from:
        decision.reasons.append(_reason(
            COMPLETION_RECONSTRUCTED, "completion rebuilt from durable commit evidence after a crash",
            **{"reconstructed_from": proof.reconstructed_from},
        ))
    if proof.kind == VERIFIED_NO_CHANGE:
        return _assess_no_change(context, milestone, proof, decision)
    if proof.kind != COMMITTED_CHANGE:
        decision.reasons.append(_reason(COMPLETION_PROOF_MISSING, f"unknown completion kind {proof.kind!r}"))
        return decision
    if not proof.transaction_ids:
        decision.reasons.append(_reason(
            NO_COMMITTED_OUTPUT,
            "the milestone committed nothing and no deterministic gate proved no change was needed",
        ))
        return decision
    own_entries = []
    for txid in proof.transaction_ids:
        entry = next((
            item for item in context.entries
            if item.transaction_id == txid and item.milestone_id == milestone_id and item.run_id == proof.run_id
        ), None)
        if entry is None:
            code = PROOF_EVIDENCE_MISMATCH if context.malformed_ledger else TRANSACTION_MISMATCH
            decision.reasons.append(_reason(code, "proof transaction is not in the commit ledger", transaction_id=txid))
            return decision
        failure = context.failures[id(entry)]
        if failure is not None:
            decision.reasons.append(failure)
            return decision
        own_entries.append(entry)

    mismatches, lineage = [], []
    for entry in own_entries:
        position = context.entries.index(entry)
        for operation in entry.operations:
            latest = context.owner.get(operation.path)
            if latest is None or latest[0] is not entry:
                continue  # a later VERIFIED commit took this path over
            mismatch = _output_mismatch(context.workspace, operation)
            if mismatch is None:
                continue
            later_unverified = [
                other for at, other in context.unverified_after.get(operation.path, []) if at > position
            ]
            if later_unverified:
                # The path was overwritten by a commit that cannot be
                # proven; that overwrite can neither explain nor excuse it.
                lineage.append(_reason(
                    COMMIT_LINEAGE_UNVERIFIED,
                    "a later commit to this path could not be verified",
                    path=operation.path, transaction_id=later_unverified[-1].transaction_id,
                ))
            else:
                mismatches.append(dict(mismatch, transaction_id=entry.transaction_id))
    if mismatches:
        decision.status = FingerprintStatus.CHANGED
        decision.reasons.extend(mismatches + lineage)
    elif lineage:
        decision.reasons.extend(lineage)
    else:
        decision.status = FingerprintStatus.MATCH
    return decision


def _assess_no_change(
    context: _AssessmentContext, milestone: Any, proof: MilestoneCompletionProof,
    decision: MilestoneReuseDecision,
) -> MilestoneReuseDecision:
    """A VERIFIED_NO_CHANGE completion stays valid only while everything its
    deterministic verification depended on is unchanged: the verification
    policy, the completions it was verified on top of, and the workspace -
    exactly as its recorded content plus every later VERIFIED ledger commit
    explains it."""
    binding = proof.verification or {}
    invalid = []
    if not binding.get("gate_evidence") or binding.get("zero_mutations") is not True:
        decision.reasons.append(_reason(COMPLETION_PROOF_MISSING, "no-change proof has no gate evidence"))
        return decision
    if binding.get("verification_policy") != context.verification_policy or context.verification_policy is None:
        invalid.append("verification policy changed")
    if binding.get("upstream") != upstream_proof_identity(milestone, context.proofs):
        invalid.append("an upstream completion changed")
    position = binding.get("ledger_position")
    later = list(context.entries[position:]) if isinstance(position, int) else None
    if later is None or len(context.entries) < position:
        decision.reasons.append(_reason(COMMIT_LINEAGE_UNVERIFIED, "no-change proof ledger position is invalid"))
        return decision
    broken = [entry for entry in later if context.failures[id(entry)] is not None]
    if broken:
        decision.reasons.append(_reason(
            COMMIT_LINEAGE_UNVERIFIED, "a commit after the no-change verification could not be verified",
            transaction_id=broken[0].transaction_id,
        ))
        return decision
    expected = later[-1].workspace_content_hash_after if later else binding.get("workspace_content_hash")
    current = context.content_hash()
    if expected is None or current is None:
        decision.reasons.append(_reason(
            COMMIT_LINEAGE_UNVERIFIED, "workspace content identity is unavailable (not a git workspace?)",
        ))
        return decision
    if current != expected:
        invalid.append("workspace content differs from what the verified commit history explains")
    if invalid:
        decision.status = FingerprintStatus.CHANGED
        decision.reasons.append(_reason(VERIFIED_NO_CHANGE_INVALIDATED, "; ".join(invalid)))
    else:
        decision.status = FingerprintStatus.MATCH
    return decision


# ---------------------------------------------------------------- reconstruction

@dataclass
class ReconstructionCandidate:
    """Commits the newest run durably attributed to a milestone that never
    reached completion (a crash between its commit and the sidecar save)."""

    milestone_id: str
    run_id: str
    entries: List[MilestoneCommitEntry]
    new_entries: List[MilestoneCommitEntry]
    failure: Optional[Dict[str, Any]] = None
    # True once every entry is bound to durable COMMITTED evidence: the new
    # entries are then real history for the ledger even if the completion
    # itself is refused (e.g. its output was edited after the crash).
    entries_verified: bool = False

    def event(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "code": COMPLETION_RECONSTRUCTION_UNVERIFIED if self.failure else COMPLETION_RECONSTRUCTED,
            "run_id": self.run_id,
            "transaction_ids": [entry.transaction_id for entry in self.entries],
            "reason": self.failure,
        }


def find_completion_to_reconstruct(
    workspace_path: str, group_id: str, milestone: Any, ledger: Sequence[Any],
) -> Optional[ReconstructionCandidate]:
    """None when there is nothing to reconstruct. Otherwise the candidate,
    with ``failure`` set whenever the evidence does not prove the exact
    committed output (fail closed: the milestone then reruns).

    Only cycles whose RunRecord work_unit names this exact milestone
    (group, id, definition digest) count - never model-reported files,
    plan file lists, sidecar assertions or timestamps alone - and only the
    newest such run's. A loop the driver saw fail is history, not a
    completion."""
    workspace = canonical_workspace(workspace_path)
    unit = milestone_work_unit(group_id, milestone)
    scan = scan_run_records(workspace)
    attributed = [
        (record, index, cycle)
        for record in scan.records
        for index, cycle in enumerate(record.commits)
        if _same_unit(cycle.get("work_unit"), unit) and cycle.get("result") == COMMIT_COMMITTED
    ]
    if not attributed:
        return None
    newest = max((record for record, _, _ in attributed), key=lambda record: str(record.created_at))
    cycles = [(index, cycle) for record, index, cycle in attributed if record.run_id == newest.run_id]
    entries, _ = _parse_ledger(ledger)
    by_txid = {entry.transaction_id: entry for entry in entries}
    if any(
        by_txid.get(cycle.get("transaction_id")) is not None
        and by_txid[cycle.get("transaction_id")].loop_outcome == LOOP_FAILED
        for _, cycle in cycles
    ):
        return None
    existing = [by_txid[c.get("transaction_id")] for _, c in cycles if c.get("transaction_id") in by_txid]
    new_cycles = [cycle for _, cycle in cycles if cycle.get("transaction_id") not in by_txid]
    new_entries = entries_from_cycles(
        workspace, milestone.id, newest.run_id, new_cycles,
        loop_outcome=LOOP_PASSED, content_hash=workspace_content_hash(workspace),
    )
    ordered = sorted(
        existing + new_entries,
        key=lambda entry: next(i for i, c in cycles if c.get("transaction_id") == entry.transaction_id),
    )
    candidate = ReconstructionCandidate(milestone.id, newest.run_id, ordered, new_entries)

    # Order: these commits must be the newest in the sequence's history.
    others = [entry for entry in entries if entry.transaction_id not in {e.transaction_id for e in ordered}]
    first_index = min(index for index, _ in cycles)
    for other in others:
        if other.run_id == newest.run_id:
            other_index = next(
                (i for i, c in enumerate(newest.commits) if c.get("transaction_id") == other.transaction_id), None,
            )
            if other_index is None or other_index > first_index:
                candidate.failure = _reason(
                    COMMIT_LINEAGE_UNVERIFIED, "a later commit follows the milestone's commits",
                    transaction_id=other.transaction_id,
                )
                return candidate
            continue
        other_record = next((r for r in scan.records if r.run_id == other.run_id), None)
        if other_record is None or str(other_record.created_at) > str(newest.created_at):
            candidate.failure = _reason(
                COMMIT_LINEAGE_UNVERIFIED, "a newer run committed after the milestone's commits",
                transaction_id=other.transaction_id,
            )
            return candidate

    records: Dict[str, Any] = {}
    for entry in ordered:
        failure = _verify_entry(workspace, entry, records)
        if failure is not None:
            candidate.failure = failure
            return candidate
    candidate.entries_verified = True
    # A commit the interrupted process never saw finish - settled afterwards
    # by `kriya runs recover` - is history, not a completion: the milestone
    # reruns (conservative; the workflow never returned from that commit).
    for entry in ordered:
        path = _evidence_file(workspace, entry.transaction_id)
        try:
            recovered = path is not None and load_commit_evidence(path).recovery is not None
        except Exception:
            recovered = True
        if recovered:
            candidate.failure = _reason(
                COMMIT_SETTLED_BY_RECOVERY, "the commit was settled by `kriya runs recover`, not by its run",
                transaction_id=entry.transaction_id,
            )
            return candidate
    final: Dict[str, CommittedOperation] = {}
    for entry in ordered:
        for operation in entry.operations:
            final[operation.path] = operation
    for operation in final.values():
        mismatch = _output_mismatch(workspace, operation)
        if mismatch is not None:
            candidate.failure = mismatch
            return candidate
    return candidate


# ---------------------------------------------------------------- checkpoint selection

def select_unit_checkpoint(
    workspace_path: str, unit: Mapping[str, Any], *, resume: bool, resume_id: Optional[str],
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """(resume, resume_id, event) to pass to this unit's workflow call.

    Selection is by identity only and never declares a checkpoint safe: the
    chosen one still goes through validate_resume_against_reality(). The
    driver never passes a bare ``resume=True`` - that would let the workflow
    pick the newest checkpoint in the workspace, whichever unit made it. A
    checkpoint without a work_unit (saved before S4c) is never offered."""
    if not resume and not resume_id:
        return False, None, None
    from kriya.workflow.checkpoint import list_checkpoints

    try:
        checkpoints = list_checkpoints(workspace_path)
    except Exception:
        checkpoints = []
    base = {"milestone_id": unit.get("milestone_id"), "unit_kind": unit.get("kind")}
    if resume_id:
        target = next((item for item in checkpoints if item.get("run_id") == resume_id), None)
        if target is not None and _same_unit(target.get("work_unit"), unit):
            return False, resume_id, dict(base, code=MILESTONE_CHECKPOINT_SELECTED, checkpoint=resume_id, explicit=True)
        return False, None, dict(base, code=CHECKPOINT_IDENTITY_MISMATCH, checkpoint=resume_id, explicit=True)
    compatible = [item for item in checkpoints if _same_unit(item.get("work_unit"), unit)]
    if not compatible:
        return False, None, dict(base, code=NO_COMPATIBLE_MILESTONE_CHECKPOINT, checkpoint=None, explicit=False)
    newest = max(compatible, key=lambda item: item.get("saved_at", 0))
    return False, newest["run_id"], dict(
        base, code=MILESTONE_CHECKPOINT_SELECTED, checkpoint=newest["run_id"], explicit=False,
    )


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
