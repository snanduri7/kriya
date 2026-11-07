"""REQUIREMENT TRUTH, MEASURED (owner ruling 2026-11-07, "D. RE-MEASURE AFTER CLOSER FIX").

Maps each mandatory requirement's FINAL outcome to ALL_VERIFIED / OPEN_REQUIREMENTS / CONTRADICTED for the representative goals of the walkthrough, with the deterministic closers that exist today:
the goal's own contract (``compile_verification_contract``: which claims a statement makes and which closers can close each), the model's verdict (MODEL_CLAIMED, so UNVERIFIED by design), and the closers run on real candidate
content. It PINS what is true now; it changes no policy. The finding it pins: closing the test-addition claim of an existing test file is correct and needed, but a statement that ALSO claims BEHAVIOR
(the usual goal wording) stays OPEN until a deterministic behaviour closer (derived examples from the goal's own examples, an operator acceptance file, an external authority, named tests at base) binds it - nothing
reads a runtime check's evidence for requirements, and a candidate's own new test is never accepted as proof of the behaviour it was written for.
"""
import pytest

from kriya.workflow import contract_closers as cc
from kriya.workflow.contract_compilation import CLOSER_NAMED_TESTS, CLOSER_TEST_ADDITION, compile_verification_contract
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)

ALL_VERIFIED, OPEN_REQUIREMENTS, CONTRADICTED = "ALL_VERIFIED", "OPEN_REQUIREMENTS", "CONTRADICTED"
_VERIFIED = {RequirementOutcome.CLOSED_BY_EVIDENCE, RequirementOutcome.HUMAN_ACCEPTED, RequirementOutcome.NOT_A_CLAIM, RequirementOutcome.DISPOSITIONED}


def truth_class(outcomes) -> str:
    """The derived final truth of a set of mandatory requirements (a DERIVED result truth, not a second authority)."""
    values = list(outcomes.values())
    if any(value is RequirementOutcome.VIOLATED for value in values):
        return CONTRADICTED
    return ALL_VERIFIED if values and all(value in _VERIFIED for value in values) else OPEN_REQUIREMENTS


PERCENT = "Add a function percent(part, whole) to calc.py that returns part as a percentage of whole rounded to one decimal place, and add a pytest test for it.\n"
FAREWELL = ('Add a method farewell(String name) to the existing Greeter class in src/main/java/demo/Greeter.java that returns "Goodbye, <name>!", '
            "and add a JUnit 5 test for it in src/test/java/demo/GreeterTest.java.\n")
GREET = 'Create greet.py with a function greet(name) that returns "Hello, <name>!" and test_greet.py with two pytest tests for it.\n'
HELLO = "Create hello.py. It prints hello world when executed. Include a pytest test for it.\n"
SUBSTITUTE = "Change hello world message to Hello Kriya in the app\n"
PY_BEFORE = "import calc\n\n\ndef test_add():\n    assert calc.add(1, 2) == 3\n"
PY_AFTER = PY_BEFORE + "\n\ndef test_percent():\n    assert calc.percent(1, 2) == 50.0\n"
JAVA_BEFORE = "package demo;\nimport org.junit.jupiter.api.Test;\nclass GreeterTest {\n  @Test void hello() { }\n}\n"
JAVA_AFTER = "package demo;\nimport org.junit.jupiter.api.Test;\nclass GreeterTest {\n  @Test void hello() { }\n  @Test void farewell() { }\n}\n"


def _case(goal, *, test_files, language="python", structural=None, created=()):
    """The contract, a ledger holding the model's verdict (UNVERIFIED: model-claimed), and the test-addition closer run on the given content; returns (contract, ledger, reqs, entries)."""
    reqs = derive_requirements(goal)
    contract = compile_verification_contract(reqs, origins=statement_origins(goal), test_files=list(test_files), project_language=language)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "model") for r in reqs.requirements}, revision=1, evidence_fingerprint="cand", source="test")
    entries = []
    if contract.binding_closers(CLOSER_TEST_ADDITION):
        present = sorted(set(test_files) | set(created))
        contents = dict(structural or {})
        entries = cc.close_test_addition_requirements(
            ledger, reqs, contract, reference_test_files=sorted(test_files), candidate_test_files=present, base_test_identities=None, candidate_test_identities=None, source="t", revision=1,
            read_candidate=lambda p: contents[p][1].encode() if p in contents else (b"def test_new():\n    assert True\n" if p in created else None),
            read_reference=(lambda p: contents[p][0].encode() if p in contents else None) if structural else None)
    return contract, ledger, reqs, entries


def _closed_claims(ledger, reqs, requirement_id="REQ-1"):
    evidence = requirement_evidence(ledger, reqs).get(requirement_id) or {}
    return sorted(claim for claim, record in (evidence.get("claims") or {}).items() if record)


# (2) Brownfield Python: existing test file modified with a new test --------------------------------------------------------------------------------------------
def test_case_2_brownfield_python_before_the_fix_test_addition_was_unproven_after_it_is_closed_and_the_statement_is_still_open_on_behaviour():
    structural = {"test_calc.py": (PY_BEFORE, PY_AFTER)}
    contract, ledger, reqs, [entry] = _case(PERCENT, test_files=["test_calc.py"], structural=structural)
    assert list(contract.required_claims_by_requirement()["REQ-1"]) == ["TEST_ADDITION", "BEHAVIOR"]
    assert entry["closed"] is True and _closed_claims(ledger, reqs) == ["TEST_ADDITION"]                 # AFTER: the test-addition claim is closed by deterministic before/after evidence
    assert truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS                           # BEHAVIOR has no deterministic closer: still open
    _, before_ledger, before_reqs, [before] = _case(PERCENT, test_files=["test_calc.py"], structural=None)
    assert before["closed"] is False and before["reason_code"] == cc.TEST_ADDITION_UNPROVEN               # BEFORE (no structural evidence, as the closer was called in production)
    assert _closed_claims(before_ledger, before_reqs) == []


# (3) Java: existing JUnit test class gains a @Test --------------------------------------------------------------------------------------------------------------
def test_case_3_java_existing_test_class_gains_a_test_the_claim_closes_but_regression_preservation_and_behaviour_stay_open():
    structural = {"src/test/java/demo/GreeterTest.java": (JAVA_BEFORE, JAVA_AFTER)}
    contract, ledger, reqs, entries = _case(FAREWELL, test_files=["src/test/java/demo/GreeterTest.java"], language="java", structural=structural)
    assert set(contract.required_claims_by_requirement()["REQ-1"]) == {"TEST_ADDITION", "REGRESSION_PRESERVATION", "BEHAVIOR"}
    assert CLOSER_NAMED_TESTS in contract.entry("REQ-1").closers and CLOSER_TEST_ADDITION in contract.entry("REQ-1").closers
    assert entries and all(e["closed"] for e in entries) and _closed_claims(ledger, reqs) == ["TEST_ADDITION"]
    assert truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS


# (1) Greenfield: created files + tests ------------------------------------------------------------------------------------------------------------------------
def test_case_1_greenfield_a_behaviour_only_statement_has_no_deterministic_closer_and_a_separate_test_statement_closes():
    contract, ledger, reqs, _ = _case(GREET, test_files=[])
    assert list(contract.required_claims_by_requirement()["REQ-1"]) == ["BEHAVIOR"] and contract.entry("REQ-1").closers == []
    assert truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS
    contract, ledger, reqs, entries = _case(HELLO, test_files=[], created=["test_hello.py"])
    outcomes = {rid: outcome.value for rid, outcome in requirement_outcomes(ledger, reqs).items()}
    assert outcomes == {"REQ-1": "unverified", "REQ-2": "unverified", "REQ-3": "closed_by_evidence"}      # the test statement is VERIFIED; the two behaviour statements are OPEN
    assert truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS


# (4) runtime-verified goal -------------------------------------------------------------------------------------------------------------------------------------------
def test_case_4_a_runtime_verified_goal_makes_behaviour_claims_that_no_closer_reads_runtime_evidence_for():
    contract, ledger, reqs, _ = _case(HELLO, test_files=[], created=["test_hello.py"])
    for requirement_id in ("REQ-1", "REQ-2"):            # "prints hello world when executed": observable behaviour, the claim a passing runtime check speaks to
        assert list(contract.required_claims_by_requirement()[requirement_id]) == ["BEHAVIOR"] and contract.entry(requirement_id).closers == []
    from kriya.workflow import contract_closers, requirement_closers

    for module in (contract_closers, requirement_closers):                                              # TRACED: no requirement closer consumes a run_verification gate outcome
        assert "run_verification" not in open(module.__file__, encoding="utf-8").read()


# (5) the substitute-file negative control ------------------------------------------------------------------------------------------------------------------------------
def test_case_5_a_substitute_file_goal_is_open_not_contradicted_and_not_verified():
    contract, ledger, reqs, _ = _case(SUBSTITUTE, test_files=[])
    assert list(contract.required_claims_by_requirement()["REQ-1"]) == ["BEHAVIOR"] and contract.entry("REQ-1").closers == []
    assert truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS


# the classification itself -------------------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("outcomes,expected", [
    ({"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE, "REQ-2": RequirementOutcome.HUMAN_ACCEPTED}, ALL_VERIFIED),
    ({"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE, "REQ-2": RequirementOutcome.UNVERIFIED}, OPEN_REQUIREMENTS),
    ({"REQ-1": RequirementOutcome.UNKNOWN}, OPEN_REQUIREMENTS), ({"REQ-1": RequirementOutcome.PENDING}, OPEN_REQUIREMENTS),
    ({"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE, "REQ-2": RequirementOutcome.VIOLATED}, CONTRADICTED),
    ({}, OPEN_REQUIREMENTS),
])
def test_the_derived_truth_classes(outcomes, expected):
    assert truth_class(outcomes) == expected
