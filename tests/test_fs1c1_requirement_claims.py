"""FS-1C1: a named pre-existing test closes only REGRESSION_PRESERVATION.

A requirement statement can claim new BEHAVIOR and REGRESSION_PRESERVATION
("implement X, keeping test T passing"). C0's oracle passed before X existed,
so it proves only that T still passes. The requirement closes only when every
claim it makes is closed by its own kind of evidence for the same candidate;
BEHAVIOR has no producer today (B2/B3), so such a requirement stays
UNVERIFIED and production blocks it. Controls 1-9 of the owner's brief, the
live A1 false success (tests/_fs1c1_a1_specimen.py), and the direct workflow
end to end with a real C0 oracle."""
from unittest.mock import patch

import pytest
from _fs1c1_a1_specimen import outcomes, production_blocking, terminal_requirements
from test_prd020_requirement_lineage import _engine, _events, _git_base, _real_named_test_runs, _verdicts_json

from kriya.workflow.named_test_oracle import CLOSURE_METHOD, ORACLE_PASSED, OracleJudgment
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    BEHAVIOR,
    REGRESSION_PRESERVATION,
    REQUIREMENT_BEHAVIOR_UNVERIFIED,
    RequirementOutcome,
    blocking_requirements,
    close_unverified_requirements_with_named_tests,
    derive_requirements,
    record_requirement_claim,
    record_requirement_closure,
    record_requirement_verdicts,
    requirement_claims,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
)

PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}
LEGACY = "tests/test_legacy.py"
PURE = "tests/test_legacy.py must continue to pass\n"
# Enumerated behaviour (B2-COV: an EXACT statement acceptance cases can close) plus preservation.
COMPOUND = "Add a scale(x) function to app.py so that scale(3) returns 6, keeping tests/test_legacy.py passing\n"


def _judge(named):
    return OracleJudgment(ORACLE_PASSED, "", {"method": CLOSURE_METHOD, "tests": list(named), "base_revision": "b"})


def _close(goal, *, modified=(), model=RequirementOutcome.SATISFIED, judge=_judge):
    reqs = derive_requirements(goal)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (model, "")}, revision=1, evidence_fingerprint="cand",
                                source="test")
    [attempt] = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=[LEGACY], modified=list(modified), judge=judge, source="test", revision=1)
    return reqs, ledger, attempt


# ---------------------------------------------------------------- claim typing

@pytest.mark.parametrize("text, named, claims", [
    ("tests/test_legacy.py must continue to pass", [LEGACY], (REGRESSION_PRESERVATION,)),
    ("Behaviour stays compatible with the legacy check test_legacy", [LEGACY], (REGRESSION_PRESERVATION,)),
    ("PricingTest keeps passing", ["src/test/java/PricingTest.java"], (REGRESSION_PRESERVATION,)),
    (COMPOUND, [LEGACY], (BEHAVIOR, REGRESSION_PRESERVATION)),
    ("Make tests/test_legacy.py pass", [LEGACY], (REGRESSION_PRESERVATION,)),
    ("tests/test_legacy.py keeps passing with the new cache enabled", [LEGACY], (BEHAVIOR, REGRESSION_PRESERVATION)),
    ("Add a scale(x) function to app.py", [], (BEHAVIOR,)),
])
def test_a_statement_claims_behaviour_whenever_anything_but_preservation_remains(text, named, claims):
    assert requirement_claims(text, named) == claims


# ---------------------------------------------------------------- controls 1-7

def test_1_a_pure_regression_requirement_is_closed_by_c0():
    reqs, ledger, attempt = _close(PURE)
    assert attempt["closed"] is True and attempt["claims"] == [REGRESSION_PRESERVATION]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert requirement_evidence(ledger, reqs)["REQ-1"]["claim"] == REGRESSION_PRESERVATION


def test_2_c0_closes_only_the_regression_claim_of_a_compound_requirement():
    reqs, ledger, attempt = _close(COMPOUND)
    assert attempt["closed"] is False and attempt["regression_preserved"] is True
    assert attempt["reason_code"] == REQUIREMENT_BEHAVIOR_UNVERIFIED
    assert attempt["claims"] == [BEHAVIOR, REGRESSION_PRESERVATION]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    claims = requirement_evidence(ledger, reqs)["REQ-1"]
    assert claims["closed"] is False and claims["claims"][BEHAVIOR] is None
    assert claims["claims"][REGRESSION_PRESERVATION]["tests"] == [LEGACY]


def test_3_and_5_wrong_behaviour_with_a_passing_regression_test_and_a_model_satisfied_never_succeeds():
    reqs, ledger, _ = _close(COMPOUND, model=RequirementOutcome.SATISFIED)
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


def test_4_behaviour_closes_only_with_independent_acceptance_evidence_and_the_regression_claim():
    reqs, ledger, _ = _close(COMPOUND)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                             detail={"cases": ["scale(2) == 4"]}, source="test", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert set(requirement_evidence(ledger, reqs)["REQ-1"]["claims"]) == {BEHAVIOR, REGRESSION_PRESERVATION}
    # Behaviour evidence alone is not enough either: the statement also claims preservation.
    reqs, ledger = derive_requirements(COMPOUND), ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.UNVERIFIED, "")}, revision=1,
                                evidence_fingerprint="cand", source="test")
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="acceptance_oracle",
                             detail={}, source="test", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    # Claims bind to the candidate: evidence for another one closes nothing.
    reqs, ledger, _ = _close(COMPOUND)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="other", method="acceptance_oracle",
                             detail={}, source="test", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("method", ["named_test_oracle", "named_test_run", "candidate_tests", "model_judgment"])
def test_6_only_acceptance_evidence_may_close_behaviour(method):
    reqs, ledger, _ = _close(COMPOUND)
    with pytest.raises(ValueError):
        record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method=method, detail={},
                                 source="test", revision=1)
    with pytest.raises(ValueError):
        record_requirement_claim(ledger, reqs, "REQ-1", REGRESSION_PRESERVATION, evidence_id="cand",
                                 method="acceptance_oracle", detail={}, source="test", revision=1)


def test_6_a_named_test_the_candidate_wrote_closes_nothing():
    reqs, ledger, attempt = _close(COMPOUND, modified=[LEGACY],
                                   judge=lambda named: pytest.fail("the judge must not run"))
    assert "written or changed by this candidate" in attempt["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_7_mutation_scope_closure_is_unchanged_on_the_a1_specimen():
    reqs, ledger, attempts = terminal_requirements()
    [scope] = [a for a in attempts if a.get("kind") == "MUTATION_SCOPE"]
    assert scope["closed"] is True and outcomes(reqs, ledger)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE


# ---------------------------------------------------------------- 9: resume cannot resurrect broad C0 closure

def test_9_a_resumed_whole_named_test_closure_of_a_compound_requirement_closes_nothing():
    """A pre-FS-1C1 checkpoint holds C0's whole-requirement closure record."""
    for method in ("named_test_oracle", "named_test_run"):
        reqs, ledger = derive_requirements(COMPOUND), ObligationLedger()
        seed_requirement_obligations(ledger, reqs)
        record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "")}, revision=1,
                                    evidence_fingerprint="cand", source="test")
        record_requirement_closure(ledger, reqs, "REQ-1", evidence_id="cand", method=method,
                                   detail={"tests": [LEGACY], "passed": True}, source="test", revision=1)
        assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
        assert "REQ-1" not in requirement_evidence(ledger, reqs)


def test_9_a_resumed_named_test_closure_of_a_pure_requirement_still_closes_and_one_without_tests_fails_closed():
    for detail, closed in (({"tests": [LEGACY]}, True), ({}, False)):
        reqs, ledger = derive_requirements(PURE), ObligationLedger()
        seed_requirement_obligations(ledger, reqs)
        record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "")}, revision=1,
                                    evidence_fingerprint="cand", source="test")
        record_requirement_closure(ledger, reqs, "REQ-1", evidence_id="cand", method="named_test_run",
                                   detail=detail, source="test", revision=1)
        expected = RequirementOutcome.CLOSED_BY_EVIDENCE if closed else RequirementOutcome.UNVERIFIED
        assert requirement_outcomes(ledger, reqs)["REQ-1"] is expected


# ---------------------------------------------------------------- the live A1 false success

def test_a1_c0_closes_only_regression_preservation_and_the_run_cannot_succeed():
    reqs, ledger, attempts = terminal_requirements()
    assert outcomes(reqs, ledger) == {"REQ-1": RequirementOutcome.UNVERIFIED,
                                      "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert production_blocking(reqs, ledger) == ["REQ-1"]
    [named] = [a for a in attempts if a.get("tests")]
    assert named["regression_preserved"] is True and named["reason_code"] == REQUIREMENT_BEHAVIOR_UNVERIFIED


# ---------------------------------------------------------------- end to end: direct workflow, real C0

# The _engine fixture's scripted candidate: greeting.py returning 'Hello, <name>'.
WRONG_BEHAVIOUR_GOAL = ("Create greeting.py with a greet(name) function that returns 'Hello, <name>!' "
                        "(with an exclamation mark), keeping tests/test_legacy.py passing\n")


@pytest.mark.parametrize("goal, succeeds", [
    (WRONG_BEHAVIOUR_GOAL, False),
    ("Behaviour stays compatible with the legacy check test_legacy\n", True),
])
@pytest.mark.asyncio
async def test_end_to_end_a_passing_regression_oracle_never_carries_new_behaviour(tmp_path, goal, succeeds):
    """The verifier says "satisfied" and the real C0 oracle passes on the
    candidate (greet returns 'Hello, <name>', without the '!' the compound goal
    asks for). A goal that only asks for preservation succeeds; one that also
    asks for new behaviour is never applied (production policy)."""
    cfg, engine, _ = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                             requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _git_base(workspace, {LEGACY: "def test_legacy():\n    assert True\n"})
    runs = []
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=_real_named_test_runs(runs, {"success": True, "output": "3 passed"})):
        res = await engine.run_generation_workflow(goal=goal, workspace_path=str(workspace))
    assert res["quality_gates_passed"] is succeeds
    assert (workspace / "greeting.py").exists() is succeeds            # never applied without success
    if succeeds:
        [closure] = [c for event in _events(cfg, "requirement.closure") for c in event["closures"] if c.get("tests")][:1]
        assert closure["reason_code"] == "ORACLE_PASSED"
    else:
        # REQUIREMENT-CLOSURE-PLAIN-GOAL-001: the new-behaviour claim has no closer (the named test proves only
        # preservation), so under the production policy the run is refused before any model call - the C0 oracle
        # can never be read as carrying the behaviour because it never has the chance to.
        assert res["failure_category"] == "goal_insufficient_for_verification"
        assert "without an acceptance case" in res["requirements_admission"]["residual"][0]["why"]
        assert runs == [] and _events(cfg, "requirement.closure") == []
