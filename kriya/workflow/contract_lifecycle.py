"""PRD-029: the ContractRegistry lifecycle for committed public API contracts.

**Who creates and updates a record, and from what evidence.** Only the
terminal commit does, through this module:

* ``derive_contract_transition`` runs BEFORE the source commit. It compares
  the candidate's deterministic public signatures
  (``file_resolution._normalized_public_signatures``, the same extractor the
  PRD-023 detector uses) with the baseline, per existing owner file:
  - Every changed API name of an owner covered by an established
    authorization (DIRECT from the goal, or HUMAN from PRD-023 escalation,
    matched exactly per (owner, symbol)) makes a ``public_api`` record. It
    is created, or advanced to a new revision, and carries the authorization
    evidence.
  - An unauthorized change is never recorded as contract fact. When it hits
    an owner that already has a record, that record is marked stale
    (``SOURCE_CHANGED_WITHOUT_AUTHORIZATION``).
  - The consumers of every changed or stale contract come from the
    detector's own name-reference scan (``name_reference_scan``) and are
    never claimed complete. They are invalidated with a reason, and the
    transition then requires downstream verification: the terminal full
    suite, on this candidate, with tests actually executed. Without it the
    transition is refused (``CONTRACT_CONSUMER_VERIFICATION_MISSING``) and
    the run cannot succeed.
* The after-state is serialized once. Its digest goes into the commit
  intent (RunRecord cycle ``contract_registry``). It is staged beside the
  live registry, promoted only after the source bytes are committed, and
  bound to the commit's own identity (``source_revision`` =
  ``<transaction id>:<candidate hash>``). See
  ``terminal_commit.commit_terminal_candidate``.
* ``complete_contract_transition`` is recovery's half. A cycle proven
  COMMITTED gets its staged transition promoted, or confirmed already
  applied. A cycle that did not commit gets it discarded. Anything else is
  NEEDS_REVIEW.

Milestone capability records (``milestone_capability``) share the same
registry, schema, strict loader, digest and resume fingerprint. Their
IMPLEMENTED marking still happens after each milestone completes (disclosed
in the PRD-029 handover).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.control.contracts import (
    CONTRACT_REGISTRY_CORRUPT,
    KIND_PUBLIC_API,
    ContractRegistry,
    ContractRegistryTransitionError,
    ContractState,
    compute_shape_hash,
    registry_digest,
)

CONTRACT_CONSUMER_VERIFICATION_MISSING = "CONTRACT_CONSUMER_VERIFICATION_MISSING"
CONTRACT_REGISTRY_TRANSITION_INCOMPLETE = "CONTRACT_REGISTRY_TRANSITION_INCOMPLETE"
CONTRACT_REGISTRY_STAGING_FAILED = "CONTRACT_REGISTRY_STAGING_FAILED"
# Commit outcomes that end a run deterministically (no generation retry can
# change them): failure_category ``contract_registry_blocked``.
CONTRACT_REGISTRY_STOP_REASON_CODES = frozenset({
    CONTRACT_CONSUMER_VERIFICATION_MISSING, CONTRACT_REGISTRY_TRANSITION_INCOMPLETE,
    CONTRACT_REGISTRY_STAGING_FAILED, CONTRACT_REGISTRY_CORRUPT,
})
STALE_UNAUTHORIZED_SOURCE_CHANGE = "SOURCE_CHANGED_WITHOUT_AUTHORIZATION"
INVALIDATED_BY_CONTRACT_REVISION = "CONTRACT_REVISION_CHANGED"
INVALIDATED_BY_STALE_CONTRACT = "CONTRACT_STALE"
CONSUMER_PROVENANCE_NAME_SCAN = "name_reference_scan"
DOWNSTREAM_VERIFIED_BY = "terminal_full_regression"

# Recovery outcomes.
TRANSITION_PROMOTED = "PROMOTED"
TRANSITION_ALREADY_APPLIED = "ALREADY_APPLIED"
TRANSITION_DISCARDED = "DISCARDED"
TRANSITION_NEEDS_REVIEW = "NEEDS_REVIEW"

_SCAN_IGNORED_DIRS = frozenset({".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"})


class ContractTransitionRefused(Exception):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class RegistryTransition:
    transaction_id: str
    before_digest: str
    after_digest: str
    after_payload: Dict[str, Any]
    created: Tuple[str, ...]
    changed: Tuple[str, ...]
    stale: Tuple[str, ...]
    invalidated_consumers: Tuple[Dict[str, Any], ...]
    downstream_verification: Dict[str, Any]

    def intent(self) -> Dict[str, Any]:
        """What the RunRecord commit cycle records about this transition."""
        return {
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "revision": self.after_payload.get("revision"),
            "source_revision": self.after_payload.get("source_revision"),
            "created": list(self.created),
            "changed": list(self.changed),
            "stale": list(self.stale),
            "invalidated_consumers": [dict(item) for item in self.invalidated_consumers],
            "downstream_verification": dict(self.downstream_verification),
        }


def public_api_contract_id(owner: str) -> str:
    return f"api:{owner}"


def _reference_scan(
    workspace_path: str, overlay: Mapping[str, str], names: Iterable[str], owner: str,
) -> Dict[str, List[str]]:
    """consumer path -> sorted API names it references (``name(``), over the
    workspace with the candidate overlaid. Each file is matched as it is
    read (never the whole workspace in memory). Name-based: it can miss
    dynamic or reflective use, so it is never a completeness claim."""
    patterns = {name: re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(") for name in sorted(set(names))}
    if not patterns:
        return {}
    consumers: Dict[str, List[str]] = {}

    def match(relpath: str, text: str) -> None:
        if relpath == owner:
            return
        hits = [name for name, pattern in patterns.items() if pattern.search(text)]
        if hits:
            consumers[relpath] = hits

    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [name for name in dirs if name not in _SCAN_IGNORED_DIRS]
        for filename in files:
            relpath = os.path.relpath(os.path.join(root, filename), workspace_path)
            if relpath in overlay:
                continue
            try:
                with open(os.path.join(root, filename), "r", encoding="utf-8", errors="replace") as handle:
                    match(relpath, handle.read())
            except OSError:
                continue
    for relpath, text in overlay.items():
        match(relpath, text)
    return dict(sorted(consumers.items()))


def _authorization_evidence(authorization: Any) -> Dict[str, Any]:
    provenance = getattr(authorization, "provenance", None)
    category = getattr(authorization, "allowed_change_category", None)
    return {
        "authorization_id": getattr(authorization, "authorization_id", None),
        "provenance": getattr(provenance, "value", provenance),
        "owner": getattr(authorization, "affected_owner", None),
        "symbol": getattr(authorization, "affected_symbol", None),
        "change_category": getattr(category, "value", category),
    }


def _establish(registry: ContractRegistry, contract_id: str) -> None:
    """An authorized, verified, committed contract is established: advance
    the current revision through the linear lifecycle to IMPLEMENTED."""
    for step in (registry.approve, registry.freeze, registry.mark_implemented):
        if registry.get(contract_id).state is ContractState.IMPLEMENTED:
            return
        step(contract_id)


def derive_contract_transition(
    *,
    workspace_path: str,
    registry: Optional[ContractRegistry],
    original_contents: Mapping[str, str],
    final_contents: Mapping[str, str],
    authorizations: Sequence[Any],
    transaction_id: str,
    candidate_hash: str,
    downstream_verified: bool,
) -> Optional[RegistryTransition]:
    """The registry transition a verified candidate implies, or None when it
    changes no contract. ``registry`` None loads the live registry strictly.
    Pure with respect to it: the registry is never mutated. Raises ContractTransitionRefused when invalidated
    consumers lack downstream verification."""
    from kriya.control.persistence import load_contract_registry
    from kriya.workflow.file_resolution import (
        _is_test_or_doc_file,
        _normalized_public_signatures,
        is_runnable_test_file,
    )

    if registry is None:
        # Strict: an unreadable registry raises ContractRegistryCorruptError,
        # which refuses the commit - never read as empty.
        registry = load_contract_registry(workspace_path)
    after = ContractRegistry.from_dict(registry.to_dict())
    source_revision = f"{transaction_id}:{candidate_hash}"
    authorized_pairs = {
        (getattr(item, "affected_owner", None), getattr(item, "affected_symbol", None)): item
        for item in authorizations
    }
    existing = {
        record.owner: record for record in after.all_records()
        if record.kind == KIND_PUBLIC_API and record.owner
    }
    created: List[str] = []
    changed: List[str] = []
    stale: List[str] = []
    invalidated: List[Dict[str, Any]] = []
    for owner in sorted(final_contents):
        original = original_contents.get(owner)
        if not original or is_runnable_test_file(owner) or _is_test_or_doc_file(owner):
            continue
        before_sigs = _normalized_public_signatures(owner, original)
        after_sigs = _normalized_public_signatures(owner, final_contents[owner])
        if before_sigs == after_sigs:
            continue
        changed_names = sorted(
            {name for sig, name in before_sigs.items() if sig not in after_sigs}
            | {name for sig, name in after_sigs.items() if sig not in before_sigs}
        )
        contract_id = public_api_contract_id(owner)
        prior = existing.get(owner)
        if all((owner, name) in authorized_pairs for name in changed_names):
            shape = sorted(after_sigs)
            consumers = _reference_scan(workspace_path, final_contents, changed_names, owner)
            reason = INVALIDATED_BY_CONTRACT_REVISION
            if prior is None:
                after.register(contract_id, name=owner, provider_milestone_id="", shape=shape)
                created.append(contract_id)
            elif prior.content_hash != compute_shape_hash(shape):
                after.apply_change(after.propose_change(
                    contract_id, shape, reason="authorized public API change committed",
                ))
                changed.append(contract_id)
            else:
                continue
            _establish(after, contract_id)
            invalidations = tuple(
                {"consumer": consumer, "contract": contract_id, "symbols": symbols, "reason": reason}
                for consumer, symbols in consumers.items()
            )
            after.replace_current(contract_id, replace(
                after.get(contract_id),
                kind=KIND_PUBLIC_API, owner=owner, source_revision=source_revision,
                consumers=tuple(consumers),
                consumer_provenance=tuple(
                    {"consumer": consumer, "symbols": symbols, "provenance": CONSUMER_PROVENANCE_NAME_SCAN}
                    for consumer, symbols in consumers.items()
                ),
                consumers_complete=False,
                authorization={
                    "changed_symbols": changed_names,
                    "authorizations": [_authorization_evidence(authorized_pairs[(owner, name)])
                                       for name in changed_names],
                },
                stale_reason=None,
                invalidated_consumers=invalidations,
            ))
            invalidated.extend(invalidations)
        elif prior is not None and prior.stale_reason is None:
            invalidations = tuple(
                {"consumer": consumer, "contract": contract_id, "reason": INVALIDATED_BY_STALE_CONTRACT}
                for consumer in prior.consumers
            )
            after.replace_current(contract_id, replace(
                prior, stale_reason=STALE_UNAUTHORIZED_SOURCE_CHANGE, invalidated_consumers=invalidations,
            ))
            stale.append(contract_id)
            invalidated.extend(invalidations)
    if not (created or changed or stale):
        return None
    verification = {
        "required": bool(invalidated),
        "satisfied": bool(downstream_verified),
        "satisfied_by": DOWNSTREAM_VERIFIED_BY if downstream_verified else None,
    }
    if invalidated and not downstream_verified:
        raise ContractTransitionRefused(
            CONTRACT_CONSUMER_VERIFICATION_MISSING,
            f"{len(invalidated)} contract consumer(s) invalidated "
            f"({sorted({item['consumer'] for item in invalidated})}) without a passing terminal full "
            "suite on this candidate",
        )
    after.revision += 1
    after.source_revision = source_revision
    payload = after.to_dict()
    return RegistryTransition(
        transaction_id=transaction_id,
        before_digest=registry.digest(),
        after_digest=registry_digest(payload),
        after_payload=payload,
        created=tuple(created), changed=tuple(changed), stale=tuple(stale),
        invalidated_consumers=tuple(invalidated),
        downstream_verification=verification,
    )


def complete_contract_transition(workspace_path: str, transaction_id: str, intent: Mapping[str, Any], *,
                                 committed: bool) -> str:
    """Recovery's half of the transaction, from the cycle's proven result.
    A committed cycle's transition is promoted exactly (or found already
    applied); an uncommitted cycle's staged transition is discarded while
    the live registry must still be the before-state; anything else is
    NEEDS_REVIEW, never forced."""
    from kriya.control.persistence import (
        discard_pending_contract_registry,
        load_contract_registry,
        promote_pending_contract_registry,
    )

    try:
        current = load_contract_registry(workspace_path).digest()
    except Exception:
        return TRANSITION_NEEDS_REVIEW
    if committed:
        if current == intent.get("after_digest"):
            discard_pending_contract_registry(workspace_path, transaction_id)
            return TRANSITION_ALREADY_APPLIED
        try:
            promote_pending_contract_registry(
                workspace_path, transaction_id,
                before_digest=intent.get("before_digest"), after_digest=intent.get("after_digest"),
            )
        except (ContractRegistryTransitionError, OSError, ValueError):
            return TRANSITION_NEEDS_REVIEW
        return TRANSITION_PROMOTED
    if current != intent.get("before_digest"):
        return TRANSITION_NEEDS_REVIEW
    discard_pending_contract_registry(workspace_path, transaction_id)
    return TRANSITION_DISCARDED
