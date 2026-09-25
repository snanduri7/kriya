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
- Outcomes: SATISFIED (the verifier found the requirement in the code),
  VIOLATED (a concrete, literally-named requirement is absent - the gate
  fails and the retry names the REQ id and its original text), UNVERIFIED
  (CANNOT_CONFIRM_FROM_CODE: the requirement describes behaviour the
  verifier cannot confirm from source text), UNKNOWN (NO_VERDICT).
  ``blocking_requirements`` applies the policy: VIOLATED always blocks
  success; UNKNOWN and UNVERIFIED block when
  ``autonomy.requirement_unknown_policy`` / ``requirement_unverified_policy``
  say ``block`` (production seals both to ``block``).
- UNVERIFIED is never SATISFIED. It can be closed only by another
  authoritative verifier's positive evidence for that exact requirement on
  that exact candidate (``record_requirement_closure``): a separate
  DETERMINISTIC record whose ``evidence_id`` must equal the verifier
  verdict's own, so evidence about an earlier candidate never closes a later
  one. The closed outcome is CLOSED_BY_EVIDENCE, distinct from the
  verifier's SATISFIED. VIOLATED and UNKNOWN are never closed this way.
- Mutation-scope requirements ("do not modify any other file", recognized
  only as a whole statement, ``is_mutation_scope_requirement``) are decided
  from Kriya's own mutation record (``close_mutation_scope_requirements``):
  authorized paths = the repository files the goal itself names
  (``authorized_mutation_paths``), actual paths = what the candidate and the
  run's committed history changed. In scope with nothing foreign closes it
  (MUTATION_SCOPE evidence); a path outside the set is deterministic VIOLATED
  evidence whatever the verifier said; no referent leaves it unresolved.

A plan that paraphrases or omits a requirement cannot remove it: the set is
derived from the goal, not from the plan, and the terminal decision reads
the set.
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


class RequirementOutcome(str, Enum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNVERIFIED = "unverified"  # CANNOT_CONFIRM_FROM_CODE
    UNKNOWN = "unknown"  # NO_VERDICT
    # UNVERIFIED by the verifier, then positively verified for this exact
    # requirement and candidate by deterministic evidence (a test run).
    CLOSED_BY_EVIDENCE = "closed_by_evidence"


_OUTCOME_STATUS = {
    RequirementOutcome.PENDING: ObligationStatus.PENDING,
    RequirementOutcome.SATISFIED: ObligationStatus.SATISFIED,
    RequirementOutcome.VIOLATED: ObligationStatus.VIOLATED,
    RequirementOutcome.UNVERIFIED: ObligationStatus.INDETERMINATE,
    RequirementOutcome.UNKNOWN: ObligationStatus.PENDING,
    RequirementOutcome.CLOSED_BY_EVIDENCE: ObligationStatus.SATISFIED,
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

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {"version": self.version, "goal_digest": self.goal_digest,
             "requirements": [asdict(r) for r in self.requirements]},
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
        }


def _goal_digest(goal: str) -> str:
    return hashlib.sha256(goal.encode("utf-8")).hexdigest()


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
    verdicts: Mapping[str, Tuple[RequirementOutcome, str]], *,
    revision: Any, evidence_fingerprint: str, source: str, gate_evidence: Iterable[str] = (),
    only: Optional[Iterable[str]] = None,
) -> Dict[str, RequirementOutcome]:
    """Record the verifier's outcome for every requirement (or just the ids
    in ``only``). A requirement the verifier gave no verdict for is UNKNOWN.
    Returns id -> outcome for what was recorded."""
    gate_evidence = list(gate_evidence)
    selected = set(only) if only is not None else None
    outcomes: Dict[str, RequirementOutcome] = {}
    for requirement in requirements.requirements:
        if selected is not None and requirement.id not in selected:
            continue
        outcome, detail = verdicts.get(requirement.id, (RequirementOutcome.UNKNOWN, "no verdict"))
        outcomes[requirement.id] = outcome
        ledger.record(ObligationRecord(
            id=requirement_obligation_id(requirement.id), kind=ObligationKind.ORIGINAL_REQUIREMENT,
            status=_OUTCOME_STATUS[outcome], authority=ObligationAuthority.JUDGMENT,
            description=requirement.text, source=source, revision=revision,
            evidence={
                "requirement_set_digest": requirements.digest, "outcome": outcome.value,
                "detail": detail, "evidence_id": evidence_fingerprint, "gate_evidence": gate_evidence,
            },
            terminal_required=True,
        ))
    return outcomes


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
        if outcome is RequirementOutcome.CLOSED_BY_EVIDENCE:
            outcome = RequirementOutcome.UNKNOWN  # only derived here, never a recorded verdict
        evidence_id = evidence.get("evidence_id")
        closure = requirement_closure(ledger, requirement.id, evidence_id)
        if requirement_counter_evidence(ledger, requirement.id, evidence_id) is not None:
            outcome = RequirementOutcome.VIOLATED
        elif closure is not None and (
                outcome is RequirementOutcome.UNVERIFIED
                or (outcome is RequirementOutcome.SATISFIED and closure.get("kind") == MUTATION_SCOPE)):
            outcome = RequirementOutcome.CLOSED_BY_EVIDENCE
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
                    or requirement_closure(ledger, requirement.id, evidence_id))
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
_PATH_TOKEN = re.compile(r"`([^`]+)`|([\w./-]+)")


def is_mutation_scope_requirement(text: str) -> bool:
    """Whether a requirement is, in its entirety, the file-boundary statement
    "do not modify any other file (in the repository)"."""
    return bool(_MUTATION_SCOPE_REQUIREMENT.fullmatch(_clean(text or "")))


def authorized_mutation_paths(requirements: RequirementSet, tracked_paths: Iterable[str]) -> List[str]:
    """The files the user's own goal names - the only deterministic referent
    of "any other file". A token counts only when it is exactly a path the
    repository tracks at the run's base (no basename, stem or fuzzy match);
    the Planner's or Developer's file choices are never an input. Empty
    means "other" has no authoritative referent."""
    tracked = set(tracked_paths)
    named: List[str] = []
    for requirement in requirements.requirements:
        for code, bare in _PATH_TOKEN.findall(requirement.text):
            token = (code or bare).strip().strip("'\"").rstrip(".,;:!?)").lstrip("(").removeprefix("./")
            if token in tracked and token not in named:
                named.append(token)
    return sorted(named)


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
    authorized = authorized_mutation_paths(requirements, tracked_paths)
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
        if not authorized:
            entry["reason"] = "the goal names no repository file, so 'other' has no authoritative referent"
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


def blocking_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, *,
    unknown_policy: str = "record", unverified_policy: str = "record",
) -> List[Tuple[Requirement, RequirementOutcome]]:
    """The requirements that forbid a successful terminal state. VIOLATED
    always blocks; PENDING and UNKNOWN (no verdict) block under
    ``unknown_policy == "block"``; UNVERIFIED under ``unverified_policy``."""
    blocking: List[Tuple[Requirement, RequirementOutcome]] = []
    outcomes = requirement_outcomes(ledger, requirements)
    for requirement in requirements.requirements:
        outcome = outcomes[requirement.id]
        if (outcome is RequirementOutcome.VIOLATED
                or (outcome in (RequirementOutcome.PENDING, RequirementOutcome.UNKNOWN)
                    and unknown_policy == "block")
                or (outcome is RequirementOutcome.UNVERIFIED and unverified_policy == "block")):
            blocking.append((requirement, outcome))
    return blocking


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
) -> Tuple[Dict[str, Tuple[RequirementOutcome, str]], List[str]]:
    """The verifier's per-requirement verdicts. Accepts a list of
    {"id", "verdict", "evidence"}; verdict satisfied|missing|unverifiable.
    Unknown ids and unreadable entries are returned as findings and ignored;
    a requirement without a readable verdict stays absent (UNKNOWN)."""
    mapping = {"satisfied": RequirementOutcome.SATISFIED, "missing": RequirementOutcome.VIOLATED,
               "unverifiable": RequirementOutcome.UNVERIFIED}
    verdicts: Dict[str, Tuple[RequirementOutcome, str]] = {}
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
            continue
        outcome = mapping[verdict]
        if outcome is RequirementOutcome.VIOLATED and not names_a_concrete_literal(requirements.get(rid).text):
            findings.append(f"{rid}: missing claim for a requirement naming nothing concrete; unverified")
            outcome = RequirementOutcome.UNVERIFIED
        verdicts[rid] = (outcome, str(entry.get("evidence") or ""))
    return verdicts, findings


def close_unverified_requirements_with_named_tests(
    ledger: ObligationLedger, requirements: RequirementSet, *,
    test_files: Iterable[str], modified: Iterable[str],
    run_tests: Any, confirms_execution: Any, source: str, revision: Any,
) -> List[Dict[str, Any]]:
    """Close each UNVERIFIED requirement whose own text names existing tests,
    by running exactly those tests on the candidate the verifier judged.

    Authoritative only under all of: the binding is the user's words
    (``named_existing_tests``); every named test file is unchanged by the
    candidate (``modified`` - a test the run wrote or edited is the model's
    evidence, not an independent verifier); the run executed tests
    (``confirms_execution(output)``) and passed. ``run_tests(paths)`` returns
    the validator's ``{"success", "output"}``; it runs against the same
    candidate the verdict's ``evidence_id`` identifies (the caller's
    worktree, before anything else changes it). Returns one record per
    attempted closure (closed or not, with why)."""
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
            try:
                result = run_tests(named) or {}
            except Exception as exc:  # a runner failure is no evidence either way
                result = {"success": False, "output": f"runner raised: {exc}"}
            executed = bool(confirms_execution(str(result.get("output", ""))))
            if result.get("success") and executed:
                record_requirement_closure(
                    ledger, requirements, requirement.id, evidence_id=evidence_id, method="named_test_run",
                    detail={"tests": named, "passed": True}, source=source, revision=revision,
                )
                entry["closed"] = True
            else:
                entry["reason"] = ("named tests did not execute" if not executed
                                   else "named tests failed")
        attempts.append(entry)
    return attempts
