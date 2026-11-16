"""S5 (C-01, R-01): the sealed verification contract is the requirement truth; a generated test is evidence under it.

Invariant: authoritative semantic requirements exist and are sealed BEFORE Developer-written implementation or tests can
define or verify them. A generated test may PROVE a requirement the contract can bind; it never CREATES one, never
closes one the contract holds, and never acts as an authoritative failure while its provenance is unknown.

Reproducer (A2 probes P5 and P7, measured on the closure baseline): an executed, failed identity that maps to no
run-authored file was read as "the run did not write this test", so a never-accepted generated test became the repair
objective of the ordinary retry loop. The loop-level case below is that sequence on the real controller and unit.
"""
from __future__ import annotations

import dataclasses

import pytest
from test_verifier_semantic_loop_001 import LIB, LIB_20, TEST, Scene, _gate_result, _report, _stopped_for_review, _test

from kriya.workflow import contract_compilation as cc
from kriya.workflow import verifier_semantic as vs
from kriya.workflow.contract_compilation import compile_verification_contract
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    GoalAdmissionError,
    RequirementOutcome,
    VerificationAuthorityRequired,
    blocking_requirements,
    derive_requirements,
    record_requirement_closure,
    record_requirement_verdicts,
    requirement_obligation_id,
    requirement_outcomes,
    requirement_truth,
    seed_requirement_obligations,
    statement_origins,
)
from kriya.workflow.verifier_semantic import derive_executed_verifiers

FAILING = b"def test_counts():\n    assert 1 == 2\n"


def _cases(*pairs):
    return {"completeness": "COMPLETE", "runner": "pytest",
            "test_cases": [{"identity": identity, "classname": identity.rsplit(".", 1)[0], "name": identity.rsplit(".", 1)[1], "status": status}
                           for identity, status in pairs]}


# ------------------------------------------------------------------ 1. the reproducer at the first wrong owner (GAP-A2-02)

def test_p5_a_src_layout_run_authored_test_is_unmapped_never_read_as_pre_existing():
    """The runner reports ``src.pkg.test_widget.test_counts``; the structural parser drops ``src/``. Pre-fix: no subject, not undetermined."""
    derivation = derive_executed_verifiers(_cases(("src.pkg.test_widget.test_counts", "failed")),
                                           {"src/pkg/__init__.py": b"", "src/pkg/test_widget.py": FAILING})
    assert derivation.verifiers == () and derivation.unmapped == ("src.pkg.test_widget.test_counts",)
    assert derivation.undetermined is True


def test_p7_a_run_authored_test_in_a_language_without_a_grammar_is_unmapped():
    derivation = derive_executed_verifiers(
        _cases(("com.example.WidgetTest.counts", "failed")),
        {"src/test/kotlin/com/example/WidgetTest.kt": b"package com.example\nclass WidgetTest {\n  @Test fun counts() { assert(false) }\n}\n"})
    assert derivation.unmapped == ("com.example.WidgetTest.counts",) and derivation.undetermined is True


def test_a_passing_unmapped_run_authored_test_is_held_too():
    """The standing ruling: ANY run-authored executable test needs semantic review, passing or failing."""
    derivation = derive_executed_verifiers(_cases(("src.pkg.test_widget.test_counts", "passed")), {"src/pkg/test_widget.py": FAILING})
    assert derivation.unmapped == ("src.pkg.test_widget.test_counts",)


def test_a_runner_rooted_below_the_structural_module_root_is_unmapped_as_well():
    derivation = derive_executed_verifiers(_cases(("test_widget.test_counts", "failed")), {"pkg/test_widget.py": FAILING, "pkg/__init__.py": b""})
    assert derivation.unmapped == ("test_widget.test_counts",)


@pytest.mark.parametrize("authored", [
    {"pkg/widget.py": b"def counts():\n    return 1\n"},                 # production code only: declares nothing the identity could be
    {"README.md": b"# notes\n", "docs/guide.md": b"text\n"},              # no grammar, and no path that spells the identity
    {"tests/test_other.py": b"def test_other():\n    assert 1\n"},        # another run-authored test, another name
    {},
])
def test_negative_control_a_pre_existing_failing_test_is_not_made_unknown(authored):
    """An executed identity no run-authored file can account for stays a repository test: the historical path is unchanged."""
    derivation = derive_executed_verifiers(_cases(("tests.test_widget.test_counts", "failed"), ("tests.test_other.test_other", "passed")), authored)
    assert derivation.unmapped == () and "tests.test_widget.test_counts" not in {i for v in derivation.verifiers for i in v.identities}
    assert derivation.undetermined is False


def test_negative_control_an_exactly_mapped_test_is_a_subject_and_not_unmapped():
    derivation = derive_executed_verifiers(_cases(("tests.test_widget.test_counts", "failed")), {"tests/test_widget.py": FAILING})
    assert [v.path for v in derivation.failing] == ["tests/test_widget.py"] and derivation.unmapped == ()


# ------------------------------------------------------------------ 2. the same reproducer on the real controller and unit

def _rerooted_gate(tree, _call):
    """The scripted tests gate, reporting the unit's test under a module root the structural parser does not use."""
    result = dict(_gate_result(tree))
    if "test_execution" in result:
        result["test_execution"] = _report(result["test_execution"]["test_cases"][0]["status"], identity="proj.tests.test_shop.test_size")
    return result


@pytest.mark.parametrize("mode", ["guardrails", "human-in-the-loop"])
def test_a_failing_generated_test_of_unknown_provenance_stops_for_review_before_any_retry(tmp_path, mode):
    """Pre-fix: no ruling at all, and the ordinary retry loop repaired towards the generated test's expectation (it was authoritative)."""
    scene = Scene(lib=[LIB_20], test=[_test(10)], gate=_rerooted_gate)
    scene.run(tmp_path, mode=mode)
    _stopped_for_review(scene, vs.REASON_SUBJECT_AMBIGUOUS)
    assert sorted(scene.requested()) == [LIB, TEST], "one generation: the generated test's failure never became a repair objective"
    assert [r["action"] for r in scene.rulings()] == ["needs_review"] and scene.subjects == [], "no subject is invented for an unmapped identity"
    assert "unmapped:proj.tests.test_shop.test_size" in str(scene.stop().get("environment_failure"))


def test_a_passing_generated_test_of_unknown_provenance_is_not_applied_unreviewed(tmp_path):
    scene = Scene(lib=[LIB_20], test=[_test(20)], gate=_rerooted_gate)
    scene.run(tmp_path, mode="guardrails")
    _stopped_for_review(scene, vs.REASON_SUBJECT_AMBIGUOUS)


# ------------------------------------------------------------------ 3. the semantic state: derived, closed, sealed before any model

GENERAL = "Add a function percent(part, whole) that returns part as a percentage of whole rounded to one decimal place. Include a pytest test for it."
RUNTIME = "Create hello.py. It prints hello world when executed."
AMBIGUOUS = "Add a function percent(part, whole) that returns part as a percentage of whole. Do not modify any other file."


def _contract(goal, **inputs):
    reqs = derive_requirements(goal)
    return reqs, compile_verification_contract(reqs, origins=statement_origins(goal), project_language="python", **inputs)


def test_every_entry_has_exactly_one_state_from_the_closed_table():
    _reqs, general = _contract(GENERAL)
    assert general.semantic_states() == {"REQ-1": cc.SEMANTIC_GENERAL_UNSPECIFIED, "REQ-2": cc.SEMANTIC_STRUCTURAL}
    _reqs, runtime = _contract(RUNTIME)
    assert runtime.semantic_states() == {"REQ-1": cc.SEMANTIC_STRUCTURAL, "REQ-2": cc.SEMANTIC_SPECIFIED}
    _reqs, ambiguous = _contract(AMBIGUOUS)
    assert ambiguous.semantic_states()["REQ-2"] == cc.SEMANTIC_AMBIGUOUS
    for contract in (general, runtime, ambiguous):
        assert set(contract.semantic_states().values()) <= set(cc.SEMANTIC_STATES)
    assert general.semantic_holds() == {"REQ-1": cc.SEMANTIC_GENERAL_UNSPECIFIED}
    assert runtime.semantic_holds() == {}
    assert ambiguous.semantic_holds()["REQ-2"] == cc.SEMANTIC_AMBIGUOUS


@pytest.mark.parametrize("status, expected", [
    (cc.STATUS_AMBIGUOUS, cc.SEMANTIC_AMBIGUOUS), (cc.STATUS_NOT_A_CLAIM, cc.SEMANTIC_NOT_APPLICABLE),
    (cc.STATUS_DISPOSITIONED, cc.SEMANTIC_NOT_APPLICABLE), (cc.STATUS_AUTHORITY_REQUIRED, cc.SEMANTIC_GENERAL_UNSPECIFIED),
])
def test_the_state_table_over_every_status_that_is_not_closable(status, expected):
    _reqs, contract = _contract(GENERAL)
    assert dataclasses.replace(contract.entries[0], status=status).semantic_state == expected


def test_an_entry_the_table_does_not_classify_is_a_programming_error_never_a_guessed_state():
    _reqs, contract = _contract(RUNTIME)
    specified = contract.entry("REQ-2")
    with pytest.raises(ValueError, match="unclassified contract status"):
        _ = dataclasses.replace(specified, status="SOMETHING_NEW").semantic_state
    foreign = dataclasses.replace(specified, bindings=(dataclasses.replace(specified.bindings[0], authority_kind=cc.AUTHORITY_REPOSITORY),))
    with pytest.raises(ValueError, match="unclassified authority"):
        _ = foreign.semantic_state


def test_the_state_is_derived_it_adds_no_field_and_does_not_move_the_contract_digest():
    """No new schema: the sealed entry, the identity payload and the compiler version are what they were (no stale contract, no golden change)."""
    _reqs, contract = _contract(GENERAL)
    assert "semantic_state" not in contract.entries[0].to_dict() and "semantic" not in str(sorted(contract.identity_payload()))
    assert cc.CONTRACT_COMPILER_VERSION == 4 and cc.CONTRACT_FORMAT == "kriya.verification_contract/1"


# ------------------------------------------------------------------ 4. the closure rule: one reader, under the sealed contract

def _ledger(reqs):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "model") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    return ledger


def _claim_closed_by_a_run_written_record(ledger, reqs, requirement_id, method="generated_test_passed"):
    """What a generated test (or a forged / resumed record) would need to write to close a requirement: a closure for the judged candidate."""
    evidence_id = ledger.current(requirement_obligation_id(requirement_id)).evidence["evidence_id"]
    record_requirement_closure(ledger, reqs, requirement_id, evidence_id=evidence_id, method=method,
                               detail={"tests": ["tests.test_percent.test_percent"]}, source="test", revision=1)


def test_control_1_a_generated_test_cannot_close_a_requirement_the_contract_holds():
    """Without the contract the record closes REQ-1 (the pre-fix reading); under the sealed contract it stays open, and so does the run's truth."""
    reqs, contract = _contract(GENERAL)
    ledger = _ledger(reqs)
    _claim_closed_by_a_run_written_record(ledger, reqs, "REQ-1")
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE, "the record alone would close it"
    outcomes = requirement_outcomes(ledger, reqs, contract=contract)
    assert outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED
    truth = requirement_truth(outcomes, reqs)
    assert truth["status"] == "OPEN_REQUIREMENTS" and [row["id"] for row in truth["unresolved"]] == ["REQ-1", "REQ-2"]
    blocked = blocking_requirements(ledger, reqs, unverified_policy="block", contract=contract)
    assert "REQ-1" in [requirement.id for requirement, _outcome in blocked]


def test_control_2_a_required_behaviour_no_generated_test_covers_stays_open():
    reqs, contract = _contract(GENERAL)
    ledger = _ledger(reqs)
    _claim_closed_by_a_run_written_record(ledger, reqs, "REQ-2", method="test_addition_record")   # the test exists: only REQ-2 is about that
    outcomes = requirement_outcomes(ledger, reqs, contract=contract)
    assert outcomes == {"REQ-1": RequirementOutcome.UNVERIFIED, "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert requirement_truth(outcomes, reqs)["status"] == "OPEN_REQUIREMENTS"


def test_control_3_evidence_never_rewrites_the_sealed_contract():
    reqs, contract = _contract(GENERAL)
    digest, states = contract.digest, contract.semantic_states()
    ledger = _ledger(reqs)
    _claim_closed_by_a_run_written_record(ledger, reqs, "REQ-1")
    requirement_outcomes(ledger, reqs, contract=contract)
    assert contract.digest == digest and contract.semantic_states() == states
    with pytest.raises(dataclasses.FrozenInstanceError):
        contract.entries[0].status = cc.STATUS_CLOSABLE   # the entry is immutable: a test cannot make its requirement closable


def test_control_4_an_ambiguous_requirement_is_refused_typed_and_is_never_closed_by_a_candidate():
    reqs, contract = _contract(AMBIGUOUS)
    refusal = contract.refusal()
    assert isinstance(refusal, GoalAdmissionError) and not isinstance(refusal, VerificationAuthorityRequired)
    assert contract.report()["admission"] == "GOAL_INSUFFICIENT_FOR_VERIFICATION"
    ledger = _ledger(reqs)
    _claim_closed_by_a_run_written_record(ledger, reqs, "REQ-2", method="mutation_scope")   # a candidate "chose" a reading and claims it holds
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert requirement_outcomes(ledger, reqs, contract=contract)["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_control_5_only_sealed_entries_carry_semantic_truth_a_model_inferred_check_is_not_one():
    """Ruling 17 at the semantic owner: the truth is computed over the contract's entries only. A check a model inferred has no entry, so it can
    neither open nor close a requirement (its gate consequence is the result contract's, S6)."""
    reqs, contract = _contract(RUNTIME)
    ledger = _ledger(reqs)
    for requirement in reqs.requirements:
        _claim_closed_by_a_run_written_record(ledger, reqs, requirement.id, method="mutation_scope")
    outcomes = requirement_outcomes(ledger, reqs, contract=contract)
    assert set(outcomes) == set(contract.semantic_states()) == {"REQ-1", "REQ-2"}
    assert requirement_truth(outcomes, reqs)["status"] == "ALL_VERIFIED"


def test_control_6_evidence_mapped_to_a_requirement_the_contract_binds_still_closes_it():
    """The negative control of the hold: SPECIFIED and STRUCTURAL entries keep every closure they had."""
    reqs, contract = _contract(RUNTIME)
    ledger = _ledger(reqs)
    _claim_closed_by_a_run_written_record(ledger, reqs, "REQ-1", method="mutation_scope")
    with_contract = requirement_outcomes(ledger, reqs, contract=contract)
    assert with_contract == requirement_outcomes(ledger, reqs)
    assert with_contract["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE and with_contract["REQ-2"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("module", ["development_loop", "outcome", "enforce_loop", "terminal_gate_service"])
def test_every_reader_that_states_the_runs_requirement_truth_reads_it_under_the_sealed_contract(module):
    """Structural: the sites that emit requirement truth, block on it or report it (both executors, the terminal gates, the result) pass the
    run's contract to the one reader. A site that drops it would read a run-written record as the requirement."""
    import ast
    import importlib

    source = importlib.import_module(f"kriya.workflow.{module}").__file__
    with open(source, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) in ("requirement_outcomes", "blocking_requirements")]
    assert calls, f"{module} no longer reads requirement outcomes: update this guard"
    missing = [node.lineno for node in calls if "contract" not in {keyword.arg for keyword in node.keywords}]
    assert not missing, f"kriya/workflow/{module}.py reads requirement outcomes without the sealed contract at lines {missing}"


def test_counter_evidence_still_contradicts_a_held_requirement():
    reqs, contract = _contract(GENERAL)
    ledger = _ledger(reqs)
    evidence_id = ledger.current(requirement_obligation_id("REQ-1")).evidence["evidence_id"]
    record_requirement_closure(ledger, reqs, "REQ-1", evidence_id=evidence_id, method="mutation_scope", detail={}, source="test", revision=1,
                               violated=True)
    assert requirement_outcomes(ledger, reqs, contract=contract)["REQ-1"] is RequirementOutcome.VIOLATED
