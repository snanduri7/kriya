"""Deterministic PlanValidator - MA6.2 of the MA6 structured-execution
implementation plan. The one place an EngineeringPlan (kriya/workflow/
plan_schema.py, MA6.1) goes from "syntactically well-formed" to
"execution-authorized." Never delegates structural validity to an LLM/
Reviewer (MA6 invariant 1) - every check here is a deterministic graph/
filesystem/registry lookup, same spirit as kriya/workflow/triage.py's own
"never asked of a model" ImpactVector components.

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

from kriya.workflow.plan_schema import (
    BUILTIN_QUALITY_GATE_VERIFIERS,
    EngineeringPlan,
    ExecutionMethod,
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
    subtask); this function only trusts the flag it's given."""
    errors: List[str] = []

    ids = [st.id for st in plan.subtasks]
    duplicate_ids = sorted({sid for sid in ids if ids.count(sid) > 1})
    if duplicate_ids:
        errors.append(f"duplicate subtask ids: {duplicate_ids}")
    id_set = set(ids)

    for st in plan.subtasks:
        for dep in st.depends_on:
            if dep not in id_set:
                errors.append(f"subtask {st.id!r} depends_on unknown subtask id {dep!r}")

    if not _acyclic(plan.subtasks):
        errors.append("subtask dependency graph contains a cycle")

    for st in plan.subtasks:
        if require_model_planned_files and st.execution_method == ExecutionMethod.MODEL and not st.planned_files:
            errors.append(
                f"subtask {st.id!r} uses execution_method=model but declares no planned_files; "
                "authoritative execution requires a non-empty modification scope"
            )
        for pf in st.planned_files:
            full_path = os.path.join(workspace_path, pf.path)
            if not os.path.exists(full_path) and pf.action != FileAction.CREATE:
                errors.append(
                    f"subtask {st.id!r} planned file {pf.path!r} (action={pf.action.value}) "
                    "does not exist on disk and is not marked action=create"
                )

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

    if plan.kind == ChangeKind.REFACTOR and not (plan.refactor_baseline or "").strip():
        errors.append(
            "plan kind=refactor requires a non-blank refactor_baseline to order "
            "equivalence verification against"
        )

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

    return PlanValidationResult(valid=not errors, errors=errors, escalated_route=escalated_route)
