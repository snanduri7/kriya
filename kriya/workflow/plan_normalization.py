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

import posixpath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethodType,
)

COALESCED_FILE_OWNERS = "PLAN_FILE_OWNERS_COALESCED"
VERIFICATION_CONTRACT_DERIVED = "PLAN_VERIFICATION_CONTRACT_DERIVED"
VERIFICATION_SCOPE_NORMALIZED = "PLAN_VERIFICATION_SCOPE_NORMALIZED"
IMMUTABLE_TEST_EDIT_REDIRECTED = "PLAN_IMMUTABLE_TEST_EDIT_REDIRECTED"


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


def derive_verification_contracts(plan: EngineeringPlan) -> Tuple[EngineeringPlan, List[Dict[str, Any]]]:
    """A verification unit that states no semantic contract gets
    ``requires`` = what the units it ``depends_on`` provide.

    Live (spring-framework-petclinic, both Spring tasks; 5 plans in 3 runs):
    a multi-stage plan's terminal verification unit owned no file, depended
    on every implementation unit and declared neither provides nor requires;
    validate_plan's SUBTASK_SEMANTIC_CONTRACT_MISSING repair never converged
    (the identical plan three times). Its depends_on already states what it
    consumes - its dependencies' outputs - so ``requires`` restates the
    Planner's own edges; nothing is invented. Left unchanged (and still
    rejected by the validator) when the unit has no dependency or its
    dependencies provide nothing. Pure; one record per derived contract."""
    provides = {st.id: st.provides for st in plan.subtasks}
    records: List[Dict[str, Any]] = []
    subtasks = []
    for st in plan.subtasks:
        if st.execution_role == ExecutionRole.VERIFICATION and not st.provides and not st.requires:
            derived = _union(*(provides.get(dep, []) for dep in st.depends_on))
            if derived:
                st = st.model_copy(update={"requires": derived})
                records.append({"reason_code": VERIFICATION_CONTRACT_DERIVED, "subtask": st.id,
                                "requires": derived, "from_depends_on": list(st.depends_on)})
        subtasks.append(st)
    if not records:
        return plan, records
    return EngineeringPlan.model_validate(
        plan.model_copy(update={"subtasks": subtasks}).model_dump(mode="json")), records


def _runtime_verifier(method: Any) -> bool:
    return bool(method.requires_application_runtime)


def scope_verification_to_requirements(
    plan: EngineeringPlan, runtime_verification_required: bool,
) -> Tuple[EngineeringPlan, List[Dict[str, Any]]]:
    """Remove application-runtime verification the request does not require
    (PLAN-VERIFICATION-SCOPE-001).

    ``runtime_verification_required`` is Kriya's own deterministic reading of
    the user's request (``acceptance.goal_requires_runtime_behavior``, the same
    value enforce validates the plan and runs each unit with). When it is
    False, a runtime verifier is a verification category the Planner invented:
    executing it makes the unit require runtime evidence nothing asked for
    (live, Spring XML pet types: correct mutation units and a passing suite,
    then a verification unit "the application starts" whose run verifier
    rightly produced no command -> the run failed closed).

    Compile/test verifiers are never touched. Only removals whose correctness
    is obvious are made:

    - a unit keeping another verifier with an evidence producer loses just the
      runtime verifier;
    - a verification-only unit whose verifiers all lack a basis is removed
      when nothing else rests on it: no unit depends on it or consumes what it
      provides, no integration relationship names it, every requirement id it
      cites is cited elsewhere, and every acceptance criterion only it cites
      is a judgment criterion (removed with it - a tool criterion never is).

    Anything else is left unchanged; validate_plan then refuses it
    (PLAN_VERIFICATION_SCOPE_UNJUSTIFIED) and the bounded Planner repair runs.
    Pure; one record per change."""
    if runtime_verification_required:
        return plan, []
    criteria = {ac.id: ac for ac in plan.acceptance_criteria}
    related = {sid for rel in plan.integration_relationships
               for sid in (*rel.producer_subtask_ids, *rel.consumer_subtask_ids)}
    records: List[Dict[str, Any]] = []
    removed_criteria: Set[str] = set()
    subtasks: List[Subtask] = []
    for st in plan.subtasks:
        runtime = [vm for vm in st.verification if _runtime_verifier(vm)]
        if not runtime:
            subtasks.append(st)
            continue
        kept = [vm for vm in st.verification if not _runtime_verifier(vm)]
        if any(vm.has_evidence_producer for vm in kept):
            subtasks.append(st.model_copy(update={"verification": kept}))
            records.append({"reason_code": VERIFICATION_SCOPE_NORMALIZED, "action": "verifier_removed",
                            "subtask": st.id, "removed": [vm.description for vm in runtime]})
            continue
        others = [o for o in plan.subtasks if o.id != st.id]
        exclusive = [cid for cid in st.acceptance_criteria_ids
                     if not any(cid in o.acceptance_criteria_ids for o in others)]
        removable = (
            st.execution_role == ExecutionRole.VERIFICATION and not st.planned_files
            and not any(st.id in o.depends_on for o in others)
            and not set(st.provides) & {r for o in others for r in o.requires}
            and st.id not in related
            and set(st.requirement_ids) <= {r for o in others for r in o.requirement_ids}
            and all(cid in criteria and criteria[cid].method == VerificationMethodType.JUDGMENT for cid in exclusive)
        )
        if not removable:
            subtasks.append(st)
            continue
        removed_criteria.update(exclusive)
        records.append({"reason_code": VERIFICATION_SCOPE_NORMALIZED, "action": "unit_removed", "subtask": st.id,
                        "removed": [vm.description for vm in st.verification],
                        "acceptance_criteria_removed": exclusive})
    if not records:
        return plan, records
    return EngineeringPlan.model_validate(plan.model_copy(update={
        "subtasks": subtasks,
        "acceptance_criteria": [ac for ac in plan.acceptance_criteria if ac.id not in removed_criteria],
    }).model_dump(mode="json")), records


_REDIRECT_MARKER = "REGRESSION"
_JVM_EXTENSIONS = (".java", ".kt", ".groovy", ".scala")


def _sibling_test_name(path: str, attempt: int) -> Optional[str]:
    """A new test file name beside ``path`` that keeps the test-file naming convention the runner collects by, or None
    for a convention this function does not know (the plan is then left to the validator and bounded repair)."""
    directory, name = posixpath.split(path)
    stem, extension = posixpath.splitext(name)
    mark = _REDIRECT_MARKER.capitalize() + (str(attempt) if attempt > 1 else "")
    if extension in _JVM_EXTENSIONS:
        for suffix in ("Tests", "Test", "IT"):
            if stem.endswith(suffix) and len(stem) > len(suffix):
                return posixpath.join(directory, f"{stem[:-len(suffix)]}{mark}{suffix}{extension}")
        return None
    if extension == ".py":
        if stem.startswith("test_") and len(stem) > 5:
            return posixpath.join(directory, f"{stem}_{mark.lower()}{extension}")
        if stem.endswith("_test") and len(stem) > 5:
            return posixpath.join(directory, f"{stem[:-5]}_{mark.lower()}_test{extension}")
        return None
    if extension in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        for infix in (".test", ".spec"):
            if stem.endswith(infix) and len(stem) > len(infix):
                return posixpath.join(directory, f"{stem[:-len(infix)]}.{mark.lower()}{infix}{extension}")
    return None


def redirect_immutable_test_edits(
    plan: EngineeringPlan, immutable_tests: Iterable[str], path_exists: Callable[[str], bool],
) -> Tuple[EngineeringPlan, List[Dict[str, Any]]]:
    """BACKEND-READINESS-V2 (S4_B; also hit by S4_A): a plan that MODIFIES an existing test file the goal froze
    (TEST_IMMUTABILITY) is rewritten to CREATE a new sibling test file instead, which is the one thing the goal allows.

    Live: the Planner scheduled ``DeepScanTest.java`` (modify) for a regression test; the validator's
    PLAN_EDITS_IMMUTABLE_TEST repair named that exact path and said "add new tests in a new test file", twice - the
    Planner re-described the subtask as "create a new test file" and kept the same existing path, then regressed another
    unit (identical violation across all three drafts; nothing was written). The remedy is deterministic, so it is not
    left to the model: the entry keeps its subtask, ordering, ``preserved_references`` and capabilities, takes
    ``action=create`` at a path that exists nowhere (in the workspace or in the plan), and records why in ``reason``.

    Narrow by construction: only MODIFY of a path in ``immutable_tests`` (a DELETE stays a typed validator violation,
    nothing is invented for it); only for a test-naming convention it knows; the rewritten plan is re-validated in full by
    ``validate_plan`` like any other. Pure; one record per redirected entry; an unchanged plan returns ``(plan, [])``."""
    frozen = set(immutable_tests or ())
    if not frozen:
        return plan, []
    taken: Set[str] = {pf.path for st in plan.subtasks for pf in st.planned_files}
    chosen: Dict[str, str] = {}
    records: List[Dict[str, Any]] = []
    subtasks = []
    for st in plan.subtasks:
        files = []
        for pf in st.planned_files:
            if pf.path in frozen and pf.action == FileAction.MODIFY:
                target = chosen.get(pf.path)
                if target is None:
                    for attempt in range(1, 20):
                        candidate = _sibling_test_name(pf.path, attempt)
                        if candidate is None:
                            break
                        if candidate not in taken and not path_exists(candidate):
                            target = candidate
                            break
                if target is not None:
                    original = pf.path
                    chosen[original] = target
                    taken.add(target)
                    note = (f"Kriya: {pf.path} is an existing test the goal requires to stay unchanged; the new test "
                            f"goes in this new file instead.")
                    pf = pf.model_copy(update={"path": target, "action": FileAction.CREATE,
                                               "reason": f"{pf.reason} {note}".strip()})
                    records.append({"reason_code": IMMUTABLE_TEST_EDIT_REDIRECTED, "subtask": st.id,
                                    "from": original, "to": target})
            files.append(pf)
        subtasks.append(st.model_copy(update={"planned_files": files}))
    if not records:
        return plan, []
    return EngineeringPlan.model_validate(plan.model_copy(update={"subtasks": subtasks}).model_dump(mode="json")), records
