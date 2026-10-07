"""REQUIREMENT-CLOSURE-PLAIN-GOAL-001: a plain-language goal is valid requirement authority, every mandatory requirement
needs a deterministic closer, model judgment closes nothing, and a goal that cannot be verified is refused before any
model call.

Measured (blind cohort T4/T5, production profile): every goal-derived requirement stayed MODEL_CLAIMED/UNVERIFIED, the
production policy blocks UNVERIFIED, and no deterministic closer could fire (no acceptance file, the named tests refused,
no scope statement), so GENUINE_SUCCESS was structurally unreachable - discovered only after 7 to 11 model calls.

Owner decisions (2026-10-07): residual mandatory requirements stay blocking; B2-COV unchanged; improve the closers the
repository gives a plain goal; refuse early (GOAL_INSUFFICIENT_FOR_VERIFICATION) naming the requirement, why, and the
accepted forms. New closers: the candidate's own complete, green full suite closes a whole-suite preservation statement;
the run's mutation record decides a test-immutability constraint.
"""
import subprocess

import pytest
from _edit_protocol_harness import UNLOCALIZED_GOAL, make_config, run_edit_protocol
from _reg_r1_fixture import validator_for, write_project
from test_prd020_milestone_requirements import _probe

from kriya.config.config import AutonomyConfig
from kriya.workflow import workflow as wf
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    ACCEPTED_GOAL_FORMS,
    BEHAVIOR,
    CLOSER_ACCEPTANCE,
    CLOSER_MUTATION_SCOPE,
    CLOSER_NAMED_TESTS,
    FULL_REGRESSION_METHOD,
    GOAL_INSUFFICIENT_FOR_VERIFICATION,
    REGRESSION_PRESERVATION,
    SUITE_PRESERVATION,
    TEST_IMMUTABILITY,
    GoalAdmissionError,
    RequirementOutcome,
    admission_gap,
    close_suite_preservation_requirements,
    close_test_immutability_requirements,
    derive_requirements,
    deterministic_closers,
    is_suite_preservation_requirement,
    is_test_immutability_requirement,
    record_requirement_verdicts,
    requirement_claims,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
)

SUITE = "Every existing test must keep passing unchanged (./gradlew :json-path:test)."
IMMUTABLE = "Do not change any existing test."
SCOPE = "Do not modify any other file."


def unverified_ledger(reqs, evidence_id="candidate-1"):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "cannot confirm from code")
                                               for r in reqs.requirements},
                                revision=1, evidence_fingerprint=evidence_id, source="test")
    return ledger


# ---------------------------------------------------------------- recognizers (closed vocabularies, whole statements)
@pytest.mark.parametrize("text", [
    SUITE, "All existing tests must keep passing.", "The existing test suite stays green.",
    "Keep all existing tests green", 'Every existing test must keep passing unchanged ("pytest").',
])
def test_01_whole_suite_preservation_statements_are_recognized_and_claim_preservation_only(text):
    assert is_suite_preservation_requirement(text)
    assert requirement_claims(text, []) == (REGRESSION_PRESERVATION,)
    assert not is_test_immutability_requirement(text)


@pytest.mark.parametrize("text", [
    "Every existing public API and every existing test must keep passing unchanged.",   # API preservation: no closer
    "Do not change the public API, the TTLCache constructor, or existing tests.",         # compound constraint
    "pytest is green",                                                                   # names no suite
    "tests/test_legacy.py must keep passing",                                            # a named test: its own closer
    "Add three built-in string functions: lower, upper and trim.",
])
def test_02_anything_beyond_the_closed_vocabulary_is_not_a_suite_statement(text):
    assert not is_suite_preservation_requirement(text)
    assert BEHAVIOR in requirement_claims(text, [])


@pytest.mark.parametrize("text, expected", [
    (IMMUTABLE, True), ("Do not modify existing tests", True), ("Never delete any of the existing test files under tests/", True),
    ("Please don't touch the existing unit tests.", True), (SCOPE, False), ("Do not change the tests or the README.", False),
])
def test_03_test_immutability_statements(text, expected):
    assert is_test_immutability_requirement(text) is expected


# ---------------------------------------------------------------- admission: every mandatory requirement needs a closer
def test_04_admission_names_the_residual_requirement_and_the_accepted_forms():
    reqs = derive_requirements("Make lower() lower-case its argument.\n" + SUITE)
    closers, residual = deterministic_closers(reqs, test_files=["tests/test_a.py"])
    assert closers == {"REQ-1": [], "REQ-2": [SUITE_PRESERVATION, TEST_IMMUTABILITY]}  # "unchanged": both closers
    assert [r["id"] for r in residual] == ["REQ-1"] and "model judgment never closes it" in residual[0]["why"]
    refusal = admission_gap(reqs, test_files=["tests/test_a.py"])
    assert isinstance(refusal, GoalAdmissionError) and refusal.reason_code == GOAL_INSUFFICIENT_FOR_VERIFICATION
    assert refusal.message.startswith("GOAL_INSUFFICIENT_FOR_VERIFICATION: 1 mandatory requirement(s)")
    assert "REQ-1: 'Make lower() lower-case its argument.'" in refusal.message
    assert all(form in refusal.message for form in ACCEPTED_GOAL_FORMS)
    assert refusal.to_dict()["residual"][0]["id"] == "REQ-1"


def test_05_a_goal_made_of_closable_statements_is_admitted():
    goal = ("Fix the expiry boundary in src/cache.py.\n\n" "tests/test_a.py must keep passing.\n\n" + SUITE + "\n\n"
            + IMMUTABLE + "\n\n" + SCOPE)
    reqs = derive_requirements(goal)
    closers, residual = deterministic_closers(reqs, test_files=["tests/test_a.py"], tracked_paths=["src/cache.py", "tests/test_a.py"])
    # REQ-1 asks for behaviour with no acceptance case: residual even though the file is named
    assert [r["id"] for r in residual] == ["REQ-1"]
    assert closers["REQ-2"] == [CLOSER_NAMED_TESTS] and closers["REQ-3"] == [SUITE_PRESERVATION, TEST_IMMUTABILITY]
    assert closers["REQ-4"] == [TEST_IMMUTABILITY] and closers["REQ-5"] == [CLOSER_MUTATION_SCOPE]
    # with an acceptance case covering REQ-1 the goal is admitted; a scope statement without a named file is not
    assert admission_gap(reqs, test_files=["tests/test_a.py"], acceptance_ids=["REQ-1"],
                         tracked_paths=["src/cache.py", "tests/test_a.py"]) is None
    assert deterministic_closers(reqs, test_files=["tests/test_a.py"], acceptance_ids=["REQ-1"])[0]["REQ-1"] == [CLOSER_ACCEPTANCE]
    scope_only = derive_requirements("Keep things tidy.\n" + SCOPE)
    _, residual = deterministic_closers(scope_only, test_files=[], tracked_paths=["a.py"])
    assert {r["id"] for r in residual} == {"REQ-1", "REQ-2"} and "no referent" in residual[1]["why"]


def test_06b_a_named_test_preservation_statement_may_start_with_every(workspace_files=("tests/test_a.py", "tests/test_b.py")):
    # cohort-2 review F3: "every" is a quantifier of the preservation statement, not a behaviour claim
    text = "Every existing test in tests/test_a.py and tests/test_b.py must keep passing unchanged."
    assert requirement_claims(text, list(workspace_files)) == (REGRESSION_PRESERVATION,)
    closers, residual = deterministic_closers(derive_requirements(text), test_files=list(workspace_files))
    assert closers["REQ-1"] == [CLOSER_NAMED_TESTS] and residual == []


def test_06_a_named_test_with_a_behaviour_claim_still_needs_acceptance():
    reqs = derive_requirements("tests/test_legacy.py keeps passing with the new cache enabled")
    closers, residual = deterministic_closers(reqs, test_files=["tests/test_legacy.py"])
    assert closers["REQ-1"] == [CLOSER_NAMED_TESTS] and residual and "without an acceptance case" in residual[0]["why"]


# ---------------------------------------------------------------- closers: the candidate's own full suite
def test_07_a_complete_green_full_suite_closes_the_suite_statement(tmp_path):
    root = write_project(tmp_path / "ws", "def test_a():\n    assert True\n\ndef test_b():\n    assert 1 == 1\n")
    reqs = derive_requirements("Fix it.\nAll existing tests must keep passing.")  # no "unchanged": the suite alone closes it
    ledger = unverified_ledger(reqs)
    clean = {"available": True, "changed_test_files": [], "missing_test_files": [], "changed_trust_surface": []}
    attempts = close_suite_preservation_requirements(
        ledger, reqs, test_files=["tests/test_suite.py"], run_suite=lambda: validator_for(root).run_tests(),
        source="t", revision=1, test_immutability=clean)
    [entry] = attempts
    assert entry["requirement"] == "REQ-2" and entry["closed"] is True and entry["test_execution"]["cases"] == 2
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED  # the behaviour statement is untouched
    evidence = requirement_evidence(ledger, reqs)["REQ-2"]
    closure = evidence.get("closure") or evidence
    assert closure.get("method") == FULL_REGRESSION_METHOD or str(evidence).count(FULL_REGRESSION_METHOD)


def test_08_a_failing_incomplete_or_empty_suite_closes_nothing(tmp_path):
    reqs = derive_requirements(SUITE)
    failing = write_project(tmp_path / "failing", "def test_a():\n    assert False\n")
    ledger = unverified_ledger(reqs)
    [entry] = close_suite_preservation_requirements(ledger, reqs, test_files=[], run_suite=lambda: validator_for(failing).run_tests(),
                                                    source="t", revision=1)
    assert entry["closed"] is False and "did not pass" in entry["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    empty = write_project(tmp_path / "empty", "x = 1\n")
    ledger = unverified_ledger(reqs)
    [entry] = close_suite_preservation_requirements(ledger, reqs, test_files=[], run_suite=lambda: validator_for(empty).run_tests(),
                                                    source="t", revision=1)
    assert entry["closed"] is False and ("zero tests" in entry["reason"] or "did not pass" in entry["reason"])
    # a gate that says success without COMPLETE structured evidence proves nothing
    ledger = unverified_ledger(reqs)
    stub = {"success": True, "output": "2 passed", "test_execution": {"gate_id": "g", "runner": "pytest", "workspace": "w",
                                                                       "completeness": "INDETERMINATE", "reason": "STRUCTURED_REPORT_MISSING"}}
    [entry] = close_suite_preservation_requirements(ledger, reqs, test_files=[], run_suite=lambda: stub, source="t", revision=1)
    assert entry["closed"] is False and "not COMPLETE" in entry["reason"]
    # no verdict to bind to: nothing runs, nothing closes
    bare = ObligationLedger()
    seed_requirement_obligations(bare, reqs)
    assert close_suite_preservation_requirements(bare, reqs, test_files=[], run_suite=lambda: stub, source="t", revision=1) == []


# ---------------------------------------------------------------- closers: the run's mutation record
def test_09_test_immutability_is_decided_from_the_mutation_record():
    reqs = derive_requirements("Fix it.\n" + IMMUTABLE)
    evidence = {"run_id": "r", "base_revision": "b", "candidate_revision": "b", "actual_paths": ["src/x.py"],
                "foreign_paths": []}
    ledger = unverified_ledger(reqs)
    [entry] = close_test_immutability_requirements(ledger, reqs, reference_test_files=["tests/test_a.py"],
                                                   present_test_files=["tests/test_a.py", "tests/test_new.py"],
                                                   scope_evidence=evidence, source="t", revision=1)
    assert entry["closed"] is True and requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    for changed, missing in (({"actual_paths": ["tests/test_a.py"]}, []), ({}, ["tests/test_a.py"])):
        ledger = unverified_ledger(reqs)
        present = [] if missing else ["tests/test_a.py"]
        [entry] = close_test_immutability_requirements(ledger, reqs, reference_test_files=["tests/test_a.py"],
                                                       present_test_files=present, scope_evidence={**evidence, **changed},
                                                       source="t", revision=1)
        assert entry["closed"] is False and entry["violated"] is True
        assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.VIOLATED
    ledger = unverified_ledger(reqs)
    [entry] = close_test_immutability_requirements(ledger, reqs, reference_test_files=None, present_test_files=[],
                                                   scope_evidence=evidence, source="t", revision=1)
    assert entry["closed"] is False and "cannot be established" in entry["reason"]


def test_10_the_mutation_record_comes_from_git_on_the_candidate(tmp_path):
    workspace = tmp_path / "ws"
    write_project(workspace, "def test_a():\n    assert True\n")
    (workspace / "src.py").write_text("x = 1\n")
    git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=workspace, check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "base")
    reqs = derive_requirements("Fix src.py.\n" + IMMUTABLE)
    ledger = unverified_ledger(reqs)
    (workspace / "src.py").write_text("x = 2\n")
    [entry] = wf.close_requirements_by_test_immutability(ledger, reqs, str(workspace), str(workspace),
                                                         candidate_paths=["src.py"], revision=1)
    assert entry["closed"] is True and entry["changed_test_files"] == []
    (workspace / "tests" / "test_suite.py").write_text("def test_a():\n    assert True\n\ndef test_added():\n    pass\n")
    ledger = unverified_ledger(reqs)
    [entry] = wf.close_requirements_by_test_immutability(ledger, reqs, str(workspace), str(workspace),
                                                         candidate_paths=["src.py", "tests/test_suite.py"], revision=1)
    assert entry["violated"] is True and entry["changed_test_files"] == ["tests/test_suite.py"]


# ---------------------------------------------------------------- the run: refused before any model call, only when it would block
def _blocking_config(tmp_path, *args, **kwargs):
    cfg = make_config(tmp_path, *args, **kwargs)
    cfg.autonomy.requirement_unverified_policy = "block"
    cfg.autonomy.requirement_unknown_policy = "block"
    return cfg


def test_11_a_goal_that_could_never_verify_is_refused_before_the_first_model_call(tmp_path, monkeypatch):
    import _edit_protocol_harness as harness
    monkeypatch.setattr(harness, "make_config", _blocking_config)
    run = run_edit_protocol(tmp_path, monkeypatch, ["unused"], probe=_probe, goal=UNLOCALIZED_GOAL)
    assert run.developer == []
    assert run.result["failure_category"] == "goal_insufficient_for_verification"
    assert run.result["reason_codes"] == [GOAL_INSUFFICIENT_FOR_VERIFICATION] and run.result["quality_gates_passed"] is False
    assert run.result["requirements_admission"]["residual"][0]["id"] == "REQ-1"
    [event] = run.kinds("requirement.admission_refused")
    assert event.details["reason_code"] == GOAL_INSUFFICIENT_FOR_VERIFICATION
    assert run.result["environment_failure"].startswith("GOAL_INSUFFICIENT_FOR_VERIFICATION:")


def test_12_under_a_recording_policy_the_same_goal_is_not_refused(tmp_path, monkeypatch):
    assert not wf._requirement_policy_blocks(AutonomyConfig())
    assert wf._requirement_policy_blocks(AutonomyConfig(requirement_unverified_policy="block"))
    run = run_edit_protocol(tmp_path, monkeypatch, ["unused"], probe=_probe, goal=UNLOCALIZED_GOAL)
    assert run.result.get("failure_category") != "goal_insufficient_for_verification"
    assert not run.kinds("requirement.admission_refused")


# ---------------------------------------------------------------- review reconciliation (2026-10-08)
@pytest.mark.parametrize("text", ["Make the failing test pass.", "Fix the failing tests.", "Make all tests pass",
                                  "The tests should fail until the fix lands."])
def test_13_a_goal_directed_sentence_is_never_a_suite_preservation_statement(text):
    """Review 5.2: the inherited named-test vocabulary allowed 'make'/'failing' next to a named test; a pure
    preservation statement never asks for a state change, so these are residual (behaviour), never admitted."""
    assert not is_suite_preservation_requirement(text)
    assert BEHAVIOR in requirement_claims(text, [])
    _, residual = deterministic_closers(derive_requirements(text), test_files=["tests/test_a.py"])
    assert [r["id"] for r in residual] == ["REQ-1"]


def test_14_unchanged_closes_only_with_the_mutation_record(tmp_path):
    """Review 5.2: a green suite never proves 'unchanged'; the statement needs the run's mutation record too."""
    from kriya.workflow.requirements import suite_statement_requires_immutability, test_immutability_evidence
    assert suite_statement_requires_immutability(SUITE) and not suite_statement_requires_immutability("All existing tests must keep passing.")
    closers, _ = deterministic_closers(derive_requirements(SUITE), test_files=["tests/test_a.py"])
    assert closers["REQ-1"] == [SUITE_PRESERVATION, TEST_IMMUTABILITY]
    root = write_project(tmp_path / "ws", "def test_a():\n    assert True\n")
    reqs = derive_requirements(SUITE)
    scope = {"run_id": "r", "base_revision": "b", "candidate_revision": "b", "actual_paths": ["src.py"], "foreign_paths": []}
    clean = test_immutability_evidence(["tests/test_suite.py"], ["tests/test_suite.py"], scope)
    rewritten = test_immutability_evidence(["tests/test_suite.py"], ["tests/test_suite.py"],
                                           {**scope, "actual_paths": ["tests/test_suite.py"]})
    unavailable = test_immutability_evidence(None, [], scope)
    for evidence, expected in ((clean, RequirementOutcome.CLOSED_BY_EVIDENCE), (rewritten, RequirementOutcome.VIOLATED),
                               (unavailable, RequirementOutcome.UNVERIFIED), (None, RequirementOutcome.UNVERIFIED)):
        ledger = unverified_ledger(reqs)
        [entry] = close_suite_preservation_requirements(
            ledger, reqs, test_files=["tests/test_suite.py"], run_suite=lambda: validator_for(root).run_tests(),
            source="t", revision=1, test_immutability=evidence)
        assert requirement_outcomes(ledger, reqs)["REQ-1"] is expected, (evidence, entry)
        assert entry["closed"] is (expected is RequirementOutcome.CLOSED_BY_EVIDENCE)
    # the mapped outcome decides closability for the immutability closer too (review 5.7)
    reqs = derive_requirements(IMMUTABLE)
    ledger = unverified_ledger(reqs)
    [entry] = close_test_immutability_requirements(ledger, reqs, reference_test_files=["tests/test_a.py"],
                                                   present_test_files=["tests/test_a.py"], scope_evidence=scope,
                                                   source="t", revision=1)
    assert entry["closed"] is True


@pytest.mark.asyncio
async def test_15_the_enforce_path_refuses_before_planning_under_production(tmp_path):
    """Review 5.1/5.4: the controller's admission branch, before retrieval and before the Planner."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from kriya.config import AppConfig
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_schema import ChangeKind
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
    (tmp_path / "app.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT))
    we.kernel = SimpleNamespace(config=cfg)
    we.planner.run = AsyncMock(return_value="never")
    we.acceptance = None
    result = await wc.WorkflowController(we).execute("Add a run() entry point to `app.py`.\n", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    assert legacy["failure_type"] == "GOAL_ADMISSION" and legacy["failure_category"] == "goal_insufficient_for_verification"
    assert legacy["reason_codes"] == [GOAL_INSUFFICIENT_FOR_VERIFICATION] and legacy["quality_gates_passed"] is False
    assert legacy["requirements_admission"]["residual"][0]["id"] == "REQ-1"
    assert we.planner.run.await_count == 0 and not we.run_generation_workflow.called


TEST_MODULE = "def test_a():\n    assert True\n"
ADMITTED_GOAL = "tests/test_a.py must keep passing.\n\nDo not change any existing test.\n"


def test_16_an_admitted_goal_whose_closers_cannot_close_still_blocks_at_the_terminal_under_production(tmp_path, monkeypatch):
    """Review 5.3: the terminal backstop under production, end to end: the goal is admitted (a named test and a
    test-immutability constraint), the Developer runs once, no closer can bind (spec compliance off: no verdict),
    and the run stops REQUIREMENTS_UNRESOLVED with nothing applied and no retry."""
    import _edit_protocol_harness as harness
    monkeypatch.setattr(harness, "make_config", _blocking_config)
    run = run_edit_protocol(tmp_path, monkeypatch, [TEST_MODULE.replace("True", "1 == 1")], probe=_probe,
                            goal=ADMITTED_GOAL, source=TEST_MODULE, target="tests/test_a.py")
    assert not run.kinds("requirement.admission_refused")
    assert len(run.developer) == 1
    assert run.result["quality_gates_passed"] is False and run.result["failure_category"] == "requirements_unresolved"
    assert run.result["environment_failure"].startswith("REQUIREMENTS_UNRESOLVED:")
    assert (run.workspace / "tests" / "test_a.py").read_text() == TEST_MODULE  # nothing applied


# ---------------------------------------------------------------- final readiness review reconciliation (2026-10-08)
@pytest.mark.parametrize("text", [
    'Every existing test must keep passing "once TTLCache.expire drops entries whose expiry time has been reached".',
    "Every existing test must keep passing:\n```\ncache[1] = 1\nassert len(cache) == 1\n```",
    "All existing tests must keep passing (see the README for how).",
    "Every existing test must keep passing (`add the missing guard in expire first`).",
    # a prose parenthetical, even a benign one, is not a command: fail closed (recorded limitation)
    "Every existing test (including the JSON compliance suite under tests/) must keep passing unchanged.",
])
def test_17_a_request_hidden_in_quotes_parentheses_or_a_fence_is_never_a_suite_statement(text):
    """Final review finding 1: stripped spans may only be commands or paths, never prose."""
    assert not is_suite_preservation_requirement(text)
    _, residual = deterministic_closers(derive_requirements(text), test_files=["tests/test_a.py"])
    assert residual


@pytest.mark.parametrize("text", ["Every existing test must keep passing (./gradlew :json-path:test).",
                                  "Every existing test must keep passing (mvn test).", 'All tests keep passing ("pytest").',
                                  "The test suite stays green (`python -m pytest -q`)."])
def test_18_a_command_or_path_span_keeps_the_suite_statement(text):
    assert is_suite_preservation_requirement(text)


def test_19_a_compound_migration_sentence_is_residual():
    """Final review finding 3: the migration gate closes the migration itself, nothing coordinated with it."""
    compound = derive_requirements("Replace requests with httpx in the client and also add exponential-backoff retries to every call.")
    closers, residual = deterministic_closers(compound, test_files=[], migration_identities=[("requests", "httpx")])
    assert closers["REQ-1"] == [] and "compound" in residual[0]["why"]
    pure = derive_requirements("Replace requests with httpx in the client.")
    closers, residual = deterministic_closers(pure, test_files=[], migration_identities=[("requests", "httpx")])
    assert closers["REQ-1"] == ["migration_gate"] and residual == []


@pytest.mark.parametrize("text", ["Do not modify or delete any existing test.", "Do not modify, delete or rename any existing test.",
                                  "Existing tests must not be modified.", "Leave the existing tests untouched.",
                                  "Keep the existing tests as they are."])
def test_20_the_cohorts_immutability_phrasings_are_recognized(text):
    """Final review finding 5 (T3 REQ-11 was residual although it is an accepted form)."""
    assert is_test_immutability_requirement(text)
    assert deterministic_closers(derive_requirements(text), test_files=[])[0]["REQ-1"] == [TEST_IMMUTABILITY]


def test_21_the_suite_closer_refuses_a_candidate_that_changed_the_test_trust_surface(tmp_path):
    """Final review finding 2: a candidate conftest.py / pytest.ini / build declaration forges the suite's verdict,
    so the candidate's own suite is not an independent oracle (the FS-1C0 rule); no record, no closure."""
    from kriya.workflow.requirements import changed_test_trust_surface, test_immutability_evidence
    assert changed_test_trust_surface(["tests/conftest.py", "pyproject.toml", "src/x.py", "pytest.ini", "pom.xml",
                                       "tests/test_a.py", "gradle/wrapper/gradle-wrapper.properties"]) == [
        "gradle/wrapper/gradle-wrapper.properties", "pom.xml", "pyproject.toml", "pytest.ini", "tests/conftest.py"]
    root = write_project(tmp_path / "ws", "def test_a():\n    assert True\n")
    reqs = derive_requirements("All existing tests must keep passing.")
    scope = {"run_id": "r", "base_revision": "b", "candidate_revision": "b", "actual_paths": ["tests/conftest.py"], "foreign_paths": []}
    tampered = test_immutability_evidence(["tests/test_suite.py"], ["tests/test_suite.py"], scope)
    assert tampered["changed_trust_surface"] == ["tests/conftest.py"]
    ledger = unverified_ledger(reqs)
    [entry] = close_suite_preservation_requirements(ledger, reqs, test_files=["tests/test_suite.py"],
                                                    run_suite=lambda: validator_for(root).run_tests(), source="t",
                                                    revision=1, test_immutability=tampered)
    assert entry["closed"] is False and "trust surface" in entry["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    ledger = unverified_ledger(reqs)
    [entry] = close_suite_preservation_requirements(ledger, reqs, test_files=["tests/test_suite.py"],
                                                    run_suite=lambda: validator_for(root).run_tests(), source="t", revision=1)
    assert entry["closed"] is False and "mutation record" in entry["reason"]  # no record at all: nothing closes
