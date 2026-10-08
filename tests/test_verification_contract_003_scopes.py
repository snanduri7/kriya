"""VERIFICATION-CONTRACT-003 slice 1: claim scopes, structural NON_CLAIM (owner decision D1), the contract compiler
and the admission taxonomy (D3).

Every recognizer here is structural and closed-vocabulary: no model, nothing decided by what Kriya can verify. The
negative tests are the owner's required proof that a descriptive behaviour sentence, a request, a constraint, an
acceptance statement, a preservation requirement and an API requirement can never be NON_CLAIM.
"""
import json

import pytest

from kriya.workflow import requirement_scopes as rs
from kriya.workflow import requirements as r
from kriya.workflow.contract_compilation import (
    CLOSER_API_PRESERVATION,
    CLOSER_CONTRACTS,
    CLOSER_DERIVED_EXAMPLES,
    CLOSER_DOCUMENTATION_NOT_APPLICABLE,
    CLOSER_EXTERNAL_ACCEPTANCE,
    CLOSER_NOT_A_CLAIM,
    CLOSER_TEST_ADDITION,
    STATUS_AMBIGUOUS,
    STATUS_AUTHORITY_REQUIRED,
    STATUS_CLOSABLE,
    STATUS_NOT_A_CLAIM,
    VERIFICATION_CONTRACT_EVENT_KINDS,
    ExternalAuthority,
    compile_verification_contract,
    record_contract_non_claims,
    seal_verification_contract,
)
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_GENERAL,
    GOAL_INSUFFICIENT_FOR_VERIFICATION,
    ORIGIN_CODE_BLOCK,
    ORIGIN_LIST_ITEM,
    ORIGIN_MIXED,
    ORIGIN_SENTENCE,
    REGRESSION_PRESERVATION,
    SUITE_PRESERVATION,
    TEST_IMMUTABILITY,
    VERIFICATION_AUTHORITY_REQUIRED,
    GoalAdmissionError,
    RequirementOutcome,
    VerificationAuthorityRequired,
    admission_gap,
    blocking_requirements,
    derive_requirements,
    deterministic_closers,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)

T1_GOAL = """TTLCache keeps entries alive one tick too long at the expiry boundary.

Reproducer 1 (Timer is a manual counter: calling it returns .time, tick() adds 1):

    cache = TTLCache(maxsize=3, ttl=3, timer=Timer())
    cache.expire(3)         # returns []        expected: [(1, 1)]

Reproducer 2:

    cache = TTLCache(maxsize=2, ttl=2, timer=Timer())
    cache[1] = 1            # t=0

Please fix the expiry logic so that an entry whose expiry time has been reached is expired everywhere.
Do not change the public API, the TTLCache constructor, or existing tests; keep the behaviour of all other cache classes unchanged.
"""


def _scope(text, origin=ORIGIN_SENTENCE, tests=()):
    return rs.statement_scope("REQ-1", text, origin=origin, test_files=list(tests))


# ---------------------------------------------------------------- origins: the segmenter is the one producer
def test_01_statement_origins_align_with_derivation_and_leave_the_set_unchanged():
    reqs = derive_requirements(T1_GOAL)
    origins = statement_origins(T1_GOAL)
    assert list(origins) == reqs.ids
    assert origins["REQ-2"] == ORIGIN_SENTENCE and origins["REQ-3"] == ORIGIN_CODE_BLOCK
    assert origins["REQ-4"] == ORIGIN_SENTENCE and origins["REQ-5"] == ORIGIN_CODE_BLOCK
    assert reqs.get("REQ-4").text == "Reproducer 2:"
    # the derivation itself is byte-identical to the pre-003 segmenter: same texts, same version, same digest shape
    assert reqs.version == 1 and reqs.digest == derive_requirements(T1_GOAL).digest
    assert statement_origins("Examples:\n  f(1) -> 2\n")["REQ-1"] == ORIGIN_MIXED
    assert statement_origins("- do this\n- do that\n")["REQ-2"] == ORIGIN_LIST_ITEM
    assert statement_origins("", ("clarified",)) == {"REQ-C1": "clarification"}


# ---------------------------------------------------------------- NON_CLAIM: structural only (D1)
@pytest.mark.parametrize("text", ["Reproducer 2:", "Specification:", "What we observe with the current build:",
                                  "Examples:", "Steps to reproduce:", "Background:"])
def test_02_labels_are_non_claims(text):
    scope = _scope(text)
    assert scope.is_non_claim and scope.non_claim_kind == rs.NON_CLAIM_LABEL and scope.claims == ()


def test_03_a_bare_code_block_without_an_expected_value_is_a_non_claim_but_one_with_a_marker_is_not():
    bare = _scope("cache = TTLCache(maxsize=2, ttl=2, timer=Timer()) cache[1] = 1 cache.timer.tick()", origin=ORIGIN_CODE_BLOCK)
    assert bare.is_non_claim and bare.non_claim_kind == rs.NON_CLAIM_CODE_BLOCK
    marked = _scope("cache.expire(3) # returns [] expected: [(1, 1)]", origin=ORIGIN_CODE_BLOCK)
    assert not marked.is_non_claim and BEHAVIOR in marked.claims
    # review VC3-R3: any comment is an expectation marker (an author states values in comments)
    assert not _scope("cache[1] = 1 # t=0", origin=ORIGIN_CODE_BLOCK).is_non_claim
    # the same bytes as a prose sentence (origin) are never a code-block non-claim
    assert not _scope("cache = TTLCache(maxsize=2, ttl=2, timer=Timer()) cache[1] = 1 cache.timer.tick()").is_non_claim
    assert _scope('python -c "from slugify import slugify; print(slugify(\'x\'))"', origin=ORIGIN_CODE_BLOCK).is_non_claim


@pytest.mark.parametrize("text", [
    "TTLCache keeps entries alive one tick too long at the expiry boundary.",   # descriptive behaviour sentence
    "Parser must preserve escaped Unicode.",                                    # mandatory behaviour requirement
    "Please fix the expiry logic so that an expired entry is gone everywhere.",  # request
    "Do not change the public API.",                                            # constraint / API
    "Every existing test must keep passing unchanged.",                         # preservation
    "Do not modify any other file.",                                            # scope
    "lower('ABC') -> 'abc'",                                                    # acceptance / example
    "Expected: cafe Actual: ModuleNotFoundError",                               # expectation
    "Reproducer 1 (Timer is a manual counter: calling it returns .time, tick() adds 1):",  # label + assertion words
    "The fix must be behaviour-only.", "Results are plain Python str values.", "Both cases worked before:",
    "All tests:", "Fix:", "Expected:", "Should return 3:", "x = 1:",
])
def test_04_propositions_requests_constraints_and_assertions_are_never_non_claims(text):
    for origin in (ORIGIN_SENTENCE, ORIGIN_LIST_ITEM, ORIGIN_MIXED):
        assert not _scope(text, origin=origin).is_non_claim, text
    assert rs.label_non_claim_reason(text) is None


def test_05_a_label_and_a_requirement_in_one_statement_keep_the_requirement():
    scope = _scope("Constraint: do not change any existing test.")
    assert not scope.is_non_claim and scope.claims == (r.TEST_IMMUTABILITY_CLAIM,)


# ---------------------------------------------------------------- clause scopes
def test_06_compound_constraints_make_every_recognized_claim_and_keep_behaviour_when_prose_remains():
    compound = _scope("Do not change the public API, the TTLCache constructor, or existing tests; keep the behaviour of "
                      "all other cache classes unchanged.")
    assert compound.claims == (API_PRESERVATION, r.TEST_IMMUTABILITY_CLAIM, BEHAVIOR) and compound.strength == BEHAVIOR_GENERAL
    clause_only = _scope("Constraints: every existing public API and every existing test must keep passing unchanged.")
    assert set(clause_only.claims) == {API_PRESERVATION, REGRESSION_PRESERVATION, r.TEST_IMMUTABILITY_CLAIM}
    assert BEHAVIOR not in clause_only.claims and rs.SUITE_PRESERVATION_SCOPE in clause_only.scopes
    # a prose parenthetical is not a command or path: it may hide a request, so the behaviour claim stays (the
    # final-review rule of REQUIREMENT-CLOSURE-PLAIN-GOAL-001, kept for every clause-only decision)
    parenthetical = _scope("Constraints: every existing public API and every existing test (including the JSON compliance "
                           "suite under tests/) must keep passing unchanged.")
    assert BEHAVIOR in parenthetical.claims and API_PRESERVATION in parenthetical.claims
    hidden = _scope('Every existing test must keep passing "once TTLCache.expire drops entries whose expiry time has been reached".')
    assert BEHAVIOR in hidden.claims and REGRESSION_PRESERVATION in hidden.claims
    assert _scope("Add a regression test for it (and delete the old ones).").claims == (r.TEST_ADDITION_CLAIM, BEHAVIOR)
    # one foreign word keeps the behaviour claim (closing never gets easier by accident)
    foreign = _scope("Every existing public API and every existing test must keep passing unchanged and log a warning.")
    assert BEHAVIOR in foreign.claims
    assert _scope("The public API must stay unchanged.").claims == (API_PRESERVATION,)
    assert not rs.has_api_preservation_clause("Add a public API for lower().")  # no preservation cue


def test_07_documentation_procedure_and_test_addition_clauses():
    pure = _scope("Document them in the README's function list if there is one.")
    assert pure.claims == (r.DOCUMENTATION_CLAIM,) and pure.documentation["conditional"] is True
    assert pure.documentation["referent"] == "README" and pure.documentation["list_noun"] == "function"
    compound = _scope("Add the new functions in the same style as the existing built-ins and document them in the "
                      "README's function list if there is one.")
    assert compound.claims == (r.DOCUMENTATION_CLAIM, BEHAVIOR)
    assert rs.documentation_clause('Document doc = Jsoup.parse("x") doc.absUrl("href") returns "a".') is None  # an identifier
    assert rs.documentation_clause("Mention it in the documentation.")["referent"] == "the documentation"
    procedure = _scope("Verify the fix by installing into a fresh virtual environment, not by hand.")
    assert rs.PROCEDURE in procedure.scopes and BEHAVIOR in procedure.claims
    assert rs.PROCEDURE in _scope("Steps: create a fresh virtual environment, then:").scopes
    addition = _scope("Please make EXCEL skip empty lines so the input yields one record, and add a test for it.")
    assert r.TEST_ADDITION_CLAIM in addition.claims and BEHAVIOR in addition.claims
    assert _scope("Add a regression test for it.").claims == (r.TEST_ADDITION_CLAIM,)
    assert _scope("Add a regression test for it and log a warning.").claims == (r.TEST_ADDITION_CLAIM, BEHAVIOR)
    assert r.TEST_ADDITION_CLAIM not in _scope("Do not add any tests for this.").claims
    assert r.TEST_ADDITION_CLAIM not in _scope("Add three built-in string functions: lower, upper and trim.").claims


# ---------------------------------------------------------------- the compiler and admission (D3)
def _compile(goal, **kw):
    reqs = derive_requirements(goal)
    kw.setdefault("origins", statement_origins(goal))
    kw.setdefault("test_files", ["tests/test_a.py"])
    return reqs, compile_verification_contract(reqs, **kw)


def test_08_t1_like_goal_compiles_with_non_claims_authority_required_and_reports_counts():
    reqs, contract = _compile(T1_GOAL, tracked_paths=["src/cache.py", "tests/test_a.py"], project_language="python")
    report = contract.report()
    # REQ-4 "Reproducer 2:" is the one non-claim; both reproducer blocks carry comments (review VC3-R3) and are claims
    assert report["totals"] == {"requirements": 7, "non_claim": 1, "closable": 0, "authority_required": 6,
                                "ambiguous": 0, "dispositioned": 0, "residual_claims": 6}
    assert report["admission"] == "VERIFICATION_AUTHORITY_REQUIRED"
    entry = contract.entry("REQ-7")
    assert entry.status == STATUS_AUTHORITY_REQUIRED and entry.closers == [CLOSER_API_PRESERVATION, TEST_IMMUTABILITY]
    assert [res.claim for res in entry.residual] == [BEHAVIOR] and entry.residual[0].strength == BEHAVIOR_GENERAL
    assert contract.entry("REQ-4").status == STATUS_NOT_A_CLAIM and contract.entry("REQ-4").closers == [CLOSER_NOT_A_CLAIM]
    refusal = contract.refusal()
    assert isinstance(refusal, VerificationAuthorityRequired) and refusal.reason_code == VERIFICATION_AUTHORITY_REQUIRED
    assert refusal.failure_category == "verification_authority_required"
    assert refusal.message.startswith("VERIFICATION_AUTHORITY_REQUIRED: 6 mandatory requirement(s)")
    rows = refusal.to_dict()["residual"]
    assert {row["id"] for row in rows} == {"REQ-1", "REQ-2", "REQ-3", "REQ-5", "REQ-6", "REQ-7"}
    assert all(row["acceptable_authorities"] for row in rows) and refusal.to_dict()["verification_contract"]["totals"]
    json.dumps(contract.to_dict(), default=str)  # serializable


def test_09_ambiguity_is_goal_insufficient_and_takes_precedence_over_missing_authority():
    reqs, contract = _compile("Keep things tidy.\nDo not modify any other file.\n", tracked_paths=["a.py"])
    assert contract.entry("REQ-2").status == STATUS_AMBIGUOUS
    refusal = contract.refusal()
    assert isinstance(refusal, GoalAdmissionError) and refusal.reason_code == GOAL_INSUFFICIENT_FOR_VERIFICATION
    assert refusal.message.startswith("GOAL_INSUFFICIENT_FOR_VERIFICATION:")
    assert {row["id"] for row in refusal.residual} == {"REQ-1", "REQ-2"} and "no referent" in refusal.residual[1]["why"]
    only_labels = compile_verification_contract(derive_requirements("Specification:\n\nExamples:\n"), origins=None)
    assert isinstance(only_labels.refusal(), GoalAdmissionError)


def test_10_a_goal_of_closable_statements_is_admitted_and_sealed(tmp_path):
    goal = ("Fix the expiry boundary in src/cache.py.\n\ntests/test_a.py must keep passing.\n\n"
            "Every existing test must keep passing unchanged.\n\nDo not change any existing test.\n\n"
            "Do not modify any other file.\n\nThe public API must stay unchanged.\n\n"
            "Document it in the README's function list if there is one.\n\nAdd a regression test for it.\n")
    acceptance = ExternalAuthority("acceptance_file", "a" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_GENERAL}}})
    reqs, contract = _compile(goal, tracked_paths=["src/cache.py", "tests/test_a.py", "README.md"],
                              project_language="python", external_authorities=[acceptance],
                              tracked_file_reader=lambda p: b"# Cache\n\nUsage.\n")
    assert contract.refusal() is None and contract.report()["admission"] == "ADMITTED"
    closers = contract.closers_by_requirement()
    assert closers["REQ-1"] == [CLOSER_EXTERNAL_ACCEPTANCE] and closers["REQ-2"] == ["named_tests"]
    assert closers["REQ-3"] == [SUITE_PRESERVATION, TEST_IMMUTABILITY] and closers["REQ-4"] == [TEST_IMMUTABILITY]
    assert closers["REQ-5"] == ["mutation_scope"] and closers["REQ-6"] == [CLOSER_API_PRESERVATION]
    assert closers["REQ-7"] == [CLOSER_DOCUMENTATION_NOT_APPLICABLE] and closers["REQ-8"] == [CLOSER_TEST_ADDITION]
    # the same README with a function list is a content claim: authority required
    _, present = _compile(goal, tracked_paths=["src/cache.py", "tests/test_a.py", "README.md"], project_language="python",
                          external_authorities=[acceptance], tracked_file_reader=lambda p: b"# Cache\n\n## Function list\n")
    assert present.entry("REQ-7").status == STATUS_AUTHORITY_REQUIRED
    # sealing: content-addressed, idempotent, outside the workspace
    path = seal_verification_contract(contract, str(tmp_path / "state"))
    assert path.endswith(contract.digest + ".json") and seal_verification_contract(contract, str(tmp_path / "state")) == path
    stored = json.load(open(path))
    assert stored["digest"] == contract.digest and stored["report"]["admission"] == "ADMITTED"


def test_11_digest_binds_every_input():
    goal = "Fix the expiry boundary in src/cache.py.\nEvery existing test must keep passing.\n"
    base = _compile(goal, tracked_paths=["src/cache.py"])[1]
    assert _compile(goal, tracked_paths=["src/cache.py"])[1].digest == base.digest
    authority = ExternalAuthority("external_acceptance_command", "b" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_GENERAL}}})
    assert _compile(goal, tracked_paths=["src/cache.py"], external_authorities=[authority])[1].digest != base.digest
    assert _compile(goal, tracked_paths=["src/cache.py"], base_revision="abc")[1].digest != base.digest
    assert _compile(goal + "Add logging.\n", tracked_paths=["src/cache.py"])[1].digest != base.digest
    assert _compile(goal, tracked_paths=["src/cache.py"], project_language="java")[1].digest != base.digest


def test_12_claim_strength_rules_hold_for_every_authority_kind():
    general = "Make lower() lower-case every string it is given, exactly as str.lower() does.\n"
    exact = "lower('ABC') -> 'abc'.\n"
    examples = ExternalAuthority("goal_examples", "c" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_EXACT}}})
    # finite examples never close a general rule (B2-COV)
    assert _compile(general, external_authorities=[examples])[1].entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED
    assert "supporting evidence only" in _compile(general, external_authorities=[examples])[1].entry("REQ-1").residual[0].why
    # at admission, an acceptance file alone never admits a GENERAL statement (it could only end REQUIREMENTS_UNRESOLVED
    # after model calls - the pre-003 pathology); the approval is the authority for it
    assert _compile(exact, external_authorities=[examples])[1].entry("REQ-1").closers == [CLOSER_DERIVED_EXAMPLES]
    # an acceptance file closes EXACT; GENERAL needs the approval (B3)
    assert _compile(exact, acceptance_ids=["REQ-1"])[1].entry("REQ-1").closers == ["acceptance"]
    assert _compile(general, acceptance_ids=["REQ-1"])[1].entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED

    class _Approval:
        digest = "d" * 64
        entries = {"REQ-1": object()}
    assert _compile(general, acceptance_ids=["REQ-1"], approval=_Approval())[1].entry("REQ-1").closers == ["acceptance_approval"]
    # an external command declared for EXACT only never covers a GENERAL statement; GENERAL coverage covers both
    weak = ExternalAuthority("external_acceptance_command", "e" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_EXACT}}})
    strong = ExternalAuthority("external_acceptance_command", "f" * 64, {"REQ-1": {BEHAVIOR: {"accepted_strength": BEHAVIOR_GENERAL}}})
    assert _compile(general, external_authorities=[weak])[1].entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED
    assert _compile(general, external_authorities=[strong])[1].entry("REQ-1").closers == [CLOSER_EXTERNAL_ACCEPTANCE]
    assert _compile(exact, external_authorities=[weak])[1].entry("REQ-1").closers == [CLOSER_EXTERNAL_ACCEPTANCE]
    # an authority never covers a requirement it does not declare, nor another claim kind
    other = ExternalAuthority("external_acceptance_command", "9" * 64, {"REQ-9": {BEHAVIOR: {"accepted_strength": BEHAVIOR_GENERAL}}})
    assert _compile(general, external_authorities=[other])[1].entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED
    api_only = ExternalAuthority("external_acceptance_command", "8" * 64, {"REQ-1": {API_PRESERVATION: {}}})
    assert _compile(general, external_authorities=[api_only])[1].entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED
    # an API constraint in a language without a predicate needs an external authority; Python and (BACKEND-
    # READINESS-004) Java have the repository predicate, and a covering authority binds beside it
    unknown = _compile("The public API must stay unchanged.\n", project_language=None)[1].entry("REQ-1")
    assert unknown.status == STATUS_AUTHORITY_REQUIRED and "no deterministic public-API predicate" in unknown.residual[0].why
    assert _compile("The public API must stay unchanged.\n", project_language=None, external_authorities=[api_only])[1].entry("REQ-1").closers == [CLOSER_EXTERNAL_ACCEPTANCE]
    java = _compile("The public API must stay unchanged.\n", project_language="java")[1].entry("REQ-1")
    assert java.status == STATUS_CLOSABLE and [b.detail.get("language") for b in java.bindings] == ["java"]


def test_13_legacy_api_delegates_to_the_compiler():
    reqs = derive_requirements("Make lower() lower-case its argument.\nEvery existing test must keep passing unchanged (pytest).")
    closers, residual = deterministic_closers(reqs, test_files=["tests/test_a.py"])
    assert closers == {"REQ-1": [], "REQ-2": [SUITE_PRESERVATION, TEST_IMMUTABILITY]}
    assert [row["id"] for row in residual] == ["REQ-1"] and "model judgment never closes it" in residual[0]["why"]
    refusal = admission_gap(reqs, test_files=["tests/test_a.py"])
    assert isinstance(refusal, VerificationAuthorityRequired)
    # "Make lower() lower-case its argument." states no concrete case (GENERAL): an acceptance file alone does not admit it
    assert isinstance(admission_gap(reqs, test_files=["tests/test_a.py"], acceptance_ids=["REQ-1"]), VerificationAuthorityRequired)
    exact = derive_requirements("lower('ABC') -> 'abc'.\nEvery existing test must keep passing unchanged (pytest).")
    assert admission_gap(exact, test_files=["tests/test_a.py"], acceptance_ids=["REQ-1"]) is None


# ---------------------------------------------------------------- NOT_A_CLAIM in the ledger
def test_14_non_claims_never_block_and_are_visible_whatever_a_verifier_said():
    reqs, contract = _compile("Specification:\n\nMake lower() lower-case its argument.\n")
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    assert record_contract_non_claims(ledger, reqs, contract, source="test") == ["REQ-1"]
    assert record_contract_non_claims(ledger, reqs, contract, source="test") == []  # idempotent
    # a verifier "missing" on the label changes nothing: it states no proposition
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.VIOLATED, "missing"),
                                               "REQ-2": (RequirementOutcome.UNVERIFIED, "cannot confirm")},
                                revision=1, evidence_fingerprint="cand", source="test")
    outcomes = requirement_outcomes(ledger, reqs)
    assert outcomes["REQ-1"] is RequirementOutcome.NOT_A_CLAIM and outcomes["REQ-2"] is RequirementOutcome.UNVERIFIED
    blocking = blocking_requirements(ledger, reqs, unknown_policy="block", unverified_policy="block")
    assert [req.id for req, _ in blocking] == ["REQ-2"]
    evidence = requirement_evidence(ledger, reqs)["REQ-1"]
    assert evidence["reason_code"] == "STRUCTURAL_NON_CLAIM" and evidence["origin"] == ORIGIN_SENTENCE
    # the non-claim record is never a candidate's closure evidence
    assert r.requirement_closure(ledger, "REQ-1", "cand") is None
    assert r.requirement_closure(ledger, "REQ-1", r.NON_CLAIM_EVIDENCE_ID) is None


def test_15_closed_tables():
    assert all({"claim", "authority", "pass", "fail", "unknown"} <= set(v) for v in CLOSER_CONTRACTS.values())
    assert "verification_contract.sealed" in VERIFICATION_CONTRACT_EVENT_KINDS
    assert STATUS_CLOSABLE and STATUS_AMBIGUOUS


def test_16_every_contract_event_kind_in_production_code_is_in_the_closed_table():
    """Trace 2 of the pre-flight: run-event kinds have no inventory; the contract's own kinds do."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "kriya"
    used = set()
    for path in root.rglob("*.py"):
        used.update(re.findall(r'kind=\(?\s*"((?:verification_contract|requirement\.authority)[a-z_.]*)"', path.read_text()))
        used.update(re.findall(r'"(requirement\.authority_required)"', path.read_text()))
    assert used, "the contract events must be emitted somewhere"
    assert used <= VERIFICATION_CONTRACT_EVENT_KINDS, used - VERIFICATION_CONTRACT_EVENT_KINDS


def test_17_the_contract_digest_is_part_of_the_resume_identity(tmp_path):
    """m02: a changed verification contract regenerates the candidate; unset keeps every fingerprint byte-identical."""
    from kriya.config.config import AppConfig
    from kriya.workflow.resume_fingerprints import ARTIFACT_DEPENDENCIES, generation_resume_fingerprints

    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    without = generation_resume_fingerprints(cfg, str(tmp_path), goal="Fix it.")
    assert without == generation_resume_fingerprints(cfg, str(tmp_path), goal="Fix it.", verification_contract_digest=None)
    bound = generation_resume_fingerprints(cfg, str(tmp_path), goal="Fix it.", verification_contract_digest="a" * 64)
    changed = generation_resume_fingerprints(cfg, str(tmp_path), goal="Fix it.", verification_contract_digest="b" * 64)
    assert {name for name in without if without[name] != bound[name]} == {"goal"}
    assert bound["goal"] != changed["goal"] and "goal" in ARTIFACT_DEPENDENCIES["candidate"]


def test_19_doctest_sessions_are_concrete_cases_for_the_strength_rule():
    """A doctest statement is EXACT (its prompts are stated cases; its code is content, not prose); prose with a
    universal word outside the session keeps it GENERAL; a doctest-less general rule is unchanged."""
    from kriya.workflow.requirements import BEHAVIOR_EXACT, BEHAVIOR_GENERAL, behavior_strength

    session = (">>> from toolz import interpose >>> list(interpose('a', [])) [] >>> list(interpose('a', iter([]))) [] "
               ">>> def grab(path, item): ... items.append(item) ... return None")
    strength, why = behavior_strength(session, regression_covered=False)
    assert strength == BEHAVIOR_EXACT and len(why["examples"]) >= 3, why
    strength, why = behavior_strength("It must work for any sequence. " + session, regression_covered=False)
    assert strength == BEHAVIOR_GENERAL and any("general rule" in r for r in why["reasons"])
    strength, _ = behavior_strength("double(x) returns 2 * x for any number x.", regression_covered=False)
    assert strength == BEHAVIOR_GENERAL
    # and the S1_A/S6_A shapes: the doctest statement is EXACT so compiled examples may close it
    s6 = _compile("interpose() blows up on an empty sequence.\n\n>>> from toolz import interpose\n>>> list(interpose('a', []))\n[]\n",
                  external_authorities=[ExternalAuthority("goal_examples", "c" * 64, {"REQ-2": {BEHAVIOR: {"accepted_strength": BEHAVIOR_EXACT}}})])[1]
    assert s6.entry("REQ-2").closers == [CLOSER_DERIVED_EXAMPLES] and s6.entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED
