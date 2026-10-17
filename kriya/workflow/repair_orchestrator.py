"""The retry/repair preparation and the coordinated repair of one attempt (full modularization W4, E7).

Moved verbatim from ``kriya/workflow/attempt.py``:

- the retry member hints: the run's own known-target grounding (``_resolve_known_target_member_hints``), the goal's
  named members (``_goal_named_members_of``, ``_goal_named_member_candidates``, ``_goal_named_member_hints``,
  ``_merge_goal_named_member_hints``, RETRY-CONTEXT-GOAL-MEMBER-LOSS-001), their validation against the current member
  boundaries (``_validated_member_hints``) and the failure-grounded hints (``_resolve_retry_member_hints``: a failure
  locator, then a rejected anchored edit's SEARCH text, CTX-001 P1 C2/C3);
- the retry projection context (``_record_retry_projection_context_items``, ``_preserve_member_exact_precision``) and
  the retry target's source record (``_classify_retry_target_source_origin``, ``_target_source_record``);
- the retry evidence fingerprint (``_compute_retry_evidence_fingerprint``, ``_DETERMINISTIC_VERDICT_REASON_CODES``)
  and the retry temperature (``_effective_retry_temperature``);
- the centralized retry preparation (``_prepare_retry_context`` / ``RetryContextPreparation``, VAL-001 G1-R3) and its
  RetryPackage (``_retry_package_for_attempt``);
- the coordinated multi-file repair (``_run_coordinated_repair_generation``, ``_materialize_candidate_content``, MA9).

Retry DECISIONS are not here: which mode an attempt runs (``retry_policy``), what a retry package contains
(``retry_package``), which files a retry targets (attribution) and how a failure is charged (``retry_strategy``,
``retry_progress``) stay with their owners; this module prepares what the chosen retry shows. Every retry prompt is
rendered by ``kriya/workflow/retry_prompts.py``.

``kriya/workflow/attempt.py`` keeps every one of these names (a forwarder that builds ``RepairDeps`` at call time, or
a re-export) so a test patch on ``kriya.workflow.attempt`` keeps intercepting the moved code: every collaborator the
bodies looked up in attempt.py's globals arrives in ``RepairDeps``, resolved through ``kriya.workflow.attempt`` when
the forwarder runs (``deps._run_developer_generation`` IS ``kriya.workflow.attempt._run_developer_generation``, and so
on). Collaborators from the kernel's all-import cycle (``context_budget``, ``context_source``,
``authority_escalation``, ``retry_package``, ``retry_prompts``, ``agents.agent``, the PROVIDER module
``core.inference_settings``) arrive the same way. This module never imports ``kriya.workflow.attempt``, ``workflow``,
``workflow_controller`` or ``kriya.cli``, nor any all-import-cycle module.

``RunState`` is the attempt's ``kriya.workflow.state.GenerationState`` and ``AttemptCtx`` its
``kriya.workflow.attempt.AttemptContext``; both are spelled ``Any`` (kriya/workflow/stage_context.py says why the
state module is not imported; AttemptContext lives in attempt.py, OI-46), as are the RetryPackage, ContextItem and
OptionalSection annotations (their modules are in the cycle).
"""
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.repository.commit import content_revision
from kriya.repository.snapshot import load_snapshot
from kriya.workflow.context_package import make_context_item
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.retry_progress import SAMPLING_NOT_PERMITTED, SAMPLING_RESAMPLE, sampling_resample_permitted
from kriya.workflow.run_events import EventAuthority, RunEvent

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext


@dataclass(frozen=True)
class RepairDeps:
    """What the moved retry/repair code may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._repair_deps()`` when a forwarder runs. Every field is required: a builder that omits one fails at
    construction, never at the first use."""

    # context_source / core.inference_settings / authority_escalation / context_budget / retry_package /
    # retry_prompts / agents.agent (the all-import cycle), and the patched apply_anchored_edits
    CurrentSourceResolver: Any
    member_boundaries_for: Callable[..., Any]
    member_ids_matching_name: Callable[..., Any]
    evaluate_member_hints_from_search_evidence: Callable[..., Any]
    resolve_member_hints_from_failure_location: Callable[..., Any]
    role_binding_for_model: Callable[..., Any]
    grant_member_hints: Callable[..., Any]
    _reserve_graph_context_budget: Callable[..., Any]
    developer_reference: Callable[..., Any]
    retry_evidence_char_budget: Callable[..., Any]
    _reserve_sibling_content_budget: Callable[..., Any]
    allocation_window: Callable[..., Any]
    build_retry_package: Callable[..., Any]
    _build_coordinated_retry_prompt: Callable[..., Any]
    DeveloperAgent: Any
    apply_anchored_edits: Callable[..., Any]
    # attempt.py's own names (forwarders and re-exports of the moved functions included: a patch on any is honoured)
    _validated_member_hints: Callable[..., Any]
    _goal_named_member_hints: Callable[..., Any]
    _merge_goal_named_member_hints: Callable[..., Any]
    _resolve_retry_member_hints: Callable[..., Any]
    _resolve_known_target_member_hints: Callable[..., Any]
    _record_retry_projection_context_items: Callable[..., Any]
    _preserve_member_exact_precision: Callable[..., Any]
    _target_source_record: Callable[..., Any]
    _compute_retry_evidence_fingerprint: Callable[..., Any]
    _effective_retry_temperature: Callable[..., Any]
    _escalation_authorized_paths: Callable[..., Any]
    _record_authority_expansions: Callable[..., Any]
    _record_known_target_package: Callable[..., Any]
    _record_omitted_members: Callable[..., Any]
    _target_package_with_goal_member_fallback: Callable[..., Any]
    _target_package_with_window_reserve: Callable[..., Any]
    _developer_request_profile: Callable[..., Any]
    _retry_package_for_attempt: Callable[..., Any]
    _materialize_candidate_content: Callable[..., Any]
    _operation_map: Callable[..., Any]
    _run_developer_generation: Callable[..., Any]


def _goal_named_members_of(goal: str, owner: str) -> List[str]:
    """The member names the goal qualifies with ``owner`` (``Owner.member``
    forms, KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001), in goal order, unique."""
    from kriya.workflow.relocalization import goal_qualified_names

    members: List[str] = []
    for qualified in goal_qualified_names(goal):
        head, _dot, member = qualified.rpartition(".")
        if head == owner and member and member not in members:
            members.append(member)
    return members


def _resolve_known_target_member_hints(
    deps: RepairDeps, ctx: AttemptCtx, known_target_files: List[str],
) -> Dict[str, List[str]]:
    """CTX-001 P1 C2 production integration (docs/assurance/
    CTX_001_P1_ARCHITECTURE.md section 25): correlates THIS run's own
    attempt-1 Graph-RAG vector-hit candidate names
    (ctx.retrieval_member_hints, populated in workflow.py's retrieval stage)
    with known_target_files - a known-target file's member is NEVER
    invented merely because it's a target (CTX001-P1's own explicit
    constraint); a hint only exists here when an INDEPENDENT, already-
    grounded retrieval signal named a candidate for that exact path.

    Every candidate is validated against member_boundaries_for() on
    CURRENT (CurrentSourceResolver-resolved, worktree-authoritative)
    content before being returned - a name that no longer resolves to any
    real boundary (renamed/removed/stale since indexing) is silently
    dropped here, never fabricated. Reuses CurrentSourceResolver as-is;
    this function makes no root-selection decision of its own."""
    candidates_by_path = {
        path: names for path, names in ctx.retrieval_member_hints.items()
        if path in known_target_files and names
    }
    return deps._validated_member_hints(ctx, candidates_by_path)


_GOAL_CALL_NAME = re.compile(r"\b([a-z_][A-Za-z0-9_]*)\s*\(")


_GOAL_MEMBER_HINTS_MAX = 4


def _goal_named_member_candidates(goal: str, path: str) -> List[str]:
    """The member names the goal names that may ground ``path`` when its whole
    source is unfit, most specific first: members the goal qualifies with
    this file's owner (Element.absUrl for Element.java), then members of any
    other qualified name (Element.absUrl for Node.java, which defines absUrl
    too - P4-T2-r2), then bare call names (attr("abs:href")). Every one is
    validated against the file's current member boundaries before it becomes
    a hint; a name the file does not define is dropped."""
    from kriya.workflow.relocalization import goal_qualified_names

    owner = os.path.splitext(os.path.basename(path))[0]
    ordered: List[str] = list(_goal_named_members_of(goal, owner))
    for qualified in goal_qualified_names(goal):
        member = qualified.rpartition(".")[2]
        if member and member not in ordered:
            ordered.append(member)
    for name in _GOAL_CALL_NAME.findall(goal):
        if name not in ordered:
            ordered.append(name)
    return ordered


def _goal_named_member_hints(deps: RepairDeps, ctx: AttemptCtx, known_target_files: List[str]) -> Dict[str, List[str]]:
    """KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001 (BACKEND-READINESS-004 cohort,
    T2): the goal's own named members as member candidates for a known target
    (``_goal_named_member_candidates``), validated against the current member
    boundaries exactly like a retrieval hint - nothing is invented for a name
    no boundary carries - and capped at ``_GOAL_MEMBER_HINTS_MAX`` per file.
    Used only as the capacity fallback below: a target that fits whole keeps
    its full, authoritative source."""
    candidates_by_path: Dict[str, List[str]] = {}
    for path in known_target_files:
        candidates = _goal_named_member_candidates(ctx.goal, path)
        if candidates:
            candidates_by_path[path] = candidates
    hints = deps._validated_member_hints(ctx, candidates_by_path)
    return {path: members[:_GOAL_MEMBER_HINTS_MAX] for path, members in hints.items()}


def _merge_goal_named_member_hints(
    deps: RepairDeps, ctx: AttemptCtx, member_hints: Dict[str, List[str]], paths: List[str],
) -> bool:
    """RETRY-CONTEXT-GOAL-MEMBER-LOSS-001 (BACKEND-FINAL-CLOSURE-005): adds the
    goal's own named members of ``paths`` (``_goal_named_member_hints``: the
    same boundary validation and cap as attempt 1's capacity fallback) to
    ``member_hints`` in place; True when a member was added."""
    added = False
    for path, members in deps._goal_named_member_hints(ctx, paths).items():
        hinted = member_hints.setdefault(path, [])
        for member in members:
            if member not in hinted:
                hinted.append(member)
                added = True
    return added


def _validated_member_hints(deps: RepairDeps, ctx: AttemptCtx, candidates_by_path: Dict[str, List[str]]) -> Dict[str, List[str]]:
    if not candidates_by_path:
        return {}
    resolver = deps.CurrentSourceResolver(ctx.workspace_path, ctx.worktree_path, content_cache=ctx.source_cache.content_cache)
    member_hints: Dict[str, List[str]] = {}
    for path, names in candidates_by_path.items():
        resolved = resolver.resolve(path)
        if not resolved.exists:
            continue
        boundaries = deps.member_boundaries_for(path, resolved.content)
        if boundaries is None:
            continue
        matched_ids: List[str] = []
        for name in names:
            for member_id in deps.member_ids_matching_name(boundaries, name):
                if member_id not in matched_ids:
                    matched_ids.append(member_id)
        if matched_ids:
            member_hints[path] = matched_ids
    return member_hints


def _resolve_retry_member_hints(
    deps: RepairDeps, ctx: AttemptCtx, state: RunState, target_files: List[str],
) -> Dict[str, List[str]]:
    """CTX-001 P1 C2 production integration: correlates state.last_failure.
    file_locations (already-structured compiler/test evidence) with
    target_files - the retry's OWN, already-authorized target set. A
    failure_location's filepath is used ONLY to pick which already-
    authorized target's member to resolve, NEVER to widen the target set
    itself (a stack frame naming a different, untargeted file is simply
    ignored here - "failure location must not authorize a new file").

    Deduplicates across multiple file_locations deterministically (a set
    per path, rendered back to a sorted list) - the same member reported
    by two different locations produces one hint, not two.

    CTX-001-P1-C3 (2026-09-18): a SECOND, additive evidence source - a
    rejected anchored-edit's own SEARCH text (state.last_failure.
    attempted_edits) - runs AFTER the file-location pass above and ONLY
    ever fills in a path that pass left with no hint (line-grounded
    evidence, when it exists at all, is never overridden by the weaker,
    model-generated SEARCH-text evidence). Gated on every one of: the path
    is still an authorized target (never widens scope, same invariant as
    the file-location pass); at least one REAL anchored-edit failure has
    already occurred for this exact path (state.budgets.
    anchor_failure_counts) - never triggers on a full-file rejection that
    produced no anchor failure at all; this run's known-target context for
    the path is not POSITIVELY known to already be full/exact (an absent
    entry is evidence of nothing, not of exactness - see the gate's own
    2026-09-19 comment below; only a recorded, non-omitted "shown in full"
    entry means there is genuinely nothing left to escalate to). See
    VAL-001 G1 rerun forensics (run d756a833) for why this
    source exists: SOURCE 1/2 above were BOTH structurally silent for an
    entire 8-attempt run despite the model's own rejected SEARCH blocks
    already containing real, current-file vocabulary that nothing
    consumed."""
    if state.last_failure is None or not target_files:
        return {}
    target_set = set(target_files)
    resolver = deps.CurrentSourceResolver(ctx.workspace_path, ctx.worktree_path, content_cache=ctx.source_cache.content_cache)
    member_hints: Dict[str, set] = {}
    for location in state.last_failure.file_locations:
        if location.filepath not in target_set or location.line is None:
            continue
        resolved = resolver.resolve(location.filepath)
        if not resolved.exists:
            continue
        for candidate in deps.resolve_member_hints_from_failure_location(
            location.filepath, resolved.content, location.line,
        ):
            member_hints.setdefault(location.filepath, set()).add(candidate.member_id)

    if state.last_failure.attempted_edits:
        failure_filepaths = {
            location.filepath for location in state.last_failure.file_locations
        } or set(state.last_failure.likely_files)
        for filepath in failure_filepaths:
            if filepath not in target_set or filepath in member_hints:
                continue
            if state.budgets.anchor_failure_counts.get(filepath, 0) < 1:
                continue
            # PERF/SOURCE-2 residual (2026-09-19): `known_item is None` -
            # NO recorded evidence of what the Developer was shown for this
            # path - was previously treated identically to "shown in full"
            # (skip, nothing left to escalate). That's backwards: this
            # function's own D1 sibling (_has_authoritative_full_source)
            # already establishes "absence of a recorded ContextItem is NOT
            # evidence of exactness - it is evidence of nothing" - applied
            # here too, an unknown view is exactly the case SOURCE 3 exists
            # to help ground, not one to skip. Confirmed empirically
            # (2026-09-19): the prior condition silently blocked SOURCE 3
            # grounding on the realistic "no known_target_context_items
            # entry yet for this path" shape, even though the identical
            # search text grounds correctly via evaluate_member_hints_from_
            # search_evidence() in isolation.
            #
            # MUTATION_RELEVANCE_GATE residual (2026-09-19, VAL-001 G1
            # DEV-INV rerun): a recorded tier="member_exact" item only
            # means ONE member of this path was shown in full - it does
            # NOT mean "nothing left to escalate to" the way a genuine
            # tier="full" (whole file) record does. The prior condition
            # treated `omitted_regions is False` alone (true for BOTH
            # tiers) as that terminal signal, so once ANY member_exact was
            # recorded for a path - even a provably WRONG one, whose own
            # anchored edit keeps failing - SOURCE 3 was permanently
            # suppressed for every LATER, possibly differently-relevant
            # failing edit against that same path for the rest of the run.
            # That is "merely any member_exact from the same file"
            # authorizing escalation silence for an unrelated member - the
            # exact gap this residual closes. Only tier="full" is treated
            # as genuinely terminal now; a tier="member_exact" record lets
            # this loop re-run below, which will correctly re-derive the
            # SAME member again when it is still the relevant one (a
            # harmless, idempotent re-confirmation - see test_K's own
            # updated assertion), or correctly escalate to a DIFFERENT
            # member when the currently-failing search text actually
            # grounds there instead.
            known_item = state.known_target_context_items.get(filepath)
            if known_item is not None and not known_item.omitted_regions and known_item.tier == "full":
                continue
            resolved = resolver.resolve(filepath)
            if not resolved.exists:
                continue
            search_grounded_ids: set = set()
            for edit_index, edit in enumerate(state.last_failure.attempted_edits):
                search_text = edit.get("search") or ""
                result = deps.evaluate_member_hints_from_search_evidence(
                    filepath, resolved.content, search_text,
                )
                for candidate in result.candidates:
                    search_grounded_ids.add(candidate.member_id)
                # CTX-001-P1-C3 observability (2026-09-18, VAL-001 G1-R2 post-mortem):
                # structured evidence, not human-readable text, is the authoritative
                # record of this evaluation - the search TEXT itself is never
                # persisted here (untrusted, model-generated, and potentially large),
                # only a bounded content_revision() hash and its length, matching
                # this module's own established "hash, never raw content" pattern
                # for anything derived from untrusted model output. `outcome` and
                # `candidates` are read directly off the SAME evaluate_member_hints_
                # from_search_evidence() call whose result feeds search_grounded_ids
                # above - this event can never disagree with the real decision,
                # because it is not a second, independently-derived classification
                # of it.
                state.record_event(RunEvent(
                    kind="context.search_evidence_grounding",
                    attempt=state.attempt_number,
                    source="attempt._resolve_retry_member_hints",
                    authority=EventAuthority.ADVISORY,
                    message=(
                        f"CTX-001-P1-C3 SOURCE 3 evaluation for {filepath} "
                        f"(edit #{edit_index + 1}): {result.outcome}."
                    ),
                    details={
                        "source": "failure_search_evidence",
                        "filepath": filepath,
                        "edit_index": edit_index,
                        "search_text_present": bool(search_text.strip()) if search_text else False,
                        "search_text_hash": content_revision(search_text) if search_text else None,
                        "search_text_length": len(search_text) if search_text else 0,
                        "distinctive_token_count": result.distinctive_token_count,
                        "outcome": result.outcome,
                        "grounded": bool(result.candidates),
                        "candidate_member_ids": [c.member_id for c in result.candidates],
                        "candidate_provenance": [c.provenance for c in result.candidates],
                        "current_revision": resolved.revision,
                    },
                ))
            if len(search_grounded_ids) == 1:
                member_hints[filepath] = search_grounded_ids

    return {path: sorted(ids) for path, ids in member_hints.items()}


def _record_retry_projection_context_items(
    deps: RepairDeps, state: RunState, retry_package: Optional[Any],
) -> None:
    """VAL-001 G1 D1 (2026-09-18): a targeted retry's own content-supply
    mechanism (RetryPackage/FileProjection, kriya/workflow/retry_package.py
    + context_projection.py) is a SEPARATE producer from build_known_target_
    context()'s ContextItem/ContextPackage, but carries the exact same real
    signal (path/revision/level/omitted_regions) - confirmed live: the
    existing test `test_first_anchor_failure_switches_next_protocol_
    without_widening_scope` demonstrates a targeted retry legitimately
    falling back to REPAIR_WITH_FULL_FILE after an anchor-match failure, and
    that fallback IS safe exactly when the retry's own projection for that
    file was ProjectionLevel.FULL (no omission) - so this function converts
    each FileProjection into the same ContextItem shape _completeness_
    gated_operation() already reads, rather than either re-deriving a
    second signal or exempting the targeted-retry path from the invariant
    outright (which would silently reopen it for a genuinely-projected/
    truncated target file, not just the always-safe case).

    Found via the real focused-suite regression after 7bc52b5 (2026-09-18,
    test_workflow_checks_toolchain_only_once_across_retries): a full-set
    retry with no grounded implicated files (`target_files=[]`) produces a
    non-None RetryPackage whose `target_projections` is empty - every
    already-written file lands in `reference_projections` instead
    (kriya/workflow/retry_package.py's own `targets`/`references` split,
    reason="dependency_reference"). Originally this function only read
    `target_projections`, so a file shown ONLY as reference content (exactly
    this test's pom.xml) was recorded nowhere, and _completeness_gated_
    operation() correctly-but-wrongly treated real, shown content as unknown.
    `reference_projections` uses the exact same budgeted `project_
    implementation_source()` mechanism as targets (a real `reference_budget`,
    not an unbounded read) - it can legitimately be partial, so it gets the
    identical tier/is_exact/omitted_regions handling, not a blanket "always
    exact" shortcut."""
    if retry_package is None:
        return
    for projection in (*retry_package.target_projections, *retry_package.reference_projections):
        new_item = make_context_item(
            path=projection.path, content=projection.content,
            reason=f"retry_package:{projection.reason}",
            source_type="named_in_request", trust_level="repository",
            tier=projection.level.value, is_exact=not projection.omitted_regions,
            revision=projection.revision, omitted_regions=projection.omitted_regions,
        )
        state.known_target_context_items[projection.path] = deps._preserve_member_exact_precision(
            state, projection.path, new_item,
        )


def _preserve_member_exact_precision(
    state: RunState, path: str, new_item: Any,
) -> Any:
    """VAL-001 G1-R3 (test-discovered while building this pass's own
    adversarial coverage for "stronger member_exact hint overwritten by
    weaker fallback" - the investigation task's own named hypothesis):
    once SOURCE 3 grounds a member_exact rendering (omitted_regions=False),
    it correctly never re-fires on a later attempt for the same revision
    (_resolve_retry_member_hints' own "nothing left to escalate" gate) - so
    that later attempt's general retry package legitimately re-includes the
    SAME path (nothing excluded it this time) and would otherwise silently
    overwrite the precise member_exact record with a coarser, non-exact
    excerpt/skeleton/signatures one, discarding real, still-current
    evidence for no reason.

    Deliberately narrow: only ever preserves a member_exact record (is_exact
    AND member_id is not None) against a same-revision, less-exact
    replacement - NEVER a "full" (whole-file) record, whose cross-attempt
    preservation would be a materially different, more sensitive claim (D1
    authorizes REPAIR_WITH_FULL_FILE off "full", never off "member_exact" -
    see _completeness_gated_operation's own docstring; member_exact and
    every coarser tier already force REPAIR_WITH_PATCH identically, so
    preserving member_exact here changes zero authorized operations, only
    precision/diagnostic value). A revision CHANGE always wins regardless -
    stale evidence, exact or not, must never be preferred over fresh."""
    existing = state.known_target_context_items.get(path)
    if (
        existing is not None
        and existing.is_exact and existing.member_id is not None
        and existing.revision == new_item.revision
        and not (new_item.is_exact and new_item.member_id is not None)
    ):
        return existing
    return new_item


def _classify_retry_target_source_origin(
    state: RunState, ctx: AttemptCtx, path: str,
) -> str:
    """VAL-001 G1-R3 observability (item 6): which universe a retry target's
    real content actually came from, reconstructable from trace alone.
    "current_worktree" covers exactly the case this pass's target-
    reachability fix newly reaches - a path present in target_files but in
    neither all_files_written nor established_files."""
    if path in state.all_files_written:
        return "already_written"
    if path in ctx.established_files:
        return "established"
    return "current_worktree"


def _target_source_record(
    state: RunState, ctx: AttemptCtx, path: str,
) -> Dict[str, Any]:
    item = state.known_target_context_items.get(path)
    return {
        "path": path,
        "source_origin": _classify_retry_target_source_origin(state, ctx, path),
        "tier": item.tier if item else None,
        "is_exact": item.is_exact if item else None,
        "member_id": item.member_id if item else None,
        "omitted_regions": item.omitted_regions if item else None,
        "known": item is not None,
    }


# VAL-001 G1-R3 no-progress gate: the ONLY verdicts where repeating
# unchanged evidence can never possibly produce a different outcome, because
# the verdict is Kriya's OWN deterministic gate reading known_target_context_
# items, not the model's own (temperature-sampled) output.
#
# Deliberately keyed on Failure.diagnostics["reason_code"], never on
# Failure.type alone: type="operation_contract" is shared by TWO structurally
# different raise sites in this module - _validate_actual_mutation_
# authority()'s own fixed, content-independent D1 rejection string (real
# reason_code="ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED", exactly the real
# G1-R3 incident's own attempts-7-vs-8 shape - resampling cannot change
# whether the SAME recorded context authorizes a full-file replacement) and
# validate_operation_result()'s own contract_error (protocol_error/shape-
# mismatch classification of what the MODEL actually wrote - genuinely
# content-dependent/probabilistic, no reason_code set at all, found during a
# 2026-09-19 review of this exact gate's own scope). Matching on the bare
# type string would have silently swept the second, probabilistic case into
# zero-tolerance blocking too.
#
# Deliberately NOT "anchored_edit" (the model's own SEARCH text is genuinely
# resampled and could ground differently even against unchanged context) or
# any compile/test/diagnosis failure (real, CANDIDATE-CONTENT-dependent
# outcomes - confirmed live: an earlier, unscoped version of this gate broke
# test_workflow_fallback_chain, which explicitly validates that Kriya spends
# its full configured targeted_max_retries budget - at a real, non-zero
# retry_temperature - even against byte-identical compile-failure evidence,
# precisely BECAUSE resampling a probabilistic failure genuinely can
# succeed). Never widen this set to a verdict whose outcome depends on model
# output without the same analysis this comment documents.
_DETERMINISTIC_VERDICT_REASON_CODES = frozenset({"ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED"})


def _compute_retry_evidence_fingerprint(
    state: RunState, target_files: Optional[List[str]], mode: Optional[str],
    model_identity: str, member_hints: Dict[str, List[str]],
) -> Tuple[Any, ...]:
    """VAL-001 G1-R3 no-progress gate: what Kriya is ABOUT TO SHOW the
    Developer this attempt, reduced to exactly the fields that matter for
    detecting a genuine repeat - deliberately NOT just (revision, tier,
    failure_type) (too coarse: that can't distinguish "no new evidence at
    all" from "new evidence that still didn't resolve to a member", and
    can't tell a real strategy transition from a same-mode repeat). Per
    implicated path: current known_target_context_items provenance
    (revision/tier/is_exact/member_id/omitted_regions - the exact real
    state _completeness_gated_operation() itself authorizes off) plus any
    NEWLY grounded member hint for that path. mode/model_identity are
    included so a genuine strategy transition (different mode, or a
    fallback-model swap within the same mode) is - by construction - never
    mistaken for a repeat, without this function special-casing
    "transition" itself; retry_strategy.py's own last_failure_signature is
    reused as the normalized failure signature (never re-derived here) -
    already excludes attempt-specific raw text (see build_failure_signature).

    Never reads raw model/candidate output: state.last_failure.
    attempted_edits' own SEARCH text feeds member-hint GROUNDING (see
    _resolve_retry_member_hints), but only the deterministic, worktree-
    derived OUTCOME of that grounding (member_hints) is part of this
    fingerprint - fabricated SEARCH text can never itself become part of
    what this gate treats as "evidence"."""
    per_path = tuple(
        (
            path,
            item.revision if item else None,
            item.tier if item else None,
            item.is_exact if item else None,
            item.member_id if item else None,
            item.omitted_regions if item else None,
            tuple(sorted(member_hints.get(path, ()))),
            # CONTEXT-EDIT-PROTOCOL-001: the exact-window escalation inputs
            # (anchor failures widen the window; an anchor found outside the
            # authoritative context becomes a locus) - new evidence the next
            # invocation will show.
            state.budgets.anchor_failure_counts.get(path, 0),
            tuple(state.edit_anchor_loci.get(path, ())),
        )
        for path, item in (
            (path, state.known_target_context_items.get(path))
            for path in sorted(set(target_files or ()))
        )
    )
    return (mode, model_identity, per_path, state.budgets.last_failure_signature)


def _effective_retry_temperature(deps: RepairDeps, ctx: AttemptCtx, model_identity: str) -> Optional[float]:
    """The sampling temperature a Developer retry on ``model_identity`` is
    sent with: ``llm.retry_temperature`` when configured, otherwise the
    called binding's own temperature (PRD-014: a fallback never inherits
    the primary's sampling)."""
    config = ctx.kernel.config
    if config.llm.retry_temperature is not None:
        return config.llm.retry_temperature
    binding = deps.role_binding_for_model(config, "developer", model_identity)
    own = getattr(binding, "temperature", None)
    return own if own is not None else config.llm.temperature


@dataclass
class RetryContextPreparation:
    """Bundles everything a targeted/fallback_targeted/full_set retry needs
    from centralized retry-context preparation - see _prepare_retry_context's
    own docstring for why generation strategy must never gate whether any of
    this runs, only what it runs WITH."""

    retry_package: Optional[Any]
    retry_error_context: str
    member_hints: Dict[str, List[str]]
    member_hint_rendered: str


def _prepare_retry_context(
    deps: RepairDeps, state: RunState, ctx: AttemptCtx, *,
    target_files: List[str], prompt_window: int, model_identity: str,
    base_code_context: str = "", enable_no_progress_gate: bool = True,
) -> RetryContextPreparation:
    """VAL-001 G1-R3 (run 0c18ac70, 2026-09-18/19): the single, centralized
    retry-time source/member-hint preparation step for every ordinary retry
    mode (targeted, fallback_targeted, full_set). Generation strategy must
    never determine whether authoritative context recovery is available -
    before this function existed, `fallback_targeted` never called
    _resolve_retry_member_hints() at all (a confirmed, real branch-coverage
    gap in the G1-R3 investigation); centralizing here closes that by
    construction rather than copying the same three calls into a third
    branch.

    Order matters, unchanged from the pre-centralization targeted-retry
    branch this replaces: member hints are resolved FIRST so their paths
    can be excluded from the general retry package (avoiding
    DUPLICATE_SOURCE_CONTEXT_PATHS - a member-exact rendering already
    covers that path at higher fidelity than the general excerpt would).

    Raises QualityGateFailure (failure.type="no_progress_retry") BEFORE
    returning - i.e. before the caller ever reaches its own Developer call -
    when this attempt's freshly-computed RetryEvidenceFingerprint
    (_compute_retry_evidence_fingerprint) exactly matches the immediately
    preceding real attempt's. This is not a second, competing retry-budget
    mechanism: the Failure it raises flows through retry_strategy.py's own
    existing handle_attempt_failure()/record_workspace_progress() pipeline
    exactly like any other Quality Gate failure, so the EXISTING
    consecutive-no-progress counter, forced-strategy-transition (after 2
    consecutive), and no_progress_terminated fail-closed termination (after
    the configured limit) all apply unchanged - "enter existing legal
    recovery if one remains, otherwise fail closed" is true by construction,
    not a second policy this function re-implements. It only ever prevents
    ONE avoidable Developer call from being spent to (re)discover, at model
    cost, a "no new evidence" conclusion Kriya can already reach
    deterministically and for free from state it already holds."""
    retry_member_hints = deps._resolve_retry_member_hints(ctx, state, target_files)
    # PRD-028: every hinted member is a read-only authority expansion the
    # control plane decides (in write scope, supported language, exactly one
    # member in the current source) - recorded with its source revision and
    # pristine/candidate origin. Only GRANTED members become context.
    retry_member_hints, expansions = deps.grant_member_hints(
        retry_member_hints, workspace_path=ctx.workspace_path, worktree_path=ctx.worktree_path,
        authorized_paths=deps._escalation_authorized_paths(ctx, target_files),
        evidence={
            "source": "retry_member_hints", "attempt": state.attempt_number,
            "failure_type": state.last_failure.type if state.last_failure is not None else None,
        },
    )
    deps._record_authority_expansions(state, expansions)
    # The run's own grounding - the members localization found for each
    # authorized target, verified against the CURRENT bytes exactly as on
    # attempt 1 (no new authority) - stays part of every retry's T0, after
    # the failure-grounded members: a failure that names no line (a protocol
    # error, an invented anchor) must never evict the causal member. A
    # failure with no locator leaves a full-set retry without failure targets:
    # the grounding then covers the attempt's own planned file set (live:
    # value_chain, the exact body vanished from every full-set retry).
    grounded_paths = list(target_files or ()) or list(ctx.expected_files_upfront or ())
    for path, member_ids in deps._resolve_known_target_member_hints(ctx, grounded_paths).items():
        hinted = retry_member_hints.setdefault(path, [])
        hinted.extend(member_id for member_id in member_ids if member_id not in hinted)
    # RETRY-CONTEXT-GOAL-MEMBER-LOSS-001 (BACKEND-FINAL-CLOSURE-005, P4-T2-r3):
    # attempt 1 showed the goal's own named members of an unfit target (the
    # capacity fallback, KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001); the retry's
    # member set came only from the failure's loci and the model's rejected
    # SEARCH text, so the causal member (Element.absUrl) vanished from every
    # retry and the model could only fabricate anchors (ANCHOR_NOT_IN_FILE
    # on five attempts, its own reasoning asking whether absUrl was hidden).
    # The goal-named members a target defines stay in every retry's set: for a
    # target already member-scoped, merged here; for a target the retry
    # package must omit whole, added below and the package rebuilt. Same
    # validation as attempt 1, never a new path, never a new authority.
    goal_grounded = deps._merge_goal_named_member_hints(
        ctx, retry_member_hints, [path for path in grounded_paths if path in retry_member_hints])
    # A grounded target with no member yet: shown whole when it fits (never demoted), else - exactly attempt
    # 1's capacity fallback - its goal-named members become its member set and the retry package excludes it
    # (the member-exact rendering below supersedes the package's truncated excerpt of the same file).
    unhinted = [path for path in grounded_paths if path not in retry_member_hints]
    unfit: List[str] = []
    if unhinted:
        probe_limit = deps._reserve_graph_context_budget(
            prompt_window, ctx.skills_prompt,
            deps.developer_reference(prompt_window, ctx.learned_rag_context, ctx.skills_prompt, ctx.design, ctx.plan),
            ctx.design, ctx.plan, base_code_context,
        )
        _rendered, _package, fallback = deps._target_package_with_goal_member_fallback(
            ctx, unhinted, probe_limit, prompt_window, {})
        if fallback is not None:
            unfit = list(fallback["unfit_targets"])
            for path, members in fallback["goal_member_hints"].items():
                hinted = retry_member_hints.setdefault(path, [])
                hinted.extend(member for member in members if member not in hinted)
            goal_grounded = True
    retry_package = deps._retry_package_for_attempt(
        state, ctx, target_files=target_files, prompt_window=prompt_window,
        exclude=retry_member_hints.keys(),
    )
    if goal_grounded:
        state.record_event(RunEvent(
            kind="context.retry_goal_member_grounding", attempt=state.attempt_number,
            source="attempt._prepare_retry_context", authority=EventAuthority.ADVISORY,
            message="the goal's own named members of a member-scoped or unfit target were kept in the retry's member set",
            details={"member_hints": {path: list(members) for path, members in retry_member_hints.items()},
                     "unfit_targets": unfit},
        ))
    retry_error_context = (
        retry_package.prior_error_text() if retry_package else state.error_context
    )
    deps._record_retry_projection_context_items(state, retry_package)

    member_hint_rendered = ""
    if retry_member_hints:
        retry_member_limit = deps._reserve_graph_context_budget(
            prompt_window, ctx.skills_prompt,
            deps.developer_reference(prompt_window, ctx.learned_rag_context, ctx.skills_prompt, ctx.design, ctx.plan),
            ctx.design, ctx.plan, base_code_context,
        )
        retry_member_rendered, retry_member_package = deps._target_package_with_window_reserve(
            ctx, list(retry_member_hints.keys()), retry_member_limit, prompt_window, retry_member_hints,
        )
        if retry_member_rendered:
            member_hint_rendered = retry_member_rendered
        # VAL-001 G1 D1: record real provenance for _completeness_gated_operation()'s
        # own authorization check - never inferred from file size.
        #
        # VAL-001 G1-R3 (found while writing this pass's own adversarial
        # coverage): build_known_target_context() can legitimately return
        # MORE than one relevant_files entry for the SAME path when a
        # member hint grounds - the member_exact slice itself, plus a
        # broader signatures-level overview of the rest of the file for
        # surrounding context. A plain dict-comprehension update() applies
        # list order, so the LAST entry silently wins regardless of
        # precision, discarding the very member_exact record C3 escalation
        # exists to produce. Apply member-scoped (more precise) entries
        # LAST so they are never overwritten by a broader same-path
        # overview - never the reverse.
        deps._record_known_target_package(state, retry_member_package.relevant_files)
        deps._record_omitted_members(state, retry_member_package.omitted)
        state.record_event(RunEvent(
            kind="context.retry_member_hint_package",
            attempt=state.attempt_number,
            source="attempt._prepare_retry_context",
            authority=EventAuthority.ADVISORY,
            message="Retry member-hint context package built from Failure evidence.",
            details={
                "target_files": sorted(retry_member_hints.keys()),
                "unit_count": len(retry_member_package.relevant_files),
                "tiers": [
                    {"path": item.path, "member_id": item.member_id, "tier": item.tier}
                    for item in retry_member_package.relevant_files
                ],
                "omitted": list(retry_member_package.omitted),
                "package_hash": retry_member_package.package_hash,
            },
        ))

    # VAL-001 G1-R3 observability (item 6): reconstructable from trace alone
    # what each requested target's own real source actually was this
    # attempt - structural provenance only, never the raw source text.
    state.record_event(RunEvent(
        kind="context.retry_target_source",
        attempt=state.attempt_number,
        source="attempt._prepare_retry_context",
        authority=EventAuthority.ADVISORY,
        message="Retry-time source provenance resolved for this attempt's implicated target(s).",
        details={
            "mode": state.last_attempt_mode,
            "targets": [
                deps._target_source_record(state, ctx, path)
                for path in sorted(set(target_files or ()))
            ],
        },
    ))

    # Scoped to targeted/fallback_targeted/full_set only (the task's own
    # explicit scope) - never API_CONTRACT_RECOVERY, which is a separate,
    # already-deterministic state machine with its own
    # API_CONTRACT_RECOVERY_MAX_ATTEMPTS budget; this centralization still
    # gives it the target-reachability fix and C3 member-hint resolution
    # (a strict improvement), just not the new no-progress termination path.
    if enable_no_progress_gate:
        # PRD-017: keyed on the model's whole request profile (runtime,
        # capabilities, edit protocol, windows, output budget), not its alias,
        # so a fallback hop that changes any of them is new progress
        # opportunity by construction.
        request_profile = deps._developer_request_profile(
            ctx, None if model_identity == ctx.kernel.config.llm.model else model_identity,
        )
        fingerprint = deps._compute_retry_evidence_fingerprint(
            state, target_files, state.last_attempt_mode,
            f"{model_identity}@{request_profile.digest}", retry_member_hints,
        )
        # Only a deterministic-verdict failure (see _DETERMINISTIC_VERDICT_
        # FAILURE_TYPES' own docstring) with at least one real, grounded
        # target is eligible to block at all - a fingerprint over an empty
        # target_files carries no per-path evidence worth acting on, and a
        # probabilistic (model-output-dependent) failure type genuinely can
        # resolve differently on an identically-evidenced resample.
        eligible = (
            bool(target_files)
            and state.last_failure is not None
            and (state.last_failure.diagnostics or {}).get("reason_code")
            in _DETERMINISTIC_VERDICT_REASON_CODES
        )
        # PRD-026: identical evidence shown on ANY earlier attempt (not only
        # the previous one) is a repeat - an A -> B -> A evidence cycle
        # presents nothing new either.
        fingerprint_hash = content_revision(repr(fingerprint))
        repeated_evidence = fingerprint_hash in state.retry_evidence_seen
        no_progress = eligible and repeated_evidence
        sampling_resample = False
        if repeated_evidence and not no_progress:
            # A probabilistic failure may be resampled on identical evidence
            # only when the model actually samples; it is budgeted as a
            # typed SAMPLING_RESAMPLE, never as material progress.
            effective_temperature = deps._effective_retry_temperature(ctx, model_identity)
            if sampling_resample_permitted(state.last_attempt_mode, effective_temperature):
                sampling_resample = True
                state.sampling_resamples += 1
                state.record_event(RunEvent(
                    kind="retry.sampling_resample",
                    attempt=state.attempt_number,
                    source="attempt._prepare_retry_context",
                    authority=EventAuthority.ADVISORY,
                    message=(
                        f"{SAMPLING_RESAMPLE}: identical retry evidence resampled at temperature "
                        f"{effective_temperature} - budgeted, not counted as progress."
                    ),
                    details={
                        "reason_code": SAMPLING_RESAMPLE,
                        "mode": state.last_attempt_mode,
                        "model": model_identity,
                        "effective_temperature": effective_temperature,
                        "first_seen_attempt": state.retry_evidence_seen[fingerprint_hash],
                        "fingerprint_hash": fingerprint_hash,
                        "sampling_resamples": state.sampling_resamples,
                    },
                ))
            else:
                no_progress = True
        state.record_event(RunEvent(
            kind="retry.progress_decision",
            attempt=state.attempt_number,
            source="attempt._prepare_retry_context",
            authority=EventAuthority.ADVISORY,
            message=(
                "No new retry evidence since the last attempt - skipping this Developer call."
                if no_progress else "Retry evidence progressed since the last attempt."
            ),
            details={
                "mode": state.last_attempt_mode,
                "model": model_identity,
                "request_profile": request_profile.digest,
                "target_files": sorted(set(target_files or ())),
                "no_progress": no_progress,
                "sampling_resample": sampling_resample,
                "fingerprint_hash": fingerprint_hash,
            },
        ))
        if no_progress:
            reason_code = (
                "NO_PROGRESS_RETRY_EXHAUSTED" if eligible else SAMPLING_NOT_PERMITTED
            )
            raise QualityGateFailure(Failure(
                type="no_progress_retry",
                message=(
                    "NO_PROGRESS_RETRY_EXHAUSTED: this attempt's retry evidence "
                    f"(mode={state.last_attempt_mode}, model={model_identity}, "
                    f"targets={sorted(set(target_files or ()))}) is materially identical to "
                    "the immediately preceding attempt's - context tier/exactness/member "
                    "grounding and the normalized failure signature are all unchanged. "
                    "Spending another Developer call would present the exact same evidence "
                    "again; deferring to the existing retry-strategy/no-progress machinery "
                    "instead of spending it."
                ),
                raw_output=f"retry_evidence_fingerprint_hash={fingerprint_hash}",
                source="orchestrator",
                attempt=state.attempt_number,
                mode=state.last_attempt_mode,
                likely_files=sorted(set(target_files or ())),
                diagnostics={"reason_code": reason_code},
            ))
        state.budgets.last_retry_evidence_fingerprint = fingerprint
        state.retry_evidence_seen.setdefault(fingerprint_hash, state.attempt_number)
        attempt_evidence_scope.note_retry_evidence(fingerprint_hash)

    return RetryContextPreparation(
        retry_package=retry_package,
        retry_error_context=retry_error_context,
        member_hints=retry_member_hints,
        member_hint_rendered=member_hint_rendered,
    )


def _retry_package_for_attempt(
    deps: RepairDeps, state: RunState,
    ctx: AttemptCtx,
    *,
    target_files: Optional[List[str]],
    prompt_window: int,
    exclude: Optional[Iterable[str]] = None,
) -> Optional[Any]:
    if state.last_failure is None:
        return None
    # Retry source evidence gets its share of the call's prompt allocation
    # window (PRD-016, context_budget.retry_evidence_char_budget), so the
    # full retry prompt still fits beside the output budget.
    max_chars = deps.retry_evidence_char_budget(prompt_window)
    # CTX-001 P1 C2: `exclude` (deliberately applied only to all_files, NOT
    # to target_files) skips a path already given its own, higher-fidelity
    # member-exact rendering (build_known_target_context(), called just
    # before this in the targeted-retry branch) - avoids the exact
    # DUPLICATE_SOURCE_CONTEXT_PATHS class of bug Package 2 already fixed
    # once. Filtering all_files (not target_files) is deliberate:
    # build_retry_package()'s own targets/references lists are BOTH derived
    # from `sorted(set(all_files))`, so an excluded path is naturally
    # dropped from both without risking target_files falling back to
    # failure.likely_files (which build_retry_package does whenever
    # target_files is falsy - filtering target_files itself down to []
    # would silently re-trigger that fallback and undo the exclusion).
    exclude_set = set(exclude or ())
    # VAL-001 G1-R3 (run 0c18ac70, 2026-09-18): a pre-existing brownfield
    # repair target that fails BEFORE its first successful write belongs to
    # neither all_files_written (only populated post-AuthorizedFileWriter.
    # commit_batch - see that call site's own comment further down this
    # file) nor established_files (prior-MILESTONE paths only - see that
    # field's own docstring in workflow.py's run_generation_workflow()).
    # Every retry for such a target previously built its source universe
    # with the target itself absent, so build_retry_package()'s own
    # `targets = [p for p in ordered_files if p in target_set]` silently
    # produced ZERO projections for a path target_files explicitly named -
    # confirmed by direct, offline reproduction against run 0c18ac70's real
    # evidence: build_retry_package(all_files=[], target_files=[
    # 'graphify/extractors/engine.py'], ...) returns target_projections=()
    # and doesn't even record the path in omitted_files, so 7 of that run's
    # 8 attempts reasoned about a 331KB file using only attempt 1's own
    # skeleton rendering. Unioning the requested targets into the universe
    # HERE - not widening build_retry_package()'s own filtering semantics -
    # keeps that function exactly as generic as its other callers already
    # rely on; a named target that doesn't actually exist on disk still
    # resolves through this same union into build_retry_package()'s own
    # pre-existing OSError->omitted_files handling, never a new failure
    # mode. Real worktree content only (the same open()/read() every other
    # entry in this universe already goes through) - never model/candidate
    # output, so this can never let generated text become source authority.
    requested_target_files = set(target_files or ())
    all_files = (
        set(state.all_files_written) | set(ctx.established_files) | requested_target_files
    ) - exclude_set
    return deps.build_retry_package(
        failure=state.last_failure,
        worktree_path=ctx.worktree_path,
        # Unioned with ctx.established_files (see that field's own docstring) -
        # a retry package's content candidates should include files earlier
        # milestones wrote too, not just this attempt's own writes.
        all_files=sorted(all_files),
        target_files=target_files,
        source_context=state.last_error_source_context,
        max_chars=max_chars,
        # A file's entry here is only trustworthy while unchanged since the
        # post-compile snapshot that produced it - true here, since nothing
        # between that snapshot and a later gate's failure writes to the
        # worktree. Files outside that snapshot (e.g. genuinely still-missing
        # ones) simply fall back to a fresh hash inside project_implementation_source.
        known_revisions=state.validated_file_revisions,
        open_findings=state.open_findings,
        advisory_context=(
            state.error_context[
                state.error_context.index("=== Reference material found"):
            ]
            if "\n\n=== Reference material found" in state.error_context
            else ""
        ),
    )


def _materialize_candidate_content(
    deps: RepairDeps, ctx: AttemptCtx, filepath: str, file_obj: Dict[str, Any],
) -> Optional[str]:
    """Best-effort materialization of one Developer response's real resulting
    content, for the coordinated candidate-view (see
    _run_coordinated_repair_generation's "Rule 2A" note below) - NOT the
    authoritative write path (that's the existing staged_writes loop further
    down run_attempt(), which re-applies these same edits independently and
    remains the only place that can raise on a genuine anchor mismatch).
    Returns None (candidate view falls back to that file's prior/baseline
    content for the next participant) whenever there is nothing to
    materialize - a NO CHANGE NEEDED response (no content, no edits) or an
    edit whose SEARCH block doesn't match; a real anchor-mismatch failure
    still surfaces correctly, just later, through the authoritative staging
    loop's own error handling, not duplicated here."""
    content = file_obj.get("content")
    if content:
        return content
    edits = file_obj.get("edits") or []
    if not edits:
        return None
    current_file_path = os.path.join(ctx.worktree_path, filepath)
    if not os.path.exists(current_file_path):
        current_file_path = os.path.join(ctx.workspace_path, filepath)
    try:
        return deps.apply_anchored_edits(load_snapshot(current_file_path).text, edits, "")
    except (ValueError, OSError):
        return None


async def _run_coordinated_repair_generation(
    deps: RepairDeps, state: RunState, ctx: AttemptCtx, contract: Any,
    base_code_context: str, stream_callback: Optional[Callable[[str], None]],
    attempt_operation: Any, optional_sections: Tuple[Any, ...] = (),
) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    """MA9 (2026-08-29): the coordinated counterpart to a single
    _run_developer_generation(known_target_files=state.last_implicated_files)
    call - one sequential Developer call per contract.generation_order entry,
    every call sharing the SAME RepairContract framing (via
    _build_coordinated_retry_prompt) instead of _build_targeted_retry_prompt's
    single-file "focus your fix there" wording. See repair_contract.py's own
    module docstring for why this exists (PRV-06 Bucket A).

    Deliberately NOT a single known_target_files=[A, B] call: DeveloperAgent.
    _fill_missing_content's own within-batch sibling section only shows an
    earlier participant's content when that participant returned FULL file
    content (entry["content"]), never when it returned an anchored edit
    (entry["edits"], content left None) - so a batch call would silently fail
    "Rule 2A" (below) exactly whenever the model preferred a small patch over
    a full rewrite for the first participant, which this codebase's own
    REPAIR mode prompt explicitly asks it to prefer. Sequential calls, each
    orchestrated here, close that gap by materializing every participant's
    real resulting content (via _materialize_candidate_content, applying
    anchored edits the same way the authoritative staging loop does) into an
    explicit `candidate_view` dict BEFORE building the next participant's
    prompt.

    Rule 2A (2026-08-29 design review - "probably the single most load-bearing
    implementation rule in MA9"): candidate_view accumulates in memory only;
    nothing here writes to ctx.worktree_path or ctx.workspace_path. The
    authoritative worktree stays exactly as it was before this attempt began
    until the existing staged_writes/AuthorizedFileWriter pipeline further
    down run_attempt() atomically accepts (or, on any gate failure, discards)
    the WHOLE returned list - every participant, across every group, lands
    together or none does, the same all-or-nothing behavior that pipeline
    already gives any multi-file `files` list, coordinated or not.

    Group-aware (2026-08-29 v2 design review, §11/§19): walks
    contract.repair_groups in order (never a flat generation_order directly -
    that field is only the flattening of every group's own order, kept on
    the contract for convenience/backward-compat), setting
    contract.active_group_id per group purely for observability
    (developer_repair_call/repair_group_started events below) - it plays no
    role in candidate-view visibility, which already spans every group
    generated so far regardless of which one is "active." No per-group
    lightweight validation is run here (§19 explicitly calls that optional -
    "acceptable," not required); the correctness invariant this module
    guarantees is the harder one anyway: no PARTIAL commit ever happens,
    since nothing here writes to the worktree at all until the full,
    all-groups candidate passes every existing gate further down
    run_attempt().

    Returns (results, candidate_view) - PRV-06 completion (2026-08-29, "MA8.1
    <-> MA9 composition and AttemptContext correctness"): the caller's own
    shared post-generation pipeline (anchored-edit application further down
    run_attempt()) needs a `shown_context` for its own grounding check
    (edit_safety.py::apply_anchored_edits's third argument) that reflects
    this SAME coordinated candidate state - not the authoritative baseline
    workspace, which Rule 2A above guarantees is still stale at this point.
    Previously this function returned only `results`, silently discarding
    the very candidate_view its own docstring calls "probably the single
    most load-bearing implementation rule in MA9" the moment control
    returned to the caller - the caller then had nothing but an unassigned
    local variable (see run_attempt's own `active_code_context`), a real,
    live-reproduced UnboundLocalError."""
    candidate_view: Dict[str, str] = {}
    results: List[Dict[str, str]] = []
    for group in contract.repair_groups:
        contract.active_group_id = group.id
        state.record_event(RunEvent(
            kind="repair_group_started", attempt=state.attempt_number, source="attempt.run_attempt",
            authority=EventAuthority.ADVISORY,
            message=f"RepairContract '{contract.id}' entering group '{group.id}'.",
            details={
                "repair_contract_id": contract.id, "group_id": group.id,
                "artifacts": list(group.artifacts), "depends_on_group_ids": list(group.depends_on_group_ids),
            },
        ))
        for filepath in group.generation_order:
            task_desc, active_code_context = deps._build_coordinated_retry_prompt(
                ctx.goal, ctx.plan, state.error_context, contract, filepath,
                state.all_files_written, ctx.worktree_path, base_code_context,
                candidate_view=candidate_view,
                ecosystem_invariant_block=ctx.ecosystem_invariant_block,
                resource_lifecycle_block=ctx.resource_lifecycle_block,
                verification_contract_block=ctx.verification_contract_block,
                # Only the currently-active group's own members are exempt
                # from this budget (see _build_coordinated_retry_prompt's own
                # docstring) - harmless/inert for today's 2-participant case
                # (both nearly always land in the same or immediately
                # adjacent group, well under budget), load-bearing for a
                # future 6-10 participant repair.
                participant_content_budget=deps._reserve_sibling_content_budget(deps.allocation_window(ctx.kernel.config)),
            )
            state.record_event(RunEvent(
                kind="developer_repair_call", attempt=state.attempt_number, source="attempt.run_attempt",
                authority=EventAuthority.ADVISORY,
                message=f"Coordinated Developer call for '{filepath}' (group '{group.id}').",
                details={
                    "repair_contract_id": contract.id, "active_group": group.id,
                    "immediate_target": filepath, "participant_count": len(contract.participating_artifacts),
                    "candidate_revision": len(candidate_view),
                },
            ))
            file_results = await deps._run_developer_generation(
                state, ctx,
                optional_sections=optional_sections,
                task_description=task_desc,
                design_context=ctx.design,
                existing_code_context=active_code_context,
                stream_callback=stream_callback,
                model_override=None, base_url_override=None, api_key_override=None, extra_body_override=None,
                known_target_files=[filepath],
                prior_error_context=state.error_context or None,
                # Every participant, always - never narrowed to just this call's
                # own filepath. See RepairContract's own docstring: an active
                # coordinated contract must never let single-file attribution
                # collapse it back to narrow targeting; implicated_files=[filepath]
                # here would also silently disable the NO CHANGE NEEDED option
                # for every participant the CURRENT failure doesn't specifically
                # name (_fill_missing_content only offers it when
                # apply_fix_analysis is True, which requires filepath to be IN
                # implicated_files).
                implicated_files=list(contract.participating_artifacts),
                error_source_context=state.last_error_source_context or None,
                retry_temperature=ctx.kernel.config.llm.retry_temperature,
                extra_fix_instruction=deps.DeveloperAgent.SELF_CONSISTENCY_NUDGE,
                files_with_current_content=state.all_files_written,
                sibling_content_budget=deps._reserve_sibling_content_budget(deps.allocation_window(ctx.kernel.config)),
                operation_by_file=deps._operation_map(ctx, [filepath], attempt_operation, state),
                default_operation=attempt_operation,
            )
            for entry in file_results:
                if entry.get("filepath") == filepath:
                    materialized = deps._materialize_candidate_content(ctx, filepath, entry)
                    if materialized is not None:
                        candidate_view[filepath] = materialized
                        state.record_event(RunEvent(
                            kind="candidate_edit_staged", attempt=state.attempt_number,
                            source="attempt.run_attempt", authority=EventAuthority.ADVISORY,
                            message=f"Candidate staged for '{filepath}' (group '{group.id}').",
                            details={"repair_contract_id": contract.id, "artifact": filepath},
                        ))
                results.append(entry)
    return results, candidate_view
