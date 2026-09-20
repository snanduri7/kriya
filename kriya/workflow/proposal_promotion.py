"""A3-P2: promotes an already-persisted, explicitly-approved, currently-valid
proposal (A3-P1's own artifact) into Kriya's EXISTING generation workflow.

This is the first A3 slice that crosses from advisory/approval state into
actual mutation authority - but it does so narrowly. The control path is
strictly:

    persisted proposal
        -> load_proposal() (A3-P1, structural validation only)
        -> verify_persisted_proposal() (A3-P1, digest + binding + approval
           validity, delegating repository/evidence checks whole to
           proposal_binding.py::verify_proposal_bindings() - never
           reimplemented here)
        -> require approval_state == APPROVED and approved_and_valid
        -> build_authoritative_goal() (pure function of ONLY the proposal's
           own digest-bearing fields - target_file/target_member_key/
           proposed_change/must_preserve/verification)
        -> proposal_to_authorized_semantic_regions() (A3-P0, reused verbatim)
        -> run_generation_workflow(goal=..., authorized_semantic_regions=...)

No proposal regeneration, no Planner-derived or model-derived authority
expansion, no new write system, no new recovery system, no MA8/MA9 change.
`prepare_proposal_promotion()` never calls build_proposed_modification(), A1
review, or the Reviewer LLM - it only ever reads the exact artifact A3-P1
already persisted and validated.

CONC-001 still owns same-repository concurrency safety; the interval between
`verify_persisted_proposal()`'s check and `run_generation_workflow()`'s
invocation is not a solved TOCTOU boundary, only a minimized one (nothing
else executes in `execute_approved_proposal()` between the two).

PROPOSAL_EXECUTION_RESUME_FAILS_CLOSED (Part 22 of the design instruction):
`authorized_semantic_regions` is not itself written into or restored from a
run checkpoint (kriya/workflow/checkpoint.py has no field for it), so
`execute_approved_proposal()` below deliberately exposes no resume/resume_id
parameter at all - every promoted execution is a fresh run. The complementary
half of this guard - refusing an UNRELATED `generate --resume` from silently
resuming a checkpoint that a promoted run left behind, which would drop the
region boundary that run was executing under - lives in workflow.py's own
checkpoint save/drift-check code (see the `had_authorized_semantic_regions`
marker there), not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

from kriya.workflow.proposal_binding import proposal_to_authorized_semantic_regions
from kriya.workflow.proposal_store import (
    ApprovalState,
    load_proposal,
    verify_persisted_proposal,
)
from kriya.workflow.semantic_region_authority import AuthorizedSemanticRegion

if TYPE_CHECKING:
    from kriya.workflow.review_context import ProposedModification
    from kriya.workflow.workflow import WorkflowEngine

REASON_PROPOSAL_NOT_APPROVED = "PROPOSAL_NOT_APPROVED"
REASON_PROPOSAL_APPROVED_DIGEST_MISSING = "PROPOSAL_APPROVED_DIGEST_MISSING"
REASON_PROPOSAL_NOT_CURRENTLY_VALID = "PROPOSAL_NOT_CURRENTLY_VALID"
REASON_PROPOSAL_PROMOTION_UNSUPPORTED = "PROPOSAL_PROMOTION_UNSUPPORTED"
REASON_PROPOSAL_PROMOTION_NO_AUTHORIZED_REGION = "PROPOSAL_PROMOTION_NO_AUTHORIZED_REGION"
REASON_PROPOSAL_PROMOTION_GOAL_INVALID = "PROPOSAL_PROMOTION_GOAL_INVALID"

# A2's current proposal shape only ever binds to a Java method or constructor
# body (see proposal_binding.py::proposal_to_authorized_semantic_regions()'s
# own docstring) - any other target_member_kind value is unsupported, fail
# closed rather than guessed at.
_SUPPORTED_MEMBER_KINDS = ("method", "constructor")


@dataclass(frozen=True)
class PromotedProposal:
    """Immutable deterministic bridge from a persisted proposal to the
    existing generation workflow's own call shape. Not a second goal model -
    `authoritative_goal` IS the plain string run_generation_workflow already
    accepts; this just proves how it was derived and pins the exact regions
    that go with it."""
    proposal_id: str
    proposal_digest: str
    authoritative_goal: str
    authorized_semantic_regions: Tuple[AuthorizedSemanticRegion, ...]
    source_target_file: str
    source_target_member_key: str


@dataclass(frozen=True)
class PromotionResult:
    ok: bool
    promoted: Optional[PromotedProposal] = None
    reason_codes: Tuple[str, ...] = ()
    details: Tuple[str, ...] = ()


def build_authoritative_goal(proposal: "ProposedModification") -> str:
    """Pure, deterministic. Reads ONLY digest-bearing fields of `proposal`
    (target_file, target_member_key, proposed_change, must_preserve,
    verification - all members of proposal_store.py's own _DIGEST_FIELDS) -
    never target_member (display-only, redundant, explicitly excluded from
    the digest), never evidence/assumptions/final_confidence/authority/
    workspace binding metadata, never approval/digest/timestamp metadata.

    This makes the goal a provable pure function of exactly what the digest
    covers: an unchanged canonical proposal always produces an identical
    goal string; a change to any digest-bearing field either changes the
    goal or changes what promotion validates against (the digest itself),
    and a change to purely-operational metadata (created_at/updated_at/
    approved_at/approval_state) can NEVER change the goal, since none of
    those fields are read here at all.

    Does not summarize, expand, infer, or add implementation strategy -
    `proposed_change`/`must_preserve`/`verification` are rendered verbatim,
    under headers that keep "required change" and "required verification"
    distinct (Part 10: a verification requirement must never read as
    mutation authority)."""
    lines: List[str] = ["AUTHORITATIVE ENGINEERING CHANGE", ""]

    lines.append("Target:")
    lines.append(proposal.target_file)
    lines.append(proposal.target_member_key or "")
    lines.append("")

    lines.append("Required change:")
    lines.append(proposal.proposed_change)
    lines.append("")

    lines.append("Must preserve:")
    if proposal.must_preserve:
        lines.extend(f"- {item}" for item in proposal.must_preserve)
    else:
        lines.append("(none specified)")
    lines.append("")

    lines.append("Required verification:")
    if proposal.verification:
        lines.extend(f"- {item}" for item in proposal.verification)
    else:
        lines.append("(none specified)")

    return "\n".join(lines)


def prepare_proposal_promotion(proposal_id: str, workspace_root: str) -> PromotionResult:
    """Pure preparation/validation (Part 5) - never invokes generation itself.

    Raises `ProposalStoreError` (propagated, not caught) only for a
    STRUCTURAL load failure (not found / unsafe id / unparseable / unsupported
    schema) - there is no artifact to report a substantive refusal about in
    that case, matching proposal_store.py's own load_proposal()/
    approve_proposal() convention. Every substantive refusal (not approved,
    tampered, stale, wrong workspace, unsupported proposal shape, no
    authorized region, invalid goal) returns a structured, non-raising
    `PromotionResult` instead.

    Reuses `verify_persisted_proposal()` (A3-P1) whole for the digest/tamper/
    binding/staleness question - `approved_and_valid` already encodes
    "APPROVED, untampered, approved_digest still matches recomputed digest,
    AND currently valid against the real repository/evidence" as one boolean;
    this function never recomputes any of that itself."""
    persisted = load_proposal(proposal_id, workspace_root)  # ProposalStoreError propagates
    integrity = verify_persisted_proposal(persisted, workspace_root)

    if persisted.approval_state != ApprovalState.APPROVED.value:
        return PromotionResult(
            ok=False,
            reason_codes=(REASON_PROPOSAL_NOT_APPROVED,),
            details=(f"approval_state is {persisted.approval_state!r}, not APPROVED",),
        )
    if not persisted.approved_digest:
        # Defensive - approve_proposal() always sets this alongside APPROVED,
        # but never trust a stored state flag alone (Part 5 step 5).
        return PromotionResult(
            ok=False,
            reason_codes=(REASON_PROPOSAL_APPROVED_DIGEST_MISSING,),
            details=("approved proposal has no approved_digest recorded",),
        )
    if not integrity.approved_and_valid:
        # Folds in the specific underlying A3-P1 codes (PROPOSAL_TAMPERED /
        # PROPOSAL_BINDING_INVALID / PROPOSAL_APPROVED_DIGEST_MISMATCH /
        # PROPOSAL_APPROVAL_STALE / workspace-or-evidence staleness codes
        # from proposal_binding.py) rather than duplicating them under a new
        # name (Part 15's closing instruction).
        return PromotionResult(
            ok=False,
            reason_codes=(REASON_PROPOSAL_NOT_CURRENTLY_VALID,) + integrity.reason_codes,
            details=integrity.details or ("proposal is approved but not currently valid",),
        )

    proposal = persisted.proposal
    if proposal.target_member_kind not in _SUPPORTED_MEMBER_KINDS:
        return PromotionResult(
            ok=False,
            reason_codes=(REASON_PROPOSAL_PROMOTION_UNSUPPORTED,),
            details=(f"unsupported target_member_kind: {proposal.target_member_kind!r}",),
        )
    try:
        regions = proposal_to_authorized_semantic_regions(proposal)
    except ValueError as e:
        # Fail closed rather than let an internal ValueError escape to a
        # caller (e.g. the CLI) as an unhandled exception (Part 15's "three
        # smaller ones" #1).
        return PromotionResult(
            ok=False,
            reason_codes=(REASON_PROPOSAL_PROMOTION_UNSUPPORTED,),
            details=(str(e),),
        )
    if not regions:
        return PromotionResult(
            ok=False,
            reason_codes=(REASON_PROPOSAL_PROMOTION_NO_AUTHORIZED_REGION,),
            details=("proposal_to_authorized_semantic_regions() returned no regions",),
        )

    goal = build_authoritative_goal(proposal)
    if not goal or not goal.strip():
        return PromotionResult(
            ok=False,
            reason_codes=(REASON_PROPOSAL_PROMOTION_GOAL_INVALID,),
            details=("authoritative goal builder produced an empty goal",),
        )

    return PromotionResult(
        ok=True,
        promoted=PromotedProposal(
            proposal_id=persisted.proposal_id,
            proposal_digest=persisted.proposal_digest,
            authoritative_goal=goal,
            authorized_semantic_regions=tuple(regions),
            source_target_file=proposal.target_file,
            source_target_member_key=proposal.target_member_key or "",
        ),
    )


class ProposalPromotionError(Exception):
    """Raised by `execute_approved_proposal()` when a persisted proposal is
    not promotable - always BEFORE any generation invocation (Part 30: zero
    pre-approval generation). Carries the same structured reason_codes/
    details a refusing `PromotionResult` carries."""
    def __init__(self, reason_codes: Tuple[str, ...], details: Tuple[str, ...]):
        super().__init__("; ".join(details) or "; ".join(reason_codes) or "proposal not promotable")
        self.reason_codes = reason_codes
        self.details = details


async def execute_approved_proposal(
    proposal_id: str,
    workspace_root: str,
    we: "WorkflowEngine",
    *,
    knowledge_risk_confirmed: bool = False,
    step_callback: Optional[Callable[[str, str], Any]] = None,
    approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]] = None,
    stream_callback: Optional[Callable[[str, str], Any]] = None,
    skill_gap_callback: Optional[Callable[[str, List[str]], Any]] = None,
    skill_conflict_callback: Optional[Callable[[str, str, str, str, str], Any]] = None,
    web_lookup_callback: Optional[Callable[[List[Dict[str, str]]], Any]] = None,
    web_lookup_query_callback: Optional[Callable[[List[str], str], Any]] = None,
    protected_source_file: Optional[str] = None,
) -> Dict[str, Any]:
    """The narrow invocation wrapper (Part 16). Deliberately has NO resume/
    resume_id parameter in its signature at all - every promoted execution is
    a fresh run (PROPOSAL_EXECUTION_RESUME_FAILS_CLOSED, see this module's
    own docstring).

    Calls `prepare_proposal_promotion()` then, if and only if it succeeds,
    calls `we.run_generation_workflow()` exactly once with the deterministic
    promoted goal and the exact authorized regions A3-P0's own translation
    produced - never a broadened set, never a second orchestration path.
    Every other existing generation option (callbacks, protected_source_file,
    knowledge_risk_confirmed) passes through unchanged, matching Part 16's
    "existing generation options unchanged" instruction."""
    result = prepare_proposal_promotion(proposal_id, workspace_root)
    if not result.ok:
        raise ProposalPromotionError(result.reason_codes, result.details)
    promoted = result.promoted
    assert promoted is not None  # ok=True always carries a promoted result
    return await we.run_generation_workflow(
        goal=promoted.authoritative_goal,
        workspace_path=workspace_root,
        authorized_semantic_regions=list(promoted.authorized_semantic_regions),
        knowledge_risk_confirmed=knowledge_risk_confirmed,
        step_callback=step_callback,
        approval_callback=approval_callback,
        stream_callback=stream_callback,
        skill_gap_callback=skill_gap_callback,
        skill_conflict_callback=skill_conflict_callback,
        web_lookup_callback=web_lookup_callback,
        web_lookup_query_callback=web_lookup_query_callback,
        protected_source_file=protected_source_file,
    )
