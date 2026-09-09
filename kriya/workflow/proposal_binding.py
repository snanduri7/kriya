"""A3-P0: durable binding between an A2 `ProposedModification` and CORR-018-P1's
proven `AuthorizedSemanticRegion[]` enforcement path.

Closes the two remaining A3-D1 gaps, both already confirmed by direct source
inspection in the A3-D1/A3-D2 investigations:

1. `compute_workspace_fingerprint()` (kriya/workflow/checkpoint.py) is HEAD SHA
   + a clean/dirty BOOLEAN - two different dirty working-tree contents under
   the same HEAD produce an identical fingerprint. Kept here as one coarse
   signal (unchanged, reused verbatim - checkpoint semantics are NOT touched),
   layered under a precise one: per-file SHA-256 of the exact target file and
   every evidence file actually cited by the proposal.
2. A1's `M#`/`R#` evidence ids are invocation-local display labels, never
   durable identity. Every `EvidenceBinding` below persists the ACTUAL
   structure/content it refers to (a `stable_member_key()` - see
   `semantic_region_authority.py` - plus a normalized content hash for a Java
   member, or a file-content hash for a related file) so a later
   re-verification checks real content, never a re-scanned label.

READ-ONLY, by construction: every function here only ever opens a file in
`"rb"` mode and calls `.read()` - never a write mode, never any write-capable
helper. No `.kriya/proposals` persistence, no approval state, no generation
invocation - see this module's own test file for the structural (AST import
scan) and dynamic (patched write-capable components raise-on-call) zero-write
proofs, matching review_context.py's own A1/A2 convention.

`ProposedModification` itself (and the fields this module adds to it) stays
defined in `kriya/workflow/review_context.py` - importing it here at RUNTIME
would be circular (review_context.py imports this module's `EvidenceBinding`/
`bind_evidence`/`sha256_file`), so every reference to it here is
TYPE_CHECKING-only; functions that take a proposal only ever read its public
fields (duck-typed), never construct or mutate one.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from kriya.analyzer.java_members import JavaMember
from kriya.control.workspace_identity import workspace_identity
from kriya.workflow.checkpoint import compute_workspace_fingerprint
from kriya.workflow.semantic_region_authority import (
    AuthorizedSemanticRegion,
    RegionType,
    build_file_snapshot,
    stable_member_key,
)

if TYPE_CHECKING:
    from kriya.workflow.review_context import ProposedModification, RelatedFile

EVIDENCE_TYPE_JAVA_MEMBER = "java_member"
EVIDENCE_TYPE_RELATED_FILE = "related_file"

REASON_WRONG_WORKSPACE = "PROPOSAL_WRONG_WORKSPACE"
REASON_WORKSPACE_STALE = "PROPOSAL_WORKSPACE_STALE"
REASON_TARGET_FILE_STALE = "PROPOSAL_TARGET_FILE_STALE"
REASON_EVIDENCE_FILE_STALE = "PROPOSAL_EVIDENCE_FILE_STALE"
REASON_EVIDENCE_MISSING = "PROPOSAL_EVIDENCE_MISSING"
REASON_EVIDENCE_CHANGED = "PROPOSAL_EVIDENCE_CHANGED"
REASON_TARGET_MEMBER_MISSING = "PROPOSAL_TARGET_MEMBER_MISSING"
REASON_TARGET_MEMBER_AMBIGUOUS = "PROPOSAL_TARGET_MEMBER_AMBIGUOUS"


@dataclass(frozen=True)
class EvidenceBinding:
    """Durable identity for one piece of evidence a proposal cites.
    `display_id` (A1's own `M#`/`R#`) is diagnostics/audit only - NEVER used
    as identity anywhere in this module; every check re-resolves by
    `stable_structural_key` (java_member) or `canonical_relpath` (both kinds)
    and compares real content hashes. `source_range` is diagnostics only,
    never identity (a reformat that shifts lines must not invalidate a
    binding - matches `stable_member_key()`'s own line-independence)."""
    display_id: str
    evidence_type: str  # EVIDENCE_TYPE_JAVA_MEMBER | EVIDENCE_TYPE_RELATED_FILE
    canonical_relpath: str
    stable_structural_key: Optional[str]  # None for related_file (file-level only, see bind_evidence())
    normalized_content_hash: str
    file_content_sha256: str
    source_range: Optional[Tuple[int, int]]


@dataclass(frozen=True)
class BindingVerificationResult:
    ok: bool
    reason_codes: Tuple[str, ...] = ()
    details: Tuple[str, ...] = ()


def sha256_file(path: str) -> str:
    """Pure, deterministic, binary-safe SHA-256 of a file's actual bytes on
    disk - no text decoding, no line-ending normalization at this layer (the
    exact bytes reviewed are what's bound, per the CORE INVARIANT - "this
    exact relevant repository state"). Raises FileNotFoundError (Python's
    own, already an unambiguous "clear error on missing file") rather than
    returning a placeholder hash - never silently binds to nothing."""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read_text(path: str) -> str:
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")


def bind_evidence(
    eid: str,
    member_ids: Dict[str, JavaMember],
    relation_ids: Dict[str, "RelatedFile"],
    workspace_root: str,
    target_relpath: str,
) -> EvidenceBinding:
    """Binds one A1 evidence id (`M#` against `member_ids`, `R#` against
    `relation_ids`) to durable content. A java_member's `canonical_relpath`
    is always `target_relpath` (A1's member registry is scoped to the single
    reviewed file) - its `stable_structural_key` and `normalized_content_hash`
    reuse `semantic_region_authority.py`'s own snapshot logic verbatim (the
    SAME function CORR-018-P1's enforcement re-runs at candidate-acceptance
    time), so a binding here and a later re-verification are guaranteed to
    agree on what "the same evidence" means.

    A related_file (`R#`) has no member-level granularity in A1's own
    `RelatedFile` record (relpath/relation/detail only - see
    review_context.py) - bound at FILE level only (`file_content_sha256`),
    per A3-P0's own explicit instruction ("bind the underlying member/file
    evidence instead" when relation data can't be made member-stable).
    `normalized_content_hash` for a related file is a hash of Kriya's own
    deterministic relation/detail text (never model text) - a real,
    documented weaker binding than a java_member's (re-verification does not
    re-run the repository-context scan to re-derive this text - see this
    module's own docstring on that limitation), not a fabricated one.

    Raises ValueError (fail closed, never a silent partial binding) if a
    java_member evidence id doesn't actually resolve to a real stable key in
    its own target file - this should never happen given `member_ids` is the
    same registry the id came from, but a scan-timing mismatch must never
    produce a binding to nothing."""
    if eid in member_ids:
        member = member_ids[eid]
        target_abs = os.path.join(workspace_root, target_relpath)
        content_bytes = _content_bytes(target_abs)
        content_text = content_bytes.decode("utf-8", errors="replace")
        snap = build_file_snapshot(target_relpath, content_text)
        key = stable_member_key(target_relpath, member.enclosing_type, member.kind, member.name, member.parameter_types)
        member_snap = snap.members.get(key) if not snap.ambiguous else None
        if member_snap is None:
            raise ValueError(
                f"evidence {eid!r} did not resolve to a stable member key in {target_relpath!r} - "
                "refusing to bind evidence to nothing"
            )
        return EvidenceBinding(
            display_id=eid, evidence_type=EVIDENCE_TYPE_JAVA_MEMBER,
            canonical_relpath=target_relpath, stable_structural_key=key,
            normalized_content_hash=member_snap.body_hash,
            file_content_sha256=hashlib.sha256(content_bytes).hexdigest(),
            source_range=(member.start_line, member.end_line),
        )

    rf = relation_ids[eid]
    rf_abs = os.path.join(workspace_root, rf.relpath)
    rf_bytes = _content_bytes(rf_abs)
    return EvidenceBinding(
        display_id=eid, evidence_type=EVIDENCE_TYPE_RELATED_FILE,
        canonical_relpath=rf.relpath, stable_structural_key=None,
        normalized_content_hash=hashlib.sha256(f"{rf.relation}\x00{rf.detail}".encode("utf-8")).hexdigest(),
        file_content_sha256=hashlib.sha256(rf_bytes).hexdigest(),
        source_range=None,
    )


def _content_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def verify_proposal_bindings(proposal: "ProposedModification", workspace_root: str) -> BindingVerificationResult:
    """Read-only re-verification - never raises for an expected stale/invalid
    state (missing file, changed content, wrong workspace); always returns a
    structured result instead, so a future A3 promotion step can report
    exactly why a proposal is no longer safe to act on. Collects every
    applicable failure (not just the first) except a workspace-identity
    mismatch, which short-circuits - every other check is meaningless
    against the wrong repository entirely."""
    current_workspace_id = workspace_identity(workspace_root)
    if current_workspace_id != proposal.workspace_id:
        return BindingVerificationResult(
            ok=False, reason_codes=(REASON_WRONG_WORKSPACE,),
            details=(f"workspace identity mismatch: proposal={proposal.workspace_id!r}, current={current_workspace_id!r}",),
        )

    reasons: List[str] = []
    details: List[str] = []

    if proposal.workspace_fingerprint is not None:
        current_fp = compute_workspace_fingerprint(workspace_root)
        if current_fp != proposal.workspace_fingerprint:
            reasons.append(REASON_WORKSPACE_STALE)
            details.append(f"workspace fingerprint changed: proposal={proposal.workspace_fingerprint!r}, current={current_fp!r}")

    target_abs = os.path.join(workspace_root, proposal.target_file)
    current_target_hash: Optional[str]
    try:
        current_target_hash = sha256_file(target_abs)
    except FileNotFoundError:
        reasons.append(REASON_TARGET_FILE_STALE)
        details.append(f"target file no longer exists: {proposal.target_file!r}")
        current_target_hash = None
    else:
        if current_target_hash != proposal.target_file_sha256:
            reasons.append(REASON_TARGET_FILE_STALE)
            details.append(f"target file content changed since the proposal was built: {proposal.target_file!r}")

    if current_target_hash is not None and proposal.target_member_key:
        snap = build_file_snapshot(proposal.target_file, _read_text(target_abs))
        if snap.ambiguous:
            reasons.append(REASON_TARGET_MEMBER_AMBIGUOUS)
            details.append(f"target file scan is ambiguous: {snap.ambiguous_reason}")
        elif proposal.target_member_key not in snap.members:
            reasons.append(REASON_TARGET_MEMBER_MISSING)
            details.append(f"target member key no longer resolves: {proposal.target_member_key!r}")

    for binding in proposal.evidence_bindings:
        eb_abs = os.path.join(workspace_root, binding.canonical_relpath)
        try:
            current_eb_hash = sha256_file(eb_abs)
        except FileNotFoundError:
            reasons.append(REASON_EVIDENCE_FILE_STALE)
            details.append(f"evidence file missing: {binding.canonical_relpath!r} ({binding.display_id})")
            continue
        if current_eb_hash != binding.file_content_sha256:
            reasons.append(REASON_EVIDENCE_FILE_STALE)
            details.append(f"evidence file content changed: {binding.canonical_relpath!r} ({binding.display_id})")
            continue
        if binding.evidence_type == EVIDENCE_TYPE_JAVA_MEMBER and binding.stable_structural_key:
            eb_snap = build_file_snapshot(binding.canonical_relpath, _read_text(eb_abs))
            if eb_snap.ambiguous or binding.stable_structural_key not in eb_snap.members:
                reasons.append(REASON_EVIDENCE_MISSING)
                details.append(f"evidence member no longer resolves: {binding.stable_structural_key!r} ({binding.display_id})")
            elif eb_snap.members[binding.stable_structural_key].body_hash != binding.normalized_content_hash:
                reasons.append(REASON_EVIDENCE_CHANGED)
                details.append(f"evidence member content changed: {binding.stable_structural_key!r} ({binding.display_id})")

    return BindingVerificationResult(ok=not reasons, reason_codes=tuple(reasons), details=tuple(details))


def proposal_to_authorized_semantic_regions(proposal: "ProposedModification") -> List[AuthorizedSemanticRegion]:
    """Pure translation of a proposal's ALREADY-APPROVED-SHAPED semantics
    into CORR-018-P1's own authority representation - never approval itself,
    never a second authority system. Reads only `proposal`'s own fields (set
    once at `build_proposed_modification()` time, before any candidate
    exists) - no Planner, no candidate, no Developer, no recovery, no LLM.

    A2's current finding/proposal shape only ever expresses "fix this
    member's body" - `StructuredFinding` has no field for "this requires a
    signature change" or "this requires a new helper", so this function
    deliberately does NOT guess a `*_SIGNATURE`/`*_ADD` region into
    existence; every A3-P0 translation is `METHOD_BODY`/`CONSTRUCTOR_BODY`
    (from the bound member's own real `kind`, never inferred from text).
    This is a documented, bounded limitation (see A3-P0's own return report),
    not a silent gap - a future A2 extension that lets a proposal explicitly
    name a signature/helper region is a separate, later slice.

    An `IMPORTS` region is always granted alongside the body region - safe
    by construction, since CORR-018-P1's own import policy still requires
    any added import to be deterministically referenced inside an authorized
    region before it's permitted; granting `IMPORTS` here can never itself
    widen what's actually accepted.

    Raises ValueError (fail closed) if the proposal has no durable
    `target_member_key` - this can only happen if `build_proposed_modification()`
    was called without `workspace_root` (no durable binding was ever
    requested); never translates an ambiguous/unbound proposal into
    something a future A3 could execute."""
    if not proposal.target_member_key:
        raise ValueError(
            "proposal has no durable target_member_key - build_proposed_modification() "
            "must be called with workspace_root to produce a translatable proposal"
        )
    body_region_type = (
        RegionType.CONSTRUCTOR_BODY if proposal.target_member_kind == "constructor" else RegionType.METHOD_BODY
    )
    source = f"a2_proposal:{proposal.proposal_id}"
    return [
        AuthorizedSemanticRegion(
            relpath=proposal.target_file, region_type=body_region_type,
            member_key=proposal.target_member_key, source=source,
        ),
        AuthorizedSemanticRegion(relpath=proposal.target_file, region_type=RegionType.IMPORTS, source=source),
    ]
