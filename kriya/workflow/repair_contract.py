"""MA9: Obligation-Driven Coordinated Repair.

Why this exists (PRV-06, 2026-08-28/29, forensic reconstruction of attempts
4-11): a violated ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY (kriya/
workflow/obligations.py) makes a cross-attempt conflict OBSERVABLE, but does
nothing to change how the Developer is asked to repair it. Every targeted
retry still called _build_targeted_retry_prompt(), whose own framing names
one "most likely responsible" file and tells the model the rest of the
codebase "is already correct" - actively wrong advice for a conflict whose
own resolution requires touching BOTH App.java (the process-terminating
entrypoint) and AppTest.java (the in-process caller) together. The Developer
correctly diagnosed the structural conflict in its own FIX ANALYSIS text
starting at attempt 4, but every actual edit still targeted exactly one of
the two files, so the retry loop oscillated (System.exit -> return ->
System.exit) for 11 attempts without ever proposing both changes in the same
candidate. Classified as Bucket A - a repair-intent formulation gap, not a
model-capability, write-scope, or edit-application limitation (both files
were already inside authorized write scope from early in the run).

This module is the single classification boundary (build_repair_contract())
between that failure shape and every other one. It is deliberately narrow:

  - Exactly one obligation kind can currently produce a COORDINATED
    RepairContract: ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY. Anything
    else returns None (ordinary LOCAL/existing single-file targeted retry
    behavior, completely unchanged) - not because other obligation kinds
    could never need this, but because this is the only one with live
    incident evidence demonstrating the need (same discipline
    ObligationKind's own docstring already applies to itself). Adding a
    second wired case later is meant to be a small, additive change here,
    not a redesign - see build_repair_contract()'s own docstring.
  - Evidence must be UNAMBIGUOUS (see derive_process_boundary_participants())
    or no contract is built at all - a signature scan that finds more than
    one plausible process-termination call site does not guess which one is
    responsible; it falls back to today's grounded-attribution LOCAL repair
    instead. Fabricating a coordinated scope from ambiguous evidence would
    trade one wrong failure mode (never coordinating) for a worse one
    (coordinating against the wrong file).
  - No new agent, no new Developer response protocol, no new persistence
    format. A RepairContract lives on GenerationState (kriya/workflow/
    state.py's `repair_contract` field) - in-memory, sticky across attempts
    within ONE active workflow run, exactly like every other per-attempt
    sticky field on that dataclass (api_contract_recovery, plan_scope_
    conflict). It is NOT checkpointed/resumed across a process crash in this
    version - see this module's own RepairContract docstring for why that's
    a deliberately deferred, not solved, question."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from kriya.workflow.obligations import ObligationKind, ObligationRecord


class RepairKind(str, Enum):
    LOCAL = "local"
    COORDINATED = "coordinated"


class RepairContractStatus(str, Enum):
    ACTIVE = "active"
    # Closed because the originating obligation itself became SATISFIED -
    # the only "successful" closure. Renamed from an earlier CLOSED (which
    # didn't distinguish why) per the 2026-08-29 v2 design review.
    SATISFIED = "satisfied"
    # Closed because the workflow gave up (retry budget/environment
    # exhaustion) while the originating obligation was STILL violated - see
    # _mark_repair_contract_abandoned() (retry_strategy.py) for the two
    # narrow, unambiguous "the loop is stopping now" points this is wired
    # to. Deliberately conservative: an orphaned ACTIVE contract at the end
    # of a run (a path this doesn't cover) is a disclosed limitation, not a
    # correctness bug - the run itself still fails closed correctly either
    # way, this status only affects observability/reporting.
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class RepairGroup:
    """A dependency-ordered subset of one RepairContract's participating
    artifacts - never a separate repair contract of its own (§11 of the
    2026-08-29 MA9 v2 design review). Derived generically by
    _derive_repair_groups() below from each participant's stack-neutral
    FileRole (kriya/workflow/generation_manifest.py - the SAME
    classification already used to order Kriya's own initial multi-file
    generation, reused here rather than inventing a second graph engine),
    never from any obligation-kind-specific field name.

    id: stable within one contract, e.g. "group.build"/"group.source".
    artifacts: this group's own members, in generation order.
    participant_roles: this group's subset of the contract's full
    participant_roles dict (domain-specific display labels like
    "termination_surface" - a DIFFERENT axis from the generic FileRole used
    to build the group itself).
    depends_on_group_ids: every group that must be generated before this
    one - in v1 always every group with a strictly higher generic-role
    priority (BUILD before MODEL before SOURCE before CONFIG before
    ENTRYPOINT before TEST), mirroring generation_manifest.py's own
    build_generation_manifest() dependency accumulation exactly."""

    id: str
    artifacts: Tuple[str, ...]
    participant_roles: Dict[str, str]
    depends_on_group_ids: Tuple[str, ...]
    generation_order: Tuple[str, ...]


@dataclass
class RepairContract:
    """One coherent, sticky repair transaction spanning more than one file -
    see this module's own docstring for why LOCAL (the existing single-file
    targeted retry) is insufficient for the obligation kind that produces
    this.

    Four deliberately distinct scopes (2026-08-29 v2 design review -
    upgraded from three to four; the added one is `active_group_id`/
    `repair_groups` below). Keep these four separate in any code/prompt/
    test that touches this object, never collapse them:

      authorized_write_scope (this dataclass - observability only; the
      real boundary is enforced entirely OUTSIDE this module, by
      AuthorizedFileWriter/ctx.allowed_write_relpaths, kriya/policy/
      filesystem.py): WHAT MAY CHANGE. This module never widens it; a
      participant this contract names that isn't yet write-authorized is
      denied by that existing boundary exactly like any other file would
      be, surfacing through the existing PLAN_SCOPE_DEFECT/plan_scope_
      conflict recovery path (kriya/workflow/retry_strategy.py) unchanged.
      Snapshotted onto the contract at construction time purely so a log/
      test/review can see "authorized 12, participating 8" without cross-
      referencing ctx - it is never consulted to make an authorization
      decision, only to report one already made elsewhere.

      participating_artifacts (this dataclass): WHAT BELONGS TO THE
      COHERENT TRANSFORMATION - every file the Developer must reason about
      together for this repair to make sense, whether or not a given
      attempt actually edits all of them (see "participant does not imply
      mandatory edit" below).

      repair_groups / active_group_id (this dataclass): THE SUBSET
      CURRENTLY BEING SYNTHESIZED OR VALIDATED TOGETHER - a dependency-
      ordered partition of participating_artifacts (see RepairGroup above),
      never a separate RepairContract. active_group_id is mutated by
      kriya/workflow/attempt.py's _run_coordinated_repair_generation() as
      it walks repair_groups in order, purely for observability (which
      group is "live" right now) - it plays no role in write authorization
      or candidate-view visibility, both of which already span the WHOLE
      contract regardless of which group is currently active.

      immediate_correction_targets (this dataclass, mutable across
      attempts): WHAT THE MOST RECENT FAILURE SPECIFICALLY IMPLICATES -
      retry-prompt emphasis only. Narrowing this must never narrow
      participating_artifacts or drop a participant from a coordinated
      generation pass - see kriya/workflow/attempt.py's
      _run_coordinated_repair_generation(), which always generates every
      participating_artifacts entry regardless of this field's value.

    "Participant does not imply mandatory edit": a coordinated generation
    pass calls the Developer once per participating_artifacts entry, but
    each call is free to answer NO CHANGE NEEDED (the same escape hatch
    _fill_missing_content's REPAIR mode already offers any single-file
    retry) - the invariant this contract exists to enforce is "reasoned
    about together," not "every file must be rewritten every attempt."

    generation_order: the flattening of every repair_groups entry's own
    generation_order, in group-dependency order - never arbitrary
    filesystem/dict ordering, which would make sequential candidate-view
    propagation (see this module's Rule 2A note on
    _run_coordinated_repair_generation) silently order-dependent in a way
    nothing documents or tests. See _derive_repair_groups() for how group/
    generation order is actually derived (stack-neutral FileRole
    classification, reused from generation_manifest.py - not a new graph
    engine, and not specific to any obligation kind's own evidence shape).

    expected_postconditions / required_verification: what "done" means for
    this contract, beyond "the originating obligation became SATISFIED"
    (which remains the actual closure authority - see §21 of the 2026-08-29
    v2 design review). Currently populated with the plain-language
    obligation-satisfaction condition itself; existing compile/test/runtime
    gates are the required_verification today, so this stays empty unless a
    future obligation kind needs something additional named explicitly.

    Persistence (2026-08-29 design review, explicitly scoped down from the
    original proposal): lives only on GenerationState, in-memory, for the
    lifetime of one active workflow run - sticky across repair ATTEMPTS
    (the evidenced PRV-06 need), not across a workflow interruption/resume.
    If a workflow is interrupted mid-repair, resume today falls back to
    ordinary resume behavior; the ORIGINATING ObligationRecord this contract
    derives from is itself already persisted via the existing
    ObligationLedger (MA8), so a fresh attempt after resume will simply
    re-derive an equivalent contract from that still-VIOLATED obligation
    the next time a test_process_terminated failure recurs - not a promise
    that mid-batch in-flight state survives a crash, just that the repair's
    OWN identity isn't lost. Whether that's sufficient, or genuine
    checkpoint persistence is needed, is exactly the kind of question a
    future crash/resume PRV should answer with live evidence, not this
    design guessing ahead of it."""

    id: str
    source_obligation_ids: Tuple[str, ...]
    kind: RepairKind
    repair_intent: str
    must_fix: Tuple[str, ...]
    must_preserve: Tuple[str, ...]
    participating_artifacts: Tuple[str, ...]
    participant_roles: Dict[str, str]
    repair_groups: Tuple[RepairGroup, ...]
    generation_order: Tuple[str, ...]
    created_attempt: int
    status: RepairContractStatus = RepairContractStatus.ACTIVE
    immediate_correction_targets: Tuple[str, ...] = field(default_factory=tuple)
    active_group_id: Optional[str] = None
    authorized_write_scope: Tuple[str, ...] = field(default_factory=tuple)
    expected_postconditions: Tuple[str, ...] = field(default_factory=tuple)
    required_verification: Tuple[str, ...] = field(default_factory=tuple)


# Deliberately a small, Java-only signature list (mirrors failure_grounding.
# py's own _PROCESS_TERMINATION_SIGNATURES precedent and its explicit "do not
# add unverified signatures for other stacks speculatively" rule) - these are
# SOURCE call-site patterns (System.exit(...) appearing in a file's own text),
# a different kind of evidence than that module's STDOUT crash-signature
# list, which detects the SYMPTOM in Surefire's own output, not the cause in
# a file's content.
_TERMINATION_CALL_SIGNATURES: Tuple[str, ...] = (
    "System.exit(",
    "Runtime.getRuntime().halt(",
)

_CRASHED_TESTS_RE = re.compile(r"Crashed tests:\s*\n(?:\[ERROR\]\s*)?([\w.$]+)")


def _resolve_class_name_to_path(class_name: str, known_files: Iterable[str]) -> Optional[str]:
    """Maps a (possibly dotted/fully-qualified) Java class name from
    Surefire's own "Crashed tests:" line to one of `known_files` by simple
    basename match - the same tier of evidence _build_java_main_class_map
    already relies on elsewhere in this codebase, not a new parser. Returns
    None (never a guess) when zero or more than one known file's basename
    matches, since an ambiguous match here would silently poison the
    "unambiguous evidence" requirement this module's whole design rests on."""
    simple_name = class_name.rsplit(".", 1)[-1]
    target_basename = f"{simple_name}.java"
    matches = [fp for fp in known_files if os.path.basename(fp) == target_basename]
    if len(matches) == 1:
        return matches[0]
    return None


def derive_process_boundary_participants(
    raw_output: str, worktree_path: str, known_files: Iterable[str],
) -> Optional[Dict[str, object]]:
    """Deterministic, no-LLM-call evidence extraction for a
    PROCESS_BOUNDARY_COMPATIBILITY violation - the CONSUMER (the crashed
    test, extractable from Surefire's own "Crashed tests:" line) and the
    PRODUCER (a static scan of `known_files`' own on-disk content for a
    process-termination call site).

    Returns None (never a guess) unless:
      - exactly one known file's basename matches the crashed test's simple
        class name (the consumer), AND
      - exactly one OTHER known file's content contains a known termination
        call signature (the producer/"termination surface") - MORE than one
        candidate is treated as ambiguous, not "pick the first," because a
        wrong producer guess actively misdirects a coordinated repair
        (2026-08-29 design review: "a signature scan finds ExitHandler.java,
        but that doesn't prove that particular termination surface caused
        this test failure" - see this module's own docstring).

    A caller with None back from this function must fall back to today's
    ordinary grounded-attribution LOCAL retry path - never widen scope on
    ambiguous evidence."""
    match = _CRASHED_TESTS_RE.search(raw_output or "")
    if not match:
        return None
    known_files = list(known_files)
    consumer = _resolve_class_name_to_path(match.group(1), known_files)
    if consumer is None:
        return None

    candidates: List[str] = []
    for filepath in sorted(known_files):
        if filepath == consumer:
            continue
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        if any(sig in content for sig in _TERMINATION_CALL_SIGNATURES):
            candidates.append(filepath)

    if len(candidates) != 1:
        return None
    return {
        "consumer": consumer,
        "producer": candidates[0],
        "termination_surface_candidates": tuple(candidates),
    }


@dataclass(frozen=True)
class _ContractShape:
    """An already-interpreted, obligation-kind-agnostic description of a
    coordinated repair - N participants, never assumed to be 2, and no
    ordering/grouping decision made yet (that's _derive_repair_groups'
    job, always, for every kind - see below). This is the boundary that
    keeps "how do we interpret this obligation kind's own raw evidence"
    (kind-specific, one small function per kind - WHO participates and
    what domain-specific role label each one gets) completely separate
    from "how do we order/group them and build a valid RepairContract"
    (generic, shared by every kind - _build_contract_from_shape below)."""

    participating_artifacts: Tuple[str, ...]
    participant_roles: Dict[str, str]
    repair_intent: str
    must_fix: Tuple[str, ...]
    must_preserve: Tuple[str, ...]
    expected_postconditions: Tuple[str, ...] = field(default_factory=tuple)


def _process_boundary_contract_shape(evidence: Dict[str, object]) -> Optional[_ContractShape]:
    """The ONE place that knows PROCESS_BOUNDARY_COMPATIBILITY evidence is
    currently only ever shaped as a 2-participant producer/consumer pair -
    see derive_process_boundary_participants()'s own docstring for why this
    detector is intentionally limited to that proven case today. A future
    obligation kind whose own evidence names 3+ participants (or a
    relationship with no producer/consumer framing at all) gets its OWN
    dedicated shape function here, built directly against whatever THAT
    kind's evidence actually contains - never routed through this one, and
    never requiring a change to it or to _build_contract_from_shape below.

    Deliberately does NOT decide generation order itself (dropped in the
    2026-08-29 v2 review) - _derive_repair_groups' stack-neutral FileRole
    classification (kriya/workflow/generation_manifest.py) independently
    orders the producer before the consumer for this case too: a crashed-
    test consumer is, by construction, always a TEST-role file (that's
    what PROCESS_BOUNDARY_COMPATIBILITY detects), and TEST is the last
    role in generation_manifest.py's own priority order - so the generic
    mechanism reproduces the evidence-grounded order without this function
    needing to assert it directly."""
    producer = evidence.get("producer")
    consumer = evidence.get("consumer")
    if not isinstance(producer, str) or not isinstance(consumer, str) or producer == consumer:
        return None
    return _ContractShape(
        participating_artifacts=tuple(sorted({producer, consumer})),
        participant_roles={producer: "termination_surface", consumer: "crashed_consumer"},
        repair_intent=(
            f"Resolve a process-boundary conflict: {producer} terminates the process on a "
            f"path {consumer} invokes in-process. Structurally separate the terminating call "
            "from the testable behavior (e.g. return a result/status instead of calling "
            "System.exit directly, with a thin wrapper performing the actual termination "
            "outside the tested path), or verify the terminating behavior out-of-process."
        ),
        must_fix=(
            f"{producer} must not terminate the process on any path {consumer} invokes in-process",
        ),
        must_preserve=(
            f"{consumer} must continue to exercise the same production behavior it already tests",
        ),
        expected_postconditions=(
            f"a test run covering {consumer} completes without terminating the test process",
        ),
    )


_CONTRACT_SHAPE_BUILDERS = {
    ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY: _process_boundary_contract_shape,
}


def _derive_repair_groups(
    participating_artifacts: Tuple[str, ...], participant_roles: Dict[str, str],
) -> Tuple[RepairGroup, ...]:
    """Stack-neutral, evidence-kind-agnostic grouping - reuses
    kriya/workflow/generation_manifest.py's own FileRole classification and
    _ROLE_PRIORITY (BUILD < MODEL < SOURCE < CONFIG < ENTRYPOINT < TEST),
    the SAME machinery Kriya already uses to order its own initial multi-
    file generation (build_generation_manifest()), not a new graph engine.
    `_BUILD_FILENAMES` there already covers pom.xml/build.gradle/
    package.json/requirements.txt/go.mod/Cargo.toml/Gemfile/composer.json
    uniformly - a build-manifest dependency issue (e.g. a Maven pom.xml
    needing a version bump alongside several source files) groups exactly
    like any other stack's manifest would, with zero per-language branching
    in this function.

    One RepairGroup per FileRole present among participating_artifacts,
    ordered by _ROLE_PRIORITY (mirroring build_generation_manifest()'s own
    ordering exactly, alphabetical tie-break within a role - the SAME tie-
    break that function already uses, not a new convention). Each group's
    depends_on_group_ids is every group with a strictly higher-priority
    role - a linear chain, matching build_generation_manifest()'s own
    dependency accumulation (SOURCE depends on BUILD; CONFIG depends on
    BUILD+MODEL+SOURCE; ...) and this module's own §11 worked example
    (domain/API contract -> mapper/repository -> service -> controller ->
    tests).

    Two or more participants sharing the same role (e.g. two SOURCE files
    with no role-priority separation between them) land in ONE group,
    generated together with mutual candidate visibility - the correct
    default per this module's own docstring ("files whose candidate states
    must remain coherent... should be grouped or ordered together") when
    no cross-role ordering signal exists between them."""
    from kriya.workflow.generation_manifest import _ROLE_PRIORITY, classify_file_role

    file_roles = {path: classify_file_role(path) for path in participating_artifacts}
    by_role: Dict[Any, List[str]] = {}
    for path in participating_artifacts:
        by_role.setdefault(file_roles[path], []).append(path)

    ordered_roles = sorted(by_role.keys(), key=lambda role: _ROLE_PRIORITY[role])
    groups: List[RepairGroup] = []
    prior_group_ids: Tuple[str, ...] = ()
    for role in ordered_roles:
        members = tuple(sorted(by_role[role]))
        group_id = f"group.{role.value}"
        groups.append(RepairGroup(
            id=group_id,
            artifacts=members,
            participant_roles={p: participant_roles.get(p, role.value) for p in members},
            depends_on_group_ids=prior_group_ids,
            generation_order=members,
        ))
        prior_group_ids = prior_group_ids + (group_id,)
    return tuple(groups)


def _build_contract_from_shape(
    obligation: ObligationRecord, shape: _ContractShape, *, created_attempt: int,
    authorized_write_scope: Tuple[str, ...] = (),
) -> Optional[RepairContract]:
    """Genuinely N-artifact: validates and assembles a RepairContract from
    an already-interpreted _ContractShape with no knowledge of any
    particular obligation kind, producer/consumer framing, or fixed
    participant count - 2 today only because that's what
    _process_boundary_contract_shape happens to produce, not because this
    constructor (or _derive_repair_groups, or the executor it feeds) assumes
    it. `_run_coordinated_repair_generation()` (attempt.py) and
    `_build_coordinated_retry_prompt()` (retry_prompts.py) both already
    iterate generically over repair_groups/participating_artifacts, so a
    future shape with more participants and real multi-role grouping needs
    no change on the executor side either - only a new shape function above
    and one more entry in _CONTRACT_SHAPE_BUILDERS."""
    if len(shape.participating_artifacts) < 2:
        return None
    repair_groups = _derive_repair_groups(shape.participating_artifacts, shape.participant_roles)
    generation_order = tuple(
        path for group in repair_groups for path in group.generation_order
    )
    if set(generation_order) != set(shape.participating_artifacts):
        return None
    return RepairContract(
        id=f"repair.{obligation.owner_subtask_id}.{obligation.id}",
        source_obligation_ids=(obligation.id,),
        kind=RepairKind.COORDINATED,
        repair_intent=shape.repair_intent,
        must_fix=shape.must_fix,
        must_preserve=shape.must_preserve,
        participating_artifacts=shape.participating_artifacts,
        participant_roles=shape.participant_roles,
        repair_groups=repair_groups,
        generation_order=generation_order,
        created_attempt=created_attempt,
        immediate_correction_targets=shape.participating_artifacts,
        active_group_id=repair_groups[0].id if repair_groups else None,
        authorized_write_scope=authorized_write_scope,
        expected_postconditions=shape.expected_postconditions,
        required_verification=(),
    )


def build_repair_contract(
    obligation: ObligationRecord,
    evidence: Optional[Dict[str, object]],
    *,
    created_attempt: int,
    authorized_write_scope: Tuple[str, ...] = (),
) -> Optional[RepairContract]:
    """The single classification boundary between LOCAL (existing behavior,
    unchanged - the overwhelming majority of every obligation kind and every
    ambiguous-evidence case) and COORDINATED (this module) - and the single
    dispatch point from a kind-specific evidence shape to the generic,
    N-artifact, group-aware constructor above. In v1 exactly one obligation
    kind has a wired shape function (PROCESS_BOUNDARY_COMPATIBILITY - itself
    intentionally limited to today's proven 2-participant producer/consumer
    case, see _process_boundary_contract_shape), not because
    _build_contract_from_shape can't handle more, but because no other
    obligation kind has live incident evidence yet. Adding a second
    evidenced kind later means adding one new shape function plus one new
    _CONTRACT_SHAPE_BUILDERS entry - deliberately a small closed lookup
    table, not a registry/plugin system (2026-08-29 design review: "no
    generalized obligation handler hierarchy... before we need it") - while
    the executor this dispatches into stays a real N-artifact, group-aware
    mechanism, not a two-artifact producer/consumer one.

    authorized_write_scope: purely an observability snapshot (see
    RepairContract's own docstring) - callers pass the write scope already
    established elsewhere (ctx.allowed_write_relpaths); this function never
    uses it to make or bypass an authorization decision."""
    if not evidence:
        return None
    shape_builder = _CONTRACT_SHAPE_BUILDERS.get(obligation.kind)
    if shape_builder is None:
        return None
    shape = shape_builder(evidence)
    if shape is None:
        return None
    return _build_contract_from_shape(
        obligation, shape, created_attempt=created_attempt,
        authorized_write_scope=authorized_write_scope,
    )
