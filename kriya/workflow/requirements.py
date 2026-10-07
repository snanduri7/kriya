"""PRD-020: immutable original requirements and their lineage.

A user's goal passes through Planner, Architect, Developer and Reviewer
prose. Each of those may paraphrase, narrow or drop what the user asked for.
This module fixes the user's own statements once, at the start of a run, as
revision-bound requirement records (REQ-1, REQ-2, ...) whose text is the
user's text, verbatim:

- Derivation is deterministic and conservative (``derive_requirements``): the
  goal is split only on its own explicit structure (list items, then
  sentences). No model is involved, so no model can reword a requirement;
  the same goal always yields the same ids (``REQUIREMENT_DERIVATION_VERSION``
  is part of the set's identity).
- Each requirement is an ``ORIGINAL_REQUIREMENT`` obligation in the run's
  ObligationLedger, seeded PENDING at the lowest authority so that the
  verifier's evidence (not the seed) becomes its authoritative state.
- Only the verifier stage records an outcome (``record_requirement_verdicts``):
  the Goal Spec Compliance gate, which runs after the deterministic gates
  passed and judges the exact candidate files (fingerprinted). Planner,
  Architect, Developer and Reviewer text never writes an outcome; citing a
  requirement id in a plan or design is lineage, not evidence.
- Verifier verdicts: satisfied, missing (a concrete, literally-named
  requirement is absent - the gate fails and the retry names the REQ id and
  its original text), unverifiable (CANNOT_CONFIRM_FROM_CODE), none
  (UNKNOWN, NO_VERDICT). Both satisfied and missing are MODEL_CLAIMED and
  recorded UNVERIFIED with the model's verdict as provenance (FS-1B, GR-R0):
  only deterministic or human-bound evidence decides CLOSED_BY_EVIDENCE /
  HUMAN_ACCEPTED or VIOLATED. ``blocking_requirements`` applies the policy:
  VIOLATED always blocks success; UNKNOWN and UNVERIFIED block when
  ``autonomy.requirement_unknown_policy`` / ``requirement_unverified_policy``
  say ``block`` (production seals both to ``block``); an UNVERIFIED the
  verifier reported missing blocks whatever the policy.
- UNVERIFIED is never SATISFIED. It can be closed only by another
  authoritative verifier's positive evidence for that exact requirement on
  that exact candidate (``record_requirement_closure``): a separate
  DETERMINISTIC record whose ``evidence_id`` must equal the verifier
  verdict's own, so evidence about an earlier candidate never closes a later
  one. The closed outcome is CLOSED_BY_EVIDENCE, distinct from the
  verifier's SATISFIED, whatever the verifier said (a model "missing" is
  outranked by it). VIOLATED and UNKNOWN are never closed this way.
- Mutation-scope requirements ("do not modify any other file", recognized
  only as a whole statement, ``is_mutation_scope_requirement``) are decided
  from Kriya's own mutation record (``close_mutation_scope_requirements``):
  authorized paths = only the files the goal's own words establish as change
  targets (``mutation_path_roles``: a reference, negated or undecidable
  mention never authorizes; an undecidable one leaves it unresolved), actual paths = what the candidate and the
  run's committed history changed. In scope with nothing foreign closes it
  (MUTATION_SCOPE evidence); a path outside the set is deterministic VIOLATED
  evidence whatever the verifier said; no referent leaves it unresolved.

A plan that paraphrases or omits a requirement cannot remove it: the set is
derived from the goal, not from the plan, and the terminal decision reads
the set.

Every recorded verdict carries a normalized reason code
(MODEL-EVIDENCE-HARDENING-001, ``REQUIREMENT_REASON_CODES``), its evidence
text, the verifier's model/runtime identity and the candidate it judged
(``evidence_id`` + revision); ``requirement_verdict_details`` reports them.
UNKNOWN is never unexplained: a verifier that returned no verdict for an id
(MODEL_RETURNED_NO_VERDICT) is distinguishable from one whose answer could
not be read (MALFORMED_VERIFIER_RESULT) or whose call failed
(VERIFIER_CALL_FAILED), and all of them from a genuine "cannot confirm from
code" (INSUFFICIENT_CODE_EVIDENCE, an UNVERIFIED outcome).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)

REQUIREMENT_DERIVATION_VERSION = 1

REQUIREMENTS_UNRESOLVED = "REQUIREMENTS_UNRESOLVED"
REQUIREMENT_OBLIGATION_PREFIX = "requirement."

_REQ_ID = re.compile(r"\bREQ-(?:C)?\d+\b")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)]|[a-zA-Z][.)])\s+(?P<text>\S.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
# A sentence ends at . ! or ? followed by whitespace and an upper-case letter,
# a digit, a quote or a backtick. Common abbreviations are not ends.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(\[])")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "incl.", "no.", "fig.")
_CONSTRAINT_CUE = re.compile(
    r"\b(do not|don't|must not|mustn't|never|should not|shouldn't|shall not|without|"
    r"no longer|avoid|unchanged|preserve|keep\b[^.]*\bas is)\b",
    re.IGNORECASE,
)


# A concrete, literally-named thing the verifier can find absent from source:
# a code span, a quoted literal, a call, a dotted name (file.py, a.b), a
# camelCase / PascalCase-with-two-humps / snake_case identifier, a number.
# An acronym alone ("API") is not a literal.
_CONCRETE_LITERAL = re.compile(
    r"`[^`]+`|'[^']+'|\"[^\"]+\"|\b\w+\(|\b\w+\.\w+\b|\b[a-z]+[A-Z]\w*|\b[A-Z][a-z0-9]+[A-Z]\w*"
    r"|\b[A-Za-z]\w*_\w+|\b\d+(?:\.\d+)?\b"
)


def names_a_concrete_literal(text: str) -> bool:
    """Whether a requirement names something concrete enough that its
    absence from the code is a fact, not an opinion (the Goal Spec
    Compliance gate's own mandate). A requirement stated only in general
    terms can be confirmed but never reported missing: that claim is
    recorded UNVERIFIED and does not fail the gate."""
    return bool(_CONCRETE_LITERAL.search(text or ""))


# FS-1B: the class of the verifier's verdict - a model's semantic assessment,
# never authorization (recorded on every verdict record).
MODEL_CLAIMED = "MODEL_CLAIMED"


class RequirementOutcome(str, Enum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNVERIFIED = "unverified"  # CANNOT_CONFIRM_FROM_CODE
    UNKNOWN = "unknown"  # NO_VERDICT
    # UNVERIFIED by the verifier, then positively verified for this exact
    # requirement and candidate by deterministic evidence (a test run).
    CLOSED_BY_EVIDENCE = "closed_by_evidence"
    # B3: a GENERAL behaviour claim an operator accepted on the exact approved
    # acceptance suite (kriya/workflow/acceptance_approval.py) - human
    # authority, never a proof. Derived only, like CLOSED_BY_EVIDENCE.
    HUMAN_ACCEPTED = "human_accepted"


# MODEL-EVIDENCE-HARDENING-001: why a requirement has its verdict.
VERIFIER_CONFIRMED = "VERIFIER_CONFIRMED"  # SATISFIED: the verifier found it in the code
VERIFIER_REPORTED_MISSING = "VERIFIER_REPORTED_MISSING"  # VIOLATED: a concrete, named requirement is absent
INSUFFICIENT_CODE_EVIDENCE = "INSUFFICIENT_CODE_EVIDENCE"  # UNVERIFIED: cannot be confirmed from source text
MISSING_CLAIM_NOT_CONCRETE = "MISSING_CLAIM_NOT_CONCRETE"  # UNVERIFIED: "missing" for a non-concrete requirement
CLAIM_CONTRADICTS_STRONGER_AUTHORITY = "CLAIM_CONTRADICTS_STRONGER_AUTHORITY"  # UNVERIFIED: suppressed claim
MODEL_RETURNED_NO_VERDICT = "MODEL_RETURNED_NO_VERDICT"  # UNKNOWN: a readable answer without this id
MALFORMED_VERIFIER_RESULT = "MALFORMED_VERIFIER_RESULT"  # UNKNOWN: the answer (or this entry) was unreadable
VERIFIER_CALL_FAILED = "VERIFIER_CALL_FAILED"  # UNKNOWN: no answer at all (backend error, timeout)
VERIFIER_REQUEST_REFUSED = "VERIFIER_REQUEST_REFUSED"  # UNKNOWN: PRD-016 refused the request before inference
NOT_YET_VERIFIED = "NOT_YET_VERIFIED"  # PENDING: no verifier verdict recorded yet
REQUIREMENT_REASON_CODES = frozenset({
    VERIFIER_CONFIRMED, VERIFIER_REPORTED_MISSING, INSUFFICIENT_CODE_EVIDENCE, MISSING_CLAIM_NOT_CONCRETE,
    CLAIM_CONTRADICTS_STRONGER_AUTHORITY, MODEL_RETURNED_NO_VERDICT, MALFORMED_VERIFIER_RESULT,
    VERIFIER_CALL_FAILED, VERIFIER_REQUEST_REFUSED, NOT_YET_VERIFIED,
})
# The reason a verdict given without one has.
_DEFAULT_REASON = {
    "satisfied": VERIFIER_CONFIRMED,
    "violated": VERIFIER_REPORTED_MISSING,
    "unverified": INSUFFICIENT_CODE_EVIDENCE,
    "unknown": MODEL_RETURNED_NO_VERDICT,
    "pending": NOT_YET_VERIFIED,
}

_OUTCOME_STATUS = {
    RequirementOutcome.PENDING: ObligationStatus.PENDING,
    RequirementOutcome.SATISFIED: ObligationStatus.SATISFIED,
    RequirementOutcome.VIOLATED: ObligationStatus.VIOLATED,
    RequirementOutcome.UNVERIFIED: ObligationStatus.INDETERMINATE,
    RequirementOutcome.UNKNOWN: ObligationStatus.PENDING,
    RequirementOutcome.CLOSED_BY_EVIDENCE: ObligationStatus.SATISFIED,
    RequirementOutcome.HUMAN_ACCEPTED: ObligationStatus.SATISFIED,
}


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    kind: str  # "requirement" | "constraint" (metadata; both are enforced alike)
    source: str  # "goal" | "clarification"
    ordinal: int


@dataclass(frozen=True)
class RequirementSet:
    goal_digest: str
    version: int
    requirements: Tuple[Requirement, ...]
    # GR-R1A: the digest of the operator's explicit requirement contract this
    # closed set came from (kriya/workflow/requirement_contract.py); None for
    # a set derived from the goal, whose digest stays byte-identical.
    contract_digest: Optional[str] = None

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {"version": self.version, "goal_digest": self.goal_digest,
             "requirements": [asdict(r) for r in self.requirements],
             **({"contract_digest": self.contract_digest} if self.contract_digest is not None else {})},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def ids(self) -> List[str]:
        return [r.id for r in self.requirements]

    def get(self, requirement_id: str) -> Optional[Requirement]:
        return next((r for r in self.requirements if r.id == requirement_id), None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version, "goal_digest": self.goal_digest, "digest": self.digest,
            "requirements": [asdict(r) for r in self.requirements],
            **({"contract_digest": self.contract_digest} if self.contract_digest is not None else {}),
        }


def _goal_digest(goal: str) -> str:
    return hashlib.sha256(goal.encode("utf-8")).hexdigest()


def goal_identity(goal: str) -> str:
    """The goal digest a requirement set derived from ``goal`` (with no
    clarifications) carries - the identity an explicit requirement contract
    binds (GR-R1A)."""
    return _goal_digest(goal + "\x00")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _meaningful(text: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", text))


def _split_sentences(paragraph: str) -> List[str]:
    """Sentences of one paragraph; text inside backticks is never split."""
    protected: List[str] = []

    def _protect(match: "re.Match[str]") -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    masked = re.sub(r"`[^`]*`", _protect, paragraph)
    for index, abbreviation in enumerate(_ABBREVIATIONS):
        masked = re.sub(re.escape(abbreviation), f"\x01{index}\x01", masked, flags=re.IGNORECASE)
    parts = _SENTENCE_END.split(masked)

    def _restore(text: str) -> str:
        text = re.sub(r"\x01(\d+)\x01", lambda m: _ABBREVIATIONS[int(m.group(1))], text)
        return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)

    return [_restore(part) for part in parts]


def _segments(goal: str) -> List[str]:
    """The goal's explicit statements, in order: every list item is one
    statement (its continuation lines included); text outside lists is split
    into sentences; a fenced block belongs to the statement before it."""
    segments: List[str] = []
    paragraph: List[str] = []
    item: Optional[List[str]] = None
    in_fence = False

    def _flush_paragraph() -> None:
        if paragraph:
            segments.extend(_split_sentences(" ".join(paragraph)))
            paragraph.clear()

    def _flush_item() -> None:
        nonlocal item
        if item is not None:
            segments.append(" ".join(item))
            item = None

    for line in goal.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            target = item if item is not None else paragraph
            target.append(line.strip())
            continue
        if in_fence:
            (item if item is not None else paragraph).append(line.rstrip())
            continue
        match = _LIST_ITEM.match(line)
        if match:
            _flush_paragraph()
            _flush_item()
            item = [match.group("text")]
        elif not line.strip():
            _flush_item()
            _flush_paragraph()
        elif item is not None and line[:1].isspace():
            item.append(line.strip())
        else:
            _flush_item()
            paragraph.append(line.strip())
    _flush_item()
    _flush_paragraph()
    return [_clean(s) for s in segments if _meaningful(s)]


def derive_requirements(goal: str, clarifications: Sequence[str] = ()) -> RequirementSet:
    """The immutable requirement set of ``goal`` (plus accepted human
    clarifications, which no producer supplies yet). Pure: the same inputs
    always give the same ids and text. A goal with no separable statements
    is one requirement, the whole goal."""
    statements = _segments(goal) or ([_clean(goal)] if _meaningful(goal) else [])
    requirements: List[Requirement] = []
    for index, text in enumerate(statements, start=1):
        requirements.append(Requirement(
            id=f"REQ-{index}", text=text,
            kind="constraint" if _CONSTRAINT_CUE.search(text) else "requirement",
            source="goal", ordinal=index,
        ))
    for index, raw in enumerate(clarifications, start=1):
        text = _clean(raw)
        if _meaningful(text):
            requirements.append(Requirement(
                id=f"REQ-C{index}", text=text,
                kind="constraint" if _CONSTRAINT_CUE.search(text) else "requirement",
                source="clarification", ordinal=len(statements) + index,
            ))
    return RequirementSet(
        goal_digest=_goal_digest(goal + "\x00" + "\x00".join(clarifications)),
        version=REQUIREMENT_DERIVATION_VERSION,
        requirements=tuple(requirements),
    )


def requirement_obligation_id(requirement_id: str) -> str:
    return f"{REQUIREMENT_OBLIGATION_PREFIX}{requirement_id}"


def requirement_closure_id(requirement_id: str) -> str:
    """The separate obligation id closure evidence is recorded under. Never
    the verdict's own id: a DETERMINISTIC record there would outrank every
    later verdict (ObligationLedger.current), including a VIOLATED one about
    a different candidate."""
    return f"{REQUIREMENT_OBLIGATION_PREFIX}{requirement_id}.closure"


def seed_requirement_obligations(ledger: ObligationLedger, requirements: RequirementSet) -> None:
    """Record every requirement PENDING, once. Seeded at JUDGMENT (the
    lowest authority) so the verifier's verdict, not the seed, becomes the
    authoritative state (ObligationLedger.current keeps the latest record of
    same-or-higher authority). Idempotent: a restored or shared ledger that
    already tracks a requirement keeps its history."""
    for requirement in requirements.requirements:
        obligation_id = requirement_obligation_id(requirement.id)
        if ledger.current(obligation_id) is not None:
            continue
        ledger.record(ObligationRecord(
            id=obligation_id, kind=ObligationKind.ORIGINAL_REQUIREMENT,
            status=ObligationStatus.PENDING, authority=ObligationAuthority.JUDGMENT,
            description=requirement.text, source="requirements.derive_requirements",
            revision=0,
            evidence={"requirement_set_digest": requirements.digest, "kind": requirement.kind,
                      "source": requirement.source, "outcome": RequirementOutcome.PENDING.value},
            terminal_required=True,
        ))


def record_requirement_verdicts(
    ledger: ObligationLedger, requirements: RequirementSet,
    verdicts: Mapping[str, Tuple[Any, ...]], *,
    revision: Any, evidence_fingerprint: str, source: str, gate_evidence: Iterable[str] = (),
    only: Optional[Iterable[str]] = None,
    missing_reason: str = MODEL_RETURNED_NO_VERDICT, missing_detail: str = "no verdict",
    verifier: Optional[Mapping[str, Any]] = None,
) -> Dict[str, RequirementOutcome]:
    """Record the verifier's outcome for every requirement (or just the ids
    in ``only``). ``verdicts`` maps id -> (outcome, detail[, reason code]);
    without a reason code the outcome's default applies. A requirement the
    verifier gave no verdict for is UNKNOWN with ``missing_reason`` (why
    there was none) and ``missing_detail``. ``verifier`` is the model/runtime
    that judged. Returns id -> outcome for what was recorded."""
    gate_evidence = list(gate_evidence)
    selected = set(only) if only is not None else None
    outcomes: Dict[str, RequirementOutcome] = {}
    for requirement in requirements.requirements:
        if selected is not None and requirement.id not in selected:
            continue
        entry = verdicts.get(requirement.id)
        if entry is None:
            outcome, detail, reason = RequirementOutcome.UNKNOWN, missing_detail, missing_reason
        else:
            outcome, detail = entry[0], entry[1]
            reason = entry[2] if len(entry) > 2 and entry[2] else _DEFAULT_REASON[outcome.value]
        # FS-1B / GR-R0: LLM output can authorize nothing, in either
        # direction. A verifier's verdict is a MODEL_CLAIMED assessment, never
        # proof: SATISFIED and VIOLATED alike are recorded UNVERIFIED (the
        # model's own verdict, reason and detail kept as provenance). Only
        # deterministic or human-bound evidence for this exact candidate
        # closes it or makes it VIOLATED (requirement_outcomes); a model
        # "missing" alone still blocks success (blocking_requirements).
        model_outcome = outcome
        if outcome in (RequirementOutcome.SATISFIED, RequirementOutcome.VIOLATED):
            outcome = RequirementOutcome.UNVERIFIED
        outcomes[requirement.id] = outcome
        ledger.record(ObligationRecord(
            id=requirement_obligation_id(requirement.id), kind=ObligationKind.ORIGINAL_REQUIREMENT,
            status=_OUTCOME_STATUS[outcome], authority=ObligationAuthority.JUDGMENT,
            description=requirement.text, source=source, revision=revision,
            evidence={
                "requirement_set_digest": requirements.digest, "outcome": outcome.value,
                "reason_code": reason, "detail": detail, "evidence_id": evidence_fingerprint,
                "gate_evidence": gate_evidence, "verifier": dict(verifier or {}),
                "model_outcome": model_outcome.value, "evidence_class": MODEL_CLAIMED,
            },
            terminal_required=True,
        ))
    return outcomes


def requirement_verdict_details(ledger: ObligationLedger, requirements: RequirementSet) -> Dict[str, Dict[str, Any]]:
    """Each requirement's recorded verdict with why: outcome, reason code,
    evidence text, the verifier's identity and the candidate it judged
    (evidence id and revision). A requirement no verdict was recorded for
    is PENDING / NOT_YET_VERIFIED."""
    details: Dict[str, Dict[str, Any]] = {}
    for requirement in requirements.requirements:
        record = ledger.current(requirement_obligation_id(requirement.id))
        evidence = (record.evidence or {}) if record is not None else {}
        recorded = str(evidence.get("outcome") or RequirementOutcome.PENDING.value)
        # FS-1B: the verifier's own verdict is kept as the model's claim; a
        # pre-FS-1 "satisfied" record reports as UNVERIFIED, like requirement_outcomes.
        outcome = (RequirementOutcome.UNVERIFIED.value if recorded == RequirementOutcome.SATISFIED.value
                   else recorded)
        details[requirement.id] = {
            "outcome": outcome,
            "reason_code": evidence.get("reason_code") or _DEFAULT_REASON.get(recorded, NOT_YET_VERIFIED),
            "model_outcome": evidence.get("model_outcome") or (recorded if record is not None else None),
            "evidence_class": evidence.get("evidence_class"),
            "detail": evidence.get("detail") or "",
            "verifier": evidence.get("verifier") or {},
            "evidence_id": evidence.get("evidence_id"),
            "revision": record.revision if record is not None else None,
            "source": record.source if record is not None else None,
        }
    return details


# ------------------------------------------------------------ claims (FS-1C1)
#
# A requirement statement can make more than one claim. "Implement X, keeping
# test T passing" claims new BEHAVIOR (X) and REGRESSION_PRESERVATION (T still
# passes). A named pre-existing test is a known-good oracle: it passed before X
# existed, so it can prove only that it still passes - never X. Each claim is
# closed only by evidence of its own kind; a requirement closes only when every
# claim it makes is closed for the same candidate.

REGRESSION_PRESERVATION = "REGRESSION_PRESERVATION"
BEHAVIOR = "BEHAVIOR"
# Producers that may close each kind. Named-test closure (PRD-020 / FS-1C0) only
# ever proves regression preservation. BEHAVIOR needs independent acceptance
# evidence: the operator's executable acceptance file (FS-1C2 B2-a,
# kriya/workflow/acceptance_oracle.py, ``acceptance_oracle``) or human-bound acceptance
# authority over an exact approved suite (B3, ``human_bound_acceptance``,
# kriya/workflow/acceptance_approval.py).
# REQUIREMENT-CLOSURE-PLAIN-GOAL-001: the candidate's own complete, green full
# test suite closes a pure suite-preservation statement ("every existing test
# must keep passing"); it proves regression preservation only, like a named test.
FULL_REGRESSION_METHOD = "full_regression_oracle"
NAMED_TEST_CLOSURE_METHODS = frozenset({"named_test_run", "named_test_oracle", FULL_REGRESSION_METHOD})
BEHAVIOR_CLOSURE_METHODS = frozenset({"acceptance_oracle", "human_bound_acceptance"})
REQUIREMENT_BEHAVIOR_UNVERIFIED = "REQUIREMENT_BEHAVIOR_UNVERIFIED"

# Words a pure regression-preservation statement is made of besides the test
# references themselves ("tests/test_legacy.py keeps passing", "Behaviour stays
# compatible with the legacy check test_legacy"). Closed on purpose: any other
# word is read as a behaviour claim, so an unknown phrasing fails closed (the
# requirement stays UNVERIFIED), never open.
_PRESERVATION_WORDS = frozenset({
    "a", "all", "an", "and", "are", "be", "behavior", "behaviors", "behaviour", "behaviours", "break", "each", "every",
    "breaking", "breaks", "broken", "check", "checks", "compatibility", "compatible", "continue", "continues",
    "continuing", "ensure", "ensures", "existing", "fail", "failing", "fails", "green", "has", "have", "in",
    "intact", "is", "it", "its", "keep", "keeping", "keeps", "kept", "legacy", "make", "must", "need", "needs",
    "no", "not", "of", "or", "pass", "passed", "passes", "passing", "regress", "regression", "regressions",
    "remain", "remaining", "remains", "shall", "should", "stay", "staying", "stays", "still", "suite", "sure",
    "test", "tests", "that", "the", "their", "they", "to", "unchanged", "will", "with", "without",
})


def requirement_claims(text: str, named_tests: Iterable[str]) -> Tuple[str, ...]:
    """The claims a requirement statement makes, decided deterministically
    from its own words: REGRESSION_PRESERVATION when it names existing tests
    (``named_tests``), or when it is, in its entirety, a suite-preservation
    statement naming no test (REQUIREMENT-CLOSURE-PLAIN-GOAL-001,
    ``is_suite_preservation_requirement``), and BEHAVIOR when anything remains
    once the test references and the closed preservation vocabulary are removed
    (or when it names no test at all)."""
    references = set()
    if not any(named_tests) and is_suite_preservation_requirement(text):
        return (REGRESSION_PRESERVATION,)
    for path in named_tests:
        base = path.rsplit("/", 1)[-1]
        references.update({path.lower(), base.lower(), base.rsplit(".", 1)[0].lower()})
    leftover = [
        token for token in (raw.strip("./").lower() for raw in _TEST_REFERENCE.findall(text or ""))
        if any(ch.isalnum() for ch in token) and token not in references and token not in _PRESERVATION_WORDS
    ]
    named = bool(references)
    return (((BEHAVIOR,) if leftover or not named else ())
            + ((REGRESSION_PRESERVATION,) if named else ()))


# ------------------------------------------------------------ claim strength (B2-COV)
#
# Evidence closes a claim only at the strength it demonstrates. A trusted
# counterexample disproves a general rule; finite passing examples never prove
# one. So a BEHAVIOR statement is EXACT (enumerated: every observable
# expectation is a stated concrete case, e.g. "`f(0)` returns X") or GENERAL (a
# rule over a domain: "of the form", "any other string", "returns 2 * x").
# Deterministic, from the user's words only; anything uncertain is GENERAL.
# Acceptance cases (finite examples) close only an EXACT statement; for a
# GENERAL one they are supporting evidence (BEHAVIOR_EXAMPLES) and the claim
# stays open - only a counterexample (VIOLATED) is decisive. Another authority
# for a general rule (B3: a human approving a suite as sufficient) is separate.

BEHAVIOR_EXACT = "EXACT"
BEHAVIOR_GENERAL = "GENERAL"
# Supporting evidence for a GENERAL statement: the operator's cases passed. Never
# a closure claim (``_effective_closure`` never requires or accepts it).
BEHAVIOR_EXAMPLES = "BEHAVIOR_EXAMPLES"
# Producers whose positive evidence is a finite set of cases.
FINITE_EVIDENCE_METHODS = frozenset({"acceptance_oracle"})
# B3: human authority over an exact approved suite (not finite-evidence gated;
# only ever a BEHAVIOR claim, never a whole-requirement closure record).
HUMAN_ACCEPTANCE_METHOD = "human_bound_acceptance"

_UNIVERSAL_WORDS = frozenset({
    "any", "anything", "every", "everything", "all", "each", "only", "never", "always", "whatever", "whichever",
    "arbitrary", "regardless", "otherwise", "none", "nothing",
})
_UNIVERSAL_PHRASES = ("of the form", "no other", "for any", "for all", "for every", "in any", "in all",
                      "match", "pattern", "range", "between", "at least", "at most", "up to")
_PRESERVATION_MARKER = re.compile(
    r"\b(?:as before|unchanged|keeps? working|keeps? passing|keeping\b.*\bpassing|continues? to (?:work|pass)"
    r"|still (?:works?|passes|pass)|stays? the same|remains? the same)\b")
_CODE_SPAN = re.compile(r"`[^`]*`|\"[^\"]*\"|'[^']*'")
_LITERAL = r"(?:-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"|None|True|False)"
_EXAMPLE_CALL = re.compile(r"[A-Za-z_][\w.]*\(\s*" + _LITERAL + r"(?:\s*,\s*" + _LITERAL + r")*\s*\)")
_EMPTY_CALL_EXAMPLE = re.compile(r"`[A-Za-z_][\w.]*\(\s*\)`")
_SIGNATURE = re.compile(r"\b[A-Za-z_]\w*\(\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*\)")
_PLACEHOLDER = re.compile(r"<[^<>\s]+>")
_CLAUSE_SPLIT = re.compile(r"[,;:()]|\bwhile\b")


def behavior_strength(text: str, *, regression_covered: bool) -> Tuple[str, Dict[str, Any]]:
    """EXACT or GENERAL for a requirement's BEHAVIOR claim, with why.

    GENERAL when any behaviour clause carries a universal quantifier or domain
    phrase, a ``<placeholder>``, or a declared parameter used as a formula
    ("double(x) returns 2 * x"); when the statement states no concrete example
    call; or when it has a preservation clause ("keeps working as before") that
    no named regression oracle covers (``regression_covered``). Preservation
    clauses covered by the regression claim are that claim's, not behaviour's.
    Quoted text and code spans are content, never quantifiers."""
    raw = text or ""
    reasons: List[str] = []
    parameters = {name.strip() for match in _SIGNATURE.finditer(raw) for name in match.group(1).split(",")}
    parameters -= {"None", "True", "False"}
    # Example calls, signatures and quoted/code text are content, never prose.
    prose = _CODE_SPAN.sub(" CODE ", _SIGNATURE.sub(" CODE ", _EXAMPLE_CALL.sub(" CODE ", raw)))
    for clause in _CLAUSE_SPLIT.split(prose):
        lowered = clause.lower()
        words = set(re.findall(r"[a-z]+", lowered))
        if _PRESERVATION_MARKER.search(lowered):
            if not regression_covered:
                reasons.append(f"preservation without a regression oracle: {clause.strip()!r}")
            continue
        cues = sorted(words & _UNIVERSAL_WORDS) + [p for p in _UNIVERSAL_PHRASES if re.search(rf"\b{p}", lowered)]
        if cues:
            reasons.append(f"general rule ({', '.join(cues)}): {clause.strip()!r}")
        formula = sorted(name for name in parameters if re.search(rf"(?<![\w(]){re.escape(name)}(?![\w(])", clause))
        if formula:
            reasons.append(f"formula over parameter(s) {', '.join(formula)}: {clause.strip()!r}")
    if _PLACEHOLDER.search(raw):
        reasons.append("placeholder pattern: " + ", ".join(sorted(set(_PLACEHOLDER.findall(raw)))))
    examples = sorted(set(_EXAMPLE_CALL.findall(raw)) | set(m.strip("`") for m in _EMPTY_CALL_EXAMPLE.findall(raw)))
    if not examples:
        reasons.append("no concrete example case is stated")
    strength = BEHAVIOR_GENERAL if reasons else BEHAVIOR_EXACT
    return strength, {"strength": strength, "reasons": reasons, "examples": examples}


def requirement_claim_id(requirement_id: str, claim: str) -> str:
    """The obligation id one claim's evidence is recorded under (never the
    verdict's or the whole requirement's closure id)."""
    return f"{REQUIREMENT_OBLIGATION_PREFIX}{requirement_id}.claim.{claim.lower()}"


# What one claim judgment recorded: SATISFIED closes the claim, VIOLATED is
# deterministic counter-evidence (the requirement becomes VIOLATED), and
# INDETERMINATE says the evidence could not be obtained - it closes nothing and
# revokes any earlier judgment of the same claim on the same candidate.
_CLAIM_STATUSES = frozenset({ObligationStatus.SATISFIED, ObligationStatus.VIOLATED, ObligationStatus.INDETERMINATE})


def record_requirement_claim(
    ledger: ObligationLedger, requirements: RequirementSet, requirement_id: str, claim: str, *,
    evidence_id: str, method: str, detail: Dict[str, Any], source: str, revision: Any,
    status: ObligationStatus = ObligationStatus.SATISFIED,
) -> None:
    """Deterministic evidence about one claim of ``requirement_id`` for the
    candidate ``evidence_id`` (``status``: SATISFIED closes it, VIOLATED is
    counter-evidence, INDETERMINATE closes nothing). Only the producer kinds
    allowed for that claim may record it: a regression oracle can never judge
    BEHAVIOR. The latest record for a candidate is its judgment."""
    allowed = {REGRESSION_PRESERVATION: NAMED_TEST_CLOSURE_METHODS, BEHAVIOR: BEHAVIOR_CLOSURE_METHODS,
               BEHAVIOR_EXAMPLES: BEHAVIOR_CLOSURE_METHODS}
    if method not in allowed.get(claim, frozenset()):
        raise ValueError(f"method {method!r} cannot close a {claim} claim")
    if status not in _CLAIM_STATUSES:
        raise ValueError(f"a claim judgment is SATISFIED, VIOLATED or INDETERMINATE, not {status}")
    requirement = requirements.get(requirement_id)
    if requirement is None:
        raise ValueError(f"unknown requirement id {requirement_id!r}")
    ledger.record(ObligationRecord(
        id=requirement_claim_id(requirement_id, claim), kind=ObligationKind.ORIGINAL_REQUIREMENT,
        status=status, authority=ObligationAuthority.DETERMINISTIC,
        description=requirement.text, source=source, revision=revision,
        evidence={"requirement_set_digest": requirements.digest, "evidence_id": evidence_id,
                  "claim": claim, "method": method, **detail},
        terminal_required=False,
    ))


def requirement_claim_record(
    ledger: ObligationLedger, requirement_id: str, claim: str, evidence_id: Optional[str],
) -> Optional[ObligationRecord]:
    """The latest judgment of ``claim`` of ``requirement_id`` on the candidate
    ``evidence_id`` (any status), if any. A later judgment of the same
    candidate replaces an earlier one: a failed or unobtainable re-run never
    leaves an older closure standing."""
    if not evidence_id:
        return None
    for record in reversed(ledger.history(requirement_claim_id(requirement_id, claim))):
        if (record.evidence or {}).get("evidence_id") == evidence_id:
            return record
    return None


def requirement_claim(
    ledger: ObligationLedger, requirement_id: str, claim: str, evidence_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The evidence closing ``claim`` of ``requirement_id`` on the candidate
    ``evidence_id``, if its latest judgment closed it."""
    record = requirement_claim_record(ledger, requirement_id, claim, evidence_id)
    if record is not None and record.status is ObligationStatus.SATISFIED:
        return record.evidence or {}
    return None


def _claim_counter_evidence(
    ledger: ObligationLedger, requirement_id: str, evidence_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """BEHAVIOR counter-evidence: the latest acceptance judgment of this
    candidate observed the behaviour contradicted (B2-a)."""
    record = requirement_claim_record(ledger, requirement_id, BEHAVIOR, evidence_id)
    if record is not None and record.status is ObligationStatus.VIOLATED:
        return record.evidence or {}
    return None


def _effective_closure(
    ledger: ObligationLedger, requirement: Requirement, evidence_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The evidence that closes ``requirement`` for ``evidence_id``: its own
    closure record - except a named-test closure of a statement that also
    claims BEHAVIOR (a pre-FS-1C1 record read back on resume, or any other
    writer), which proves only regression preservation - or else every claim
    the statement makes closed by its own kind of evidence. Which claims the
    statement makes is what the BEHAVIOR producer decided from the statement
    and the repository's test files (``required_claims``); without it, both."""
    closure = requirement_closure(ledger, requirement.id, evidence_id)
    if (closure is not None and closure.get("method") in NAMED_TEST_CLOSURE_METHODS
            and BEHAVIOR in requirement_claims(requirement.text, closure.get("tests") or ())):
        closure = None
    if (closure is not None and closure.get("method") in FINITE_EVIDENCE_METHODS
            and not _finite_evidence_may_close(requirement, tuple(closure.get("required_claims") or ()))):
        closure = None
    if closure is not None and closure.get("method") == HUMAN_ACCEPTANCE_METHOD:
        closure = None  # B3 closes only the BEHAVIOR claim, never a whole requirement
    if closure is not None:
        return closure
    claims = {claim: requirement_claim(ledger, requirement.id, claim, evidence_id)
              for claim in (BEHAVIOR, REGRESSION_PRESERVATION)}
    behavior = claims[BEHAVIOR]
    required = tuple((behavior or {}).get("required_claims") or (BEHAVIOR, REGRESSION_PRESERVATION))
    if (behavior is not None and behavior.get("method") in FINITE_EVIDENCE_METHODS
            and not _finite_evidence_may_close(requirement, required)):
        return None  # B2-COV: finite cases never close a general rule, whatever a record says
    if (behavior is not None and behavior.get("method") == HUMAN_ACCEPTANCE_METHOD
            and not _human_acceptance_binds(requirement, behavior)):
        return None  # B3: the approval was for other words (a resumed or altered record)
    if BEHAVIOR in required and all(claims.get(claim) for claim in required):
        return {"method": "claims", "claims": {claim: claims[claim] for claim in required}}
    return None


def _human_acceptance_binds(requirement: Requirement, evidence: Mapping[str, Any]) -> bool:
    """B3, re-checked at read time: the approval was granted for exactly this
    requirement's words (the closure site checks every other binding)."""
    entry = evidence.get("approval") or {}
    return (bool(evidence.get("approval_digest")) and entry.get("requirement_id") == requirement.id
            and entry.get("requirement_text_sha256")
            == hashlib.sha256((requirement.text or "").encode("utf-8")).hexdigest())


def _finite_evidence_may_close(requirement: Requirement, required: Sequence[str]) -> bool:
    """B2-COV, decided again at read time from the user's words (a resumed or
    pre-B2-COV record cannot carry a broader closure than they allow): finite
    cases close only an EXACT statement. A preservation clause counts as
    covered only when the closure also requires the regression claim."""
    covered = REGRESSION_PRESERVATION in required
    return behavior_strength(requirement.text, regression_covered=covered)[0] == BEHAVIOR_EXACT


def record_requirement_closure(
    ledger: ObligationLedger, requirements: RequirementSet, requirement_id: str, *,
    evidence_id: str, method: str, detail: Dict[str, Any], source: str, revision: Any,
    violated: bool = False,
) -> None:
    """Record deterministic evidence about ``requirement_id`` for the
    candidate identified by ``evidence_id`` (the verifier verdict's own
    evidence id). Positive evidence closes the requirement only while the
    verifier's current verdict is UNVERIFIED for that same evidence id
    (``requirement_outcomes``); ``violated`` evidence (a deterministic
    counter-proof, e.g. a mutation outside the authorized scope) makes it
    VIOLATED whatever the verifier said. It never touches the verdict record."""
    requirement = requirements.get(requirement_id)
    if requirement is None:
        raise ValueError(f"unknown requirement id {requirement_id!r}")
    ledger.record(ObligationRecord(
        id=requirement_closure_id(requirement_id), kind=ObligationKind.ORIGINAL_REQUIREMENT,
        status=ObligationStatus.VIOLATED if violated else ObligationStatus.SATISFIED,
        authority=ObligationAuthority.DETERMINISTIC,
        description=requirement.text, source=source, revision=revision,
        evidence={"requirement_set_digest": requirements.digest, "evidence_id": evidence_id,
                  "method": method, **detail},
        terminal_required=False,
    ))


def requirement_closure(
    ledger: ObligationLedger, requirement_id: str, evidence_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The closure evidence recorded for ``requirement_id`` on the candidate
    ``evidence_id``, if any (the most recent matching record)."""
    if not evidence_id:
        return None
    for record in reversed(ledger.history(requirement_closure_id(requirement_id))):
        evidence = record.evidence or {}
        if record.status is ObligationStatus.SATISFIED and evidence.get("evidence_id") == evidence_id:
            return evidence
    return None


def requirement_counter_evidence(
    ledger: ObligationLedger, requirement_id: str, evidence_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Deterministic VIOLATED evidence recorded for ``requirement_id`` on the
    candidate ``evidence_id``, if any."""
    if not evidence_id:
        return None
    for record in reversed(ledger.history(requirement_closure_id(requirement_id))):
        evidence = record.evidence or {}
        if record.status is ObligationStatus.VIOLATED and evidence.get("evidence_id") == evidence_id:
            return evidence
    return None


def requirement_outcomes(ledger: ObligationLedger, requirements: RequirementSet) -> Dict[str, RequirementOutcome]:
    """Each requirement's authoritative outcome (PENDING until a verdict).
    Deterministic counter-evidence for the candidate the verdict judged makes
    it VIOLATED, whatever the verdict. UNVERIFIED becomes CLOSED_BY_EVIDENCE
    only with closure evidence for that exact candidate; a SATISFIED verdict
    does too when the evidence is a deterministic proof of the requirement
    itself (MUTATION_SCOPE), so the outcome names what actually proved it.
    VIOLATED, UNKNOWN and PENDING verdicts are never closed."""
    outcomes: Dict[str, RequirementOutcome] = {}
    for requirement in requirements.requirements:
        record = ledger.current(requirement_obligation_id(requirement.id))
        evidence = (record.evidence or {}) if record is not None else {}
        raw = evidence.get("outcome")
        try:
            outcome = RequirementOutcome(raw) if raw else RequirementOutcome.PENDING
        except ValueError:
            outcome = RequirementOutcome.UNKNOWN
        if outcome in (RequirementOutcome.CLOSED_BY_EVIDENCE, RequirementOutcome.HUMAN_ACCEPTED):
            outcome = RequirementOutcome.UNKNOWN  # only derived here, never a recorded verdict
        if outcome is RequirementOutcome.VIOLATED:
            # GR-R0: a verdict record is the verifier's MODEL_CLAIMED judgment
            # (a pre-GR-R0 record, e.g. read back on resume): never VIOLATED by
            # itself; deterministic counter-evidence below decides that.
            outcome = RequirementOutcome.UNVERIFIED
        evidence_id = evidence.get("evidence_id")
        closure = _effective_closure(ledger, requirement, evidence_id)
        if (requirement_counter_evidence(ledger, requirement.id, evidence_id) is not None
                or _claim_counter_evidence(ledger, requirement.id, evidence_id) is not None):
            outcome = RequirementOutcome.VIOLATED
        elif closure is not None and outcome in (RequirementOutcome.UNVERIFIED, RequirementOutcome.SATISFIED):
            human = ((closure.get("claims") or {}).get(BEHAVIOR) or {}).get("method") == HUMAN_ACCEPTANCE_METHOD
            outcome = RequirementOutcome.HUMAN_ACCEPTED if human else RequirementOutcome.CLOSED_BY_EVIDENCE
        elif outcome is RequirementOutcome.SATISFIED:
            # FS-1B: a SATISFIED verdict record without deterministic closure
            # (a pre-FS-1 record, e.g. read back on resume) authorizes nothing.
            outcome = RequirementOutcome.UNVERIFIED
        outcomes[requirement.id] = outcome
    return outcomes


def requirement_evidence(ledger: ObligationLedger, requirements: RequirementSet) -> Dict[str, Dict[str, Any]]:
    """The deterministic evidence behind each requirement's current outcome
    (counter-evidence first, then closure evidence), bound to the candidate
    its verdict judged; requirements without any are left out."""
    found: Dict[str, Dict[str, Any]] = {}
    for requirement in requirements.requirements:
        record = ledger.current(requirement_obligation_id(requirement.id))
        evidence_id = (record.evidence or {}).get("evidence_id") if record is not None else None
        evidence = (requirement_counter_evidence(ledger, requirement.id, evidence_id)
                    or _claim_counter_evidence(ledger, requirement.id, evidence_id)
                    or _effective_closure(ledger, requirement, evidence_id))
        if evidence is None:
            # FS-1C1: an open requirement whose claims are partly proven shows
            # which (e.g. regression preserved, behaviour unverified).
            claims = {claim: requirement_claim(ledger, requirement.id, claim, evidence_id)
                      for claim in (BEHAVIOR, BEHAVIOR_EXAMPLES, REGRESSION_PRESERVATION)}
            if any(claims.values()):
                evidence = {"method": "claims", "closed": False, "claims": claims}
        if evidence is not None:
            found[requirement.id] = dict(evidence)
    return found


# ------------------------------------------------------------ mutation scope

MUTATION_SCOPE = "MUTATION_SCOPE"

# A requirement that forbids modifying any file other than the ones the goal
# itself names. Matched against the whole requirement, never a fragment: a
# requirement about other *methods*, or "keep the change small", is not a
# file-boundary statement and is never treated as one.
_MUTATION_SCOPE_REQUIREMENT = re.compile(
    r"(?:do not|don't|must not|mustn't|should not|shouldn't|shall not|never)\s+"
    r"(?:modify|change|edit|touch|alter)\s+any\s+other\s+files?"
    r"(?:\s+in\s+(?:the|this)\s+(?:repository|repo|project|codebase|workspace))?\s*[.!]?",
    re.IGNORECASE,
)
def is_mutation_scope_requirement(text: str) -> bool:
    """Whether a requirement is, in its entirety, the file-boundary statement
    "do not modify any other file (in the repository)"."""
    return bool(_MUTATION_SCOPE_REQUIREMENT.fullmatch(_clean(text or "")))


# One token of a requirement, in order: a code span, a word/path, or a
# clause boundary (";"). A path's role comes only from the words of its own
# clause.
_ROLE_TOKEN = re.compile(r"`(?P<code>[^`]+)`|(?P<word>[\w./-]+)|(?P<boundary>;)")


def _forms(*bases: str) -> frozenset:
    forms = set()
    for base in bases:
        stem = base[:-1] if base.endswith("e") else base
        forms.update({base, base + "s", stem + "ed", stem + "ing", base + "d"})
    return frozenset(forms)


# Closed vocabularies (no model): a path is a mutation TARGET only when the
# nearest cue before it, in its clause, is one of these verbs, un-negated.
_MUTATION_CUES = _forms("modify", "change", "edit", "fix", "update", "patch", "rewrite", "refactor",
                        "implement", "correct", "adjust", "alter", "amend", "add", "remove", "delete",
                        "rename", "replace", "create", "write") | frozenset({"modifies", "modified", "fixes",
                                                                             "patches", "written", "wrote"})
# ... and a REFERENCE (never writable) when that cue is one of these.
_REFERENCE_CUES = _forms("compare", "see", "refer", "reference", "read", "consult", "mirror", "follow",
                         "inspect", "use", "reuse", "look") | frozenset({
                             "like", "according", "based", "similar", "against", "per", "cf", "example",
                             "template", "seen", "compared"})
# Relational words whose object's role cannot be read deterministically
# ("next to X", "replace it with X", "beside X"): the path's role is unknown,
# so the scope requirement stays unresolved rather than guessed.
_UNDECIDABLE_CUES = frozenset({"with", "next", "beside", "besides", "alongside", "near", "into", "onto"})
# A new file is a target only under a creation verb (it is not tracked yet).
_CREATION_CUES = _forms("create", "add", "write") | frozenset({"written", "wrote"})
_NEGATIONS = frozenset({"not", "never", "don't", "dont", "doesn't", "without", "avoid", "no", "nor"})
_NEW_FILE_SHAPE = re.compile(r"^[\w.-]+(?:/[\w.-]+)*\.[A-Za-z][\w]{0,9}$")


def _path_token(raw: str) -> str:
    return raw.strip().strip("'\"").rstrip(".,:!?)").lstrip("(").removeprefix("./")


def mutation_path_roles(requirements: RequirementSet, tracked_paths: Iterable[str]) -> Dict[str, Any]:
    """The role of every repository path the goal names, from the goal's own
    words only - never the Planner's, Architect's or Developer's choices.

    A named path is a path tracked at the run's base (exact match, no
    basename/stem), or an untracked file-shaped path under a creation verb.
    Its role at each mention is decided by the nearest cue word before it in
    the same clause (code spans are skipped): a mutation verb makes it a
    ``target`` (``Modify src/A.java and src/B.java`` - both), a reference
    word a ``reference`` (``Compare it with src/B.java``), a negated
    mutation verb ``forbidden``. No cue, or different roles at different
    mentions, is ``ambiguous``. Returns sorted ``authorized`` (targets),
    ``references``, ``forbidden`` and ``ambiguous`` path lists."""
    tracked = set(tracked_paths)
    roles: Dict[str, set] = {}
    for requirement in requirements.requirements:
        # REQUIREMENT-CLOSURE-PLAIN-GOAL-001: a path a pure preservation
        # statement names ("tests/test_a.py must keep passing") is read, never
        # written - a reference, whatever precedes it.
        named = named_existing_tests(requirement.text, tracked)
        if named and requirement_claims(requirement.text, named) == (REGRESSION_PRESERVATION,):
            for path in named:
                roles.setdefault(path, set()).add("reference")
            continue
        tokens = [(m.group("code"), m.group("word"), m.group("boundary"))
                  for m in _ROLE_TOKEN.finditer(requirement.text)]
        for index, (code, word, _) in enumerate(tokens):
            raw = code if code is not None else word
            if raw is None:
                continue
            path = _path_token(raw)
            role, cue, relational = None, None, False
            for back in range(index - 1, -1, -1):
                b_code, b_word, b_boundary = tokens[back]
                if b_boundary:
                    break
                if b_word is None:
                    continue  # a code span is never a cue
                lowered = b_word.lower().strip(".,:!?")
                if lowered in _UNDECIDABLE_CUES:
                    relational = True
                    continue
                if lowered in _MUTATION_CUES or lowered in _REFERENCE_CUES:
                    cue = lowered
                    preceding = [w.lower() for _, w, _ in tokens[max(0, back - 3):back] if w]
                    if lowered in _REFERENCE_CUES and lowered not in _MUTATION_CUES:
                        role = "reference"  # "compare it with X": still a reference
                    elif relational:
                        role = None  # "replace it with X", "create N next to X": undecidable
                    elif any(w in _NEGATIONS for w in preceding):
                        role = "forbidden"
                    else:
                        role = "target"
                    break
            if path in tracked:
                pass
            elif role == "target" and cue in _CREATION_CUES and _NEW_FILE_SHAPE.match(path) and "(" not in raw:
                pass  # a file the goal asks to create
            else:
                continue
            roles.setdefault(path, set()).add(role or "unknown")
    result: Dict[str, List[str]] = {"authorized": [], "references": [], "forbidden": [], "ambiguous": []}
    key = {"target": "authorized", "reference": "references", "forbidden": "forbidden"}
    for path, seen in sorted(roles.items()):
        result[key[next(iter(seen))] if len(seen) == 1 and "unknown" not in seen else "ambiguous"].append(path)
    return result


def close_mutation_scope_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, *,
    tracked_paths: Iterable[str], scope_evidence: Optional[Mapping[str, Any]], source: str, revision: Any,
) -> List[Dict[str, Any]]:
    """Decide every mutation-scope requirement from Kriya's own record of
    what the run changed (``scope_evidence``: ``actual_paths`` - every path
    the candidate changed plus the run's committed history - ``foreign_paths``
    - changes present that the run cannot attribute to itself - and the
    candidate/run identity). ``actual_paths ⊆ authorized`` with nothing
    foreign closes the requirement for the candidate its verdict judged; any
    actual path outside the authorized set is deterministic VIOLATED
    evidence. No referent, no verdict to bind to, unavailable evidence or a
    foreign change leaves it as the verifier left it (fail closed)."""
    roles = mutation_path_roles(requirements, tracked_paths)
    authorized = roles["authorized"]
    attempts: List[Dict[str, Any]] = []
    for requirement in requirements.requirements:
        if not is_mutation_scope_requirement(requirement.text):
            continue
        record = ledger.current(requirement_obligation_id(requirement.id))
        verdict = (record.evidence or {}) if record is not None else {}
        evidence_id = verdict.get("evidence_id")
        closable = verdict.get("outcome") in (RequirementOutcome.SATISFIED.value,
                                              RequirementOutcome.UNVERIFIED.value)
        entry: Dict[str, Any] = {"requirement": requirement.id, "kind": MUTATION_SCOPE, "closed": False,
                                 "authorized_paths": authorized}
        entry.update({"reference_paths": roles["references"], "forbidden_paths": roles["forbidden"],
                      "ambiguous_paths": roles["ambiguous"]})
        if roles["ambiguous"]:
            entry["reason"] = ("the goal names paths whose role (change target or reference) cannot be "
                               "determined: " + ", ".join(roles["ambiguous"]))
        elif not authorized:
            entry["reason"] = "the goal names no file to change, so 'other' has no authoritative referent"
        elif not evidence_id:
            entry["reason"] = "no verifier verdict on this candidate to bind the evidence to"
        elif not scope_evidence or scope_evidence.get("unavailable"):
            entry["reason"] = "mutation evidence unavailable: " + str(
                (scope_evidence or {}).get("unavailable") or "not collected")
        else:
            actual = sorted(set(scope_evidence.get("actual_paths") or ()))
            foreign = sorted(set(scope_evidence.get("foreign_paths") or ()))
            outside = [path for path in actual if path not in set(authorized)]
            detail = {
                "kind": MUTATION_SCOPE, "requirement": requirement.id, "authorized_paths": authorized,
                "reference_paths": roles["references"],
                "actual_paths": actual, "out_of_scope_paths": outside, "foreign_paths": foreign,
                **{key: scope_evidence.get(key) for key in ("run_id", "base_revision", "candidate_revision", "committed_history")},
            }
            entry.update(detail)
            if outside:
                record_requirement_closure(ledger, requirements, requirement.id, evidence_id=evidence_id,
                                           method="mutation_scope", detail=detail, source=source,
                                           revision=revision, violated=True)
                entry["reason"] = f"changed outside the authorized paths: {', '.join(outside)}"
                entry["violated"] = True
            elif foreign:
                entry["reason"] = f"changes present that the run did not make: {', '.join(foreign)}"
            elif not closable:
                entry["reason"] = f"the verifier's verdict is {verdict.get('outcome')}, which evidence never closes"
            else:
                record_requirement_closure(ledger, requirements, requirement.id, evidence_id=evidence_id,
                                           method="mutation_scope", detail=detail, source=source,
                                           revision=revision)
                entry["closed"] = True
        attempts.append(entry)
    return attempts


_TEST_REFERENCE = re.compile(r"[\w./-]+")


def named_existing_tests(text: str, test_files: Iterable[str]) -> List[str]:
    """Test files a requirement's own text names: by path, file name or
    stem (``tests/test_pricing.py``, ``test_pricing``, ``PricingTest``).
    The binding comes from the user's words, never a model's citation."""
    files = sorted(set(test_files))
    names = {token.strip("./") for token in _TEST_REFERENCE.findall(text or "")}
    named: List[str] = []
    for path in files:
        base = path.rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        if path in names or base in names or (len(stem) > 3 and stem in names):
            named.append(path)
    return named


# ------------------------------------------------------------ REQUIREMENT-CLOSURE-PLAIN-GOAL-001
#
# A plain-language goal is valid requirement authority, but every mandatory
# requirement needs a deterministic closer; MODEL_CLAIMED closes nothing. The
# closers the repository and the run can give a plain goal, besides the
# operator's acceptance file (BEHAVIOR) and the named-test oracle:
# - SUITE_PRESERVATION: "every existing test must keep passing (unchanged)",
#   closed by the candidate's own complete, green full suite;
# - TEST_IMMUTABILITY: "do not change any existing test", closed by the run's
#   own mutation record (no existing test file changed or deleted);
# - MUTATION_SCOPE: "do not modify any other file" (close_mutation_scope_requirements).
# A requirement with no closer is RESIDUAL: the goal is refused before any
# model call (GOAL_INSUFFICIENT_FOR_VERIFICATION), naming it and the accepted
# forms. Recognizers are closed vocabularies with full matches - an unknown
# phrasing is residual, never guessed.
GOAL_INSUFFICIENT_FOR_VERIFICATION = "GOAL_INSUFFICIENT_FOR_VERIFICATION"
SUITE_PRESERVATION = "suite_preservation"
TEST_IMMUTABILITY = "test_immutability"
TEST_IMMUTABILITY_METHOD = "test_immutability"
CLOSER_NAMED_TESTS = "named_tests"
CLOSER_ACCEPTANCE = "acceptance"
CLOSER_MUTATION_SCOPE = "mutation_scope"
CLOSER_MIGRATION_GATE = "migration_gate"

ACCEPTED_GOAL_FORMS = (
    "a statement naming existing test files by path or name (closed by running exactly those tests)",
    "a whole-suite preservation statement, e.g. 'Every existing test must keep passing unchanged.' (closed by the "
    "candidate's own complete, green full test suite)",
    "a test-immutability constraint, e.g. 'Do not change any existing test.' (closed by the run's mutation record)",
    "the file-boundary constraint 'Do not modify any other file.' when the goal names the file(s) to change",
    "a behaviour statement covered by an operator acceptance file (--acceptance) or approval (--acceptance-approval)",
)

_SUITE_NOUNS = frozenset({"test", "tests", "suite", "testsuite"})
# Review (2026-10-08): goal-directed words ask for a state change ("Make the failing test pass") - never part of a
# pure preservation statement, whatever the inherited named-test vocabulary allows next to a named test.
_GOAL_DIRECTED_WORDS = frozenset({"make", "fix", "fixed", "fail", "failing", "fails", "failed"})
# A preservation statement that also asks that the tests themselves stay as they are: closed only together with
# the run's mutation record (no existing test changed or deleted) - a green suite alone never proves "unchanged".
_IMMUTABILITY_CUES = frozenset({"unchanged", "intact", "untouched", "unmodified"})
_SUITE_PRESERVATION_CUES = frozenset({"pass", "passes", "passing", "green", "unchanged", "intact", "working", "works",
                                      "succeed", "succeeds", "successful"})
# Everything a pure whole-suite statement may be made of (besides a command or
# path in parentheses or code spans, which are stripped first).
_SUITE_PRESERVATION_WORDS = (_PRESERVATION_WORDS - _GOAL_DIRECTED_WORDS) | frozenset({
    "every", "each", "any", "current", "whole", "entire", "full", "complete", "unit", "integration", "automated",
    "as", "before", "after", "same", "run", "runs", "running", "command", "via", "using", "through", "also", "this",
    "these", "those", "project", "projects", "repository", "repo", "codebase", "module", "modules", "package",
    "packages", "work", "working", "works", "succeed", "succeeds", "successful", "successfully", "exactly", "fully",
    "always", "at", "on", "for", "by", "under", "including", "included", "when", "once", "done", "afterwards",
    "cases", "case", "scenario", "scenarios", "coverage", "please", "already", "present", "previously", "prior",
})
_STRIP_CODE_AND_PARENS = re.compile(r"`[^`]*`|\([^()]*\)|\"[^\"]*\"|'[^']*'")
_TEST_IMMUTABILITY_REQUIREMENT = re.compile(
    r"(?:please\s+)?(?:do not|don't|must not|mustn't|should not|shouldn't|shall not|never|without)\s+"
    r"(?:modify|modifying|change|changing|edit|editing|touch|touching|alter|altering|remove|removing|delete|"
    r"deleting|rewrite|rewriting)\s+(?:any\s+of\s+the\s+|any\s+|the\s+|all\s+)?(?:existing\s+|current\s+|pre-existing\s+)?"
    r"(?:unit\s+|integration\s+)?tests?(?:\s+files?|\s+cases?|\s+suite)?"
    r"(?:\s+(?:in|under)\s+\S+)?\s*[.!]?",
    re.IGNORECASE,
)


def is_suite_preservation_requirement(text: str) -> bool:
    """Whether a requirement is, in its entirety, a whole-suite preservation
    statement: it names the test suite, carries a preservation cue, and
    consists of nothing but the closed suite-preservation vocabulary once
    code spans, quotes and parentheses (a command, a path) are removed. A
    statement naming specific tests is the named-test closer's, not this."""
    stripped = _STRIP_CODE_AND_PARENS.sub(" ", _clean(text or ""))
    tokens = re.findall(r"[a-z]+", stripped.lower())
    if not tokens:
        return False
    words = set(tokens)
    if not (words & _SUITE_NOUNS) or not (words & _SUITE_PRESERVATION_CUES):
        return False
    return all(token in _SUITE_PRESERVATION_WORDS for token in tokens)


def suite_statement_requires_immutability(text: str) -> bool:
    """Whether a suite-preservation statement also asks that the tests stay
    unchanged/intact - then the mutation record must agree before it closes."""
    tokens = set(re.findall(r"[a-z]+", _STRIP_CODE_AND_PARENS.sub(" ", _clean(text or "")).lower()))
    return bool(tokens & _IMMUTABILITY_CUES)


def test_immutability_evidence(
    reference_test_files: Optional[Sequence[str]], present_test_files: Iterable[str],
    scope_evidence: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """What the run's mutation record says about the tests that existed before
    it: ``available`` (the reference set and the record exist), the existing
    test files the candidate changed and those no longer present."""
    if reference_test_files is None:
        return {"available": False, "reason": "the tests that existed before the run cannot be established"}
    if not scope_evidence or scope_evidence.get("unavailable"):
        return {"available": False, "reason": "mutation evidence unavailable: " + str(
            (scope_evidence or {}).get("unavailable") or "not collected")}
    reference = sorted(set(reference_test_files))
    present = set(present_test_files)
    changed = set(scope_evidence.get("actual_paths") or ()) | set(scope_evidence.get("foreign_paths") or ())
    return {"available": True, "reference_test_files": reference,
            "changed_test_files": sorted(path for path in reference if path in changed),
            "missing_test_files": sorted(path for path in reference if path not in present),
            **{key: scope_evidence.get(key) for key in ("run_id", "base_revision", "candidate_revision")}}


def is_test_immutability_requirement(text: str) -> bool:
    """Whether a requirement is, in its entirety, "do not change (any) existing
    test(s)" - closed by the run's own mutation record."""
    return bool(_TEST_IMMUTABILITY_REQUIREMENT.fullmatch(_clean(text or "")))


class GoalAdmissionError(Exception):
    """GOAL_INSUFFICIENT_FOR_VERIFICATION: a mandatory requirement of the goal
    has no deterministic closer; raised before any model call."""

    def __init__(self, residual: Sequence[Mapping[str, Any]], closers: Mapping[str, Sequence[str]]) -> None:
        self.reason_code = GOAL_INSUFFICIENT_FOR_VERIFICATION
        self.residual = [dict(entry) for entry in residual]
        self.closers = {key: list(value) for key, value in closers.items()}
        super().__init__(self.message)

    @property
    def message(self) -> str:
        listed = "; ".join(f"{entry['id']}: {entry['text']!r} ({entry['why']})" for entry in self.residual)
        return (f"{GOAL_INSUFFICIENT_FOR_VERIFICATION}: {len(self.residual)} mandatory requirement(s) of the goal have "
                f"no deterministic closer, so success could never be verified - {listed}. Accepted forms: "
                + "; ".join(ACCEPTED_GOAL_FORMS))

    def to_dict(self) -> Dict[str, Any]:
        return {"reason_code": self.reason_code, "residual": list(self.residual), "closers": dict(self.closers),
                "accepted_forms": list(ACCEPTED_GOAL_FORMS)}


def _migration_terms(identity: str) -> set:
    return {token for token in re.split(r"[-_]", str(identity).lower()) if len(token) >= 3}


def names_migration(text: str, migration_identities: Iterable[Tuple[str, str]]) -> bool:
    """Whether a statement names both the source and the target of one of the
    run's resolved dependency migrations (the deterministic migration gate's
    own binding rule, kriya/workflow/attempt.py _close_requirements_by_migration_gate)."""
    lowered = (text or "").lower()

    def _names(terms: set) -> bool:
        return any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms)

    return any(_names(_migration_terms(source)) and _names(_migration_terms(target))
               for source, target in migration_identities if source and target)


def deterministic_closers(
    requirements: RequirementSet, *, test_files: Iterable[str], acceptance_ids: Iterable[str] = (),
    tracked_paths: Iterable[str] = (), migration_identities: Iterable[Tuple[str, str]] = (),
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """(closers per requirement id, residual requirements): which deterministic
    closer each requirement has, decided from its own words, the repository's
    test files, the operator acceptance file's covered ids and the tracked
    paths - never from a model. A requirement is residual when one of its
    claims has no closer."""
    files = sorted(set(test_files))
    covered = set(acceptance_ids)
    tracked = list(tracked_paths)
    migrations = [tuple(pair) for pair in migration_identities]
    closers: Dict[str, List[str]] = {}
    residual: List[Dict[str, Any]] = []
    roles = mutation_path_roles(requirements, tracked) if any(
        is_mutation_scope_requirement(r.text) for r in requirements.requirements) else None
    for requirement in requirements.requirements:
        found: List[str] = []
        why: Optional[str] = None
        if is_mutation_scope_requirement(requirement.text):
            if roles and roles["ambiguous"]:
                why = "the goal names paths whose role (change target or reference) cannot be determined"
            elif roles and roles["authorized"]:
                found.append(CLOSER_MUTATION_SCOPE)
            else:
                why = "the goal names no tracked file to change, so 'other' has no referent"
        elif is_test_immutability_requirement(requirement.text):
            found.append(TEST_IMMUTABILITY)
        elif migrations and names_migration(requirement.text, migrations):
            found.append(CLOSER_MIGRATION_GATE)
        else:
            named = named_existing_tests(requirement.text, files)
            if not named and is_suite_preservation_requirement(requirement.text):
                found.append(SUITE_PRESERVATION)
                if suite_statement_requires_immutability(requirement.text):
                    found.append(TEST_IMMUTABILITY)
            else:
                claims = requirement_claims(requirement.text, named)
                if named and REGRESSION_PRESERVATION in claims:
                    found.append(CLOSER_NAMED_TESTS)
                if BEHAVIOR in claims:
                    if requirement.id in covered:
                        found.append(CLOSER_ACCEPTANCE)
                    else:
                        why = ("a behaviour statement without an acceptance case; model judgment never closes it"
                               if named else "no existing test named, no acceptance case, not a preservation or "
                                             "scope statement; model judgment never closes it")
        closers[requirement.id] = found
        if why is not None or not found:
            residual.append({"id": requirement.id, "text": requirement.text,
                             "why": why or "no deterministic closer recognized"})
    return closers, residual


def admission_gap(
    requirements: RequirementSet, *, test_files: Iterable[str], acceptance_ids: Iterable[str] = (),
    tracked_paths: Iterable[str] = (), migration_identities: Iterable[Tuple[str, str]] = (),
) -> Optional[GoalAdmissionError]:
    """The typed refusal when a mandatory requirement has no deterministic
    closer, else None. Decided from the authoritative set before any model
    call; a run without a requirement set (``kriya fix``) is never refused."""
    closers, residual = deterministic_closers(requirements, test_files=test_files, acceptance_ids=acceptance_ids,
                                              tracked_paths=tracked_paths, migration_identities=migration_identities)
    return GoalAdmissionError(residual, closers) if residual else None


def close_test_immutability_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, *, reference_test_files: Optional[Sequence[str]],
    present_test_files: Iterable[str], scope_evidence: Optional[Mapping[str, Any]], source: str, revision: Any,
) -> List[Dict[str, Any]]:
    """Decide every test-immutability requirement from the run's own mutation
    record: an existing test file (``reference_test_files``: the files that
    existed before the run) the candidate changed, or that is no longer
    present (``present_test_files``), is deterministic VIOLATED evidence;
    none changed and nothing foreign closes it. Unavailable evidence or an
    unknown reference set leaves the verdict as it is (fail closed)."""
    attempts: List[Dict[str, Any]] = []
    outcomes = requirement_outcomes(ledger, requirements)
    immutability = test_immutability_evidence(reference_test_files, present_test_files, scope_evidence)
    for requirement in requirements.requirements:
        if not is_test_immutability_requirement(requirement.text):
            continue
        record = ledger.current(requirement_obligation_id(requirement.id))
        evidence_id = (record.evidence or {}).get("evidence_id") if record is not None else None
        closable = outcomes.get(requirement.id) is RequirementOutcome.UNVERIFIED  # the mapped outcome (GR-R0)
        entry: Dict[str, Any] = {"requirement": requirement.id, "kind": TEST_IMMUTABILITY, "closed": False}
        if not evidence_id:
            entry["reason"] = "no verifier verdict on this candidate to bind the evidence to"
        elif not immutability["available"]:
            entry["reason"] = immutability["reason"]
        else:
            touched, missing = immutability["changed_test_files"], immutability["missing_test_files"]
            detail = {"kind": TEST_IMMUTABILITY, "requirement": requirement.id, **immutability}
            entry.update(detail)
            if touched or missing:
                record_requirement_closure(ledger, requirements, requirement.id, evidence_id=evidence_id,
                                           method=TEST_IMMUTABILITY_METHOD, detail=detail, source=source,
                                           revision=revision, violated=True)
                entry["reason"] = ("existing test file(s) changed: " + ", ".join(touched) if touched else
                                   "existing test file(s) missing: " + ", ".join(missing))
                entry["violated"] = True
            elif not closable:
                entry["reason"] = f"the requirement's outcome is {outcomes.get(requirement.id)}, which evidence never closes"
            else:
                record_requirement_closure(ledger, requirements, requirement.id, evidence_id=evidence_id,
                                           method=TEST_IMMUTABILITY_METHOD, detail=detail, source=source,
                                           revision=revision)
                entry["closed"] = True
        attempts.append(entry)
    return attempts


def close_suite_preservation_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, *, test_files: Iterable[str],
    run_suite: Any, source: str, revision: Any, test_immutability: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Close every UNVERIFIED whole-suite preservation requirement from the
    candidate's own full test run (``run_suite()``: the production test gate
    result, run once, lazily). Closes only when the structured evidence of
    that run is COMPLETE, the gate passed, at least one test executed and no
    executed case failed; anything else leaves it as it is, with why. A
    statement that also says "unchanged"/"intact" closes only when the run's
    mutation record (``test_immutability``, test_immutability_evidence) shows
    no existing test changed or deleted - a changed one is deterministic
    VIOLATED evidence; an unavailable record closes nothing."""
    from kriya.tools import test_execution

    files = list(test_files)
    outcomes = requirement_outcomes(ledger, requirements)
    pending = [r for r in requirements.requirements
               if outcomes.get(r.id) is RequirementOutcome.UNVERIFIED and is_suite_preservation_requirement(r.text)
               and not named_existing_tests(r.text, files)]
    if not pending:
        return []
    result = run_suite()
    report = test_execution.report_from_result(result)
    summary = report.summary() if report is not None else None
    failing = sorted(case.identity for case in report.cases if case.status not in (test_execution.PASSED, test_execution.SKIPPED))         if report is not None else []
    attempts: List[Dict[str, Any]] = []
    for requirement in pending:
        record = ledger.current(requirement_obligation_id(requirement.id))
        evidence_id = (record.evidence or {}).get("evidence_id") if record is not None else None
        entry: Dict[str, Any] = {"requirement": requirement.id, "kind": SUITE_PRESERVATION, "closed": False,
                                 "success": bool(result.get("success")) if isinstance(result, dict) else None,
                                 "test_execution": summary}
        if not evidence_id:
            entry["reason"] = "the verdict has no evidence id to bind to"
        elif report is None or not report.complete:
            entry["reason"] = "the suite's structured evidence is not COMPLETE: " + str(
                report.reason if report is not None else "no test execution report")
        elif not result.get("success"):
            entry["reason"] = "the full suite did not pass on this candidate"
        elif not report.cases:
            entry["reason"] = "the full suite executed zero tests"
        elif failing:
            entry["reason"] = "executed case(s) did not pass: " + ", ".join(failing[:5])
        else:
            detail = {"kind": SUITE_PRESERVATION, "claim": REGRESSION_PRESERVATION, "cases": len(report.cases),
                      "status_counts": summary["status_counts"], "report_files": list(report.report_files),
                      "gate_id": report.gate_id, "runner": report.runner}
            if suite_statement_requires_immutability(requirement.text):
                immutability = test_immutability if test_immutability is not None else {
                    "available": False, "reason": "no mutation record was supplied"}
                detail["test_immutability"] = dict(immutability)
                entry["test_immutability"] = dict(immutability)
                if not immutability.get("available"):
                    entry["reason"] = "'unchanged' needs the run's mutation record: " + str(immutability.get("reason"))
                    attempts.append(entry)
                    continue
                touched = list(immutability.get("changed_test_files") or ()) + list(immutability.get("missing_test_files") or ())
                if touched:
                    record_requirement_closure(ledger, requirements, requirement.id, evidence_id=evidence_id,
                                               method=FULL_REGRESSION_METHOD, detail=detail, source=source,
                                               revision=revision, violated=True)
                    entry["reason"] = "the suite is green but existing test file(s) changed or vanished: " + ", ".join(touched)
                    entry["violated"] = True
                    attempts.append(entry)
                    continue
            record_requirement_closure(ledger, requirements, requirement.id, evidence_id=evidence_id,
                                       method=FULL_REGRESSION_METHOD, detail=detail, source=source, revision=revision)
            entry["closed"] = True
        attempts.append(entry)
    return attempts


def blocking_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, *,
    unknown_policy: str = "record", unverified_policy: str = "record",
) -> List[Tuple[Requirement, RequirementOutcome]]:
    """The requirements that forbid a successful terminal state. VIOLATED
    always blocks; PENDING and UNKNOWN (no verdict) block under
    ``unknown_policy == "block"``; UNVERIFIED under ``unverified_policy``, and
    always when the verifier reported it missing (GR-R0: an unrefuted model
    negative is not evidence either way, so it cannot become success)."""
    blocking: List[Tuple[Requirement, RequirementOutcome]] = []
    outcomes = requirement_outcomes(ledger, requirements)
    for requirement in requirements.requirements:
        outcome = outcomes[requirement.id]
        if (outcome is RequirementOutcome.VIOLATED
                or (outcome in (RequirementOutcome.PENDING, RequirementOutcome.UNKNOWN)
                    and unknown_policy == "block")
                or (outcome is RequirementOutcome.UNVERIFIED
                    and (unverified_policy == "block" or _model_reported_missing(ledger, requirement.id)))):
            blocking.append((requirement, outcome))
    return blocking


def _model_reported_missing(ledger: ObligationLedger, requirement_id: str) -> bool:
    """The verifier's current verdict for ``requirement_id`` is "missing"."""
    record = ledger.current(requirement_obligation_id(requirement_id))
    evidence = (record.evidence or {}) if record is not None else {}
    return RequirementOutcome.VIOLATED.value in (evidence.get("model_outcome"), evidence.get("outcome"))


def cited_requirement_ids(text: str, requirements: RequirementSet) -> List[str]:
    """Requirement ids a plan or design cites (lineage, never evidence);
    ids the set does not contain are ignored."""
    known = set(requirements.ids)
    return [rid for rid in dict.fromkeys(_REQ_ID.findall(text or "")) if rid in known]


def requirement_lineage(requirements: RequirementSet, stage: str, cited: Sequence[str]) -> Dict[str, Any]:
    """A stage's mapping onto the original requirements: which ids it cites
    and which it leaves out. An omitted requirement stays active."""
    cited_set = set(cited)
    return {
        "stage": stage, "requirement_set_digest": requirements.digest,
        "cited": [rid for rid in requirements.ids if rid in cited_set],
        "omitted": [rid for rid in requirements.ids if rid not in cited_set],
    }


def requirements_prompt_block(requirements: RequirementSet, *, instruction: str = "") -> str:
    """The original requirements as a prompt section: ids and the user's own
    text. ``instruction`` says what the reader should do with them."""
    if not requirements.requirements:
        return ""
    lines = "\n".join(f"{r.id}: {r.text}" for r in requirements.requirements)
    tail = f"\n{instruction}" if instruction else ""
    return (
        "=== Original Requirements (the user's own words; immutable) ===\n"
        f"{lines}{tail}\n"
    )


def parse_requirement_verdicts(
    raw: Any, requirements: RequirementSet,
) -> Tuple[Dict[str, Tuple[RequirementOutcome, str, str]], List[str]]:
    """The verifier's per-requirement verdicts as id -> (outcome, evidence,
    reason code). Accepts a list of {"id", "verdict", "evidence"}; verdict
    satisfied|missing|unverifiable. An unreadable verdict for a known id is
    UNKNOWN / MALFORMED_VERIFIER_RESULT; unknown ids and entries without an
    id are findings and ignored; a requirement the answer does not mention
    stays absent (the caller records it UNKNOWN with the missing reason)."""
    mapping = {"satisfied": RequirementOutcome.SATISFIED, "missing": RequirementOutcome.VIOLATED,
               "unverifiable": RequirementOutcome.UNVERIFIED}
    verdicts: Dict[str, Tuple[RequirementOutcome, str, str]] = {}
    findings: List[str] = []
    if not isinstance(raw, list):
        return verdicts, (["requirement_verdicts missing or not a list"] if raw is not None else [])
    known = set(requirements.ids)
    for entry in raw:
        if not isinstance(entry, dict):
            findings.append(f"unreadable verdict entry {entry!r}")
            continue
        rid, verdict = entry.get("id"), str(entry.get("verdict", "")).strip().lower()
        if rid not in known:
            findings.append(f"verdict for unknown requirement id {rid!r}")
            continue
        if verdict not in mapping:
            findings.append(f"{rid}: unreadable verdict {verdict!r}")
            verdicts[rid] = (RequirementOutcome.UNKNOWN, f"unreadable verdict {verdict!r}", MALFORMED_VERIFIER_RESULT)
            continue
        outcome = mapping[verdict]
        reason = _DEFAULT_REASON[outcome.value]
        if outcome is RequirementOutcome.VIOLATED and not names_a_concrete_literal(requirements.get(rid).text):
            findings.append(f"{rid}: missing claim for a requirement naming nothing concrete; unverified")
            outcome, reason = RequirementOutcome.UNVERIFIED, MISSING_CLAIM_NOT_CONCRETE
        verdicts[rid] = (outcome, str(entry.get("evidence") or ""), reason)
    return verdicts, findings


def verifier_result_verdicts(
    result: Optional[Mapping[str, Any]], requirements: RequirementSet,
) -> Tuple[Dict[str, Tuple[RequirementOutcome, str, str]], List[str], str, str]:
    """(verdicts, findings, missing_reason, missing_detail) of one
    SpecComplianceAgent.check() result: the per-id verdicts, and why any id
    without one has none - the call failed or was refused
    (``failure_reason_code``), the answer was unreadable or its
    requirement_verdicts not a list (MALFORMED_VERIFIER_RESULT), or it simply
    did not mention the id (MODEL_RETURNED_NO_VERDICT)."""
    result = result or {}
    if result.get("status") == "unknown":
        reasoning = str(result.get("reasoning") or "no reason given")
        reason = str(result.get("failure_reason_code") or MALFORMED_VERIFIER_RESULT)
        return {}, [f"verifier status unknown ({reason})", reasoning], reason, reasoning
    raw = result.get("requirement_verdicts")
    verdicts, findings = parse_requirement_verdicts(raw, requirements)
    if raw is None:
        return verdicts, findings, MODEL_RETURNED_NO_VERDICT, "the verifier returned no requirement_verdicts"
    if not isinstance(raw, list):
        return verdicts, findings, MALFORMED_VERIFIER_RESULT, f"requirement_verdicts is {type(raw).__name__}, not a list"
    return verdicts, findings, MODEL_RETURNED_NO_VERDICT, "the verifier's answer gave no verdict for this id"


def close_unverified_requirements_with_named_tests(
    ledger: ObligationLedger, requirements: RequirementSet, *,
    test_files: Iterable[str], modified: Iterable[str], judge: Any, source: str, revision: Any,
) -> List[Dict[str, Any]]:
    """Close each UNVERIFIED requirement whose own text names existing tests,
    by running exactly those tests on the candidate the verifier judged.

    The binding is the user's words (``named_existing_tests``). A named test
    file the candidate wrote or edited (``modified``) is the model's evidence,
    not an independent verifier, and is refused without running anything.
    Otherwise ``judge(named)`` - the FS-1C0 named-test oracle
    (kriya/workflow/named_test_oracle.py), run against the same candidate the
    verdict's ``evidence_id`` identifies - decides: it closes only when the
    oracle is independent of the candidate (its trust surface equals the
    authorized base), every case the base revision executes ran and passed,
    and the evidence is complete; the closure record carries its bindings.
    Returns one record per attempted closure (closed or not, with why)."""
    changed = set(modified)
    files = list(test_files)
    attempts: List[Dict[str, Any]] = []
    outcomes = requirement_outcomes(ledger, requirements)
    for requirement in requirements.requirements:
        if outcomes.get(requirement.id) is not RequirementOutcome.UNVERIFIED:
            continue
        named = named_existing_tests(requirement.text, files)
        if not named:
            continue
        record = ledger.current(requirement_obligation_id(requirement.id))
        evidence_id = (record.evidence or {}).get("evidence_id") if record is not None else None
        entry: Dict[str, Any] = {"requirement": requirement.id, "tests": named, "closed": False}
        touched = sorted(set(named) & changed)
        if touched:
            entry["reason"] = f"named test(s) written or changed by this candidate: {', '.join(touched)}"
        elif not evidence_id:
            entry["reason"] = "the verdict has no evidence id to bind to"
        else:
            judgment = judge(named)
            entry["reason_code"] = judgment.reason_code
            claims = requirement_claims(requirement.text, named)
            entry["claims"] = list(claims)
            if not judgment.closed:
                entry["reason"] = judgment.reason
            elif BEHAVIOR not in claims:
                record_requirement_closure(
                    ledger, requirements, requirement.id, evidence_id=evidence_id, method=judgment.evidence["method"],
                    detail={**judgment.evidence, "tests": named, "claim": REGRESSION_PRESERVATION}, source=source,
                    revision=revision,
                )
                entry["closed"] = True
            else:
                # FS-1C1: the oracle proves only that the named tests still
                # pass; the new behaviour the same statement asks for needs
                # its own independent evidence.
                record_requirement_claim(
                    ledger, requirements, requirement.id, REGRESSION_PRESERVATION, evidence_id=evidence_id,
                    method=judgment.evidence["method"], detail={**judgment.evidence, "tests": named}, source=source,
                    revision=revision,
                )
                entry["regression_preserved"] = True
                entry["closed"] = requirement_outcomes(ledger, requirements)[requirement.id] in (
                    RequirementOutcome.CLOSED_BY_EVIDENCE, RequirementOutcome.HUMAN_ACCEPTED)
                if not entry["closed"]:
                    entry["reason_code"] = REQUIREMENT_BEHAVIOR_UNVERIFIED
                    entry["reason"] = ("the named pre-existing test(s) still pass (regression preserved), but they "
                                       "passed before the requested behaviour existed and cannot prove it; the "
                                       "behaviour has no independent evidence")
        attempts.append(entry)
    return attempts
