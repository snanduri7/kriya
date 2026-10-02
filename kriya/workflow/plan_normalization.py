"""Deterministic Planner-output normalization (Planner Reliability R1, PR-1).

A mutable candidate file normally has one owning work unit in a plan. The
Planner often splits one change by symbol - "fix one()" and "fix only()",
both in more.py, neither depending on the other (live: more-itertools; the
validator's AMBIGUOUS_PLANNED_FILE_OWNERSHIP repair never converged, the
Planner returned the identical plan three times and once more with an
explicit merge instruction). Two unordered implementation units writing the
same file would race; their objectives are one change to one file.

``coalesce_same_file_owners`` merges such units into one owner when that is
safe, and leaves the plan unchanged otherwise (validate_plan then reports the
typed ownership conflict and the bounded repair runs, exactly as before):

- only MODEL implementation units count - a verification unit owns no file;
- units already ordered (one transitively depends on the other) are a
  sequential ownership chain the validator accepts - never merged;
- merging two unordered units cannot create a cycle (a dependency of one
  that reached the other would already order them);
- nothing is dropped: descriptions, planned files, requirement /
  acceptance / invariant ids, verification, provides/requires, ownership
  justifications and every reference to the absorbed id (depends_on,
  integration relationships) are carried over.

Pure: no I/O, no model. The returned records name what was merged and why.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, PlannedFile, Subtask

COALESCED_FILE_OWNERS = "PLAN_FILE_OWNERS_COALESCED"


def _mutates(subtask: Subtask) -> bool:
    """A MODEL unit that declares files (a verification unit never can: the
    schema rejects planned_files on execution_role=verification)."""
    return subtask.execution_method == ExecutionMethod.MODEL and bool(subtask.planned_files)


def _transitive(subtasks: Sequence[Subtask]) -> Dict[str, Set[str]]:
    """id -> every id it depends on, transitively (cycle-safe)."""
    direct = {st.id: set(st.depends_on) for st in subtasks}
    closure: Dict[str, Set[str]] = {}

    def resolve(sid: str, trail: Set[str]) -> Set[str]:
        if sid in closure:
            return closure[sid]
        if sid in trail:
            return set()
        found: Set[str] = set()
        for dep in direct.get(sid, ()):
            found.add(dep)
            found |= resolve(dep, trail | {sid})
        closure[sid] = found
        return found

    for sid in direct:
        resolve(sid, set())
    return closure


def _union(*lists: Sequence[Any]) -> List[Any]:
    out: List[Any] = []
    for values in lists:
        for value in values:
            if value not in out:
                out.append(value)
    return out


def _merge_files(first: Sequence[PlannedFile], second: Sequence[PlannedFile]) -> List[PlannedFile]:
    merged: Dict[str, PlannedFile] = {pf.path: pf for pf in first}
    for pf in second:
        if pf.path not in merged:
            merged[pf.path] = pf
            continue
        kept = merged[pf.path]
        merged[pf.path] = kept.model_copy(update={
            "reason": "; ".join(r for r in _union([kept.reason], [pf.reason]) if r),
            "environment_requirements": _union(kept.environment_requirements, pf.environment_requirements),
            "requires_capabilities": _union(kept.requires_capabilities, pf.requires_capabilities),
            "preserved_references": _union(kept.preserved_references, pf.preserved_references),
        })
    return list(merged.values())


def _merge(keep: Subtask, absorb: Subtask) -> Subtask:
    provides = _union(keep.provides, absorb.provides)
    return keep.model_copy(update={
        "description": f"{keep.description}\n{absorb.description}",
        "depends_on": [d for d in _union(keep.depends_on, absorb.depends_on) if d not in (keep.id, absorb.id)],
        "planned_files": _merge_files(keep.planned_files, absorb.planned_files),
        "acceptance_criteria_ids": _union(keep.acceptance_criteria_ids, absorb.acceptance_criteria_ids),
        "verification": _union(keep.verification, absorb.verification),
        "provides": provides,
        "requires": [r for r in _union(keep.requires, absorb.requires) if r not in provides],
        "relevant_global_invariant_ids": _union(keep.relevant_global_invariant_ids,
                                                absorb.relevant_global_invariant_ids),
        "requirement_ids": _union(keep.requirement_ids, absorb.requirement_ids),
        "ownership_justification": {**absorb.ownership_justification, **keep.ownership_justification},
        "mutation_targets": keep.mutation_targets + [t for t in absorb.mutation_targets
                                                     if t.target_id not in {k.target_id for k in keep.mutation_targets}],
    })


def _mergeable_pair(plan: EngineeringPlan) -> Optional[Tuple[str, str, str]]:
    """(path, keep id, absorb id) for the first file owned by two unordered
    mutation units whose merge cannot create a cycle, or None."""
    closure = _transitive(plan.subtasks)
    owners: Dict[str, List[str]] = {}
    for st in plan.subtasks:
        if _mutates(st):
            for pf in st.planned_files:
                owners.setdefault(pf.path, []).append(st.id)
    for path, ids in owners.items():
        for i, first in enumerate(ids):
            for second in ids[i + 1:]:
                if first in closure.get(second, ()) or second in closure.get(first, ()):
                    continue  # a sequential ownership chain: allowed, never merged
                # Unordered units cannot form a cycle when merged: a dependency
                # of one that reaches the other would make them ordered.
                return path, first, second
    return None


def coalesce_same_file_owners(plan: EngineeringPlan) -> Tuple[EngineeringPlan, List[Dict[str, Any]]]:
    """``plan`` with unordered same-file mutation units merged into one
    owner where safe, and one record per merge (empty: unchanged plan)."""
    records: List[Dict[str, Any]] = []
    while True:
        pair = _mergeable_pair(plan)
        if pair is None:
            return plan, records
        path, keep_id, absorb_id = pair
        keep, absorb = plan.subtask_by_id(keep_id), plan.subtask_by_id(absorb_id)
        merged = _merge(keep, absorb)
        subtasks = []
        for st in plan.subtasks:
            if st.id == absorb_id:
                continue
            if st.id == keep_id:
                subtasks.append(merged)
                continue
            if absorb_id in st.depends_on:
                st = st.model_copy(update={"depends_on": _union(
                    [keep_id if d == absorb_id else d for d in st.depends_on])})
            subtasks.append(st)
        relationships = [rel.model_copy(update={
            "producer_subtask_ids": _union([keep_id if s == absorb_id else s for s in rel.producer_subtask_ids]),
            "consumer_subtask_ids": _union([keep_id if s == absorb_id else s for s in rel.consumer_subtask_ids]),
        }) for rel in plan.integration_relationships]
        plan = EngineeringPlan.model_validate(plan.model_copy(update={
            "subtasks": subtasks, "integration_relationships": relationships}).model_dump(mode="json"))
        records.append({"reason_code": COALESCED_FILE_OWNERS, "path": path, "kept": keep_id, "absorbed": absorb_id})
