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
from typing import Dict, Iterable, List, Optional, Tuple

from kriya.workflow.obligations import ObligationKind, ObligationRecord


class RepairKind(str, Enum):
    LOCAL = "local"
    COORDINATED = "coordinated"


class RepairContractStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass
class RepairContract:
    """One coherent, sticky repair transaction spanning more than one file -
    see this module's own docstring for why LOCAL (the existing single-file
    targeted retry) is insufficient for the obligation kind that produces
    this.

    Three deliberately distinct scopes (the user-facing architectural
    output of MA9, per the 2026-08-29 design review - keep these three
    separate in any code/prompt/test that touches this object, never
    collapse them):

      authorized_write_scope: governed entirely OUTSIDE this module, by
      AuthorizedFileWriter/ctx.allowed_write_relpaths (kriya/policy/
      filesystem.py) - WHAT MAY CHANGE. This module never widens it; a
      participant this contract names that isn't yet write-authorized is
      denied by that existing boundary exactly like any other file would
      be, surfacing through the existing PLAN_SCOPE_DEFECT/plan_scope_
      conflict recovery path (kriya/workflow/retry_strategy.py) unchanged.

      participating_artifacts (this dataclass): WHAT BELONGS TO THE
      COHERENT TRANSFORMATION - every file the Developer must reason about
      together for this repair to make sense, whether or not a given
      attempt actually edits all of them (see "participant does not imply
      mandatory edit" below).

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

    generation_order: deterministic, evidence-derived where a role is known
    (see derive_process_boundary_participants: termination surface before
    the crashed consumer, on the reasoning that the consumer's test-side fix,
    if any, should react to the resulting production API/behavior rather
    than the other way around) - never arbitrary filesystem/dict ordering,
    which would make sequential candidate-view propagation (see this
    module's Rule 2A note on _run_coordinated_repair_generation) silently
    order-dependent in a way nothing documents or tests.

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
    source_obligation_id: str
    kind: RepairKind
    repair_intent: str
    must_fix: Tuple[str, ...]
    must_preserve: Tuple[str, ...]
    participating_artifacts: Tuple[str, ...]
    participant_roles: Dict[str, str]
    generation_order: Tuple[str, ...]
    created_attempt: int
    status: RepairContractStatus = RepairContractStatus.ACTIVE
    immediate_correction_targets: Tuple[str, ...] = field(default_factory=tuple)


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
    coordinated repair - N participants, never assumed to be 2. This is the
    boundary that keeps "how do we interpret this obligation kind's own raw
    evidence" (kind-specific, one small function per kind) completely
    separate from "how do we build a valid RepairContract once evidence has
    been interpreted" (_build_contract_from_shape below - genuinely generic,
    shares zero producer/consumer-specific logic with any detector)."""

    participating_artifacts: Tuple[str, ...]
    generation_order: Tuple[str, ...]
    participant_roles: Dict[str, str]
    repair_intent: str
    must_fix: Tuple[str, ...]
    must_preserve: Tuple[str, ...]


def _process_boundary_contract_shape(evidence: Dict[str, object]) -> Optional[_ContractShape]:
    """The ONE place that knows PROCESS_BOUNDARY_COMPATIBILITY evidence is
    currently only ever shaped as a 2-participant producer/consumer pair -
    see derive_process_boundary_participants()'s own docstring for why this
    detector is intentionally limited to that proven case today. A future
    obligation kind whose own evidence names 3+ participants (or a
    relationship with no producer/consumer framing at all) gets its OWN
    dedicated shape function here, built directly against whatever THAT
    kind's evidence actually contains - never routed through this one, and
    never requiring a change to it or to _build_contract_from_shape below."""
    producer = evidence.get("producer")
    consumer = evidence.get("consumer")
    if not isinstance(producer, str) or not isinstance(consumer, str) or producer == consumer:
        return None
    return _ContractShape(
        participating_artifacts=tuple(sorted({producer, consumer})),
        generation_order=(producer, consumer),
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
    )


def _build_contract_from_shape(
    obligation: ObligationRecord, shape: _ContractShape, *, created_attempt: int,
) -> Optional[RepairContract]:
    """Genuinely N-artifact: validates and assembles a RepairContract from
    an already-interpreted _ContractShape with no knowledge of any
    particular obligation kind, producer/consumer framing, or fixed
    participant count - 2 today only because that's what
    _process_boundary_contract_shape happens to produce, not because this
    constructor assumes it. `_run_coordinated_repair_generation()`
    (attempt.py) and `_build_coordinated_retry_prompt()` (retry_prompts.py)
    both already iterate generically over participating_artifacts/
    generation_order, so a future shape with more participants needs no
    change on the executor side either - only a new shape function above
    and one more dispatch branch in build_repair_contract()."""
    if len(shape.participating_artifacts) < 2:
        return None
    if set(shape.generation_order) != set(shape.participating_artifacts):
        return None
    return RepairContract(
        id=f"repair.{obligation.owner_subtask_id}.{obligation.id}",
        source_obligation_id=obligation.id,
        kind=RepairKind.COORDINATED,
        repair_intent=shape.repair_intent,
        must_fix=shape.must_fix,
        must_preserve=shape.must_preserve,
        participating_artifacts=shape.participating_artifacts,
        participant_roles=shape.participant_roles,
        generation_order=shape.generation_order,
        created_attempt=created_attempt,
        immediate_correction_targets=shape.participating_artifacts,
    )


def build_repair_contract(
    obligation: ObligationRecord,
    evidence: Optional[Dict[str, object]],
    *,
    created_attempt: int,
) -> Optional[RepairContract]:
    """The single classification boundary between LOCAL (existing behavior,
    unchanged - the overwhelming majority of every obligation kind and every
    ambiguous-evidence case) and COORDINATED (this module) - and the single
    dispatch point from a kind-specific evidence shape to the generic,
    N-artifact constructor above. In v1 exactly one obligation kind has a
    wired shape function (PROCESS_BOUNDARY_COMPATIBILITY - itself
    intentionally limited to today's proven 2-participant producer/consumer
    case, see _process_boundary_contract_shape), not because
    _build_contract_from_shape can't handle more, but because no other
    obligation kind has live incident evidence yet. Adding a second
    evidenced kind later means adding one more `elif obligation.kind is
    ObligationKind.X:` branch here plus one new shape function - deliberately
    NOT a registry/plugin system (2026-08-29 design review: "no generalized
    obligation handler hierarchy... before we need it") - while the executor
    this dispatches into stays a real N-artifact mechanism, not a two-
    artifact producer/consumer one."""
    if not evidence:
        return None
    if obligation.kind is ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY:
        shape = _process_boundary_contract_shape(evidence)
    else:
        return None
    if shape is None:
        return None
    return _build_contract_from_shape(obligation, shape, created_attempt=created_attempt)
