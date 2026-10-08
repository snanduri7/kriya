"""VERIFICATION-CONTRACT-003: what kind of claim each derived statement makes.

A goal's statements (``requirements.derive_requirements``) are the user's own
words. Before any model call the contract compiler
(kriya/workflow/contract_compilation.py) asks, for every statement, which
claims it makes - and so which deterministic evidence could ever close it.
This module answers that question structurally, from closed vocabularies and
the statement's own origin in the goal (``requirements.statement_origins``);
no model is involved and nothing here is decided by what Kriya can verify.

Scopes (reconciled with the existing vocabulary, never a parallel one):

- ``BEHAVIOR`` (requirements.BEHAVIOR, strength EXACT/GENERAL from
  ``behavior_strength``): the statement asks for or describes observable
  behaviour; closed by acceptance evidence (B2), an approved suite (B3), the
  goal's own compiled examples (EXACT only) or an external authority.
- ``REGRESSION_PRESERVATION``: names existing tests or is a whole-suite
  preservation statement; closed by the named-test / full-regression oracle.
- ``MUTATION_SCOPE``: "do not modify any other file"; the mutation record.
- ``TEST_IMMUTABILITY``: "do not change existing tests" - as a whole
  statement (existing recognizer) or as one clause of a compound constraint.
- ``API_PRESERVATION``: "the public API / signatures may not change"; a
  static repository predicate (Python AST; Java stays authority-required).
- ``DOCUMENTATION``: "document it in the README (if there is one)"; the
  referent's existence is a deterministic repository fact, its content a claim.
- ``PROCEDURE``: "verify the fix by installing into a fresh virtual
  environment"; only an external authority can execute it.
- ``TEST_ADDITION``: "add a test for it"; the candidate's own structured
  test inventory versus the base.
- ``MIGRATION``: the resolved dependency migration (existing gate).
- ``NON_CLAIM`` (owner decision D1, 2026-10-08, conservative): a heading or
  label ("Reproducer 2:", "Specification:") or a bare code block that carries
  no expected-value marker. Structural only; a descriptive sentence, a
  request, a constraint, an acceptance, preservation or API statement is
  never NON_CLAIM, and a label that also carries a request or an assertion
  keeps its claim.

Compound statements are read conservatively: every recognized clause adds a
claim (closing gets harder, never easier), and whatever prose remains beyond
the recognized preservation/scope vocabulary is a BEHAVIOR claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from kriya.workflow.requirements import (
    _CONSTRAINT_CUE,
    _EXAMPLE_CALL,
    _IMMUTABILITY_VERBS,
    _NEGATIONS,
    _STRIP_CODE_AND_PARENS,
    _SUITE_PRESERVATION_CUES,
    API_PRESERVATION,
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_GENERAL,
    DOCUMENTATION_CLAIM,
    MUTATION_SCOPE,
    ORIGIN_CODE_BLOCK,
    REGRESSION_PRESERVATION,
    TEST_ADDITION_CLAIM,
    TEST_IMMUTABILITY_CLAIM,
    _spans_are_commands,
    behavior_strength,
    is_mutation_scope_requirement,
    is_suite_preservation_requirement,
    is_test_immutability_requirement,
    named_existing_tests,
    names_migration,
    requirement_claims,
    suite_statement_requires_immutability,
)

SCOPE_RECOGNIZER_VERSION = 1

NON_CLAIM = "NON_CLAIM"
SUITE_PRESERVATION_SCOPE = "SUITE_PRESERVATION"
DOCUMENTATION = DOCUMENTATION_CLAIM
PROCEDURE = "PROCEDURE"
TEST_ADDITION = TEST_ADDITION_CLAIM
MIGRATION = "MIGRATION"
# Filler a pure documentation statement may carry beside its clause.
_DOCUMENTATION_FILLER = frozenset({"please", "also", "and", "then", "the", "a", "an", "it", "them", "this", "that",
                                   "these", "those", "accordingly", "too", "as", "well", "if", "there", "is", "one",
                                   "any", "such", "exists", "present", "applicable", "where", "when", "of", "in", "to",
                                   "its", "new", "function", "functions", "entry", "entries", "section", "list"})

NON_CLAIM_LABEL = "label"
NON_CLAIM_CODE_BLOCK = "code_block"

_WORD = re.compile(r"[A-Za-z0-9_'-]+")
# A label is a short noun phrase (at most three words) or a longer one that
# opens with a heading phrase from this closed list (at most eight words);
# "Both cases worked before:" is neither and stays a proposition.
_LABEL_SHORT_WORDS = 3
_LABEL_MAX_WORDS = 8
_LABEL_HEADING_OPENERS = re.compile(
    r"^\s*(?:what we (?:observe|see|get|have)|steps?(?: to reproduce)?|how to reproduce|to reproduce|reproducer|"
    r"reproduction|repro|specification|spec|examples?|example (?:usage|session)|background|context|constraints?|"
    r"notes?|details|observations?|observed behaviou?r|current behaviou?r|desired behaviou?r|proposed behaviou?r|"
    r"proposal|summary|motivation|rationale|environment|versions?|acceptance criteria|definition of done|"
    r"out of scope|non-goals?|goals?)\b",
    re.IGNORECASE,
)
# A label never carries a request, an obligation, a quantifier or an
# assertion verb; any of these keeps the statement a claim (conservative).
_LABEL_FORBIDDEN_WORDS = frozenset({
    "please", "must", "mustn't", "should", "shouldn't", "shall", "fix", "fixed", "add", "adds", "make", "makes",
    "implement", "ensure", "update", "change", "changes", "changed", "remove", "removes", "replace", "support",
    "supports", "return", "returns", "returned", "raise", "raises", "throw", "throws", "expected", "actual",
    "always", "never", "all", "any", "every", "each", "no", "not", "don't", "do", "keep", "keeps", "preserve",
    "unchanged", "fails", "fail", "failing", "pass", "passes", "passing", "works", "work", "broken", "wrong",
    "correct", "correctly", "incorrect", "incorrectly", "is", "are", "was", "were", "be", "has", "have", "does",
    "create", "run", "install", "open", "use", "set", "call", "start", "then", "delete", "write", "edit",
    "modify", "verify", "check", "test", "restore", "handle", "resolve", "skip", "produce", "produces",
    # review VC3-R6: constraint adjectives written as headings are claims ("Zero regressions:", "Thread safety:")
    "zero", "same", "identical", "compatible", "compatibility", "only", "safe", "safety", "idempotent", "constant",
    "thread", "regressions", "regression", "backward", "backwards", "deterministic", "stable", "immutable",
})
_LABEL_FORBIDDEN_MARKS = ("=", "->", "=>", "?", "!")
# Inside a code block, any of these says the author stated an expected value
# or result: the block is then a claim, not scaffolding.
# A print/output call inside a snippet is code, not an expectation; the
# expectation words are the ones an author writes beside a value. Review
# VC3-R3: any ``#`` comment, an ``==`` comparison and the result words
# (correct/wrong/got/but) are expectation markers too - a reproducer whose
# only stated expectation is a comment is a claim, never scaffolding. A
# ``print(...)`` CALL is code (the comment word "prints" is covered by ``#``).
_EXPECTED_VALUE_MARKER = re.compile(
    r"->|=>|==|#\s*\S|\bexpected\b|\bexpect\b|\bexpects\b|\bshould\b|\breturns?\b|\bactual\b|"
    r"\bassert\w*\b|\bfails?\b|\bpasses\b|\braises?\b|\bthrows?\b|\bcorrect\b|\bwrong\b|\bgot\b|\bbut\b",
    re.IGNORECASE,
)
_API_NOUNS = re.compile(
    r"\bpublic\s+(?:api|interface|signatures?|methods?|classes|class|constructors?|fields?)\b|"
    r"\bprotected\s+(?:signatures?|methods?|members?)\b|\bsignatures?\b|\bpublic api\b|"
    r"\b(?:binary|source)[- ]compatib\w+\b|\bclass hierarch\w+\b",
    re.IGNORECASE,
)
_API_PRESERVATION_CUE = re.compile(
    r"\b(?:may|must|should|shall|can)\s+not\s+(?:be\s+)?(?:change|changed|altered|removed|renamed|modified|broken)\b|"
    r"\bdo not\s+(?:change|alter|modify|break|remove|rename)\b|\bdon't\s+(?:change|alter|modify|break)\b|"
    r"\bno\s+[^.;]{0,80}\b(?:may|must)\s+(?:be\s+)?(?:change|changed|added|removed|renamed)\b|"
    r"\b(?:stay|stays|remain|remains|kept|keep)\s+(?:exactly\s+)?(?:unchanged|intact|as (?:it|they) (?:is|are|was|were)|compatible)\b|"
    r"\b(?:unchanged|intact|preserved?|backwards?[- ]compatible)\b",
    re.IGNORECASE,
)
# The referent follows a documentation verb within one clause ("document
# them in the README's function list", "update CHANGELOG.md", "mention it in
# the documentation"); a bare "doc"/"docs" token is an identifier as often
# as a referent (Java's ``Document doc``), so only "the docs"/"the
# documentation" forms count.
_DOCUMENTATION_REFERENT = (
    r"(?P<name>readme(?:\.\w+)?|changelog(?:\.\w+)?|changes(?:\.\w+)?|contributing(?:\.\w+)?|"
    r"[\w./-]+\.(?:md|rst|txt|adoc))\b|(?P<word>(?:the|its|our|project)\s+(?:docs|documentation)|javadoc|docstrings?)\b"
)
_DOCUMENTATION_CLAUSE = re.compile(
    r"\b(?:document|documents|documented|mention|mentions|mentioned|describe|describes|described|update|updates|"
    r"updated|add|adds|added|list|lists|listed|note|notes|noted|record|records|recorded)\b[^.;]{0,60}?\b(?:"
    + _DOCUMENTATION_REFERENT + ")",
    re.IGNORECASE,
)
_DOCUMENTATION_CONDITIONAL = re.compile(
    r"\bif there is (?:one|such a \w+|any)\b|\bif (?:it|one|such a \w+|the \w+) exists?\b|\bif present\b|"
    r"\bif (?:any|applicable)\b|\bwhere applicable\b|\bwhen present\b",
    re.IGNORECASE,
)
_DOCUMENTATION_LIST_NOUN = re.compile(r"(?P<noun>(?<![\w'])[a-z]{2,}(?:\s+[a-z]{2,})?)\s+list\b", re.IGNORECASE)
_PROCEDURE_HEAD = re.compile(
    r"^\s*(?:steps?|to reproduce|reproduce|reproduction|repro|how to reproduce|procedure)\s*:", re.IGNORECASE)
_PROCEDURE_VERIFY = re.compile(
    r"^\s*(?:verify|test|check|validate|confirm|reproduce)\b[^.;]{0,160}\bby\b", re.IGNORECASE)
_TEST_ADDITION = re.compile(
    r"\b(?:add|adds|adding|write|writes|writing|create|creates|creating|include|includes|including|cover|covers)\b"
    r"[^.;]{0,40}?\b(?:(?:unit|regression|integration|acceptance|new)\s+)?tests?(?:\s+cases?)?\b",
    re.IGNORECASE,
)
_TEST_ADDITION_NEGATED = re.compile(
    r"\b(?:do not|don't|never|without|no need to|must not|should not)\b[^.;]{0,30}\b(?:add|write|create|include)\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"[;:,]|\b(?:and|or|but|while|then|so that)\b", re.IGNORECASE)
_EXISTING_TESTS = re.compile(r"\b(?:existing|current|pre-existing|present)\s+(?:unit\s+)?tests?\b|\btest suite\b",
                             re.IGNORECASE)
_SUITE_CLAUSE = re.compile(
    r"\b(?:every|all|each|the)\s+(?:existing|current|pre-existing)\s+(?:unit\s+)?tests?\b|"
    r"\b(?:the\s+)?(?:existing\s+)?(?:test\s+)?suite\b", re.IGNORECASE)
# Review VC3-R1: the leftover of a compound constraint is computed against the
# RECOGNIZED CLAUSE SPANS (the API noun and cue, the suite noun and cues, the
# negated change verb and its "existing tests" object), never against a
# vocabulary allow-list: a second clause the recognizers did not match ("do
# not break the tests", "do not delete tests") keeps the statement's BEHAVIOR
# claim and the goal stays authority-required. Only connective filler may
# remain once the spans are removed.
_PURE_FILLER = frozenset({
    "constraint", "constraints", "hard", "please", "also", "and", "or", "the", "a", "an", "of", "to", "in", "as",
    "its", "their", "this", "that", "these", "those", "both", "must", "should", "shall", "may", "be", "is", "are",
    "every", "all", "each", "any", "existing", "current", "keep", "keeps", "kept", "stay", "stays", "remain",
    "remains", "still", "so", "too", "well",
})
_NEGATED_CHANGE_VERB = re.compile(
    rf"\b(?:{'|'.join(re.escape(n) for n in sorted(_NEGATIONS))})\b[^.;]{{0,40}}?{_IMMUTABILITY_VERBS}", re.IGNORECASE)
# Filler a pure test-addition statement may carry beside its clause.
_TEST_ADDITION_FILLER = frozenset({"please", "also", "and", "for", "it", "this", "that", "the", "a", "an", "new",
                                   "regression", "unit", "integration", "acceptance", "case", "cases", "covering",
                                   "cover", "covers", "to", "of", "them", "these", "those", "fix", "change", "behaviour",
                                   "behavior", "too", "as", "well"})


@dataclass(frozen=True)
class StatementScope:
    """The claims one statement makes, and why."""

    requirement_id: str
    text: str
    origin: str
    claims: Tuple[str, ...]  # closure claims: BEHAVIOR / REGRESSION_PRESERVATION / API_PRESERVATION / TEST_IMMUTABILITY
    scopes: Tuple[str, ...]  # every recognized scope, in the order above plus the structural ones
    strength: Optional[str] = None  # BEHAVIOR_EXACT / BEHAVIOR_GENERAL when BEHAVIOR is claimed
    strength_reasons: Tuple[str, ...] = ()
    non_claim_kind: Optional[str] = None  # NON_CLAIM_LABEL / NON_CLAIM_CODE_BLOCK
    non_claim_reason: Optional[str] = None
    named_tests: Tuple[str, ...] = ()
    documentation: Optional[Dict[str, object]] = None  # referent, conditional, list noun
    detail: Dict[str, object] = field(default_factory=dict)

    @property
    def is_non_claim(self) -> bool:
        return self.non_claim_kind is not None

    def to_dict(self) -> Dict[str, object]:
        return {
            "requirement_id": self.requirement_id, "text": self.text, "origin": self.origin,
            "claims": list(self.claims), "scopes": list(self.scopes), "strength": self.strength,
            "strength_reasons": list(self.strength_reasons), "non_claim_kind": self.non_claim_kind,
            "non_claim_reason": self.non_claim_reason, "named_tests": list(self.named_tests),
            "documentation": dict(self.documentation) if self.documentation else None,
            "recognizer_version": SCOPE_RECOGNIZER_VERSION, **self.detail,
        }


# ------------------------------------------------------------ NON_CLAIM (D1)

def label_non_claim_reason(text: str) -> Optional[str]:
    """Why ``text`` is a bare label (a heading), or None when it is not:
    ends with ":", at most ``_LABEL_MAX_WORDS`` words, no request/obligation/
    quantifier/assertion word, no example call, no "=", "->", "?" or "!"."""
    stripped = (text or "").strip()
    if not stripped.endswith(":"):
        return None
    body = stripped[:-1]
    if any(mark in body for mark in _LABEL_FORBIDDEN_MARKS):
        return None
    words = _WORD.findall(body)
    if not words or len(words) > _LABEL_MAX_WORDS:
        return None
    if len(words) > _LABEL_SHORT_WORDS and (not _LABEL_HEADING_OPENERS.match(body) or "," in body):
        return None
    lowered = {word.lower() for word in words}
    if lowered & _LABEL_FORBIDDEN_WORDS or _CONSTRAINT_CUE.search(body) or _EXAMPLE_CALL.search(body):
        return None
    return f"a label of {len(words)} word(s) ending in ':' with no request, obligation or assertion"


def code_block_non_claim_reason(text: str, origin: str) -> Optional[str]:
    """Why ``text`` is a bare reproducer/snippet block, or None: its origin is
    a paragraph of indented lines only, and it carries no expected-value
    marker (an arrow, "expected", "returns", "should", an assertion ...)."""
    if origin != ORIGIN_CODE_BLOCK:
        return None
    if _EXPECTED_VALUE_MARKER.search(text or ""):
        return None
    return "an indented code block stating no expected value or result"


# ------------------------------------------------------------ clause recognizers

def _prose(text: str) -> str:
    """The statement without code spans, quotes and parentheticals: the words
    a clause recognizer reads (content inside them is never a cue)."""
    return _STRIP_CODE_AND_PARENS.sub(" ", text or "")


def has_api_preservation_clause(text: str) -> bool:
    """An API/signature noun under a preservation or no-change constraint
    ("no public or protected signature may change", "Do not change the
    public API", "keep the public interface unchanged")."""
    prose = _prose(text)
    return bool(_API_NOUNS.search(prose) and _API_PRESERVATION_CUE.search(prose))


def has_test_immutability_clause(text: str) -> bool:
    """A negated change verb applied to existing tests anywhere in the
    statement ("Do not change the public API ... or existing tests"): the
    whole-statement recognizer's clause form. Never weakens anything - it
    only adds a claim the mutation record must close."""
    prose = _prose(text).lower()
    if is_test_immutability_requirement(text):
        return True
    negated = re.search(rf"\b(?:{'|'.join(re.escape(n) for n in sorted(_NEGATIONS))})\b[^.;]{{0,40}}?{_IMMUTABILITY_VERBS}",
                        prose)
    return bool(negated and _EXISTING_TESTS.search(prose[negated.start():]))


def has_suite_preservation_clause(text: str) -> bool:
    """A whole-suite preservation clause inside a compound constraint
    ("every existing public API and every existing test must keep passing
    unchanged"): the existing-tests / suite noun with a preservation cue in
    the same statement. Adds a REGRESSION_PRESERVATION claim the full suite
    must close; never removes one."""
    prose = _prose(text).lower()
    if is_suite_preservation_requirement(text):
        return True
    return bool(_SUITE_CLAUSE.search(prose) and any(re.search(rf"\b{cue}\b", prose) for cue in _SUITE_PRESERVATION_CUES))


def constraint_without_behaviour_leftover(text: str) -> bool:
    """Whether a compound constraint is made only of its recognized clauses:
    once the spans the recognizers matched (API noun + preservation cue,
    suite noun + preservation cue, negated change verb + existing-tests
    object) are removed from the prose, only connective filler remains. A
    second clause nothing recognized ("do not break the tests") keeps the
    statement's BEHAVIOR claim: closing never gets easier by accident. A
    quoted, parenthesized or fenced span that is not a command or a path is
    prose (the final-review rule of REQUIREMENT-CLOSURE-PLAIN-GOAL-001): a
    request hidden in it keeps the behaviour claim too."""
    if not _spans_are_commands(text):
        return False
    prose = _prose(text)
    spans = []
    for pattern in (_API_NOUNS, _API_PRESERVATION_CUE, _SUITE_CLAUSE, _NEGATED_CHANGE_VERB, _EXISTING_TESTS):
        spans += [m.span() for m in pattern.finditer(prose)]
    lowered = prose.lower()
    for cue in _SUITE_PRESERVATION_CUES:
        spans += [m.span() for m in re.finditer(rf"\b{re.escape(cue)}\b", lowered)]
    kept = [True] * len(prose)
    for start, end in spans:
        for index in range(start, end):
            kept[index] = False
    remainder = "".join(ch if keep else " " for ch, keep in zip(prose, kept, strict=True))
    words = {word for word in (raw.lower().strip("'") for raw in _WORD.findall(remainder)) if len(word) > 1}
    return not words - _PURE_FILLER


def test_addition_only(text: str) -> bool:
    """Whether ``text`` asks for nothing beyond adding a test ("Add a
    regression test for it."): the words outside the clause are filler, and
    no quoted or parenthesized prose hides a further request."""
    if not _spans_are_commands(text):
        return False
    prose = _prose(text)
    clause = _TEST_ADDITION.search(prose)
    if clause is None:
        return False
    rest = prose[:clause.start()] + " " + prose[clause.end():]
    words = {word for word in (raw.lower().strip("'") for raw in _WORD.findall(rest)) if len(word) > 1}
    return not words - _TEST_ADDITION_FILLER


def documentation_clause(text: str) -> Optional[Dict[str, object]]:
    """The documentation claim of ``text`` ("document them in the README's
    function list if there is one"): the referent (a README/CHANGELOG/doc
    file or the word "docs"), whether the clause is conditional on the
    referent's existence, and the list noun before "list" when one is
    named. None when the statement names no documentation referent with a
    documentation verb."""
    prose = _prose(text)
    clause = _DOCUMENTATION_CLAUSE.search(prose)
    if clause is None:
        return None
    name = clause.group("name") or clause.group("word")
    # Review VC3-R4: the conditional belongs to the documentation clause only
    # when it follows it within the same sentence segment - never a condition
    # stated earlier for another clause ("... if any exist, and document that").
    segment_end = re.search(r"[.;]", prose[clause.end():])
    segment = prose[clause.start():clause.end() + (segment_end.start() if segment_end else len(prose))]
    noun = _DOCUMENTATION_LIST_NOUN.search(segment)
    return {"referent": name, "conditional": bool(_DOCUMENTATION_CONDITIONAL.search(segment)),
            "list_noun": noun.group("noun").lower() if noun else None, "clause": clause.group(0)}


def documentation_only(text: str, clause: Dict[str, object]) -> bool:
    """Whether ``text`` asks for nothing beyond its documentation clause (the
    words outside the clause are filler): then the statement's only claim is
    DOCUMENTATION. "Add the new functions ... and document them in the README"
    is compound; "Document them in the README's function list if there is
    one." is pure. Quoted or parenthesized prose keeps the behaviour claim."""
    if not _spans_are_commands(text):
        return False
    prose = _prose(text).replace(str(clause.get("clause") or ""), " ")
    prose = _DOCUMENTATION_CONDITIONAL.sub(" ", prose)
    noun = str(clause.get("list_noun") or "")
    if noun:
        prose = re.sub(rf"\b{re.escape(noun)}\s+list\b", " ", prose, flags=re.IGNORECASE)
    words = {word for word in (raw.lower().strip("'") for raw in _WORD.findall(prose)) if len(word) > 1}
    return not (words - _DOCUMENTATION_FILLER)


def is_procedure_statement(text: str) -> bool:
    """A reproduction or verification procedure ("Steps: ...", "Verify the
    fix by installing into a fresh virtual environment")."""
    prose = _prose(text)
    return bool(_PROCEDURE_HEAD.match(prose) or _PROCEDURE_VERIFY.match(prose))


def has_test_addition_clause(text: str) -> bool:
    """"add a test for it", "write regression tests" - not when negated."""
    prose = _prose(text)
    return bool(_TEST_ADDITION.search(prose)) and not _TEST_ADDITION_NEGATED.search(prose)


# ------------------------------------------------------------ the statement's scope

def statement_scope(
    requirement_id: str, text: str, *, origin: str, test_files: Iterable[str],
    migration_identities: Iterable[Tuple[str, str]] = (),
) -> StatementScope:
    """Every claim ``text`` makes, decided from its own words and origin.

    Order of decision (each step is a closed recognizer): structural
    NON_CLAIM; whole-statement mutation scope; whole-statement test
    immutability; whole-suite preservation; the migration; then the clause
    recognizers (API preservation, test immutability, documentation,
    procedure, test addition) and finally FS-1C1's claims from the named
    tests and the remaining prose (BEHAVIOR with its B2-COV strength)."""
    files = list(test_files)
    migrations = [tuple(pair) for pair in migration_identities]
    label = label_non_claim_reason(text)
    block = code_block_non_claim_reason(text, origin)
    if label or block:
        kind = NON_CLAIM_LABEL if label else NON_CLAIM_CODE_BLOCK
        return StatementScope(requirement_id, text, origin, claims=(), scopes=(NON_CLAIM,),
                              non_claim_kind=kind, non_claim_reason=label or block)
    if is_mutation_scope_requirement(text):
        return StatementScope(requirement_id, text, origin, claims=(), scopes=(MUTATION_SCOPE,))
    if is_test_immutability_requirement(text):
        return StatementScope(requirement_id, text, origin, claims=(TEST_IMMUTABILITY_CLAIM,),
                              scopes=(TEST_IMMUTABILITY_CLAIM,))
    named = tuple(named_existing_tests(text, files))
    if not named and is_suite_preservation_requirement(text):
        scopes: List[str] = [SUITE_PRESERVATION_SCOPE]
        claims: List[str] = [REGRESSION_PRESERVATION]
        if suite_statement_requires_immutability(text):
            scopes.append(TEST_IMMUTABILITY_CLAIM)
            claims.append(TEST_IMMUTABILITY_CLAIM)
        return StatementScope(requirement_id, text, origin, claims=tuple(claims), scopes=tuple(scopes))
    if migrations and names_migration(text, migrations):
        return StatementScope(requirement_id, text, origin, claims=(), scopes=(MIGRATION,),
                              detail={"compound": bool(_CLAUSE_SPLIT.search(_prose(text)))})
    scopes = []
    claims = []
    detail: Dict[str, object] = {}
    clause_only = False
    if has_api_preservation_clause(text):
        scopes.append(API_PRESERVATION)
        claims.append(API_PRESERVATION)
    if has_test_immutability_clause(text):
        scopes.append(TEST_IMMUTABILITY_CLAIM)
        claims.append(TEST_IMMUTABILITY_CLAIM)
    if not named and has_suite_preservation_clause(text):
        scopes.append(SUITE_PRESERVATION_SCOPE)
        claims.append(REGRESSION_PRESERVATION)
        if TEST_IMMUTABILITY_CLAIM not in claims and suite_statement_requires_immutability(text):
            scopes.append(TEST_IMMUTABILITY_CLAIM)
            claims.append(TEST_IMMUTABILITY_CLAIM)
    if claims and constraint_without_behaviour_leftover(text):
        clause_only = True  # a constraint made only of recognized clauses asks for no behaviour
    documentation = documentation_clause(text)
    if documentation is not None:
        scopes.append(DOCUMENTATION)
        claims.append(DOCUMENTATION_CLAIM)
    if is_procedure_statement(text):
        scopes.append(PROCEDURE)
    addition_only = False
    if has_test_addition_clause(text):
        scopes.append(TEST_ADDITION)
        claims.append(TEST_ADDITION_CLAIM)
        addition_only = test_addition_only(text)
    base_claims = requirement_claims(text, named)
    if clause_only or addition_only or (documentation is not None and documentation_only(text, documentation)):
        # A pure documentation statement or a clause-only constraint asks
        # for nothing beyond its recognized clauses.
        base_claims = tuple(claim for claim in base_claims if claim != BEHAVIOR)
    if REGRESSION_PRESERVATION in base_claims and REGRESSION_PRESERVATION not in claims:
        scopes.append(REGRESSION_PRESERVATION)
        claims.append(REGRESSION_PRESERVATION)
    strength: Optional[str] = None
    reasons: Tuple[str, ...] = ()
    if BEHAVIOR in base_claims:
        # The prose beyond the recognized clauses asks for or describes
        # behaviour: a BEHAVIOR claim with its B2-COV strength. A statement
        # that is only a documentation or procedure clause still claims the
        # behaviour it names (the content / the procedure's outcome).
        scopes.append(BEHAVIOR)
        claims.append(BEHAVIOR)
        strength, why = behavior_strength(text, regression_covered=REGRESSION_PRESERVATION in base_claims)
        reasons = tuple(why["reasons"])
        detail["examples"] = list(why["examples"])
    return StatementScope(requirement_id, text, origin, claims=tuple(claims), scopes=tuple(scopes),
                          strength=strength, strength_reasons=reasons, named_tests=named,
                          documentation=documentation, detail=detail)


def behavior_strength_label(scope: StatementScope) -> Optional[str]:
    """"BEHAVIOR_EXACT" / "BEHAVIOR_GENERAL" for a statement claiming behaviour."""
    if scope.strength is None:
        return None
    return "BEHAVIOR_EXACT" if scope.strength == BEHAVIOR_EXACT else (
        "BEHAVIOR_GENERAL" if scope.strength == BEHAVIOR_GENERAL else scope.strength)


# ------------------------------------------------------------ documentation subjects (BACKEND-READINESS-004, owner decision 2)
#
# "Document them in the README's function list": the subjects are the things the goal asks to add. They are read from
# the statement itself when it names them (backticked or call-formed identifiers before the referent) and otherwise from
# the goal's own addition statements - "Add three built-in string functions ...: lower, upper and trim" - whose noun
# matches the list noun ("function list" <-> "functions"). Pure text, no model, no repository; an undeterminable
# subject set leaves the clause a content claim (authority required), never a guess.
_ADDITION_VERB = re.compile(
    r"\b(?:add|adds|added|adding|introduce|introduces|introduced|implement|implements|implemented|provide|provides|"
    r"provided|create|creates|created|expose|exposes|exposed|define|defines|defined)\b", re.IGNORECASE)
_SUBJECT_PRONOUN = re.compile(r"\b(?:them|these|those|it|the new \w+|the added \w+|each of them|all of them)\b", re.IGNORECASE)
_DOCUMENTATION_VERB = re.compile(
    r"\b(?:document|documents|documented|mention|mentions|mentioned|describe|describes|described|update|updates|"
    r"updated|add|adds|added|list|lists|listed|note|notes|noted|record|records|recorded)\b", re.IGNORECASE)
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_CODE_SPAN_IDENTIFIER = re.compile(r"`(" + _IDENTIFIER + r")(?:\(\))?`")
_CALL_IDENTIFIER = re.compile(r"\b(" + _IDENTIFIER + r")\(\)")
_IDENTIFIER_LIST = re.compile(
    r"(?P<list>" + _IDENTIFIER + r"(?:\(\))?(?:\s*(?:,|and|or|,\s*and|,\s*or)\s*" + _IDENTIFIER + r"(?:\(\))?)*)")
_LIST_SEPARATOR = re.compile(r"\s*(?:,\s*(?:and|or)\s+|,\s*|\s+(?:and|or)\s+)")
_SUBJECT_STOP_WORDS = frozenset({"the", "a", "an", "to", "in", "of", "for", "new", "and", "or", "that", "which", "so",
                                 "it", "them", "these", "those", "same", "as", "existing", "style", "with", "all",
                                 "each", "every", "its", "their", "this", "is", "are", "be", "must", "should"})


def _plural_family(noun: str) -> str:
    noun = noun.lower().strip()
    for suffix in ("ies", "es", "s"):
        if noun.endswith(suffix) and len(noun) > len(suffix) + 2:
            return noun[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return noun


def _identifiers(fragment: str) -> List[str]:
    found: List[str] = []
    for token in _LIST_SEPARATOR.split(fragment):
        name = token.strip().rstrip("()").strip("`'\"")
        if re.fullmatch(_IDENTIFIER, name) and name.lower() not in _SUBJECT_STOP_WORDS and name not in found:
            found.append(name)
    return found


def documentation_subjects(text: str, clause: Mapping[str, object], other_statements: Iterable[Tuple[str, str]],
                           ) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(subjects, source requirement ids) of a documentation clause: the
    identifiers the statement itself names before the referent (code spans
    or ``name()`` forms), else - when the clause refers to "them"/"the new
    <noun>" - the identifier list of every other statement that adds things
    of the list noun's family ("functions" for a "function list"), written as
    "<verb> ... <noun>[:] a, b and c" or with code spans. Empty when no
    deterministic subject exists."""
    raw = text or ""
    own = str(clause.get("clause") or "")
    # The clause text is prose (code spans stripped): read explicit identifiers from the RAW statement, in the
    # window between the last documentation verb and the referent.
    referent = re.search(_DOCUMENTATION_REFERENT, raw, re.IGNORECASE)
    window = raw[: referent.start()] if referent else raw
    verbs = list(_DOCUMENTATION_VERB.finditer(window))
    window = window[verbs[-1].end():] if verbs else window
    named = _CODE_SPAN_IDENTIFIER.findall(window) + _CALL_IDENTIFIER.findall(window)
    if named:
        return tuple(dict.fromkeys(named)), ()
    if not _SUBJECT_PRONOUN.search(own) and not _SUBJECT_PRONOUN.search(_prose(raw)):
        return (), ()
    family = _plural_family(str(clause.get("list_noun") or ""))
    if not family:
        return (), ()
    subjects: List[str] = []
    sources: List[str] = []
    for rid, statement in other_statements:
        if not _ADDITION_VERB.search(statement):
            continue
        spans = _CODE_SPAN_IDENTIFIER.findall(statement)
        nouns = [m for m in re.finditer(r"\b(" + _IDENTIFIER + r")\b", statement) if _plural_family(m.group(1)) == family]
        if not nouns:
            continue
        found: List[str] = []
        if spans:
            found = [name for name in spans if name.lower() not in _SUBJECT_STOP_WORDS]
        else:
            after = statement[nouns[0].end():]
            colon = after.find(":")
            fragment = after[colon + 1:] if 0 <= colon < 80 else after
            match = _IDENTIFIER_LIST.search(fragment)
            if match is not None and fragment[: match.start()].strip(" \t") == "":
                found = _identifiers(match.group("list"))
        for name in found:
            if name not in subjects:
                subjects.append(name)
        if found and rid not in sources:
            sources.append(rid)
    return tuple(subjects), tuple(sources)
