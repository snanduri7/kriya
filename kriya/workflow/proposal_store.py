"""A3-P1: persisted proposal integrity and approval state.

Takes A3-P0's proven in-memory `ProposedModification`/`EvidenceBinding` model
and makes it safely persistable and explicitly approvable, still WITHOUT ever
invoking generation. The core invariant this module exists to hold:

    canonical proposal semantics -> proposal_digest -> explicit approval of
    THAT digest -> a later promotion step (not implemented here) must
    require the SAME digest, still currently valid.

Never: approve proposal A, have it mutated/rebuilt/regenerated, then act as
if B were approved. `approve_proposal()` never rebuilds a proposal from
current repository state (that's `build_proposed_modification()`'s job,
called once, by A2, before persistence) - it only ever approves the exact
already-persisted artifact, after re-verifying it is both byte-for-byte
un-tampered (`proposal_digest`) and still currently valid against the real
repository/evidence (`kriya/workflow/proposal_binding.py::verify_proposal_bindings()`,
reused verbatim - not duplicated here).

Reuses, rather than reinvents, this codebase's own established
conventions: the tmp-file + `os.replace()` atomic-write pattern
(`kriya/workflow/checkpoint.py::save_checkpoint()`) and the
`json.dumps(..., sort_keys=True)` canonicalization idiom
(`kriya/workflow/checkpoint.py::compute_config_fingerprint()`).

Still explicitly NOT implemented here, by instruction: promotion into an
executable generation goal, any `run_generation_workflow`/`DeveloperAgent`/
`AuthorizedFileWriter`/recovery invocation, and any change to
`semantic_region_authority.py` or `proposal_binding.py` (both reused as-is).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from kriya.workflow.proposal_binding import EvidenceBinding, verify_proposal_bindings
from kriya.workflow.review_context import PROPOSAL_APPROVAL_NOT_APPROVED, ProposedModification

SCHEMA_VERSION = 1
PROPOSALS_DIRNAME = os.path.join(".kriya", "proposals")

REASON_PROPOSAL_NOT_FOUND = "PROPOSAL_NOT_FOUND"
REASON_PROPOSAL_ID_INVALID = "PROPOSAL_ID_INVALID"
REASON_PROPOSAL_SCHEMA_UNSUPPORTED = "PROPOSAL_SCHEMA_UNSUPPORTED"
REASON_PROPOSAL_PARSE_INVALID = "PROPOSAL_PARSE_INVALID"
REASON_PROPOSAL_TAMPERED = "PROPOSAL_TAMPERED"
REASON_PROPOSAL_DIGEST_MISMATCH = "PROPOSAL_DIGEST_MISMATCH"
REASON_PROPOSAL_NOT_PENDING = "PROPOSAL_NOT_PENDING"
REASON_PROPOSAL_ALREADY_APPROVED = "PROPOSAL_ALREADY_APPROVED"
REASON_PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
REASON_PROPOSAL_BINDING_INVALID = "PROPOSAL_BINDING_INVALID"
REASON_PROPOSAL_APPROVAL_STALE = "PROPOSAL_APPROVAL_STALE"
REASON_PROPOSAL_APPROVED_DIGEST_MISMATCH = "PROPOSAL_APPROVED_DIGEST_MISMATCH"

# Digest-bearing field set (Part 6 of the A3-P1 investigation): every field
# here materially describes WHAT is being proposed and WHAT it was grounded
# against - operational metadata (created_at/updated_at/approved_at) and
# purely-redundant display text (target_member - already fully implied by
# target_member_key+target_member_kind) are deliberately excluded so
# reformatting/re-timestamping never changes a proposal's identity, while
# any change to what it actually proposes always does.
_DIGEST_FIELDS = (
    "proposal_id", "source_finding_id", "target_file", "target_member_key",
    "target_member_kind", "problem_statement", "proposed_change",
    "must_preserve", "verification", "evidence", "assumptions",
    "final_confidence", "authority", "workspace_id", "workspace_fingerprint",
    "target_file_sha256", "evidence_bindings",
)

_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class ApprovalState(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ProposalStoreError(Exception):
    """Raised only for STRUCTURAL failures (not found / unsafe id / unparseable
    / unsupported schema / missing required field) - never for a substantive
    approval-eligibility question (tamper, staleness, wrong state), which
    always returns a structured, non-raising result instead (matches this
    codebase's own `verify_proposal_bindings()` convention)."""
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


@dataclass(frozen=True)
class PersistedProposal:
    schema_version: int
    proposal_id: str
    proposal_digest: str
    proposal: ProposedModification
    approval_state: str
    approved_digest: Optional[str]
    approved_at: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    persisted: Optional[PersistedProposal]
    reason_codes: Tuple[str, ...] = ()
    details: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposalIntegrityResult:
    """`ok`: artifact integrity (untampered) AND current repository/evidence
    validity - independent of approval_state entirely (a PENDING_APPROVAL
    proposal can be `ok=True`). `approved_and_valid`: the actual future-
    promotion invariant - APPROVED, untampered, approved_digest still
    matches, AND currently valid - stronger than `approval_state ==
    APPROVED` alone (Part 21's `APPROVED_BUT_STALE` case: `approval_state`
    stays "APPROVED", lifecycle state is never silently rewritten, but
    `approved_and_valid` is False and the reason is in `reason_codes`)."""
    ok: bool
    tampered: bool
    approval_state: str
    approved_and_valid: bool
    reason_codes: Tuple[str, ...] = ()
    details: Tuple[str, ...] = ()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_proposal_id(proposal_id: str) -> str:
    if (
        not proposal_id
        or not _PROPOSAL_ID_RE.match(proposal_id)
        or ".." in proposal_id
        or proposal_id in (".", "..")
        or "/" in proposal_id or "\\" in proposal_id
    ):
        raise ProposalStoreError(REASON_PROPOSAL_ID_INVALID, f"invalid proposal id: {proposal_id!r}")
    return proposal_id


def _proposals_dir(workspace_root: str) -> str:
    return os.path.join(workspace_root, PROPOSALS_DIRNAME)


def _proposal_path(workspace_root: str, proposal_id: str) -> str:
    safe_id = _validate_proposal_id(proposal_id)
    return os.path.join(_proposals_dir(workspace_root), f"{safe_id}.json")


def _evidence_binding_to_dict(eb: EvidenceBinding) -> Dict[str, Any]:
    return {
        "display_id": eb.display_id,
        "evidence_type": eb.evidence_type,
        "canonical_relpath": eb.canonical_relpath,
        "stable_structural_key": eb.stable_structural_key,
        "normalized_content_hash": eb.normalized_content_hash,
        "file_content_sha256": eb.file_content_sha256,
        "source_range": list(eb.source_range) if eb.source_range else None,
    }


def _evidence_binding_from_dict(d: Dict[str, Any]) -> EvidenceBinding:
    source_range = d.get("source_range")
    return EvidenceBinding(
        display_id=d["display_id"], evidence_type=d["evidence_type"],
        canonical_relpath=d["canonical_relpath"], stable_structural_key=d.get("stable_structural_key"),
        normalized_content_hash=d["normalized_content_hash"], file_content_sha256=d["file_content_sha256"],
        source_range=tuple(source_range) if source_range else None,
    )


def _sorted_evidence_dicts(evidence_bindings) -> List[Dict[str, Any]]:
    dicts = [_evidence_binding_to_dict(eb) for eb in evidence_bindings]
    return sorted(dicts, key=lambda d: (d["canonical_relpath"], d["stable_structural_key"] or "", d["display_id"]))


def canonicalize_proposal_semantics(proposal: ProposedModification) -> Dict[str, Any]:
    """Pure, deterministic - the exact `_DIGEST_FIELDS` subset, evidence
    bindings sorted independent of the order `bind_evidence()` happened to
    produce them in (so evidence-order variation never changes the digest,
    per the required test list), every other field taken verbatim (`tuple`
    fields become `list` for JSON, order preserved - these ARE Kriya-
    generated deterministic sequences, not something needing re-sorting)."""
    return {
        "proposal_id": proposal.proposal_id,
        "source_finding_id": proposal.source_finding_id,
        "target_file": proposal.target_file,
        "target_member_key": proposal.target_member_key,
        "target_member_kind": proposal.target_member_kind,
        "problem_statement": proposal.problem_statement,
        "proposed_change": proposal.proposed_change,
        "must_preserve": list(proposal.must_preserve),
        "verification": list(proposal.verification),
        "evidence": list(proposal.evidence),
        "assumptions": list(proposal.assumptions),
        "final_confidence": proposal.final_confidence,
        "authority": proposal.authority,
        "workspace_id": proposal.workspace_id,
        "workspace_fingerprint": proposal.workspace_fingerprint,
        "target_file_sha256": proposal.target_file_sha256,
        "evidence_bindings": _sorted_evidence_dicts(proposal.evidence_bindings),
    }


def canonical_json_bytes(d: Dict[str, Any]) -> bytes:
    """Deterministic bytes for identical semantics: sorted keys, no
    whitespace variance, ASCII-escaped (no platform/locale-dependent
    unicode encoding differences) - the same `sort_keys=True` idiom
    `compute_config_fingerprint()` (checkpoint.py) already established in
    this codebase, reused rather than inventing a second convention."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def compute_proposal_digest(proposal: ProposedModification) -> str:
    return hashlib.sha256(canonical_json_bytes(canonicalize_proposal_semantics(proposal))).hexdigest()


def _proposal_to_json_dict(proposal: ProposedModification) -> Dict[str, Any]:
    """The full on-disk `"proposal"` object - the digest-bearing fields
    (canonicalize_proposal_semantics()'s own dict, unsorted-evidence order
    here is fine, purely a display convenience - the DIGEST always
    re-sorts) plus `target_member`, a display-only field never fed into the
    digest."""
    d = dict(canonicalize_proposal_semantics(proposal))
    d["evidence_bindings"] = [_evidence_binding_to_dict(eb) for eb in proposal.evidence_bindings]
    d["target_member"] = proposal.target_member
    return d


def _proposal_from_json_dict(d: Dict[str, Any]) -> ProposedModification:
    try:
        evidence_bindings = tuple(_evidence_binding_from_dict(e) for e in d["evidence_bindings"])
        return ProposedModification(
            proposal_id=d["proposal_id"], source_finding_id=d["source_finding_id"],
            target_file=d["target_file"], target_member=d.get("target_member", ""),
            problem_statement=d["problem_statement"], proposed_change=d["proposed_change"],
            must_preserve=tuple(d["must_preserve"]), verification=tuple(d["verification"]),
            evidence=tuple(d["evidence"]), assumptions=tuple(d["assumptions"]),
            final_confidence=d["final_confidence"], authority=d["authority"],
            approval=PROPOSAL_APPROVAL_NOT_APPROVED,
            target_member_key=d["target_member_key"], target_member_kind=d["target_member_kind"],
            workspace_id=d["workspace_id"], workspace_fingerprint=d.get("workspace_fingerprint"),
            target_file_sha256=d["target_file_sha256"], evidence_bindings=evidence_bindings,
        )
    except KeyError as e:
        raise ProposalStoreError(REASON_PROPOSAL_PARSE_INVALID, f"persisted proposal missing required field: {e}")


def _persisted_to_json_dict(persisted: PersistedProposal) -> Dict[str, Any]:
    return {
        "schema_version": persisted.schema_version,
        "proposal_id": persisted.proposal_id,
        "proposal_digest": persisted.proposal_digest,
        "proposal": _proposal_to_json_dict(persisted.proposal),
        "approval": {
            "state": persisted.approval_state,
            "approved_digest": persisted.approved_digest,
            "approved_at": persisted.approved_at,
        },
        "metadata": {
            "created_at": persisted.created_at,
            "updated_at": persisted.updated_at,
        },
    }


def _atomic_write(workspace_root: str, persisted: PersistedProposal) -> None:
    d = _proposals_dir(workspace_root)
    os.makedirs(d, exist_ok=True)
    path = _proposal_path(workspace_root, persisted.proposal_id)
    tmp_path = path + ".tmp"
    payload = _persisted_to_json_dict(persisted)
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)  # atomic on POSIX and Windows - never a partially-written final path


def persist_proposal(proposal: ProposedModification, workspace_root: str) -> PersistedProposal:
    """Creates a new PENDING_APPROVAL artifact. Refuses (ValueError, fail
    closed) a proposal that isn't durably bound - `build_proposed_modification()`
    must have been called with `workspace_root` (A3-P0) so `target_member_key`/
    `workspace_id`/`target_file_sha256` are all real, not empty defaults;
    persisting an unbound proposal would produce an artifact no future
    verification could ever meaningfully check."""
    if not proposal.target_member_key or not proposal.workspace_id or not proposal.target_file_sha256:
        raise ValueError(
            "proposal is not durably bound (build_proposed_modification() must be called with "
            "workspace_root) - refusing to persist an unverifiable proposal"
        )
    _validate_proposal_id(proposal.proposal_id)
    digest = compute_proposal_digest(proposal)
    now = _utc_now_iso()
    persisted = PersistedProposal(
        schema_version=SCHEMA_VERSION, proposal_id=proposal.proposal_id, proposal_digest=digest,
        proposal=proposal, approval_state=ApprovalState.PENDING_APPROVAL.value,
        approved_digest=None, approved_at=None, created_at=now, updated_at=now,
    )
    _atomic_write(workspace_root, persisted)
    return persisted


def load_proposal(proposal_id: str, workspace_root: str) -> PersistedProposal:
    """Structural validation only (schema version, required fields, safe
    id, file exists/parses) - does NOT check the digest or repository
    binding; a successfully-loaded `PersistedProposal` may still be
    tampered or stale. See `verify_persisted_proposal()` for that (kept
    separate on purpose - `proposal show` needs to DISPLAY a
    tampered/stale artifact's status, never refuse to even load it)."""
    path = _proposal_path(workspace_root, proposal_id)
    if not os.path.isfile(path):
        raise ProposalStoreError(REASON_PROPOSAL_NOT_FOUND, f"no persisted proposal found: {proposal_id!r}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise ProposalStoreError(REASON_PROPOSAL_PARSE_INVALID, f"could not parse persisted proposal {proposal_id!r}: {e}")

    if not isinstance(raw, dict):
        raise ProposalStoreError(REASON_PROPOSAL_PARSE_INVALID, f"persisted proposal {proposal_id!r} is not a JSON object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ProposalStoreError(
            REASON_PROPOSAL_SCHEMA_UNSUPPORTED,
            f"unsupported schema_version {raw.get('schema_version')!r} for proposal {proposal_id!r} "
            f"(expected {SCHEMA_VERSION})",
        )
    try:
        proposal = _proposal_from_json_dict(raw["proposal"])
        approval_dict = raw["approval"]
        metadata_dict = raw["metadata"]
        return PersistedProposal(
            schema_version=raw["schema_version"], proposal_id=raw["proposal_id"],
            proposal_digest=raw["proposal_digest"], proposal=proposal,
            approval_state=approval_dict["state"], approved_digest=approval_dict.get("approved_digest"),
            approved_at=approval_dict.get("approved_at"),
            created_at=metadata_dict["created_at"], updated_at=metadata_dict["updated_at"],
        )
    except KeyError as e:
        raise ProposalStoreError(REASON_PROPOSAL_PARSE_INVALID, f"persisted proposal {proposal_id!r} missing required field: {e}")


def verify_persisted_proposal(persisted: PersistedProposal, workspace_root: str) -> ProposalIntegrityResult:
    """Read-only. Checks BOTH independent layers Part 16 asks for - artifact
    integrity (digest) and repository/evidence validity (delegated whole to
    `verify_proposal_bindings()`, never reimplemented here) - and reports
    the Part 21 `APPROVED_BUT_STALE` case explicitly via `approved_and_valid`
    without ever rewriting `approval_state` itself."""
    reasons: List[str] = []
    details: List[str] = []

    recomputed = compute_proposal_digest(persisted.proposal)
    tampered = recomputed != persisted.proposal_digest
    if tampered:
        reasons.append(REASON_PROPOSAL_TAMPERED)
        details.append(f"stored digest {persisted.proposal_digest!r} != recomputed {recomputed!r}")

    binding_result = verify_proposal_bindings(persisted.proposal, workspace_root)
    if not binding_result.ok:
        reasons.append(REASON_PROPOSAL_BINDING_INVALID)
        reasons.extend(binding_result.reason_codes)
        details.extend(binding_result.details)

    is_approved = persisted.approval_state == ApprovalState.APPROVED.value
    approved_digest_matches = is_approved and persisted.approved_digest == recomputed
    if is_approved and not approved_digest_matches:
        reasons.append(REASON_PROPOSAL_APPROVED_DIGEST_MISMATCH)
        details.append("approved_digest no longer matches the proposal's current recomputed digest")

    approved_and_valid = is_approved and not tampered and binding_result.ok and approved_digest_matches
    if is_approved and not approved_and_valid:
        reasons.append(REASON_PROPOSAL_APPROVAL_STALE)
        details.append("proposal was approved but is no longer currently valid (tamper and/or repository/evidence drift)")

    return ProposalIntegrityResult(
        ok=(not tampered and binding_result.ok),
        tampered=tampered, approval_state=persisted.approval_state,
        approved_and_valid=approved_and_valid,
        reason_codes=tuple(reasons), details=tuple(details),
    )


def approve_proposal(proposal_id: str, workspace_root: str) -> ApprovalResult:
    """Approves the EXACT already-persisted artifact, never rebuilding or
    re-running any part of A1/A2 (Part 18's hard invariant). Follows Part
    17's exact ordered checklist: state check, then digest integrity, then
    full repository/evidence binding (workspace identity/fingerprint,
    target file hash, evidence hashes, structural evidence - all delegated
    to `verify_proposal_bindings()`) - only writes APPROVED if every check
    passes. Never raises for a substantive refusal (tamper/stale/wrong
    state) - always a structured `ApprovalResult`; `ProposalStoreError`
    still propagates for a structural load failure (not found/invalid id/
    bad schema), since there is no artifact to report a refusal about."""
    persisted = load_proposal(proposal_id, workspace_root)

    if persisted.approval_state == ApprovalState.APPROVED.value:
        return ApprovalResult(ok=False, persisted=persisted, reason_codes=(REASON_PROPOSAL_ALREADY_APPROVED,),
                               details=("this proposal is already approved",))
    if persisted.approval_state == ApprovalState.REJECTED.value:
        return ApprovalResult(ok=False, persisted=persisted, reason_codes=(REASON_PROPOSAL_REJECTED,),
                               details=("a rejected proposal cannot be approved - create a new proposal instead",))
    if persisted.approval_state != ApprovalState.PENDING_APPROVAL.value:
        return ApprovalResult(ok=False, persisted=persisted, reason_codes=(REASON_PROPOSAL_NOT_PENDING,),
                               details=(f"unexpected approval state: {persisted.approval_state!r}",))

    recomputed_digest = compute_proposal_digest(persisted.proposal)
    if recomputed_digest != persisted.proposal_digest:
        return ApprovalResult(ok=False, persisted=persisted, reason_codes=(REASON_PROPOSAL_TAMPERED,),
                               details=(f"stored digest {persisted.proposal_digest!r} != recomputed {recomputed_digest!r}",))

    binding_result = verify_proposal_bindings(persisted.proposal, workspace_root)
    if not binding_result.ok:
        return ApprovalResult(
            ok=False, persisted=persisted,
            reason_codes=(REASON_PROPOSAL_BINDING_INVALID,) + binding_result.reason_codes,
            details=binding_result.details,
        )

    now = _utc_now_iso()
    approved = dataclasses.replace(
        persisted, approval_state=ApprovalState.APPROVED.value,
        approved_digest=recomputed_digest, approved_at=now, updated_at=now,
    )
    _atomic_write(workspace_root, approved)
    return ApprovalResult(ok=True, persisted=approved)


def reject_proposal(proposal_id: str, workspace_root: str) -> PersistedProposal:
    """Immutable once rejected (Part 22 - prefer immutable rejection over a
    reset mechanism for v1); idempotent if called again on an already-
    rejected proposal."""
    persisted = load_proposal(proposal_id, workspace_root)
    if persisted.approval_state == ApprovalState.REJECTED.value:
        return persisted
    now = _utc_now_iso()
    rejected = dataclasses.replace(persisted, approval_state=ApprovalState.REJECTED.value, updated_at=now)
    _atomic_write(workspace_root, rejected)
    return rejected
