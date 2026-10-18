"""Owner recovery: who must repair an artifact a failed stage needs, under which obligation, in which order, and
the integration evidence the reopened chain is judged on (full modularization W4, E11).

Moved verbatim from ``kriya/workflow/workflow_controller.py`` (module-level code; the enforce loop that runs the
recovery stays in the controller):

- effective artifact ownership: ``ArtifactOwnerResolutionBasis``, ``OwnerResolution``,
  ``_successful_modifiers_in_execution_order``, ``resolve_effective_artifact_owner`` with its dependency-ancestry
  fallback ``resolve_scope_conflict_owners`` (its only caller is here) and ``resolve_effective_scope_conflict_owners``;
- the recovery execution plan: ``derive_recovery_participants``, ``_order_recovery_groups``,
  ``build_recovery_execution_plan``, the evidence authority (``_participant_evidence_is_grounded``,
  ``_scope_conflict_evidence_authority``), the cross-owner obligation (``_cross_owner_obligation_id``,
  ``_prior_cross_owner_musts``, ``_get_or_create_cross_owner_obligation``), the reopened owner's context
  (``_build_owner_recovery_context``), the acceptance precheck (``_evaluate_recovery_acceptance_precheck``) and the
  bounded self-correction first attempt (``_attempt_owner_recovery_self_correction``);
- the verification-owner recovery texts (``verification_failure_evidence`` with ``_GATE_FAILURE_TYPES``,
  ``verification_owner_recovery_context``);
- the cross-subtask integration obligations (``_integration_reference_token``, ``_integration_evidence``,
  ``_evaluate_integration_obligations``).

Every resolution, ordering, authority and obligation decision is unchanged, in the same order (the records keep their
``workflow_controller.owner_recovery`` / ``workflow_controller.integration_check`` sources). ``EstablishedProvenance``
stays in the controller: its ``current_content`` projects through ``context_projection`` (all-import cycle) and the
controller's own ``_ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE``, and the enforce loop constructs it; the moved
integration functions only call its methods, so it is spelled ``Any`` in their annotations, as is ``ControlState``
(an allowlisted edge of the controller's).

``kriya/workflow/workflow_controller.py`` keeps every one of these names (a forwarder, or a re-export of a class or
constant). What arrives in ``OwnerRecoveryDeps``, resolved through ``kriya.workflow.workflow_controller`` by
``workflow_controller._owner_recovery_deps()`` when a forwarder runs: the controller's ``logger`` (the recovery and
integration records keep logging as ``kriya.workflow.workflow_controller``), ``attribution``'s
``DETERMINISTIC_ATTRIBUTION_TIERS`` and a loader doing the body's call-time import of ``self_correction``'s
``run_self_correction_loop`` (both all-import cycle). This module never imports ``kriya.workflow.workflow_controller``,
``workflow``, ``attempt`` or ``kriya.cli``, nor any all-import-cycle module.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_revision import _plan_scope_conflict_files, _transitive_upstream_ids
from kriya.workflow.plan_schema import EngineeringPlan, Subtask
from kriya.workflow.recovery_plan import (
    RecoveryAction,
    RecoveryExecutionPlan,
    RecoveryOwnerGroup,
    RecoveryParticipant,
    RecoveryParticipantRole,
)
from kriya.workflow.workflow_types import SubtaskStatus


@dataclass(frozen=True)
class OwnerRecoveryDeps:
    """What the moved owner-recovery code may use, resolved through ``kriya.workflow.workflow_controller`` by
    ``workflow_controller._owner_recovery_deps()``. Every field is required."""

    # the controller's logger
    logger: Any
    # kriya.workflow.attribution (all-import cycle)
    DETERMINISTIC_ATTRIBUTION_TIERS: Any
    # the self-correction loop's call-time import (kriya.workflow.self_correction, all-import cycle)
    load_run_self_correction_loop: Callable[[], Any]


def resolve_scope_conflict_owners(
    plan: EngineeringPlan, required_files: List[str], failed_subtask: Subtask,
) -> Dict[str, List[str]]:
    """Resolve required repair files to unique, declared upstream owners."""
    upstream_ids: set[str] = set()
    frontier = list(failed_subtask.depends_on)
    while frontier:
        dependency_id = frontier.pop()
        if dependency_id in upstream_ids:
            continue
        upstream_ids.add(dependency_id)
        dependency = plan.subtask_by_id(dependency_id)
        if dependency is not None:
            frontier.extend(dependency.depends_on)
    owners: Dict[str, List[str]] = {}
    for path in required_files:
        owner = plan.file_owner(path)
        if owner is None or owner.id == failed_subtask.id or owner.id not in upstream_ids:
            continue
        owners.setdefault(owner.id, []).append(path)
    return owners


# The failure types whose raw output is a Kriya-run gate's own text (compiler, test runner, runtime verifier, static
# analyser) - public to the run. Anything else (a requirements_unresolved stop carrying a sealed external authority's
# "REQ-n (VIOLATED)" verdict, a contract or integrity stop) is NOT handed to the model: independent review F2 -
# even the verdict bit of a hidden oracle is authority the Developer never receives.
_GATE_FAILURE_TYPES = frozenset({
    "compile", "test", "targeted_test", "regression_test", "run_verification", "static_analysis",
    "pom_semantic_validation",
})


def verification_failure_evidence(call_result: Mapping[str, Any]) -> str:
    """The verification-only unit's own deterministic failure text - its
    gate's output, run by Kriya, public to the run - bounded for the
    reopened owner's context. A failure that is not a gate's output names
    only its type: no hidden-oracle content or verdict, no model text."""
    failure = call_result.get("last_failure") or {}
    failure_type = str(failure.get("type") or "")
    if failure_type and failure_type not in _GATE_FAILURE_TYPES:
        return (f"the verification unit stopped with failure type {failure_type!r}; its detail is not a gate "
                "output and is not shared")
    text = str(failure.get("raw_output") or failure.get("message") or call_result.get("environment_failure") or "")
    return text[-2000:]


def verification_owner_recovery_context(
    *, verification_subtask_id: str, owner_id: str, owner_files: Sequence[str], evidence: str,
) -> str:
    """The Developer-facing recovery text for an owner reopened by a failed
    downstream verification unit (unit-testable like every other retry-prompt
    builder here)."""
    return (
        "--- VERIFICATION FAILURE RECOVERY ---\n"
        f"Reopened owner: {owner_id}\n"
        f"Verification unit that failed after your change: {verification_subtask_id}\n"
        f"Repair files (your own planned files only): {json.dumps(list(owner_files))}\n"
        "Deterministic verification evidence (the unit's own gate output):\n"
        f"{evidence or 'unavailable'}\n"
        "Fix the cause of this failure in the files above. Preserve every provided contract and global "
        "invariant; a candidate identical to the current content is not a repair."
    )


class ArtifactOwnerResolutionBasis(str, Enum):
    """MA8.1 completion v3 (2026-08-29, 'Effective Artifact Ownership and
    Recovery Routing'): WHY resolve_effective_artifact_owner picked a given
    subtask as an artifact's recovery owner - pure observability/testing
    metadata, never itself a control-flow input (every caller only ever
    branches on owner_subtask_id being present or absent).

    LATEST_SUCCESSFUL_MODIFIER: the artifact's real, applied execution
    provenance (ControlState.subtask_states/subtask_written_files) names a
    subtask that actually wrote it and completed successfully - the
    primary, authoritative signal (see resolve_effective_artifact_owner).

    DEPENDENCY_ANCESTRY: no execution provenance was available (or usable
    yet), so the plan's own declared file_owner() was used, gated - exactly
    as resolve_scope_conflict_owners always required - on that owner being
    a transitive depends_on ancestor of the failing subtask. This is the
    ORIGINAL (pre-2026-08-29-v3) resolution mechanism, kept as the fallback
    for when provenance genuinely doesn't exist yet.

    UNIQUE_PLAN_OWNER: reserved for a unique plan-declared owner accepted
    WITHOUT dependency-ancestry confirmation. Deliberately never produced by
    resolve_effective_artifact_owner today - test_enforce_surfaces_
    unrelated_owner_scope_conflict_as_plan_revision_required_instead_of_
    merging depends on a same-shaped case (a unique owner, declared but
    neither upstream nor yet-executed) staying UNRESOLVED, not silently
    auto-picked. Kept as a distinct typed value so a future, more permissive
    policy could be represented without another vocabulary change - not a
    live code path.

    UNRESOLVED: no owner could be defensibly selected. Callers must fail
    closed, never guess (PRV-06 §26 invariant 3/8)."""

    LATEST_SUCCESSFUL_MODIFIER = "latest_successful_modifier"
    UNIQUE_PLAN_OWNER = "unique_plan_owner"
    DEPENDENCY_ANCESTRY = "dependency_ancestry"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class OwnerResolution:
    """One artifact's recovery-owner resolution result - small, typed,
    reused as-is for both control flow (owner_subtask_id is None or not)
    and observability (resolution_basis/evidence, logged verbatim by
    resolve_effective_scope_conflict_owners as ARTIFACT_OWNER_RESOLVED/
    ARTIFACT_OWNER_UNRESOLVED)."""

    artifact: str
    owner_subtask_id: Optional[str]
    resolution_basis: ArtifactOwnerResolutionBasis
    evidence: Dict[str, Any] = field(default_factory=dict)


def _successful_modifiers_in_execution_order(
    path: str, order: List[str], control_state: Any,
) -> List[str]:
    """Every subtask id (in real execution order) whose OWN ControlState
    record shows it completed successfully AND actually wrote `path` - the
    real, applied file list (subtask_written_files), never the plan's mere
    upfront planned_files declaration. A subtask that hasn't run yet has no
    entry here at all (ControlState is populated incrementally, in
    execution order, as _run_structured_enforce's own per-subtask loop
    proceeds) - so this can never surface a not-yet-executed subtask, no
    special-casing required to keep it safe for that case."""
    return [
        subtask_id for subtask_id in order
        if control_state.subtask_states.get(subtask_id) == SubtaskStatus.COMPLETED.value
        and path in control_state.subtask_written_files.get(subtask_id, [])
    ]


def resolve_effective_artifact_owner(
    plan: EngineeringPlan,
    path: str,
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[Any] = None,
) -> OwnerResolution:
    """MA8.1 completion v3 (PRV-06, 2026-08-29): the artifact's EFFECTIVE
    recovery owner - the most recent successfully-completed subtask that
    actually produced/modified the revision currently visible to the
    workflow - not necessarily the planner-assigned owner, the nearest
    depends_on ancestor, or the first subtask that mentioned the file.

    Live PRV-06 evidence this closes: s1 created App.java, s2 later
    successfully MODIFIED it (both declare it in planned_files - a
    legitimate sequential ownership chain, see plan_schema.py's
    classify_file_ownership docstring) - but s3 (needing a further App.java
    fix) only declared depends_on=['s1'], omitting s2. The OLD single
    mechanism (resolve_scope_conflict_owners, walking depends_on only)
    could never find s2: plan.file_owner('App.java') itself already returns
    None for a MULTI-declared path, and even if it didn't, s2 sits outside
    s3's own declared ancestry. Recovery then fell through to
    revise_plan_for_grounded_scope_owner's DAG-mutation path, which hit its
    own real bug (see that function's own docstring) and the run terminated
    unresolved - despite Kriya already knowing, from its own execution
    state, that s2 was the artifact's real current owner.

    Two-step authority order, execution provenance ALWAYS tried first:

    1. Execution provenance (_successful_modifiers_in_execution_order) -
       the most recent completed, real modifier. Requires `order` and
       `control_state`; skipped entirely (falls through to step 2) when
       either is omitted, preserving the exact old behavior for any caller
       that doesn't have them (e.g. direct unit tests).
    2. Dependency-ancestry fallback (resolve_scope_conflict_owners,
       UNCHANGED) - the plan's own declared file_owner(), gated on being a
       transitive depends_on ancestor of the failing subtask. This is
       deliberately the exact ORIGINAL mechanism, not a new one: widening
       it to "any unique plan-declared owner" would resolve
       test_enforce_surfaces_unrelated_owner_scope_conflict_as_plan_
       revision_required_instead_of_merging's own case (a unique but
       genuinely unrelated/not-yet-run owner) into an incorrect silent
       auto-pick - exactly the "arbitrary topological predecessor" failure
       mode this design explicitly forbids (PRV-06 §4/§20 invariant 2).

    Never returns a subtask that hasn't successfully completed (a FAILED
    candidate's own written files are ordinary in-progress residue, not
    ownership - PRV-06 §26 invariant 3) and never returns failed_subtask
    itself."""
    if order is not None and control_state is not None:
        modifiers = [
            subtask_id for subtask_id in _successful_modifiers_in_execution_order(path, order, control_state)
            if subtask_id != failed_subtask.id
        ]
        if modifiers:
            return OwnerResolution(
                artifact=path, owner_subtask_id=modifiers[-1],
                resolution_basis=ArtifactOwnerResolutionBasis.LATEST_SUCCESSFUL_MODIFIER,
                evidence={"prior_modifiers": modifiers},
            )
    ancestry_owners = resolve_scope_conflict_owners(plan, [path], failed_subtask)
    for owner_id, owned_paths in ancestry_owners.items():
        if path in owned_paths:
            return OwnerResolution(
                artifact=path, owner_subtask_id=owner_id,
                resolution_basis=ArtifactOwnerResolutionBasis.DEPENDENCY_ANCESTRY,
                evidence={},
            )
    return OwnerResolution(
        artifact=path, owner_subtask_id=None,
        resolution_basis=ArtifactOwnerResolutionBasis.UNRESOLVED,
        evidence={},
    )


def resolve_effective_scope_conflict_owners(
    deps: OwnerRecoveryDeps,
    plan: EngineeringPlan,
    required_files: List[str],
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[Any] = None,
) -> Dict[str, List[str]]:
    """Multi-artifact wrapper around resolve_effective_artifact_owner,
    returning the exact same Dict[owner_id, [paths]] shape resolve_scope_
    conflict_owners always has - the real call site in
    _run_structured_enforce's PLAN_SCOPE_DEFECT merge-skip condition uses
    this directly. General Obligation-Centric Recovery Execution
    (2026-08-30): the owner-recovery loop itself no longer calls this
    wrapper - it calls resolve_effective_artifact_owner PER ARTIFACT via
    derive_recovery_participants, since a bare Dict[owner_id, [paths]] can
    no longer distinguish "N artifacts share one owner" (one
    RecoveryOwnerGroup) from "N owners for N different artifacts" (N
    RecoveryOwnerGroups) - see build_recovery_execution_plan and the
    implementation spec's §4. Once an owner is resolved (by either path),
    control still hands off entirely to the existing, unmodified MA8.1
    requirement-scoped recovery mechanism (this function performs no
    recovery itself).

    Every per-artifact resolution is logged (ARTIFACT_OWNER_RESOLVED /
    ARTIFACT_OWNER_UNRESOLVED) so a later forensic read never has to
    reconstruct "why was X picked as owner of Y" by hand (PRV-06 §22)."""
    owners: Dict[str, List[str]] = {}
    for path in required_files:
        resolution = resolve_effective_artifact_owner(
            plan, path, failed_subtask, order=order, control_state=control_state,
        )
        if resolution.owner_subtask_id is None:
            deps.logger.info(
                "ARTIFACT_OWNER_UNRESOLVED artifact=%s candidate_owners=%s reason=%s",
                path, [], resolution.resolution_basis.value,
            )
            continue
        deps.logger.info(
            "ARTIFACT_OWNER_RESOLVED artifact=%s owner_subtask_id=%s resolution_basis=%s prior_modifiers=%s",
            path, resolution.owner_subtask_id, resolution.resolution_basis.value,
            resolution.evidence.get("prior_modifiers", []),
        )
        owners.setdefault(resolution.owner_subtask_id, []).append(path)
    return owners


def _cross_owner_obligation_id(
    originating_subtask_id: str, owner_subtask_id: str, required_files: List[str],
    failure_family: str, generation: int,
) -> str:
    """MA8.1 completion (2026-08-29 v2 design review): identity is keyed by
    REQUIREMENT, not by owner alone - see this module's own "Requirement-
    scoped repeated owner recovery" note (near `recovery_generation_by_key`
    below) for the live incident this closes. `failure_family` is the
    EXISTING, already-typed `scope_conflict["failure_type"]` (compile,
    diagnosis_mismatch, misdirected_edit, ...) - deliberately NOT a hash of
    free-text requirement prose (the design review's own explicit
    instruction: "identity must be based primarily on deterministic/typed
    information"). `generation` is a monotonic counter, incremented once
    per completed owner-recovery-and-downstream-retry cycle for this exact
    (origin, owner, files) tuple (see `recovery_generation_by_key`) - NOT
    incremented per raw occurrence, so a requirement recurring *within* the
    same still-in-progress cycle keeps the SAME id (sticky - see
    `_get_or_create_cross_owner_obligation`), while a requirement that
    surfaces *after* a full cycle has already completed gets a NEW id even
    when its failure_family happens to match the prior one - exactly the
    live PRV-06 case (both occurrences were "diagnosis_mismatch", but the
    second arose only after the first owner-recovery-plus-retry cycle had
    already finished)."""
    return (
        f"recovery.{originating_subtask_id}.{owner_subtask_id}."
        f"{'.'.join(sorted(required_files))}.{failure_family}.{generation}"
    )


def _prior_cross_owner_musts(
    ledger: Optional[ObligationLedger], *, owner_subtask_id: str, required_files: List[str],
) -> List[str]:
    """Every earlier-generation CROSS_OWNER_ARTIFACT_REQUIREMENT's own
    MUST_FIX text for this exact owner+artifact combination, regardless of
    which originating subtask or failure_family raised it - folded into a
    LATER generation's own MUST_PRESERVE (see _get_or_create_cross_owner_
    obligation) so a second recovery of the same owner cannot silently
    regress a correction an earlier, still-relevant requirement already
    established (2026-08-29 v2 design review, invariant 6: "a later
    recovery of an owner must preserve corrections established by earlier
    active recovery requirements unless authoritative evidence explicitly
    invalidates them"). Uses the ledger's own current_by_kind() - no new
    query surface - and filters by the same required_files tuple already
    stored in repair_scope."""
    if ledger is None:
        return []
    target_scope = tuple(sorted(required_files))
    musts: List[str] = []
    for record in ledger.current_by_kind(ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT):
        if record.owner_subtask_id != owner_subtask_id or record.repair_scope != target_scope:
            continue
        musts.extend(record.evidence.get("must_fix", []))
    return musts


def _get_or_create_cross_owner_obligation(
    deps: OwnerRecoveryDeps,
    ledger: Optional[ObligationLedger],
    *,
    originating_subtask_id: str,
    owner_subtask_id: str,
    required_files: List[str],
    scope_conflict: Dict[str, Any],
    generation: int,
    revision: Any,
) -> Optional[ObligationRecord]:
    """MA8.1 (PRV-06, 2026-08-29; completed 2026-08-29 v2): the single place
    a downstream failure's grounded requirement for an upstream owner
    becomes a durable, ledger-tracked fact instead of a one-shot prompt
    string that can be lost or overwritten - see ObligationKind.
    CROSS_OWNER_ARTIFACT_REQUIREMENT's own docstring for the original live
    incident, and this module's "Requirement-scoped repeated owner
    recovery" note for the v2 completion (a SECOND, genuinely different
    requirement on the same owner was being silently blocked by an owner-
    level one-shot guard).

    Sticky WITHIN one generation: if this EXACT requirement (same
    originating subtask, owner, required files, failure_family, AND
    generation - see _cross_owner_obligation_id) is already on record as
    VIOLATED, returns it UNCHANGED rather than re-deriving from the
    CURRENT scope_conflict - a later attempt's own grounded evidence can
    be weaker or differently shaped than the first, strongest occurrence
    within the same cycle - the obligation's own MUST_FIX/evidence must
    not silently regress just because a later symptom looks different. A
    NEW generation (see the caller's own `recovery_generation_by_key`)
    always gets a fresh obligation, even when failure_family repeats -
    genuinely different requirements are allowed to coexist rather than
    collapsing into one.

    A new obligation's own MUST_PRESERVE always folds in every prior
    generation's MUST_FIX for the same owner+artifact (_prior_cross_owner_
    musts) - the mechanism behind invariant 6 (later recovery must
    preserve earlier corrections).

    Reuses ObligationRecord as-is (no new dataclass) - owner_subtask_id and
    repair_scope are already exactly the fields this needs; must_fix/
    must_preserve/acceptance_conditions/raw_evidence live in the free-form
    `evidence` dict.

    Returns None only when there is no ledger to record into (a caller
    outside structured/enforce execution, which never reaches this
    recovery path today anyway - matches every other MA8/MA9 obligation
    hook's own "no ledger -> no-op" convention)."""
    if ledger is None:
        return None
    failure_family = scope_conflict.get("failure_type") or "unknown"
    obligation_id = _cross_owner_obligation_id(
        originating_subtask_id, owner_subtask_id, required_files, failure_family, generation,
    )
    existing = ledger.current(obligation_id)
    if existing is not None and existing.status == ObligationStatus.VIOLATED:
        # Sticky CONTENT (must_fix/must_preserve/evidence unchanged - never
        # regresses to a weaker later symptom), but a NEW history entry is
        # still recorded each time - recurrence must remain a durable,
        # countable ledger fact (matching PROCESS_BOUNDARY_COMPATIBILITY's
        # own precedent), not silently invisible. This is what gives the
        # caller's own bound check (ledger.history(id) vs
        # _MAX_PLAN_SCOPE_REVISION_ATTEMPTS) real teeth - a requirement
        # that keeps recurring within the same generation actually
        # exhausts its own budget instead of looking, forever, like a
        # single untouched record.
        recurring = ObligationRecord(
            id=obligation_id, kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
            status=ObligationStatus.VIOLATED,
            # Sticky like every other field on this branch (see the comment
            # above) - reuses the ORIGINAL authority this obligation earned
            # when first created, never re-derived from the CURRENT
            # scope_conflict (control-plane audit, 2026-08-30 - was
            # hardcoded DETERMINISTIC here regardless of `existing`'s own
            # real authority, same defect as the fresh-record branch below).
            authority=existing.authority,
            description=existing.description, source="workflow_controller.owner_recovery",
            revision=revision, evidence=existing.evidence,
            owner_subtask_id=existing.owner_subtask_id, terminal_required=False,
            repair_scope=existing.repair_scope,
        )
        ledger.record(recurring)
        return recurring
    grounded_reason = scope_conflict.get("reason") or "a grounded requirement discovered by the reopened downstream subtask"
    must_preserve = [
        "existing project/module identity",
        "existing configuration and declarations unrelated to this requirement",
    ] + _prior_cross_owner_musts(ledger, owner_subtask_id=owner_subtask_id, required_files=required_files)
    evidence = {
        "must_fix": [grounded_reason],
        "must_preserve": must_preserve,
        "raw_evidence": scope_conflict.get("raw_evidence") or "",
        "required_artifacts": list(required_files),
        "failure_family": failure_family,
        "generation": generation,
        "acceptance_conditions": [
            f"subtask {originating_subtask_id} passes its own Quality Gates without the same "
            "grounded requirement recurring",
        ],
        "originating_subtask_id": originating_subtask_id,
    }
    record = ObligationRecord(
        id=obligation_id,
        kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
        status=ObligationStatus.VIOLATED,
        # Control-plane audit (2026-08-30): was hardcoded DETERMINISTIC
        # unconditionally - a Developer's own advisory, unverified
        # self-diagnosis (Failure.type=="attribution_rejected") could stamp
        # its own MUST_FIX text with the ledger's HIGHEST authority tier,
        # letting it wrongly outrank a later, genuinely correct JUDGMENT-
        # tier contradiction under ObligationLedger's own DETERMINISTIC >
        # GROUNDED > JUDGMENT precedence rule - authority escalating merely
        # because advisory text got copied into a typed record's evidence
        # field. See _scope_conflict_evidence_authority's own docstring.
        authority=_scope_conflict_evidence_authority(deps, scope_conflict),
        description=f"{owner_subtask_id} must satisfy a requirement grounded by {originating_subtask_id}'s own failure: {grounded_reason}",
        source="workflow_controller.owner_recovery",
        revision=revision,
        evidence=evidence,
        owner_subtask_id=owner_subtask_id,
        terminal_required=False,
        repair_scope=tuple(sorted(required_files)),
    )
    ledger.record(record)
    deps.logger.info(
        "CROSS_OWNER_REQUIREMENT_CREATED requirement_id=%s origin=%s owner=%s "
        "failure_family=%s generation=%d",
        obligation_id, originating_subtask_id, owner_subtask_id, failure_family, generation,
    )
    return record


def _build_owner_recovery_context(
    *,
    owner_id: str,
    failed_subtask_id: str,
    required_owner_files: List[str],
    cross_owner_obligation: Optional[ObligationRecord],
    scope_conflict: Dict[str, Any],
) -> str:
    """The Developer-facing recovery_context text for a reopened owner
    subtask - extracted to its own function (2026-08-29, MA8.1) so the
    exact wording/fields it produces are directly unit-testable, matching
    this codebase's own precedent for every other retry-prompt builder
    (e.g. kriya/workflow/retry_prompts.py). When cross_owner_obligation is
    available, surfaces its MUST FIX/MUST PRESERVE/EVIDENCE/ACCEPTANCE
    explicitly - the exact grounded reason this owner is being reopened,
    not a generic "preserve brownfield identity" framing (see
    ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT's own docstring for the
    live incident this closes). Falls back to the original, plainer
    framing only when no ledger was available to record an obligation into
    (a caller outside structured/enforce execution)."""
    if cross_owner_obligation is not None:
        ev = cross_owner_obligation.evidence
        return (
            "--- ACTIVE CROSS-OWNER RECOVERY REQUIREMENT ---\n"
            f"Reopened owner: {owner_id}\n"
            f"Originating failure: subtask {failed_subtask_id}\n"
            f"Required repair files: {json.dumps(required_owner_files)}\n\n"
            f"MUST FIX: {'; '.join(ev.get('must_fix', [])) or 'unavailable'}\n"
            f"MUST PRESERVE: {'; '.join(ev.get('must_preserve', [])) or 'unavailable'}\n"
            f"EVIDENCE: {ev.get('raw_evidence') or 'unavailable'}\n"
            f"ACCEPTANCE: {'; '.join(ev.get('acceptance_conditions', [])) or 'unavailable'}\n\n"
            "This is the exact grounded reason this owner was reopened - address it "
            "specifically, not a generic regeneration. Preserve every other existing "
            "declaration this file already has."
        )
    return (
        "--- authoritative plan recovery ---\n"
        f"Failed consumer: {failed_subtask_id}\n"
        f"Reopened owner: {owner_id}\n"
        f"Required repair files: {json.dumps(required_owner_files)}\n"
        f"Failure type: {scope_conflict.get('failure_type')}\n"
        f"Grounded failure diagnosis: {scope_conflict.get('reason') or 'unavailable'}\n"
        "Repair only the reopened owner's approved files. Preserve its provided "
        "contracts and all relevant global invariants."
    )


def _evaluate_recovery_acceptance_precheck(
    *,
    scope_conflict: Dict[str, Any],
    plan_workspace_path: str,
    required_owner_files: List[str],
) -> Optional[bool]:
    """Recovery Execution Contract Invariant 3 (2026-08-29): "owner local
    gates PASS" and "the recovery requirement that reopened this owner is
    actually satisfied" are different questions - a regenerated candidate
    can change (so RECOVERY_NO_PROGRESS never fires) while still being
    WRONG (e.g. a build config corrected from one bad entrypoint value to
    a DIFFERENT bad one). This is the smallest authoritative acceptance
    evidence available BEFORE ever resuming the downstream consumer -
    deliberately narrow, matching Correctness Continuity Part C's own "no
    universal semantic evaluator" boundary, reused here rather than
    invented fresh. Returns True/False when a deterministic evidence
    source actually applies to this scope_conflict; None when none does,
    in which case the caller falls back to the PRE-EXISTING, already-
    correct design documented at the CONSUMER_RETRY_STARTED call site:
    the consumer's own retry Quality Gates ARE the authoritative
    acceptance signal for everything not covered by a cheap check here -
    this function only ever narrows WHEN a wasted consumer call can be
    skipped, never widens what counts as success.

    Exactly one evidence source, deliberately: `required_reference_token`
    (an optional string a caller may place directly on scope_conflict,
    e.g. an authoritative entrypoint/config value a downstream consumer
    already established) - a deterministic whole-word text match against
    the owner's own required files, the SAME crude, generic, language-
    agnostic primitive Correctness Continuity Part C already uses for
    cross-subtask integration evidence (`_integration_reference_token`/
    its own regex). Absent by default on every scope_conflict shape this
    codebase currently produces - populating it is future wiring, not
    built this round (disclosed, not silently assumed).

    A `PolymorphicValidator.run_compile_check()`-based compile-family
    fallback was drafted and DELIBERATELY REMOVED during this same
    round's own regression sweep: it produced false RECOVERY_ACCEPTANCE_
    UNSATISFIED rejections against every pre-existing test's synthetic
    pom.xml fixture (no real Maven toolchain/buildable project behind
    them), and - more fundamentally - directly contradicts this
    codebase's own already-tested finding at the CONSUMER_RETRY_STARTED
    call site: a compile check on the owner's OWN file has no way to
    evaluate a DOWNSTREAM cross-owner requirement (confirmed live:
    pom.xml's own compile check happily accepted a still-incomplete
    manifest). A real per-ecosystem "did this specific requirement's own
    evidence clear" check remains a legitimate future extension, but is
    not this one - see docs/design.md's own disclosure for this round."""
    required_token = scope_conflict.get("required_reference_token")
    if not required_token:
        return None
    pattern = re.compile(rf"\b{re.escape(required_token)}\b")
    for path in required_owner_files:
        full_path = os.path.join(plan_workspace_path, path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        if pattern.search(content):
            return True
    return False


def _participant_evidence_is_grounded(scope_conflict: Dict[str, Any]) -> bool:
    """MUST_CHANGE vs VERIFY disposition (PRV-11, 2026-08-30) - see
    RecoveryAction's own docstring (kriya/workflow/recovery_plan.py) for the
    live incident this closes.

    Deliberately narrow, in the SAFE direction: only `Failure.type ==
    "attribution_rejected"` is treated as NOT grounded - the one shape this
    codebase produces today that is BY CONSTRUCTION always `authority=
    "advisory"` (a Developer's own free-text FIX ANALYSIS naming files,
    never a compiler/test locator or a deterministic write-scope denial -
    see failure.py's own docstring for that type, and attempt.py's own
    Failure(type="attribution_rejected", ...) construction site, the ONLY
    place it is ever raised). Every OTHER scope_conflict shape (the ordinary
    PLAN_SCOPE_DEFECT path, the DENY_ALL/VERIFICATION_CONTRACT_DEFECT path,
    ...) is treated as grounded - this is NOT a claim that every such path
    is equally strong evidence (a genuinely differentiated per-artifact
    evidence tier remains future work, see derive_recovery_participants's
    own mutation_reason note); it is the smallest change that closes the
    ONE confirmed live gap (self-diagnosed files silently promoted to the
    same "must change" status as a real compiler locator) without
    reclassifying every existing, already-proven recovery scenario as
    VERIFY - a much larger, unproven behavior change this round does not
    make."""
    return scope_conflict.get("failure_type") != "attribution_rejected"


# Recovery obligations grounded in attribute_failure()'s own tier vocabulary
# (kriya/workflow/attribution.py) - reused here, not re-derived, matching
# _participant_evidence_is_grounded's own precedent of consulting existing
# typed evidence rather than inventing a parallel signal. PRV-11 (2026-08-
# 31): the classification itself now lives in attribution.py as
# DETERMINISTIC_ATTRIBUTION_TIERS - the one place attribute_failure()'s own
# tier vocabulary is defined - and is ALSO consulted by retry_strategy.py's
# own scope_conflict_is_grounded gate (previously that gate used only
# attribution.confidence=="high", which self_diagnosis satisfies
# unconditionally regardless of tier - the exact live gap this closes: a
# self-diagnosis-driven "the model named a different file as the real
# cause" was being treated as grounded enough to trigger real plan surgery
# BEFORE this same tier ever got classified JUDGMENT here, by which point
# the reopening had already happened). Both consumers now read the
# identical set, so they can never disagree about which tiers count.


def _scope_conflict_evidence_authority(deps: OwnerRecoveryDeps, scope_conflict: Dict[str, Any]) -> ObligationAuthority:
    """PRV-11 control-plane audit (2026-08-30) - the governing invariant:
    recovery obligation authority must never exceed the authority of the
    evidence it was constructed from. _get_or_create_cross_owner_obligation
    used to stamp EVERY cross-owner obligation ObligationAuthority.
    DETERMINISTIC unconditionally - including one built from
    scope_conflict["reason"] originating in Failure.type==
    "attribution_rejected" (a Developer's own advisory, unverified FIX
    ANALYSIS self-diagnosis - see _participant_evidence_is_grounded's own
    docstring for the confirmed live incident this class of evidence
    produces). A mislabeled DETERMINISTIC record could then wrongly outrank
    a later, genuinely correct JUDGMENT-tier contradiction under
    ObligationLedger's own precedence rule (DETERMINISTIC > GROUNDED >
    JUDGMENT) - authority escalating merely because advisory text got
    copied into a typed record's evidence field, not because the evidence
    itself became any stronger.

    Reuses _participant_evidence_is_grounded's own narrow, safety-first
    attribution_rejected check FIRST (never promoted past JUDGMENT
    regardless of what attribution_tier happens to say - matching that
    function's own precedent exactly, so the two mechanisms can never
    disagree about this ONE confirmed case), then classifies by
    scope_conflict["attribution_tier"] (attribute_failure()'s own real tier
    vocabulary) for every other, already-grounded case. An unrecognized or
    absent tier fails toward the SAFER, lower-authority JUDGMENT reading
    rather than assuming DETERMINISTIC for evidence this function has never
    actually evaluated - the same "never guess upward" discipline
    RecoveryAction's own docstring already established.

    GROUNDED is not yet reachable from any tier attribute_failure() or
    retry_strategy.py currently produce (see the tier-set comments above) -
    left as a real, supported outcome, not hardcoded away, for whenever a
    future tier genuinely earns it; not built speculatively today."""
    if not _participant_evidence_is_grounded(scope_conflict):
        return ObligationAuthority.JUDGMENT
    tier = scope_conflict.get("attribution_tier")
    if tier in deps.DETERMINISTIC_ATTRIBUTION_TIERS:
        return ObligationAuthority.DETERMINISTIC
    return ObligationAuthority.JUDGMENT


def derive_recovery_participants(
    plan: EngineeringPlan,
    scope_conflict: Dict[str, Any],
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[Any] = None,
    recovery_generation_by_key: Optional[Dict[Tuple[str, str, Tuple[str, ...]], int]] = None,
) -> Tuple[RecoveryParticipant, ...]:
    """General Obligation-Centric Recovery Execution (2026-08-30) - see
    `Kriya_General_Obligation_Centric_Recovery_Execution_Implementation_
    Specification.md` (repo root) for the full design. Resolves EVERY
    artifact a scope_conflict names to its own independent effective owner
    via the UNCHANGED resolve_effective_artifact_owner - never a bare
    owner-count check. Reuses _plan_scope_conflict_files for the same
    grounded_owner_files-over-required_files precedence every other
    recovery call site already uses.

    mutation_reason (spec §6, v1 scope): the SAME aggregate
    scope_conflict['reason'] every required_files entry already implicitly
    shares today - not yet independently derived per artifact
    (retry_strategy.py's own scope-conflict construction has no per-file
    evidence breakdown to draw from). Disclosed, not silently assumed - see
    RecoveryParticipant's own docstring in kriya/workflow/recovery_plan.py.

    recovery_action (PRV-11, 2026-08-30): MUST_CHANGE when
    `_participant_evidence_is_grounded` says this scope_conflict's own
    evidence is independently verified, OR when this exact (originating
    subtask, effective owner, artifact) triple has ALREADY been through at
    least one full recovery cycle before (`recovery_generation_by_key`,
    the SAME per-(subtask,owner,files) counter the owner-recovery loop
    already threads through `build_recovery_execution_plan` - reused here,
    keyed per-artifact as `(subtask, owner, (artifact,))`, not invented
    fresh) - a requirement that RECURRED after a full skip-and-reverify
    cycle already gave it the benefit of the doubt is no longer treated as
    merely "implicated." Otherwise VERIFY. `recovery_generation_by_key`
    defaults to None (treated as empty - generation 0 for everything),
    matching every other optional-dependency default in this module."""
    required_files = _plan_scope_conflict_files(scope_conflict)
    mutation_reason = scope_conflict.get("reason") or ""
    grounded = _participant_evidence_is_grounded(scope_conflict)
    generation_by_key = recovery_generation_by_key or {}
    participants: List[RecoveryParticipant] = []
    for path in required_files:
        resolution = resolve_effective_artifact_owner(
            plan, path, failed_subtask, order=order, control_state=control_state,
        )
        if resolution.owner_subtask_id is None:
            participants.append(RecoveryParticipant(
                artifact=path, role=RecoveryParticipantRole.READ_ONLY_CONTEXT,
                effective_owner_subtask_id=None, owner_resolution_basis="unresolved",
                mutation_reason="",
            ))
            continue
        prior_generation = generation_by_key.get(
            (failed_subtask.id, resolution.owner_subtask_id, (path,)), 0,
        )
        recovery_action = (
            RecoveryAction.MUST_CHANGE if (grounded or prior_generation > 0)
            else RecoveryAction.VERIFY
        )
        participants.append(RecoveryParticipant(
            artifact=path, role=RecoveryParticipantRole.REQUIRED_MUTATION,
            effective_owner_subtask_id=resolution.owner_subtask_id,
            owner_resolution_basis=resolution.resolution_basis.value,
            mutation_reason=mutation_reason,
            recovery_action=recovery_action,
        ))
    return tuple(participants)


def _order_recovery_groups(
    plan: EngineeringPlan, participants: Tuple[RecoveryParticipant, ...],
) -> Optional[Tuple[RecoveryOwnerGroup, ...]]:
    """Groups REQUIRED_MUTATION participants by effective owner (one
    RecoveryOwnerGroup per owner - an owner asked to fix two of its own
    files still gets exactly one group, since its own nested subtask
    invocation is already free to touch both), then topologically orders
    the groups (Kahn's algorithm): a real plan depends_on edge between two
    owners' subtasks (via the UNCHANGED _transitive_upstream_ids) is the
    real ordering signal; FileRole priority (kriya/workflow/
    generation_manifest.py - the SAME primitive repair_contract.py's own
    _derive_repair_groups already reuses) plus an alphabetical owner-id
    tie-break decides among owners with no pending real dependency, purely
    for determinism, never asserted as a real relationship.

    Returns None on a genuine ordering cycle among the PARTICIPATING owners
    (structurally impossible in a valid plan DAG, but guarded explicitly
    rather than letting a topological sort silently drop an edge or a
    comparator produce an inconsistent order) - callers must fail closed,
    never guess an order (mirrors resolve_effective_artifact_owner's own
    UNRESOLVED-on-ambiguity posture, applied to group scheduling instead of
    ownership).

    v1 deliberately linear (depends_on_group_ids is every already-placed
    group, a straight chain) - no partial/parallel group execution, matching
    RepairGroup's own v1 precedent in repair_contract.py."""
    from kriya.workflow.generation_manifest import _ROLE_PRIORITY, classify_file_role

    required = [p for p in participants if p.role is RecoveryParticipantRole.REQUIRED_MUTATION]
    if not required:
        return ()
    by_owner: Dict[str, List[RecoveryParticipant]] = {}
    for p in required:
        by_owner.setdefault(p.effective_owner_subtask_id, []).append(p)
    owner_ids = sorted(by_owner.keys())
    upstream_of = {owner_id: _transitive_upstream_ids(plan, owner_id) for owner_id in owner_ids}

    predecessors: Dict[str, set] = {owner_id: set() for owner_id in owner_ids}
    for a in owner_ids:
        for b in owner_ids:
            if a != b and a in upstream_of[b]:
                predecessors[b].add(a)

    def _file_role_priority(owner_id: str) -> int:
        return min(_ROLE_PRIORITY[classify_file_role(p.artifact)] for p in by_owner[owner_id])

    remaining = set(owner_ids)
    ordered_owner_ids: List[str] = []
    while remaining:
        ready = sorted(
            (oid for oid in remaining if not (predecessors[oid] & remaining)),
            key=lambda oid: (_file_role_priority(oid), oid),
        )
        if not ready:
            return None  # a real cycle among the remaining owners - fail closed
        next_owner = ready[0]
        ordered_owner_ids.append(next_owner)
        remaining.discard(next_owner)

    groups: List[RecoveryOwnerGroup] = []
    prior_group_ids: Tuple[str, ...] = ()
    for owner_id in ordered_owner_ids:
        members = tuple(sorted(by_owner[owner_id], key=lambda p: p.artifact))
        basis = "plan_dependency" if predecessors[owner_id] else "file_role_priority"
        group_id = f"group.{owner_id}"
        groups.append(RecoveryOwnerGroup(
            group_id=group_id, owner_subtask_id=owner_id, participants=members,
            depends_on_group_ids=prior_group_ids, relationship_basis=basis,
        ))
        prior_group_ids = prior_group_ids + (group_id,)
    return tuple(groups)


def build_recovery_execution_plan(
    deps: OwnerRecoveryDeps,
    plan: EngineeringPlan,
    scope_conflict: Dict[str, Any],
    failed_subtask: Subtask,
    *,
    order: Optional[List[str]] = None,
    control_state: Optional[Any] = None,
    plan_generation: int = 0,
    recovery_generation_by_key: Optional[Dict[Tuple[str, str, Tuple[str, ...]], int]] = None,
) -> Optional[RecoveryExecutionPlan]:
    """General Obligation-Centric Recovery Execution (2026-08-30) - see the
    implementation spec (repo root) for the full design this generalizes
    MA8.1's owner-recovery loop from. Returns None when recovery must fail
    closed: no artifact resolves to a REQUIRED_MUTATION participant at all,
    at least one named artifact resolves to a genuinely ambiguous owner
    (SCOPE_RECOVERY_OWNER_UNRESOLVED - now fires ONLY on true per-artifact
    ambiguity, never merely because more than one owner is involved - two
    unambiguously, independently resolved owners for two different
    artifacts is a valid multi-owner recovery plan, not unresolved
    ownership), or the owner-groups cannot be deterministically ordered (a
    genuine dependency cycle among participating owners). Callers treat
    None exactly like the old single-owner owner_map-empty/ambiguous
    break."""
    if not scope_conflict:
        return None
    participants = derive_recovery_participants(
        plan, scope_conflict, failed_subtask, order=order, control_state=control_state,
        recovery_generation_by_key=recovery_generation_by_key,
    )
    required = [p for p in participants if p.role is RecoveryParticipantRole.REQUIRED_MUTATION]
    unresolved = [p for p in participants if p.owner_resolution_basis == "unresolved"]
    if not required or unresolved:
        resolved_owners: Dict[str, List[str]] = {}
        for p in required:
            resolved_owners.setdefault(p.effective_owner_subtask_id, []).append(p.artifact)
        deps.logger.warning(
            "SCOPE_RECOVERY_OWNER_UNRESOLVED subtask=%s required_files=%s "
            "unresolved_artifacts=%s resolved_owners=%s - failing closed, no owner will be guessed.",
            failed_subtask.id, sorted(p.artifact for p in participants),
            sorted(p.artifact for p in unresolved), resolved_owners,
        )
        return None
    groups = _order_recovery_groups(plan, participants)
    if groups is None:
        deps.logger.warning(
            "RECOVERY_GROUP_ORDER_AMBIGUOUS subtask=%s owners=%s - failing closed, "
            "no arbitrary group order will be guessed.",
            failed_subtask.id, sorted({p.effective_owner_subtask_id for p in required}),
        )
        return None
    group_order = tuple(g.group_id for g in groups)
    return RecoveryExecutionPlan(
        id=f"recovery.{failed_subtask.id}.{plan_generation}",
        originating_subtask_id=failed_subtask.id,
        scope_conflict=scope_conflict,
        participants=participants,
        groups=groups,
        group_order=group_order,
        active_group_id=group_order[0] if group_order else None,
    )


async def _attempt_owner_recovery_self_correction(
    deps: OwnerRecoveryDeps,
    *,
    kernel: Any,
    developer_llm: Any,
    plan_workspace_path: str,
    workspace_path: str,
    owner: Subtask,
    required_owner_files: List[str],
    scope_conflict: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Targeted repair for grounded owner recovery (2026-08-30, PRV-06
    follow-up): reuses the existing, proven `self_correction.py` tool loop
    (read_file/apply_patch/recompile, already opt-in via `autonomy.
    self_correction_loop_enabled`) as a cheap, bounded FIRST attempt at a
    grounded owner-recovery fix, before falling through to the existing full
    Developer-generation owner-recovery path unchanged. No new agent, no new
    state machine, no change to any generation retry counter -
    self_correction_loop's own `max_turns` bound is independent and
    untouched.

    Why this, not exact-line highlighting (failure_grounding.py::
    extract_error_source_locations, already used for ordinary in-subtask
    retries): traced live against the PRV-06 2026-08-30 09:18-09:39 run - a
    cross-owner failure's own compiler locator names the CONSUMER file
    (where the symptom was detected: `AppTest.java:[29,12]` for a `pom.xml`/
    `App.java` requirement), never the owner's own file where the fix
    belongs - by construction, since that is the entire reason this is a
    cross-owner recovery rather than an ordinary same-file retry. Matching
    the locator's own file against the owner's required files would find
    zero matches for this whole failure class, so that mechanism was not
    reused here.

    CRITICAL safety property, found and fixed during design, not shipped
    broken: self_correction_loop's own `resolved` flag comes from
    `validator.run_compile_check()` scoped to the OWNER's own files - the
    exact same narrow, cross-owner-blind signal `owner_local_accepted`'s
    plain `quality_gates_passed` already is (confirmed live: App.java
    compiles fine standalone regardless of InMemoryService's visibility,
    which is exactly the PRV-06 defect this exists to fix). `resolved=True`
    is therefore NEVER treated as sufficient evidence on its own. This
    function returns an owner_result-shaped dict (status/quality_gates_
    passed/files, matching _invoke_bounded_subtask's own contract) ONLY
    when self-correction both resolved its own narrow gate AND genuinely
    modified at least one file - the caller then feeds this candidate
    through the EXACT SAME downstream pipeline (owner_undeclared check,
    candidate_fingerprint/RECOVERY_NO_PROGRESS, owner_local_accepted, and
    eventually the plan-level acceptance precheck/consumer retry) already
    built for the Developer-generation path, completely unchanged. A trivial
    "already fine, nothing to patch" resolve (modified_files empty) is
    rejected here directly rather than trusted, AND would additionally be
    caught downstream by RECOVERY_NO_PROGRESS even if this check were
    somehow bypassed - defense in depth, not a single point of trust.

    Returns None (caller falls through to the unchanged
    _invoke_bounded_subtask(execution_role="owner_recovery") path) when: the
    feature flag is off, there is no raw compiler/failure evidence to seed
    the loop, or self-correction did not resolve/modify anything within its
    own bounded turns."""
    if not getattr(kernel.config.autonomy, "self_correction_loop_enabled", False):
        return None
    raw_evidence = scope_conflict.get("raw_evidence") or ""
    if not raw_evidence.strip():
        return None

    from kriya.tools.validate import PolymorphicValidator
    run_self_correction_loop = deps.load_run_self_correction_loop()

    active_code_context_parts: List[str] = []
    for path in required_owner_files:
        full_path = os.path.join(plan_workspace_path, path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        active_code_context_parts.append(f"--- {path} ---\n{content}")
    if not active_code_context_parts:
        return None

    from kriya.workflow.toolchain import toolchain_declaration_mutable

    validator = PolymorphicValidator(
        plan_workspace_path, original_workspace_path=workspace_path,
        autonomy_cfg=kernel.config.autonomy,
        # PRD-011: the owner's own authorized files are the authority to
        # change a toolchain declaration here.
        toolchain_declaration_mutable=toolchain_declaration_mutable(
            "allowlist", [], None,
            extra_relpaths=list(required_owner_files) + [p.path for p in owner.planned_files],
        ),
    )
    result = await run_self_correction_loop(
        llm=developer_llm,
        worktree_path=plan_workspace_path,
        validator=validator,
        files_in_scope=sorted({pf.path for pf in owner.planned_files} | set(required_owner_files)),
        writable_files=list(required_owner_files),
        compile_error_output=raw_evidence,
        active_code_context="\n\n".join(active_code_context_parts),
        max_turns=kernel.config.autonomy.self_correction_loop_max_turns,
    )
    if not result.resolved or not result.modified_files:
        return None
    deps.logger.info(
        "OWNER_RECOVERY_SELF_CORRECTION_RESOLVED owner=%s files=%s turns=%d",
        owner.id, sorted(result.modified_files.keys()), result.turns_used,
    )
    return {
        "status": "success",
        "quality_gates_passed": True,
        "files": sorted(result.modified_files.keys()),
    }


def _integration_reference_token(path: str) -> str:
    """The stem of a file's basename (no directory, no extension) - e.g.
    "InMemoryService" from "src/main/java/com/example/InMemoryService.java",
    "service" from "app/repository.py". A deliberately crude, generic,
    language-agnostic proxy for "the identifier another file would use to
    reference this one" - no AST, no import-graph, no per-language parsing
    (Part C2's own explicit boundary: use existing plan/obligation
    machinery, never build a semantic dependency graph)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem


def _integration_evidence(
    plan: EngineeringPlan, rel: Any, consumer_id: str, producer_paths: List[str], consumer_paths: List[str],
    established_file_context: Dict[str, str], provenance: Any,
) -> Tuple[List[str], Dict[str, Any]]:
    """LR-R1-P5: the relationship judged on the artifacts that can
    legitimately satisfy it. Provider side: every required producer artifact
    must have been established by one of the relationship's declared
    producers and be unchanged in the workspace (a provider's local pass is
    not enough; another subtask's write of the same path does not count).
    Consumer side, unchanged criterion (whole-word reference to each provider
    artifact's stem) over the consumer's CURRENT planned artifacts - written
    this run, else unchanged on disk - so a verified no-change consumer is
    judged on what it actually contains. A consumer with no planned artifact
    in the relationship (verification-only) has nothing to reference: it is
    judged by the valid provider artifacts plus its own declared
    verification, which has passed when this runs (the check runs only after
    the consumer completed); with no declared verification it fails closed.
    Returns (missing provider artifacts, evidence fields)."""
    provider_evidence: Dict[str, Any] = {}
    invalid: List[str] = []
    for path in producer_paths:
        state, writer = provenance.provider_state(path, rel.producer_subtask_ids)
        provider_evidence[path] = {"state": state, "established_by": writer}
        if state != "valid":
            invalid.append(path)
    sources: Dict[str, str] = {}
    contents: List[str] = []
    for path in consumer_paths:
        if path in established_file_context and provenance.writer.get(path) in rel.consumer_subtask_ids:
            sources[path] = "written_this_run"
            contents.append(established_file_context[path])
            continue
        current = provenance.current_content(path)
        sources[path] = "current_workspace" if current is not None else "absent"
        if current is not None:
            contents.append(current)
    consumer = plan.subtask_by_id(consumer_id)
    verification = [f"{getattr(getattr(v, 'type', ''), 'value', getattr(v, 'type', ''))}:"
                    f"{getattr(v, 'tool_name', None) or getattr(v, 'description', '')}"
                    for v in (getattr(consumer, "verification", None) or [])]
    evidence: Dict[str, Any] = {
        "provider_evidence": provider_evidence,
        "consumer_evidence": {"paths": list(consumer_paths), "sources": sources, "verification": verification},
    }
    missing = list(invalid)
    if consumer_paths:
        evidence["evaluation"] = "reference"
        consumer_content = "\n".join(contents)
        for path in producer_paths:
            if path in invalid:
                continue
            token = _integration_reference_token(path)
            if not re.search(rf"\b{re.escape(token)}\b", consumer_content):
                missing.append(path)
    else:
        evidence["evaluation"] = "verification_only_consumer"
        if not verification:
            evidence["failure_reason"] = "consumer has no planned artifact and no declared verification"
            missing = sorted(set(missing) | set(producer_paths)) or ["<no consumer evidence>"]
    if missing and "failure_reason" not in evidence:
        evidence["failure_reason"] = "; ".join(
            f"{path}: {provider_evidence[path]['state'] if path in invalid else 'not referenced by the consumer'}"
            for path in missing if path in provider_evidence)
    return missing, evidence


def _evaluate_integration_obligations(
    deps: OwnerRecoveryDeps,
    plan: EngineeringPlan,
    obligation_ledger: Optional[ObligationLedger],
    completed_subtask_id: str,
    established_file_context: Dict[str, str],
    revision: Any,
    provenance: Optional[Any] = None,
) -> None:
    """Correctness Continuity Part C (PRV-06, 2026-08-29) - the deterministic
    evidence source that can transition a plan.integration.* obligation
    (seeded PENDING by plan_validation.validate_plan(), see its own
    docstring there) to SATISFIED or VIOLATED. Called once, right after a
    subtask finishes and its files land in established_file_context - see
    this module's own per-subtask loop for the call site.

    Deliberately narrow evidence: a whole-word textual reference from the
    token(s) of every relevant PRODUCER artifact's basename appearing
    somewhere in the CONSUMER subtask's own just-established file content -
    generic, cheap, and language-agnostic (Part C8: deterministic reference
    evidence preferred over any new LLM judge; Part C2: no semantic graph,
    no AST, no per-ecosystem adapter). This is intentionally a coarse
    proxy for "the consumer actually uses the producer," not a compiler -
    it can't confirm the reference is semantically correct, only that it
    exists at all. That is exactly the class of evidence PRV-06 showed
    Kriya had NONE of before this: App.java never once mentioned
    "InMemoryService" anywhere in its own content, which this check would
    have caught with zero false negatives on the real live incident.

    Only evaluates a relationship once it is still PENDING (never re-
    evaluates an already-SATISFIED/VIOLATED one - Part A's own evidence-
    monotonicity spirit applied here too: this module deliberately does not
    build upstream-change invalidation/stale-propagation, per Part C's own
    explicit "not built until MA9 ever does partial regeneration" boundary
    - see repair_contract.py's own three-level dependency model note)."""
    if obligation_ledger is None or not plan.integration_relationships:
        return
    for rel in plan.integration_relationships:
        if completed_subtask_id not in rel.consumer_subtask_ids:
            continue
        obligation_id = f"plan.integration.{rel.id}"
        current = obligation_ledger.current(obligation_id)
        if current is None or current.status != ObligationStatus.PENDING:
            continue
        producer_paths = [
            pf.path
            for pid in rel.producer_subtask_ids
            for pf in (plan.subtask_by_id(pid).planned_files if plan.subtask_by_id(pid) else [])
            if not rel.participating_artifacts or pf.path in rel.participating_artifacts
        ]
        consumer_paths = [
            pf.path
            for cid in rel.consumer_subtask_ids
            for pf in (plan.subtask_by_id(cid).planned_files if plan.subtask_by_id(cid) else [])
            if not rel.participating_artifacts or pf.path in rel.participating_artifacts
        ]
        if provenance is None:
            # Callers without the run's provenance: the established content only.
            consumer_content = "\n".join(
                established_file_context[p] for p in consumer_paths if p in established_file_context
            )
            missing_producers = []
            for producer_path in producer_paths:
                if producer_path not in established_file_context:
                    # The producer hasn't been established yet at all - cannot
                    # possibly have been integrated (Part C6's scheduling
                    # invariant: a consumer finalizing before its producer's
                    # contract exists is itself the defect this records).
                    missing_producers.append(producer_path)
                    continue
                token = _integration_reference_token(producer_path)
                if not re.search(rf"\b{re.escape(token)}\b", consumer_content):
                    missing_producers.append(producer_path)
            p5_evidence: Dict[str, Any] = {}
        else:
            missing_producers, p5_evidence = _integration_evidence(
                plan, rel, completed_subtask_id, producer_paths, consumer_paths, established_file_context, provenance,
            )
        satisfied = not missing_producers
        record = ObligationRecord(
            id=obligation_id,
            kind=ObligationKind.CROSS_SUBTASK_INTEGRATION,
            status=ObligationStatus.SATISFIED if satisfied else ObligationStatus.VIOLATED,
            authority=ObligationAuthority.DETERMINISTIC,
            description=current.description,
            source="workflow_controller.integration_check",
            revision=revision,
            evidence={
                **current.evidence,
                "missing_producer_references": missing_producers,
                **p5_evidence,
            },
            owner_subtask_id=current.owner_subtask_id,
            terminal_required=True,
            repair_scope=current.repair_scope,
        )
        obligation_ledger.record(record)
        deps.logger.info(
            "INTEGRATION_OBLIGATION_%s id=%s consumer=%s missing=%s",
            "SATISFIED" if satisfied else "VIOLATED",
            obligation_id, completed_subtask_id, missing_producers,
        )
        if provenance is not None:
            # LR-R1-P5: the decision and its evidence in the attempt-evidence
            # store (an ordinary mirrored run event; observational).
            from kriya.workflow.run_events import EventAuthority, RunEvent

            attempt_evidence_scope.mirror_event(RunEvent(
                kind="integration.obligation", attempt=0, source="workflow_controller.integration_check",
                authority=EventAuthority.AUTHORITATIVE,
                message=f"{obligation_id} {'SATISFIED' if satisfied else 'VIOLATED'}",
                details={"obligation_id": obligation_id, "relationship_kind": rel.kind,
                         "producer_subtask_ids": list(rel.producer_subtask_ids),
                         "consumer_subtask_ids": list(rel.consumer_subtask_ids),
                         "completed_consumer": completed_subtask_id,
                         "status": record.status.value, "missing_producer_references": missing_producers,
                         **p5_evidence},
            ))
