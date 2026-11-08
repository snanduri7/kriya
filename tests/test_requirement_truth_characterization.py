"""REQUIREMENT TRUTH, MEASURED (owner ruling 2026-11-07, "D. RE-MEASURE AFTER CLOSER FIX").

(Updated 2026-11-08, REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001: the file-creation and runtime-stdout closers exist; cases 1/4/5 below are the re-measure with every closer given its evidence.)
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
    requirement_truth,
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


# (1)/(4)/(5) Greenfield, runtime-observable and substitute-file, with EVERY closer given its evidence (REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001) ----------------
def _full(goal, tmp_path, *, files, written, created, stdout="hello world\n", run_file="hello.py"):
    """All closers that exist, each with the evidence a real run of this shape records: the model's UNVERIFIED verdict, the raw capture of the writes, the application run's gate outcome (stamped with the candidate digest),
    the test-addition record. Returns (contract, ledger, reqs)."""
    from kriya.config.environment import PROJECT_PYTHON_INTERPRETER
    from kriya.workflow import requirement_closers as rc
    from kriya.workflow.runtime_observation import candidate_files_digest

    root = tmp_path / "cand"
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    contract, ledger, reqs, _ = _case(goal, test_files=[], created=[w for w in written if w.startswith("test_")])
    rc.close_requirements_by_file_creation(ledger, reqs, contract, str(root), created_paths=created, revision=1)
    outcome = {"attempt": 1, "type": "run_verification", "success": True, "graded_by": "deterministic",
               "candidate_digest": candidate_files_digest(str(root), written),
               "steps": [{"command": [PROJECT_PYTHON_INTERPRETER, run_file], "exit_code": 0, "stdout": stdout, "stderr": "", "timed_out": False}]}
    rc.close_requirements_by_runtime_stdout(ledger, reqs, contract, str(root), gate_outcomes=[outcome], attempt=1, written_files=written, revision=1)
    return contract, ledger, reqs


HELLO_FILES = {"hello.py": "print('hello world')\n", "test_hello.py": "def test_x():\n    assert True\n"}


def test_case_1_greenfield_create_with_a_formatted_return_value_stays_open():
    """The usual greenfield wording is ONE compound sentence with a placeholder return value: GENERAL behaviour, and no deterministic authority exists for it."""
    contract, ledger, reqs, _ = _case(GREET, test_files=[])
    assert list(contract.required_claims_by_requirement()["REQ-1"]) == ["BEHAVIOR"] and contract.entry("REQ-1").closers == []
    assert truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS


def test_case_4_the_runtime_observable_goal_is_all_verified_when_the_run_printed_what_the_goal_states(tmp_path):
    contract, ledger, reqs = _full(HELLO, tmp_path, files=HELLO_FILES, written=["hello.py", "test_hello.py"], created=["hello.py", "test_hello.py"])
    assert {rid: o.value for rid, o in requirement_outcomes(ledger, reqs).items()} == {"REQ-1": "closed_by_evidence", "REQ-2": "closed_by_evidence", "REQ-3": "closed_by_evidence"}
    assert truth_class(requirement_outcomes(ledger, reqs)) == ALL_VERIFIED
    assert requirement_truth(requirement_outcomes(ledger, reqs), reqs)["status"] == ALL_VERIFIED          # the production derivation agrees with this test's own


def test_case_4_the_same_goal_with_a_quoted_literal_and_the_wrong_output_is_contradicted(tmp_path):
    goal = 'Create hello.py. It prints "hello world" when executed. Include a pytest test for it.\n'
    contract, ledger, reqs = _full(goal, tmp_path, files={**HELLO_FILES, "hello.py": "print('goodbye')\n"}, written=["hello.py", "test_hello.py"],
                                   created=["hello.py", "test_hello.py"], stdout="goodbye\n")
    assert {rid: o.value for rid, o in requirement_outcomes(ledger, reqs).items()} == {"REQ-1": "closed_by_evidence", "REQ-2": "violated", "REQ-3": "closed_by_evidence"}
    assert truth_class(requirement_outcomes(ledger, reqs)) == CONTRADICTED
    assert requirement_truth(requirement_outcomes(ledger, reqs), reqs)["status"] == CONTRADICTED


def test_case_4_the_same_goal_unquoted_with_different_case_is_open_not_contradicted(tmp_path):
    contract, ledger, reqs = _full(HELLO, tmp_path, files=HELLO_FILES, written=["hello.py", "test_hello.py"], created=["hello.py", "test_hello.py"], stdout="Hello World\n")
    assert {rid: o.value for rid, o in requirement_outcomes(ledger, reqs).items()} == {"REQ-1": "closed_by_evidence", "REQ-2": "unverified", "REQ-3": "closed_by_evidence"}
    assert truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS


def test_case_5_a_substitute_file_for_a_created_file_leaves_the_creation_requirement_open(tmp_path):
    """"Create hello.py." - the candidate created hello2.py instead. The creation requirement is OPEN (never verified). "It prints hello world" states what the run executed does, so it closes on the observation;
    the goal as a whole is OPEN_REQUIREMENTS, which is the truth: the requested file does not exist."""
    contract, ledger, reqs = _full(HELLO, tmp_path, files={"hello2.py": "print('hello world')\n", "test_hello.py": HELLO_FILES["test_hello.py"]},
                                   written=["hello2.py", "test_hello.py"], created=["hello2.py", "test_hello.py"], run_file="hello2.py")
    outcomes = {rid: o.value for rid, o in requirement_outcomes(ledger, reqs).items()}
    assert outcomes["REQ-1"] == "unverified" and truth_class(requirement_outcomes(ledger, reqs)) == OPEN_REQUIREMENTS


def test_case_5_a_substitute_goal_has_no_deterministic_closer_and_is_open():
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
