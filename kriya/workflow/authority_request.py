"""BACKEND-READINESS-004 (owner decision D2): typed authority requests for uncovered mandatory claims.

When the sealed verification contract refuses a goal as VERIFICATION_AUTHORITY_REQUIRED, the operator should not
have to understand Kriya's evidence architecture to answer it. For every residual (requirement, claim) Kriya emits one
``AuthorityRequest``: what must be proven and at which scope, what evidence the contract already binds for that
statement and why it does not close this claim, the authority types that would (cheapest safe closer first), how each
is supplied (the CLI flag), a coverage skeleton for the external-authority manifest and a disposition skeleton - both
UNSEALED: the ``why`` / ``reason`` are empty and only the operator writes them. The requests are deterministic
(computed from the contract alone, no model), sealed content-addressed under ``<state>/authority-requests/`` keyed by
the contract digest, carried on the refusal result (``authority_requests``) and recorded as the run event
``verification_contract.authority_requested`` (KUP reads it from the trace row).

A request can PROPOSE authority. It can never AUTHORIZE it: nothing here closes a claim, and a skeleton becomes
authority only when the operator completes it and binds it through the existing sealed paths (--acceptance,
--acceptance-approval, --verification-authority, --requirement-disposition).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from kriya.workflow.contract_compilation import (
    AUTHORITY_ACCEPTANCE_APPROVAL,
    AUTHORITY_ACCEPTANCE_FILE,
    AUTHORITY_EXTERNAL_COMMAND,
    AUTHORITY_GOAL_EXAMPLES,
    AUTHORITY_OPERATOR_DISPOSITION,
    STATUS_AUTHORITY_REQUIRED,
    ContractEntry,
    ResidualClaim,
    VerificationContract,
    _closer_contract,
)
from kriya.workflow.requirement_scopes import PROCEDURE
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_GENERAL,
    DOCUMENTATION_CLAIM,
    REGRESSION_PRESERVATION,
)

logger = logging.getLogger(__name__)

AUTHORITY_REQUEST_FORMAT = "kriya.authority_request/1"
AUTHORITY_REQUEST_STORE_DIR = "authority-requests"

# Cheapest safe closer first: the goal's own words, an acceptance file, a sealed oracle, a human approval, and -
# never a closer - the operator removing the claim from the obligation set.
_COST_ORDER: Tuple[str, ...] = (AUTHORITY_GOAL_EXAMPLES, AUTHORITY_ACCEPTANCE_FILE, AUTHORITY_EXTERNAL_COMMAND,
                                AUTHORITY_ACCEPTANCE_APPROVAL, AUTHORITY_OPERATOR_DISPOSITION)
_HOW_TO_SUPPLY: Mapping[str, str] = {
    AUTHORITY_GOAL_EXAMPLES: "restate the statement with an exact example line (`expr -> literal`, a doctest session "
                             "or `expr -> raises Error`); Kriya compiles it before any model call (Python projects)",
    AUTHORITY_ACCEPTANCE_FILE: "kriya generate --acceptance <file outside the workspace> (B2: closes EXACT statements)",
    AUTHORITY_EXTERNAL_COMMAND: "kriya generate --verification-authority <manifest.json outside the workspace> "
                                "(kriya.verification_authority/1; runs under containment; the coverage skeleton below)",
    AUTHORITY_ACCEPTANCE_APPROVAL: "kriya generate --acceptance <file> --acceptance-approval <approval.json> (B3: the "
                                   "operator accepts the suite as sufficient for a GENERAL statement)",
    AUTHORITY_OPERATOR_DISPOSITION: "kriya generate --requirement-disposition <disposition.json outside the workspace> "
                                    "(REJECTED_FALSE_PREMISE | HISTORICAL_CONTEXT | INFORMATIONAL_CONTEXT | OUT_OF_SCOPE "
                                    "with a reason; the statement is reported, never satisfied)",
}
_DISPOSITION_OPTION = (AUTHORITY_OPERATOR_DISPOSITION + " (REJECTED_FALSE_PREMISE, HISTORICAL_CONTEXT, "
                       "INFORMATIONAL_CONTEXT or OUT_OF_SCOPE: removes the claim from the obligation set, never closes it)")


def _must_prove(entry: ContractEntry, residual: ResidualClaim) -> str:
    claim, strength = residual.claim, residual.strength
    if claim == BEHAVIOR and strength == BEHAVIOR_GENERAL:
        return ("the general rule the statement asserts, for every input it ranges over - finite passing cases never "
                "prove it (B2-COV); a reference or property oracle, or an operator-approved suite, can")
    if claim == BEHAVIOR and PROCEDURE in entry.scope.scopes:
        return "the stated procedure executed on the candidate with its stated outcome"
    if claim == BEHAVIOR:
        return "the exact outcome(s) the statement names, observed on the candidate" if strength == BEHAVIOR_EXACT \
            else "the behaviour the statement asserts, observed on the candidate"
    if claim == API_PRESERVATION:
        return "every public signature of the base revision is present and unchanged in the candidate"
    if claim == DOCUMENTATION_CLAIM:
        return "the named documentation referent documents the subject the statement names"
    if claim == REGRESSION_PRESERVATION:
        return "the named pre-existing tests execute and pass on the candidate"
    return f"the {claim} claim of the statement holds on the candidate"


def _ordered(acceptable: Sequence[str]) -> List[str]:
    """The residual's acceptable authority strings, cheapest first, with the disposition option last."""
    def rank(item: str) -> int:
        for index, kind in enumerate(_COST_ORDER):
            if item.startswith(kind):
                return index
        return len(_COST_ORDER) - 1
    ordered = sorted(dict.fromkeys(acceptable), key=rank)
    return ordered + [_DISPOSITION_OPTION]


@dataclass(frozen=True)
class AuthorityRequest:
    requirement_id: str
    text: str
    claim: str
    strength: Optional[str]
    scopes: Tuple[str, ...]
    must_prove: str
    existing_evidence: Tuple[Dict[str, Any], ...]
    why_insufficient: str
    acceptable_authorities: Tuple[str, ...]
    cheapest_safe_closer: str
    how_to_supply: Mapping[str, str] = field(default_factory=dict)
    coverage_skeleton: Mapping[str, Any] = field(default_factory=dict)
    disposition_skeleton: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"requirement_id": self.requirement_id, "text": self.text, "claim": self.claim, "strength": self.strength,
                "scopes": list(self.scopes), "must_prove": self.must_prove,
                "existing_evidence": [dict(e) for e in self.existing_evidence],
                "why_insufficient": self.why_insufficient, "acceptable_authorities": list(self.acceptable_authorities),
                "cheapest_safe_closer": self.cheapest_safe_closer, "how_to_supply": dict(self.how_to_supply),
                "coverage_skeleton": dict(self.coverage_skeleton), "disposition_skeleton": dict(self.disposition_skeleton),
                "authorizes": "nothing: a request proposes authority, only the operator seals it"}


def authority_requests(contract: VerificationContract) -> List[AuthorityRequest]:
    """One request per residual (requirement, claim) of every AUTHORITY_REQUIRED
    entry of ``contract`` - pure, no model, no repository access."""
    emitted: List[AuthorityRequest] = []  # named so the PRD-012 client inventory never reads it as the HTTP library
    for entry in contract.entries:
        if entry.status != STATUS_AUTHORITY_REQUIRED:
            continue
        text_digest = hashlib.sha256((entry.text or "").encode("utf-8")).hexdigest()
        existing = tuple({"claim": b.claim, "closer": b.closer, "authority": b.authority_kind,
                          "proves": _closer_contract(b.closer)["pass"]} for b in entry.bindings)
        for residual in entry.residual:
            ordered = _ordered(residual.acceptable_authorities)
            cheapest = ordered[0]
            kinds = [kind for kind in _COST_ORDER if any(item.startswith(kind) for item in ordered)]
            emitted.append(AuthorityRequest(
                requirement_id=entry.requirement_id, text=entry.text, claim=residual.claim, strength=residual.strength,
                scopes=tuple(entry.scope.scopes), must_prove=_must_prove(entry, residual), existing_evidence=existing,
                why_insufficient=residual.why + (("; the bound evidence proves other claims of this statement only")
                                                 if existing else "; no deterministic evidence is bound to this statement"),
                acceptable_authorities=tuple(ordered), cheapest_safe_closer=cheapest,
                how_to_supply={kind: _HOW_TO_SUPPLY[kind] for kind in kinds},
                coverage_skeleton={"requirement_id": entry.requirement_id, "requirement_text_sha256": text_digest,
                                   "claim": residual.claim,
                                   "accepted_strength": residual.strength if residual.claim == BEHAVIOR else None,
                                   "accept_as_sufficient": True, "why": ""},
                disposition_skeleton={"requirement_id": entry.requirement_id, "requirement_text_sha256": text_digest,
                                      "claim": residual.claim, "disposition": "", "reason": "", "evidence": []},
            ))
    return emitted


def request_document(contract: VerificationContract, requests: Sequence[AuthorityRequest]) -> Dict[str, Any]:
    return {"format": AUTHORITY_REQUEST_FORMAT, "contract_digest": contract.digest, "goal_digest": contract.goal_digest,
            "requirement_set_digest": contract.requirement_set_digest, "base_revision": contract.base_revision,
            "project_language": contract.project_language, "requests": [r.to_dict() for r in requests]}


def seal_authority_requests(contract: VerificationContract, requests: Sequence[AuthorityRequest], state_root: str) -> str:
    """Store the requests content-addressed by the contract digest under
    Kriya's state directory (never the workspace); idempotent."""
    store = os.path.join(state_root, AUTHORITY_REQUEST_STORE_DIR)
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{contract.digest}.json")
    if not os.path.isfile(stored):
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(request_document(contract, requests), handle, sort_keys=True, indent=1, default=str)
        os.replace(temporary, stored)
    return stored


def seal_requests_for_refusal(config: Any, contract: Any, admission: Any) -> Optional[str]:
    """At a VERIFICATION_AUTHORITY_REQUIRED refusal: seal the contract's
    requests (already on ``admission.authority_requests``) and return the
    stored path; None when there is no state directory or nothing to seal."""
    from kriya.core.state_paths import resolve_state_directory

    requests = getattr(admission, "authority_requests", None) or []
    if not requests or config is None or contract is None:
        return None
    try:
        return seal_authority_requests(contract, requests, resolve_state_directory(config)[0])
    except Exception as error:  # the refusal stands with the requests on the result; the store is evidence
        # Review F8: never silent - the refusal and its event carry why the store has no copy.
        logger.warning("Authority requests not sealed: %s: %s", type(error).__name__, error)
        admission.authority_requests_sealing_error = f"{type(error).__name__}: {error}"
        return None
