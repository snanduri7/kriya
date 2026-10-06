"""B2-COV: evidence closes a claim only at the strength it demonstrates
(kriya/workflow/requirements.py behavior_strength, _effective_closure;
kriya/workflow/acceptance_oracle.py close_requirements_with_acceptance).

A trusted counterexample disproves a general rule; finite passing examples
never prove one. Every acceptance run here is real (Kriya's runner); A1 and A2
run on the real freezegun code (tests/_b2a_fixtures.py), A2 on the candidate
Kriya applied as a SUCCESS in the post-B2-a live run.
"""
import hashlib

import pytest
from _b2a_fixtures import (
    A1_ACCEPTANCE,
    A1_GOAL,
    A2_ACCEPTANCE,
    A2_GOAL,
    A2_LIVE_CANDIDATE_DIGEST,
    CALC,
    CALC_ACCEPTANCE,
    a2_live_api,
    a2_project,
    calc_project,
    freezegun_project,
)
from _fs1c1_a1_specimen import TARGET, TRACKED, live_c0_judgment
from test_b2a_acceptance_oracle import _artifact, _close, _ledger

from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.obligations import ObligationStatus
from kriya.workflow.requirements import (
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_EXAMPLES,
    BEHAVIOR_GENERAL,
    REGRESSION_PRESERVATION,
    RequirementOutcome,
    behavior_strength,
    blocking_requirements,
    close_mutation_scope_requirements,
    close_unverified_requirements_with_named_tests,
    derive_requirements,
    record_requirement_claim,
    record_requirement_closure,
    requirement_evidence,
    requirement_outcomes,
)

PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}
EXACT_GOAL = "Add a double(x) function to calc/__init__.py so that double(5) returns 10.\n"
GENERAL_GOAL = "Add a double(x) function to calc/__init__.py that returns 2 * x for any number x.\n"
TWO_CASES = CALC_ACCEPTANCE + ('\n\n@pytest.mark.kriya_requirement("REQ-1")\n'
                               "def test_double_of_zero():\n    assert double(0) == 0\n")


# ---------------------------------------------------------------- the classifier (deterministic, conservative)

@pytest.mark.parametrize("text, strength", [
    ("Add a double(x) function so that double(5) returns 10.", BEHAVIOR_EXACT),
    ("calling foo(None) raises ValueError", BEHAVIOR_EXACT),
    ("`f(0)` returns 1 and `f(1)` returns 2", BEHAVIOR_EXACT),
    ("Create greeting.py with a greet(name) function so that greet('Ann') returns 'Hello, Ann'", BEHAVIOR_EXACT),
    # general / universal / domain rules
    ("accept strings of the form +HH:MM or -HH:MM, so that parse('+01:00') returns 60", BEHAVIOR_GENERAL),
    ("raise ValueError for any other string, so that parse('x') raises ValueError", BEHAVIOR_GENERAL),
    ("all inputs are trimmed, so that trim(' a ') returns 'a'", BEHAVIOR_GENERAL),
    ("every value is doubled, so that double(5) returns 10", BEHAVIOR_GENERAL),
    ("only values matching the pattern pass, so that check('a') returns True", BEHAVIOR_GENERAL),
    ("it must never return None, so that get(1) returns 2", BEHAVIOR_GENERAL),
    ("Add a double(x) function that returns 2 * x, so that double(5) returns 10.", BEHAVIOR_GENERAL),  # formula
    ("greet(name) returns 'Hello, <name>!'", BEHAVIOR_GENERAL),  # placeholder
    ("greet returns Hello <name>, so that greet('Ann') returns 'Hello Ann'", BEHAVIOR_GENERAL),  # even with a case
    # 6: nothing concrete stated -> uncertain -> GENERAL
    ("m1.py defines VALUE", BEHAVIOR_GENERAL),
    ("Make the cache faster.", BEHAVIOR_GENERAL),
])
def test_claim_strength_is_decided_deterministically_from_the_users_words(text, strength):
    assert behavior_strength(text, regression_covered=True)[0] == strength


def test_a1_is_enumerated_and_a2_is_a_general_rule():
    a1 = derive_requirements(A1_GOAL).requirements[0].text
    a2 = derive_requirements(A2_GOAL).requirements[0].text
    assert behavior_strength(a1, regression_covered=True) == (BEHAVIOR_EXACT, {
        "strength": BEHAVIOR_EXACT, "reasons": [], "examples": ["freeze_time(0)", "freeze_time(86400.5)"]})
    strength, why = behavior_strength(a2, regression_covered=True)
    assert strength == BEHAVIOR_GENERAL
    assert any("of the form" in r for r in why["reasons"]) and any("any" in r for r in why["reasons"])
    # A1's "every input it already accepts keeps working" is the regression claim's, not behaviour's -
    # unless no regression oracle covers it.
    assert behavior_strength(a1, regression_covered=False)[0] == BEHAVIOR_GENERAL


# ---------------------------------------------------------------- 1, 2: enumerated claims

def test_1_exact_claim_closes_when_all_its_cases_pass(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(EXACT_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, TWO_CASES, EXACT_GOAL), root)
    assert (attempt["reason_code"], attempt["strength"], attempt["closed"]) == (
        ao.ACCEPTANCE_PASSED, BEHAVIOR_EXACT, True)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_2_exact_claim_is_violated_when_one_case_fails(tmp_path):
    root = calc_project(tmp_path / "ws", True, {"calc/__init__.py": "def double(x):\n    return 2 * x if x else 1\n"})
    reqs, ledger = _ledger(EXACT_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, TWO_CASES, EXACT_GOAL), root)
    assert attempt["case_results"]["kriya_acceptance.test_double_doubles"] == ["passed"]
    assert attempt["reason_code"] == ao.ACCEPTANCE_VIOLATED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED


# ---------------------------------------------------------------- 3, 4, 6: general claims

def test_3_finite_passing_cases_never_close_a_general_claim(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, TWO_CASES, GENERAL_GOAL), root)
    assert attempt["case_results"] == {"kriya_acceptance.test_double_doubles": ["passed"],
                                       "kriya_acceptance.test_double_of_zero": ["passed"]}
    assert (attempt["reason_code"], attempt["strength"], attempt["closed"]) == (
        ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN, BEHAVIOR_GENERAL, False)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    claims = requirement_evidence(ledger, reqs)["REQ-1"]["claims"]
    assert claims[BEHAVIOR] is None and claims[BEHAVIOR_EXAMPLES]["reason_code"] == ao.ACCEPTANCE_PASSED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


def test_4_one_trusted_counterexample_violates_a_general_claim(tmp_path):
    """The owner's example: the preserved A2 candidate accepts "+5:30"."""
    counterexample = A2_ACCEPTANCE + ('\n\n@pytest.mark.kriya_requirement("REQ-1")\n'
                                      "def test_one_digit_hour_is_another_string():\n"
                                      "    with pytest.raises(ValueError):\n"
                                      "        _parse_tz_offset(\"+5:30\")\n")
    root = a2_project(tmp_path / "ws")
    reqs, ledger = _ledger(A2_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, counterexample, A2_GOAL), root,
                       test_files=["tests/test_operations.py"])
    assert attempt["case_results"]["kriya_acceptance.test_one_digit_hour_is_another_string"] == ["failed"]
    assert attempt["reason_code"] == ao.ACCEPTANCE_VIOLATED and attempt["strength"] == BEHAVIOR_GENERAL
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED


def test_6_an_uncertain_statement_is_general_and_finite_cases_cannot_close_it(tmp_path):
    goal = "Add a double function to calc/__init__.py.\n"
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(goal)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, goal), root)
    assert attempt["reason_code"] == ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- 5, 9, 12: A2 (examples + general rule)

def _a2(tmp_path):
    assert hashlib.sha256(a2_live_api().encode()).hexdigest() == A2_LIVE_CANDIDATE_DIGEST
    root = a2_project(tmp_path / "ws")
    reqs, ledger = _ledger(A2_GOAL, candidate="a2-live-candidate")
    close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence={"actual_paths": [TARGET], "foreign_paths": []},
        source="test", revision=1)
    [acceptance] = _close(ledger, reqs, _artifact(tmp_path, A2_ACCEPTANCE, A2_GOAL), root,
                          test_files=["tests/test_operations.py"], paths=[TARGET])
    [named] = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=["tests/test_operations.py"], modified=[TARGET], judge=live_c0_judgment,
        source="test", revision=1)
    return reqs, ledger, acceptance, named


def test_12_the_preserved_a2_candidate_can_no_longer_succeed(tmp_path):
    reqs, ledger, acceptance, named = _a2(tmp_path)
    # every operator case still executes and passes on it ...
    assert set(sum(acceptance["case_results"].values(), [])) == {"passed"} and len(acceptance["case_results"]) == 3
    # ... but the statement is a general rule they cannot prove
    assert acceptance["reason_code"] == ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN
    assert named["regression_preserved"] is True and named["closed"] is False  # 9: C0 does not upgrade it
    assert requirement_outcomes(ledger, reqs) == {"REQ-1": RequirementOutcome.UNVERIFIED,
                                                  "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


def test_5_the_enumerated_part_is_proven_and_the_general_rule_stays_open(tmp_path):
    reqs, ledger, _, _ = _a2(tmp_path)
    claims = requirement_evidence(ledger, reqs)["REQ-1"]["claims"]
    assert claims[BEHAVIOR_EXAMPLES]["reason_code"] == ao.ACCEPTANCE_PASSED          # the examples: proven
    assert claims[REGRESSION_PRESERVATION]["method"] == "named_test_oracle"          # preservation: proven
    assert claims[BEHAVIOR] is None                                                  # the rule: open
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- 7, 8, 10: nothing upgrades a general claim

def test_7_a_model_satisfied_verdict_cannot_upgrade_the_general_claim(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=RequirementOutcome.SATISFIED)
    _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, GENERAL_GOAL), root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


@pytest.mark.parametrize("method", ["named_test_oracle", "named_test_run", "candidate_tests", "model_judgment"])
def test_8_candidate_tests_and_other_producers_cannot_record_behaviour(method):
    reqs, ledger = _ledger(GENERAL_GOAL)
    for claim in (BEHAVIOR, BEHAVIOR_EXAMPLES):
        with pytest.raises(ValueError):
            record_requirement_claim(ledger, reqs, "REQ-1", claim, evidence_id="cand", method=method, detail={},
                                     source="test", revision=1)


def test_8_supporting_examples_never_stand_in_for_the_behaviour_claim():
    reqs, ledger = _ledger(EXACT_GOAL)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR_EXAMPLES, evidence_id="cand", method="acceptance_oracle",
                             detail={"required_claims": [BEHAVIOR]}, source="test", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("record", ["claim", "whole_closure"])
def test_10_a_resumed_pre_b2cov_broad_closure_closes_nothing(record):
    """What a B2-a (pre-B2-COV) checkpoint holds for A2: a SATISFIED acceptance
    BEHAVIOR claim (no strength) plus the C0 regression claim - or a whole
    closure record by the acceptance method."""
    reqs, ledger = _ledger(A2_GOAL)
    if record == "claim":
        record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                                 detail={"required_claims": [BEHAVIOR, REGRESSION_PRESERVATION]}, source="old",
                                 revision=1)
        record_requirement_claim(ledger, reqs, "REQ-1", REGRESSION_PRESERVATION, evidence_id="cand",
                                 method="named_test_oracle", detail={"tests": ["tests/test_operations.py"]},
                                 source="old", revision=1)
    else:
        record_requirement_closure(ledger, reqs, "REQ-1", evidence_id="cand", method="acceptance_oracle",
                                   detail={"required_claims": [BEHAVIOR, REGRESSION_PRESERVATION]}, source="old",
                                   revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_10_a_record_without_the_regression_claim_cannot_cover_a_preservation_clause():
    """A1's "every input it already accepts keeps working exactly as before" is
    the regression claim's to prove; a behaviour record that does not require
    that claim leaves the clause unproven, so the statement is GENERAL for it."""
    reqs, ledger = _ledger(A1_GOAL)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                             detail={"required_claims": [BEHAVIOR]}, source="old", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_10_the_same_records_still_close_an_enumerated_statement():
    reqs, ledger = _ledger(EXACT_GOAL)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                             detail={"required_claims": [BEHAVIOR]}, source="old", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE


# ---------------------------------------------------------------- 11: A1 stays closable

def test_11_a1_enumerated_behaviour_is_still_closed_by_b2(tmp_path):
    root = freezegun_project(tmp_path / "ws", "correct")
    reqs, ledger = _ledger(A1_GOAL, candidate="a1")
    close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence={"actual_paths": [TARGET], "foreign_paths": []},
        source="test", revision=1)
    [acceptance] = _close(ledger, reqs, _artifact(tmp_path, A1_ACCEPTANCE, A1_GOAL), root,
                          test_files=["tests/test_operations.py"], paths=[TARGET])
    close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=["tests/test_operations.py"], modified=[TARGET], judge=live_c0_judgment,
        source="test", revision=1)
    assert (acceptance["reason_code"], acceptance["strength"]) == (ao.ACCEPTANCE_PASSED, BEHAVIOR_EXACT)
    assert requirement_outcomes(ledger, reqs) == {"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE,
                                                  "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert not blocking_requirements(ledger, reqs, **PRODUCTION)


# ---------------------------------------------------------------- the counterexample overrides earlier support

def test_a_later_counterexample_revokes_earlier_supporting_evidence(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(GENERAL_GOAL)
    _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, GENERAL_GOAL), root)
    (root / "calc" / "__init__.py").write_text(CALC[False])
    _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, GENERAL_GOAL), root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED
    assert ledger.current("requirement.REQ-1.claim.behavior").status is ObligationStatus.VIOLATED
