"""VERIFIER-SEMANTIC-REVIEW-001 (WS3-B, owner ruling 2026-11-14): the verifier SEMANTIC review is a fourth typed review kind on the EXISTING launch record,
waiter and private review channel, with its own three-word decision vocabulary.

Pinned here on the real launch session and a real socket pair (tests/_review_channel_host.py): decision validity is per (review kind, decision); a word of
another kind is refused with a typed result before anything is recorded or resumed; the three outcomes travel wire -> durable record -> waiter -> caller
exactly; an explicit ``cannot_decide`` is a recorded decision and is never what an expiry, a missing channel or an unanswered prompt produces; a decision is
bound to the fingerprint of every binding; the regeneration allowance is per verifier subject (one V1, no V2) and is not the replay path's once-per-run
allowance, which is untouched (tests/test_verifier_repair_launch_001.py, tests/test_verifier_repair_authorization_001.py).

Generic synthetic names only."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json

import pytest
from test_launch_approval import LID, _pending, _session, _wait_pending

import kriya.control.launch_approval as approval
from kriya.config import AppConfig
from kriya.control import launch as launch_module
from kriya.control import review_channel
from kriya.control.launch_approval import build_pending_verifier_semantic
from kriya.control.persistence import load_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.kup.cli_ops import run_kup_operation
from kriya.policy import approval as policy
from kriya.policy.approval import (
    ALL_DECISIONS,
    CANNOT_DECIDE,
    DECISIONS_BY_KIND,
    EXPECTATION_INVALID,
    EXPECTATION_VALID,
    KIND_CHANGE,
    KIND_RUNTIME,
    KIND_VERIFIER_REPAIR,
    KIND_VERIFIER_SEMANTIC,
    KINDS,
    REVIEW_PHASE_FAILING,
    REVIEW_PHASE_PASSING,
    VERIFIER_SEMANTIC_DECISIONS,
    TypedApprovalCallback,
    VerifierSemanticAllowance,
    VerifierSemanticDecision,
    VerifierSemanticSubject,
    ask_verifier_semantic,
    decision_valid_for_kind,
    verifier_semantic_entry_verified,
    verifier_semantic_matches,
    verifier_semantic_reviewable,
)

TEST = "src/test/java/com/example/WidgetTest.java"
SOURCE = "class WidgetTest {\n  void counts() { assertEquals(3, widget.count()); }\n}\n"
GOAL = "count() is the number of items the widget holds."


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _subject(**changes) -> VerifierSemanticSubject:
    fields = dict(
        run_id="20261114T060030-40a14267", plan_hash="sha256:" + "1b" * 32, verifier_unit_id="s5", verifier_files=((TEST, _hex(SOURCE)),),
        goal_digest=_hex(GOAL), verification_contract_digest="vc" * 32, acceptance_criteria=(("AC1", "count() returns the number of items"),),
        invariants=(("GI1", "no item is counted twice"),), review_phase=REVIEW_PHASE_FAILING, failure_identity="f1" * 32,
        contract_text=GOAL, verifier_sources=((TEST, SOURCE),), outcome="WidgetTest.counts:2 expected: <3> but was: <2>",
    )
    fields.update(changes)
    return VerifierSemanticSubject(**fields)


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _run(ws, scenario, subject=None):
    """The real session inside a real run lock: ``scenario(host, pending)`` is the desktop host on the real socket while the workflow awaits."""
    subject = subject or _subject()
    canonical, session, host = _session(ws)
    outcome = {}

    async def main_():
        awaiting = launch_module.request_verifier_semantic_review(subject)
        assert awaiting is not None
        task = asyncio.ensure_future(awaiting)
        pending = await _wait_pending(canonical)
        outcome["pending"] = copy.deepcopy(pending)
        outcome["scenario"] = await asyncio.to_thread(scenario, host, pending)
        outcome["decision"] = await asyncio.wait_for(task, 20)
        outcome["record_at_resume"] = load_launch_record(canonical, LID)["pending_approval"]

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    return canonical, outcome, subject


# ------------------------------------------------------------------ the closed tables

def test_decision_validity_is_per_review_kind_and_the_earlier_kinds_keep_exactly_their_words():
    assert KINDS == ("change_approval", "runtime_command", "verifier_defect_repair_authorization", "verifier_semantic_review")
    assert set(DECISIONS_BY_KIND) == set(KINDS)
    for kind in (KIND_CHANGE, KIND_RUNTIME, KIND_VERIFIER_REPAIR):
        assert DECISIONS_BY_KIND[kind] == ("approve", "reject")
    assert DECISIONS_BY_KIND[KIND_VERIFIER_SEMANTIC] == VERIFIER_SEMANTIC_DECISIONS == ("expectation_valid", "expectation_invalid", "cannot_decide")
    assert ALL_DECISIONS == ("approve", "reject", "expectation_valid", "expectation_invalid", "cannot_decide")
    for kind in KINDS:
        for word in ALL_DECISIONS:
            assert decision_valid_for_kind(kind, word) is (word in DECISIONS_BY_KIND[kind])
    assert not decision_valid_for_kind("another_kind", "approve") and not decision_valid_for_kind(KIND_CHANGE, None)
    assert review_channel.DECISIONS == approval.DECISIONS == ("approve", "reject") and review_channel.PROTOCOL_VERSION == 1


@pytest.mark.parametrize("kind", KINDS)
def test_the_wire_parses_every_known_word_and_fails_closed_on_an_unknown_one(kind):
    frame = {"v": 1, "type": "decision", "launch_id": LID, "review_id": "ra-" + "1" * 24, "kind": kind, "candidate_digest": "sha256:" + "a" * 64,
             "capability": "c" * 64}
    for word in ALL_DECISIONS:   # well-formed: whether the word fits the open review's kind is the waiter's typed refusal, not a protocol error
        assert review_channel.parse_frame(review_channel.encode_frame({**frame, "decision": word}).rstrip(b"\n"))["decision"] == word
    for junk in ("maybe", "APPROVE", "", None, 1, ["approve"]):
        with pytest.raises(review_channel.ChannelProtocolError) as caught:
            review_channel.parse_frame(review_channel.encode_frame({**frame, "decision": junk}).rstrip(b"\n"))
        assert caught.value.code == review_channel.INVALID_FIELDS


# ------------------------------------------------------------------ wire -> durable record -> waiter -> caller

@pytest.mark.parametrize("word", VERIFIER_SEMANTIC_DECISIONS)
def test_each_semantic_outcome_is_durable_before_the_caller_resumes_and_arrives_exactly(ws, word):
    canonical, outcome, subject = _run(ws, lambda host, pending: host.decide(word))

    decision = outcome["decision"]
    assert isinstance(decision, VerifierSemanticDecision) and (decision.decision, decision.fingerprint) == (word, subject.fingerprint)
    assert (decision.valid, decision.invalid, decision.cannot_decide) == tuple(word == w for w in VERIFIER_SEMANTIC_DECISIONS) and decision.decided
    assert verifier_semantic_matches(decision, subject)
    record = outcome["record_at_resume"]
    # the status has no polarity; the word is the authoritative field. APPROVED / REJECTED are never written for this kind.
    assert record["status"] == "DECIDED" and record["decision"]["decision"] == word and record["decision"]["source"] == "review-channel"
    assert outcome["scenario"]["accepted"] is True and outcome["scenario"]["code"] == "ACCEPTED"


def test_the_pending_review_holds_the_evidence_every_binding_and_its_own_three_words(ws):
    seen = {}

    def scenario(host, pending):
        seen["frame"] = dict(host.review())
        return host.decide(CANNOT_DECIDE)

    _canonical, outcome, subject = _run(ws, scenario)
    pending = outcome["pending"]
    assert pending["kind"] == KIND_VERIFIER_SEMANTIC and pending["status"] == "PENDING" and pending["decision"] is None
    assert pending["candidate_digest"] == pending["semantic_fingerprint"] == subject.fingerprint
    # the one durable review record also carries what a VALID decision is authority FOR (no phase, no failure identity): there is no second store
    assert pending["semantic_authority_fingerprint"] == subject.semantic_authority_fingerprint != subject.fingerprint
    assert pending["question"] == policy.VERIFIER_SEMANTIC_QUESTION and pending["notice"] == policy.VERIFIER_SEMANTIC_NOTICE
    assert pending["decisions"] == ["expectation_valid", "expectation_invalid", "cannot_decide"]
    assert pending["verifier_files"] == [{"path": TEST, "sha256": _hex(SOURCE), "source": SOURCE}]
    assert pending["contract_text"] == GOAL and pending["contract_digest"] == _hex(GOAL)
    assert pending["acceptance_criteria"] == [{"id": "AC1", "text": "count() returns the number of items"}]
    assert pending["invariants"] == [{"id": "GI1", "text": "no item is counted twice"}]
    assert pending["review_phase"] == REVIEW_PHASE_FAILING and pending["outcome"] == subject.outcome and pending["verifier_unit_id"] == "s5"
    assert pending["bindings"] == {"run_id": subject.run_id, "plan_hash": subject.plan_hash, "goal_digest": _hex(GOAL),
                                   "verification_contract_digest": "vc" * 32, "failure_identity": "f1" * 32}
    assert verifier_semantic_entry_verified(pending)
    assert seen["frame"]["kind"] == KIND_VERIFIER_SEMANTIC and seen["frame"]["candidate_digest"] == subject.fingerprint


@pytest.mark.parametrize("word", ["approve", "reject"])
def test_a_binary_word_on_a_semantic_review_is_refused_typed_and_nothing_is_recorded_or_resumed(ws, word):
    seen = {}

    def scenario(host, pending):
        seen["refusal"] = host.decide(word)                                   # the live capability, the right review, the wrong vocabulary
        seen["record_after_refusal"] = copy.deepcopy(load_launch_record(host.workspace, LID)["pending_approval"])
        return host.decide(EXPECTATION_VALID)                                 # the review is still open and its capability was not consumed

    _canonical, outcome, subject = _run(ws, scenario)
    assert seen["refusal"] == {"v": 1, "type": "result", "review_id": outcome["pending"]["review_id"], "accepted": False,
                               "code": "WRONG_DECISION_FOR_REVIEW_KIND"}
    assert seen["record_after_refusal"]["status"] == "PENDING" and seen["record_after_refusal"]["decision"] is None
    assert outcome["decision"] == VerifierSemanticDecision.decided_as(EXPECTATION_VALID, subject.fingerprint)   # never read as a rejection


@pytest.mark.parametrize("word", VERIFIER_SEMANTIC_DECISIONS)
def test_a_semantic_word_on_a_binary_review_is_refused_typed_and_is_never_a_rejection(ws, word):
    canonical, session, host = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval([{"filepath": "a.py", "content": "+x = 1\n"}], "review this change"))
        await _wait_pending(canonical)
        seen["refusal"] = await asyncio.to_thread(host.decide, word)
        seen["record_after_refusal"] = copy.deepcopy(load_launch_record(canonical, LID)["pending_approval"])
        seen["accepted"] = await asyncio.to_thread(host.decide, "approve")
        seen["outcome"] = await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    assert (seen["refusal"]["accepted"], seen["refusal"]["code"]) == (False, "WRONG_DECISION_FOR_REVIEW_KIND")
    assert seen["record_after_refusal"]["status"] == "PENDING"
    assert seen["accepted"]["accepted"] is True and seen["outcome"].approved is True and _pending(canonical)["status"] == "APPROVED"


def test_the_waiter_itself_never_records_a_word_of_another_kind(ws):
    """Defence behind the channel's own refusal: were a channel ever to hand the waiter a word of another kind, it is not recorded as a decision of this review,
    never a rejection and never an approval - the review ends undecided."""
    class Channel:
        def __init__(self, word):
            self.word = word

        async def offer(self, **_kw):
            return None

        async def await_decision(self, _expires):
            return review_channel.ChannelOutcome(review_channel.OUTCOME_DECIDED, decision=self.word)

        def abandon(self):
            return None

        def close(self):
            return None

    canonical, session, host = _session(ws)
    try:
        with begin_mutating_run(canonical):
            session.attach_review_channel(Channel("approve"))
            semantic = asyncio.run(launch_module.request_verifier_semantic_review(_subject()))
            assert semantic == VerifierSemanticDecision.none(policy.APPROVAL_RESULT_UNRECOGNIZED)
            record = _pending(canonical)
            assert record["status"] == "EXPIRED" and record["decision"]["decision"] is None
            session.attach_review_channel(Channel(CANNOT_DECIDE))
            change = asyncio.run(launch_module.request_change_approval([{"filepath": "a.py", "content": "+x = 1\n"}], "review this change"))
            assert change.unavailable and not change.declined and not change.approved
            assert _pending(canonical)["status"] == "EXPIRED"
    finally:
        session.finish(0, None, None)
        host.close()


def test_a_decision_of_another_kind_or_for_another_fingerprint_decides_nothing(ws):
    refusals = []

    def scenario(host, pending):
        for override in ({"kind": KIND_CHANGE}, {"kind": KIND_VERIFIER_REPAIR}, {"candidate_digest": "sha256:" + "dd" * 32}, {"capability": "0" * 64}):
            refusals.append(host.decide(EXPECTATION_INVALID, **override)["code"])
        return host.decide(CANNOT_DECIDE)

    _canonical, outcome, _subject_ = _run(ws, scenario)
    assert refusals == ["WRONG_REVIEW_KIND", "WRONG_REVIEW_KIND", "STALE_REVIEW", "CAPABILITY_MISMATCH"]
    assert outcome["decision"].cannot_decide and not outcome["decision"].invalid


# ------------------------------------------------------------------ no decision is never `cannot_decide`

def test_a_review_nobody_answers_expires_with_no_decision(ws, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.2)
    canonical, session, host = _session(ws)
    try:
        with begin_mutating_run(canonical):
            decision = asyncio.run(asyncio.wait_for(launch_module.request_verifier_semantic_review(_subject()), 10))
        assert decision == VerifierSemanticDecision.none(policy.APPROVAL_REVIEW_EXPIRED)
        assert not decision.decided and not decision.cannot_decide and decision.fingerprint is None
        record = _pending(canonical)
        assert record["status"] == "EXPIRED" and record["decision"]["decision"] is None      # an explicit cannot_decide is DECIDED with its word
    finally:
        session.finish(0, None, None)
        host.close()


def test_a_decision_that_cannot_be_made_durable_is_no_decision(ws, monkeypatch):
    def scenario(host, pending):
        monkeypatch.setattr(launch_module._ACTIVE, "resolve_review", lambda *a, **k: False)   # the record cannot be written
        return host.decide(EXPECTATION_INVALID)

    _canonical, outcome, _subject_ = _run(ws, scenario)
    assert outcome["decision"] == VerifierSemanticDecision.none(policy.APPROVAL_DECISION_NOT_RECORDED)
    assert outcome["record_at_resume"]["status"] == "PENDING"                                # nothing resumed on an unrecorded word


def test_a_launch_without_a_channel_or_a_subject_that_cannot_be_shown_is_never_offered(ws):
    canonical, session, _host = _session(ws, channel=False)
    try:
        assert launch_module.request_verifier_semantic_review(_subject()) is None
        assert load_launch_record(canonical, LID).get("pending_approval") is None
    finally:
        session.finish(0, None, None)
    assert launch_module.request_verifier_semantic_review(_subject()) is None                # no launch at all
    secret = 'String token = "ghp_' + "a" * 36 + '";'
    unshowable = [
        _subject(verifier_sources=((TEST, secret),), verifier_files=((TEST, _hex(secret)),)), _subject(contract_text=""), _subject(verifier_sources=()),
        _subject(run_id=""), _subject(plan_hash=""), _subject(verifier_unit_id=""), _subject(goal_digest=""),
        _subject(review_phase="another_phase"), _subject(failure_identity=None),                        # a failing review names its failure
        _subject(review_phase=REVIEW_PHASE_PASSING),                                                    # a passing review names none
        _subject(acceptance_criteria=(("", "text"),)), _subject(invariants=(("GI1", "token ghp_" + "a" * 36),)),
    ]
    for subject in unshowable:
        assert not verifier_semantic_reviewable(subject) and build_pending_verifier_semantic(LID, "identity", 1, subject) is None
    assert verifier_semantic_reviewable(_subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None))


@pytest.mark.asyncio
async def test_only_a_typed_semantic_channel_is_asked_and_only_a_typed_answer_is_a_decision():
    subject = _subject()

    def plain(files, reason):
        raise AssertionError("the change channel must never be asked a semantic question")

    assert await ask_verifier_semantic(plain, subject) == VerifierSemanticDecision.none(policy.VERIFIER_SEMANTIC_NOT_ASKED)
    assert await ask_verifier_semantic(TypedApprovalCallback(plain, lambda s: None, lambda s: None), subject) == VerifierSemanticDecision.none(
        policy.VERIFIER_SEMANTIC_NOT_ASKED)                                                            # the replay channel is not the semantic channel
    for answer in (True, "expectation_valid", None, policy.VerifierRepairDecision(subject.fingerprint), policy.ApprovalOutcome.approve()):
        typed = TypedApprovalCallback(plain, lambda s: None, None, lambda s, answer=answer: answer)
        assert await ask_verifier_semantic(typed, subject) == VerifierSemanticDecision.none(policy.APPROVAL_RESULT_UNRECOGNIZED)
    asked = []
    typed = TypedApprovalCallback(plain, lambda s: None, None, lambda s: asked.append(s) or VerifierSemanticDecision.decided_as(EXPECTATION_VALID, s.fingerprint))
    assert (await ask_verifier_semantic(typed, subject)).valid and asked == [subject]
    assert (await ask_verifier_semantic(typed, _subject(contract_text=""))).decided is False and asked == [subject]   # unshowable: not asked


def test_a_semantic_decision_is_a_closed_bound_value_with_no_truth_value():
    fingerprint = _subject().fingerprint
    for bad in ((EXPECTATION_VALID, None, None), ("approve", fingerprint, None), (None, fingerprint, "X"), (None, None, None),
                (CANNOT_DECIDE, fingerprint, "X"), ("", fingerprint, None)):
        with pytest.raises(ValueError):
            VerifierSemanticDecision(*bad)
    with pytest.raises(TypeError):
        bool(VerifierSemanticDecision.decided_as(EXPECTATION_VALID, fingerprint))
    with pytest.raises(TypeError):
        bool(VerifierSemanticDecision.none("X"))


# ------------------------------------------------------------------ exact binding

BINDING_DRIFTS = {
    "run": {"run_id": "20261114T060030-00000000"}, "plan": {"plan_hash": "sha256:" + "2c" * 32}, "verifier-unit": {"verifier_unit_id": "s6"},
    "verifier-digest": {"verifier_files": ((TEST, _hex("another verifier")),)}, "verifier-path": {"verifier_files": (("src/test/java/Other.java", _hex(SOURCE)),)},
    "goal": {"goal_digest": _hex("another goal")}, "contract": {"verification_contract_digest": "dd" * 32},
    "criteria-id": {"acceptance_criteria": (("AC2", "count() returns the number of items"),)},
    "criteria-text": {"acceptance_criteria": (("AC1", "count() returns one more than the number of items"),)},
    "criteria-dropped": {"acceptance_criteria": ()},
    "invariant-id": {"invariants": (("GI2", "no item is counted twice"),)}, "invariant-text": {"invariants": (("GI1", "items may be counted twice"),)},
    "failure": {"failure_identity": "f2" * 32}, "phase": {"review_phase": REVIEW_PHASE_PASSING, "failure_identity": None},
    "phase-only": {"review_phase": REVIEW_PHASE_PASSING},
}


@pytest.mark.parametrize("drift", sorted(BINDING_DRIFTS))
def test_a_decision_is_stale_for_any_drifted_binding(drift):
    subject, drifted = _subject(), _subject(**BINDING_DRIFTS[drift])
    for word in VERIFIER_SEMANTIC_DECISIONS:
        decision = VerifierSemanticDecision.decided_as(word, subject.fingerprint)
        assert verifier_semantic_matches(decision, subject) and not verifier_semantic_matches(decision, drifted)
    assert drifted.fingerprint != subject.fingerprint


def test_what_is_only_shown_is_not_in_the_fingerprint_and_a_non_decision_matches_nothing():
    subject = _subject()
    # the exact preimage: every binding and the review KIND itself (domain separation: no other subject's fingerprint can be built from these values)
    preimage = json.dumps({
        "kind": "verifier_semantic_review", "run_id": subject.run_id, "plan_hash": subject.plan_hash, "verifier_unit_id": "s5",
        "verifier_files": [[TEST, _hex(SOURCE)]], "goal_digest": _hex(GOAL), "verification_contract_digest": "vc" * 32,
        "acceptance_criteria": [["AC1", "count() returns the number of items"]], "invariants": [["GI1", "no item is counted twice"]],
        "review_phase": "failing_verifier", "failure_identity": "f1" * 32}, separators=(",", ":"), sort_keys=True)
    assert subject.fingerprint == "sha256:" + _hex(preimage)
    assert _subject(outcome="other gate text").fingerprint == subject.fingerprint
    assert not verifier_semantic_matches(VerifierSemanticDecision.none("X"), subject)
    assert not verifier_semantic_matches(policy.VerifierRepairDecision(subject.fingerprint), subject)   # a replay decision is never semantic authority
    assert subject.fingerprint != policy.VerifierRepairSubject(
        run_id=subject.run_id, plan_hash=subject.plan_hash, recovery_plan_id="r", recovery_generation=0, verifier_unit_id="s5", owner_unit_id="s3",
        verifier_files=subject.verifier_files, goal_digest=subject.goal_digest, verification_contract_digest=subject.verification_contract_digest,
        pre_repair_owner_files=(), repaired_owner_files=(), pre_repair_failure_identity=None, replay_failure_identity="f1" * 32).fingerprint


@pytest.mark.parametrize("tamper", [
    lambda e: e["verifier_files"][0].__setitem__("source", SOURCE.replace("3", "4")),
    lambda e: e["verifier_files"][0].__setitem__("sha256", _hex("another verifier")),
    lambda e: e.__setitem__("contract_text", GOAL + " (edited)"),
    lambda e: e["bindings"].__setitem__("run_id", "20261114T060030-00000000"),
    lambda e: e["bindings"].__setitem__("plan_hash", "sha256:" + "2c" * 32),
    lambda e: e["bindings"].__setitem__("failure_identity", "f2" * 32),
    lambda e: e["bindings"].__setitem__("verification_contract_digest", "dd" * 32),
    lambda e: e["acceptance_criteria"][0].__setitem__("text", "edited criterion"),
    lambda e: e["invariants"][0].__setitem__("id", "GI9"),
    lambda e: e.__setitem__("review_phase", REVIEW_PHASE_PASSING),
    lambda e: e.__setitem__("verifier_unit_id", "s6"),
    lambda e: e.__setitem__("candidate_digest", "sha256:" + "dd" * 32),
    lambda e: e.__setitem__("semantic_fingerprint", "sha256:" + "dd" * 32),
    lambda e: e.__setitem__("semantic_authority_fingerprint", "sha256:" + "dd" * 32),
    lambda e: e.pop("semantic_authority_fingerprint"),
    lambda e: e.__setitem__("kind", KIND_VERIFIER_REPAIR),
    lambda e: e.pop("bindings"),
    lambda e: e.__setitem__("verifier_files", []),
    lambda e: e.pop("invariants"),
], ids=["source", "source-digest", "contract", "run", "plan", "failure", "contract-digest", "criterion-text", "invariant-id", "phase", "unit",
        "candidate-digest", "fingerprint", "authority-fingerprint", "no-authority-fingerprint", "kind", "no-bindings", "no-files", "no-invariants"])
def test_an_altered_record_is_not_verified(tamper):
    entry = build_pending_verifier_semantic(LID, "identity", 1, _subject())
    assert entry is not None and verifier_semantic_entry_verified(entry)
    tamper(entry)
    assert verifier_semantic_entry_verified(entry) is False
    for junk in (None, [], "x", {}):
        assert verifier_semantic_entry_verified(junk) is False


def test_launch_status_serves_the_review_with_kriyas_own_proof(ws):
    seen = {}

    def scenario(host, pending):
        view = run_kup_operation(AppConfig(), {"operation": "launch.status", "workspace": str(ws), "launch_id": LID})["data"]["launch"]
        seen["pending"], seen["view"], seen["capability"] = view["pending_approval"], str(view), host.review()["capability"]
        return host.decide(EXPECTATION_VALID)

    _run(ws, scenario)
    served = seen["pending"]
    assert served["kind"] == KIND_VERIFIER_SEMANTIC and served["verified"] is True and served["status"] == "PENDING"
    assert served["verifier_files"][0]["source"] == SOURCE and served["decisions"] == list(VERIFIER_SEMANTIC_DECISIONS)
    assert seen["capability"] not in seen["view"]


# ------------------------------------------------------------------ per-subject allowance (never the replay path's run-global one)

def test_an_invalid_decision_permits_one_regeneration_per_subject_and_never_a_second():
    allowance = VerifierSemanticAllowance()
    a0, b0 = _subject(), _subject(verifier_unit_id="s6")
    invalid_a = VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, a0.fingerprint)
    assert not allowance.regeneration_spent(a0)
    assert allowance.take_regeneration(invalid_a, a0) is True and allowance.regeneration_spent(a0)
    # V1 of the same subject: new bytes, a new fingerprint, even a new INVALID decision bound to it - the subject already regenerated. No V2.
    a1 = _subject(verifier_files=((TEST, _hex("regenerated verifier")),), failure_identity="f9" * 32)
    assert a1.subject_key == a0.subject_key and a1.fingerprint != a0.fingerprint and allowance.regeneration_spent(a1)
    assert allowance.take_regeneration(VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, a1.fingerprint), a1) is False
    assert allowance.take_regeneration(invalid_a, a0) is False
    # subject B is independent of A
    assert b0.subject_key != a0.subject_key and not allowance.regeneration_spent(b0)
    assert allowance.take_regeneration(VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, b0.fingerprint), b0) is True
    assert _subject(run_id="20261114T060030-00000000").subject_key != a0.subject_key          # another run is another subject


@pytest.mark.parametrize("word", [EXPECTATION_VALID, CANNOT_DECIDE])
def test_valid_and_cannot_decide_permit_no_regeneration_and_carry_no_owner_or_mutation_authority(word):
    allowance, subject = VerifierSemanticAllowance(), _subject()
    decision = VerifierSemanticDecision.decided_as(word, subject.fingerprint)
    assert allowance.take_regeneration(decision, subject) is False and not allowance.regeneration_spent(subject)
    # a decision is a word bound to a fingerprint, plus - for expectation_invalid only (VERIFIER-REGENERATION-BLIND-001, owner contract 2026-11-18) - the
    # person's bounded rationale. It still has no field that could name a defect owner, a file or a repair target, and these two words carry no rationale.
    assert {f.name for f in VerifierSemanticDecision.__dataclass_fields__.values()} == {"decision", "fingerprint", "no_decision_reason", "rationale"}
    assert decision.rationale is None


def test_a_stale_invalid_decision_or_a_non_decision_takes_nothing():
    allowance, subject = VerifierSemanticAllowance(), _subject()
    stale = VerifierSemanticDecision.decided_as(EXPECTATION_INVALID, _subject(plan_hash="sha256:" + "2c" * 32).fingerprint)
    assert allowance.take_regeneration(stale, subject) is False
    assert allowance.take_regeneration(VerifierSemanticDecision.none("X"), subject) is False
    assert allowance.take_regeneration(policy.VerifierRepairDecision(subject.fingerprint), subject) is False
    assert not allowance.regeneration_spent(subject)


# ------------------------------------------------------------------ the `kriya generate` channel

def test_inside_a_launch_the_review_is_held_and_yes_changes_nothing(ws):
    import kriya.cli as cli
    canonical, session, host = _session(ws)
    try:
        for yes in (False, True):
            awaiting = cli._verifier_semantic_decision(_subject(), yes)
            assert inspect.iscoroutine(awaiting), "answered by the durable review (an awaitable), never synchronously"
            awaiting.close()
            assert _pending(canonical)["kind"] == KIND_VERIFIER_SEMANTIC and _pending(canonical)["status"] == "PENDING"
    finally:
        session.finish(0, None, None)
        host.close()


def test_a_launch_that_cannot_hold_the_review_and_an_unattended_yes_run_decide_nothing_and_prompt_nobody(ws, monkeypatch):
    import kriya.cli as cli
    asked = []
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: asked.append(text) or EXPECTATION_VALID)
    _canonical, session, _host = _session(ws, channel=False)
    try:
        assert cli._verifier_semantic_decision(_subject(), False) == VerifierSemanticDecision.none(policy.APPROVAL_LAUNCH_CANNOT_ASK)
    finally:
        session.finish(0, None, None)
    assert cli._verifier_semantic_decision(_subject(), True) == VerifierSemanticDecision.none(policy.VERIFIER_SEMANTIC_NOT_ASKED)   # --yes never decides
    assert asked == []


@pytest.mark.parametrize("typed,expected", [
    ("expectation_valid", EXPECTATION_VALID), (" cannot_decide \n", CANNOT_DECIDE),     # expectation_invalid: tests/test_verifier_regeneration_blind_001.py
    ("", None), ("y", None), ("approve", None), ("reject", None), ("valid", None), ("EXPECTATION_VALID", None),
])
def test_the_terminal_prompt_takes_only_the_three_exact_words(monkeypatch, capsys, typed, expected):
    import kriya.cli as cli
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: typed)
    subject = _subject()
    decision = cli._verifier_semantic_decision(subject, False)
    shown = capsys.readouterr().out
    assert GOAL in shown and SOURCE.strip() in shown and "AC1" in shown and "GI1" in shown and subject.outcome in shown
    if expected is None:
        assert decision == VerifierSemanticDecision.none(policy.APPROVAL_DECISION_NOT_RECORDED)    # never read as cannot_decide
    else:
        assert decision == VerifierSemanticDecision.decided_as(expected, subject.fingerprint)


def test_the_terminal_prompt_puts_the_failing_expectation_first_and_says_what_a_valid_answer_starts(monkeypatch, capsys):
    """VERIFIER-SEMANTIC-FAILING-EXPECTATION-001 (Stage C run 20261119T200403-218cbbf6): the review named neither the failing test nor the real
    consequence of ``expectation_valid``. Shown first: the failing identity and the gate's own detail; asked: about THAT expectation."""
    import kriya.cli as cli
    asked = []
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: asked.append(text) or "cannot_decide")
    shown = (("com.example.WidgetTest.counts", "AssertionFailedError: expected: <3> but was: <2>"),)
    subject = _subject(failure_identity=policy.failing_expectations_identity(["com.example.WidgetTest.counts"]), failing_expectations=shown)
    assert verifier_semantic_reviewable(subject)
    decision = cli._verifier_semantic_decision(subject, False)
    out = capsys.readouterr().out
    assert decision == VerifierSemanticDecision.decided_as(CANNOT_DECIDE, subject.fingerprint)
    assert out.index("FAILING expectation(s) this decision is about") < out.index("--- Canonical contract ---") < out.index("--- Verifier under review")
    assert "com.example.WidgetTest.counts" in out and "AssertionFailedError: expected: <3> but was: <2>" in out
    assert policy.VERIFIER_SEMANTIC_FAILING_QUESTION in asked[0] and policy.VERIFIER_SEMANTIC_QUESTION not in asked[0]
    # the stale statement is gone and the real consequence is stated
    assert "authorizes no change" not in policy.VERIFIER_SEMANTIC_NOTICE and "authorizes no change" not in out
    assert "may then reopen the one production owner" in out and "a different failing set of the same verifier is asked again" in out


def test_the_terminal_prompt_says_so_when_the_failing_test_is_not_identified_and_guesses_nothing(monkeypatch, capsys):
    import kriya.cli as cli
    asked = []
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: asked.append(text) or "cannot_decide")
    cli._verifier_semantic_decision(_subject(), False)                                   # failing, no identity in the report
    out = capsys.readouterr().out
    assert policy.VERIFIER_SEMANTIC_FAILING_IDENTITY_UNAVAILABLE in out and policy.VERIFIER_SEMANTIC_QUESTION in asked[0]
    assert policy.VERIFIER_SEMANTIC_FAILING_QUESTION not in asked[0]
    cli._verifier_semantic_decision(_subject(review_phase=REVIEW_PHASE_PASSING, failure_identity=None, outcome=""), False)   # passing: no failing block
    out = capsys.readouterr().out
    assert "FAILING expectation(s)" not in out and policy.VERIFIER_SEMANTIC_FAILING_IDENTITY_UNAVAILABLE not in out
    assert policy.VERIFIER_SEMANTIC_QUESTION in asked[1]


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_an_interrupted_prompt_is_no_decision(monkeypatch, error):
    import click

    import kriya.cli as cli

    def interrupted(text, **kw):
        raise click.Abort() if error is KeyboardInterrupt else error()

    monkeypatch.setattr(cli.click, "prompt", interrupted)
    assert cli._verifier_semantic_decision(_subject(), False) == VerifierSemanticDecision.none(policy.APPROVAL_DECISION_NOT_RECORDED)


def test_kriya_generate_wires_the_semantic_channel_beside_the_three_existing_ones():
    import kriya.cli as cli
    source = inspect.getsource(cli)
    assert "lambda subject: _verifier_repair_decision(subject, yes),\n" in source
    assert "lambda subject: _verifier_semantic_decision(subject, yes))" in source
