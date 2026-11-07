"""REQUIREMENT CLOSER (owner ruling 2026-11-07, "B. FIX THE CLOSER FIRST"): an EXISTING test file that gains a test closes "add a test for it".

MEASURED (owner walkthrough, traces.db runs 76123f00 percent/calc.py and bce4bee6 farewell/GreeterTest.java): the candidate modified an EXISTING test file (test_calc.py, GreeterTest.java) and added a test; the closure
event read ``added_test_files: [], new_test_identities: null`` -> TEST_ADDITION_UNPROVEN. TRACED root cause: ``close_test_addition_requirements`` counted only test files ABSENT from the pre-run set, and its second route
(identities in the candidate's complete report) was dead in production - no caller of ``close_requirements_by_test_addition`` ever passed identities. The evidence is deterministic: the structural before/after of the file
(``test_delta.added_test_callables``), intersected with the identities the run's test-delta gate recorded as executed and passed; never model prose, never a file name.
"""
import subprocess

import pytest

from kriya.workflow import contract_closers as cc
from kriya.workflow.contract_compilation import compile_verification_contract
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)
from kriya.workflow.test_delta import added_test_callables, executed_test_identities

GOAL = "Make calc.percent(1, 2) -> 50.0 and add a regression test for it.\n"
PY_BEFORE = "import calc\n\n\ndef test_add():\n    assert calc.add(1, 2) == 3\n"
PY_AFTER = PY_BEFORE + "\n\ndef test_percent():\n    assert calc.percent(1, 2) == 50.0\n"
JAVA_BEFORE = "package demo;\nimport org.junit.jupiter.api.Test;\nclass GreeterTest {\n  @Test void hello() { }\n}\n"
JAVA_WITH_HELPER = "package demo;\nimport org.junit.jupiter.api.Test;\nclass GreeterTest {\n  @Test void hello() { }\n  void helper() { }\n}\n"
JAVA_AFTER = "package demo;\nimport org.junit.jupiter.api.Test;\nclass GreeterTest {\n  @Test void hello() { }\n  @Test void farewell() { }\n}\n"


def _ledger(reqs):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in reqs.requirements}, revision=1, evidence_fingerprint="cand", source="test")
    return ledger


def _close(path, before, after, *, reference=None, executed=None, goal=GOAL, candidate_files=None):
    reqs = derive_requirements(goal)
    contract = compile_verification_contract(reqs, origins=statement_origins(goal), test_files=[path])
    ledger = _ledger(reqs)
    [entry] = cc.close_test_addition_requirements(
        ledger, reqs, contract, reference_test_files=[path] if reference is None else reference,
        candidate_test_files=candidate_files if candidate_files is not None else [path], base_test_identities=None, candidate_test_identities=None,
        source="t", revision=1, read_candidate=lambda p: after.encode() if p == path else None,
        read_reference=lambda p: before.encode() if p == path and before is not None else None, executed_passed=executed)
    return entry, ledger, reqs


# ---- the owner's measured shape -------------------------------------------------------------------------------------------------------------------------------
def test_a_python_test_added_to_an_existing_test_file_closes_the_claim_with_its_identity():
    entry, ledger, reqs = _close("test_calc.py", PY_BEFORE, PY_AFTER)
    assert entry["closed"] is True and entry["reason_code"] == cc.TEST_ADDED
    assert entry["tests_gained_by_existing_files"] == {"test_calc.py": ["test_calc.test_percent"]}
    assert entry["added_test_files"] == []                                              # nothing was CREATED: this is the case the old closer could not see


def test_a_junit_method_added_to_an_existing_test_class_closes_the_claim():
    entry, _, _ = _close("src/test/java/demo/GreeterTest.java", JAVA_BEFORE, JAVA_AFTER, goal="Add farewell() to Greeter and add a JUnit test for it.\n")
    assert entry["closed"] is True and entry["tests_gained_by_existing_files"] == {"src/test/java/demo/GreeterTest.java": ["demo.GreeterTest.farewell"]}


def test_the_closer_records_the_claim_in_the_ledger_with_the_gained_identities():
    entry, ledger, reqs = _close("test_calc.py", PY_BEFORE, PY_AFTER)
    from kriya.workflow.requirements import requirement_evidence
    evidence = str(requirement_evidence(ledger, reqs))
    assert "test_percent" in evidence and cc.TEST_ADDED in evidence


# ---- negative controls: nothing else closes it -----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("after,why", [
    (PY_BEFORE, "the file did not change"),
    (PY_BEFORE.replace("assert calc.add(1, 2) == 3", "assert calc.add( 1,2 )==3"), "only reformatted"),
    (PY_BEFORE.replace("test_add", "test_addition"), "only renamed"),
    (PY_BEFORE.replace("== 3", "== 4"), "a test was modified, none added"),
    (PY_BEFORE + "\n\ndef helper():\n    return 1\n", "a non-test helper was added"),
    (PY_BEFORE + "\n\ndef test_broken(:\n", "the file no longer parses"),
])
def test_a_changed_test_file_without_a_new_test_stays_unproven(after, why):
    entry, ledger, reqs = _close("test_calc.py", PY_BEFORE, after)
    assert entry["closed"] is False and entry["reason_code"] == cc.TEST_ADDITION_UNPROVEN, why
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_java_rename_or_a_helper_is_not_a_new_test():
    for after in (JAVA_BEFORE.replace("void hello()", "void greet()"), JAVA_WITH_HELPER, JAVA_BEFORE.replace("@Test void hello()", "void hello()")):
        entry, _, _ = _close("src/test/java/demo/GreeterTest.java", JAVA_BEFORE, after, goal="Add a JUnit test for Greeter.\n")
        assert entry["closed"] is False, after


def test_a_removed_test_is_not_an_added_one():
    entry, _, _ = _close("test_calc.py", PY_AFTER, PY_BEFORE)
    assert entry["closed"] is False


def test_with_no_readable_before_the_claim_stays_open_and_says_the_pre_run_content_is_unknown():
    entry, _, _ = _close("test_calc.py", None, PY_AFTER)
    assert entry["closed"] is False and entry["reason_code"] == cc.TEST_ADDITION_UNPROVEN
    reqs = derive_requirements(GOAL)
    contract = compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=["test_calc.py"])
    [unknown] = cc.close_test_addition_requirements(_ledger(reqs), reqs, contract, reference_test_files=None, candidate_test_files=["test_calc.py"], base_test_identities=None,
                                                    candidate_test_identities=None, source="t", revision=1, read_candidate=lambda p: PY_AFTER.encode(), read_reference=lambda p: PY_BEFORE.encode())
    assert unknown["closed"] is False and "unknown" in unknown["reason"]                # an unknown reference set still fails closed


def test_without_a_reader_for_the_before_the_old_behaviour_is_unchanged():
    reqs = derive_requirements(GOAL)
    contract = compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=["test_calc.py"])
    [entry] = cc.close_test_addition_requirements(_ledger(reqs), reqs, contract, reference_test_files=["test_calc.py"], candidate_test_files=["test_calc.py"], base_test_identities=None,
                                                  candidate_test_identities=None, source="t", revision=1, read_candidate=lambda p: PY_AFTER.encode())
    assert entry["closed"] is False


# ---- execution evidence: a new test the run did not execute and pass does not close the claim ------------------------------------------------------------
def test_a_gained_test_counts_only_when_the_runs_test_delta_gate_says_it_executed_and_passed():
    assert _close("test_calc.py", PY_BEFORE, PY_AFTER, executed=["test_calc.test_percent"])[0]["closed"] is True
    entry = _close("test_calc.py", PY_BEFORE, PY_AFTER, executed=["test_calc.test_add"])[0]            # the gate ran, but not THIS test
    assert entry["closed"] is False and entry["tests_gained_by_existing_files"] == {}
    assert _close("test_calc.py", PY_BEFORE, PY_AFTER, executed=[])[0]["closed"] is False


def test_the_executed_identities_come_only_from_a_passing_test_delta_gate_of_this_attempt():
    passing = {"attempt": 2, "type": "test_delta", "success": True, "test_delta": {"executed": ["a.test_x", "a.test_y"]}}
    assert executed_test_identities([passing], 2) == ["a.test_x", "a.test_y"]
    assert executed_test_identities([passing], 1) is None                                                # another attempt
    assert executed_test_identities([{**passing, "success": False}], 2) is None                          # the gate did not pass
    assert executed_test_identities([{**passing, "type": "regression_test"}], 2) is None
    assert executed_test_identities(["junk", None, {"type": "test_delta", "attempt": 2, "success": True, "test_delta": "x"}], 2) is None
    assert executed_test_identities([], 2) is None


# ---- a test added to a DIFFERENT existing file: this closer does not look at the goal's wording, exactly as for a created file ---------------------------------
def test_a_new_test_in_another_existing_file_is_judged_by_the_same_rule_and_the_named_test_oracle_is_untouched():
    goal = "Add a regression test for calc in tests/test_named.py.\n"
    reqs = derive_requirements(goal)
    contract = compile_verification_contract(reqs, origins=statement_origins(goal), test_files=["tests/test_named.py", "test_other.py"])
    ledger = _ledger(reqs)
    contents = {"test_other.py": (PY_BEFORE, PY_AFTER)}
    entries = cc.close_test_addition_requirements(
        ledger, reqs, contract, reference_test_files=["tests/test_named.py", "test_other.py"], candidate_test_files=["tests/test_named.py", "test_other.py"], base_test_identities=None,
        candidate_test_identities=None, source="t", revision=1, read_candidate=lambda p: contents.get(p, (None, PY_BEFORE))[1].encode(),
        read_reference=lambda p: contents.get(p, (PY_BEFORE, None))[0].encode())
    for entry in entries:                       # the named file is unchanged; the new test sits elsewhere: the test_addition claim itself is about "a test was added"
        assert entry["tests_gained_by_existing_files"] == {"test_other.py": ["test_other.test_percent"]}
    from kriya.workflow.contract_compilation import CLOSER_NAMED_TESTS
    assert CLOSER_NAMED_TESTS in contract.entry("REQ-1").closers                                         # the named-test oracle still owns the named file's claim (its own tests cover it)


# ---- the pure structural comparison ---------------------------------------------------------------------------------------------------------------------------
def test_added_test_callables_is_none_without_structural_evidence():
    assert added_test_callables("test_calc.py", None, PY_AFTER) is None                                 # no baseline: a created file, not this question
    assert added_test_callables("notes.txt", "a", "b") is None                                           # not a structural language
    assert added_test_callables("test_calc.py", PY_BEFORE, "def test_x(:\n") is None


def test_one_disappeared_test_explains_one_new_name_only():
    before = "def test_a():\n    assert 1\n"
    after = "def test_b():\n    assert 1\n\n\ndef test_c():\n    assert 1\n"
    assert [c.identity for c in added_test_callables("test_m.py", before, after)] == ["test_m.test_c"]


# ---- through the real wrapper: a sandbox candidate against its untouched workspace, and an in-place candidate against the run base ---------------------------------
def _git(cwd, *args):
    return subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_calc.py").write_text(PY_BEFORE)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _wrapper(candidate, workspace, **kwargs):
    from kriya.workflow import workflow

    reqs = derive_requirements(GOAL)
    contract = compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=["test_calc.py"], project_language="python")
    ledger = _ledger(reqs)
    return workflow.close_requirements_by_test_addition(ledger, reqs, contract, str(candidate), str(workspace), revision=1, **kwargs)[0]


def test_through_the_real_wrapper_a_sandbox_candidate_is_compared_with_the_untouched_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    candidate = tmp_path / "sandbox"
    candidate.mkdir()
    (candidate / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef percent(a, b):\n    return a / b * 100\n")
    (candidate / "test_calc.py").write_text(PY_AFTER)
    entry = _wrapper(candidate, workspace)
    assert entry["closed"] is True and entry["tests_gained_by_existing_files"] == {"test_calc.py": ["test_calc.test_percent"]}
    (candidate / "test_calc.py").write_text(PY_BEFORE)
    assert _wrapper(candidate, workspace)["closed"] is False
    assert _wrapper(candidate, workspace, executed_passed=["test_calc.test_add"])["closed"] is False


def test_through_the_real_wrapper_an_in_place_candidate_is_compared_with_the_base_revision(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "test_calc.py").write_text(PY_AFTER)                                                   # the run edited the tracked file in place
    entry = _wrapper(workspace, workspace)
    assert entry["closed"] is True and entry["tests_gained_by_existing_files"] == {"test_calc.py": ["test_calc.test_percent"]}
