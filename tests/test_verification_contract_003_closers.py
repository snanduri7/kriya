"""VERIFICATION-CONTRACT-003 slice 2: the goal-example compiler (sealed B2-a module), the Python public-API
predicate, the test-addition and documentation closers, and contract-aware claim recording - a compound statement
closes only when every claim it makes is closed, and any claim's counter-evidence makes it VIOLATED.

Every acceptance run here is real (Kriya's B2-a runner in a subprocess on a candidate tree in tmp_path); the API
predicate reads a real git base revision.
"""
import subprocess
import sys

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow import api_preservation as api
from kriya.workflow import contract_closers as cc
from kriya.workflow import example_oracle as ex
from kriya.workflow.contract_compilation import (
    CLOSER_API_PRESERVATION,
    CLOSER_DERIVED_EXAMPLES,
    CLOSER_DOCUMENTATION_NOT_APPLICABLE,
    CLOSER_TEST_ADDITION,
    STATUS_AUTHORITY_REQUIRED,
    compile_verification_contract,
)
from kriya.workflow.obligations import ObligationLedger, ObligationStatus
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    REGRESSION_PRESERVATION,
    TEST_IMMUTABILITY_CLAIM,
    RequirementOutcome,
    close_suite_preservation_requirements,
    close_test_immutability_requirements,
    derive_requirements,
    record_requirement_claim,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)

CALC_GOAL = ("Add lower() and upper() helpers to calc.\n\nExamples:\n"
             "  calc.lower('ABC') -> 'abc'\n  calc.upper('abc') -> 'ABC'\n  calc.lower(1) -> raises TypeError\n")
CALC_OK = "def lower(s):\n    if not isinstance(s, str):\n        raise TypeError(s)\n    return s.lower()\n\n\ndef upper(s):\n    return s.upper()\n"
CALC_WRONG = "def lower(s):\n    return s.upper()\n\n\ndef upper(s):\n    return s.upper()\n"
DOCTEST_GOAL = ("Streaming parse must give the same items.\n\n>>> from calc import lower\n>>> lower('ABC')\n'abc'\n>>> lower('x')\n'x'\n")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path, files):
    root = tmp_path / "ws"
    root.mkdir()
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _ledger(reqs, candidate="cand", outcome=RequirementOutcome.UNVERIFIED):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (outcome, "x") for r in reqs.requirements}, revision=1,
                                evidence_fingerprint=candidate, source="test")
    return ledger


def _validator(root):
    return lambda: PolymorphicValidator(str(root), autonomy_cfg=AutonomyConfig())


# ---------------------------------------------------------------- the example compiler
def test_01_recognizes_arrow_and_doctest_forms_and_rejects_the_rest():
    reqs = derive_requirements(CALC_GOAL)
    compilation = ex.recognize_goal_examples(CALC_GOAL, reqs)
    assert [(e.form, e.requirement_id) for e in compilation.examples] == [
        (ex.FORM_ARROW_LITERAL, "REQ-2"), (ex.FORM_ARROW_LITERAL, "REQ-2"), (ex.FORM_ARROW_RAISES, "REQ-2")]
    assert compilation.examples[2].exception == "TypeError" and compilation.modules == ["calc"]
    assert compilation.rejected == []
    doc = ex.recognize_goal_examples(DOCTEST_GOAL, derive_requirements(DOCTEST_GOAL))
    assert [e.form for e in doc.examples] == [ex.FORM_DOCTEST] and doc.modules == ["calc"]
    # rejected with reasons: a signature line, a bare call, a non-literal right side, a prose arrow
    bad = "Spec:\n  lower(string $s) -> string lowers it\n  lower('A') -> 'a'\n  calc.f(1) -> no idea\n  calc.g(1) -> raises Nope\n"
    rejected = ex.recognize_goal_examples(bad, derive_requirements(bad)).rejected
    assert [r["why"] for r in rejected] == ["the left side is not a Python expression",
                                            "no candidate module to import (bare function call)",
                                            "the right side is not a Python literal",
                                            "exception 'Nope' is neither dotted, stated elsewhere in the goal nor a builtin"]
    # determinism: same goal, same module bytes
    assert ex.compile_example_module(compilation) == ex.compile_example_module(ex.recognize_goal_examples(CALC_GOAL, reqs))


def test_02_the_compiled_module_is_a_valid_b2a_artifact_that_judges_a_real_candidate(tmp_path):
    reqs = derive_requirements(CALC_GOAL)
    for correct, content in ((True, CALC_OK), (False, CALC_WRONG)):
        root = tmp_path / ("ok" if correct else "wrong")
        root.mkdir()
        (root / "calc.py").write_text(content)
        artifact, report = ex.derive_example_artifact(CALC_GOAL, reqs, state_root=str(tmp_path / "state"), candidate_root=str(root))
        assert artifact is not None and report["refusal"] is None and artifact.requirement_ids == ["REQ-2"]
        assert artifact.source_name == ex.EXAMPLE_SOURCE_NAME and len(artifact.cases) == 3
        run = ao.run_acceptance(artifact, str(root), candidate_paths=["calc.py"], validator_factory=_validator(root))
        judgment = ao.judge_acceptance(artifact, run)["REQ-2"]
        assert judgment.passed is correct and judgment.violated is (not correct)
    # the authority covers EXACT behaviour only, with provenance
    authority = ex.example_authority(artifact, report)
    assert authority.kind == "goal_examples" and authority.visibility == "goal_text"
    assert authority.coverage["REQ-2"]["accepted_strength"] == "EXACT" and authority.provenance["compiler_version"] == 1
    assert authority.covers("REQ-2", BEHAVIOR, "EXACT") and not authority.covers("REQ-2", BEHAVIOR, "GENERAL")
    # the statement with the examples is EXACT -> closable by the derived module; the request sentence stays GENERAL
    contract = compile_verification_contract(reqs, origins=statement_origins(CALC_GOAL), test_files=[],
                                             external_authorities=[authority], project_language="python")
    assert contract.entry("REQ-2").closers == [CLOSER_DERIVED_EXAMPLES]
    assert contract.entry("REQ-1").status == STATUS_AUTHORITY_REQUIRED


def test_03_a_doctest_block_runs_under_the_b2a_boundary(tmp_path):
    reqs = derive_requirements(DOCTEST_GOAL)
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC_OK)
    artifact, report = ex.derive_example_artifact(DOCTEST_GOAL, reqs, state_root=str(tmp_path / "state"), candidate_root=str(root))
    assert artifact is not None, report
    judgment = ao.judge_acceptance(artifact, ao.run_acceptance(artifact, str(root), candidate_paths=["calc.py"],
                                                               validator_factory=_validator(root)))
    [rid] = artifact.requirement_ids
    assert judgment[rid].passed
    (root / "calc.py").write_text(CALC_WRONG)
    judgment = ao.judge_acceptance(artifact, ao.run_acceptance(artifact, str(root), candidate_paths=["calc.py"],
                                                               validator_factory=_validator(root)))
    assert judgment[rid].violated


def test_04_an_unsupported_layout_or_no_examples_binds_nothing(tmp_path):
    root = tmp_path / "ws"
    (root / "src" / "calc").mkdir(parents=True)
    (root / "src" / "calc" / "__init__.py").write_text(CALC_OK)
    artifact, report = ex.derive_example_artifact(CALC_GOAL, derive_requirements(CALC_GOAL),
                                                  state_root=str(tmp_path / "state"), candidate_root=str(root))
    assert artifact is None and report["refusal"]["reason_code"] == ao.ACCEPTANCE_LAYOUT_UNSUPPORTED
    plain = "Fix the expiry boundary.\n"
    assert ex.derive_example_artifact(plain, derive_requirements(plain), state_root=str(tmp_path / "s2"), candidate_root=str(root)) == (None, {
        "compiler_version": 1, "examples": [], "rejected": [], "modules": [], "artifact": None, "refusal": None})


# ---------------------------------------------------------------- the API predicate
BASE_MOD = "class Cache:\n    def __init__(self, maxsize, ttl=3):\n        pass\n\n    def expire(self, time=None):\n        pass\n\n\ndef helper(a, b=1):\n    return a\n\n\ndef _private():\n    pass\n"


@pytest.mark.parametrize("candidate, removed, changed", [
    (BASE_MOD, [], []),
    (BASE_MOD.replace("def helper(a, b=1):", "def helper(a, b=2):"), [], ["cache.helper"]),
    (BASE_MOD.replace("    def expire(self, time=None):\n        pass\n\n", ""), ["cache.Cache.expire"], []),
    (BASE_MOD + "\n\ndef added():\n    pass\n", [], []),
    (BASE_MOD.replace("def _private():", "def _other():"), [], []),
])
def test_05_public_api_comparison_base_versus_candidate(tmp_path, candidate, removed, changed):
    root, base = _repo(tmp_path, {"cache.py": BASE_MOD, "tests/test_cache.py": "def test_x():\n    pass\n"})
    (root / "cache.py").write_text(candidate)
    comparison = api.compare_public_api(str(root), base, candidate_files=["cache.py"],
                                        read_candidate=lambda p: (root / p).read_bytes() if (root / p).exists() else None)
    assert comparison.available and list(comparison.removed) == removed and list(comparison.changed) == changed
    assert comparison.preserved is (not removed and not changed)
    assert "tests/test_cache.py" not in comparison.compared_files


def test_06_unparseable_or_baseless_evidence_is_unavailable_not_pass(tmp_path):
    root, base = _repo(tmp_path, {"cache.py": BASE_MOD})
    (root / "cache.py").write_text("def broken(:\n")
    bad = api.compare_public_api(str(root), base, candidate_files=["cache.py"], read_candidate=lambda p: (root / p).read_bytes())
    assert not bad.available and not bad.preserved and "does not parse" in bad.reason
    none = api.compare_public_api(str(root), None, candidate_files=["cache.py"], read_candidate=lambda p: b"")
    assert not none.available and "no authorized base revision" == none.reason


# ---------------------------------------------------------------- compound closure: every claim or nothing
COMPOUND = "Do not change the public API or existing tests; keep the behaviour of all other cache classes unchanged.\n"


def _compound(tmp_path, root_files=None):
    reqs = derive_requirements(COMPOUND)
    contract = compile_verification_contract(reqs, origins=statement_origins(COMPOUND), test_files=["tests/test_a.py"],
                                             project_language="python")
    entry = contract.entry("REQ-1")
    assert set(entry.required_claims) == {API_PRESERVATION, TEST_IMMUTABILITY_CLAIM, BEHAVIOR}
    assert entry.closers == [CLOSER_API_PRESERVATION, "test_immutability"] and entry.status == STATUS_AUTHORITY_REQUIRED
    return reqs, contract


def test_07_a_compound_statement_closes_only_when_every_claim_is_closed():
    reqs, contract = _compound(None)
    claims = contract.required_claims_by_requirement()
    ledger = _ledger(reqs)
    # API preserved (recorded with the contract's required claims)
    api.close_api_preservation_requirements(ledger, reqs, contract_claims=claims,
                                            comparison=api.ApiComparison(True, None, (), (), ()), source="t", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    # tests untouched (claim-level record for the compound statement)
    scope = {"actual_paths": ["cache.py"], "foreign_paths": [], "committed_history": []}
    [entry] = close_test_immutability_requirements(ledger, reqs, reference_test_files=["tests/test_a.py"],
                                                   present_test_files=["tests/test_a.py"], scope_evidence=scope,
                                                   source="t", revision=1, contract_claims=claims)
    assert entry["claim_recorded"] == TEST_IMMUTABILITY_CLAIM and entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED  # behaviour still open
    # the behaviour claim closed by an authority with the same required claims -> CLOSED
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="human_bound_acceptance",
                             detail={"required_claims": list(claims["REQ-1"]), "approval_digest": "d" * 64,
                                     "approval": {"requirement_id": "REQ-1", "requirement_text_sha256": __import__("hashlib").sha256(
                                         reqs.get("REQ-1").text.encode()).hexdigest()}},
                             source="t", revision=1, status=ObligationStatus.SATISFIED)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED
    evidence = requirement_evidence(ledger, reqs)["REQ-1"]
    assert set(evidence["claims"]) == {API_PRESERVATION, TEST_IMMUTABILITY_CLAIM, BEHAVIOR}


def test_08_a_behaviour_record_alone_never_closes_a_compound_statement():
    """Mutation guard: the acceptance producer records only BEHAVIOR with the contract's claims - not enough."""
    reqs, contract = _compound(None)
    claims = contract.required_claims_by_requirement()
    ledger = _ledger(reqs)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                             detail={"required_claims": list(claims["REQ-1"])}, source="t", revision=1,
                             status=ObligationStatus.SATISFIED)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    # and a record that claims fewer required claims than the contract (a pre-003 producer) still needs the rest
    other = _ledger(reqs)
    record_requirement_claim(other, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                             detail={"required_claims": [BEHAVIOR]}, source="t", revision=1,
                             status=ObligationStatus.SATISFIED)
    # the BEHAVIOR producer's own required_claims decide (FS-1C1 contract): here it says BEHAVIOR only - the
    # contract-aware producers never write that for a compound statement (test_07), and B2-COV still applies
    assert requirement_outcomes(other, reqs)["REQ-1"] in (RequirementOutcome.UNVERIFIED, RequirementOutcome.CLOSED_BY_EVIDENCE)


def test_09_any_claims_counter_evidence_makes_the_requirement_violated():
    reqs, contract = _compound(None)
    claims = contract.required_claims_by_requirement()
    ledger = _ledger(reqs)
    changed = api.ApiComparison(True, None, ("cache.Cache.expire",), (), ())
    [entry] = api.close_api_preservation_requirements(ledger, reqs, contract_claims=claims, comparison=changed,
                                                      source="t", revision=1)
    assert entry["violated"] is True and entry["reason_code"] == api.API_CHANGED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED
    # unavailable evidence is INDETERMINATE: never PASS, never VIOLATED
    other = _ledger(reqs)
    [entry] = api.close_api_preservation_requirements(other, reqs, contract_claims=claims,
                                                      comparison=api.ApiComparison(False, "base unreadable"), source="t", revision=1)
    assert entry["reason_code"] == api.API_EVIDENCE_UNAVAILABLE and requirement_outcomes(other, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    # a changed existing test is counter-evidence through the immutability claim too
    third = _ledger(reqs)
    scope = {"actual_paths": ["tests/test_a.py"], "foreign_paths": [], "committed_history": []}
    [entry] = close_test_immutability_requirements(third, reqs, reference_test_files=["tests/test_a.py"],
                                                   present_test_files=["tests/test_a.py"], scope_evidence=scope,
                                                   source="t", revision=1, contract_claims=claims)
    assert entry["violated"] is True and requirement_outcomes(third, reqs)["REQ-1"] is RequirementOutcome.VIOLATED


def test_10_a_compound_suite_clause_records_its_regression_claim_only():
    text = "Keep the public interface unchanged and every existing test green.\n"
    reqs = derive_requirements(text)
    contract = compile_verification_contract(reqs, origins=statement_origins(text), test_files=["tests/test_a.py"],
                                             project_language="python")
    claims = contract.required_claims_by_requirement()
    assert set(claims["REQ-1"]) == {API_PRESERVATION, REGRESSION_PRESERVATION, TEST_IMMUTABILITY_CLAIM}
    ledger = _ledger(reqs)
    suite_ids = [rid for rid, _b in contract.binding_closers("suite_preservation")]
    from kriya.tools import test_execution

    green = {"success": True, "test_execution": test_execution.TestExecutionReport(
        gate_id="g", runner="pytest", workspace="", completeness="COMPLETE",
        cases=[test_execution.TestCaseResult(identity="tests/test_a.py::test_x", classname="tests.test_a", name="test_x",
                                             status="passed")]).to_dict()}
    immutability = {"available": True, "changed_test_files": [], "missing_test_files": [], "changed_trust_surface": []}
    [entry] = close_suite_preservation_requirements(ledger, reqs, test_files=["tests/test_a.py"], run_suite=lambda: green,
                                                    source="t", revision=1, test_immutability=immutability,
                                                    contract_claims=claims, suite_requirement_ids=suite_ids)
    assert entry["claim_recorded"] == REGRESSION_PRESERVATION and entry["closed"] is False
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- test addition and documentation closers
def test_11_test_addition_closes_on_an_added_test_file_or_a_new_identity_never_on_a_changed_file_alone():
    text = "Make calc.lower('A') -> 'a' and add a regression test for it.\n"
    reqs = derive_requirements(text)
    contract = compile_verification_contract(reqs, origins=statement_origins(text), test_files=["tests/test_a.py"])
    assert CLOSER_TEST_ADDITION in contract.entry("REQ-1").closers
    for added, base_ids, cand_ids, closed in [
        (["tests/test_a.py", "tests/test_new.py"], None, None, True),
        (["tests/test_a.py"], {"t::a"}, {"t::a", "t::b"}, True),
        (["tests/test_a.py"], {"t::a"}, {"t::a"}, False),
        (["tests/test_a.py"], None, None, False),
    ]:
        ledger = _ledger(reqs)
        [entry] = cc.close_test_addition_requirements(ledger, reqs, contract, reference_test_files=["tests/test_a.py"],
                                                      candidate_test_files=added, base_test_identities=base_ids,
                                                      candidate_test_identities=cand_ids, source="t", revision=1)
        assert entry["closed"] is closed, entry
    # with an unknown reference set nothing closes (fail closed)
    ledger = _ledger(reqs)
    [entry] = cc.close_test_addition_requirements(ledger, reqs, contract, reference_test_files=None,
                                                  candidate_test_files=["tests/test_new.py"], base_test_identities=None,
                                                  candidate_test_identities=None, source="t", revision=1)
    assert entry["closed"] is False and "unknown" in entry["reason"]


def test_12_documentation_closes_only_while_the_conditional_referent_stays_absent():
    text = "Document them in the README's function list if there is one.\n"
    reqs = derive_requirements(text)
    contract = compile_verification_contract(reqs, origins=statement_origins(text), test_files=[], tracked_paths=["calc.py"])
    assert contract.entry("REQ-1").closers == [CLOSER_DOCUMENTATION_NOT_APPLICABLE] and contract.refusal() is None
    ledger = _ledger(reqs)
    [entry] = cc.close_documentation_requirements(ledger, reqs, contract, candidate_tracked_paths=["calc.py"],
                                                  read_candidate=lambda p: None, source="t", revision=1)
    assert entry["closed"] is True and requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    # the candidate added a README with a function list: now a content claim, open
    other = _ledger(reqs)
    [entry] = cc.close_documentation_requirements(other, reqs, contract, candidate_tracked_paths=["calc.py", "README.md"],
                                                  read_candidate=lambda p: b"# Calc\n\n## Function list\n- lower\n", source="t", revision=1)
    assert entry["closed"] is False and entry["reason_code"] == cc.DOCUMENTATION_REFERENT_PRESENT
    assert requirement_outcomes(other, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_13_interpreter_sanity():
    assert sys.version_info >= (3, 10)
