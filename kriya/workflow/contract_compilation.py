"""VERIFICATION-CONTRACT-003: the sealed verification contract of a run.

Before the first model call, every statement of the authoritative
requirement set (PRD-020 derivation or the GR-R1A operator contract) is
compiled into one immutable record that says what kind of claim it makes
(kriya/workflow/requirement_scopes.py), which deterministic closer may close
each claim, which authority supplies that closer's evidence, and - when none
is bound - why and what authority would be acceptable. The contract composes
what already exists: the requirement set, FS-1C1 claims and B2-COV strength,
the B2 acceptance artifact, the B3 approval, the external authority bundle
(kriya/workflow/authority_bundle.py), the goal's compiled examples
(kriya/workflow/example_oracle.py), the repository's own facts (test files,
tracked paths, the resolved migration, a documentation referent). It is one
owner of the admission decision for both execution paths: the legacy
``requirements.deterministic_closers`` / ``admission_gap`` read it.

Hard rules kept: LLM output authorizes nothing (no model is consulted here
or by any closer); MODEL_CLAIMED is advisory; finite examples never close a
GENERAL statement (B2-COV); a residual mandatory claim blocks; a compound
statement closes only when every claim it makes is closed; NON_CLAIM is
structural, visible and never a proposition (owner decision D1).

Admission (D3): a statement whose semantics are undecidable (mutation-scope
roles that cannot be determined, "other" with no referent) makes the goal
GOAL_INSUFFICIENT_FOR_VERIFICATION; otherwise a mandatory claim without a
bound deterministic authority makes it VERIFICATION_AUTHORITY_REQUIRED; a
goal whose every mandatory claim has a closer is admitted and the contract
sealed: stored content-addressed under ``<state>/verification-contracts/``,
its digest joining both resume identities, so a changed goal, requirement
set, authority or closer map never reuses a candidate.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.workflow.requirement_scopes import (
    MIGRATION,
    NON_CLAIM,
    PROCEDURE,
    SCOPE_RECOGNIZER_VERSION,
    SUITE_PRESERVATION_SCOPE,
    StatementScope,
    statement_scope,
)
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_GENERAL,
    CLOSER_ACCEPTANCE,
    CLOSER_MIGRATION_GATE,
    CLOSER_MUTATION_SCOPE,
    CLOSER_NAMED_TESTS,
    DISPOSITION_EVIDENCE_ID,
    DOCUMENTATION_CLAIM,
    FILE_IMMUTABILITY,
    FILE_IMMUTABILITY_CLAIM,
    MUTATION_SCOPE,
    ORIGIN_SENTENCE,
    REGRESSION_PRESERVATION,
    REQUIREMENT_DERIVATION_VERSION,
    SUITE_PRESERVATION,
    TEST_ADDITION_CLAIM,
    TEST_IMMUTABILITY,
    TEST_IMMUTABILITY_CLAIM,
    AdmissionRefusal,
    GoalAdmissionError,
    RequirementSet,
    VerificationAuthorityRequired,
    disposition_record,
    mutation_path_roles,
    non_claim_record,
    record_non_claim,
    record_requirement_disposition,
    requirement_claim_id,
)

CONTRACT_FORMAT = "kriya.verification_contract/1"
# BACKEND-READINESS-004: /2 - one claim may carry several bindings (the repository closer beside the sealed
# operator oracle); an authority's coverage is declared per (requirement, claim). Every contract sealed by /1 is STALE.
# 3 (BACKEND-FINAL-CLOSURE-005, OD-3): the FILE_IMMUTABILITY claim - a pure freeze of named tracked files is
# closable from the mutation record instead of a BEHAVIOR claim needing authority; every /2 contract is stale by design.
CONTRACT_COMPILER_VERSION = 3
CONTRACT_STORE_DIR = "verification-contracts"

# Entry statuses.
STATUS_CLOSABLE = "CLOSABLE"
STATUS_AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"
STATUS_NOT_A_CLAIM = "NOT_A_CLAIM"
STATUS_AMBIGUOUS = "AMBIGUOUS"
# BACKEND-READINESS-004 (D3): the operator sealed a disposition for the whole statement (or every claim of it).
STATUS_DISPOSITIONED = "DISPOSITIONED"

# Closer ids beyond the existing ones (requirements.CLOSER_* / SUITE_PRESERVATION / TEST_IMMUTABILITY).
CLOSER_ACCEPTANCE_APPROVAL = "acceptance_approval"
CLOSER_DERIVED_EXAMPLES = "derived_examples"
CLOSER_API_PRESERVATION = "api_preservation"
CLOSER_DOCUMENTATION_NOT_APPLICABLE = "documentation_not_applicable"
CLOSER_DOCUMENTATION_LIST_ENTRIES = "documentation_list_entries"  # BACKEND-READINESS-004 (owner decision 2)
CLOSER_TEST_ADDITION = "test_addition"
CLOSER_EXTERNAL_ACCEPTANCE = "external_acceptance_command"
CLOSER_NOT_A_CLAIM = "not_a_claim"
CLOSER_OPERATOR_DISPOSITION = "operator_disposition"
DISPOSITIONED_STATEMENT = "STATEMENT"  # the binding label of a whole-statement disposition

# Who created the evidence a closer judges.
AUTHORITY_GOAL = "goal"  # the user's own words (examples, named tests, the statements themselves)
AUTHORITY_REPOSITORY = "repository"  # deterministic repository facts (tests, tree, mutation record)
AUTHORITY_ACCEPTANCE_FILE = "acceptance_file"  # B2 operator acceptance file
AUTHORITY_ACCEPTANCE_APPROVAL = "acceptance_approval"  # B3 human-bound approval
AUTHORITY_GOAL_EXAMPLES = "goal_examples"  # the goal's example lines compiled deterministically
AUTHORITY_EXTERNAL_COMMAND = "external_acceptance_command"  # a sealed operator oracle bundle
AUTHORITY_CONTRACT = "contract"  # the compiler's own structural decision
AUTHORITY_OPERATOR_DISPOSITION = "operator_disposition"  # a sealed operator disposition (kriya/workflow/requirement_disposition.py)

VISIBILITY_HIDDEN = "hidden"  # never in a prompt, never in the workspace
VISIBILITY_GOAL_TEXT = "goal_text"  # part of the goal the Developer reads anyway

# Project languages with a deterministic public-API predicate (kriya/workflow/api_preservation.py):
# Python (AST) and, since BACKEND-READINESS-004, Java (the code-intelligence structural model).
API_PREDICATE_LANGUAGES = frozenset({"python", "java"})

# What each closer closes, who authored its evidence, and what PASS / FAIL / UNKNOWN are. Closed table: a closer id
# outside it is a programming error (``_closer_contract``).
CLOSER_CONTRACTS: Dict[str, Dict[str, str]] = {
    CLOSER_NAMED_TESTS: {
        "claim": REGRESSION_PRESERVATION, "authority": AUTHORITY_REPOSITORY,
        "pass": "every named pre-existing test executed and passed on the candidate, trust surface unchanged (FS-1C0)",
        "fail": "a named test failed (counter-evidence)", "unknown": "incomplete evidence, changed trust surface, "
        "a test the candidate wrote or edited, an unreadable base"},
    SUITE_PRESERVATION: {
        "claim": REGRESSION_PRESERVATION, "authority": AUTHORITY_REPOSITORY,
        "pass": "the candidate's complete full suite ran green with the run's mutation record intact",
        "fail": "the suite is green but an existing test changed or vanished under an 'unchanged' statement",
        "unknown": "incomplete structured evidence, zero tests, a changed test trust surface, no mutation record"},
    TEST_IMMUTABILITY: {
        "claim": TEST_IMMUTABILITY_CLAIM, "authority": AUTHORITY_REPOSITORY,
        "pass": "no test file that existed before the run changed or vanished (mutation record)",
        "fail": "an existing test file changed or vanished", "unknown": "mutation record unavailable"},
    FILE_IMMUTABILITY: {  # OD-3 (BACKEND-FINAL-CLOSURE-005)
        "claim": FILE_IMMUTABILITY_CLAIM, "authority": AUTHORITY_REPOSITORY,
        "pass": "every file the goal froze is byte-identical to the base and present at its path (mutation record)",
        "fail": "a frozen file changed, was deleted or was renamed", "unknown": "mutation record unavailable"},
    CLOSER_MUTATION_SCOPE: {
        "claim": MUTATION_SCOPE, "authority": AUTHORITY_REPOSITORY,
        "pass": "every changed path is one the goal names as a change target, nothing foreign",
        "fail": "a path outside the authorized set changed", "unknown": "mutation record unavailable"},
    CLOSER_MIGRATION_GATE: {
        "claim": MIGRATION, "authority": AUTHORITY_REPOSITORY,
        "pass": "the deterministic migration gate judged the resolved migration complete on the candidate",
        "fail": "the migration gate found the source dependency still present or the target absent",
        "unknown": "the migration could not be resolved or the gate did not run"},
    CLOSER_ACCEPTANCE: {
        "claim": BEHAVIOR, "authority": AUTHORITY_ACCEPTANCE_FILE,
        "pass": "every acceptance case bound to the requirement executed and passed under Kriya's runner (B2)",
        "fail": "a case contradicted the candidate (assertion after candidate code ran)",
        "unknown": "no report, incomplete report, environment error, a foreign plugin, an unsupported layout"},
    CLOSER_ACCEPTANCE_APPROVAL: {
        "claim": BEHAVIOR, "authority": AUTHORITY_ACCEPTANCE_APPROVAL,
        "pass": "the approved suite executed and passed in full; the operator accepted it as sufficient for this "
                "general statement (HUMAN_ACCEPTED, never 'verified')",
        "fail": "a case contradicted the candidate", "unknown": "any B2 INDETERMINATE outcome, or an approval that "
        "does not bind this goal, requirement text, artifact, cases, runner contract and base revision"},
    CLOSER_DERIVED_EXAMPLES: {
        "claim": BEHAVIOR, "authority": AUTHORITY_GOAL_EXAMPLES,
        "pass": "the goal's own example lines, compiled deterministically into an acceptance module, executed and "
                "passed (EXACT statements only, B2-COV)",
        "fail": "an example contradicted the candidate", "unknown": "any B2 INDETERMINATE outcome"},
    CLOSER_API_PRESERVATION: {
        "claim": API_PRESERVATION, "authority": AUTHORITY_REPOSITORY,
        "pass": "every public module-level and class-level signature of the base is present and unchanged in the "
                "candidate (Python AST; Java public/protected surface from the structural parser)",
        "fail": "a public symbol was removed, narrowed or its signature, modifiers or hierarchy changed",
        "unknown": "base unreadable, a file that does not parse, a project language without a predicate"},
    CLOSER_DOCUMENTATION_NOT_APPLICABLE: {
        "claim": DOCUMENTATION_CLAIM, "authority": AUTHORITY_REPOSITORY,
        "pass": "the conditional documentation referent does not exist in the repository (nothing to document)",
        "fail": "never (a present referent is a content claim, not a failure)", "unknown": "never"},
    CLOSER_DOCUMENTATION_LIST_ENTRIES: {
        "claim": DOCUMENTATION_CLAIM, "authority": AUTHORITY_REPOSITORY,
        "pass": "the referent's named list section (sealed at compile time from the baseline) still exists in the "
                "candidate and carries an entry for every subject the goal adds (identifiers derived from the goal's "
                "own addition statements) - a whole-word match inside that section only, never a repository-wide search",
        "fail": "never (a missing entry or a removed list leaves the claim open)",
        "unknown": "the referent unreadable in the candidate"},
    CLOSER_TEST_ADDITION: {
        "claim": TEST_ADDITION_CLAIM, "authority": AUTHORITY_REPOSITORY,
        "pass": "the candidate adds a runnable test file, or its complete structured suite report carries a test "
                "identity the base's report does not",
        "fail": "never (an absent test leaves the claim open)", "unknown": "no complete report on either side"},
    CLOSER_EXTERNAL_ACCEPTANCE: {
        "claim": "declared per coverage entry", "authority": AUTHORITY_EXTERNAL_COMMAND,
        "pass": "the sealed operator command exited 0 under Kriya's containment, assets unchanged (operator "
                "sufficiency: HUMAN_ACCEPTED, never 'verified')",
        "fail": "the command exited 1 (the oracle contradicted the candidate)",
        "unknown": "any other exit, a timeout, a changed asset, an unavailable toolchain or containment"},
    CLOSER_NOT_A_CLAIM: {
        "claim": NON_CLAIM, "authority": AUTHORITY_CONTRACT,
        "pass": "the statement is a label or a bare code block with no expected value (structural)",
        "fail": "never", "unknown": "never"},
    CLOSER_OPERATOR_DISPOSITION: {
        "claim": "the dispositioned statement or claim", "authority": AUTHORITY_OPERATOR_DISPOSITION,
        "pass": "the operator sealed a disposition (rejected false premise, historical, informational, out of scope) for "
                "this exact text before any model call: removed from the obligation set, reported, never satisfied",
        "fail": "never", "unknown": "never"},
}

# Acceptable authority types for a residual claim, by claim and strength.
_ACCEPTABLE: Dict[Tuple[str, Optional[str]], Tuple[str, ...]] = {
    (BEHAVIOR, BEHAVIOR_EXACT): (AUTHORITY_ACCEPTANCE_FILE, AUTHORITY_GOAL_EXAMPLES, AUTHORITY_EXTERNAL_COMMAND),
    (BEHAVIOR, BEHAVIOR_GENERAL): (AUTHORITY_EXTERNAL_COMMAND + " (reference or property oracle)",
                                   AUTHORITY_ACCEPTANCE_APPROVAL + " (B3: an operator-approved acceptance suite)",
                                   "authoritative acceptance test (an acceptance file plus approval)"),
    (API_PRESERVATION, None): (AUTHORITY_EXTERNAL_COMMAND + " (a signature baseline check)",),
    (DOCUMENTATION_CLAIM, None): (AUTHORITY_EXTERNAL_COMMAND, AUTHORITY_ACCEPTANCE_FILE),
    (REGRESSION_PRESERVATION, None): ("a statement naming the existing tests, or a whole-suite preservation statement",),
}

VERIFICATION_CONTRACT_EVENT_KINDS = frozenset({
    "verification_contract.authority_requested",  # BACKEND-READINESS-004 (D2): typed requests sealed on refusal
    "verification_contract.dispositioned",  # BACKEND-READINESS-004 (D3): operator dispositions bound and sealed
    "verification_contract.compiled",
    "verification_contract.sealed",
    "verification_contract.refused",
    "verification_contract.baseline",
    "verification_contract.no_mutation_required",
    "verification_contract.invalidated",
    "requirement.authority_required",
})

_README = re.compile(r"^readme(\.[\w.]+)?$", re.IGNORECASE)
_CHANGELOG = re.compile(r"^(changelog|changes|history|news)(\.[\w.]+)?$", re.IGNORECASE)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
_RST_UNDERLINE = re.compile(r"^\s*([=\-~^\"'`#*+_:.])\1{2,}\s*$")


@dataclass(frozen=True)
class ExternalAuthority:
    """An authority bound to the run beside the goal: what it covers, by digest."""

    kind: str
    digest: str
    # requirement id -> claim -> {"accepted_strength": ..., "why": ...}: one entry per (requirement, claim) the
    # authority proves (BACKEND-READINESS-004: a compound statement may be covered claim by claim).
    coverage: Mapping[str, Mapping[str, Mapping[str, Any]]]
    visibility: str = VISIBILITY_HIDDEN
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def covers(self, requirement_id: str, claim: str, strength: Optional[str]) -> bool:
        entries = self.coverage.get(requirement_id) or {}
        if claim not in entries:
            return False
        accepted = (entries[claim] or {}).get("accepted_strength")
        if claim != BEHAVIOR:
            return True
        return accepted == BEHAVIOR_GENERAL or (accepted == BEHAVIOR_EXACT and strength == BEHAVIOR_EXACT)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "digest": self.digest, "visibility": self.visibility,
                "coverage": {rid: {claim: dict(entry) for claim, entry in sorted(entries.items())}
                             for rid, entries in sorted(self.coverage.items())},
                "provenance": dict(self.provenance)}

    def identity(self) -> Dict[str, Any]:
        """The authority as the contract digest sees it: review VC3-R10 - where
        the state directory lives (``stored_path``/``stored_dir``) is not an
        input of the contract, the content digest is."""
        provenance = {k: v for k, v in self.provenance.items() if k not in ("stored_path", "stored_dir")}
        return {**self.to_dict(), "provenance": provenance}


@dataclass(frozen=True)
class ClaimBinding:
    claim: str
    closer: str
    authority_kind: str
    authority_digest: Optional[str] = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        contract = _closer_contract(self.closer)
        return {"claim": self.claim, "closer": self.closer, "authority": self.authority_kind,
                "authority_digest": self.authority_digest, "evidence_pass": contract["pass"],
                "evidence_fail": contract["fail"], "evidence_unknown": contract["unknown"], **dict(self.detail)}


@dataclass(frozen=True)
class ResidualClaim:
    claim: str
    strength: Optional[str]
    why: str
    acceptable_authorities: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"claim": self.claim, "strength": self.strength, "why": self.why,
                "acceptable_authorities": list(self.acceptable_authorities)}


@dataclass(frozen=True)
class ContractEntry:
    requirement_id: str
    text: str
    kind: str
    source: str
    scope: StatementScope
    status: str
    bindings: Tuple[ClaimBinding, ...] = ()
    residual: Tuple[ResidualClaim, ...] = ()
    ambiguity: Optional[str] = None
    disposition: Optional[Mapping[str, Any]] = None  # D3: the sealed disposition of the whole statement
    dispositioned_claims: Tuple[str, ...] = ()  # D3: claims the operator removed from the obligation

    @property
    def closers(self) -> List[str]:
        seen: List[str] = []
        for binding in self.bindings:
            if binding.closer not in seen:
                seen.append(binding.closer)
        return seen

    @property
    def required_claims(self) -> Tuple[str, ...]:
        return tuple(claim for claim in self.scope.claims if claim not in self.dispositioned_claims)

    def to_dict(self) -> Dict[str, Any]:
        return {"requirement_id": self.requirement_id, "text": self.text, "kind": self.kind, "source": self.source,
                "origin": self.scope.origin, "scopes": list(self.scope.scopes), "claims": list(self.scope.claims),
                "strength": self.scope.strength, "strength_reasons": list(self.scope.strength_reasons),
                "status": self.status, "closers": self.closers, "bindings": [b.to_dict() for b in self.bindings],
                "residual": [r.to_dict() for r in self.residual], "ambiguity": self.ambiguity,
                "non_claim": ({"kind": self.scope.non_claim_kind, "reason": self.scope.non_claim_reason}
                              if self.scope.is_non_claim else None),
                "named_tests": list(self.scope.named_tests),
                "documentation": dict(self.scope.documentation) if self.scope.documentation else None,
                "disposition": dict(self.disposition) if self.disposition else None,
                "dispositioned_claims": list(self.dispositioned_claims)}


@dataclass(frozen=True)
class VerificationContract:
    goal_digest: str
    requirement_set_digest: str
    requirement_contract_digest: Optional[str]
    base_revision: Optional[str]
    project_language: Optional[str]
    entries: Tuple[ContractEntry, ...]
    authorities: Tuple[ExternalAuthority, ...]
    derivation_version: int = REQUIREMENT_DERIVATION_VERSION
    recognizer_version: int = SCOPE_RECOGNIZER_VERSION
    compiler_version: int = CONTRACT_COMPILER_VERSION
    dispositions: Optional[Mapping[str, Any]] = None  # D3: the bound operator dispositions' identity

    # ---- identity
    def identity_payload(self) -> Dict[str, Any]:
        return {"format": CONTRACT_FORMAT, "compiler_version": self.compiler_version,
                "dispositions": dict(self.dispositions) if self.dispositions else None,
                "recognizer_version": self.recognizer_version, "derivation_version": self.derivation_version,
                "goal_digest": self.goal_digest, "requirement_set_digest": self.requirement_set_digest,
                "requirement_contract_digest": self.requirement_contract_digest, "base_revision": self.base_revision,
                "project_language": self.project_language,
                "entries": [entry.to_dict() for entry in self.entries],
                "authorities": [authority.identity() for authority in self.authorities]}

    @property
    def digest(self) -> str:
        blob = json.dumps(self.identity_payload(), sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    # ---- views
    def entry(self, requirement_id: str) -> Optional[ContractEntry]:
        return next((e for e in self.entries if e.requirement_id == requirement_id), None)

    def closers_by_requirement(self) -> Dict[str, List[str]]:
        return {entry.requirement_id: entry.closers for entry in self.entries}

    def required_claims_by_requirement(self) -> Dict[str, Tuple[str, ...]]:
        return {entry.requirement_id: entry.required_claims for entry in self.entries}

    def residual_requirements(self) -> List[Dict[str, Any]]:
        """Legacy shape (id, text, why): every requirement that cannot be admitted."""
        rows: List[Dict[str, Any]] = []
        for entry in self.entries:
            if entry.status == STATUS_AMBIGUOUS:
                rows.append({"id": entry.requirement_id, "text": entry.text, "why": entry.ambiguity or "undecidable"})
            elif entry.status == STATUS_AUTHORITY_REQUIRED:
                rows.append({"id": entry.requirement_id, "text": entry.text,
                             "why": "; ".join(f"{r.claim}{' ' + r.strength if r.strength else ''}: {r.why}"
                                              for r in entry.residual)})
        return rows

    def non_claim_ids(self) -> List[str]:
        return [e.requirement_id for e in self.entries if e.status == STATUS_NOT_A_CLAIM]

    def mandatory_entries(self) -> List[ContractEntry]:
        return [e for e in self.entries if e.status not in (STATUS_NOT_A_CLAIM, STATUS_DISPOSITIONED)]

    def dispositioned_entries(self) -> List[ContractEntry]:
        return [e for e in self.entries if e.disposition is not None or e.dispositioned_claims]

    def visibility(self) -> Dict[str, str]:
        return {authority.kind + ":" + authority.digest[:12]: authority.visibility for authority in self.authorities}

    def binding_closers(self, closer: str) -> List[Tuple[str, ClaimBinding]]:
        return [(e.requirement_id, b) for e in self.entries for b in e.bindings if b.closer == closer]

    # ---- admission
    def refusal(self) -> Optional[AdmissionRefusal]:
        """GoalAdmissionError when any statement is undecidable (the goal must
        be clarified first; every non-admissible statement is listed),
        VerificationAuthorityRequired when every statement is determinate
        but a mandatory claim lacks a bound authority, else None."""
        closers = self.closers_by_requirement()
        ambiguous = [e for e in self.entries if e.status == STATUS_AMBIGUOUS]
        if ambiguous or not self.mandatory_entries():
            residual = self.residual_requirements()
            if not self.mandatory_entries():
                residual = [{"id": e.requirement_id, "text": e.text,
                             "why": "every statement of the goal is a label, a bare code block or an operator-"
                                    "dispositioned statement: nothing to verify"}
                            for e in self.entries] or [{"id": "-", "text": "", "why": "the goal has no statement"}]
            return GoalAdmissionError(residual, closers, report=self.report())
        residual_entries = [e for e in self.entries if e.status == STATUS_AUTHORITY_REQUIRED]
        if residual_entries:
            rows = [{"id": e.requirement_id, "text": e.text, "claim": r.claim, "strength": r.strength,
                     "scopes": list(e.scope.scopes), "why": r.why,
                     "acceptable_authorities": list(r.acceptable_authorities)}
                    for e in residual_entries for r in e.residual]
            # BACKEND-READINESS-004 (D2): one typed request per residual claim (local import: that module
            # imports this one's vocabulary).
            from kriya.workflow.authority_request import authority_requests

            return VerificationAuthorityRequired(rows, closers, report=self.report(), authority_requests=authority_requests(self))
        return None

    # ---- reporting
    def report(self) -> Dict[str, Any]:
        counts = {"requirements": len(self.entries), "non_claim": 0, "closable": 0, "authority_required": 0,
                  "ambiguous": 0, "dispositioned": 0, "residual_claims": 0}
        rows = []
        for entry in self.entries:
            key = {STATUS_NOT_A_CLAIM: "non_claim", STATUS_CLOSABLE: "closable", STATUS_DISPOSITIONED: "dispositioned",
                   STATUS_AUTHORITY_REQUIRED: "authority_required", STATUS_AMBIGUOUS: "ambiguous"}[entry.status]
            counts[key] += 1
            counts["residual_claims"] += len(entry.residual)
            rows.append({"id": entry.requirement_id, "text": entry.text, "origin": entry.scope.origin,
                         "scopes": list(entry.scope.scopes), "claims": list(entry.scope.claims),
                         "strength": entry.scope.strength, "closers": entry.closers,
                         "authorities": sorted({b.authority_kind for b in entry.bindings}),
                         "status": entry.status, "residual": [r.to_dict() for r in entry.residual],
                         "ambiguity": entry.ambiguity, "disposition": dict(entry.disposition) if entry.disposition else None,
                         "dispositioned_claims": list(entry.dispositioned_claims)})
        return {"format": CONTRACT_FORMAT, "contract_digest": self.digest, "goal_digest": self.goal_digest,
                "requirement_set_digest": self.requirement_set_digest, "base_revision": self.base_revision,
                "project_language": self.project_language, "totals": counts, "requirements": rows,
                "authorities": [a.to_dict() for a in self.authorities], "visibility": self.visibility(),
                "dispositions": dict(self.dispositions) if self.dispositions else None,
                "admission": ("GOAL_INSUFFICIENT_FOR_VERIFICATION" if counts["ambiguous"] or not self.mandatory_entries()
                              else "VERIFICATION_AUTHORITY_REQUIRED" if counts["authority_required"] else "ADMITTED")}

    def to_dict(self) -> Dict[str, Any]:
        return {**self.identity_payload(), "digest": self.digest, "report": self.report()}


def _closer_contract(closer: str) -> Dict[str, str]:
    try:
        return CLOSER_CONTRACTS[closer]
    except KeyError as error:  # a closer outside the closed table is a programming error
        raise KeyError(f"unknown closer {closer!r}; register it in CLOSER_CONTRACTS") from error


def _covering(authorities: Sequence[ExternalAuthority], requirement_id: str, claim: str,
              strength: Optional[str]) -> Optional[ExternalAuthority]:
    """The first sealed operator authority declaring coverage of this exact
    (requirement, claim) at a sufficient strength; the goal's own compiled
    examples are a separate closer, never an operator authority."""
    return next((a for a in authorities if a.kind != AUTHORITY_GOAL_EXAMPLES
                 and a.covers(requirement_id, claim, strength)), None)


def _external_binding(claim: str, external: ExternalAuthority, requirement_id: str) -> ClaimBinding:
    return ClaimBinding(claim, CLOSER_EXTERNAL_ACCEPTANCE, external.kind, external.digest,
                        detail=dict(external.coverage[requirement_id][claim]))


def _acceptable(claim: str, strength: Optional[str]) -> Tuple[str, ...]:
    return _ACCEPTABLE.get((claim, strength)) or _ACCEPTABLE.get((claim, None)) or (AUTHORITY_EXTERNAL_COMMAND,)


# ------------------------------------------------------------ repository facts

def _documentation_referent_paths(referent: str, tracked_paths: Sequence[str]) -> List[str]:
    """The tracked paths a documentation clause's referent names: README.* /
    CHANGELOG.* by basename (case-insensitive), an explicit file by path or
    basename, "the docs"/"the documentation" by a docs directory."""
    lowered = referent.lower()
    names = [(path, os.path.basename(path)) for path in tracked_paths]
    if lowered.startswith("readme"):
        return sorted(path for path, base in names if _README.match(base))
    if lowered.startswith(("changelog", "changes")):
        return sorted(path for path, base in names if _CHANGELOG.match(base))
    if "doc" in lowered and "." not in lowered:
        return sorted(path for path in tracked_paths if path.split("/")[0].lower() in ("docs", "doc"))
    return sorted(path for path, base in names if path.lower() == lowered or base.lower() == lowered)


def _headings(data: bytes) -> List[str]:
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    headings: List[str] = []
    for index, line in enumerate(lines):
        match = _MD_HEADING.match(line)
        if match:
            headings.append(match.group("title"))
        elif index + 1 < len(lines) and _RST_UNDERLINE.match(lines[index + 1]) and line.strip() \
                and len(lines[index + 1].strip()) >= len(line.strip()) - 1:
            headings.append(line.strip())
    return headings


def _documentation_list_present(
    clause: Mapping[str, Any], tracked_paths: Sequence[str], reader: Optional[Callable[[str], Optional[bytes]]],
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """(present, evidence) for a documentation referent: None when it cannot
    be decided (no reader), False when the referent - or the named list's
    heading inside it - does not exist, True when it does."""
    paths = _documentation_referent_paths(str(clause.get("referent") or ""), tracked_paths)
    evidence: Dict[str, Any] = {"referent": clause.get("referent"), "paths": paths, "list_noun": clause.get("list_noun")}
    if not paths:
        return False, evidence
    noun = clause.get("list_noun")
    if not noun:
        return True, evidence
    if reader is None:
        return None, {**evidence, "reason": "no tracked-file reader supplied"}
    matched: List[str] = []
    for path in paths:
        data = reader(path)
        if data is None:
            return None, {**evidence, "reason": f"{path} unreadable"}
        matched += [f"{path}: {heading}" for heading in _headings(data) if str(noun).lower() in heading.lower()]
    evidence["matching_headings"] = matched
    return bool(matched), evidence


# An entry of a list: a list item, a table row, or a line opening with a code span or bold name; a plain prose line
# counts only when it OPENS with the subject (a definition entry such as ``lower(string) - ...``).
_LIST_ENTRY_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\|)\s*\S|^\s*(?:`|\*\*)")


def documentation_sections(data: bytes, noun: str) -> Dict[str, List[str]]:
    """The lines of every section of a Markdown/RST document whose heading
    names ``noun`` ("Function list", "Functions"), keyed by heading: from
    the heading to the next heading of any level."""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    headings = []  # (line index, title, is RST underline heading)
    for index, line in enumerate(lines):
        match = _MD_HEADING.match(line)
        if match:
            headings.append((index, match.group("title")))
        elif index + 1 < len(lines) and _RST_UNDERLINE.match(lines[index + 1]) and line.strip() \
                and len(lines[index + 1].strip()) >= len(line.strip()) - 1:
            headings.append((index, line.strip()))
    sections: Dict[str, List[str]] = {}
    family = noun.lower()
    for position, (index, title) in enumerate(headings):
        if family not in title.lower():
            continue
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        sections[title] = lines[index + 1:end]
    return sections


def documentation_entries_present(sections: Mapping[str, Sequence[str]], subjects: Sequence[str]) -> Dict[str, List[str]]:
    """subject -> the ``heading: line`` entries that name it as a whole word
    (``name`` or ``name(``) inside a matching section; an entry is a list
    item, a table row, a definition or a code-spanned name - prose elsewhere
    in the document never counts."""
    found: Dict[str, List[str]] = {subject: [] for subject in subjects}
    for heading, lines in sections.items():
        for line in lines:
            entry_line = bool(_LIST_ENTRY_LINE.match(line))
            for subject in subjects:
                whole_word = r"(?<![\w.])" + re.escape(subject) + r"(?![\w])"
                if (entry_line and re.search(whole_word, line)) or re.match(r"\s*" + whole_word, line):
                    found[subject].append(f"{heading}: {line.strip()}")
    return found


def documentation_entries_predicate(
    text: str, clause: Mapping[str, Any], evidence: Mapping[str, Any], other_statements: Sequence[Tuple[str, str]],
    reader: Callable[[str], Optional[bytes]],
) -> Optional[Dict[str, Any]]:
    """The sealed list-entries predicate of a documentation clause whose
    referent and list section exist at baseline: the paths, the matching
    headings, the subjects (from the goal's own words) and which of them the
    baseline already lists. None when no deterministic subject set exists
    (the clause stays a content claim)."""
    from kriya.workflow.requirement_scopes import documentation_subjects

    subjects, sources = documentation_subjects(text, clause, other_statements)
    if not subjects:
        return None
    noun = str(clause.get("list_noun") or "")
    present_at_baseline: Dict[str, List[str]] = {subject: [] for subject in subjects}
    headings: List[str] = []
    for path in evidence.get("paths") or ():
        data = reader(path)
        if data is None:
            continue
        sections = documentation_sections(data, noun)
        headings += [f"{path}: {heading}" for heading in sections]
        for subject, entries in documentation_entries_present(sections, subjects).items():
            present_at_baseline[subject] += [f"{path}: {entry}" for entry in entries]
    if not headings:
        return None
    return {"paths": list(evidence.get("paths") or ()), "list_noun": noun, "headings": headings,
            "subjects": list(subjects), "source_requirements": list(sources),
            "baseline_present": {s: entries for s, entries in present_at_baseline.items() if entries},
            "baseline_satisfied": all(present_at_baseline[s] for s in subjects),
            "requirement_text_sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest()}


# ------------------------------------------------------------ compilation

class _AcceptanceIds:
    """A minimal stand-in for an acceptance artifact when a caller has only the covered ids (legacy API)."""

    def __init__(self, ids: Iterable[str]) -> None:
        self.requirement_ids = sorted(set(ids))
        self.digest = "ids-only"


def compile_verification_contract(
    requirement_set: RequirementSet, *,
    origins: Optional[Mapping[str, str]] = None,
    test_files: Iterable[str] = (),
    tracked_paths: Iterable[str] = (),
    migration_identities: Iterable[Tuple[str, str]] = (),
    acceptance: Any = None,
    acceptance_ids: Iterable[str] = (),
    approval: Any = None,
    external_authorities: Iterable[ExternalAuthority] = (),
    project_language: Optional[str] = None,
    tracked_file_reader: Optional[Callable[[str], Optional[bytes]]] = None,
    base_revision: Optional[str] = None,
    dispositions: Any = None,
) -> VerificationContract:
    """Compile the contract of ``requirement_set``. Pure given its inputs:
    the statements and their origins, the repository's test files and
    tracked paths, the resolved migration, the bound B2 artifact (or its
    covered ids), the B3 approval, the external authorities, the project
    language (for the API predicate) and a reader for documentation
    referents. Never consults a model."""
    files = sorted(set(test_files))
    tracked = sorted(set(tracked_paths))
    migrations = [tuple(pair) for pair in migration_identities]
    authorities = tuple(external_authorities)
    covered_ids = set(getattr(acceptance, "requirement_ids", ()) or ()) if acceptance is not None else set()
    covered_ids |= set(acceptance_ids)
    acceptance_digest = getattr(acceptance, "digest", None) if acceptance is not None else None
    approval_ids = set(getattr(approval, "entries", {}) or {}) if approval is not None else set()
    approval_digest = getattr(approval, "digest", None) if approval is not None else None
    origins = dict(origins or {})
    roles = None
    if any(statement_scope(r.id, r.text, origin=origins.get(r.id, ORIGIN_SENTENCE), test_files=files,
                           tracked_paths=tracked).scopes == (MUTATION_SCOPE,)
           for r in requirement_set.requirements):
        roles = mutation_path_roles(requirement_set, tracked)

    entries: List[ContractEntry] = []
    for requirement in requirement_set.requirements:
        origin = origins.get(requirement.id, ORIGIN_SENTENCE)
        scope = statement_scope(requirement.id, requirement.text, origin=origin, test_files=files,
                                migration_identities=migrations, tracked_paths=tracked)
        bindings: List[ClaimBinding] = []
        residual: List[ResidualClaim] = []
        ambiguity: Optional[str] = None
        # BACKEND-READINESS-004 (D3): a sealed operator disposition of the whole statement, or of single claims.
        whole = dispositions.whole(requirement.id) if dispositions is not None else None
        dispositioned: List[str] = []
        disposition_detail: Optional[Dict[str, Any]] = None
        if scope.is_non_claim:
            bindings.append(ClaimBinding(NON_CLAIM, CLOSER_NOT_A_CLAIM, AUTHORITY_CONTRACT,
                                         detail={"non_claim_kind": scope.non_claim_kind,
                                                 "reason": scope.non_claim_reason}))
        elif whole is not None:
            disposition_detail = {**whole.to_dict(), "digest": dispositions.digest, "operator": dict(dispositions.operator)}
            bindings.append(_disposition_binding(DISPOSITIONED_STATEMENT, whole, dispositions.digest))
        elif scope.scopes == (MUTATION_SCOPE,):
            if roles and roles["ambiguous"]:
                ambiguity = "the goal names paths whose role (change target or reference) cannot be determined"
            elif roles and roles["authorized"]:
                bindings.append(ClaimBinding(MUTATION_SCOPE, CLOSER_MUTATION_SCOPE, AUTHORITY_REPOSITORY,
                                             detail={"authorized_paths": sorted(roles["authorized"])}))
            else:
                ambiguity = "the goal names no tracked file to change, so 'other' has no referent"
        elif scope.scopes == (MIGRATION,):
            bindings.append(ClaimBinding(MIGRATION, CLOSER_MIGRATION_GATE, AUTHORITY_REPOSITORY,
                                         detail={"migrations": [list(pair) for pair in migrations]}))
            if scope.detail.get("compound"):
                residual.append(ResidualClaim(
                    BEHAVIOR, None, "a compound statement: the migration gate closes only the migration itself, "
                    "the rest of the sentence has no closer", _acceptable(BEHAVIOR, BEHAVIOR_GENERAL)))
        else:
            for claim in scope.claims:
                partial = dispositions.for_claim(requirement.id, claim) if dispositions is not None else None
                if partial is not None:
                    dispositioned.append(claim)
                    bindings.append(_disposition_binding(claim, partial, dispositions.digest))
                    continue
                if claim == REGRESSION_PRESERVATION:
                    if SUITE_PRESERVATION_SCOPE in scope.scopes:
                        bindings.append(ClaimBinding(claim, SUITE_PRESERVATION, AUTHORITY_REPOSITORY))
                    elif scope.named_tests:
                        bindings.append(ClaimBinding(claim, CLOSER_NAMED_TESTS, AUTHORITY_GOAL,
                                                     detail={"tests": list(scope.named_tests)}))
                    external = _covering(authorities, requirement.id, claim, None)
                    if external is not None:
                        # BACKEND-READINESS-004 (AUTHORITY-REGRESSION-CLAIM-COVERAGE-001): the sealed oracle runs
                        # the named tests in its own environment beside the repository oracle, which refuses by
                        # design when the candidate changes the test dependency declaration; either producer's
                        # PASS closes the claim, any producer's FAIL is counter-evidence.
                        bindings.append(_external_binding(claim, external, requirement.id))
                    if not any(b.claim == claim for b in bindings):  # unreachable by construction; fail closed
                        residual.append(ResidualClaim(claim, None, "no named test and no suite statement",
                                                      _acceptable(claim, None)))
                elif claim == TEST_IMMUTABILITY_CLAIM:
                    bindings.append(ClaimBinding(claim, TEST_IMMUTABILITY, AUTHORITY_REPOSITORY))
                elif claim == FILE_IMMUTABILITY_CLAIM:
                    # OD-3: the frozen paths are sealed in the binding - the planner's rule and the terminal closer
                    # read them from here, never from the candidate or a model.
                    bindings.append(ClaimBinding(claim, FILE_IMMUTABILITY, AUTHORITY_REPOSITORY,
                                                 detail={"frozen_paths": list(scope.detail.get("frozen_paths") or ())}))
                elif claim == TEST_ADDITION_CLAIM:
                    bindings.append(ClaimBinding(claim, CLOSER_TEST_ADDITION, AUTHORITY_REPOSITORY))
                elif claim == API_PRESERVATION:
                    external = _covering(authorities, requirement.id, claim, None)
                    if project_language in API_PREDICATE_LANGUAGES:
                        bindings.append(ClaimBinding(claim, CLOSER_API_PRESERVATION, AUTHORITY_REPOSITORY,
                                                     detail={"language": project_language}))
                    if external is not None:
                        bindings.append(_external_binding(claim, external, requirement.id))
                    if not any(b.claim == claim for b in bindings):
                        residual.append(ResidualClaim(
                            claim, None, f"no deterministic public-API predicate for project language "
                            f"{project_language or 'unknown'}", _acceptable(claim, None)))
                elif claim == DOCUMENTATION_CLAIM:
                    clause = scope.documentation or {}
                    present, evidence = _documentation_list_present(clause, tracked, tracked_file_reader)
                    external = _covering(authorities, requirement.id, claim, None)
                    if clause.get("conditional") and present is False:
                        bindings.append(ClaimBinding(claim, CLOSER_DOCUMENTATION_NOT_APPLICABLE, AUTHORITY_REPOSITORY,
                                                     detail=evidence))
                    elif present and clause.get("list_noun") and tracked_file_reader is not None:
                        # BACKEND-READINESS-004 (owner decision 2): the list exists - seal the predicate now, from
                        # the baseline: which section, which subjects, which are already listed.
                        predicate = documentation_entries_predicate(
                            requirement.text, clause, evidence,
                            [(r.id, r.text) for r in requirement_set.requirements if r.id != requirement.id],
                            tracked_file_reader)
                        if predicate is not None:
                            bindings.append(ClaimBinding(claim, CLOSER_DOCUMENTATION_LIST_ENTRIES, AUTHORITY_REPOSITORY,
                                                         detail=predicate))
                    if external is not None:
                        bindings.append(_external_binding(claim, external, requirement.id))
                    if not any(b.claim == claim for b in bindings):
                        why = ("the documentation referent exists; documenting it correctly is a content claim "
                               "Kriya cannot verify deterministically" if present else
                               "an unconditional documentation request is a content claim Kriya cannot verify "
                               "deterministically" if present is False else
                               f"the documentation referent could not be inspected: {evidence.get('reason')}")
                        residual.append(ResidualClaim(claim, None, why, _acceptable(claim, None)))
                elif claim == BEHAVIOR:
                    strength = scope.strength
                    external = _covering(authorities, requirement.id, claim, strength)
                    examples = next((a for a in authorities if a.kind == AUTHORITY_GOAL_EXAMPLES
                                     and requirement.id in a.coverage), None)
                    # Every applicable authority binds (complementary producers: any PASS closes, any FAIL is
                    # counter-evidence); the acceptance file and its approval are one artifact, one binding.
                    if requirement.id in covered_ids and strength == BEHAVIOR_EXACT:
                        bindings.append(ClaimBinding(claim, CLOSER_ACCEPTANCE, AUTHORITY_ACCEPTANCE_FILE, acceptance_digest))
                    elif requirement.id in covered_ids and requirement.id in approval_ids:
                        bindings.append(ClaimBinding(claim, CLOSER_ACCEPTANCE_APPROVAL, AUTHORITY_ACCEPTANCE_APPROVAL,
                                                     approval_digest, detail={"acceptance_digest": acceptance_digest}))
                    if examples is not None and strength == BEHAVIOR_EXACT:
                        bindings.append(ClaimBinding(claim, CLOSER_DERIVED_EXAMPLES, AUTHORITY_GOAL_EXAMPLES,
                                                     examples.digest, detail=dict(examples.coverage[requirement.id][BEHAVIOR])))
                    if external is not None:
                        bindings.append(_external_binding(claim, external, requirement.id))
                    if not any(b.claim == claim for b in bindings):
                        if strength == BEHAVIOR_GENERAL:
                            why = ("a general behaviour statement (" + "; ".join(scope.strength_reasons) + "): finite "
                                   "cases never prove it (B2-COV), model judgment never closes it")
                            if requirement.id in covered_ids or examples is not None:
                                why += "; the bound finite cases are supporting evidence only, no approval binds them"
                        elif PROCEDURE in scope.scopes:
                            why = "a procedure only an external authority can execute; model judgment never closes it"
                        else:
                            why = ("an exact behaviour statement with no acceptance case, no compilable example and "
                                   "no external authority; model judgment never closes it")
                        residual.append(ResidualClaim(claim, strength, why, _acceptable(claim, strength)))
            if not scope.claims and not bindings:  # a determinate statement with no claim at all cannot happen; fail closed
                residual.append(ResidualClaim(BEHAVIOR, None, "no claim recognized", _acceptable(BEHAVIOR, None)))
        if dispositioned and scope.claims and set(dispositioned) == set(scope.claims):
            # every claim the statement makes is dispositioned: nothing of it remains an obligation
            parts = [dispositions.for_claim(requirement.id, claim) for claim in scope.claims]
            kinds = sorted({part.disposition for part in parts})
            disposition_detail = {"requirement_id": requirement.id, "claim": None,
                                  "disposition": kinds[0] if len(kinds) == 1 else "MULTIPLE",
                                  "reason": "; ".join(f"{part.claim}: {part.reason}" for part in parts),
                                  "evidence": sorted({item for part in parts for item in part.evidence}),
                                  "claims": [part.to_dict() for part in parts],
                                  "requirement_text_sha256": parts[0].requirement_text_sha256,
                                  "digest": dispositions.digest, "operator": dict(dispositions.operator)}
        # One status rule for every branch: undecidable first, then structural, then dispositioned, then any
        # residual claim.
        status = (STATUS_AMBIGUOUS if ambiguity else STATUS_NOT_A_CLAIM if scope.is_non_claim
                  else STATUS_DISPOSITIONED if disposition_detail is not None
                  else STATUS_AUTHORITY_REQUIRED if residual else STATUS_CLOSABLE)
        entries.append(ContractEntry(requirement.id, requirement.text, requirement.kind, requirement.source, scope,
                                     status, tuple(bindings), tuple(residual), ambiguity,
                                     disposition=disposition_detail, dispositioned_claims=tuple(dispositioned)))
    return VerificationContract(
        goal_digest=requirement_set.goal_digest, requirement_set_digest=requirement_set.digest,
        requirement_contract_digest=requirement_set.contract_digest, base_revision=base_revision,
        project_language=project_language, entries=tuple(entries), authorities=authorities,
        dispositions=dispositions.identity() if dispositions is not None else None,
    )


def _disposition_binding(claim: str, entry: Any, digest: str) -> ClaimBinding:
    return ClaimBinding(claim, CLOSER_OPERATOR_DISPOSITION, AUTHORITY_OPERATOR_DISPOSITION, digest,
                        detail={"disposition": entry.disposition, "reason": entry.reason, "evidence": list(entry.evidence)})


# ------------------------------------------------------------ sealing and ledger

def contract_store(state_root: str) -> str:
    return os.path.join(state_root, CONTRACT_STORE_DIR)


def seal_verification_contract(contract: VerificationContract, state_root: str) -> str:
    """Store the contract content-addressed under Kriya's state directory
    (never the workspace) and return the path; idempotent for the same digest."""
    store = contract_store(state_root)
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{contract.digest}.json")
    if not os.path.isfile(stored):
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(contract.to_dict(), handle, sort_keys=True, indent=1, default=str)
        os.replace(temporary, stored)
    return stored


def record_contract_non_claims(ledger: Any, requirement_set: RequirementSet, contract: VerificationContract, *,
                               source: str) -> List[str]:
    """Record every NOT_A_CLAIM entry of ``contract`` in the ledger (idempotent)."""
    recorded: List[str] = []
    for entry in contract.entries:
        if entry.status != STATUS_NOT_A_CLAIM or non_claim_record(ledger, entry.requirement_id) is not None:
            continue
        record_non_claim(ledger, requirement_set, entry.requirement_id, origin=entry.scope.origin,
                         reason=entry.scope.non_claim_reason or "", source=source)
        recorded.append(entry.requirement_id)
    return recorded


def record_contract_dispositions(ledger: Any, requirement_set: RequirementSet, contract: VerificationContract, *,
                                 source: str) -> List[str]:
    """Record every operator disposition the contract bound (idempotent): the
    whole statement when its status is DISPOSITIONED, else each dispositioned
    claim - audit records, never a candidate's closure."""
    recorded: List[str] = []
    for entry in contract.entries:
        rid = entry.requirement_id
        if entry.status == STATUS_DISPOSITIONED and entry.disposition is not None:
            if disposition_record(ledger, rid) is not None:
                continue
            detail = entry.disposition
            record_requirement_disposition(ledger, requirement_set, rid, disposition=str(detail["disposition"]),
                                           reason=str(detail["reason"]), claim=None, disposition_digest=str(detail["digest"]),
                                           operator=dict(detail["operator"]), evidence=list(detail.get("evidence") or ()),
                                           source=source)
            recorded.append(rid)
            continue
        for binding in entry.bindings:
            if binding.closer != CLOSER_OPERATOR_DISPOSITION or binding.claim not in entry.dispositioned_claims:
                continue
            if any((r.evidence or {}).get("evidence_id") == DISPOSITION_EVIDENCE_ID
                   for r in ledger.history(requirement_claim_id(rid, binding.claim))):
                continue
            record_requirement_disposition(ledger, requirement_set, rid, disposition=str(binding.detail["disposition"]),
                                           reason=str(binding.detail["reason"]), claim=binding.claim,
                                           disposition_digest=str(binding.authority_digest),
                                           operator=dict((contract.dispositions or {}).get("operator") or {}),
                                           evidence=list(binding.detail.get("evidence") or ()), source=source)
            recorded.append(f"{rid}:{binding.claim}")
    return recorded


def bound_external_authorities(engine: Any) -> Tuple[ExternalAuthority, ...]:
    """The external authorities bound to ``engine`` for this run (the CLI sets
    ``WorkflowEngine.verification_authorities``: the sealed operator bundle,
    the goal's compiled examples), or none - never anything that merely looks
    like one."""
    bound = getattr(engine, "verification_authorities", None) or ()
    return tuple(authority for authority in bound if isinstance(authority, ExternalAuthority))


def load_sealed_contract(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
