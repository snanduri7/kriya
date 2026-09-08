"""CORR-016 (P9/PRV-08, 2026-09-08 - DIRECT-authorization-only implementation):
the smallest slice of the accepted architecture design
(docs/architecture/CORR016_AUTHORIZED_CONTRACT_EVOLUTION_DESIGN.md, Revision 2)
authorized for implementation. DIRECT authorization only - DERIVED remains
DESIGNED BUT DEFERRED (see the design doc's own §5/§19): no current production
scenario demonstrates the need, and the general case (whether a preserving
implementation exists) is not soundly decidable from repository evidence alone.
Implementing it without that evidence would be exactly the unauthorized
architectural-surface expansion this whole investigation exists to prevent.

Root authority source: `grounding_goal` (`AttemptContext.grounding_goal`) - the
raw, unmediated top-level user request, never Planner-authored text
(`Subtask.description`/`requires`/`provides`), never `GlobalInvariant` prose.
A DIRECT `ContractEvolutionAuthorization` is created only when the SAME clause
of `grounding_goal` independently grounds all three of: the owner's real,
resolved identity (a real planned file, never invented); a specific symbol; and
a change category. Any one missing grounds nothing - this is the fail-closed
default, not an edge case (see PRV08_CustomerSummary_change_not_authorized in
tests/test_workflow.py: "update all affected consumers" names no owner and no
symbol, so it grounds nothing, exactly as intended).

No MA8/ObligationLedger integration: DIRECT authorizations are a pure,
deterministic, idempotent function of `grounding_goal` and the approved plan's
own `planned_files` - recomputing them fresh on every call (exactly like the
reverted `compute_authorized_contract_evolutions()` was computed) costs
nothing and needs no ledger/ownership/lifecycle machinery, since there is no
parent chain, no plan-repair-vs-authorization interaction, and no resume state
to reconcile for a DIRECT-only record. That machinery is designed (see the
architecture doc) for when DERIVED authorization is actually implemented, not
before.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from kriya.workflow.plan_schema import EngineeringPlan


class AuthorizationProvenance(str, Enum):
    DIRECT = "direct"
    # DERIVED is designed but not produced by this module - see module
    # docstring. Reserved here only so a future implementation does not need
    # to change this enum's own shape.
    DERIVED = "derived"


class AuthorizationAuthority(str, Enum):
    AUTHORITATIVE = "authoritative"
    # Not produced by this module (DIRECT records are always AUTHORITATIVE).
    # Reserved for a future DERIVED implementation.
    DETERMINISTIC_DERIVED = "deterministic_derived"


class ChangeCategory(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    REMOVE = "remove"


@dataclass(frozen=True)
class ContractEvolutionAuthorization:
    """See docs/architecture/CORR016_AUTHORIZED_CONTRACT_EVOLUTION_DESIGN.md
    §4 for the full field-by-field justification. This implementation only
    ever constructs provenance=DIRECT, authority=AUTHORITATIVE,
    parent_authorization_id=None records - the other fields exist so a future
    DERIVED implementation does not require a breaking schema change."""

    authorization_id: str
    source_requirement_id: str
    provenance: AuthorizationProvenance
    authority: AuthorizationAuthority
    source_contract_owner: Optional[str]
    source_symbol: Optional[str]
    source_change_category: Optional[ChangeCategory]
    affected_owner: str
    affected_symbol: str
    allowed_change_category: ChangeCategory
    derivation_evidence: Dict[str, Any]
    parent_authorization_id: Optional[str]
    legal_scope: Dict[str, Optional[str]]
    plan_revision: str


# Fixed, narrow, deterministic vocabulary - deliberately not exhaustive NLP.
# A false negative here costs one more explicit-goal rewrite; a false
# positive would grant unauthorized mutation permission. Matches this
# codebase's own established convention (_goal_explicitly_requests_api_change,
# _goal_explicitly_requests_new_entrypoint) of narrow, high-precision-only
# regex over free text.
_CATEGORY_VERBS: Dict[str, ChangeCategory] = {
    "add": ChangeCategory.ADD, "extend": ChangeCategory.ADD,
    "introduce": ChangeCategory.ADD, "require": ChangeCategory.ADD,
    "change": ChangeCategory.MODIFY, "modify": ChangeCategory.MODIFY,
    "rename": ChangeCategory.MODIFY,
    "remove": ChangeCategory.REMOVE, "delete": ChangeCategory.REMOVE,
}
_CATEGORY_VERB_RE = re.compile(
    r"\b(" + "|".join(_CATEGORY_VERBS) + r")\b", re.IGNORECASE,
)
# "a new required field named `region`" / "the `legacyGreet` method" /
# "a method called run" - captures (kind, symbol). kind distinguishes a
# FIELD/PROPERTY/PARAMETER (whose real _normalized_public_signatures() api_name
# is the OWNER's own record/class name, since a record-component-shape change
# is keyed by the record's own type identity, never the individual field name)
# from a METHOD/SYMBOL (whose api_name is the named identifier itself).
_SYMBOL_NAMED_RE = re.compile(
    r"\b(field|method|property|parameter|symbol)\s+(?:named|called)\s+"
    r"[`\"']?([A-Za-z_][A-Za-z0-9_]*)[`\"']?",
    re.IGNORECASE,
)
_SYMBOL_BACKTICK_NEAR_RE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*)`\s+(field|method|property)\b"
    r"|\b(field|method|property)\s+`([A-Za-z_][A-Za-z0-9_]*)`",
    re.IGNORECASE,
)
_FIELD_KINDS = {"field", "property", "parameter"}


def _split_clauses(text: str) -> List[str]:
    """Sentence-level split - deliberately coarse (a clause is roughly a
    sentence), matching the fact that a real authoritative goal states one
    contract delta per sentence (see goal.md's own bullet-list shape)."""
    return [c.strip() for c in re.split(r"(?<=[.!?])\s+|\n+", text or "") if c.strip()]


def _find_symbol(clause: str) -> Optional[tuple]:
    """Returns (symbol_name, is_field) or None."""
    match = _SYMBOL_NAMED_RE.search(clause)
    if match:
        return match.group(2), match.group(1).lower() in _FIELD_KINDS
    match = _SYMBOL_BACKTICK_NEAR_RE.search(clause)
    if match:
        if match.group(1) is not None:
            return match.group(1), match.group(2).lower() in _FIELD_KINDS
        return match.group(4), match.group(3).lower() in _FIELD_KINDS
    return None


def _find_category(clause: str) -> Optional[ChangeCategory]:
    match = _CATEGORY_VERB_RE.search(clause)
    if not match:
        return None
    return _CATEGORY_VERBS[match.group(1).lower()]


def _find_named_owner(clause: str, candidate_owners: Dict[str, str]) -> Optional[str]:
    """candidate_owners: {real, resolved owner identity name (e.g.
    "CustomerRecord", from a real planned_files path) -> that file's path}.
    Never invents an owner name - only ever matches names the approved plan
    itself already resolved to a real file."""
    for name, path in candidate_owners.items():
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", clause):
            return path
    return None


def derive_direct_contract_authorizations(
    grounding_goal: str,
    structured_plan: Optional["EngineeringPlan"],
) -> List[ContractEvolutionAuthorization]:
    """Every DIRECT ContractEvolutionAuthorization the raw, authoritative
    grounding_goal text alone supports. [] for every plain Legacy run
    (structured_plan is None, identical to today's unauthorized behavior) or
    any goal that never grounds owner + symbol + category together in the
    same clause - fail closed by construction, not by special-casing."""
    if not grounding_goal or structured_plan is None:
        return []
    candidate_owners: Dict[str, str] = {}
    for subtask in getattr(structured_plan, "subtasks", None) or []:
        for planned_file in getattr(subtask, "planned_files", None) or []:
            path = planned_file.path
            basename = path.rsplit("/", 1)[-1]
            name = basename.rsplit(".", 1)[0] if "." in basename else basename
            if name:
                candidate_owners[name] = path
    if not candidate_owners:
        return []

    plan_revision = getattr(structured_plan, "plan_id", "") or ""
    authorizations: List[ContractEvolutionAuthorization] = []
    for clause in _split_clauses(grounding_goal):
        owner_path = _find_named_owner(clause, candidate_owners)
        if owner_path is None:
            continue
        symbol_match = _find_symbol(clause)
        if symbol_match is None:
            continue
        symbol_name, is_field = symbol_match
        category = _find_category(clause)
        if category is None:
            continue
        # A record/class own component-shape change (a FIELD/PROPERTY named
        # in the goal) is keyed, in _normalized_public_signatures()'s own
        # identity space, by the OWNER's own type name (record_name), never
        # by the individual field name - see file_resolution.py's own
        # `signatures[f"record {record_name}(...)"] = record_name`. A named
        # METHOD/SYMBOL matches its own name directly.
        owner_basename = owner_path.rsplit("/", 1)[-1]
        owner_type_name = (
            owner_basename.rsplit(".", 1)[0] if "." in owner_basename else owner_basename
        )
        affected_symbol = owner_type_name if is_field else symbol_name

        owning_subtask_id = None
        for subtask in structured_plan.subtasks:
            if any(pf.path == owner_path for pf in (subtask.planned_files or [])):
                owning_subtask_id = subtask.id
                break

        requirement_id = (
            f"grounding_goal::{owner_path}::{affected_symbol}::{category.value}"
        )
        authorizations.append(ContractEvolutionAuthorization(
            authorization_id=requirement_id,
            source_requirement_id=requirement_id,
            provenance=AuthorizationProvenance.DIRECT,
            authority=AuthorizationAuthority.AUTHORITATIVE,
            source_contract_owner=owner_path,
            source_symbol=symbol_name,
            source_change_category=category,
            affected_owner=owner_path,
            affected_symbol=affected_symbol,
            allowed_change_category=category,
            derivation_evidence={"grounding_clause": clause, "named_symbol": symbol_name},
            parent_authorization_id=None,
            legal_scope={"owner": owner_path, "subtask_id": owning_subtask_id},
            plan_revision=plan_revision,
        ))
    return authorizations
