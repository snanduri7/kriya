"""Deterministic PlanValidator - MA6.2 of the MA6 structured-execution
implementation plan. The one place an EngineeringPlan (kriya/workflow/
plan_schema.py, MA6.1) goes from "syntactically well-formed" to
"execution-authorized." Never delegates structural validity to an LLM/
Reviewer (MA6 invariant 1) - every check here is a deterministic graph/
filesystem/registry lookup, same spirit as kriya/workflow/triage.py's own
"never asked of a model" ImpactVector components.

canonicalize_planned_file_actions() (PRV-05 run #10, 2026-08-28) takes the
same "never ask a model for what Kriya can derive" principle one step
further: called by the caller BEFORE validate_plan(), it returns a corrected
copy of the plan with each planned file's create/modify action normalized
against real repository state, rather than rejecting the plan and spending
a bounded repair attempt asking the Planner to reproduce metadata that is
fully mechanical.

validate_plan() checks, in order: unique subtask ids, all depends_on
references resolve, the dependency graph is acyclic, every planned file
either already exists or is explicitly marked `action=create`, every
acceptance criterion is covered by at least one subtask (and every
subtask's acceptance_criteria_ids resolve to a real criterion),
extension_points are present for enhancement/milestone plans (exempted
for a genuinely empty workspace - MA7.8 fix, nothing established yet
means no real insertion point could exist for any plan to name; ALSO
exempted when the caller passes resuming_own_established_progress=True -
MA5.9/subtask-resume fix, 2026-08-24 - established content that is
Kriya's OWN prior subtask output for the SAME resumed goal is not the
foreign pre-existing work this rule exists to protect, and requiring a
justification the fresh re-plan has no way to know it needs previously
sent a resumed run down the legacy whole-goal fallback, which regenerated
and broke an already-working, already-completed file),
refactor_baseline is set for refactor plans, every TOOL-tagged
subtask/verification/acceptance-criterion tool_name resolves to a real
registered tool, every planned file lands inside the supplied context
package (when one is supplied), and - reusing kriya.workflow.triage's own
MA2.4 `EngineeringTriageService.recompute_from_files` machinery rather than
inventing new heuristics - the plan's real touched-file set is used to
recompute risk, which can only ESCALATE the caller's EngineeringRoute
(MA6 invariant 5), never silently continue under a stale, lighter profile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import (
    BUILTIN_QUALITY_GATE_VERIFIERS,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    Subtask,
    VerificationMethodType,
)
from kriya.workflow.triage import ChangeKind, EngineeringRoute, EngineeringTriageService, _workspace_appears_empty

import os


@dataclass(frozen=True)
class PlanValidationResult:
    """valid=False means the plan is NOT execution-authorized - a caller
    (WorkflowController, MA6.8) must never hand a Subtask from a failed
    validation to SubtaskExecutor (MA6.5). escalated_route is only set when
    both `route` and `triage_service` were supplied to validate_plan() -
    it is the caller's job to actually adopt it (e.g. store it back onto
    ControlState), validate_plan() never mutates anything itself."""

    valid: bool
    errors: List[str] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)
    escalated_route: Optional[EngineeringRoute] = None


def _acyclic(subtasks: List[Subtask]) -> bool:
    """Kahn's algorithm over the depends_on graph (edge: dependency -> its
    dependent). Unknown depends_on ids are skipped here on purpose - a
    dangling reference is already reported as its own error by
    validate_plan(); this function only answers "is the graph of KNOWN
    edges acyclic," so one bad reference doesn't also mask an unrelated
    real cycle (or vice versa) behind a single conflated error."""
    id_set = {st.id for st in subtasks}
    indegree: Dict[str, int] = {st.id: 0 for st in subtasks}
    children: Dict[str, List[str]] = {st.id: [] for st in subtasks}
    for st in subtasks:
        for dep in st.depends_on:
            if dep not in id_set:
                continue
            children[dep].append(st.id)
            indegree[st.id] += 1

    queue = [sid for sid, deg in indegree.items() if deg == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for nxt in children[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return visited == len(subtasks)


def _transitive_dependencies(subtasks: List[Subtask]) -> Dict[str, "set[str]"]:
    """id -> the set of every OTHER subtask id it depends on, directly or
    transitively (its own id never included). Assumes the graph is already
    known-acyclic (callers check that separately) - a cycle would recurse
    forever here. Memoized per call since real plans reuse the same
    upstream subtask across many downstream dependents."""
    by_id = {st.id: st for st in subtasks}
    memo: Dict[str, "set[str]"] = {}

    def resolve(subtask_id: str) -> "set[str]":
        if subtask_id in memo:
            return memo[subtask_id]
        memo[subtask_id] = set()  # cycle guard - real cycles are rejected elsewhere
        result: "set[str]" = set()
        for dep in by_id[subtask_id].depends_on if subtask_id in by_id else []:
            if dep not in by_id:
                continue
            result.add(dep)
            result |= resolve(dep)
        memo[subtask_id] = result
        return result

    return {st.id: resolve(st.id) for st in subtasks}


def _forms_sequential_ownership_chain(
    owner_ids: List[str], transitive_deps: Dict[str, "set[str]"],
) -> bool:
    """True when every pair of co-owners is dependency-ORDERED - one
    transitively depends on the other, in either direction - so the whole
    set forms a single, unambiguous evolutionary sequence for that file.
    Found live, PRV-05 (2026-08-28 rerun): a real dependency-migration plan
    legitimately had TWO strictly sequential stages both declare
    JsonService.java (an early "identify usages" stage, then a later
    "migrate to the new API" stage depending on it through the chain in
    between) - genuinely safe, since the later stage can only ever run
    after the earlier one's output already exists, with no ordering
    ambiguity about which edit happens first. Two co-owners with NO
    dependency relationship between them (parallel, or in unrelated
    branches) are NOT covered by this - that shape is the real "which one
    wins" ambiguity this whole check exists to catch, unchanged."""
    for i in range(len(owner_ids)):
        for j in range(i + 1, len(owner_ids)):
            a, b = owner_ids[i], owner_ids[j]
            if b not in transitive_deps.get(a, set()) and a not in transitive_deps.get(b, set()):
                return False
    return True


def canonicalize_planned_file_actions(
    plan: EngineeringPlan, workspace_path: str,
) -> "tuple[EngineeringPlan, List[str]]":
    """Deterministically corrects each planned file's create/modify action
    against real repository state, returning a corrected copy of `plan`
    (the input is never mutated - see below) to hand to validate_plan(),
    rather than rejecting a wrong action and asking the Planner to
    reproduce baseline-existence metadata Kriya can look up exactly itself.

    Found live, PRV-05 run #10 (2026-08-28, Hardened): the local planner
    model declared action=modify for a test file that did not yet exist,
    and reproduced the byte-identical wrong value across two full repair
    rounds despite build_structured_plan_repair_prompt's PLANNED_FILE_ACTION_
    MISMATCH guidance naming the exact subtask, path, and required fix each
    time - confirmed not a baseline-authority disagreement (the repository_
    evidence handed to the planner was consistent and correct throughout,
    never listing the file) and not a shifting-obligation issue (the same
    single obligation failed identically all three revisions), so spending
    bounded repair attempts asking the model to get this one field right is
    pointless - it is fully determined by os.path.exists() and needs no
    model judgment at all.

    action=delete is deliberately left untouched - it is the Planner's own
    declared intent to remove something, never silently invented by this
    function - so a delete of a path that does not exist stays a real,
    unfixable validation error requiring a genuine repair, not silent
    normalization into a plan the Planner never actually asked for.

    Returns a deep copy rather than mutating `plan` in place: in real
    production use build_engineering_plan_from_planner_output() always
    hands back a freshly-parsed object, so this distinction is invisible -
    but a caller (or test double) that reuses one EngineeringPlan instance
    across multiple calls must not have an earlier call's baseline-derived
    correction silently leak into a later one made against different repo
    state."""
    corrected_plan = plan.model_copy(deep=True)
    corrections: List[str] = []
    for st in corrected_plan.subtasks:
        for pf in st.planned_files:
            if pf.action == FileAction.DELETE:
                continue
            exists = os.path.exists(os.path.join(workspace_path, pf.path))
            correct_action = FileAction.MODIFY if exists else FileAction.CREATE
            if pf.action != correct_action:
                corrections.append(
                    f"subtask {st.id!r} planned file {pf.path!r}: normalized action "
                    f"{pf.action.value!r} -> {correct_action.value!r} (baseline_exists={exists})"
                )
                pf.action = correct_action
    return corrected_plan, corrections


async def validate_plan(
    plan: EngineeringPlan,
    *,
    workspace_path: str,
    available_tool_names: Optional[Iterable[str]] = None,
    route: Optional[EngineeringRoute] = None,
    triage_service: Optional[EngineeringTriageService] = None,
    context_files: Optional[Iterable[str]] = None,
    resuming_own_established_progress: bool = False,
    require_model_planned_files: bool = False,
    require_semantic_contracts: bool = False,
    obligation_ledger: Optional[ObligationLedger] = None,
    revision: object = None,
) -> PlanValidationResult:
    """available_tool_names=None SKIPS the tool-registry check entirely -
    only safe for contexts guaranteed not to contain TOOL-tagged subtasks
    (e.g. a schema-only unit test). Every real caller (WorkflowController,
    MA6.8) must pass the kernel's real registered tool names
    (`kernel.registry.list_components("tool")`) so MA6 invariant 3's
    "an unregistered tool_name must be REJECTED at validation time" is
    actually enforced, not silently bypassed by omission.

    context_files=None similarly skips the "planned files match context"
    check - only meaningful once a caller has an actual ContextPackage
    (MA6.4) to compare against.

    route+triage_service must both be supplied together to get risk
    recomputation; either alone is treated as "recomputation not
    requested" (no error - a caller validating a plan in isolation, before
    a route even exists, is a legitimate use).

    resuming_own_established_progress=True (default False) tells the
    extension_points check that whatever real content the workspace
    already has was produced by an EARLIER, genuinely-completed subtask of
    the SAME plan being resumed (kriya/workflow/workflow_controller.py's
    subtask-spanning resume, 2026-08-24) - not pre-existing, foreign work a
    plan needs to justify extending. The caller is responsible for that
    judgment (a persisted ControlState showing at least one completed
    subtask); this function only trusts the flag it's given.

    obligation_ledger/revision (PRV-05 run #8, MA8 - kriya/workflow/
    obligations.py): purely an optional side effect, added without
    changing any check's own pass/fail logic - when supplied, four
    specific PLAN_STRUCTURAL_VALIDITY constraints (the ones demonstrated
    live in run #8: refactor_baseline non-blank, planned-file action vs
    disk existence, planned-file ownership, and MODEL-subtask scope) are
    recorded as DETERMINISTIC ObligationRecords with stable, field-derived
    ids (never derived from the error text itself), SATISFIED or VIOLATED,
    tagged with `revision` (the caller's own repair-attempt counter). This
    lets the caller's repair loop detect a regression - a constraint that
    was SATISFIED on a prior call and comes back VIOLATED on this one -
    and tell the next repair prompt to preserve it, not just fix whatever
    is currently broken."""
    errors: List[str] = []
    reason_codes: List[str] = []

    if require_semantic_contracts and not plan.global_invariants:
        errors.append("authoritative multi-stage planning requires non-empty global_invariants")
        reason_codes.append("PLAN_GLOBAL_INVARIANTS_MISSING")
    if require_semantic_contracts and len(plan.subtasks) > 1:
        missing_contracts = [
            st.id for st in plan.subtasks if not st.provides and not st.requires
        ]
        if missing_contracts:
            errors.append(
                f"subtask(s) {missing_contracts!r} declare neither provides nor requires; "
                "authoritative multi-stage plans require explicit semantic contracts"
            )
            reason_codes.append("SUBTASK_SEMANTIC_CONTRACT_MISSING")
        missing_invariant_projection = [
            st.id for st in plan.subtasks if not st.relevant_global_invariants
        ]
        if missing_invariant_projection:
            errors.append(
                f"subtask(s) {missing_invariant_projection!r} receive no relevant_global_invariants"
            )
            reason_codes.append("SUBTASK_GLOBAL_INVARIANTS_MISSING")

    ids = [st.id for st in plan.subtasks]
    duplicate_ids = sorted({sid for sid in ids if ids.count(sid) > 1})
    if duplicate_ids:
        errors.append(f"duplicate subtask ids: {duplicate_ids}")
    id_set = set(ids)

    for st in plan.subtasks:
        for dep in st.depends_on:
            if dep not in id_set:
                errors.append(f"subtask {st.id!r} depends_on unknown subtask id {dep!r}")

    graph_is_acyclic = _acyclic(plan.subtasks)
    if not graph_is_acyclic:
        errors.append("subtask dependency graph contains a cycle")

    file_owners: Dict[str, List[str]] = {}
    capability_providers: Dict[str, List[str]] = {}
    for st in plan.subtasks:
        for planned_file in st.planned_files:
            file_owners.setdefault(planned_file.path, []).append(st.id)
        for capability in st.provides:
            capability_providers.setdefault(capability, []).append(st.id)
    # A file with 2+ owners is ambiguous UNLESS every pair of co-owners is
    # dependency-ORDERED (one transitively depends on the other) - a
    # genuinely sequential evolutionary ownership (e.g. a dependency-
    # migration plan's "identify usages" stage and its later "migrate to
    # the new API" stage both legitimately touching the same file, in a
    # fixed, unambiguous order) is not the same hazard as two independent/
    # parallel subtasks racing to write the same path. Found live, PRV-05
    # (2026-08-28 rerun) - see _forms_sequential_ownership_chain's own
    # docstring for the exact incident. Skipped entirely when the graph is
    # already known-cyclic (that's its own separate, more fundamental
    # error - a "sequential" judgment is meaningless without a real order).
    transitive_deps = _transitive_dependencies(plan.subtasks) if graph_is_acyclic else {}
    ambiguous_files = {
        path: owners for path, owners in file_owners.items()
        if len(owners) != 1 and not (transitive_deps and _forms_sequential_ownership_chain(owners, transitive_deps))
    }
    if ambiguous_files:
        errors.append(f"planned file ownership must be unique: {ambiguous_files}")
        reason_codes.append("AMBIGUOUS_PLANNED_FILE_OWNERSHIP")
    if obligation_ledger is not None:
        for path, owners in file_owners.items():
            obligation_ledger.record(ObligationRecord(
                id=f"plan.file.{path}.ownership", kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY,
                status=(
                    ObligationStatus.VIOLATED if path in ambiguous_files else ObligationStatus.SATISFIED
                ),
                authority=ObligationAuthority.DETERMINISTIC,
                description="planned file path must be owned by exactly one subtask, or a "
                            "dependency-ordered sequential chain of subtasks",
                source="plan_validation.validate_plan", revision=revision,
                evidence={"path": path, "owners": list(owners)},
                owner_subtask_id=owners[0] if len(owners) == 1 else None,
                terminal_required=True,
            ))
    ambiguous_capabilities = {
        capability: providers
        for capability, providers in capability_providers.items()
        if len(providers) != 1
    }
    if ambiguous_capabilities:
        errors.append(f"semantic capabilities must have exactly one provider: {ambiguous_capabilities}")
        reason_codes.append("AMBIGUOUS_SUBTASK_CAPABILITY_PROVIDER")

    invariant_set = set(plan.global_invariants)
    for st in plan.subtasks:
        unknown_invariants = sorted(set(st.relevant_global_invariants) - invariant_set)
        if unknown_invariants:
            errors.append(
                f"subtask {st.id!r} references unknown global invariant(s): {unknown_invariants}"
            )
            reason_codes.append("UNKNOWN_GLOBAL_INVARIANT")
        for requirement in st.requires:
            providers = capability_providers.get(requirement, [])
            if not providers:
                errors.append(
                    f"subtask {st.id!r} requires {requirement!r} but no subtask provides it"
                )
                reason_codes.append("SUBTASK_REQUIREMENT_UNPROVIDED")
            elif len(providers) == 1 and providers[0] not in st.depends_on:
                errors.append(
                    f"subtask {st.id!r} requires {requirement!r} from {providers[0]!r} "
                    "but does not declare that provider in depends_on"
                )
                reason_codes.append("SEMANTIC_DEPENDENCY_EDGE_MISSING")

    for st in plan.subtasks:
        # execution_role=verification is EXEMPT here, not weakened: Subtask's
        # own model_validator (plan_schema.py) already requires a
        # verification-role subtask to have zero planned_files AND at least
        # one concrete verifier - this rule exists to catch an
        # IMPLEMENTATION-role model subtask with no modification scope
        # (a genuinely unbounded write), a different failure than "this
        # subtask is intentionally non-mutating." Found live, PRV-05
        # (2026-08-28): a real, needed regression-verification subtask (zero
        # files by design, a populated `verification` list) had no legal
        # encoding before execution_role existed - see ExecutionRole's own
        # docstring for the full incident.
        model_subtask_unscoped = (
            st.execution_method == ExecutionMethod.MODEL
            and st.execution_role != ExecutionRole.VERIFICATION
            and not st.planned_files
        )
        if require_model_planned_files and model_subtask_unscoped:
            errors.append(
                f"subtask {st.id!r} uses execution_method=model but declares no planned_files; "
                "authoritative execution requires a non-empty modification scope"
            )
            reason_codes.append("MODEL_SUBTASK_MISSING_PLANNED_FILES")
        # Only recorded when require_model_planned_files is actually the
        # active policy - otherwise this constraint isn't being enforced at
        # all this call, and a SATISFIED record would be a fabricated signal
        # (regression detection would then fire falsely once a caller DOES
        # start enforcing it).
        if obligation_ledger is not None and require_model_planned_files and (
            st.execution_method == ExecutionMethod.MODEL
            and st.execution_role != ExecutionRole.VERIFICATION
        ):
            obligation_ledger.record(ObligationRecord(
                id=f"plan.subtask.{st.id}.model_subtask_scope", kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY,
                status=ObligationStatus.VIOLATED if model_subtask_unscoped else ObligationStatus.SATISFIED,
                authority=ObligationAuthority.DETERMINISTIC,
                description="a MODEL implementation subtask must declare a non-empty planned_files scope",
                source="plan_validation.validate_plan", revision=revision,
                evidence={"subtask_id": st.id, "planned_files": [pf.path for pf in st.planned_files]},
                owner_subtask_id=st.id,
                terminal_required=True,
            ))
        for pf in st.planned_files:
            full_path = os.path.join(workspace_path, pf.path)
            action_mismatch = not os.path.exists(full_path) and pf.action != FileAction.CREATE
            if action_mismatch:
                errors.append(
                    f"subtask {st.id!r} planned file {pf.path!r} (action={pf.action.value}) "
                    "does not exist on disk and is not marked action=create"
                )
                reason_codes.append("PLANNED_FILE_ACTION_MISMATCH")
            if obligation_ledger is not None:
                obligation_ledger.record(ObligationRecord(
                    id=f"plan.file.{pf.path}.action_consistency", kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY,
                    status=ObligationStatus.VIOLATED if action_mismatch else ObligationStatus.SATISFIED,
                    authority=ObligationAuthority.DETERMINISTIC,
                    description="a planned file's action must be create when it does not yet exist "
                                "on disk, modify/delete when it does",
                    source="plan_validation.validate_plan", revision=revision,
                    evidence={"subtask_id": st.id, "path": pf.path, "action": pf.action.value},
                    owner_subtask_id=st.id,
                    terminal_required=True,
                ))

    acceptance_ids = {ac.id for ac in plan.acceptance_criteria}
    covered_ids = set()
    for st in plan.subtasks:
        for acid in st.acceptance_criteria_ids:
            if acid not in acceptance_ids:
                errors.append(f"subtask {st.id!r} references unknown acceptance_criteria id {acid!r}")
            else:
                covered_ids.add(acid)
    uncovered = sorted(acceptance_ids - covered_ids)
    if uncovered:
        errors.append(f"acceptance criteria not covered by any subtask: {uncovered}")

    # MA7.8 fix (2026-08-24, real live-validation finding, protocol_encoder_java):
    # a genuinely EMPTY workspace (zero commits, nothing established) has
    # no real insertion point ANY plan could name - requiring
    # extension_points there asks for something that structurally cannot
    # exist yet, not for a justification the Planner failed to give. Reuses
    # triage.py's own _workspace_appears_empty() (the SAME real check
    # EngineeringTriageService.classify() already uses for its own
    # "repo empty / brand new" signal) rather than a second, driftable
    # emptiness heuristic. A workspace with ANY established content still
    # requires a real extension_points justification, unchanged - this is
    # a narrow exemption for the true first-plan-ever case, not a general
    # loosening of MA3's own physical-topology-preservation intent.
    if (
        plan.kind in (ChangeKind.ENHANCEMENT, ChangeKind.MILESTONE)
        and not plan.extension_points
        and not _workspace_appears_empty(workspace_path)
        and not resuming_own_established_progress
    ):
        errors.append(
            f"plan kind={plan.kind.value} requires at least one extension_points entry "
            "(a real insertion point the new capability attaches to) but none were given"
        )
        reason_codes.append("EXTENSION_POINT_REQUIRED")

    if plan.kind == ChangeKind.REFACTOR:
        refactor_baseline_missing = not (plan.refactor_baseline or "").strip()
        if refactor_baseline_missing:
            errors.append(
                "plan kind=refactor requires a non-blank refactor_baseline to order "
                "equivalence verification against"
            )
            reason_codes.append("REFACTOR_BASELINE_MISSING")
        if obligation_ledger is not None:
            obligation_ledger.record(ObligationRecord(
                id="plan.refactor_baseline.non_blank", kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY,
                status=ObligationStatus.VIOLATED if refactor_baseline_missing else ObligationStatus.SATISFIED,
                authority=ObligationAuthority.DETERMINISTIC,
                description="a refactor-kind plan must set a non-blank refactor_baseline "
                            "(the subtask id whose output orders equivalence verification)",
                source="plan_validation.validate_plan", revision=revision,
                evidence={"refactor_baseline": plan.refactor_baseline},
                terminal_required=True,
            ))

    if available_tool_names is not None:
        available = set(available_tool_names)
        for st in plan.subtasks:
            if st.execution_method == ExecutionMethod.TOOL and st.tool_name not in available:
                errors.append(f"subtask {st.id!r} references unregistered tool_name {st.tool_name!r}")
            for vm in st.verification:
                if (
                    vm.type == VerificationMethodType.TOOL
                    and vm.tool_name not in available
                    and vm.tool_name not in BUILTIN_QUALITY_GATE_VERIFIERS
                ):
                    errors.append(
                        f"subtask {st.id!r} verification references unregistered tool_name {vm.tool_name!r}"
                    )
        for ac in plan.acceptance_criteria:
            if (
                ac.method == VerificationMethodType.TOOL
                and ac.tool_name not in available
                and ac.tool_name not in BUILTIN_QUALITY_GATE_VERIFIERS
            ):
                errors.append(f"acceptance criterion {ac.id!r} references unregistered tool_name {ac.tool_name!r}")

    if context_files is not None:
        context_set = set(context_files)
        for st in plan.subtasks:
            for pf in st.planned_files:
                if pf.path not in context_set:
                    errors.append(
                        f"subtask {st.id!r} planned file {pf.path!r} is outside the supplied context package"
                    )

    escalated_route: Optional[EngineeringRoute] = None
    if route is not None and triage_service is not None:
        all_planned_files = sorted({pf.path for st in plan.subtasks for pf in st.planned_files})
        escalated_route = await triage_service.recompute_from_files(
            route=route, workspace_path=workspace_path, planned_files=all_planned_files
        )

    if errors and not reason_codes:
        reason_codes.append("PLAN_VALIDATION_FAILED")
    return PlanValidationResult(
        valid=not errors,
        errors=errors,
        reason_codes=list(dict.fromkeys(reason_codes)),
        escalated_route=escalated_route,
    )
