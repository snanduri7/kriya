"""Per-subtask checkpoint/resume - MA6.7 of the MA6 structured-execution
implementation plan. Extends MA5.9's run-level control-plane checkpoint
(kriya/workflow/checkpoint.py) with one record PER SUBTASK, so a resumed
run can pick back up mid-plan rather than only mid-run. Reuses
checkpoint.py's own hash primitives (compute_tree_hash/compute_base_commit)
rather than duplicating them - this module adds a new persisted shape, not
a new hashing mechanism.

Optional `agent/wip/M3/S1`-style WIP commits are explicitly NOT required in
this first slice - hash checkpoints (this module) are sufficient to
determine resumability; an actual git commit per subtask is a possible
future hardening, not something this module assumes exists.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from kriya.workflow.checkpoint import compute_base_commit, compute_tree_hash
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.workflow_types import SubtaskStatus


@dataclass(frozen=True)
class SubtaskCheckpoint:
    """Section 29's per-subtask checkpoint fields, verbatim. plan_hash and
    context_package_hash are the subtask's OWN inputs at the moment it
    ran (an EngineeringPlan/ContextPackage are both immutable and
    hash-addressed once used - MA6 invariant 9), never recomputed later -
    a stale one is exactly what resolve_subtask_resume_point below is
    meant to catch, not silently paper over."""

    subtask_id: str
    status: SubtaskStatus
    base_commit: Optional[str] = None
    tree_hash: Optional[str] = None
    patch_hash: Optional[str] = None
    verification_hash: Optional[str] = None
    plan_hash: Optional[str] = None
    context_package_hash: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "status": self.status.value,
            "base_commit": self.base_commit,
            "tree_hash": self.tree_hash,
            "patch_hash": self.patch_hash,
            "verification_hash": self.verification_hash,
            "plan_hash": self.plan_hash,
            "context_package_hash": self.context_package_hash,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubtaskCheckpoint":
        return cls(
            subtask_id=data["subtask_id"],
            status=SubtaskStatus(data["status"]),
            base_commit=data.get("base_commit"),
            tree_hash=data.get("tree_hash"),
            patch_hash=data.get("patch_hash"),
            verification_hash=data.get("verification_hash"),
            plan_hash=data.get("plan_hash"),
            context_package_hash=data.get("context_package_hash"),
            timestamp=data.get("timestamp", 0.0),
        )


def record_subtask_checkpoint(
    checkpoint_data: Dict[str, Any], subtask_checkpoint: SubtaskCheckpoint
) -> Dict[str, Any]:
    """Returns a NEW checkpoint data dict (never mutates checkpoint_data in
    place, the same frozen/copy-on-write convention every MA5/MA6 control
    object already follows) with subtask_checkpoint recorded under its own
    subtask_id - re-recording the same subtask_id (e.g. a retried subtask
    that eventually succeeds) OVERWRITES its previous entry rather than
    accumulating stale duplicates. The caller still owns actually calling
    kriya.workflow.checkpoint.save_checkpoint() with the result - this
    function only shapes the dict, it has no filesystem side effects."""
    result = dict(checkpoint_data)
    by_id: Dict[str, Any] = dict(result.get("subtask_checkpoints", {}))
    by_id[subtask_checkpoint.subtask_id] = subtask_checkpoint.to_dict()
    result["subtask_checkpoints"] = by_id
    return result


def load_subtask_checkpoints(checkpoint_data: Dict[str, Any]) -> Dict[str, SubtaskCheckpoint]:
    raw = checkpoint_data.get("subtask_checkpoints", {}) or {}
    return {sid: SubtaskCheckpoint.from_dict(d) for sid, d in raw.items()}


def topological_subtask_order(plan: EngineeringPlan) -> List[str]:
    """Kahn's algorithm, same edge direction as plan_validation._acyclic
    (dependency before dependent) - a resume decision must walk subtasks
    in an order their own depends_on actually permits, not just plan.subtasks'
    raw list position, which plan_validation.py never guarantees is already
    topologically sorted. Assumes the plan already passed
    plan_validation.validate_plan() (acyclic, all depends_on resolve); an
    unresolved/cyclic plan here simply yields a partial order (whatever it
    can topologically place) rather than raising - resolve_subtask_resume_point
    below is the one place that decides what an incomplete order means.
    Ties broken by sorted id, purely for determinism (repeatable output
    across runs), not a meaningful priority."""
    id_set = {st.id for st in plan.subtasks}
    indegree: Dict[str, int] = {st.id: 0 for st in plan.subtasks}
    children: Dict[str, List[str]] = {st.id: [] for st in plan.subtasks}
    for st in plan.subtasks:
        for dep in st.depends_on:
            if dep not in id_set:
                continue
            children[dep].append(st.id)
            indegree[st.id] += 1

    queue = sorted(sid for sid, deg in indegree.items() if deg == 0)
    order: List[str] = []
    while queue:
        queue.sort()
        current = queue.pop(0)
        order.append(current)
        for nxt in children[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return order


class ResumePointStatus(str, Enum):
    RESUME = "resume"
    ALREADY_COMPLETE = "already_complete"
    NEEDS_REVIEW = "needs_review"
    FRESH_START = "fresh_start"


@dataclass(frozen=True)
class SubtaskResumeResult:
    status: ResumePointStatus
    next_subtask_id: Optional[str] = None
    completed_subtask_ids: List[str] = field(default_factory=list)
    mismatches: List[str] = field(default_factory=list)


def resolve_subtask_resume_point(
    plan: EngineeringPlan,
    checkpoint_data: Dict[str, Any],
    workspace_path: str,
) -> SubtaskResumeResult:
    """Section 55's resume flow: validate plan_hash -> validate tree_hash
    -> find the last valid COMPLETED subtask (in dependency order) ->
    return the next incomplete one as the resume point. Any mismatch ->
    NEEDS_REVIEW, never a stale completion marker trusted silently (MA6
    invariant 8). Re-running required local verification for the resumed
    subtask is the CALLER's job (WorkflowController, MA6.8+) - this
    function only determines WHERE to resume and whether the checkpoint is
    even trustworthy enough to resume from at all, the same "pure
    comparison, no side effects" contract
    checkpoint.validate_resume_against_reality() already established for
    run-level resume.

    Only the LAST completed subtask's tree_hash/base_commit is compared
    against the CURRENT workspace state - earlier completed subtasks'
    hashes are historical audit trail (the tree necessarily kept changing
    as later subtasks ran), never expected to still match current state."""
    current_plan_hash = plan.content_hash()
    stored_plan_hash = checkpoint_data.get("plan_hash")
    if stored_plan_hash is not None and stored_plan_hash != current_plan_hash:
        return SubtaskResumeResult(
            status=ResumePointStatus.NEEDS_REVIEW,
            mismatches=[f"plan_hash: checkpoint={stored_plan_hash!r} current={current_plan_hash!r}"],
        )

    order = topological_subtask_order(plan)
    subtask_checkpoints = load_subtask_checkpoints(checkpoint_data)

    if not subtask_checkpoints:
        return SubtaskResumeResult(status=ResumePointStatus.FRESH_START, next_subtask_id=order[0] if order else None)

    completed: List[str] = []
    for subtask_id in order:
        record = subtask_checkpoints.get(subtask_id)
        if record is None or record.status != SubtaskStatus.COMPLETED:
            break
        if record.plan_hash is not None and record.plan_hash != current_plan_hash:
            return SubtaskResumeResult(
                status=ResumePointStatus.NEEDS_REVIEW,
                completed_subtask_ids=completed,
                mismatches=[f"subtask {subtask_id!r} plan_hash does not match the current plan"],
            )
        completed.append(subtask_id)

    if completed:
        last_record = subtask_checkpoints[completed[-1]]
        if last_record.tree_hash is not None:
            current_tree_hash = compute_tree_hash(workspace_path)
            if current_tree_hash != last_record.tree_hash:
                return SubtaskResumeResult(
                    status=ResumePointStatus.NEEDS_REVIEW,
                    completed_subtask_ids=completed,
                    mismatches=[
                        f"workspace tree_hash does not match last completed subtask {completed[-1]!r}: "
                        f"checkpoint={last_record.tree_hash!r} current={current_tree_hash!r}"
                    ],
                )
        if last_record.base_commit is not None:
            current_base_commit = compute_base_commit(workspace_path)
            if current_base_commit != last_record.base_commit:
                return SubtaskResumeResult(
                    status=ResumePointStatus.NEEDS_REVIEW,
                    completed_subtask_ids=completed,
                    mismatches=[
                        f"workspace base_commit does not match last completed subtask {completed[-1]!r}: "
                        f"checkpoint={last_record.base_commit!r} current={current_base_commit!r}"
                    ],
                )

    remaining = [sid for sid in order if sid not in completed]
    if not remaining:
        return SubtaskResumeResult(status=ResumePointStatus.ALREADY_COMPLETE, completed_subtask_ids=completed)

    return SubtaskResumeResult(
        status=ResumePointStatus.RESUME, next_subtask_id=remaining[0], completed_subtask_ids=completed,
    )
