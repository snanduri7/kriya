"""PRD-023: derived contract change classification, then escalation.

``file_resolution.find_brownfield_public_api_changes`` reports every
established public signature a candidate removed or reshaped while other
files call it, after dropping the changes a DIRECT authorization covers
(CORR-016, ``contract_authority.py``). It stays the one detector. This
module classifies each remaining violation, from evidence only, before any
escalation is considered:

- AUTHORIZED_DIRECT: a DIRECT authorization covers it (dropped by the
  detector itself; kept here for completeness of the vocabulary).
- AUTHORIZED_HUMAN: a human-approved, revision-bound authorization covers it.
- UNAUTHORIZED (clear, blocking, never escalated): the symbol is gone while
  callers still name it; the candidate left a caller on the old shape; or the
  goal authorizes no contract change at all in this run.
- POTENTIALLY_DERIVED: every caller was co-updated by the candidate and the
  owner references a symbol or owner a DIRECT authorization of this run
  changes - the evidence a derived change needs, not proof of one.
- INDETERMINATE: every caller was co-updated and the goal does authorize a
  contract change elsewhere in this run, but no supported relationship links
  this owner to it.

Only POTENTIALLY_DERIVED and INDETERMINATE may be offered to a human, and only
when ``autonomy.contract_change_escalation`` is ``human``. The offer needs a
human-in-the-loop run with an approval callback; otherwise the change stays
blocked (CONTRACT_ESCALATION_UNAVAILABLE). An approval creates a revision-
bound ``ContractEvolutionAuthorization`` (provenance HUMAN) naming owner,
symbol, change category, scope and evidence. Model prose never creates one.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.workflow.contract_authority import (
    ChangeCategory,
    ContractEvolutionAuthorization,
    human_contract_authorization,
)
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)

CONTRACT_ESCALATION_UNAVAILABLE = "CONTRACT_ESCALATION_UNAVAILABLE"
CONTRACT_ESCALATION_DECLINED = "CONTRACT_ESCALATION_DECLINED"


class ContractChangeStatus(str, Enum):
    AUTHORIZED_DIRECT = "authorized_direct"
    AUTHORIZED_HUMAN = "authorized_human"
    UNAUTHORIZED = "unauthorized"
    POTENTIALLY_DERIVED = "potentially_derived"
    INDETERMINATE = "indeterminate"


ESCALATABLE = frozenset({ContractChangeStatus.POTENTIALLY_DERIVED, ContractChangeStatus.INDETERMINATE})


@dataclass(frozen=True)
class ContractChangeClassification:
    owner: str
    signature: str
    api_name: str
    change_category: str  # "modify" (reshaped) | "remove"
    status: ContractChangeStatus
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"contract_change.{self.owner}::{self.signature}"

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "status": self.status.value, "id": self.id}


def _type_name(path: str) -> str:
    base = os.path.basename(path)
    return base.rsplit(".", 1)[0] if "." in base else base


def _names(text: str, name: str) -> bool:
    return bool(name) and re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text or "") is not None


def classify_api_violations(
    violations: Sequence[Mapping[str, Any]], *, original_contents: Mapping[str, str],
    final_contents: Mapping[str, str], run_authorizations: Sequence[ContractEvolutionAuthorization] = (),
    human_authorizations: Sequence[ContractEvolutionAuthorization] = (),
    authorized_changes: Sequence[Mapping[str, Any]] = (),
) -> List[ContractChangeClassification]:
    """Classify the detector's violations, and the ``authorized_changes`` an
    authorization already covered. ``run_authorizations`` are every DIRECT
    authorization of the run (not only this subtask's): relationship evidence
    may point at an upstream owner another subtask changes."""
    from kriya.workflow.file_resolution import _normalized_public_signatures

    human = {(a.affected_owner, a.affected_symbol, a.allowed_change_category.value): a for a in human_authorizations}
    results: List[ContractChangeClassification] = []
    authorized_keys = {(v["owner"], v["removed_signature"]) for v in authorized_changes}
    for violation in [*authorized_changes, *violations]:
        owner, signature = violation["owner"], violation["removed_signature"]
        original = original_contents.get(owner, "")
        final = final_contents.get(owner, "")
        api_name = _normalized_public_signatures(owner, original).get(signature, signature)
        still_present = api_name in set(_normalized_public_signatures(owner, final).values())
        category = "modify" if still_present else "remove"
        callers = list(violation.get("evidence_files") or [])

        # This iteration's values are bound as defaults (B023), never read late.
        def classified(status: ContractChangeStatus, reason: str, *,
                       _identity: Tuple[str, str, str, str] = (owner, signature, api_name, category),
                       _callers: Tuple[str, ...] = tuple(callers), **extra: Any) -> None:
            bound_owner, bound_signature, bound_api_name, bound_category = _identity
            results.append(ContractChangeClassification(
                owner=bound_owner, signature=bound_signature, api_name=bound_api_name,
                change_category=bound_category, status=status, reason=reason,
                evidence={"callers": list(_callers), **extra}))

        approval = human.get((owner, api_name, category))
        if (owner, signature) in authorized_keys and approval is None:
            direct = [a.authorization_id for a in run_authorizations
                      if (a.affected_owner, a.affected_symbol) == (owner, api_name)]
            classified(ContractChangeStatus.AUTHORIZED_DIRECT, "the goal directly authorizes this change",
                       authorization_ids=direct)
            continue
        if approval is not None:
            classified(ContractChangeStatus.AUTHORIZED_HUMAN, "a human-approved authorization covers it",
                       authorization_id=approval.authorization_id)
            continue
        if not still_present:
            classified(ContractChangeStatus.UNAUTHORIZED,
                       f"{api_name} was removed while {len(callers)} caller(s) still name it")
            continue
        if not run_authorizations:
            classified(ContractChangeStatus.UNAUTHORIZED, "the goal authorizes no contract change in this run")
            continue
        stale = [c for c in callers
                 if c not in final_contents or final_contents.get(c) == original_contents.get(c)]
        if stale:
            classified(ContractChangeStatus.UNAUTHORIZED,
                       "callers were left on the old shape", stale_callers=stale)
            continue
        # Supported relationships to a contract this run is authorized to
        # change: the owner references it, or a co-updated caller maps one to
        # the other (it references both, as a mapper/consumer does).
        relationship: List[str] = []
        related: List[str] = []
        for a in run_authorizations:
            if a.affected_owner == owner:
                continue
            upstream = (a.affected_symbol, _type_name(a.affected_owner))
            if any(_names(original, name) for name in upstream):
                relationship.append(f"{owner} references {a.affected_symbol} ({a.affected_owner})")
                related.append(a.authorization_id)
                continue
            for caller in callers:
                caller_text = original_contents.get(caller, "") + "\n" + final_contents.get(caller, "")
                if any(_names(caller_text, name) for name in upstream) and _names(caller_text, _type_name(owner)):
                    relationship.append(f"{caller} maps {a.affected_symbol} ({a.affected_owner}) to {owner}")
                    related.append(a.authorization_id)
                    break
        if related:
            classified(ContractChangeStatus.POTENTIALLY_DERIVED,
                       "callers co-updated; a supported relationship links this owner to a contract this run "
                       "is authorized to change",
                       upstream_authorizations=sorted(set(related)), relationship=relationship)
            continue
        classified(ContractChangeStatus.INDETERMINATE,
                   "callers co-updated and this run authorizes a contract change elsewhere, but no supported "
                   "relationship links this owner to it",
                   run_authorizations=[a.authorization_id for a in run_authorizations])
    return results


def human_authorization(
    classification: ContractChangeClassification, *, subtask_id: Optional[str], plan_revision: str,
    approver_note: str = "",
) -> ContractEvolutionAuthorization:
    """The revision-bound record a human approval of ``classification``
    creates (minted in contract_authority.py, the only minting module)."""
    return human_contract_authorization(
        owner=classification.owner, symbol=classification.api_name,
        category=ChangeCategory(classification.change_category),
        source_requirement_id=classification.id, subtask_id=subtask_id, plan_revision=plan_revision,
        evidence={"classification": classification.to_dict(), "approver_note": approver_note},
    )


def escalation_prompt(classifications: Sequence[ContractChangeClassification]) -> str:
    lines = []
    for c in classifications:
        detail = "; ".join(c.evidence.get("relationship") or []) or c.reason
        lines.append(f"- {c.owner}: {c.signature} ({c.change_category}, {c.status.value}) - {detail}; "
                     f"callers updated: {', '.join(c.evidence.get('callers') or [])}")
    return (
        "CONTRACT CHANGE NEEDS YOUR AUTHORIZATION: the change below alters an established public "
        "signature. Kriya could not establish from the goal alone that it is authorized:\n"
        + "\n".join(lines)
        + "\nApprove to authorize exactly these owner/symbol/category changes for this plan revision."
    )


def record_classifications(ledger: Optional[ObligationLedger], classifications: Iterable[ContractChangeClassification],
                           *, revision: Any, subtask_id: Optional[str]) -> None:
    """Persist each classification as contract evidence (non-terminal: the
    gate that raised the violation decides)."""
    if ledger is None:
        return
    status_map = {
        ContractChangeStatus.AUTHORIZED_DIRECT: ObligationStatus.SATISFIED,
        ContractChangeStatus.AUTHORIZED_HUMAN: ObligationStatus.SATISFIED,
        ContractChangeStatus.UNAUTHORIZED: ObligationStatus.VIOLATED,
        ContractChangeStatus.POTENTIALLY_DERIVED: ObligationStatus.INDETERMINATE,
        ContractChangeStatus.INDETERMINATE: ObligationStatus.INDETERMINATE,
    }
    for c in classifications:
        ledger.record(ObligationRecord(
            id=c.id, kind=ObligationKind.CONTRACT_CHANGE_CLASSIFICATION, status=status_map[c.status],
            authority=(ObligationAuthority.DETERMINISTIC if c.status in (
                ContractChangeStatus.UNAUTHORIZED, ContractChangeStatus.AUTHORIZED_DIRECT)
                else ObligationAuthority.GROUNDED),
            description=f"{c.owner}: {c.signature} ({c.status.value})", source="contract_classification",
            revision=revision, evidence=c.to_dict(), owner_subtask_id=subtask_id, terminal_required=False,
            repair_scope=(c.owner,),
        ))
