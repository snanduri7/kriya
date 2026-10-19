"""The candidate staging of one attempt as a transaction (full modularization W4, E10).

Moved verbatim from ``kriya/workflow/attempt.py``:

- run_attempt's four per-file statement regions, each a function here, run in today's order by ``CandidateTransaction``
  (run_attempt keeps the checks between them - the self-diagnosis capture, the unpreserved test obligation, the NO
  CHANGE attribution dispute, the RESTORE_PUBLIC_CONTRACT evidence restoration and the ownership-redirect restoration):
  the operation-contract loop (``_check_operation_contracts``), the originals capture (``_capture_originals``), the
  staging loop and its setup (``_stage_candidate_writes``), the batch commit and its bookkeeping
  (``_commit_staged_writes``);
- the staging helpers they and the Developer request use: the D1 completeness gate and the whole-file authority
  (``_completeness_gated_operation``, ``_is_restore_public_contract_phase``, ``_has_authoritative_full_source``,
  ``_current_revision``, ``_validate_actual_mutation_authority``, ``_operation_map``), the edit capability of one
  Developer invocation (``_decide_edit_capabilities``, ``_edit_capability_loci``, ``_structural_insertion_locus``,
  ``_requested_operation``, ``_current_edit_capability``, ``MAX_SYMBOL_LOCI_PER_FILE``), its response side
  (``_authorize_anchors``, ``_authorize_structural_insertion``, ``_sent_exact_source_covers``,
  ``_record_edit_protocol_failure``, ``_remember_anchor_loci``), the originals record (``_capture_original``) and the
  response-validity family (``_only_response_validity_failures``, ``_RESPONSE_VALIDITY_FAILURES``).

TODAY's atomic semantics, unchanged: the first refusal (an operation contract, a whole-file authority, the write scope,
the edit engine, a pre-flight check, the guarded commit) raises and refuses the whole attempt; nothing reaches the
sandbox before every file passed. ``CandidateTransaction`` adds no decision: it runs the same checks in the same order
and records, as a plain frozen ``FileProposal`` per file of the response, what happened to it, and the transaction's
``decision`` (``staged`` after the commit, ``refused`` when a phase raised) - G1/G2/G4 own the typed decision record,
the independence proof and any partial staging. Already moved by earlier slices (the plan's candidate-transaction row
also names them): ``_record_candidate_change`` (E8, gate_orchestrator) and ``_materialize_candidate_content`` (E7,
repair_orchestrator).

``kriya/workflow/attempt.py`` keeps every moved name (a forwarder, or a re-export of a constant) so a test patch on
``kriya.workflow.attempt`` keeps intercepting the moved code. What arrives in ``CandidateDeps``, resolved through
``kriya.workflow.attempt`` (``attempt._candidate_deps()``, called when a forwarder runs and at each transaction
phase): every name the bodies looked up that attempt.py defines (its helpers, the moved siblings' forwarders), every
name tests patch on ``kriya.workflow.attempt`` (``tests/architecture/test_patch_points.py::PATCH_POINTS``:
``apply_anchored_edits``, ``_reject_exception_masking``, ``_code_intelligence_for``), attempt.py's ``logger``, the names
of the kernel's all-import cycle (``operations``, ``attribution``, ``state``; ``edit_capability``, ``context_budget``
and ``insertion_locus`` through loaders called where the bodies imported them) and the AUTHORITY names attempt.py
imports from modules that are not AUTHORITY-public (``policy.errors``, ``policy.model``: their allowlisted edges stay
counted on attempt.py). Names of modules outside that cycle that never reach this module and that no test patches on
``kriya.workflow.attempt`` are imported here directly. This module never imports ``kriya.workflow.attempt``,
``workflow``, ``workflow_controller`` or ``kriya.cli``, nor any all-import-cycle module.

``RunState`` is the attempt's ``kriya.workflow.state.GenerationState`` and ``AttemptCtx`` its
``kriya.workflow.attempt.AttemptContext``; both are spelled ``Any`` (``kriya/workflow/stage_context.py`` says why the
state module is not imported; AttemptContext lives in attempt.py, OI-46), as is the ``operations.CodeOperation`` of the
signatures (an all-import-cycle module).
"""
import os
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from kriya.policy.filesystem import AuthorizedFileWriter, WriteScopeMode, normalize_workspace_relpath
from kriya.workflow.dependency_invalidation import invalidate_validated_revisions
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    content_revision,
    find_cross_file_type_conflict,
    find_structural_corruption,
    read_file_revision,
)
from kriya.workflow.exception_masking import find_unsafe_exception_masking
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.file_integrity import (
    DETERMINISTIC_FILE_INTEGRITY_STOPS,
    FileIntegrityError,
    display_text,
    file_raw_digest,
    keep_final_newline_state,
    load_snapshot,
    raw_digest,
    read_shown_text,
)
from kriya.workflow.file_resolution import find_explanatory_prose_contamination
from kriya.workflow.run_events import EventAuthority, RunEvent

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext


@dataclass(frozen=True)
class CandidateDeps:
    """What the moved candidate-staging code may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._candidate_deps()``. Every field is required: a builder that omits one fails at construction, never at the
    first use."""

    # attempt.py's logger
    logger: Any
    # names tests patch on kriya.workflow.attempt (PATCH_POINTS)
    _code_intelligence_for: Callable[..., Any]
    _reject_exception_masking: Callable[..., Any]
    apply_anchored_edits: Callable[..., Any]
    # the kernel's all-import cycle (operations, attribution, state) and the loaders of the bodies' function-local edit_capability / context_budget / insertion_locus imports
    APIContractRecoveryPhase: Any
    CodeOperation: Any
    find_edits_ignoring_own_diagnosis: Callable[..., Any]
    find_edits_ignoring_reported_line: Callable[..., Any]
    find_misdirected_edit_target: Callable[..., Any]
    find_whole_response_no_op: Callable[..., Any]
    load_allocation_window: Callable[[], Any]
    load_anchor_statuses: Callable[[], Any]
    load_capability_decision: Callable[[], Any]
    load_capability_loci: Callable[[], Any]
    load_locate_search_text: Callable[[], Any]
    load_resolve_insertion_locus: Callable[[], Any]
    load_verify_insertion: Callable[[], Any]
    operation_for_file: Callable[..., Any]
    validate_operation_result: Callable[..., Any]
    # AUTHORITY names attempt.py imports from non-public modules (policy.errors, policy.model; allowlisted on attempt.py)
    ActionRequest: Any
    ActionType: Any
    PolicyDecision: Any
    PolicyDeniedError: Any
    PolicyResult: Any
    # the moved siblings (their forwarders on attempt.py)
    _authorize_anchors: Callable[..., Any]
    _authorize_structural_insertion: Callable[..., Any]
    _capture_original: Callable[..., Any]
    _completeness_gated_operation: Callable[..., Any]
    _current_edit_capability: Callable[..., Any]
    _current_revision: Callable[..., Any]
    _edit_capability_loci: Callable[..., Any]
    _has_authoritative_full_source: Callable[..., Any]
    _is_restore_public_contract_phase: Callable[..., Any]
    _record_edit_protocol_failure: Callable[..., Any]
    _remember_anchor_loci: Callable[..., Any]
    _requested_operation: Callable[..., Any]
    _sent_exact_source_covers: Callable[..., Any]
    _structural_insertion_locus: Callable[..., Any]
    _validate_actual_mutation_authority: Callable[..., Any]
    # attempt.py's own names that stay there
    _build_workspace_type_index: Callable[..., Any]
    _chain_binding: Callable[..., Any]
    _dependency_graph_db_path: Callable[..., Any]
    _diagnosis_mismatch_bypass_reason: Callable[..., Any]
    _extract_class_names_best_effort: Callable[..., Any]
    _file_integrity_stop: Callable[..., Any]
    _preserved_attribution_diagnostics: Callable[..., Any]
    _preserved_authoritative_locator_files: Callable[..., Any]
    _record_authority_snapshot: Callable[..., Any]
    _reject_explanatory_prose: Callable[..., Any]
    _request_fallback_for_rejected_authoritative_target: Callable[..., Any]
    _target_exists: Callable[..., Any]
    # E14: what the bodies moved here in E14 read beyond the fields above - attempt.py's fallback-routing view and the
    # loader of the Code Intelligence opener's function-local kriya.workflow.graph_retrieval import (all-import cycle)
    _fallback_routing: Callable[..., Any]
    load_open_code_intelligence: Callable[[], Any]


def _capture_original(state: RunState, ctx: AttemptCtx, relpath: str) -> None:
    """Record, once per run, the exact workspace bytes of ``relpath`` (None
    when absent) and their display text. The bytes are the commit base
    revision (FILE-INTEGRITY-CONTRACT-001) and what a restoration writes back."""
    if relpath in state.all_original_raw:
        return
    try:
        with open(os.path.join(ctx.workspace_path, relpath), "rb") as handle:
            data: Optional[bytes] = handle.read()
    except (FileNotFoundError, IsADirectoryError):
        data = None
    except OSError:
        return
    state.all_original_raw[relpath] = data
    state.all_original_contents.setdefault(relpath, "" if data is None else display_text(data))


def _completeness_gated_operation(
    deps: CandidateDeps, filepath: str, base_operation: Any, *,
    file_exists: bool, ctx: AttemptCtx, state: Optional[RunState],
) -> Tuple[Any, bool]:
    """VAL-001 G1 D1 (2026-09-18): `permitted mutation precision <=
    authoritative current-source precision available to the Developer`, for
    an EXISTING file. Whole-file replacement (CREATE_FULL_FILE/
    REPAIR_WITH_FULL_FILE - never anything else; a new file, a targeted
    patch, or a no-change assessment carry no such risk and pass through
    unchanged) is authorized only when the model was actually shown a
    ContextItem for this exact path that is tier="full" (never a member
    slice - `member_id is None` - "member_exact" is real, exact evidence
    for the MEMBER it covers, never authority to rewrite the surrounding
    file), is_exact=True, and whose recorded revision still matches the
    file's real current content.

    Absence of a recorded ContextItem is NOT evidence of exactness - it is
    evidence of nothing, and the invariant reads "requires authoritative
    complete exact current source", not "requires proof of its absence" -
    so a file with no known_target_context_items entry fails closed to
    REPAIR_WITH_PATCH exactly like a recorded skeleton/signatures/
    member_exact/stale entry does. REPAIR_WITH_PATCH is not a workaround:
    apply_anchored_edits (kriya/workflow/attempt.py's own edit-application
    path) already refuses a SEARCH: block that doesn't match the real
    current content exactly once, so downgrading to it never authorizes an
    ungrounded mutation - it either succeeds against real content or fails
    closed on its own, through the exact same failure-handling path any
    other Quality Gate rejection already uses.

    Returns (operation, mandatory). `mandatory=True` means this downgrade is
    the invariant speaking, not an ordinary retry preference - a caller MUST
    additionally reject (not merely allow validate_operation_result's own,
    intentionally more permissive, patch-may-fall-back-to-full-file
    transition) a result that still resolves to a full-file shape for this
    file. See run_attempt()'s own operation-contract enforcement block,
    which applies that additional check using this same function's output.

    EXEMPTION (found while implementing this design, against the real
    existing test suite - not merely theorized): RESTORE_PUBLIC_CONTRACT is
    deterministic, never a Developer/LLM call at all -
    _restore_api_contract_owners_deterministically() writes
    state.all_original_contents[owner] (the exact, already-known baseline)
    verbatim, and its own docstring is explicit that its output is shaped
    identically to real Developer output specifically so downstream
    consumers "are completely unaware this attempt never called the
    Developer at all." This invariant is about the risk of PROBABILISTIC
    generation from incomplete context - a risk that provably does not
    exist for a deterministic byte-for-byte restoration of content Kriya
    itself already held before the risky candidate was even sandboxed. Gating
    it here would have rejected Kriya's own correct, hard-won PRV-11 fix
    (state.py's own restoration mechanism, 2026-08-30) as if it were an
    unsafe model response.
    """
    if not file_exists or base_operation not in (
        deps.CodeOperation.CREATE_FULL_FILE, deps.CodeOperation.REPAIR_WITH_FULL_FILE,
    ):
        return base_operation, False

    if deps._is_restore_public_contract_phase(state):
        return base_operation, False

    if deps._has_authoritative_full_source(filepath, ctx, state):
        return base_operation, False

    return deps.CodeOperation.REPAIR_WITH_PATCH, True


def _is_restore_public_contract_phase(deps: CandidateDeps, state: Optional[RunState]) -> bool:
    """D2's own deterministic-restoration exemption (see _completeness_
    gated_operation's own docstring for the full rationale) - extracted so
    _validate_actual_mutation_authority() below can share the EXACT same
    exemption, never a second, independently-maintained copy of it."""
    return bool(
        state is not None
        and state.api_contract_recovery is not None
        and state.api_contract_recovery.phase is deps.APIContractRecoveryPhase.RESTORE_PUBLIC_CONTRACT
    )


def _has_authoritative_full_source(
    filepath: str, ctx: AttemptCtx, state: Optional[RunState],
) -> bool:
    """VAL-001 G1 D1 (2026-09-18, extracted from _completeness_gated_
    operation's own original body - identical logic, now shared with
    _validate_actual_mutation_authority() below so REQUESTED-operation
    gating and ACTUAL-returned-shape gating can never independently drift
    on what "authoritative" means). True only when state.known_target_
    context_items records a ContextItem for this exact path that is
    tier="full" (never a member slice - member_id is None), is_exact=True,
    and whose recorded revision still matches the file's real CURRENT
    content (read fresh, worktree first, workspace fallback - never
    trusted from a stale in-memory copy). CONTENT EXACTNESS (is_exact=True
    for whatever the item represents) is deliberately a narrower claim
    than SOURCE AUTHORITY (safe to authorize a WHOLE-FILE mutation from) -
    this function is the one place that gap is closed, by additionally
    requiring tier=="full" and a live revision match; a member_exact item,
    or a full/exact item recorded under a since-changed revision, both
    correctly return False here despite each having is_exact=True on its
    own ContextItem."""
    item = state.known_target_context_items.get(filepath) if state is not None else None
    if item is None or item.tier != "full" or not item.is_exact or item.member_id is not None:
        return False
    full_path = os.path.join(ctx.worktree_path, filepath)
    if not os.path.exists(full_path):
        full_path = os.path.join(ctx.workspace_path, filepath)
    try:
        current_revision = read_shown_text(full_path)[1]
    except OSError:
        current_revision = None
    # An item with no recorded revision at all predates CTX-001 P1 WP3
    # (LEGACY_COMPATIBILITY default, see ContextItem.from_dict) - treated
    # as current rather than stale, matching that field's own documented
    # additive/optional contract; every real construction site
    # (build_known_target_context()) always sets a real revision.
    return not item.revision or current_revision == item.revision


def _current_revision(filepath: str, ctx: AttemptCtx) -> Optional[str]:
    full_path = os.path.join(ctx.worktree_path, filepath)
    if not os.path.exists(full_path):
        full_path = os.path.join(ctx.workspace_path, filepath)
    try:
        return read_shown_text(full_path)[1]
    except OSError:
        return None


def _validate_actual_mutation_authority(
    deps: CandidateDeps, filepath: str, actual_operation: Any, *,
    file_exists: bool, ctx: AttemptCtx, state: Optional[RunState],
) -> Optional[str]:
    """VAL-001 G1 D1-A (2026-09-18, closing the run d756a833/8cc2018a G1-R2
    incident): the authority decision for a whole-file mutation must be
    keyed on the operation the model ACTUALLY RETURNED (`actual_operation`,
    from validate_operation_result() - already parsed/interpreted), never
    on which operation was originally REQUESTED. "Requested operation
    cannot grant authority" - _completeness_gated_operation()'s own
    `mandatory_patch` output is about what SHOULD have been asked for, and
    is only ever True when the ATTEMPT's own base operation (attempt_
    operation, mode-derived) already happened to be full-file-shaped
    (CREATE_FULL_FILE/REPAIR_WITH_FULL_FILE) - a targeted/fallback_
    targeted-mode attempt's own base operation is UNCONDITIONALLY
    REPAIR_WITH_PATCH (operations.py::operation_for_attempt), so
    mandatory_patch is structurally always False for those modes,
    regardless of actual context completeness. The proven G1-R2 gap: a
    model in fallback_targeted mode that simply ignores the patch
    instruction and returns full file content anyway was NEVER checked
    against this invariant at all - mandatory_patch's own False value
    (about the REQUEST) was silently treated as license for the RESPONSE.

    This function is the independent, unconditional check on the RESPONSE
    itself - called for EVERY attempt, every mode, right after actual_
    operation is known, regardless of what mandatory_patch said. Shares
    the exact same _has_authoritative_full_source()/_is_restore_public_
    contract_phase() primitives _completeness_gated_operation() itself
    uses, so the two can never disagree about what "authoritative" means -
    only about WHEN they get to ask the question (request-time vs
    response-time).

    Returns None when authorized (not full-file-shaped at all; a genuinely
    new file with no existing source to be incomplete about; the
    deterministic RESTORE_PUBLIC_CONTRACT phase; or real, current,
    authoritative full source IS on record) - otherwise a rejection
    reason string, never raises itself (the caller decides how to turn
    this into a QualityGateFailure, matching every other gate in this
    module)."""
    if actual_operation not in (deps.CodeOperation.CREATE_FULL_FILE, deps.CodeOperation.REPAIR_WITH_FULL_FILE):
        return None
    if not file_exists:
        return None
    # CONTEXT-EDIT-PROTOCOL-001: the capability this invocation's contract
    # was built from decides, never a second evaluation - bound to the
    # revision it was decided for.
    capability = deps._current_edit_capability(state, filepath)
    if capability is not None:
        if capability.full_file and deps._current_revision(filepath, ctx) == capability.revision:
            return None
    elif deps._is_restore_public_contract_phase(state) or deps._has_authoritative_full_source(filepath, ctx, state):
        return None
    return (
        "a full-file replacement was returned, but this file's context this attempt was not "
        "authoritative, complete, exact current source (skeleton/signatures/member-only/stale "
        "revision/candidate-derived) - whole-file replacement requires authoritative pristine "
        "current source regardless of which operation was originally requested. Return an "
        "anchored SEARCH/REPLACE edit instead."
    )


def _operation_map(
    deps: CandidateDeps, ctx: AttemptCtx, filepaths: List[str], attempt_operation: Any,
    state: Optional[RunState] = None,
) -> Dict[str, Any]:
    operations = {
        filepath: deps.operation_for_file(
            attempt_operation, file_exists=deps._target_exists(ctx, filepath),
        )
        for filepath in filepaths
    }
    if state is not None:
        for filepath in filepaths:
            if (
                state.budgets.anchor_failure_counts.get(filepath, 0) >= 1
                and deps._target_exists(ctx, filepath)
            ):
                operations[filepath] = deps.CodeOperation.REPAIR_WITH_FULL_FILE
    for filepath in filepaths:
        operations[filepath], _mandatory = deps._completeness_gated_operation(
            filepath, operations[filepath],
            file_exists=deps._target_exists(ctx, filepath), ctx=ctx, state=state,
        )
    return operations


# CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001: how many structural localization
# candidates of one file may each open an exact window (the adopted targets
# first); the window budget is shared equally among all loci of the file.
MAX_SYMBOL_LOCI_PER_FILE = 2


def _edit_capability_loci(
    deps: CandidateDeps, state: RunState, ctx: AttemptCtx, path: str, lines: List[str], *,
    revision: Optional[str] = None, raw_digest: Optional[str] = None,
) -> List[int]:
    """Deterministic edit loci for ``path``: code the user's goal quotes,
    the lines the last failure names, where every rejected SEARCH block
    pointed in the real source (_remember_anchor_loci), and -
    CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001 - the declaration lines of the
    structural localization candidates for this file (ctx.retrieval_symbol_loci,
    only those bound to ``raw_digest``, the digest of the current bytes) and
    the first line of every grounded member the known-target package could
    not show whole (state.known_target_omitted_members, only those of
    ``revision``, the current shown revision). A locus of another revision
    authorizes nothing: the file changed since it was read."""
    (goal_code_fragments, locate_fragments) = deps.load_capability_loci()

    loci = locate_fragments(lines, goal_code_fragments(ctx.goal))
    failure = state.last_failure
    if failure is not None:
        loci.extend(loc.line for loc in failure.file_locations if loc.filepath == path and loc.line)
    loci.extend(state.edit_anchor_loci.get(path, ()))
    if raw_digest:
        current = [line for line, digest in ctx.retrieval_symbol_loci.get(path, ()) if digest == raw_digest]
        loci.extend(current[:MAX_SYMBOL_LOCI_PER_FILE])
    if revision:
        loci.extend(start for start, _end, member_revision in state.known_target_omitted_members.get(path, ())
                    if member_revision == revision)
    return loci


def _remember_anchor_loci(deps: CandidateDeps, state: RunState, path: str, edits: List[Dict[str, str]], current: str) -> None:
    """Where a rejected SEARCH block points in the real source, kept for
    every later invocation (an intervening stop must not forget it, or the
    capability could fall back to one already tried)."""
    locate_search_text = deps.load_locate_search_text()

    lines = current.splitlines()
    located = {locus for edit in edits for locus in locate_search_text(lines, edit.get("search", ""))}
    if located:
        state.edit_anchor_loci[path] = sorted(set(state.edit_anchor_loci.get(path, [])) | located)


# Failures that judge only the Developer's RESPONSE (its shape, its anchors,
# its consistency) - never evidence that a file is the cause of anything.
_RESPONSE_VALIDITY_FAILURES = frozenset({
    "operation_contract", "anchored_edit", "structural_corruption", "no_op_edit", "diagnosis_mismatch",
})


def _only_response_validity_failures(state: RunState) -> bool:
    """Positive evidence that every failure of this run so far only judged a
    malformed or inconsistent Developer response (its shape, anchors,
    consistency) - never a gate, locator or misdirection that is evidence
    about a file. Then a targeted retry's targets are simply the stage's own
    planned files, and a well-formed NO CHANGE NEEDED is answered by the
    gates exactly as on a first attempt (live: spring-petclinic s3 - two
    protocol failures, then NO CHANGE on its own test file, was turned into
    an attribution dispute, re-attributed to another stage's file and
    stranded). Unknown provenance (no recorded failure) is not that evidence."""
    family = state.budgets.last_failure_signature  # the active family; repair feedback inherits it
    failed = [outcome for outcome in state.gate_outcomes if outcome.get("success") is False]
    if family is None and not failed:
        return False
    if family and family[0] not in _RESPONSE_VALIDITY_FAILURES:
        return False
    return all(outcome.get("type") in _RESPONSE_VALIDITY_FAILURES for outcome in failed)


def _decide_edit_capabilities(deps: CandidateDeps, state: RunState, ctx: AttemptCtx, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """CONTEXT-EDIT-PROTOCOL-001: one EditCapability per existing target of
    this Developer invocation (kriya/workflow/edit_capability.py), decided
    before inference. Its exact windows join the mandatory context, the
    feasible operations go to the Developer contract, and the response
    validators read the same record. No feasible operation is a typed stop
    before inference; an edit-protocol failure that meets an unchanged
    capability is no progress."""
    allocation_window = deps.load_allocation_window()
    (ANCHOR_CONTEXT_NOT_ESCALATED, CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE, build_edit_capability, exact_window_reserve,
     render_exact_spans, shown_exact_texts) = deps.load_capability_decision()

    candidates = kwargs.get("known_target_files") or ctx.expected_files_upfront or []
    targets = []
    for path in dict.fromkeys(candidates):
        for root in (ctx.worktree_path, ctx.workspace_path):
            full = os.path.join(root, path) if root else None
            if full and os.path.isfile(full):
                targets.append((path, full))
                break
    if not targets:
        return {}
    binding = deps._chain_binding(ctx, kwargs.get("model_override"))
    model = kwargs.get("model_override") or ctx.kernel.config.llm.model
    # Only mandatory text is certain to be sent: an optional section may be
    # trimmed or dropped by the request fit, so it never makes source shown.
    mandatory_context = kwargs.get("existing_code_context") or ""
    for section in kwargs.get("optional_sections") or ():
        if section.text:
            mandatory_context = mandatory_context.replace(section.text, "", 1)
    budget_chars = exact_window_reserve(allocation_window(ctx.kernel.config, binding)) * 4 // len(targets)
    capabilities: Dict[str, Any] = {}
    for path, full in targets:
        content, content_shown_revision = read_shown_text(full)
        # Pieces already shown count only when Kriya's producer vouches for
        # their bytes (a member_exact slice; an implementation excerpt's head
        # and tail, exact by construction) and they are of the current revision.
        item = state.known_target_context_items.get(path)
        shown = (
            shown_exact_texts(item.tier, item.content)
            if item is not None and (item.is_exact or item.tier == "implementation_excerpt")
            and (not item.revision or item.revision == content_shown_revision) else []
        )
        # Whatever renderer put it there (a retry prompt's current content),
        # the whole current file present verbatim in this request's mandatory
        # text is exact anchor source - checked on the bytes, not on a label.
        # Whole-file replacement authority stays D1's alone.
        # Every other member unit rendered for the path (all members
        # localization grounded, every overload of an ambiguous name) counts
        # when it is of the current revision and its bytes are in this
        # request's mandatory text.
        for member in state.known_target_member_items.get(path, ()):
            if (member is not item and member.is_exact and member.content
                    and (not member.revision or member.revision == content_shown_revision)
                    and member.content in mandatory_context):
                shown.extend(shown_exact_texts(member.tier, member.content))
        if content.strip() and content in mandatory_context:
            shown = [("shown_full", content)]
        full_file = deps._is_restore_public_contract_phase(state) or deps._has_authoritative_full_source(path, ctx, state)
        capabilities[path] = build_edit_capability(
            path, content,
            full_file=full_file,
            loci=deps._edit_capability_loci(state, ctx, path, content.splitlines(), revision=content_shown_revision,
                                       raw_digest=file_raw_digest(full)),
            budget_chars=budget_chars,
            level=state.budgets.anchor_failure_counts.get(path, 0),
            shown=shown,
            insertion=None if full_file else deps._structural_insertion_locus(state, ctx, path, content),
        )
    if state.edit_capabilities_attempt != state.attempt_number:
        state.edit_capabilities = {}
        state.edit_capabilities_attempt = state.attempt_number
    state.edit_capabilities.update(capabilities)
    requested = {path: deps._requested_operation(kwargs, path) for path in capabilities}
    state.edit_capability_models.update({path: (model, requested[path]) for path in capabilities})
    state.record_event(RunEvent(
        kind="context.edit_capability",
        attempt=state.attempt_number,
        source="attempt._decide_edit_capabilities",
        authority=EventAuthority.ADVISORY,
        message="Mutation operations decided from this invocation's authoritative context.",
        details={"model": model, "budget_chars": budget_chars,
                 "targets": [capability.summary() for capability in capabilities.values()]},
    ))
    deps._record_authority_snapshot(state, ctx, kwargs, capabilities, requested, model)

    infeasible = [path for path, capability in capabilities.items() if not capability.feasible]
    if infeasible:
        detail = "; ".join(
            f"{path}: no authoritative full source, and no exact source for "
            + (f"located line(s) {list(capabilities[path].uncovered_loci)} within the window budget"
               if capabilities[path].uncovered_loci else "any located edit region")
            for path in infeasible
        )
        # PLAN-TARGET-LOCALIZATION-MISMATCH-001: before stopping on an editing error, ask the structural index where
        # the goal's named symbols are defined; a definition outside the approved write scope is a plan-scope
        # conflict with grounded locations (the existing plan revision + re-invocation mechanism takes it from here).
        from kriya.workflow.relocalization import relocalization_conflict

        conflict = relocalization_conflict(
            deps._code_intelligence_for(ctx), ctx.goal, infeasible_paths=infeasible, allowed_paths=ctx.allowed_write_relpaths,
            read_revision=lambda rel: read_file_revision(os.path.join(ctx.workspace_path, rel))
            if os.path.isfile(os.path.join(ctx.workspace_path, rel)) else None)
        if conflict is not None:
            state.plan_scope_conflict = conflict
            detail += "; " + conflict["reason"]
        message = (f"{CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE}: no mutation operation is feasible under the "
                   f"authoritative context of this Developer invocation - {detail}. Nothing was sent to the model.")
        deps.logger.error(message)
        raise QualityGateFailure(Failure(
            type="context_edit_protocol_unsatisfiable", message=message, raw_output=message,
            source="orchestrator", attempt=state.attempt_number, likely_files=infeasible,
            file_locations=[FileLocation(filepath=loc["filepath"], line=loc["line"])
                            for loc in (conflict or {}).get("grounded_locations", ())],
            diagnostics={"reason_code": CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE,
                         "capabilities": [capabilities[path].summary() for path in infeasible],
                         "relocalization": (conflict or {}).get("relocalization")},
        ))

    model = kwargs.get("model_override") or ctx.kernel.config.llm.model
    for path, capability in capabilities.items():
        previous = state.edit_failure_capability.get(path)
        if previous is not None and previous[1:] == (capability.digest, (model, requested[path])):
            refusal = (path, capability.digest, (model, requested[path]))
            repeated = refusal in state.edit_refused_capabilities
            state.edit_refused_capabilities.add(refusal)
            message = (f"{ANCHOR_CONTEXT_NOT_ESCALATED}: the last {previous[0]} failure in {path} would be "
                       f"retried on {model} with the same authoritative context, the same feasible operations "
                       f"({', '.join(capability.operations)}) and the same requested operation "
                       f"({requested[path]}); nothing new can be shown within the budget.")
            raise QualityGateFailure(Failure(
                type="no_progress_retry", message=message, raw_output=f"edit_capability={capability.digest}",
                source="orchestrator", attempt=state.attempt_number, mode=state.last_attempt_mode,
                likely_files=[path], diagnostics={"reason_code": ANCHOR_CONTEXT_NOT_ESCALATED,
                                                  "refusal_repeated": repeated},
            ))

    windows = "".join(render_exact_spans(capability) for capability in capabilities.values())
    if windows:
        kwargs["existing_code_context"] = (kwargs.get("existing_code_context") or "") + windows
    kwargs["edit_operations"] = {path: capability.operations for path, capability in capabilities.items()}
    return capabilities


def _structural_insertion_locus(deps: CandidateDeps, state: RunState, ctx: AttemptCtx, path: str, content: str) -> Any:
    """P3-D: the zero-width insertion locus for a new member the user's goal
    names (kriya/workflow/insertion_locus.py), decided from the bytes this
    invocation read and the members localization grounded - never from the
    plan or the model. None when there is none (recorded, never a guess)."""
    resolve_insertion_locus = deps.load_resolve_insertion_locus()

    grounded = []
    for item in [state.known_target_context_items.get(path), *state.known_target_member_items.get(path, ())]:
        if item is not None:
            grounded.extend(key for key in (item.type_id, item.member_id) if key)
    locus, reason = resolve_insertion_locus(
        path, content, goal=ctx.grounding_goal or ctx.goal, revision=content_revision(content),
        grounded_keys=grounded)
    if locus is None and reason:
        deps.logger.info("Structural insertion locus for %s: none (%s)", path, reason)
    return locus


def _requested_operation(kwargs: Dict[str, Any], path: str) -> Optional[str]:
    """The operation this invocation's contract requests for ``path``."""
    operation = (kwargs.get("operation_by_file") or {}).get(path, kwargs.get("default_operation"))
    return getattr(operation, "value", operation)


def _current_edit_capability(state: Optional[RunState], path: str) -> Any:
    """The EditCapability this attempt's Developer invocation decided for
    ``path``, or None (no invocation decided one this attempt)."""
    if state is None or state.edit_capabilities_attempt != state.attempt_number:
        return None
    return state.edit_capabilities.get(path)


def _record_edit_protocol_failure(deps: CandidateDeps, state: RunState, path: str, family: str) -> None:
    """Remember the capability, model and requested operation an
    edit-protocol failure happened under; it stands until an edit to
    ``path`` is accepted."""
    capability = deps._current_edit_capability(state, path)
    if capability is not None:
        state.edit_failure_capability[path] = (family, capability.digest, state.edit_capability_models.get(path))


def _authorize_anchors(deps: CandidateDeps, state: RunState, path: str, edits: List[Dict[str, str]], current: str) -> None:
    """The response side of the same capability: every SEARCH block must lie
    in the exact source this invocation was authorized to rely on. An anchor
    that is real but was never shown records its lines as loci for the next
    window; one that is not in the file at all is fabricated or stale."""
    (ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT, INSERTION_ONLY) = deps.load_anchor_statuses()

    capability = deps._current_edit_capability(state, path)
    if capability is None:
        return
    for index, edit in enumerate(edits, 1):
        status = capability.anchor_status(edit.get("search", ""), current)
        if status is None:
            continue
        if status == INSERTION_ONLY:
            deps._authorize_structural_insertion(state, capability, path, index, edit, current)
            continue
        if status == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT and deps._sent_exact_source_covers(
                state, capability, path, edit.get("search", ""), current):
            continue
        if status == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT:
            raise ValueError(
                f"{status}: Anchor matching failed for edit #{index}: the search block is real source, but not "
                "source this attempt was authorized to rely on (it was not in the exact current source shown). "
                "Its location is now shown as exact source."
            )
        raise ValueError(
            f"{status}: Anchor matching failed for edit #{index}: the search block does not occur in the "
            "current file (fabricated or stale). Copy SEARCH text only from the EXACT CURRENT SOURCE shown."
        )


def _authorize_structural_insertion(
    deps: CandidateDeps, state: RunState, capability: Any, path: str, index: int, edit: Dict[str, str], current: str,
) -> None:
    """P3-D: an edit anchored only on a structural insertion carrier is
    accepted only when, applied alone to the current bytes, it is a pure
    insertion at the locus of exactly this revision whose carrier the
    dispatched request carried (verify_insertion); anything else is refused
    with its typed reason and the existing source is never touched."""
    verify_insertion = deps.load_verify_insertion()

    locus = capability.insertion
    after = deps.apply_anchored_edits(current, [edit], "")
    problem = verify_insertion(locus, current, after, state.edit_capability_sent.get(path) or ())
    if problem is not None:
        raise ValueError(f"{problem} (edit #{index})")
    state.record_event(RunEvent(
        kind="context.structural_insertion_authorized", attempt=state.attempt_number,
        source="attempt._authorize_anchors", authority=EventAuthority.AUTHORITATIVE,
        message=f"edit #{index} in {path} is a pure insertion at the structural locus of {locus.owner_lookup_key}",
        details={"path": path, "locus": locus.summary(), "capability": capability.digest},
    ))


def _sent_exact_source_covers(state: RunState, capability: Any, path: str, search: str, current: str) -> bool:
    """P3-A: an anchor outside the capability's spans is still one the model
    relied on exact source for when the request this invocation actually
    dispatched (as fitted) carried, verbatim, the whole current file or a
    current exact member unit containing it - the byte rule
    _decide_edit_capabilities applies to mandatory text, applied to what was
    really sent (optional text the fit trimmed stays unseen). Only the
    capability's own revision qualifies, and it authorizes this anchor only:
    the operations offered, the write scope and full-file authority (D1's
    alone) are untouched."""
    from kriya.workflow.edit_safety import normalize_whitespace

    sent = state.edit_capability_sent.get(path) or ()
    revision = content_revision(current)
    if not sent or revision != capability.revision or not current.strip():
        return False
    pieces = [current] if any(current in prompt for prompt in sent) else []
    pieces.extend(
        member.content for member in state.known_target_member_items.get(path, ())
        if member.is_exact and member.content and (not member.revision or member.revision == revision)
        and any(member.content in prompt for prompt in sent))
    norm_search = normalize_whitespace(search)
    if not any(norm_search in normalize_whitespace(piece) for piece in pieces):
        return False
    state.record_event(RunEvent(
        kind="context.anchor_authorized_by_sent_request", attempt=state.attempt_number,
        source="attempt._authorize_anchors", authority=EventAuthority.AUTHORITATIVE,
        message=f"anchor in {path} lies in exact current source the dispatched request carried verbatim",
        details={"path": path, "revision": revision, "capability": capability.digest},
    ))
    return True


def _check_operation_contracts(deps: CandidateDeps, state: RunState, ctx: AttemptCtx, files: Iterable[Dict[str, Any]],
                               attempt_operation: Any) -> None:
    """run_attempt's per-file operation-contract loop (moved verbatim, E10): every file's response against the
    operation this attempt requested for it and against its whole-file authority, before any write is prepared.
    The first refusal raises (the whole attempt is refused)."""
    # Enforce the selected response contract before attribution heuristics or
    # writes.  Classification is based on the target's real existence, so the
    # same full-content shape is CREATE for an absent file and REPAIR for an
    # existing one.  Repair fallbacks are explicit and observable; creation has
    # no permissive fallback that could turn a missing file into a silent no-op.
    for file_obj in files:
        filepath = file_obj["filepath"]
        file_exists = deps._target_exists(ctx, filepath)
        expected_operation, mandatory_patch = deps._completeness_gated_operation(
            filepath,
            deps.operation_for_file(attempt_operation, file_exists=file_exists),
            file_exists=file_exists, ctx=ctx, state=state,
        )
        actual_operation, contract_error = deps.validate_operation_result(
            file_obj,
            expected=expected_operation,
            file_exists=file_exists,
        )
        if contract_error:
            failure = Failure(
                type="operation_contract",
                message=(
                    f"OPERATION CONTRACT FAILURE in {filepath}: {contract_error}. "
                    f"Return exactly the {expected_operation.value} response shape requested."
                ),
                raw_output=contract_error,
                file_locations=[FileLocation(filepath=filepath)],
                likely_files=[filepath],
                attempted_edits=file_obj.get("edits") or [],
                # Typed only when the producer named the violation (e.g. a `[]`
                # file-list answer given as file content). Deliberately NOT in
                # _DETERMINISTIC_VERDICT_REASON_CODES: it is model output, and a
                # resampled retry can genuinely return real content.
                diagnostics=(
                    {"reason_code": file_obj["protocol_reason_code"]}
                    if file_obj.get("protocol_reason_code") else None
                ),
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        # VAL-001 G1 D1-A (2026-09-18): independent of `mandatory_patch`
        # above (which only ever reflects the REQUESTED operation - see
        # _validate_actual_mutation_authority()'s own docstring for the
        # full G1-R2 incident this closes) - this checks the ACTUAL
        # returned shape, unconditionally, for every mode. validate_
        # operation_result()'s own PATCH -> FULL_FILE transition is
        # intentionally permissive at THAT layer (an ordinary targeted
        # retry may legitimately decide a small patch isn't enough) - this
        # is the layer that must never be permissive about a full-file
        # response reaching the sandbox without real authority for it,
        # PRE-WRITE (this whole loop runs strictly before the batch commit
        # further down in this function - see AuthorizedFileWriter.
        # commit_batch()'s own call site).
        authority_rejection_reason = (
            None if file_obj.get("_kriya_carried_forward_content")
            else deps._validate_actual_mutation_authority(
                filepath, actual_operation, file_exists=file_exists, ctx=ctx, state=state,
            )
        )
        if authority_rejection_reason is not None:
            state.record_event(RunEvent(
                kind="operation_authority.rejected",
                attempt=state.attempt_number,
                source="attempt.run_attempt",
                authority=EventAuthority.AUTHORITATIVE,
                operation=actual_operation.value,
                message=f"{filepath}: whole-file authority rejected - {authority_rejection_reason}",
                details={
                    "filepath": filepath,
                    "requested_operation": expected_operation.value,
                    "actual_operation": actual_operation.value,
                    "mandatory_patch_from_request": mandatory_patch,
                    "known_context_tier": (
                        state.known_target_context_items[filepath].tier
                        if filepath in state.known_target_context_items else None
                    ),
                    "known_context_is_exact": (
                        state.known_target_context_items[filepath].is_exact
                        if filepath in state.known_target_context_items else None
                    ),
                    "known_context_member_id": (
                        state.known_target_context_items[filepath].member_id
                        if filepath in state.known_target_context_items else None
                    ),
                    "known_context_revision": (
                        state.known_target_context_items[filepath].revision
                        if filepath in state.known_target_context_items else None
                    ),
                },
            ))
            failure = Failure(
                type="operation_contract",
                message=f"OPERATION CONTRACT FAILURE in {filepath}: {authority_rejection_reason}",
                raw_output=f"actual returned operation {actual_operation.value} lacks whole-file authority",
                file_locations=[FileLocation(filepath=filepath)],
                likely_files=[filepath],
                attempted_edits=file_obj.get("edits") or [],
                diagnostics={"reason_code": "ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"},
                attempt=state.attempt_number,
            )
            deps._record_edit_protocol_failure(state, filepath, failure.type)
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if actual_operation in (deps.CodeOperation.CREATE_FULL_FILE, deps.CodeOperation.REPAIR_WITH_FULL_FILE):
            state.edit_failure_capability.pop(filepath, None)  # an authorized whole-file edit was accepted
        # VAL-001 G1 D1-B: only reached once this file's actual mutation
        # shape has cleared authority - see this cache's own recording-site
        # comment above for why an unconditional/earlier recording would
        # reopen required authority case #6. A targeted/anchored-edit
        # response's own entries carry "edits", not "content" (resolved to
        # full content later by the anchored-edit pipeline further down) -
        # skipped here, not an error, exactly as the original P9-R1
        # recording did.
        _candidate_content = file_obj.get("content")
        if _candidate_content is not None:
            state.last_candidate_contents[filepath] = _candidate_content
        if actual_operation is not expected_operation:
            state.record_event(RunEvent(
                kind="operation.fallback",
                attempt=state.attempt_number,
                source="developer",
                authority=EventAuthority.ADVISORY,
                operation=actual_operation.value,
                message=(
                    f"{filepath}: accepted safe fallback from "
                    f"{expected_operation.value} to {actual_operation.value}."
                ),
                details={
                    "filepath": filepath,
                    "requested": expected_operation.value,
                    "returned": actual_operation.value,
                },
            ))


def _capture_originals(deps: CandidateDeps, state: RunState, ctx: AttemptCtx, files: Iterable[Dict[str, Any]]) -> None:
    """run_attempt's originals capture (moved verbatim, E10): the exact workspace bytes of every returned path,
    once per run, before any write."""
    # Read original file contents before overwriting (crucial for fallback mode diffs)
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        if not filepath:
            continue
        deps._capture_original(state, ctx, filepath)


def _stage_candidate_writes(deps: CandidateDeps, state: RunState, ctx: AttemptCtx, files: Iterable[Dict[str, Any]],
                            active_code_context: str, self_diagnosed: List[str]) -> List[StagedFileWrite]:
    """run_attempt's staging loop and its setup (moved verbatim, E10): per file, the write scope, the one edit engine
    and the cheap deterministic pre-flight checks, then the prepared write. Nothing reaches the sandbox here; the
    first refusal raises (the whole attempt is refused). Returns the prepared writes in response order."""
    # Write files to worktree sandbox
    state.files_written = []
    staged_writes: List[StagedFileWrite] = []
    # Built once per attempt, not once per file - see _build_workspace_type_index's
    # own docstring. Updated in place below as each new file is accepted, so two
    # files in the SAME batch that collide with each other are caught too.
    workspace_type_index = deps._build_workspace_type_index(state, ctx)
    # PRV-17 (2026-09-03): normalized ONCE per attempt, not per file - the
    # plan-declared allowlist doesn't change mid-attempt. Canonicalizes
    # trailing-slash/'./' formatting so 'customers_project/' (as the Planner
    # wrote it) and 'customers_project' (as the Developer reported it) are
    # recognized as the same target - see normalize_workspace_relpath's own
    # docstring for the live incident this closes.
    normalized_allowed_write_relpaths = {
        normalize_workspace_relpath(p) for p in ctx.allowed_write_relpaths
    }
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        content = file_obj.get("content", "")
        edits = file_obj.get("edits", [])
        analysis = file_obj.get("analysis")

        if not filepath:
            continue

        if (
            ctx.write_scope_mode == WriteScopeMode.ALLOWLIST
            and ctx.allowed_write_relpaths
            and normalize_workspace_relpath(filepath) not in normalized_allowed_write_relpaths
        ):
            # Correctness Continuity Part B4 (PRV-06, 2026-08-29): rejected
            # HERE, before apply_anchored_edits or any write is attempted -
            # distinct from, and in addition to, AuthorizedFileWriter's own
            # final write-scope enforcement at commit time later in this
            # pipeline (both layers remain; see WriteScopeMode's own
            # docstring for that one). This layer exists so an unauthorized
            # target never even gets a wasted anchored-edit attempt against
            # it. Live incident this closes: an owner-recovery attempt for
            # s2 (authorized scope = App.java only) also generated a SEARCH/
            # REPLACE for InMemoryService.java (owned by a different
            # subtask, s3) - apply_anchored_edits() was invoked on it anyway
            # and failed the whole retry attempt on content that was never
            # legally writable by this attempt in the first place.
            #
            # Raises the SAME PolicyDeniedError/reason_code AuthorizedFile
            # Writer.authorize() would eventually produce for this exact
            # target, rather than silently dropping the file_obj and letting
            # the REST of the batch proceed - MA9's own atomic-rejection
            # invariant ("an unauthorized coordinated participant denies the
            # WHOLE batch") must hold here too. Found and fixed during this
            # same change's own regression sweep: an earlier draft silently
            # `continue`d past the unauthorized file, which let an
            # OTHERWISE-authorized sibling in the same batch (e.g. App.java)
            # commit anyway - exactly the bypass MA9's own tests
            # (test_run_attempt_coordinated_repair_denies_unauthorized_
            # participant_atomically) exist to catch. Nothing in this
            # attempt's `staged_writes` has reached disk yet at this point
            # (that only happens in the later, single atomic commit step),
            # so raising here still leaves every file at its pre-attempt
            # baseline, matching the final write-scope gate's own contract
            # exactly.
            state.rejected_generation_targets.append(filepath)
            full_path = os.path.join(ctx.worktree_path, filepath)
            deps.logger.warning(
                "RECOVERY_GENERATION_TARGET_REJECTED filepath=%s reason=outside_authorized_scope "
                "allowed=%s", filepath, sorted(ctx.allowed_write_relpaths),
            )
            raise deps.PolicyDeniedError(
                request=deps.ActionRequest(action_type=deps.ActionType.WRITE_FILE, target=full_path),
                result=deps.PolicyResult(
                    decision=deps.PolicyDecision.DENY,
                    reason_code="FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE",
                    explanation=(
                        f"'{full_path}' is outside the validated subtask's allowed modification "
                        f"scope: {sorted(ctx.allowed_write_relpaths)!r}."
                    ),
                    matched_rule="filesystem.authorized_writer.validated_subtask_scope",
                ),
            )

        # FILE-INTEGRITY-CONTRACT-001: content and edits arrive here as
        # parsed protocol payload (kriya/agents/response_protocol.py) and are
        # never rewritten; the bytes written are the snapshot-bound result of
        # the one edit engine (kriya/workflow/file_integrity.py).
        full_path = os.path.join(ctx.worktree_path, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        if edits:
            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                current_file_path = os.path.join(ctx.workspace_path, filepath)

            source_snapshot = load_snapshot(current_file_path)
            orig_text = display_text(source_snapshot.raw_bytes)
            try:
                orig_text = source_snapshot.text
                deps._authorize_anchors(state, filepath, edits, orig_text)
                new_content = deps.apply_anchored_edits(orig_text, edits, active_code_context)
                new_bytes = source_snapshot.encode(new_content)
            except ValueError as anchor_ex:
                if getattr(anchor_ex, "reason_code", None) in DETERMINISTIC_FILE_INTEGRITY_STOPS:
                    raise deps._file_integrity_stop(state, filepath, anchor_ex) from anchor_ex
                # apply_anchored_edits() itself never receives a filepath, so its
                # raised ValueError never named one either - this failure class
                # always fell through to a blind full-set retry, unlike a compile
                # error (which self-names its file). filepath IS known right here,
                # in the caller's loop scope - capture it now instead of losing it.
                # orig_text (the real pre-edit content the SEARCH block was
                # supposed to match against) is already in memory - exactly what's
                # needed to debug an anchor mismatch, no disk re-read needed.
                # edits (the actual search/replace text that was attempted, already
                # sanitized) is captured alongside it as attempted_edits - together
                # both halves of "why didn't this match" are now persisted, not just
                # the generic "matched 0 times" message.
                #
                # Before accepting that generic explanation, check whether the
                # failed search block actually belongs to a DIFFERENT known file -
                # see find_misdirected_edit_target()'s own docstring for the real
                # incident (a-3, ignite_qpid_protocol, 2026-08-14) this closes: a
                # retry scoped to one file by a locator that could only ever name
                # the file where an explicit runtime check THREW, not the
                # different file whose method silently returned the wrong value,
                # produced a textbook-correct diagnosis whose edit could never
                # match the file it was constrained to. Reads every OTHER
                # already-written file straight from the worktree (nothing keeps
                # their content in memory this late in the per-file write loop).
                other_files: Dict[str, str] = {}
                # Unioned with ctx.established_files (see that field's own
                # docstring) - a misdirected edit could legitimately belong to
                # an established file from an earlier milestone, not just
                # another file this attempt itself wrote.
                for other_filepath in set(state.all_files_written) | set(ctx.established_files):
                    if other_filepath == filepath:
                        continue
                    other_full_path = os.path.join(ctx.worktree_path, other_filepath)
                    try:
                        with open(other_full_path, "r", encoding="utf-8", errors="replace") as fh:
                            other_files[other_filepath] = fh.read()
                    except OSError:
                        continue

                misdirected_target = deps.find_misdirected_edit_target(edits, orig_text, other_files)
                if misdirected_target:
                    # raw_output (not just message) carries both filenames - it's
                    # what to_gate_outcome() persists as "output" (raw_output or
                    # message) and what a future post-mortem reads from traces.db,
                    # same forensics goal as failed_content/attempted_edits below.
                    misdirected_explanation = (
                        f"the search block for {filepath} matched 0 times against "
                        f"{filepath}'s content, but was found instead inside {misdirected_target}"
                    )
                    failure = Failure(
                        type="misdirected_edit",
                        message=(
                            f"MISDIRECTED EDIT: {misdirected_explanation}. The fix you "
                            f"diagnosed likely belongs in {misdirected_target}, not {filepath} - "
                            f"target {misdirected_target} in your next edit (and {filepath} too, "
                            f"only if it genuinely also needs its own companion change)."
                        ),
                        raw_output=misdirected_explanation,
                        file_locations=[
                            FileLocation(filepath=filepath),
                            FileLocation(filepath=misdirected_target),
                        ],
                        likely_files=[filepath, misdirected_target],
                        failed_content={filepath: orig_text, misdirected_target: other_files[misdirected_target]},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure) from anchor_ex

                state.budgets.anchor_failure_counts[filepath] = (
                    state.budgets.anchor_failure_counts.get(filepath, 0) + 1
                )
                deps._record_edit_protocol_failure(state, filepath, "anchored_edit")
                deps._remember_anchor_loci(state, filepath, edits, orig_text)
                anchor_reason = str(anchor_ex).split(":", 1)[0]
                failure = Failure(
                    type="anchored_edit",
                    message=f"ANCHORED EDIT FAILURE in {filepath}: {anchor_ex}",
                    raw_output=str(anchor_ex),
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    diagnostics=({"reason_code": anchor_reason} if anchor_reason.isupper() else {}),
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure) from anchor_ex

            state.budgets.anchor_failure_counts[filepath] = 0
            state.edit_failure_capability.pop(filepath, None)

            # Layer 0 pre-flight check (see find_whole_response_no_op's own
            # docstring): purely structural, no analysis text or fail_type
            # dispatch needed - unlike Layers 1/2 below, a response whose
            # every edit is a byte-identical no-op is objectively useless
            # regardless of what it claims, so this is never bypassed and
            # never counts against the Layer 2 bounded veto (a different
            # check, a different question). apply_anchored_edits() above
            # already confirmed every search block matched real content -
            # this only fires when the edit(s) that matched changed nothing.
            if deps.find_whole_response_no_op(edits):
                preserved_locator_files = deps._preserved_authoritative_locator_files(
                    state, [filepath], self_diagnosed,
                )
                retry_files = list(self_diagnosed or preserved_locator_files) or [filepath]
                deps._request_fallback_for_rejected_authoritative_target(
                    state, ctx, preserved_locator_files,
                )
                failure = Failure(
                    type="no_op_edit",
                    message=(
                        f"NO-OP EDIT in {filepath}: every SEARCH/REPLACE pair in your response "
                        f"is byte-identical - this response changes nothing. If this file "
                        f"genuinely needs no change, use the response protocol's no-change outcome "
                        f"instead of an edit; otherwise your REPLACE text must actually differ "
                        f"from your SEARCH text."
                    ),
                    raw_output="every edit in the response was a no-op (search == replace)",
                    source="developer",
                    authority="advisory",
                    file_locations=[FileLocation(filepath=f) for f in retry_files],
                    likely_files=retry_files,
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    diagnostics=deps._preserved_attribution_diagnostics(preserved_locator_files),
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 1 pre-flight check (see find_edits_ignoring_reported_line's
            # own docstring): only meaningful when this attempt is itself
            # responding to a real prior error with a locatable line for this
            # file - error_context is "" on a clean first attempt, where
            # extract_error_source_locations() would find nothing anyway, but
            # the explicit guard avoids the line-matching work entirely there.
            #
            # Deterministic-validation-first (same principle as
            # _diagnosis_mismatch_bypass_reason, 2026-08-17): this check ONLY
            # ever fires when extract_error_source_locations() found a real
            # javac-style file:[line,col] locator for THIS file - i.e. it is
            # compile-scoped by construction, every single time, not just
            # sometimes (unlike diagnosis_mismatch, which fires for every
            # failure type). A real compile gate runs immediately after this
            # per-file write loop regardless, so it is always the cheaper,
            # authoritative answer to "does this edit actually still leave
            # the reported error in place" - no heuristic needed at all.
            # Confirmed both ways live via the corpus survey's replay of the
            # 2 DEBUG-capturable unaddressed_error_location occurrences
            # (b-10f, b-10n): b-10f was a FALSE positive - a companion edit
            # earlier in the same response fixed the bad import, which
            # transitively resolves the reported line's "cannot find symbol"
            # error too, but this check has no visibility into OTHER edits in
            # the same response and flagged the untouched (but now-harmless)
            # usage-site line anyway. b-10n was a genuine TRUE positive - the
            # model's own analysis said it would add a missing import and
            # never did - and bypassing here costs nothing there: the real
            # compile gate catches the exact same missing-import error one
            # step later, through the normal compile-failure retry path this
            # loop already handles well. Given every occurrence of this
            # check's own reported-line recurred identically across many
            # historical runs (10x/9x/6x for single line numbers - the same
            # small number of Ignite/Qpid goals hitting it over and over),
            # the false-positive cost of hard-blocking here was likely the
            # dominant driver of this failure category, not genuine misses.
            if state.error_context:
                ignored_lines = deps.find_edits_ignoring_reported_line(
                    orig_text, edits, filepath, state.error_context
                )
                if ignored_lines:
                    lines_desc = ", ".join(str(n) for n in sorted(ignored_lines))
                    deps.logger.info(
                        f"UNADDRESSED ERROR LOCATION pre-flight check bypassed for {filepath} "
                        f"(line(s) {lines_desc} of the reported error left unchanged): deferring "
                        f"to the real compiler instead of this heuristic, which cannot see whether "
                        f"a companion edit elsewhere in the same response already resolved it."
                    )

            # Structural corruption pre-flight check (see
            # find_structural_corruption's own docstring) - a cheap,
            # deterministic tripwire for the "obviously broken" shape
            # BOTH real corruption incidents this session actually had
            # (unbalanced braces from a folded-in duplicate class),
            # before the expensive compile gate spends itself
            # discovering the same thing.
            structural_problem = find_structural_corruption(filepath, new_content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=(
                        f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem} "
                        f"This usually means an edit's replace text accidentally folded in "
                        f"extra, unrelated content (e.g. a redundant full-file dump appended "
                        f"after the intended change). Re-check your SEARCH/REPLACE blocks - "
                        f"the replace text for each pair should contain ONLY the corrected "
                        f"version of that pair's search text, nothing else."
                    ),
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 2 pre-flight check (see find_edits_ignoring_own_diagnosis's own
            # docstring): the Layer 1 check above only catches an edit that left the
            # COMPILER's own reported line unchanged - it deliberately allows a fix
            # at a different, legitimate line (e.g. a declaration above the reported
            # use site). This catches the gap that leaves open: does the edit's own
            # content actually implement what its own FIX ANALYSIS just said, at ANY
            # line - found live, 2026-08-13, a real compile error recurred verbatim
            # across 3 targeted retries despite a textbook-correct analysis every time.
            diagnosis_mismatch = deps.find_edits_ignoring_own_diagnosis(analysis, edits, None, orig_text)
            if diagnosis_mismatch:
                bypass_reason = deps._diagnosis_mismatch_bypass_reason(state, ctx, filepath, new_content)
                if bypass_reason:
                    deps.logger.info(
                        f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                    )
                else:
                    # Counted here, at the actual rejection - see the bounded-
                    # veto policy's own comment in _diagnosis_mismatch_bypass_
                    # reason for why this exists.
                    state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                        state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                    )
                    failure = Failure(
                        type="diagnosis_mismatch",
                        message=(
                            f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                            f"Make the exact change your own analysis described - not a "
                            f"different or partial change."
                        ),
                        raw_output=diagnosis_mismatch,
                        file_locations=[FileLocation(filepath=filepath)],
                        likely_files=[filepath],
                        failed_content={filepath: orig_text},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            else:
                # This file's edit looked consistent with its own analysis -
                # a clean slate, so an unrelated mismatch found later in the
                # run for this same file still gets its own bounded veto.
                state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            deps._reject_explanatory_prose(state, filepath, new_content, orig_text)
            deps._reject_exception_masking(state, filepath, new_content, orig_text)
            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=new_content,
                base_path=current_file_path,
                expected_base_revision=source_snapshot.raw_sha256,
                content_bytes=new_bytes,
                mode=source_snapshot.file_mode,
            ))
        else:
            if content is None:
                continue

            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                workspace_file_path = os.path.join(ctx.workspace_path, filepath)
                if os.path.exists(workspace_file_path):
                    current_file_path = workspace_file_path
            # FILE-INTEGRITY-CONTRACT-001: a whole-file replacement of an
            # existing file keeps its BOM, line-ending convention and final
            # newline state, and is refused for a file the edit engine cannot
            # represent exactly (never re-encoded from a lossy view).
            source_snapshot = load_snapshot(current_file_path)
            file_is_new = not source_snapshot.exists
            prior_content = display_text(source_snapshot.raw_bytes)
            if not file_is_new:
                try:
                    source_snapshot.require_mutable()
                except FileIntegrityError as integrity_ex:
                    raise deps._file_integrity_stop(state, filepath, integrity_ex) from integrity_ex
                content = keep_final_newline_state(source_snapshot, content)
            try:
                content_bytes = source_snapshot.encode(content)
            except FileIntegrityError as integrity_ex:
                failure = Failure(
                    type="file_integrity", message=f"FILE INTEGRITY FAILURE in {filepath}: {integrity_ex}",
                    raw_output=str(integrity_ex), file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath], diagnostics={"reason_code": integrity_ex.reason_code},
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure) from integrity_ex

            structural_problem = find_structural_corruption(filepath, content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem}",
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: content},
                    attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Cross-file duplicate-type pre-flight check - only for a
            # genuinely NEW file (a REPAIR of a file that already legitimately
            # owns `filepath` is never a conflict, even if it re-declares its
            # own class). See find_cross_file_type_conflict's own docstring
            # for the live incident this prevents.
            candidate_type_names: List[str] = []
            if file_is_new and workspace_type_index:
                candidate_type_names = deps._extract_class_names_best_effort(
                    deps._dependency_graph_db_path(ctx), filepath, content,
                )
            if candidate_type_names:
                conflict = find_cross_file_type_conflict(filepath, candidate_type_names, workspace_type_index)
                if conflict:
                    # conflict[0] is an "ext:name" key (see extract_class_names'
                    # own docstring for why the extension is folded in) - strip
                    # it back to a plain class name for the human-facing message.
                    type_name = conflict[0].split(":", 1)[-1]
                    other_paths = conflict[1]
                    conflict_content: Dict[str, str] = {filepath: content}
                    for other_path in other_paths:
                        for base in (ctx.worktree_path, ctx.workspace_path):
                            other_full = os.path.join(base, other_path)
                            if os.path.exists(other_full):
                                with open(other_full, "r", encoding="utf-8", errors="replace") as fh:
                                    conflict_content[other_path] = fh.read()
                                break
                    failure = Failure(
                        type="duplicate_type_across_files",
                        message=(
                            f"DUPLICATE TYPE in {filepath}: '{type_name}' is already declared in "
                            f"{', '.join(other_paths)} (shown below). This is almost always a sign "
                            f"the existing file was never found or edited - target "
                            f"{other_paths[0]} directly instead of creating a new file. Only if "
                            f"'{type_name}' in {filepath} is genuinely a different, deliberately "
                            f"separate concept (rare) should you instead rename it to something "
                            f"unambiguous."
                        ),
                        raw_output=f"'{type_name}' declared in both {filepath} and {', '.join(other_paths)}",
                        file_locations=[FileLocation(filepath=filepath)] + [FileLocation(filepath=p) for p in other_paths],
                        likely_files=[filepath] + other_paths,
                        failed_content=conflict_content,
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                for name in candidate_type_names:
                    paths = workspace_type_index.setdefault(name, [])
                    if filepath not in paths:
                        paths.append(filepath)

            # Layer 2 pre-flight check - same rationale as the anchored-edit branch
            # above, applied to a full-file regeneration instead. Only reads the
            # file's current on-disk content when there's actually an analysis to
            # check against, to avoid the extra I/O on the common (no prior error)
            # case.
            if analysis:
                diagnosis_mismatch = deps.find_edits_ignoring_own_diagnosis(analysis, None, content, prior_content)
                if diagnosis_mismatch:
                    bypass_reason = deps._diagnosis_mismatch_bypass_reason(state, ctx, filepath, content)
                    if bypass_reason:
                        deps.logger.info(
                            f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                        )
                    else:
                        state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                            state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                        )
                        failure = Failure(
                            type="diagnosis_mismatch",
                            message=(
                                f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                                f"Make the exact change your own analysis described - not a "
                                f"different or partial change."
                            ),
                            raw_output=diagnosis_mismatch,
                            file_locations=[FileLocation(filepath=filepath)],
                            likely_files=[filepath],
                            failed_content={filepath: prior_content},
                            attempt=state.attempt_number,
                        )
                        state.record_gate_outcome(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                else:
                    state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            # A new file's prior content is empty: all of it is the candidate's.
            deps._reject_explanatory_prose(state, filepath, content, prior_content)
            deps._reject_exception_masking(state, filepath, content, prior_content)
            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=content,
                base_path=current_file_path,
                expected_base_revision=source_snapshot.raw_sha256,
                content_bytes=content_bytes,
                mode=source_snapshot.file_mode,
            ))
    return staged_writes


def _commit_staged_writes(deps: CandidateDeps, state: RunState, ctx: AttemptCtx, staged_writes: List[StagedFileWrite]) -> None:
    """run_attempt's batch commit and its bookkeeping (moved verbatim, E10): the one guarded write of every prepared
    write to the sandbox, then the candidate digests, the invalidated compiled revisions and the written/identical
    paths."""
    # Nothing reaches the sandbox until every candidate has passed its cheap
    # deterministic checks.  The batch commit re-checks all source revisions
    # before the first write and rolls back already-written targets if an OS
    # error or last-moment revision conflict interrupts the commit.
    # MA4.16 - AuthorizedFileWriter really enforces (raises PolicyDeniedError,
    # not audit-only) workspace containment + a narrow sensitive-path check
    # BEFORE any write in the batch, using the real ctx.worktree_path this
    # call site has always had in scope - propagates uncaught, same as
    # FileRevisionConflict/BatchCommitError already do from this call.
    AuthorizedFileWriter(
        ctx.worktree_path,
        protected_relpaths=(ctx.protected_relpath,) if ctx.protected_relpath else (),
        allowed_relpaths=ctx.allowed_write_relpaths,
        write_scope_mode=ctx.write_scope_mode,
    ).commit_batch(staged_writes)
    for staged in staged_writes:
        state.candidate_digests[os.path.relpath(staged.target_path, ctx.worktree_path)] = (
            None if staged.delete else raw_digest(
                staged.content_bytes if staged.content_bytes is not None else staged.content.encode("utf-8"))
        )
    changed_files = [
        os.path.relpath(staged.target_path, ctx.worktree_path)
        for staged in staged_writes
    ]
    invalidated = invalidate_validated_revisions(
        state.validated_file_revisions,
        changed_files,
        ctx.generation_dependencies,
    )
    if invalidated:
        state.record_event(RunEvent(
            kind="validation.invalidated",
            attempt=state.attempt_number,
            source="workflow",
            authority=EventAuthority.ADVISORY,
            message=(
                "Candidate changes invalidated compiled revisions for: "
                + ", ".join(invalidated)
            ),
            details={"changed_files": changed_files, "invalidated_files": invalidated},
        ))
    for staged in staged_writes:
        filepath = os.path.relpath(staged.target_path, ctx.worktree_path)
        if staged.delete:
            # PRD-022: an abandoned run-created parallel file is gone from
            # the candidate, so no later gate reads it.
            state.all_files_written.discard(filepath)
            state.identical_rewrites.discard(filepath)
            deps.logger.info(f"Removed abandoned candidate from sandbox: {filepath}")
            continue
        staged_bytes = staged.content_bytes if staged.content_bytes is not None else staged.content.encode("utf-8")
        if staged_bytes == state.all_original_raw.get(filepath):
            # ENFORCE-IDENTICAL-WRITE-COMPLETION-001 (BACKEND-FINAL-CLOSURE-005
            # cohort 2, C2-S4_B): the bytes equal the captured baseline bytes
            # (an identical rewrite, or this attempt restoring an earlier
            # attempt's change) - not a change. The path is not written for
            # this unit: nothing to apply, no gate reads it as the candidate's
            # own, and a planned one is settled by the verified no-change
            # contract from deterministic evidence (never by the write).
            state.all_files_written.discard(filepath)
            state.identical_rewrites.add(filepath)
            deps.logger.info(f"Candidate for {filepath} is byte-identical to the baseline: not a change.")
            continue
        state.identical_rewrites.discard(filepath)
        state.files_written.append(filepath)
        state.all_files_written.add(filepath)
        deps.logger.info(f"Committed generated/edited candidate to sandbox: {filepath}")


PROPOSAL_PARSES = ("ok", "invalid")
# What happened to one proposal: "refused" (this file's check refused the attempt), "prepared" (the staging loop
# prepared its write), "skipped" (the staging loop wrote nothing for it: no path, or no content and no edits),
# "staged" / "identical" (committed: a change / byte-identical to the baseline). None: never reached by the staging
# loop (the attempt was refused earlier, or by a file before it).
EDIT_RESULTS = ("refused", "prepared", "skipped", "staged", "identical")


@dataclass(frozen=True)
class FileProposal:
    """One file of the Developer's response as this attempt's transaction saw it (internal record; the typed decision
    record, its evidence kind and the per-finding fields are G1/G2's). ``response_seq``: its position in the response;
    ``parse``: "invalid" when the response parser refused the answer (``protocol_error``); ``reason_code``: the typed
    reason of the refusal it caused, else the parser's own (``protocol_reason_code``); ``staged_digest``: the raw
    digest of the committed bytes."""

    path: str
    response_seq: int
    parse: str
    reason_code: Optional[str]
    edit_result: Optional[str] = None
    staged_digest: Optional[str] = None


def _describe(seq: int, file_obj: Any) -> FileProposal:
    """A proposal from one response entry, read without judging it (never raises: a malformed entry is the contract
    loop's to refuse, as before)."""
    entry = file_obj if isinstance(file_obj, dict) else {}
    path = entry.get("filepath", "")
    reason = entry.get("protocol_reason_code")
    return FileProposal(path=path if isinstance(path, str) else "", response_seq=seq,
                        parse="invalid" if entry.get("protocol_error") else "ok",
                        reason_code=reason if isinstance(reason, str) and reason else None)


def _refusal_reason(error: BaseException) -> str:
    """The typed reason of a refusal: the failure's reason code, else its type; a policy denial's reason code; else
    the exception's class name."""
    failure = getattr(error, "failure", None)
    if failure is not None:
        diagnostics = getattr(failure, "diagnostics", None)
        code = diagnostics.get("reason_code") if isinstance(diagnostics, dict) else None
        if isinstance(code, str) and code:
            return code
        kind = getattr(failure, "type", None)
        if isinstance(kind, str) and kind:
            return kind
    code = getattr(getattr(error, "result", None), "reason_code", None)
    if isinstance(code, str) and code:
        return code
    return type(error).__name__


class CandidateTransaction:
    """One attempt's candidate staging: run_attempt's per-file loops as one transaction with TODAY's atomic semantics.

    ``run_attempt`` calls the phases in today's order - ``check_operation_contracts()``, ``capture_originals()``,
    ``stage(...)``, ``commit(...)`` - and keeps the checks between them. Each phase is the moved region, unchanged: the
    first refusal raises and refuses the whole attempt (nothing staged). The transaction only observes: it walks the
    response in order (so it knows which file a refusal came from) and records one ``FileProposal`` per file of the
    response, the ``decision`` (``"staged"`` once the commit returned, ``"refused"`` when a phase raised, None while
    open: run_attempt abandoned it before its commit, nothing staged) and the refusal (``reason_code``, ``refused``:
    the refusing file and reason). ``load_deps`` is ``attempt._candidate_deps``, called at each phase."""

    def __init__(self, load_deps: Callable[[], CandidateDeps], state: RunState, ctx: AttemptCtx,
                 files: List[Dict[str, Any]], attempt_operation: Any) -> None:
        self._load_deps = load_deps
        self._state, self._ctx, self._files, self._attempt_operation = state, ctx, files, attempt_operation
        self._proposals: List[FileProposal] = [_describe(seq, file_obj) for seq, file_obj in enumerate(files)]
        self._at: Optional[int] = None
        self.decision: Optional[str] = None
        self.reason_code: Optional[str] = None
        self.refused: Tuple[Tuple[str, str], ...] = ()

    @property
    def proposals(self) -> Tuple[FileProposal, ...]:
        return tuple(self._proposals)

    def _walk(self) -> Iterator[Dict[str, Any]]:
        """The response in order, remembering the file under evaluation (the one a refusal comes from)."""
        for seq, file_obj in enumerate(self._files):
            self._at = seq
            yield file_obj
        self._at = None

    def _refuse(self, error: BaseException) -> None:
        reason = _refusal_reason(error)
        self.decision, self.reason_code = "refused", reason
        if self._at is not None:
            proposal = self._proposals[self._at]
            self._proposals[self._at] = replace(proposal, edit_result="refused", reason_code=reason)
            self.refused = ((proposal.path, reason),)

    def _phase(self, run: Callable[[], Any]) -> Any:
        self._at = None
        try:
            return run()
        except Exception as error:
            self._refuse(error)
            raise

    def check_operation_contracts(self) -> None:
        self._phase(lambda: _check_operation_contracts(
            self._load_deps(), self._state, self._ctx, self._walk(), self._attempt_operation))

    def capture_originals(self) -> None:
        self._phase(lambda: _capture_originals(self._load_deps(), self._state, self._ctx, self._walk()))

    def stage(self, active_code_context: str, self_diagnosed: List[str]) -> List[StagedFileWrite]:
        staged_writes = self._phase(lambda: _stage_candidate_writes(
            self._load_deps(), self._state, self._ctx, self._walk(), active_code_context, self_diagnosed))
        targets = {staged.target_path for staged in staged_writes}
        for seq, proposal in enumerate(self._proposals):
            prepared = bool(proposal.path) and os.path.join(self._ctx.worktree_path, proposal.path) in targets
            self._proposals[seq] = replace(proposal, edit_result="prepared" if prepared else "skipped")
        return staged_writes

    def commit(self, staged_writes: List[StagedFileWrite]) -> None:
        self._phase(lambda: _commit_staged_writes(self._load_deps(), self._state, self._ctx, staged_writes))
        for seq, proposal in enumerate(self._proposals):
            if proposal.edit_result != "prepared":
                continue
            relpath = os.path.relpath(os.path.join(self._ctx.worktree_path, proposal.path), self._ctx.worktree_path)
            self._proposals[seq] = replace(
                proposal, edit_result="identical" if relpath in self._state.identical_rewrites else "staged",
                staged_digest=self._state.candidate_digests.get(relpath))
        self.decision = "staged"


# ----------------------------------------------------------------------------- E14 (OI-58, OI-50): moved verbatim from
# kriya/workflow/attempt.py - the staging loop's pre-flight checks and the NO CHANGE / no-op attribution helpers it shares
# with run_attempt, the baseline restoration write, the target-exists check, the Developer request's D1 patch-only set
# and the read-only Code Intelligence opener (CandidateDeps, GenerationDeps, FallbackDeps, SpecDeps and run_attempt
# reach them through attempt.py's forwarders).

def _baseline_restoration_write(state: RunState, target_path: str, relpath: str) -> StagedFileWrite:
    """Restore ``relpath`` to its exact original bytes, based on the target's
    current raw revision (re-checked by the batch commit)."""
    original = state.all_original_raw.get(relpath)
    data = original if original is not None else state.all_original_contents[relpath].encode("utf-8")
    return StagedFileWrite(
        target_path=target_path, content=display_text(data), base_path=target_path,
        expected_base_revision=file_raw_digest(target_path), content_bytes=data,
    )


def _file_integrity_stop(state: RunState, filepath: str, error: Any) -> "QualityGateFailure":
    """FILE-INTEGRITY-CONTRACT-001: a file the edit engine cannot mutate
    byte-exactly (not UTF-8, mixed line endings, a symbolic link). No
    Developer retry can change the file itself, so this is a deterministic
    stop (retry_strategy: ``file_integrity_unsupported``), never a retry."""
    message = str(error)
    failure = Failure(
        type="file_integrity_unsupported", message=message, raw_output=message,
        source="orchestrator", authority="deterministic", attempt=state.attempt_number,
        file_locations=[FileLocation(filepath=filepath)], likely_files=[filepath],
        diagnostics={"reason_code": getattr(error, "reason_code", None)},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    return QualityGateFailure(failure)


def _target_exists(ctx: AttemptCtx, filepath: str) -> bool:
    return os.path.exists(os.path.join(ctx.worktree_path, filepath)) or os.path.exists(
        os.path.join(ctx.workspace_path, filepath)
    )


def _preserved_authoritative_locator_files(
    state: RunState, target_files: List[str], self_diagnosed: List[str],
) -> List[str]:
    """Return current targets backed by the preceding authoritative locator.

    Developer analysis is advisory and cannot erase deterministic evidence.
    A different file explicitly named by current-turn analysis is allowed to
    redirect (root cause and surfacing location may differ), so preservation
    applies only when there is no such alternate.  Intersecting with this
    attempt's actual targets prevents stale locator evidence from widening or
    redirecting a later, unrelated repair.
    """
    prior_attribution = state.last_attribution
    prior_failure = state.last_failure
    if (
        self_diagnosed
        or state.last_attempt_mode not in ("targeted", "fallback_targeted")
        or prior_attribution is None
        or getattr(prior_attribution, "tier", None) != "locator"
        or prior_failure is None
        or getattr(prior_failure, "authority", None) != "authoritative"
    ):
        return []
    return [
        filepath for filepath in getattr(prior_attribution, "files", [])
        if filepath in target_files
    ]


def _request_fallback_for_rejected_authoritative_target(
    deps: CandidateDeps,
    state: RunState, ctx: AttemptCtx, preserved_files: List[str],
) -> None:
    if (
        preserved_files
        and state.last_attempt_mode == "targeted"
        and deps._fallback_routing(state, ctx).available
        and not state.budgets.fallback_targeted_attempted
    ):
        state.budgets.fallback_targeted_requested = True


def _reject_explanatory_prose(
    state: RunState, filepath: str, content: str, baseline: Optional[str],
) -> None:
    """``baseline``: the file before this write (None for a new file); only
    what the candidate added is judged (P3-C)."""
    contamination = find_explanatory_prose_contamination(filepath, content, baseline)
    if not contamination:
        return
    failure = Failure(
        type="prose_contamination",
        message=(
            f"SOURCE PROSE CONTAMINATION in {filepath}: obvious explanatory text "
            "was emitted as executable source; return code only or use the "
            "language's comment syntax for genuine documentation."
        ),
        raw_output=contamination,
        file_locations=[FileLocation(filepath=filepath)],
        likely_files=[filepath], failed_content={filepath: content},
        attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _reject_exception_masking(
    state: RunState, filepath: str, content: str, baseline: Optional[str],
) -> None:
    """BACKEND-READINESS-V2 (S4_A): a candidate that newly catches/swallows ``Error``/``Throwable`` hides the crash it
    was asked to fix. ``baseline``: the file before this write; the pristine run-start content wins when known, so a
    masking clause an earlier attempt introduced is still the candidate's. Runs on every attempt, repairs included."""
    pristine = state.all_original_contents.get(filepath, baseline)
    masking = find_unsafe_exception_masking(filepath, content, pristine)
    if not masking:
        return
    guidance = (
        "Catching Error/Throwable hides the failure instead of fixing the code that raises it; fix the cause "
        "(for a StackOverflowError, the unbounded recursion or loop) and do not widen a catch to Error/Throwable."
    )
    failure = Failure(
        type="unsafe_exception_masking",
        message=f"UNSAFE EXCEPTION MASKING in {filepath}: {masking}. {guidance}",
        raw_output=f"{masking}. {guidance}",
        file_locations=[FileLocation(filepath=filepath)],
        likely_files=[filepath], failed_content={filepath: content},
        attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _preserved_attribution_diagnostics(preserved_files: List[str]) -> Optional[dict]:
    if not preserved_files:
        return None
    return {
        "preserved_prior_attribution": {
            "tier": "locator",
            "files": list(preserved_files),
        }
    }


def _code_intelligence_for(deps: CandidateDeps, ctx: AttemptCtx) -> Any:
    """The repository's Code Intelligence index (read-only T1-T3 context),
    or None when there is none of the current parser identity."""
    open_code_intelligence = deps.load_open_code_intelligence()

    memory = getattr(getattr(getattr(ctx.kernel, "config", None), "paths", None), "memory", None)
    if not isinstance(memory, str):
        return None
    try:
        return open_code_intelligence(ctx.workspace_path, os.path.join(memory, "dependency_graph.db"))
    except Exception as error:  # read-only enrichment; T0 never depends on it
        deps.logger.warning("Code Intelligence index unavailable for known-target context: %s", error)
        return None


def _patch_required_files(deps: CandidateDeps, state: RunState, ctx: AttemptCtx, kwargs: Dict[str, Any]) -> List[str]:
    """Existing files this Developer call may only patch: the completeness
    gate (D1) downgraded their whole-file operation because no complete,
    exact current source was shown, so a whole-file answer would be rejected
    after the call."""
    required = []
    for path, operation in (kwargs.get("operation_by_file") or {}).items():
        if operation != deps.CodeOperation.REPAIR_WITH_PATCH or not deps._target_exists(ctx, path):
            continue
        gated, mandatory = deps._completeness_gated_operation(
            path, deps.CodeOperation.REPAIR_WITH_FULL_FILE, file_exists=True, ctx=ctx, state=state,
        )
        if mandatory and gated == deps.CodeOperation.REPAIR_WITH_PATCH:
            required.append(path)
    return required
