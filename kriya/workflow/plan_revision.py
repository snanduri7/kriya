"""Authoritative plan revision: the plan surgery and the plan-graph queries recovery decides with (full
modularization W4, E11).

Moved verbatim from ``kriya/workflow/workflow_controller.py`` (module-level code; the enforce loop that calls it stays in
the controller):

- the plan revisions, each validated by the caller before it is executed: the grounded scope owner moved into the
  failed stage (``revise_plan_for_grounded_scope_owner`` with ``_transitive_dependents``/``_transitive_upstream_ids``),
  a discovered planned prerequisite wired upstream (``revise_plan_for_planned_prerequisite``) and a runtime plan gap
  minted on its package establisher (``revise_plan_for_runtime_plan_gap``, ``resolve_runtime_plan_gap_owner``,
  ``resolve_python_module_to_candidate_artifact_path``);
- the scope-conflict report and its files (``subtask_scope_conflict_report``, ``PLAN_SCOPE_REVISION_REQUIRED``,
  ``_plan_scope_conflict_files``);
- the plan-graph queries: the stages invalidated by reopening an owner (``transitive_subtask_dependents``) and the
  nearest completed mutating ancestor of a verification-only unit (``nearest_mutating_upstream_owner``).

Every revision, query and report is unchanged. ``kriya/workflow/workflow_controller.py`` keeps every one of these names
(a forwarder, or a re-export of the constant). What arrives in ``RevisionDeps``, resolved through
``kriya.workflow.workflow_controller`` by ``workflow_controller._revision_deps()`` when a forwarder runs: the
all-import-cycle ``topological_subtask_order`` (``subtask_checkpoint``). ``kriya/workflow/owner_recovery.py`` imports
``_plan_scope_conflict_files`` and ``_transitive_upstream_ids`` from here. This module never imports
``kriya.workflow.workflow_controller``, ``workflow``, ``attempt`` or ``kriya.cli``, nor any all-import-cycle module.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.workflow.plan_schema import EngineeringPlan, FileAction, FileOwnershipRelation, PlannedFile, Subtask


@dataclass(frozen=True)
class RevisionDeps:
    """What the moved plan-revision code may use, resolved through ``kriya.workflow.workflow_controller`` by
    ``workflow_controller._revision_deps()``. Every field is required."""

    # kriya.workflow.subtask_checkpoint (all-import cycle)
    topological_subtask_order: Callable[..., Any]


def transitive_subtask_dependents(deps: RevisionDeps, plan: EngineeringPlan, subtask_id: str) -> List[str]:
    """Return every downstream stage invalidated by reopening one owner."""
    dependents: set[str] = set()
    frontier = [subtask_id]
    while frontier:
        provider_id = frontier.pop()
        direct = [st.id for st in plan.subtasks if provider_id in st.depends_on]
        for dependent_id in direct:
            if dependent_id not in dependents:
                dependents.add(dependent_id)
                frontier.append(dependent_id)
    return [subtask_id_ for subtask_id_ in deps.topological_subtask_order(plan) if subtask_id_ in dependents]


def nearest_mutating_upstream_owner(
    plan: EngineeringPlan, subtask: Subtask, completed_ids: Sequence[str],
) -> Optional[Subtask]:
    """ENFORCE-VERIFICATION-UNIT-STOP-BEFORE-TERMINAL-GATE-001 (BACKEND-FINAL-
    CLOSURE-005, measured on P4-T4): the nearest completed depends_on
    ancestor of a verification-only unit that owns planned files - the one
    unit whose work the verification judged and that can act on its failure.
    Breadth-first over the plan's declared dependencies only (its own
    structure, never a name, a file's content or a model's word); None when
    no such ancestor exists."""
    completed = set(completed_ids)
    seen: set = set()
    frontier = list(subtask.depends_on)
    while frontier:
        next_frontier: List[str] = []
        for dependency_id in frontier:
            if dependency_id in seen:
                continue
            seen.add(dependency_id)
            dependency = plan.subtask_by_id(dependency_id)
            if dependency is None:
                continue
            if dependency.planned_files and dependency.id in completed:
                return dependency
            next_frontier.extend(dependency.depends_on)
        frontier = next_frontier
    return None


def _transitive_dependents(plan: EngineeringPlan, subtask_id: str) -> set[str]:
    """Every subtask that depends on `subtask_id`, directly or through a
    chain of other subtasks - i.e. everything that is DOWNSTREAM of it in
    the DAG. Used to keep a plan-surgery merge from introducing a cycle."""
    dependents: set[str] = set()
    frontier = [subtask_id]
    while frontier:
        current = frontier.pop()
        for st in plan.subtasks:
            if current in st.depends_on and st.id not in dependents:
                dependents.add(st.id)
                frontier.append(st.id)
    return dependents


def _transitive_upstream_ids(plan: EngineeringPlan, subtask_id: str) -> set[str]:
    """Every subtask `subtask_id` depends on, directly or through a chain -
    i.e. everything UPSTREAM of it in the DAG (the mirror of
    _transitive_dependents above, walking depends_on forward instead of
    finding reverse-dependents).

    2026-08-29 (PRV-06, MA8.1) - added after live evidence: the merge loop
    below already protects one direction (an owner's OWN depends_on
    pointing at something downstream of the failed stage - see
    dropped_owner_deps) but not the other. When a THIRD subtask that
    already depends on the deleted owner gets redirected to depend on
    `failed` instead, that redirect is only safe if the third subtask is
    NOT itself upstream of `failed` - otherwise `failed` already
    (transitively) depends on it, and pointing it at `failed` too closes a
    cycle: consumer -> failed -> ... -> consumer. Confirmed live: a
    grounded-owner plan revision reopening a build-manifest owner (pom.xml)
    for a downstream test subtask redirected the manifest's OTHER, earlier
    consumers (production-code subtasks upstream of the failing test stage)
    to depend on the failing test stage - inverting the original
    dependency direction and producing exactly this cycle, caught only by
    validate_plan()'s own after-the-fact structural check. See the merge
    loop's own use of this function for the fix - skip (drop, don't
    redirect) any such edge instead of constructing it."""
    upstream: set[str] = set()
    frontier = list(plan.subtask_by_id(subtask_id).depends_on) if plan.subtask_by_id(subtask_id) else []
    while frontier:
        current = frontier.pop()
        if current in upstream:
            continue
        upstream.add(current)
        dependency = plan.subtask_by_id(current)
        if dependency is not None:
            frontier.extend(dependency.depends_on)
    return upstream


PLAN_SCOPE_REVISION_REQUIRED = "PLAN_SCOPE_REVISION_REQUIRED"


def subtask_scope_conflict_report(scope_conflict: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    """PLAN-SCOPE-CONFLICT-REASON-TYPING-001: the error text and reason codes a
    subtask's ``plan_scope_conflict`` reports. Every conflict carries
    PLAN_SCOPE_REVISION_REQUIRED (the aggregate recovery the controller runs);
    one with its own ``reason_code`` (VERIFICATION_CONTRACT_REVISION_REQUIRED,
    RUNTIME_PLAN_GAP, PLANNED_PREREQUISITE_OWNER_REQUIRED,
    PLAN_TARGET_RELOCALIZATION_REQUIRED, ...) reports that code beside it, and
    one that names no files reports its own reason instead of an empty
    grounded-files sentence. Pure; the conflict itself is never changed."""
    own = scope_conflict.get("reason_code")
    own = own if isinstance(own, str) and own else None
    codes: Tuple[str, ...] = (PLAN_SCOPE_REVISION_REQUIRED,)
    if own and own != PLAN_SCOPE_REVISION_REQUIRED:
        codes += (own,)
    required = list(scope_conflict.get("required_files") or [])
    if own and not required:
        reason = str(scope_conflict.get("reason") or "").strip() or "no reason recorded"
        return f"subtask repair requires approved-plan revision ({own}): {reason}", codes
    return (
        "subtask repair requires approved-plan scope revision; grounded required "
        f"files {required!r} are outside this stage's "
        f"allowed files {list(scope_conflict.get('allowed_files') or [])!r}",
        codes,
    )


def _plan_scope_conflict_files(scope_conflict: Optional[Dict[str, object]]) -> List[str]:
    """The file(s) a PLAN_SCOPE_DEFECT scope_conflict names as needing
    authoritative plan revision - grounded_owner_files if present, else
    required_files.

    PRV-03 hardened (2026-08-27): a chain's SECOND hop can be attributed
    through a different path than its first. retry_strategy.py only
    populates grounded_owner_files when the offending attribution reached
    "architectural_owner" tier (its own strongest, most direct signal - a
    deterministic AuthorizedFileWriter write-scope denial always lands
    there). A later hop in the SAME chain can instead surface through a
    misdirected_edit-triggered lookup or another attribution path that
    never reaches that tier, leaving grounded_owner_files empty even though
    required_files is populated and classification is still
    PLAN_SCOPE_DEFECT - retry_strategy.py already required
    scope_conflict_is_grounded (high confidence, or fail_type ==
    "misdirected_edit") before setting plan_scope_conflict at all, so
    required_files is not a guess. Confirmed live: s2's second hop (needing
    Customer.java, attributed via a misdirected_edit lookup after the
    goal-spec-compliance failure) had grounded_owner_files == [] and
    required_files == ["...Customer.java"], which silently ended the
    revise-and-revalidate loop one hop early and fell to the weaker,
    upstream-only resolve_scope_conflict_owners() fallback instead.

    Safe to widen this way: revise_plan_for_grounded_scope_owner() itself
    re-derives ownership from the PLAN's own declared file_owner() (or
    treats a genuinely unowned path as newly owned by the failed subtask)
    rather than trusting either list blindly, and every revision this
    produces is revalidated by validate_plan() before being accepted - a
    spurious entry here fails that revalidation closed instead of silently
    corrupting the plan."""
    scope_conflict = scope_conflict or {}
    return list(
        scope_conflict.get("grounded_owner_files")
        or scope_conflict.get("required_files")
        or []
    )


def revise_plan_for_grounded_scope_owner(
    plan: EngineeringPlan,
    failed_subtask_id: str,
    grounded_files: List[str],
    workspace_path: str,
) -> EngineeringPlan:
    """Move deterministically grounded existing owners into the failed stage.

    This is plan surgery, not a retry hint: ownership remains unique, an
    emptied owner stage is merged into the failed stage, dependency edges are
    rewired, and the caller must validate the returned plan before execution.

    The grounded owner is not always upstream of the failed stage - a
    compliance gate can just as easily discover that a LATER stage (e.g. a
    controller three subtasks downstream) is the real owner of behavior the
    failed stage's goal requires. When that happens, naively unioning the
    owner's OWN depends_on into the failed stage's depends_on can point the
    failed stage at something that (transitively) already depends on it -
    an immediate cycle, silently rejected by validate_plan()'s acyclic
    check with no actionable signal for the caller. See
    _transitive_dependents below: any owner dependency already downstream
    of the failed stage is dropped rather than inherited - the failed stage
    keeps its own original ordering constraints (it already runs at its
    original position; a dependency that only existed to sequence the
    NOW-REMOVED owner stage relative to ITS neighbors is meaningless once
    that stage's scope has been absorbed here).
    """
    revised = plan.model_copy(deep=True)
    failed = revised.subtask_by_id(failed_subtask_id)
    if failed is None:
        raise ValueError(f"unknown failed subtask {failed_subtask_id!r}")

    grounded = list(dict.fromkeys(grounded_files))
    for path in grounded:
        if not os.path.isfile(os.path.join(workspace_path, path)):
            raise ValueError(f"grounded scope owner does not exist: {path}")
        # MA8.1 completion v3 (PRV-06, 2026-08-29): a path may legitimately
        # be declared by MULTIPLE subtasks at once (a validated sequential
        # ownership chain - e.g. one subtask creates, a later one modifies;
        # plan_schema.py's classify_file_ownership own docstring). The
        # single-owner-only file_owner() lookup this used to call returns
        # None for exactly that shape, which fell through to the
        # "genuinely new file" branch below WITHOUT stripping the path
        # from ANY of its real declared owners - producing an invalid plan
        # where the same path ended up declared on every original owner
        # AND the failed stage simultaneously (live PRV-06 evidence:
        # App.java on s1, s2, AND s3 at once - "planned file ownership
        # must be unique", the exact validation failure that then forced
        # a fallback to owner-recovery with no valid path forward either).
        # Re-derived directly here instead, over every declaring subtask.
        owners = [
            st for st in revised.subtasks
            if st.id != failed.id and any(pf.path == path for pf in st.planned_files)
        ]
        moved: Optional[PlannedFile] = None
        if owners:
            # The LAST declared owner's own PlannedFile metadata (action/
            # reason) seeds `moved` - a simple, sufficient heuristic since
            # actual content correctness is enforced by the merged
            # subtask's own compile/test Quality Gates afterward, not by
            # this planning-time metadata (the same acceptance already
            # applies to owner_requires below).
            source_owner = owners[-1]
            moved = next(pf for pf in source_owner.planned_files if pf.path == path)
            moved = moved.model_copy(update={
                "reason": "deterministically grounded architectural owner",
            })
            for owner in owners:
                owner.planned_files = [pf for pf in owner.planned_files if pf.path != path]
        else:
            moved = PlannedFile(
                path=path, action=FileAction.MODIFY,
                reason="deterministically grounded architectural owner",
            )
        if moved is not None and not any(pf.path == path for pf in failed.planned_files):
            failed.planned_files.append(moved)

        for owner in owners:
            if owner.planned_files:
                continue

            # The old stage has no independently executable scope after
            # moving its sole remaining declaration. Merge its contract and
            # remove it, preserving the DAG. owner.depends_on is only safe
            # to inherit when owner was UPSTREAM of failed; when it was
            # downstream (this is a forward-grounded discovery, not a
            # backward one), those dependencies are already reachable FROM
            # failed and inheriting them would create a cycle - drop those
            # instead of raising, since failed's own original position
            # already sequences it correctly relative to them.
            failed_downstream = _transitive_dependents(revised, failed.id)
            dropped_owner_deps = {
                dep for dep in owner.depends_on if dep in failed_downstream
            }
            failed.depends_on = list(dict.fromkeys(
                dep for dep in failed.depends_on + owner.depends_on
                if dep not in (failed.id, owner.id) and dep not in failed_downstream
            ))
            failed.acceptance_criteria_ids = list(dict.fromkeys(
                failed.acceptance_criteria_ids + owner.acceptance_criteria_ids
            ))
            existing_verification = {
                json.dumps(vm.model_dump(mode="json"), sort_keys=True)
                for vm in failed.verification
            }
            failed.verification.extend(
                vm for vm in owner.verification
                if json.dumps(vm.model_dump(mode="json"), sort_keys=True)
                not in existing_verification
            )
            failed.provides = list(dict.fromkeys(failed.provides + owner.provides))
            # A requires whose sole provider was one of dropped_owner_deps can no
            # longer be validly declared - keeping it would fail validate_plan's
            # SEMANTIC_DEPENDENCY_EDGE_MISSING check (requires X but doesn't
            # depend on X's provider) for exactly the edge we just had to drop
            # above to avoid a cycle. The capability itself may genuinely not be
            # ready yet; that risk is accepted the same way any other grounded-
            # owner recovery accepts it - actual correctness is still enforced
            # by the merged subtask's own compile/test Quality Gates, not by
            # this planning-time metadata alone.
            capability_providers: Dict[str, List[str]] = {}
            for st in revised.subtasks:
                for capability in st.provides:
                    capability_providers.setdefault(capability, []).append(st.id)
            owner_requires = [
                item for item in owner.requires
                if not (
                    len(capability_providers.get(item, [])) == 1
                    and capability_providers[item][0] in dropped_owner_deps
                )
            ]
            failed.requires = [
                item for item in dict.fromkeys(failed.requires + owner_requires)
                if item not in failed.provides
            ]
            failed.relevant_global_invariant_ids = list(dict.fromkeys(
                failed.relevant_global_invariant_ids + owner.relevant_global_invariant_ids
            ))
            revised.subtasks = [st for st in revised.subtasks if st.id != owner.id]
            # 2026-08-29 (PRV-06, MA8.1): computed BEFORE the redirect loop,
            # against the plan as it stood before this merge (owner already
            # removed above, but no depends_on rewritten yet) - see
            # _transitive_upstream_ids' own docstring for the live cyclic-DAG
            # incident this guards against.
            failed_upstream = _transitive_upstream_ids(revised, failed.id)
            for consumer in revised.subtasks:
                if owner.id not in consumer.depends_on:
                    continue
                if consumer.id in failed_upstream or consumer.id == failed.id:
                    # consumer already runs before (or is) failed - the merge
                    # doesn't need it to depend on anything for ordering, and
                    # redirecting it to depend on failed would invert direction
                    # and close a cycle (failed already transitively depends on
                    # it). Drop the now-dangling reference to the deleted owner
                    # instead of redirecting it.
                    consumer.depends_on = [dep for dep in consumer.depends_on if dep != owner.id]
                    continue
                consumer.depends_on = list(dict.fromkeys(
                    failed.id if dep == owner.id else dep for dep in consumer.depends_on
                    if (failed.id if dep == owner.id else dep) != consumer.id
                ))

    failed.description = (
        f"{failed.description} Authoritative scope revision: modify grounded owner(s) "
        f"{', '.join(grounded)}."
    )
    return EngineeringPlan.model_validate(revised.model_dump(mode="json"))


def revise_plan_for_planned_prerequisite(
    plan: EngineeringPlan,
    *,
    consumer_subtask_id: str,
    owner_subtask_id: str,
    capability: str,
    consumer_artifacts: Iterable[str] = (),
) -> EngineeringPlan:
    """Wire a discovered prerequisite upstream without moving ownership."""
    revised = plan.model_copy(deep=True)
    consumer = revised.subtask_by_id(consumer_subtask_id)
    owner = revised.subtask_by_id(owner_subtask_id)
    if consumer is None or owner is None:
        raise ValueError("planned prerequisite revision references an unknown subtask")
    if not capability or capability not in owner.provides:
        raise ValueError(
            f"planned prerequisite capability {capability!r} is not provided by {owner_subtask_id!r}"
        )
    if consumer.id == owner.id:
        raise ValueError("planned prerequisite owner and consumer must be different subtasks")

    consumer.depends_on = list(dict.fromkeys(consumer.depends_on + [owner.id]))
    consumer.requires = list(dict.fromkeys(consumer.requires + [capability]))
    artifact_paths = set(consumer_artifacts)
    for planned_file in consumer.planned_files:
        if planned_file.path in artifact_paths:
            planned_file.requires_capabilities = list(dict.fromkeys(
                planned_file.requires_capabilities + [capability]
            ))
    return EngineeringPlan.model_validate(revised.model_dump(mode="json"))


def resolve_python_module_to_candidate_artifact_path(
    module_name: str, workspace_path: str,
) -> Optional[str]:
    """Deterministic dotted-module -> physical-file resolution for Runtime-
    Evidence Plan Repair (PRV-17 Run 13, 2026-09-04 - see ObligationKind.
    RUNTIME_PLAN_GAP's own docstring for the live incident). Never guesses:
    Python module resolution genuinely has more than one physical shape (a
    plain module x/y.py vs. a package directory x/y/__init__.py), and this
    function returns a path only when exactly one interpretation is safe.

    Requires at least two dotted segments - a bare top-level name has no
    established parent package on disk to anchor against, exactly the
    "fresh single-file module OR the start of a brand-new package"
    ambiguity this function refuses to guess through.

    Requires the parent package directory to ALREADY exist on disk (real,
    established content, not merely planned) - with no established
    convention to resolve against, there is nothing to prefer one form
    over the other for.

    Prefers the ordinary module-file form (parent/leaf.py) over the
    package-directory form (parent/leaf/__init__.py): the module-file form
    never requires creating a new directory beyond what already exists -
    the strictly more conservative reading of what the evidence proves.
    Returns None (instead of guessing the module-file form) when EITHER
    candidate already exists on disk - the module already exists in some
    form, so this is a different bug, not a missing artifact this
    mechanism is scoped to repair."""
    parts = [p for p in module_name.split(".") if p]
    if len(parts) < 2:
        return None
    parent_dir = "/".join(parts[:-1])
    if not os.path.isdir(os.path.join(workspace_path, parent_dir)):
        return None
    leaf = parts[-1]
    module_file_real_path = os.path.join(workspace_path, parent_dir, f"{leaf}.py")
    package_dir_real_path = os.path.join(workspace_path, parent_dir, leaf)
    if os.path.exists(module_file_real_path) or os.path.exists(package_dir_real_path):
        return None
    return f"{parent_dir}/{leaf}.py"


def resolve_runtime_plan_gap_owner(
    plan: EngineeringPlan, candidate_path: str, failed_subtask: Subtask,
) -> Optional[str]:
    """Package-establisher ownership rule for Runtime-Evidence Plan Repair
    (PRV-17 Run 13, 2026-09-04): the subtask that owns candidate_path's
    parent package's own __init__.py is the one deterministic, language-
    convention (not framework-specific) signal for "who should own a new
    member of this package." Deliberately narrower than "any subtask
    owning any file under this directory" - PRV-17 Run 13's own audit
    found myproject/ owned by BOTH s1 (myproject/__init__.py,
    myproject/settings.py) and s3 (myproject/urls.py) in the live incident
    this closes; only __init__.py's owner is unambiguous.

    Returns None (never guesses) when: candidate_path has no parent
    directory (a root-level new module has no package-establisher to
    anchor to); __init__.py has no unique plan-declared owner
    (EngineeringPlan.file_owner() itself already returns None for a
    multi-declared path - see revise_plan_for_planned_prerequisite's own
    precedent, above); the owner IS the failing subtask itself (nothing to
    reopen); or the owner is not upstream (PAST_ORDERED) of the failing
    subtask - a downstream or unrelated "owner" is not a legal reopen
    target."""
    parent_dir = os.path.dirname(candidate_path)
    if not parent_dir:
        return None
    init_path = f"{parent_dir}/__init__.py"
    owner = plan.file_owner(init_path)
    if owner is None or owner.id == failed_subtask.id:
        return None
    if plan.classify_file_ownership(failed_subtask.id, init_path) != FileOwnershipRelation.PAST_ORDERED:
        return None
    return owner.id


def revise_plan_for_runtime_plan_gap(
    plan: EngineeringPlan, *, owner_subtask_id: str, artifact_path: str, reason: str,
) -> EngineeringPlan:
    """Mints exactly one new PlannedFile(action=CREATE) on an EXISTING,
    already-validated owner subtask - the one new kind of plan mutation
    Runtime-Evidence Plan Repair (PRV-17 Run 13, 2026-09-04) performs.
    Deliberately as small an edit as revise_plan_for_planned_prerequisite's
    own: no new subtask, no reordering, no touching any other subtask's
    planned_files/requires/depends_on. The caller's own validate_plan()
    call on the returned plan is what proves this bounded edit is safe -
    never this function's own judgment, matching the north-star invariant
    this whole mechanism exists to preserve: runtime evidence may prove
    the plan is incomplete, but only a validated plan revision may convert
    that evidence into new write authority."""
    revised = plan.model_copy(deep=True)
    owner = revised.subtask_by_id(owner_subtask_id)
    if owner is None:
        raise ValueError(f"runtime plan gap revision references unknown owner subtask {owner_subtask_id!r}")
    if any(pf.path == artifact_path for pf in owner.planned_files):
        raise ValueError(f"{artifact_path!r} is already planned by {owner_subtask_id!r}")
    owner.planned_files = owner.planned_files + [
        PlannedFile(path=artifact_path, action=FileAction.CREATE, reason=reason)
    ]
    return EngineeringPlan.model_validate(revised.model_dump(mode="json"))
