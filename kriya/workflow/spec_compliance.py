"""Spec compliance, requirement verdicts and the contract-change classification of one attempt (full modularization
W4, E10).

Moved verbatim from ``kriya/workflow/attempt.py`` (the contiguous region from ``_spec_compliance_authoritative_context``
to ``_verified_no_change_proposal``):

- the spec-compliance arbitration: the authoritative-context block (``_spec_compliance_authoritative_context``), the
  migration precondition (``_migration_obligations_all_satisfied``), the two suppressions of a judgment that
  contradicts stronger authority (``_spec_requirements_contradicting_authority``,
  ``_spec_requirements_naming_planner_only_identifiers`` with its provenance primitives ``_identifier_local_terms``,
  ``_extract_requirement_identifier_tokens``, ``_REQUIREMENT_WORD_RE``, ``_REQUIREMENT_PROVENANCE_STOPWORDS``), the
  stage-scoped goal (``_stage_scoped_spec_compliance_goal``), the goal-spec evidence identity
  (``_goal_spec_requirement_obligation_id``, ``_goal_spec_evidence_fingerprint``, ``_settled_goal_spec_requirement``);
- the PRD-020 requirement verdicts (``_record_original_requirement_verdicts``,
  ``_downgrade_suppressed_requirement_claims``, ``_close_requirements_by_migration_gate``);
- the PRD-023 contract-change classification and escalation (``_classify_and_escalate_contract_changes``);
- the deterministic contract restorations and the no-change proposal the region also holds: the API-contract owners'
  restoration (``_restore_api_contract_owners_deterministically``), the PRD-022 ownership-redirect restoration
  (``_stage_ownership_redirect_restoration``, ``_structured_plan_paths``) and the enforce unit's verified no-change
  proposal (``_verified_no_change_proposal``).

Every verdict, arbitration, classification and escalation decision is unchanged, in the same order. The gates still
receive these names from attempt.py (``attempt._gate_deps``: GateDeps), ``terminal_gate_service`` still imports
``_close_requirements_by_migration_gate`` from attempt.py, and run_attempt still calls the restorations, the
classification and the no-change proposal by these names.

``kriya/workflow/attempt.py`` keeps every one of these names (a forwarder, or a re-export of a constant) so a test patch
on ``kriya.workflow.attempt`` keeps intercepting the moved code. What arrives in ``SpecDeps``, resolved through
``kriya.workflow.attempt`` by ``attempt._spec_deps()`` when a forwarder runs: the moved siblings' forwarders, attempt.py's
``logger`` and ``_baseline_restoration_write`` (shared with run_attempt's RESTORE_PUBLIC_CONTRACT restoration), and the
two names of the all-import-cycle ``operations`` module (``CodeOperation``, ``classify_result_operation``). Names of
modules outside that cycle that never reach this module and that no test patches on ``kriya.workflow.attempt`` are
imported here directly. This module never imports ``kriya.workflow.attempt``, ``workflow``, ``workflow_controller`` or
``kriya.cli``, nor any all-import-cycle module.

``RunState`` is the attempt's ``kriya.workflow.state.GenerationState`` and ``AttemptCtx`` its
``kriya.workflow.attempt.AttemptContext``; both are spelled ``Any`` (``kriya/workflow/stage_context.py`` says why the
state module is not imported; AttemptContext lives in attempt.py, OI-46), as is the state module's
``APIContractRecovery`` of one signature.
"""
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER, PLANNED_IMPLEMENTATION_SECTION_HEADER
from kriya.config.authority import is_human_in_the_loop
from kriya.policy.approval import resolve_approval
from kriya.workflow.edit_safety import StagedFileWrite, content_revision
from kriya.workflow.file_integrity import file_raw_digest
from kriya.workflow.file_resolution import IncompleteGenerationError
from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationRecord, ObligationStatus
from kriya.workflow.plan_schema import RequirementOwnershipRelation
from kriya.workflow.requirements import (
    CLAIM_CONTRADICTS_STRONGER_AUTHORITY,
    RequirementOutcome,
    record_requirement_closure,
    record_requirement_verdicts,
    requirement_obligation_id,
    requirement_outcomes,
    requirement_verdict_details,
    verifier_result_verdicts,
)
from kriya.workflow.run_events import EventAuthority, RunEvent

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext


@dataclass(frozen=True)
class SpecDeps:
    """What the moved spec-compliance code may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._spec_deps()``. Every field is required: a builder that omits one fails at construction, never at the
    first use."""

    # attempt.py's logger
    logger: Any
    # the kernel's all-import cycle (operations, attribution, state) and the loaders of the bodies' function-local edit_capability / context_budget / insertion_locus imports
    CodeOperation: Any
    classify_result_operation: Callable[..., Any]
    # the moved siblings (their forwarders on attempt.py)
    _extract_requirement_identifier_tokens: Callable[..., Any]
    _identifier_local_terms: Callable[..., Any]
    _migration_obligations_all_satisfied: Callable[..., Any]
    _structured_plan_paths: Callable[..., Any]
    # attempt.py's own names that stay there
    _baseline_restoration_write: Callable[..., Any]


def _spec_compliance_authoritative_context(ledger: Optional[ObligationLedger]) -> Optional[str]:
    """MA8 (spec §31) - a short "AUTHORITATIVELY ESTABLISHED" prose block
    naming which MIGRATION_COMPLETION requirements are already
    DETERMINISTIC-authority SATISFIED, injected into SpecComplianceAgent's
    own prompt BEFORE the call - see SpecComplianceAgent.check()'s own
    docstring for why this is advisory only. The mandatory backstop
    remains _spec_requirements_contradicting_authority below, applied
    AFTER the model responds regardless of whether this context was
    honored.

    Same precondition as that function (only when EVERY current migration
    obligation is SATISFIED - a still-VIOLATED requirement is exactly what
    SpecCompliance should remain free to also flag), so the two never
    disagree about when authoritative context applies."""
    if not ledger:
        return None
    migration_records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
    if not migration_records or any(
        rec.status != ObligationStatus.SATISFIED for rec in migration_records
    ):
        return None
    lines = "\n".join(f"- {rec.description}" for rec in migration_records)
    return (
        "AUTHORITATIVELY ESTABLISHED (deterministic evidence - do not report these as "
        f"missing/incomplete):\n{lines}\n\n"
        "Evaluate only requirements not already covered by the above."
    )


def _migration_obligations_all_satisfied(ledger: Optional[ObligationLedger]) -> bool:
    """True only when the ledger has at least one current MIGRATION_
    COMPLETION obligation and every one of them is SATISFIED. Shared
    precondition for every SpecCompliance arbitration path below - a
    JUDGMENT-authority verdict may only ever be suppressed/overridden when
    the DETERMINISTIC migration gate has fully confirmed the fact it
    contradicts; a still-VIOLATED (or no-obligation-recorded-at-all) case
    means judgment and determinism simply agree, or there's nothing to
    arbitrate against, and the LLM verdict must stand."""
    if not ledger:
        return False
    migration_records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
    return bool(migration_records) and not any(
        rec.status == ObligationStatus.VIOLATED for rec in migration_records
    )


def _spec_requirements_contradicting_authority(
    deps: SpecDeps, missing_requirements: List[str], ledger: Optional[ObligationLedger],
) -> Tuple[List[str], List[str]]:
    """MA8 (PRV-05 run #8, 2026-08-28) - splits SpecComplianceAgent's own
    free-text missing_requirements into (kept, contradicted).

    An entry is "contradicted" when it mentions the migration's own
    source/target identity terms (e.g. "gson", "jackson-databind") AND the
    ledger's current MIGRATION_COMPLETION obligations are ALL SATISFIED -
    i.e. the DETERMINISTIC migration gate already confirmed this exact
    fact, so a JUDGMENT-authority claim to the contrary is a contradiction,
    not a second, independent finding. Deliberately does NOT try to give
    each free-text requirement its own stable ObligationRecord id (missing_
    requirements are reworded attempt to attempt by construction - exactly
    the "never derive an id from an LLM's own error string" case this
    module's own docstring warns about) - arbitration here is a one-shot
    text correlation against the migration ledger's OWN stable ids, not a
    new obligation-tracked kind.

    Never touches a requirement while ANY current migration obligation is
    still VIOLATED - that is judgment and determinism agreeing, not a
    contradiction to arbitrate away."""
    if not deps._migration_obligations_all_satisfied(ledger):
        return list(missing_requirements), []
    migration_records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
    # Tokenized (split on "-"/"_"), not the raw identity string as a single
    # substring: an artifactId like "jackson-databind" must still correlate
    # with prose that names the human-friendly library "Jackson" - found
    # live while verifying this fix, not assumed: a full-string match missed
    # the exact hallucinated text ("the code still uses Jackson library
    # components") this arbitration exists to catch. Short tokens (<3 chars)
    # are dropped to avoid trivial false positives.
    identity_terms = {
        token
        for rec in migration_records
        for value in (rec.evidence.get("source_identity"), rec.evidence.get("target_identity"))
        if value
        for token in re.split(r"[-_]", str(value).lower())
        if len(token) >= 3
    }
    if not identity_terms:
        return list(missing_requirements), []
    kept: List[str] = []
    contradicted: List[str] = []
    for requirement in missing_requirements:
        lowered = requirement.lower()
        matched = any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in identity_terms)
        (contradicted if matched else kept).append(requirement)
    return kept, contradicted


_REQUIREMENT_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


_REQUIREMENT_PROVENANCE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "does",
    "for", "from", "has", "have", "in", "instead", "is", "it", "must",
    "named", "not", "of", "on", "or", "requires", "require", "required",
    "should", "that", "the", "this", "to", "uses", "using", "with",
}


def _identifier_local_terms(text: str, identifier: str, radius: int = 3) -> set[str]:
    """Return meaningful lexical terms close to ``identifier`` in ``text``.

    This is deliberately a provenance primitive, not a vocabulary of code
    shapes.  It knows nothing about fields, methods, routes, schemas, UI
    controls, or any particular programming language.  A term matters only
    because the Planner and the later judgment both placed it next to the
    same concrete identifier while the authoritative goal did not.
    """
    words = _REQUIREMENT_WORD_RE.findall(text)
    lowered_identifier = identifier.lower()
    terms: set[str] = set()
    for index, word in enumerate(words):
        if word.lower() != lowered_identifier:
            continue
        start = max(0, index - radius)
        end = min(len(words), index + radius + 1)
        for nearby in words[start:end]:
            normalized = nearby.lower()
            if (
                normalized != lowered_identifier
                and normalized not in _REQUIREMENT_PROVENANCE_STOPWORDS
                and len(normalized) >= 3
            ):
                terms.add(normalized)
    return terms


def _extract_requirement_identifier_tokens(requirement: str) -> List[str]:
    """Pulls candidate provenance-check terms out of a SpecCompliance
    missing_requirement string - reuses the SAME general word/stopword
    primitives _identifier_local_terms above already uses for nearby-term
    provenance, rather than a syntax-specific shape (camelCase, backtick/
    quote-wrapped, a dotted manifest filename, a version specifier, ...).

    PRV-17 (2026-09-03): an earlier version of this function matched only
    code-shaped identifiers via a hand-written regex, extended twice live
    to also catch a dotted manifest filename ("requirements.txt") and a
    PEP 508 version specifier ("python>=3.12") after each shape was found,
    separately, to slip through with zero extracted tokens. A THIRD shape
    ("requires-python = \">=3.12\"") and a fourth, pure-prose one ("minimum
    Python version 3.12") immediately proved this was an open-ended list,
    not a closed one - correctness must not depend on anticipating every
    way a Planner (or SpecComplianceAgent's own judgment) can phrase an
    invented detail. General word extraction needs no such list: _spec_
    requirements_naming_planner_only_identifiers' own section-provenance
    comparison (does this word, or its nearby context, appear only in
    Planned Implementation, never Authoritative Goal) already does the
    real work for ANY word shape, once given real candidate words to
    check - producing those candidates is this function's only job.
    Excludes short/stopword-only words the same way _identifier_local_
    terms already does, so a bare "Customer" or "The" isn't itself proof
    of anything (matched or not, the actual provenance decision is
    _spec_requirements_naming_planner_only_identifiers' own nearby-context
    comparison, not this function's word list)."""
    tokens: List[str] = []
    for word in _REQUIREMENT_WORD_RE.findall(requirement):
        if (
            len(word) >= 3
            and word.lower() not in _REQUIREMENT_PROVENANCE_STOPWORDS
            and word not in tokens
        ):
            tokens.append(word)
    return tokens


def _spec_requirements_naming_planner_only_identifiers(
    deps: SpecDeps, missing_requirements: List[str], goal_text: str,
) -> Tuple[List[str], List[str]]:
    """PRV-11 (2026-08-30) - the deterministic other half of build_subtask_
    goal_text()'s own authority-isolation split (kriya/workflow/
    workflow_controller.py). That function already labels a Planner-only
    identifier under PLANNED IMPLEMENTATION STRATEGY and SpecComplianceAgent's
    own system_prompt already instructs the model not to treat it as a
    requirement - but a prompt instruction alone is not a guarantee, the same
    "do not trust the prompt alone" principle _spec_requirements_
    contradicting_authority above already applies to the migration case, just
    never extended to this one. Live incident this closes: SpecCompliance's
    own judge repeatedly reported "the goal requires a displayName field",
    reconstructing the model's own worked counter-example from its system
    prompt almost verbatim, even though "displayName field" appears ONLY in
    the Planned Implementation Strategy section of the exact goal text it was
    given, never in the Authoritative Goal section.

    Splits missing_requirements into (kept, planner_only). An entry is
    planner_only in either of two text-grounded cases:

    * it names a concrete identifier found only in Planned Implementation; or
    * the identifier is authoritative, but the judgment attaches a nearby
      constraining term that is also attached to it in Planned Implementation
      and absent from Authoritative Goal.

    The second case closes a more subtle authority leak: a Planner can preserve
    the user's identifier while silently narrowing its representation. The
    implementation is intentionally language- and use-case-agnostic: it has no
    catalog of representation words. It correlates lexical provenance around
    the same identifier instead. Requirements with no identifier, behavioral
    terms grounded in the authoritative section, and judgment-only terms with
    no Planner provenance are always kept.

    A no-op (everything kept) when goal_text doesn't carry both section
    headers - every pre-MA6 caller, and every subtask goal without a real
    top-level goal to separate out, sees identical behavior to before this
    function existed."""
    if (
        AUTHORITATIVE_GOAL_SECTION_HEADER not in goal_text
        or PLANNED_IMPLEMENTATION_SECTION_HEADER not in goal_text
    ):
        return list(missing_requirements), []
    authoritative_text = goal_text.split(AUTHORITATIVE_GOAL_SECTION_HEADER, 1)[1].split(
        PLANNED_IMPLEMENTATION_SECTION_HEADER, 1,
    )[0]
    planned_text = goal_text.split(PLANNED_IMPLEMENTATION_SECTION_HEADER, 1)[1]
    kept: List[str] = []
    planner_only: List[str] = []
    for requirement in missing_requirements:
        tokens = deps._extract_requirement_identifier_tokens(requirement)
        if not tokens:
            kept.append(requirement)
            continue
        authoritative_tokens = [
            tok for tok in tokens
            if re.search(rf"\b{re.escape(tok)}\b", authoritative_text)
        ]
        planned_tokens = [
            tok for tok in tokens
            if re.search(rf"\b{re.escape(tok)}\b", planned_text)
        ]
        planner_only_identifier = bool(planned_tokens) and not authoritative_tokens
        planner_only_constraint = False
        for token in set(authoritative_tokens) & set(planned_tokens):
            requirement_terms = deps._identifier_local_terms(requirement, token)
            planned_terms = deps._identifier_local_terms(planned_text, token)
            authoritative_terms = deps._identifier_local_terms(authoritative_text, token)
            if (requirement_terms & planned_terms) - authoritative_terms:
                planner_only_constraint = True
                break
        (planner_only if planner_only_identifier or planner_only_constraint else kept).append(
            requirement
        )
    return kept, planner_only


def _goal_spec_requirement_obligation_id(subtask_id: str) -> str:
    return f"attempt.subtask.{subtask_id}.goal_spec_requirement"


def _stage_scoped_spec_compliance_goal(ctx: AttemptCtx) -> tuple[str, List[str]]:
    """Return only obligations due at the current structured-plan stage.

    The top-level goal remains the authority used to validate the plan.  Once
    that plan is approved, however, its acceptance-criterion ownership is the
    deterministic schedule for *when* each final requirement can be demanded.
    Feeding the whole final goal to every intermediate candidate gate made a
    project-scaffold stage fail for an endpoint explicitly owned by a later
    application stage.  Unstructured callers retain the historical behavior.

    Stage-projection ownership (PRV-17, 2026-09-03): `current`'s own
    acceptance_criteria_ids/relevant_global_invariant_ids are Planner-
    authored data this function used to trust in isolation. An earlier
    version of this fix deferred an id whenever ANY other pending subtask
    also claimed it - but bare duplicate occurrence is not proof of FUTURE
    ownership: an accidental/lazy Planner assignment onto an UNRELATED
    sibling subtask produces the exact same "also claimed elsewhere"
    signal a genuine future owner would, and silently deferring on that
    signal alone would just as easily hide a real, currently-due
    violation. This now asks the plan's own validated dependency DAG
    directly via EngineeringPlan.classify_requirement_ownership() (mirrors
    classify_file_ownership()'s existing PAST_ORDERED/CURRENT/
    FUTURE_ORDERED/UNRELATED reasoning, generalized to a claimant-id list
    since a criterion/invariant carries no file path to derive ownership
    from) - a FUTURE_ORDERED verdict requires a REAL, structurally later
    (not-yet-run, provably downstream) claimant, not just another mention.
    A genuinely stage-spanning claim with no provable DAG relationship
    (UNRELATED) falls back to this function's own pre-existing behavior
    (trust current's claim, evaluate now) rather than inventing a new
    resolution for an ambiguity the plan's own structure can't settle.
    """
    plan = ctx.structured_plan
    current_id = ctx.current_subtask_id
    if plan is None or not current_id:
        return ctx.goal, []
    current = plan.subtask_by_id(current_id)
    if current is None:
        return ctx.goal, []

    def _claimants(cid: str, field: str) -> List[str]:
        return [st.id for st in plan.subtasks if cid in getattr(st, field)]

    criteria = {criterion.id: criterion.description for criterion in plan.acceptance_criteria}
    due_acceptance_ids = [
        cid for cid in current.acceptance_criteria_ids
        if cid in criteria
        and plan.classify_requirement_ownership(current_id, _claimants(cid, "acceptance_criteria_ids"))
        != RequirementOwnershipRelation.FUTURE_ORDERED
    ]
    due = [criteria[cid] for cid in due_acceptance_ids]
    invariants = {invariant.id: invariant.statement for invariant in plan.global_invariants}
    due_invariant_ids = [
        iid for iid in current.relevant_global_invariant_ids
        if iid in invariants
        and plan.classify_requirement_ownership(current_id, _claimants(iid, "relevant_global_invariant_ids"))
        != RequirementOwnershipRelation.FUTURE_ORDERED
    ]
    due.extend(invariants[iid] for iid in due_invariant_ids)
    # Genuinely still-pending work only (claimed by a not-yet-completed
    # OTHER subtask) - pure observability, matching this field's own
    # pre-existing contract; an id only a COMPLETED subtask ever claimed is
    # done, not "future", regardless of whether current also excluded it.
    pending_subtask_ids = {
        st.id for st in plan.subtasks if st.id != current_id and st.id not in ctx.completed_subtask_ids
    }
    pending_acceptance_ids = {
        cid for st in plan.subtasks if st.id in pending_subtask_ids for cid in st.acceptance_criteria_ids
    }
    pending_invariant_ids = {
        iid for st in plan.subtasks if st.id in pending_subtask_ids for iid in st.relevant_global_invariant_ids
    }
    future_ids = sorted(
        (pending_acceptance_ids - set(due_acceptance_ids))
        | (pending_invariant_ids - set(due_invariant_ids))
    )
    scoped = (
        f"Current approved subtask: {current.id}\n"
        f"Current implementation obligation: {current.description}\n"
        "Acceptance requirements due at this stage:\n"
        + ("\n".join(f"- {item}" for item in due) if due else "- None beyond the current implementation obligation")
        + "\nOnly decide whether this current stage satisfies the requirements listed above. "
          "Requirements assigned to unfinished later subtasks are pending, not violated."
    )
    return scoped, future_ids


def _goal_spec_evidence_fingerprint(goal: str, file_contents: Dict[str, str]) -> str:
    """Correctness Continuity Part A (PRV-06, 2026-08-29) - a stable digest
    of exactly what a goal_spec_compliance verdict was based on: the
    requirement text plus every checked file's own content, byte for byte.
    Reuses edit_safety.content_revision (the same primitive the anchored-
    edit pipeline already trusts for content identity) rather than
    inventing a second hashing scheme. Two calls producing the same
    fingerprint mean the judge was shown literally the same requirement
    and the same code - the precondition evidence monotonicity requires
    before a settled verdict may be reused or a contradiction suppressed.
    A single byte of real change (either side) produces a different
    fingerprint and is always treated as genuinely new evidence, never
    "close enough" - see _settled_goal_spec_requirement's own docstring."""
    blob = goal + "\x00" + "\x00".join(
        f"{path}\x01{file_contents[path]}" for path in sorted(file_contents)
    )
    return content_revision(blob)


def _record_original_requirement_verdicts(
    state: RunState, ctx: AttemptCtx, spec_result: Dict[str, Any], fingerprint: str,
) -> None:
    """PRD-020: record the verifier's per-requirement outcome for the exact
    candidate it judged (the evidence id is the fingerprint of the goal and
    every checked file). Runs only here, after every deterministic gate of
    this attempt passed; a requirement without a readable verdict is
    UNKNOWN. The event is the run's durable lineage record."""
    requirements = ctx.requirement_set
    if requirements is None or ctx.obligation_ledger is None:
        return
    # MODEL-EVIDENCE-HARDENING-001: typed reasons and the verifier identity.
    verdicts, findings, missing_reason, missing_detail = verifier_result_verdicts(spec_result, requirements)
    outcomes = record_requirement_verdicts(
        ctx.obligation_ledger, requirements, verdicts,
        revision=state.attempt_number, evidence_fingerprint=fingerprint,
        source="attempt.goal_spec_compliance",
        gate_evidence=[f"attempt {state.attempt_number}: deterministic gates passed"],
        missing_reason=missing_reason, missing_detail=missing_detail,
        verifier=spec_result.get("verifier"),
    )
    details = requirement_verdict_details(ctx.obligation_ledger, requirements)
    state.record_event(RunEvent(
        kind="requirement.verdicts", attempt=state.attempt_number, source="attempt.goal_spec_compliance",
        authority=EventAuthority.ADVISORY,
        message="original requirement outcomes: " + ", ".join(
            f"{rid}={outcome.value}({details[rid]['reason_code']})" for rid, outcome in outcomes.items()),
        details={"requirement_set_digest": requirements.digest, "evidence_id": fingerprint,
                 "outcomes": {rid: outcome.value for rid, outcome in outcomes.items()},
                 "evidence": {rid: entry[1] for rid, entry in verdicts.items()},
                 "verdicts": {rid: details[rid] for rid in outcomes},
                 "findings": findings},
    ))


def _downgrade_suppressed_requirement_claims(
    state: RunState, ctx: AttemptCtx, suppressed: List[str], fingerprint: str,
) -> None:
    """PRD-020: a verifier claim that REQ-n is missing, suppressed because it
    contradicts stronger authority (a SATISFIED deterministic obligation, or
    an identifier only the Planner named), is not evidence either way: the
    requirement is recorded UNVERIFIED rather than left VIOLATED."""
    requirements = ctx.requirement_set
    if requirements is None or ctx.obligation_ledger is None:
        return
    claimed = {text.split(":", 1)[0].strip() for text in suppressed if ":" in text}
    ids = [rid for rid in requirements.ids if rid in claimed]
    if not ids:
        return
    verdicts = {rid: (RequirementOutcome.UNVERIFIED, "missing claim contradicts stronger authority; suppressed",
                      CLAIM_CONTRADICTS_STRONGER_AUTHORITY)
                for rid in ids}
    record_requirement_verdicts(
        ctx.obligation_ledger, requirements, verdicts, revision=state.attempt_number,
        evidence_fingerprint=fingerprint, source="attempt.goal_spec_compliance.arbitration", only=ids,
    )


def _close_requirements_by_migration_gate(
    deps: SpecDeps, state: RunState, ctx: AttemptCtx, fingerprint: str,
) -> None:
    """PRD-020: an UNVERIFIED requirement that states the migration itself -
    its own text names both the migration's source and its target identity -
    is positively verified by the DETERMINISTIC migration gate once every
    current migration obligation is SATISFIED for this candidate. The
    binding is the user's words, never the verifier's claim text; a
    requirement naming only one side (e.g. a detail of how the target
    library is used) is not the migration and stays UNVERIFIED."""
    requirements, ledger = ctx.requirement_set, ctx.obligation_ledger
    if requirements is None or ledger is None or not deps._migration_obligations_all_satisfied(ledger):
        return
    records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)

    def _terms(key: str) -> set:
        return {token for rec in records for value in [rec.evidence.get(key)] if value
                for token in re.split(r"[-_]", str(value).lower()) if len(token) >= 3}

    source_terms, target_terms = _terms("source_identity"), _terms("target_identity")
    if not source_terms or not target_terms:
        return
    outcomes = requirement_outcomes(ledger, requirements)
    for requirement in requirements.requirements:
        if outcomes.get(requirement.id) is not RequirementOutcome.UNVERIFIED:
            continue
        record = ledger.current(requirement_obligation_id(requirement.id))
        if record is None or (record.evidence or {}).get("evidence_id") != fingerprint:
            continue
        lowered = requirement.text.lower()

        def _names(terms: set, text: str = lowered) -> bool:
            return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)

        if _names(source_terms) and _names(target_terms):
            record_requirement_closure(
                ledger, requirements, requirement.id, evidence_id=fingerprint, method="migration_gate",
                detail={"migration_obligations": sorted(rec.id for rec in records)},
                source="attempt.migration_gate", revision=state.attempt_number,
            )


def _structured_plan_paths(ctx: AttemptCtx) -> set:
    """Every path an approved structured plan declares (any subtask)."""
    plan = getattr(ctx, "structured_plan", None)
    return {pf.path for st in (getattr(plan, "subtasks", None) or []) for pf in (st.planned_files or [])}


def _stage_ownership_redirect_restoration(
    deps: SpecDeps, state: RunState, ctx: AttemptCtx, staged_writes: List[StagedFileWrite],
) -> None:
    """PRD-022: after a deterministic brownfield ownership violation
    (workflow.py), the corrective retry is scoped to the grounded owner, with
    patch authority over it only. The tests the rejected candidate redirected
    and the parallel file it created are not the retry's to repair, and left
    in place they would fail the same check whatever the owner fix, making
    the retry redundant by construction. Like the RESTORE_PUBLIC_CONTRACT
    evidence restoration above, they are restored deterministically in the
    same guarded batch: each redirected test back to its exact baseline, and
    each parallel file this run created removed - unless an approved
    structured plan declares that file (the enforce terminal commit
    materializes every planned path), in which case it stays and the
    restored test alone ends the redirect. Anything the Developer
    writes again in this attempt is left as written, so a repeated choice
    still recurs and still counts toward ARCHITECTURE_CHOICE_INVALIDATED."""
    recovery = state.ownership_redirect_recovery
    if not recovery:
        return
    staged_targets = {staged.target_path for staged in staged_writes}
    restored: List[str] = []
    for test_path in recovery.get("redirected_tests", []):
        baseline = state.all_original_contents.get(test_path)
        target = os.path.join(ctx.worktree_path, test_path)
        if not baseline or target in staged_targets:
            continue
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                current = handle.read()
        except OSError:
            current = ""
        if current != baseline:
            staged_writes.append(deps._baseline_restoration_write(state, target, test_path))
            restored.append(test_path)
    removed: List[str] = []
    for candidate in recovery.get("abandoned_candidates", []):
        target = os.path.join(ctx.worktree_path, candidate)
        if (target in staged_targets or state.all_original_contents.get(candidate)
                or candidate in deps._structured_plan_paths(ctx)
                or os.path.exists(os.path.join(ctx.workspace_path, candidate)) or not os.path.isfile(target)):
            continue  # written again this attempt, or not a file this run created
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            current = handle.read()
        staged_writes.append(StagedFileWrite(
            target_path=target, content="", base_path=target,
            expected_base_revision=file_raw_digest(target), delete=True,
        ))
        removed.append(candidate)
    if restored or removed:
        state.record_event(RunEvent(
            kind="ownership.redirect_restored", attempt=state.attempt_number, source="attempt.staged_writes",
            authority=EventAuthority.AUXILIARY,
            message=f"restored redirected tests {restored}; removed abandoned parallel files {removed}",
            details={"restored_tests": restored, "removed_candidates": removed,
                     "owners": list(recovery.get("owners", []))},
        ))


async def _classify_and_escalate_contract_changes(
    deps: SpecDeps, state: RunState, ctx: AttemptCtx, violations: List[Dict[str, Any]],
    baseline_contents: Dict[str, str], candidate_contents: Dict[str, str],
    run_direct_authorizations: List[Any], authorized_changes: List[Dict[str, Any]] = (),
) -> Tuple[List[Dict[str, Any]], List[Any], Optional[str]]:
    """PRD-023: classify every public contract change the detector reported,
    then offer only the evidence-backed POTENTIALLY_DERIVED/INDETERMINATE
    ones to a human - when ``autonomy.contract_change_escalation`` is
    ``human`` and a human-in-the-loop approval callback exists. An approval
    creates revision-bound authorizations and drops exactly those changes;
    otherwise every change stays a violation (fail closed). Returns the
    remaining violations, the classifications and the escalation reason code."""
    from kriya.workflow.contract_classification import (
        CONTRACT_ESCALATION_DECLINED,
        CONTRACT_ESCALATION_UNAVAILABLE,
        ESCALATABLE,
        classify_api_violations,
        escalation_prompt,
        human_authorization,
        record_classifications,
    )

    classifications = classify_api_violations(
        violations, original_contents=baseline_contents, final_contents=candidate_contents,
        run_authorizations=run_direct_authorizations,
        human_authorizations=state.human_contract_authorizations, authorized_changes=authorized_changes,
    )
    record_classifications(ctx.obligation_ledger, classifications, revision=state.attempt_number,
                           subtask_id=ctx.current_subtask_id)
    escalatable = [c for c in classifications if c.status in ESCALATABLE]
    autonomy = ctx.kernel.config.autonomy
    if not escalatable or autonomy.contract_change_escalation != "human":
        return violations, classifications, None
    if not is_human_in_the_loop(autonomy.mode) or not ctx.approval_callback:  # OI-135: the fail-closed reader
        deps.logger.warning("Contract change needs human authorization but none can be asked - blocked: %s",
                       [c.id for c in escalatable])
        return violations, classifications, CONTRACT_ESCALATION_UNAVAILABLE
    outcome = await resolve_approval(ctx.approval_callback, [], escalation_prompt(escalatable))
    if outcome.unavailable:  # nobody was asked or nobody decided: the same stop as "none can be asked", never a human decline
        deps.logger.warning("Contract change needs human authorization but no decision could be obtained (%s) - blocked: %s", outcome.reason_code, [c.id for c in escalatable])
        return violations, classifications, CONTRACT_ESCALATION_UNAVAILABLE
    if not outcome.approved:
        return violations, classifications, CONTRACT_ESCALATION_DECLINED
    plan_revision = (getattr(ctx.structured_plan, "plan_id", None)
                     or hashlib.sha256((ctx.grounding_goal or ctx.goal).encode("utf-8")).hexdigest())
    granted = [human_authorization(c, subtask_id=ctx.current_subtask_id, plan_revision=plan_revision)
               for c in escalatable]
    state.human_contract_authorizations.extend(granted)
    for authorization in granted:
        state.record_event(RunEvent(
            kind="contract.human_authorization", attempt=state.attempt_number, source="attempt.contract_gate",
            authority=EventAuthority.AUXILIARY,
            message=f"human authorized {authorization.affected_owner}::{authorization.affected_symbol} "
                    f"({authorization.allowed_change_category.value})",
            details={"authorization_id": authorization.authorization_id,
                     "plan_revision": authorization.plan_revision,
                     "legal_scope": authorization.legal_scope,
                     "evidence": authorization.derivation_evidence},
        ))
    approved_keys = {(c.owner, c.signature) for c in escalatable}
    remaining = [v for v in violations if (v["owner"], v["removed_signature"]) not in approved_keys]
    return remaining, classifications, None


def _settled_goal_spec_requirement(
    ledger: Optional[ObligationLedger], obligation_id: Optional[str], fingerprint: str,
) -> Optional[ObligationRecord]:
    """Correctness Continuity Part A (PRV-06, 2026-08-29) - MA8 evidence
    monotonicity applied to ObligationKind.GOAL_SPEC_REQUIREMENT (defined
    since MA8, never populated until now - see docs/design.md's own
    "defined for future use" note). Returns the ledger's current record for
    this obligation only when it is SATISFIED and its own recorded evidence
    fingerprint exactly matches `fingerprint` - i.e. the SAME requirement
    text and the SAME checked-file content already satisfied it once.

    Returns None whenever there is no prior record, the prior record isn't
    SATISFIED, or the fingerprint differs by even one byte - a changed
    fingerprint is always treated as genuinely new evidence, entitled to a
    full, independent re-judgment (Part A6: unchanged evidence cannot be
    destabilized by weaker judgment; new evidence must always be free to
    invalidate). This function only ever answers "is there settled,
    unchanged evidence to protect" - it never itself skips or overrides an
    LLM call; see run_attempt's own call site for how the answer is used to
    suppress a contradictory JUDGMENT-authority verdict without ever
    letting it become failure evidence or consume a Developer retry."""
    if ledger is None or not obligation_id:
        return None
    current = ledger.current(obligation_id)
    if current is None or current.status != ObligationStatus.SATISFIED:
        return None
    if current.evidence.get("fingerprint") != fingerprint:
        return None
    return current


def _restore_api_contract_owners_deterministically(
    state: RunState, contract: Any,
) -> List[Dict[str, str]]:
    """Control-plane audit (2026-08-30): RESTORE_PUBLIC_CONTRACT's own real
    objective - "restore these exact signatures, do not solve the
    behavioral issue yet" - is a FACT Kriya already has, not a generation
    task. `state.all_original_contents[owner]` is populated at violation-
    detection time (run_attempt's own "BEFORE WRITE" early-violation
    branch), before any byte of the offending candidate that triggered
    recovery ever reached the sandbox - it is the exact, authoritative
    pre-mutation content for every owner file this phase exists to restore.

    Live incident this closes (PRV-11, 2026-08-30): asking the Developer to
    reproduce this already-known content is not merely redundant, it is
    unreliable - a real run burned all 3 RESTORE_PUBLIC_CONTRACT attempts
    with the model repeatedly re-adding the very field whose presence broke
    the signature in the first place, despite a prompt that never once
    mentioned that field. This mirrors an ALREADY-EXISTING precedent one
    phase over: protected_evidence_files (damaged callers/tests) are
    already restored deterministically, never via the Developer, a few
    hundred lines below in run_attempt's own staged-write section - this
    closes the one remaining asymmetric case (the owner file itself).

    Returns the SAME [{"filepath": ..., "content": ...}, ...] shape
    _run_developer_generation would have returned, so every downstream
    consumer (the staged-write commit, find_unrestored_public_api_contracts,
    the RESTORE_PUBLIC_CONTRACT -> REPAIR_BEHAVIOR transition) is completely
    unaware this attempt never called the Developer at all - none of that
    machinery needed to change.

    Fails closed, not silently: a missing baseline for a real violating
    owner is a genuine internal-state defect (that owner's content should
    ALWAYS have been captured at violation-detection time) - raising here,
    rather than skipping that owner or silently falling back to asking the
    Developer anyway, surfaces the bug instead of masking it."""
    files: List[Dict[str, str]] = []
    for owner in contract.owner_files:
        baseline = state.all_original_contents.get(owner)
        if baseline is None:
            raise IncompleteGenerationError(
                [owner],
                f"API_CONTRACT_RECOVERY: no captured baseline content for owner "
                f"{owner!r} - cannot restore deterministically without it.",
            )
        files.append({"filepath": owner, "content": baseline})
    return files


def _verified_no_change_proposal(
    deps: SpecDeps, state: RunState, ctx: AttemptCtx, files: List[Dict[str, Any]], missing_files: List[str],
) -> List[str]:
    """ENFORCE-VERIFIED-NO-CHANGE-001 / ENFORCE-PARTIAL-NO-CHANGE-001 (OD-3)
    / ENFORCE-PARTIAL-NO-CHANGE-COVERAGE-FREE-TEXT-001 (BACKEND-FINAL-
    CLOSURE-005): the planned paths of an enforce unit the Developer left
    untouched - a NO CHANGE answer or a byte-identical rewrite
    (ENFORCE-IDENTICAL-WRITE-COMPLETION-001, state.identical_rewrites) - when
    they explain exactly the expected files never written, else []. Only an
    existing file this unit never wrote can be untouched (a planned file still
    missing is a missing artifact; a file the unit wrote and now calls
    unchanged is not an assessment of the base); a protocol-error entry is a
    rejected response, never an assessment.

    What the untouched paths MEAN is the caller's decision, from the unit's
    effective mutation (state.all_files_written): a unit that changed nothing
    proposes them as a no-change the final attempt's deterministic gate
    evidence must verify (kriya/workflow/verified_no_change.py); a unit that
    changed another file delivers them unchanged and is judged like every
    other mutating unit. The answer is never success by itself."""
    if not missing_files or ctx.structured_plan is None or not ctx.current_subtask_id:
        return []
    subtask = ctx.structured_plan.subtask_by_id(ctx.current_subtask_id)
    planned_paths = {pf.path for pf in (subtask.planned_files if subtask is not None else [])}
    # Review F3: a restored unplanned path is never an untouched planned file.
    identical_proposals = set(state.identical_rewrites) & planned_paths
    no_change = sorted({
        f["filepath"] for f in files
        if f.get("filepath") and not f.get("protocol_error")
        and deps.classify_result_operation(f) is deps.CodeOperation.NO_CHANGE_ASSESSMENT
    } | identical_proposals)
    if not no_change or any(path in state.all_files_written for path in no_change):
        return []
    if not all(os.path.isfile(os.path.join(ctx.worktree_path, path)) for path in no_change):
        return []
    explained = {path for path in no_change if os.path.basename(path) in set(missing_files)}
    if not set(missing_files) <= {os.path.basename(path) for path in explained}:
        return []
    return sorted(explained)
