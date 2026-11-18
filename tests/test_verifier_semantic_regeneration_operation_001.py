"""VERIFIER-SEMANTIC-REGENERATION-OPERATION-001 and TEST-ASSERTION-CONTRACT-001 (owner ruling 2026-11-18; Stage B runs 5, 6 and 7).

`expectation_invalid -> regenerate the verifier once` is its own operation, not a gate-failure repair:

    run 7 (20261118T151530-ee67399b), MEASURED: the request carried the person's rationale and said "Rewrite exactly", but it was a targeted
    REPAIR: only an anchored EDIT was offered and only the failing member (18 lines) was shown. The model said it would remove the unsupported
    test, invented the rest of the file as its SEARCH text, the edit engine refused it (ANCHOR_NOT_IN_FILE) and the unit stopped
    VERIFIER_SEMANTIC_REGENERATION_NOT_EXECUTED.

The operation: the request shows the COMPLETE reviewed verifier and its only outcome is the complete replacement file. Authorization is not
delivery: a provider answer that violates the response protocol is re-asked ONCE on the same model binding with the machine-detected error;
the first valid whole-file answer is the one candidate. And every test-role request carries one generic assertion contract.

The scripted model answers only in an outcome the request offers (tests/_enforce_unit_harness.py, protocol_faithful); a deliberate violation
is wrapped in ``Unoffered``.
"""
from __future__ import annotations

import pytest
from _enforce_unit_harness import Unoffered, offered_outcomes
from test_verifier_regeneration_blind_001 import (
    FALLBACK,
    MARKER,
    PRIMARY,
    RATIONALE,
    V0,
    V1,
    _for,
    _invalid,
    _like_the_live_model,
    _run,
    _sha,
)
from test_verifier_semantic_loop_001 import LIB, LIB_20, TEST, _stopped_for_review

from kriya.core import token_budget as tb
from kriya.workflow import verifier_semantic as vs
from kriya.workflow.generation_manifest import TEST_ASSERTION_CONTRACT, role_contract_block

CORRECTION = "=== Response protocol correction ==="
SOURCE_BLOCK = f"<<<VERIFIER_SOURCE\n{V0}VERIFIER_SOURCE>>>"


def _deliveries(scene):
    return [event.details for event in scene.result.events if event.kind == "verifier_semantic.regeneration_delivery"]


def _regenerations(seen):
    return [request for request in _for(seen) if MARKER in request["user"]]


# ------------------------------------------------------------------ the run 7 sequence, with a model that obeys the offered protocol

def test_run7_sequence_the_regeneration_shows_the_whole_verifier_and_accepts_only_the_whole_replacement_file(tmp_path):
    scene, seen = _run(tmp_path, semantic=[_invalid(), "expectation_valid"], model=_like_the_live_model)
    result = scene.stop()
    assert result.get("quality_gates_passed") is True, (result.get("failure_category"), result.get("environment_failure"))
    assert scene.applied() == {LIB: LIB_20, TEST: V1}
    (regeneration,) = _regenerations(seen)
    system, user = regeneration["system"], regeneration["user"]
    # its own operation: the complete replacement file is the only legal outcome
    assert "MODE: VERIFIER_REGENERATION." in system and "MODE: REPAIR." not in system
    assert offered_outcomes(system, TEST) == {"file"}
    assert "KRIYA:EDIT" not in system and "KRIYA:NO_CHANGE" not in system and "KRIYA:REDIRECT" not in system
    # the exact, complete verifier under review, byte for byte, bound to the reviewed digest, with the bound rationale
    assert user.count(SOURCE_BLOCK) == 1 and f"sha256 {_sha(V0)[:16]}" in user
    assert f"<<<OPERATOR_RATIONALE\n{RATIONALE}\nOPERATOR_RATIONALE>>>" in user and "Return the COMPLETE replacement file." in user
    assert user.index(SOURCE_BLOCK) < user.index(MARKER)
    # one request, one candidate, no delivery retry
    assert _deliveries(scene) == [{"path": TEST, "reviewed_sha256": _sha(V0), "model": PRIMARY, "delivery_retry": None,
                                   "candidate_produced": True, "delivery_retries": 0, "candidates": 1, "candidate_sha256": _sha(V1)}]
    assert CORRECTION not in user
    # the write scope of the regeneration was exactly the verifier, and no other path's request knows of it
    assert [record["during"][0] for record in scene.scopes].count([TEST]) == 1
    assert all(MARKER not in request["user"] and "VERIFIER_SOURCE" not in request["user"] for request in _for(seen, LIB))


# ------------------------------------------------------------------ authorization != delivery

def test_a_protocol_violating_answer_is_re_asked_once_on_the_same_binding_with_only_the_machine_error(tmp_path):
    answers = iter([Unoffered(None), V1])        # "no change" (not an outcome of this request), then the whole file

    def model(request):
        return V0 if MARKER not in request["user"] else next(answers)

    scene, seen = _run(tmp_path, semantic=[_invalid(), "expectation_valid"], model=model, fallback=True)
    assert scene.stop().get("quality_gates_passed") is True and scene.applied() == {LIB: LIB_20, TEST: V1}
    first, second = _regenerations(seen)
    assert (first["model"], second["model"]) == (PRIMARY, PRIMARY), "the correction is re-sent to the same model binding"
    assert first["system"] == second["system"] and second["user"].startswith(first["user"])
    added = second["user"][len(first["user"]):]
    assert added.startswith("\n" + CORRECTION + "\n") and "no file content" in added and RATIONALE not in added
    assert "machine-detected reason only" in added and added.count("\n") <= 4, "only the protocol error is added"
    (delivery,) = _deliveries(scene)
    assert delivery["delivery_retry"]["outcome"] == "candidate_produced" and delivery["delivery_retry"]["model"] == PRIMARY
    assert (delivery["delivery_retries"], delivery["candidates"], delivery["candidate_produced"]) == (1, 1, True)
    assert seen["takes"] == 1 and len(scene.subjects) == 2, "one authorization; the person was asked only for the two verifiers' semantics"
    assert [record["during"][0] for record in scene.scopes].count([TEST]) == 1, "the re-send is the same attempt, not another one"


@pytest.mark.parametrize("violation, named", [
    (Unoffered(None), "no file content"),
    # a well-anchored EDIT: the parser already refuses a patch where none is offered, so it arrives as a protocol error
    (Unoffered([("    assert page_size() == 20", "    assert page_size() == 20  # patched")]), "did not follow the response protocol"),
    (Unoffered(("tests/test_other.py", V1)), "did not follow the response protocol"),
])
def test_a_second_violation_ends_it_with_no_third_request_no_failover_and_nothing_applied(tmp_path, violation, named):
    scene, seen = _run(tmp_path, semantic=[_invalid()], model=lambda request: V0 if MARKER not in request["user"] else violation, fallback=True)
    _stopped_for_review(scene, vs.REASON_REGENERATION_NOT_EXECUTED)
    regenerations = _regenerations(seen)
    assert len(regenerations) == 2 and {request["model"] for request in regenerations} == {PRIMARY}, "one re-send, never another model"
    assert named in regenerations[1]["user"].split(CORRECTION)[1]
    (delivery,) = _deliveries(scene)
    assert delivery["delivery_retry"]["outcome"] == "violated_again" and delivery["candidate_produced"] is False
    assert (delivery["delivery_retries"], delivery["candidates"]) == (1, 0)
    assert all(tree[TEST] == V0 for tree in scene.gate_trees), "a violating answer is never applied - not even a well-anchored edit"
    assert seen["takes"] == 1 and len(scene.subjects) == 1


def test_a_correction_that_cannot_be_dispatched_stops_without_failover(tmp_path):
    """A provider has answered: the pre-dispatch failover of an unanswered request does not apply any more."""
    scene, seen = _run(tmp_path, semantic=[_invalid()], model=lambda request: V0 if MARKER not in request["user"] else Unoffered(None),
                       fallback=True, refuse_when=lambda model, user: CORRECTION in user)
    _stopped_for_review(scene, vs.REASON_REGENERATION_NOT_EXECUTED)
    regenerations = _regenerations(seen)
    assert len(regenerations) == 1 and regenerations[0]["model"] == PRIMARY and seen["refused"] == [PRIMARY]
    assert FALLBACK not in {request["model"] for request in seen["requests"]}
    (delivery,) = _deliveries(scene)
    assert delivery["delivery_retry"]["outcome"] == "correction_not_dispatched" and delivery["candidates"] == 0


@pytest.mark.parametrize("candidate, reason", [
    (V0, vs.REASON_REGENERATION_NOT_EXECUTED),                                     # the same bytes back
    (V1.replace("== 20", "== 11"), vs.REASON_REGENERATION_SPENT),                  # a new verifier that fails and is declared invalid again
])
def test_the_first_valid_whole_file_answer_is_the_one_candidate_whatever_becomes_of_it(tmp_path, candidate, reason):
    scene, seen = _run(tmp_path, semantic=[_invalid(), _invalid("still not what the goal states")],
                       model=lambda request: V0 if MARKER not in request["user"] else candidate)
    _stopped_for_review(scene, reason)
    assert len(_regenerations(seen)) == 1, "a valid whole-file answer is never re-asked: no delivery retry, no candidate #2"
    (delivery,) = _deliveries(scene)
    assert delivery["delivery_retry"] is None and (delivery["candidate_produced"], delivery["candidates"]) == (True, 1)
    assert delivery["candidate_sha256"] == _sha(candidate) and seen["takes"] == 1


def test_a_refusal_before_dispatch_keeps_the_pending_authorization_and_the_whole_operation_moves_to_the_fallback(tmp_path):
    scene, seen = _run(tmp_path, semantic=[_invalid(), "expectation_valid"], model=_like_the_live_model, refuse={PRIMARY}, fallback=True)
    assert scene.stop().get("quality_gates_passed") is True and scene.applied() == {LIB: LIB_20, TEST: V1}
    (regeneration,) = _regenerations(seen)
    assert regeneration["model"] == FALLBACK and seen["refused"] == [PRIMARY]
    assert offered_outcomes(regeneration["system"], TEST) == {"file"} and SOURCE_BLOCK in regeneration["user"]
    (delivery,) = _deliveries(scene)
    assert (delivery["model"], delivery["delivery_retry"], delivery["candidates"]) == (FALLBACK, None, 1)
    assert seen["takes"] == 1


# ------------------------------------------------------------------ the owner's typed request

def test_the_request_names_what_a_candidate_verifier_is_and_nothing_else_is_one():
    request = vs.VerifierRegenerationRequest(TEST, _sha(V0), RATIONALE, V0)
    assert request.delivery_retries == 1 and "MODE: VERIFIER_REGENERATION." in request.mode_text and TEST in request.mode_text
    assert request.response_violation({"filepath": TEST, "content": V1}) is None
    assert "anchored EDIT" in request.response_violation({"filepath": TEST, "content": None, "edits": [{"search": "a", "replace": "b"}]})
    assert "no file content" in request.response_violation({"filepath": TEST, "content": None})
    assert "x went wrong" in request.response_violation({"filepath": TEST, "content": V1, "protocol_error": "x went wrong"})
    correction = request.correction_block("the machine error")
    assert "the machine error" in correction and RATIONALE not in correction and V0 not in correction
    no_newline = vs.VerifierRegenerationRequest(TEST, _sha("a"), RATIONALE, "a")
    assert "<<<VERIFIER_SOURCE\na\nVERIFIER_SOURCE>>>" in no_newline.prompt_block


def test_the_owner_counts_delivery_retries_and_candidates_apart():
    unit = vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal="g", regenerable=True)
    refused = unit.note_delivery({"filepath": TEST, "content": None, "protocol_error": "bad",
                                  "regeneration_delivery_retry": {"violation": "bad", "model": "m", "outcome": "violated_again"}})
    assert (refused["delivery_retries"], refused["candidates"], refused["candidate_produced"], refused["candidate_sha256"]) == (1, 0, False, None)
    produced = unit.note_delivery({"filepath": TEST, "content": V1})
    assert (produced["delivery_retries"], produced["candidates"], produced["candidate_sha256"]) == (1, 1, _sha(V1))
    assert (unit.delivery_retries, unit.candidates) == (1, 1)


def test_an_unshowable_subject_is_never_replaced_by_a_generic_repair(tmp_path):
    """The verifier on disk is no longer the reviewed bytes when the regeneration starts: no request of any kind is sent for it."""
    import kriya.workflow.verifier_semantic as owner

    real = owner.UnitSemantics.regeneration_request
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(owner.UnitSemantics, "regeneration_request", lambda self, targets: None if self.regenerating else real(self, targets))
        scene, seen = _run(tmp_path, semantic=[_invalid()], model=_like_the_live_model)
    assert scene.stop().get("quality_gates_passed") is not True and scene.applied() == {LIB: None, TEST: None}
    assert len(_for(seen)) == 1, "only the planned request: nothing was sent in the regeneration's place"
    assert "VERIFIER_SEMANTIC_REGENERATION_NOT_EXECUTED" in str(scene.stop().get("environment_failure"))


# ------------------------------------------------------------------ TEST-ASSERTION-CONTRACT-001: every test-role request, nothing else

RULE = ("every assertion must be entailed by the Authoritative Goal or this unit's assigned acceptance criteria. Existing code is the candidate "
        "under test and may establish APIs/signatures, but its behavior is not authority for expected behavior. Do not assert outcomes for "
        "inputs/cases whose semantics the contract does not define.")


def test_every_test_role_request_carries_the_assertion_contract_and_no_other_request_does(tmp_path):
    scene, seen = _run(tmp_path, semantic=[_invalid(), "expectation_valid"], model=_like_the_live_model)
    assert scene.stop().get("quality_gates_passed") is True
    test_requests, other_requests = _for(seen), _for(seen, LIB)
    assert len(test_requests) == 2 and other_requests
    assert all(request["user"].count(RULE) == 1 for request in test_requests), "the planned request and the regeneration alike"
    assert all(RULE not in request["user"] and RULE not in request["system"] for request in other_requests)
    assert all(RULE not in request["system"] for request in test_requests), "a contract paragraph of the request, not a system instruction"


@pytest.mark.parametrize("path, carries", [
    ("tests/test_shop.py", True), ("src/test/java/com/example/ProtocolParserTest.java", True), ("pkg/widget_test.go", True),
    ("spec/models/user_spec.rb", True), ("shop.py", False), ("src/main/java/com/example/ProtocolParser.java", False), ("pom.xml", False),
    ("README.md", False),
])
def test_the_contract_is_keyed_on_the_file_role_alone(path, carries):
    assert role_contract_block(path) == (TEST_ASSERTION_CONTRACT if carries else "")
    for word in ("null", "Java", "Ignite", "JUnit", "pytest"):
        assert word.lower() not in TEST_ASSERTION_CONTRACT.lower()
    assert RULE in TEST_ASSERTION_CONTRACT


def test_the_context_refusal_used_here_is_the_real_type():
    with pytest.raises(tb.ContextBudgetUnsatisfiableError):
        tb.plan_dispatch(messages=[{"role": "user", "content": "u" * 90000}], requested_max_tokens=16384, context_window=32768,
                         window_source="requested_per_request")
