"""ENFORCE-VERIFIER-REPLAY-TEST-DEFECT-NEVER-REPAIRED-001, the approval surface (owner rulings 2026-11-13): the VERIFIER-DEFECT REPAIR AUTHORIZATION is a
third typed review kind on the EXISTING launch record, waiter and private review channel (no new permission store), and a terminal prompt outside a launch.

The enforce loop's side is tests/test_verifier_repair_authorization_001.py. Pinned here, on the real launch session and a real socket pair
(tests/_review_channel_host.py): the pending review holds the deterministic evidence and every binding, never the owner's pre-repair bytes; the decision is
bound to the authorization fingerprint and arrives only over the channel; a rejection, an expiry, a missing channel or a decision of another kind authorizes
nothing; `launch.status` serves the review with Kriya's own read-time proof, which fails for any altered record; `--yes` never authorizes."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect

import pytest
from _review_channel_host import channel_decide
from test_launch_approval import LID, _pending, _session, _wait_pending

import kriya.control.launch_approval as approval
from kriya.config import AppConfig
from kriya.control import launch as launch_module
from kriya.control.launch_approval import build_pending_verifier_repair
from kriya.control.persistence import load_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.kup.cli_ops import run_kup_operation
from kriya.policy.approval import (
    KIND_CHANGE,
    KIND_RUNTIME,
    KIND_VERIFIER_REPAIR,
    KINDS,
    VERIFIER_REPAIR_NOTICE,
    VERIFIER_REPAIR_QUESTION,
    TypedApprovalCallback,
    VerifierRepairDecision,
    VerifierRepairSubject,
    ask_verifier_repair,
    verifier_repair_entry_verified,
    verifier_repair_matches,
)

TEST = "src/test/java/com/example/ProtocolParserTest.java"
PARSER = "src/main/java/com/example/ProtocolParser.java"
SOURCE = "class ProtocolParserTest {\n  void roundTrip() { assertEquals(0, decoded.getDataLength()); }\n}\n"
GOAL = "dataLength is the number of bytes in body; encode and decode must not change the Protocol they are given."
O0_BYTES_MARKER = "PRE-REPAIR OWNER BYTES"


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _subject(**changes) -> VerifierRepairSubject:
    fields = dict(
        run_id="20261113T060030-40a14267", plan_hash="sha256:" + "1b" * 32, recovery_plan_id="recovery.s5.0", recovery_generation=0,
        verifier_unit_id="s5", owner_unit_id="s3", verifier_files=((TEST, _hex(SOURCE)),), goal_digest=_hex(GOAL),
        verification_contract_digest="vc" * 32, pre_repair_owner_files=((PARSER, _hex(O0_BYTES_MARKER)),),
        repaired_owner_files=((PARSER, _hex("repaired")),), pre_repair_failure_identity="f0" * 32, replay_failure_identity="f1" * 32,
        contract_text=GOAL, verifier_sources=((TEST, SOURCE),), pre_repair_outcome="java.nio.BufferOverflowException at ProtocolParser.encode:15",
        replay_outcome="ProtocolParserTest.roundTrip:24 expected: <0> but was: <15>", replay_failure_type="targeted_test",
    )
    fields.update(changes)
    return VerifierRepairSubject(**fields)


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _run(ws, scenario, subject=None):
    """The real session inside a real run lock: `scenario(host, pending)` is the desktop host on the real socket while the 'enforce loop' awaits."""
    subject = subject or _subject()
    canonical, session, host = _session(ws)
    outcome = {}

    async def main_():
        awaiting = launch_module.request_verifier_repair_authorization(subject)
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


def test_the_third_kind_is_in_the_one_closed_kind_table_and_the_two_existing_kinds_are_unchanged():
    # VERIFIER-SEMANTIC-REVIEW-001 added a fourth kind after these three (tests/test_verifier_semantic_review_001.py pins the whole table): OLD pinned the
    # table at exactly three; NEW INVARIANT: the three earlier kinds keep their names and order; the old pin was a count tripwire, not a behaviour.
    assert KINDS[:3] == ("change_approval", "runtime_command", "verifier_defect_repair_authorization")
    assert (KIND_CHANGE, KIND_RUNTIME, KIND_VERIFIER_REPAIR) == KINDS[:3]


def test_an_approval_over_the_channel_is_bound_to_the_authorization_fingerprint_and_durable_before_the_loop_resumes(ws):
    canonical, outcome, subject = _run(ws, lambda host, pending: host.decide("approve"))

    decision = outcome["decision"]
    assert isinstance(decision, VerifierRepairDecision) and decision.approved_fingerprint == subject.fingerprint
    assert verifier_repair_matches(decision, subject)
    assert not verifier_repair_matches(decision, _subject(recovery_generation=1))            # it never transfers to another generation
    assert outcome["record_at_resume"]["status"] == "APPROVED" and outcome["record_at_resume"]["decision"]["source"] == "review-channel"
    assert outcome["scenario"] == {"accepted": True, "code": "ACCEPTED"} or outcome["scenario"]["accepted"] is True


def test_the_pending_review_holds_the_deterministic_evidence_and_every_binding_but_never_the_owner_bytes(ws):
    seen = {}

    def scenario(host, pending):
        seen["frame"] = dict(host.review())
        return host.decide("reject")

    _canonical, outcome, subject = _run(ws, scenario)
    pending = outcome["pending"]
    assert pending["kind"] == KIND_VERIFIER_REPAIR and pending["status"] == "PENDING"
    assert pending["candidate_digest"] == pending["authorization_fingerprint"] == subject.fingerprint
    assert pending["question"] == VERIFIER_REPAIR_QUESTION == "Does this verifier expectation conflict with the canonical contract, authorizing one verifier repair?"
    assert pending["notice"] == VERIFIER_REPAIR_NOTICE and "never repairs a verifier on its own" in pending["notice"]
    assert pending["verifier_files"] == [{"path": TEST, "sha256": _hex(SOURCE), "source": SOURCE}]
    assert pending["contract_text"] == GOAL and pending["contract_digest"] == _hex(GOAL)
    assert pending["pre_repair_outcome"] == subject.pre_repair_outcome and pending["replay_outcome"] == subject.replay_outcome
    assert pending["bindings"] == {
        "run_id": subject.run_id, "plan_hash": subject.plan_hash, "recovery_plan_id": "recovery.s5.0", "recovery_generation": 0,
        "goal_digest": _hex(GOAL), "verification_contract_digest": "vc" * 32, "pre_repair_failure_identity": "f0" * 32,
        "replay_failure_identity": "f1" * 32}
    assert pending["pre_repair_owner_files"] == [{"path": PARSER, "sha256": _hex(O0_BYTES_MARKER)}]         # path and digest only
    assert O0_BYTES_MARKER not in str(pending)
    assert verifier_repair_entry_verified(pending)
    # the review frame names the third kind and the fingerprint; the decision is "not authorized"
    assert seen["frame"]["kind"] == KIND_VERIFIER_REPAIR and seen["frame"]["candidate_digest"] == subject.fingerprint
    assert outcome["decision"] == VerifierRepairDecision(None) and outcome["record_at_resume"]["status"] == "REJECTED"


def test_a_decision_of_another_kind_or_for_another_fingerprint_authorizes_nothing_and_the_review_keeps_waiting(ws):
    refusals = []

    def scenario(host, pending):
        for override in ({"kind": KIND_CHANGE}, {"kind": KIND_RUNTIME}, {"candidate_digest": "sha256:" + "dd" * 32}):
            with pytest.raises(approval.ApprovalRefused) as caught:
                channel_decide(host.workspace, LID, pending["review_id"], override.get("candidate_digest", pending["candidate_digest"]), "approve",
                               override.get("kind", KIND_VERIFIER_REPAIR))
            refusals.append(caught.value.code)
        return host.decide("reject")

    _canonical, outcome, _subject_ = _run(ws, scenario)
    assert refusals == ["WRONG_REVIEW_KIND", "WRONG_REVIEW_KIND", "STALE_REVIEW"]
    assert outcome["decision"].approved is False


def test_a_review_nobody_answers_expires_and_authorizes_nothing(ws, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.2)
    canonical, session, host = _session(ws)
    try:
        with begin_mutating_run(canonical):
            decision = asyncio.run(asyncio.wait_for(launch_module.request_verifier_repair_authorization(_subject()), 10))
        assert decision == VerifierRepairDecision(None)
        assert _pending(canonical)["status"] == "EXPIRED"
    finally:
        session.finish(0, None, None)
        host.close()


def test_a_launch_without_a_private_channel_or_a_subject_that_cannot_be_shown_is_never_offered(ws):
    canonical, session, _host = _session(ws, channel=False)
    try:
        assert launch_module.request_verifier_repair_authorization(_subject()) is None
        assert load_launch_record(canonical, LID).get("pending_approval") is None
    finally:
        session.finish(0, None, None)
    secret = 'String token = "ghp_' + "a" * 36 + '";'
    unshowable = _subject(verifier_sources=((TEST, secret),), verifier_files=((TEST, _hex(secret)),))
    assert build_pending_verifier_repair(LID, "identity", 1, unshowable) is None
    assert build_pending_verifier_repair(LID, "identity", 1, _subject(contract_text="")) is None
    assert launch_module.request_verifier_repair_authorization(_subject()) is None        # no launch at all


def test_launch_status_serves_the_review_with_kriyas_own_proof(ws):
    seen = {}

    def scenario(host, pending):
        view = run_kup_operation(AppConfig(), {"operation": "launch.status", "workspace": str(ws), "launch_id": LID})["data"]["launch"]
        seen["pending"] = view["pending_approval"]
        seen["capability"] = host.review()["capability"]
        seen["view"] = str(view)
        return host.decide("approve")

    _run(ws, scenario)
    served = seen["pending"]
    assert served["kind"] == KIND_VERIFIER_REPAIR and served["verified"] is True and served["status"] == "PENDING"
    assert served["verifier_files"][0]["source"] == SOURCE and served["contract_text"] == GOAL
    assert seen["capability"] not in seen["view"] and O0_BYTES_MARKER not in seen["view"]


@pytest.mark.parametrize("tamper", [
    lambda e: e["verifier_files"][0].__setitem__("source", SOURCE.replace("0", "15")),         # the shown source is not the bound bytes
    lambda e: e["verifier_files"][0].__setitem__("sha256", _hex("another verifier")),            # the digest is not the fingerprinted one
    lambda e: e.__setitem__("contract_text", GOAL + " (edited)"),
    lambda e: e["bindings"].__setitem__("recovery_generation", 3),
    lambda e: e["bindings"].__setitem__("run_id", "20261113T060030-00000000"),
    lambda e: e["repaired_owner_files"][0].__setitem__("sha256", _hex("another owner state")),
    lambda e: e["pre_repair_owner_files"][0].__setitem__("sha256", _hex("another pre-repair state")),
    lambda e: e.__setitem__("candidate_digest", "sha256:" + "dd" * 32),
    lambda e: e.__setitem__("authorization_fingerprint", "sha256:" + "dd" * 32),
    lambda e: e.__setitem__("kind", KIND_RUNTIME),
    lambda e: e.pop("bindings"),
    lambda e: e.__setitem__("verifier_files", []),
], ids=["source", "source-digest", "contract", "generation", "run", "o1", "o0", "candidate-digest", "fingerprint", "kind", "no-bindings", "no-files"])
def test_an_altered_record_is_not_verified(tamper):
    entry = build_pending_verifier_repair(LID, "identity", 1, _subject())
    assert entry is not None and verifier_repair_entry_verified(entry)
    tamper(entry)
    assert verifier_repair_entry_verified(entry) is False
    for junk in (None, [], "x", {}):
        assert verifier_repair_entry_verified(junk) is False


def test_the_display_text_of_an_outcome_is_redacted_and_bounded_without_changing_what_is_bound():
    noisy = _subject(replay_outcome="token=ghp_" + "b" * 36 + " " + "x" * 20000)
    entry = build_pending_verifier_repair(LID, "identity", 1, noisy)
    assert entry is not None and "ghp_" + "b" * 36 not in entry["replay_outcome"] and len(entry["replay_outcome"]) <= approval.OUTCOME_MAX_CHARS
    assert entry["candidate_digest"] == noisy.fingerprint == _subject().fingerprint and verifier_repair_entry_verified(entry)


# ------------------------------------------------------------------ the `kriya generate` channel

def test_inside_a_launch_the_authorization_is_held_for_review_and_yes_changes_nothing(ws):
    import kriya.cli as cli
    canonical, session, host = _session(ws)
    try:
        for yes in (False, True):
            awaiting = cli._verifier_repair_decision(_subject(), yes)
            assert inspect.iscoroutine(awaiting), "answered by the durable review (an awaitable), never synchronously"
            awaiting.close()
            assert _pending(canonical)["kind"] == KIND_VERIFIER_REPAIR and _pending(canonical)["status"] == "PENDING"
    finally:
        session.finish(0, None, None)
        host.close()


def test_inside_a_launch_that_cannot_hold_the_review_nothing_is_authorized_and_nobody_is_prompted(ws, monkeypatch):
    import kriya.cli as cli
    asked = []
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: asked.append(text) or "y")
    _canonical, session, _host = _session(ws, channel=False)
    try:
        assert cli._verifier_repair_decision(_subject(), False) == VerifierRepairDecision(None) and asked == []
    finally:
        session.finish(0, None, None)


def test_in_a_terminal_only_an_explicit_yes_authorizes_and_the_yes_flag_never_does(monkeypatch, capsys):
    import kriya.cli as cli
    subject, asked = _subject(), []
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: asked.append(text) or "y")
    assert cli._verifier_repair_decision(subject, True) == VerifierRepairDecision(None) and asked == [], "--yes is not a judgement about this verifier"
    granted = cli._verifier_repair_decision(subject, False)
    assert granted == VerifierRepairDecision(subject.fingerprint) and len(asked) == 1 and VERIFIER_REPAIR_QUESTION in asked[0]
    shown = capsys.readouterr().out
    for evidence in (GOAL, SOURCE.splitlines()[1].strip(), TEST, subject.pre_repair_outcome, subject.replay_outcome, "never repairs a verifier on its own"):
        assert evidence in shown, evidence
    for answer in ("n", "", "maybe"):
        monkeypatch.setattr(cli.click, "prompt", lambda text, answer=answer, **kw: answer)
        assert cli._verifier_repair_decision(subject, False) == VerifierRepairDecision(None)

    def aborted(text, **kw):
        raise cli.click.Abort()

    monkeypatch.setattr(cli.click, "prompt", aborted)
    assert cli._verifier_repair_decision(subject, False) == VerifierRepairDecision(None)


def test_the_generate_callback_carries_the_third_channel():
    import kriya.cli as cli
    source = inspect.getsource(cli)
    # VERIFIER-SEMANTIC-REVIEW-001: a fourth channel now follows this one in the same call (OLD pinned the call's closing parenthesis; NEW INVARIANT: the
    # third channel is still wired with exactly this lambda; the old text pinned punctuation, not the wiring).
    assert "lambda subject: _verifier_repair_decision(subject, yes)," in source
    typed = TypedApprovalCallback(lambda files, reason: True, lambda subject: None, lambda subject: VerifierRepairDecision(subject.fingerprint))
    assert asyncio.run(ask_verifier_repair(typed, _subject())).approved
