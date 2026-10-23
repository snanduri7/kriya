"""GUI final + D2 M2: human review of a change inside a desktop launch (kriya/control/launch_approval.py).

The change gate's callback stays the one continuation point; these tests pin that the decision is Kriya-authoritative, bound to the exact pending review (launch,
review id, kind, candidate digest, live capability), durable BEFORE the workflow resumes, and fail-closed on every stale, replayed, foreign or late request.

D2 M2: the decision arrives ONLY over the launch's private review channel (kriya/control/review_channel.py; the host end is tests/_review_channel_host.py, a peer on a
real socket pair). The persisted decision file is audit output - it is written after the decision was accepted and never consulted as authority, which the forgery
tests below prove by planting every file a same-UID workload could write. The channel's own protocol is pinned in tests/test_d2_m2_review_channel.py."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from _review_channel_host import OMIT, attach_review_channel, channel_decide
from click.testing import CliRunner

import kriya.control.launch_approval as approval
from kriya.cli import main
from kriya.config import AppConfig
from kriya.context.workspace_identity import canonical_workspace, workspace_identity
from kriya.control import launch as launch_module
from kriya.control.launch import LaunchSession
from kriya.control.persistence import launch_review_decision_path, load_launch_record, save_launch_review_decision
from kriya.control.run_coordinator import begin_mutating_run
from kriya.kup.cli_ops import run_kup_operation
from kriya.policy.approval import (
    APPROVAL_CHANNEL_UNAVAILABLE,
    APPROVAL_DECISION_NOT_RECORDED,
    APPROVAL_LAUNCH_CANNOT_ASK,
    APPROVAL_REVIEW_EXPIRED,
    ApprovalOutcome,
)

LID = "20261010T101010-abcdef01"
OTHER_LID = "20261010T101010-abcdef02"
FILES = [{"filepath": "hello.py", "content": "--- a/hello.py\n+++ b/hello.py\n@@ -1 +1 @@\n-old\n+new\n"}]
REASON = "Human-in-the-loop review policy\n\n=== Automated Code Review ===\nLooks right."


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _session(ws, launch_id=LID, *, channel=True):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), launch_id, "goal", {"ready": True}, {})
    host = attach_review_channel(session) if channel else None
    session.open()
    return canonical, session, host


def _decide(canonical, pending, decision="approve", **override):
    """The old test API over the channel (other test modules import it): a decision bound to the pending review's identities, with the overrides applied."""
    return channel_decide(canonical, override.get("launch_id", LID), override.get("review_id", pending["review_id"]),
                          override.get("candidate_digest", pending["candidate_digest"]), decision, override.get("kind", "change_approval"))


def _refusal(canonical, pending, decision="approve", **override):
    with pytest.raises(approval.ApprovalRefused) as caught:
        _decide(canonical, pending, decision, **override)
    return caught.value.code


def _pending(canonical, launch_id=LID):
    return load_launch_record(canonical, launch_id)["pending_approval"]


async def _wait_pending(canonical, launch_id=LID):
    for _ in range(500):
        record = load_launch_record(canonical, launch_id)
        if record and record.get("pending_approval"):
            return record["pending_approval"]
        await asyncio.sleep(0.01)
    raise AssertionError("no pending review was recorded")


def _run(ws, scenario, *, files=FILES, reason=REASON):
    """Drive the real session inside a real run lock: `scenario(host, pending)` plays the part of the desktop host (over the real socket) while the 'workflow' awaits."""
    canonical, session, host = _session(ws)
    outcome = {}

    async def main_():
        awaiting = launch_module.request_change_approval(files, reason)
        assert awaiting is not None
        task = asyncio.ensure_future(awaiting)
        pending = await _wait_pending(canonical)
        outcome["scenario"] = await asyncio.to_thread(scenario, host, pending)
        outcome["approved"] = await asyncio.wait_for(task, 20)
        outcome["record_at_resume"] = load_launch_record(canonical, LID)["pending_approval"]

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    return canonical, outcome


# ------------------------------------------------------------------ the decision itself

def test_valid_approval_is_durable_before_the_workflow_resumes_and_the_file_is_only_an_audit_record(ws):
    canonical, outcome = _run(ws, lambda host, p: host.decide("approve"))
    assert outcome["approved"] == ApprovalOutcome.approve()
    assert outcome["scenario"] == {"v": 1, "type": "result", "review_id": outcome["record_at_resume"]["review_id"], "accepted": True, "code": "ACCEPTED"}
    # the record the workflow sees AT THE MOMENT it resumes already says APPROVED
    assert outcome["record_at_resume"]["status"] == "APPROVED" and outcome["record_at_resume"]["decision"]["decision"] == "approve"
    assert outcome["record_at_resume"]["decision"]["source"] == "review-channel"
    audit = json.loads(Path(launch_review_decision_path(canonical, LID, outcome["record_at_resume"]["review_id"])).read_text())
    assert audit["decision"] == "approve" and audit["source"] == "review-channel" and audit["candidate_digest"] == outcome["record_at_resume"]["candidate_digest"]


def test_valid_rejection_declines_and_is_recorded(ws):
    _canonical, outcome = _run(ws, lambda host, p: host.decide("reject"))
    assert outcome["approved"] == ApprovalOutcome.decline(), "the person said no: DECLINED, not unavailable"
    assert outcome["record_at_resume"]["status"] == "REJECTED" and outcome["record_at_resume"]["decision"]["decision"] == "reject"


def test_the_pending_review_and_the_review_frame_carry_the_exact_identities_the_decision_is_bound_to(ws):
    seen = {}

    def scenario(host, pending):
        seen.update(pending)
        seen["frame"] = dict(host.review())
        return host.decide("approve")

    canonical, _ = _run(ws, scenario)
    assert seen["status"] == "PENDING" and seen["kind"] == "change_approval" and seen["reason"] == REASON
    assert seen["candidate_digest"] == approval.candidate_digest(FILES) and re.match(r"^ra-[0-9a-f]{24}$", seen["review_id"])
    assert seen["files"][0]["filepath"] == "hello.py" and seen["files"][0]["lines_added"] == 1 and seen["files"][0]["lines_removed"] == 1
    assert seen["review_id"] == approval.review_identity(LID, workspace_identity(canonical), 1, seen["candidate_digest"], REASON)
    frame = seen["frame"]
    assert (frame["launch_id"], frame["review_id"], frame["kind"], frame["candidate_digest"]) == (LID, seen["review_id"], "change_approval", seen["candidate_digest"])


def test_a_second_decision_for_an_accepted_review_changes_nothing_and_fails_the_channel_closed(ws):
    def scenario(host, pending):
        review = dict(host.review())
        first = host.decide("approve")
        host.send({"v": 1, "type": "decision", "launch_id": LID, "review_id": review["review_id"], "kind": review["kind"],
                   "candidate_digest": review["candidate_digest"], "decision": "reject", "capability": review["capability"]})  # the same review again, with the genuine capability
        return first

    canonical, outcome = _run(ws, scenario)
    assert outcome["approved"] == ApprovalOutcome.approve(), "the second (reject) never overwrote the first (approve)"
    assert outcome["record_at_resume"]["status"] == "APPROVED"
    assert json.loads(Path(launch_review_decision_path(canonical, LID, outcome["record_at_resume"]["review_id"])).read_text())["decision"] == "approve"


def test_a_wrong_digest_or_review_id_is_refused_and_the_run_keeps_waiting(ws):
    def scenario(host, pending):
        codes = [host.decide("approve", candidate_digest="sha256:" + "0" * 64)["code"], host.decide("approve", review_id="ra-" + "0" * 24)["code"]]
        return codes, host.decide("approve")

    _canonical, outcome = _run(ws, scenario)
    assert outcome["scenario"][0] == ["STALE_REVIEW", "STALE_REVIEW"] and outcome["approved"] == ApprovalOutcome.approve()


def test_a_wrong_or_missing_capability_is_refused_and_does_not_spend_the_live_one(ws):
    def scenario(host, pending):
        codes = [host.decide("approve", capability="0" * 64)["code"], host.decide("approve", capability=OMIT)["code"], host.decide("approve", capability=None)["code"]]
        return codes, host.decide("approve")

    _canonical, outcome = _run(ws, scenario)
    assert outcome["scenario"][0] == ["CAPABILITY_MISMATCH"] * 3 and outcome["approved"] == ApprovalOutcome.approve()


def test_a_review_superseded_by_a_new_candidate_cannot_be_approved_any_more(ws):
    canonical, session, host = _session(ws)
    seen = {}

    async def main_():
        first = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        old = await _wait_pending(canonical)
        seen["old"] = old
        old_frame = await asyncio.to_thread(host.review)
        second = asyncio.ensure_future(launch_module.request_change_approval([{"filepath": "hello.py", "content": "different diff\n"}], REASON))
        for _ in range(500):
            if _pending(canonical)["review_id"] != old["review_id"]:
                break
            await asyncio.sleep(0.01)
        new_frame = await asyncio.to_thread(host.next_review)
        assert new_frame["review_id"] != old_frame["review_id"] and new_frame["capability"] != old_frame["capability"]
        seen["code"] = (await asyncio.to_thread(host.decide, "approve", review_id=old_frame["review_id"], candidate_digest=old_frame["candidate_digest"], capability=old_frame["capability"]))["code"]
        await asyncio.to_thread(host.decide, "approve")
        seen["second"] = await asyncio.wait_for(second, 20)
        seen["first"] = await asyncio.wait_for(first, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    assert seen["code"] == "STALE_REVIEW" and seen["second"] == ApprovalOutcome.approve()
    assert seen["first"] == ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED), "the superseded waiter ends unavailable, never approved"
    history = load_launch_record(canonical, LID)["approval_history"]
    assert history and history[0]["review_id"] == seen["old"]["review_id"] and history[0]["status"] == "EXPIRED"


def test_a_decision_naming_another_launch_is_refused(ws):
    def scenario(host, pending):
        code = host.decide("approve", launch_id=OTHER_LID)["code"]
        return code, host.decide("approve")

    _canonical, outcome = _run(ws, scenario)
    assert outcome["scenario"][0] == "STALE_REVIEW" and outcome["approved"] == ApprovalOutcome.approve()


@pytest.mark.parametrize("override", [
    {"review_id": "not-a-review"}, {"review_id": "ra-XYZ"}, {"candidate_digest": "sha256:abc"}, {"candidate_digest": "../../etc/passwd"},
    {"decision": "approve-always"}, {"decision": "Approve"}, {"decision": ""}, {"launch_id": "../escape"}, {"launch_id": "20261010T101010-ABCDEF01"},
    {"kind": "anything"}, {"v": 2}, {"type": "approve"}, {"surprise": 1},
])
def test_a_malformed_decision_frame_fails_the_channel_closed_and_the_review_ends_unavailable(ws, override):
    def scenario(host, pending):
        host.send({**host.decision_frame("approve"), **override})  # a frame that is NOT what the protocol allows
        return None

    canonical, outcome = _run(ws, scenario)
    assert outcome["approved"] == ApprovalOutcome.unavailable_because(APPROVAL_CHANNEL_UNAVAILABLE)
    assert outcome["record_at_resume"]["status"] == "EXPIRED" and outcome["record_at_resume"]["decision"]["source"] == "channel-failed"
    assert not os.path.exists(launch_review_decision_path(canonical, LID, outcome["record_at_resume"]["review_id"]))


def test_a_review_nobody_answers_expires_and_declines(ws, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.2)
    canonical, session, host = _session(ws)
    try:
        with begin_mutating_run(canonical):
            assert asyncio.run(asyncio.wait_for(launch_module.request_change_approval(FILES, REASON), 10)) == ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED)
        record = _pending(canonical)
        assert record["status"] == "EXPIRED" and record["decision"]["source"] == "timeout"
    finally:
        session.finish(0, None, None)
        host.close()


def test_a_decision_that_arrives_after_the_review_expired_is_not_honoured_even_if_it_is_valid(ws, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.3)
    canonical, session, host = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        await _wait_pending(canonical)
        review = await asyncio.to_thread(host.review)
        seen["approved"] = await asyncio.wait_for(task, 10)  # the review ran out with nobody deciding
        host.send(host.decision_frame("approve"))  # a perfectly bound decision with the genuine capability, too late
        seen["review"] = review

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    assert seen["approved"] == ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED)
    assert _pending(canonical)["status"] == "EXPIRED"
    assert not os.path.exists(launch_review_decision_path(canonical, LID, _pending(canonical)["review_id"]))


def test_a_late_decision_poisons_the_channel_for_the_next_review_which_is_then_unavailable_not_approved(ws, monkeypatch):
    """Review F3 under the channel: a decision for a review that already ended arrives when no review is open -> out of order -> the channel fails closed, so the NEXT
    review can not be answered at all (UNAVAILABLE) rather than being open to a replay."""
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.2)
    canonical, session, host = _session(ws)
    seen = {}

    async def main_():
        first = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        await _wait_pending(canonical)
        await asyncio.to_thread(host.review)
        seen["first"] = await asyncio.wait_for(first, 10)
        host.send(host.decision_frame("approve"))
        await asyncio.sleep(0.1)
        seen["second"] = await asyncio.wait_for(launch_module.request_change_approval([{"filepath": "x.py", "content": "d\n"}], REASON), 10)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    assert seen["first"] == ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED)
    assert seen["second"] == ApprovalOutcome.unavailable_because(APPROVAL_CHANNEL_UNAVAILABLE) and not seen["second"].approved


def test_a_run_interrupted_while_the_review_waits_closes_the_review_as_undecided_and_forgets_its_capability(ws):
    canonical, session, host = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        await _wait_pending(canonical)
        await asyncio.to_thread(host.review)
        await asyncio.sleep(0.1)  # the wait has begun
        task.cancel()  # the SIGINT path: the waiting coroutine is cancelled mid-wait
        try:
            await task
        except asyncio.CancelledError:
            seen["cancelled"] = True

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(130, None, "KeyboardInterrupt")
        host.close()
    record = _pending(canonical)
    assert seen["cancelled"] is True
    assert record["status"] == "EXPIRED" and record["decision"]["source"] == "interrupted", "an interrupted run must not leave its review PENDING"


def test_a_launch_that_ends_with_an_undecided_review_never_leaves_it_pending(ws):
    canonical, session, host = _session(ws)
    awaiting = launch_module.request_change_approval(FILES, REASON)
    awaiting.close()  # the wait never began (a crash between the callback and the await)
    assert _pending(canonical)["status"] == "PENDING"
    session.finish(1, None, "killed")
    host.close()
    record = _pending(canonical)
    assert record["status"] == "EXPIRED" and record["decision"]["source"] == "launch-ended"


def test_a_vanished_host_ends_the_review_unavailable_instead_of_waiting_forever(ws):
    canonical, session, host = _session(ws)

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        await _wait_pending(canonical)
        await asyncio.to_thread(host.review)
        host.close()  # the host died with the review in front of it
        return await asyncio.wait_for(task, 10)

    try:
        with begin_mutating_run(canonical):
            result = asyncio.run(main_())
        record = _pending(canonical)
    finally:
        session.finish(0, None, None)
    assert result == ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED) and record["decision"]["source"] == "host-gone"
    assert not os.path.exists(launch_review_decision_path(canonical, LID, record["review_id"]))


# ------------------------------------------------------------------ D2 M2: files are never approval authority

def _plant(canonical, pending, **forgery):
    """The strongest file a same-UID workload could write: stamped by Kriya's own writer, every identity bound, an 'approve'."""
    payload = {"schema": 1, "launch_id": LID, "review_id": pending["review_id"], "candidate_digest": pending["candidate_digest"],
               "workspace_identity": workspace_identity(canonical), "decision": "approve", "decided_at": "now", "nonce": "x", **forgery}
    save_launch_review_decision(canonical, LID, pending["review_id"], payload)


@pytest.mark.parametrize("forgery", [
    {}, {"candidate_digest": "sha256:" + "1" * 64}, {"workspace_identity": "someone-elses-workspace"}, {"launch_id": OTHER_LID}, {"review_id": "ra-" + "9" * 24},
    {"decision": "approve-always"}, {"schema": 2}, {"capability": "0" * 64}, {"token_sha256": "0" * 64, "token": "attacker"},
])
def test_a_decision_file_is_never_a_decision_not_even_a_perfectly_forged_one(ws, monkeypatch, forgery):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.4)
    canonical, session, host = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        pending = await _wait_pending(canonical)
        await asyncio.to_thread(_plant, canonical, pending, **forgery)
        seen["approved"] = await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    assert not seen["approved"].approved, f"a forged approval file ({forgery}) was honoured"
    assert seen["approved"] == ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED) and _pending(canonical)["status"] == "EXPIRED"


def test_a_planted_file_before_the_real_decision_neither_approves_nor_blocks_the_real_decision(ws):
    def scenario(host, pending):
        _plant(canonical_workspace(str(ws)), pending, decision="reject")  # an attacker pre-writes a REJECT at the decision's name
        return host.decide("approve")

    canonical, outcome = _run(ws, scenario)
    assert outcome["approved"] == ApprovalOutcome.approve() and outcome["record_at_resume"]["status"] == "APPROVED"
    planted = json.loads(Path(launch_review_decision_path(canonical, LID, outcome["record_at_resume"]["review_id"])).read_text())
    assert planted["decision"] == "reject", "the audit write never overwrites a document that is already there; it is output only and changes nothing"


def test_a_workload_has_no_cli_action_that_reaches_the_review(ws, monkeypatch):
    """`kriya launch-decision` is deleted (D2 M2): invoking it is a usage error that records nothing, so the only decision is the one over the channel."""
    monkeypatch.chdir(ws)

    def scenario(host, pending):
        runner = CliRunner()
        forged = runner.invoke(main, ["launch-decision", "--launch-id", LID, "--review-id", pending["review_id"], "--candidate-digest", pending["candidate_digest"], "--decision", "approve"])
        return forged, host.decide("reject")

    canonical, outcome = _run(ws, scenario)
    forged, real = outcome["scenario"]
    assert forged.exit_code == 2 and "No such command" in forged.output
    assert real["accepted"] is True and outcome["approved"] == ApprovalOutcome.decline()  # the forged approve changed nothing; the person's real reject stood
    audit = json.loads(Path(launch_review_decision_path(canonical, LID, outcome["record_at_resume"]["review_id"])).read_text())
    assert audit["decision"] == "reject", "the only document at the decision's name is the audit of the REAL decision"


def test_the_capability_is_never_written_anywhere_durable_or_logged(ws, caplog):
    caplog.set_level("DEBUG")
    seen = {}

    def scenario(host, pending):
        seen["capability"] = host.review()["capability"]
        return host.decide("approve")

    canonical, _outcome = _run(ws, scenario)
    capability = seen["capability"]
    home = Path(os.environ["KRIYA_AUTHORITY_HOME"])
    leaked = [str(p) for p in list(home.rglob("*")) + list(Path(canonical).rglob("*")) if p.is_file() and capability in p.read_text(errors="ignore")]
    assert leaked == [] and capability not in caplog.text


def test_a_launch_without_a_private_channel_cannot_be_asked_to_approve_anything(ws):
    canonical, session, _none = _session(ws, channel=False)
    try:
        assert launch_module.request_change_approval(FILES, REASON) is None, "no channel: the review is not even offered"
        from kriya.workflow.runtime_command_approval import build_subject
        subject = build_subject(unit_id="s1", criterion="prints Hello", working_directory="/wt/run-1", commands=[["python", "main.py"]], input_channel="none",
                                stdin_payload=None, reason="Allow Kriya to execute these command(s)?")
        assert launch_module.request_runtime_command_approval(subject) is None, "a REAL, showable subject is still not offered without a channel"
        assert load_launch_record(canonical, LID)["pending_approval"] is None
        outcome = asyncio.run(approval._wait(session, "ra-" + "1" * 24, "sha256:" + "a" * 64))  # the guard for a channel that went away mid-launch
        assert outcome == ApprovalOutcome.unavailable_because(APPROVAL_CHANNEL_UNAVAILABLE)
    finally:
        session.finish(0, None, None)


def test_resolving_a_review_requires_it_to_be_the_pending_one(ws):
    canonical, session, host = _session(ws)
    try:
        awaiting = launch_module.request_change_approval(FILES, REASON)
        awaiting.close()
        pending = _pending(canonical)
        assert session.resolve_review("ra-" + "7" * 24, "APPROVED", {"decision": "approve"}) is False, "another review's id"
        assert _pending(canonical)["status"] == "PENDING"
        assert session.resolve_review(pending["review_id"], "APPROVED", {"decision": "approve"}) is True
        assert session.resolve_review(pending["review_id"], "REJECTED", {"decision": "reject"}) is False, "an already-resolved review cannot be flipped"
        assert _pending(canonical)["status"] == "APPROVED"
    finally:
        session.finish(0, None, None)
        host.close()


def test_if_the_approval_cannot_be_recorded_nothing_is_approved(ws):
    # A private MonkeyPatch, not the fixture: undoing the fixture would also revert the autouse KRIYA_AUTHORITY_HOME isolation, and session.finish below
    # would then write the launch record into the real ~/.kriya/authority.
    local = pytest.MonkeyPatch()

    def scenario(host, pending):
        real_save = launch_module.save_launch_record

        def failing(workspace, launch_id, record):
            if (record.get("pending_approval") or {}).get("status") == "APPROVED":
                raise OSError("disk full")
            return real_save(workspace, launch_id, record)
        local.setattr(launch_module, "save_launch_record", failing)
        return host.decide("approve")

    canonical, session, host = _session(ws)
    outcome = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        pending = await _wait_pending(canonical)
        await asyncio.to_thread(scenario, host, pending)
        outcome["approved"] = await asyncio.wait_for(task, 20)

    local.setattr(approval, "APPROVAL_WAIT_SECONDS", 5.0)
    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        local.undo()
        session.finish(0, None, None)
        host.close()
    assert outcome["approved"] == ApprovalOutcome.unavailable_because(APPROVAL_DECISION_NOT_RECORDED), "an approval that could not be made durable must never let the workflow continue - and is not a decline either"


# ------------------------------------------------------------------ the callback and the retired typed action

def test_launch_status_exposes_the_pending_review_to_the_desktop_but_never_the_capability(ws):
    seen = {}

    def scenario(host, pending):
        view = run_kup_operation(AppConfig(), {"operation": "launch.status", "workspace": str(ws), "launch_id": LID})["data"]["launch"]
        seen["pending"] = view["pending_approval"]
        seen["capability"] = host.review()["capability"]
        seen["view"] = json.dumps(view)
        return host.decide("reject")

    _run(ws, scenario)
    assert seen["pending"]["status"] == "PENDING" and seen["pending"]["reason"] == REASON and seen["pending"]["files"][0]["filepath"] == "hello.py"
    assert seen["capability"] not in seen["view"]


def test_the_decision_action_is_deleted_everywhere_a_review_could_once_be_decided_from_outside_the_channel():
    """Structural (D2 M2 slice 4): no CLI command, no application service, no client-contract entry, no authority-port member and no owner function remains."""
    import glob

    from kriya.application.compose import SERVICE_NAMES
    from kriya.client.contract import PORTS
    from kriya.policy.authority import AuthorityPort
    assert "launch-decision" not in main.commands and not hasattr(approval, "decide") and not hasattr(AuthorityPort, "decide_review")
    assert "approvals" not in SERVICE_NAMES and "approvals" not in PORTS
    assert not os.path.exists("kriya/application/approval.py")
    leftovers = [p for p in glob.glob("kriya/**/*.py", recursive=True) if "launch-decision" in open(p, encoding="utf-8").read() and "docs" not in p]
    assert leftovers == [], leftovers


def test_inside_a_launch_a_change_is_held_for_review_and_everything_else_is_unavailable_synchronously(ws):
    import inspect

    import kriya.cli as cli
    canonical, session, host = _session(ws)
    try:
        for yes in (False, True):  # `--yes` can never take effect inside a launch
            awaiting = cli._approval_decision(FILES, REASON, yes)
            assert inspect.iscoroutine(awaiting), "a change is answered by the durable review (an awaitable), never by a boolean"
            awaiting.close()
            assert _pending(canonical)["status"] == "PENDING"
            unavailable = cli._approval_decision([], "Knowledge Guard found gaps", yes)
            assert unavailable == ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK), "nobody was asked: UNAVAILABLE, never a bare False that reads as a human decline"
            assert not unavailable.declined and not unavailable.approved and not inspect.isawaitable(unavailable), "answered synchronously, never an awaitable"
        assert load_launch_record(canonical, LID)["approval_required"]["kind"] == "other_approval"
    finally:
        session.finish(0, None, None)
        host.close()


def test_outside_a_launch_the_callback_is_the_terminal_behaviour_unchanged(monkeypatch):
    import kriya.cli as cli
    asked = []
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: asked.append(text) or "y")
    assert cli._approval_decision(FILES, REASON, False) is True and len(asked) == 1, "no launch: the person is asked at the terminal"
    asked.clear()
    assert cli._approval_decision(FILES, REASON, True) is True and asked == [], "--yes keeps its terminal meaning outside a launch"
    monkeypatch.setattr(cli.click, "prompt", lambda text, **kw: "n")
    assert cli._approval_decision(FILES, REASON, False) is False


def test_the_generate_callback_delegates_to_the_tested_function():
    import inspect

    import kriya.cli as cli
    source = inspect.getsource(cli._generate_impl)
    nested = source[source.index("    def on_change_approval("):]
    nested = nested[: nested.index("\n    def ", 10)] if "\n    def " in nested[10:] else nested[:400]
    assert "return _approval_decision(files, reason, yes)" in nested
    assert "on_approval = TypedApprovalCallback(on_change_approval, lambda subject: _runtime_command_decision(subject, yes))" in source   # the one typed channel for the runtime subject


def test_only_the_change_gate_passes_a_non_empty_change_set_to_an_approval_callback():
    """The knowledge-gap site (workflow.py) calls the callback WITHOUT awaiting it: an awaitable returned there would read as truthy, i.e. APPROVE. So the awaiting
    callback must only ever be handed work that awaits it - the change gate - and every other call site must keep passing an empty change set."""
    import glob

    non_empty = []
    for path in sorted(glob.glob("kriya/workflow/*.py")):
        text = open(path, encoding="utf-8").read()
        for match in re.finditer(r"resolve_approval\(\s*(?:ctx\.)?approval_callback,\s*([^,)]*(?:\([^)]*\))?[^,)]*),", text):
            first = match.group(1).strip()
            if first not in ("[]", "diffs_to_show or []"):
                non_empty.append((path, first))
    # E4: the change gate moved from workflow.py to stage_apply.py (approval_gate)
    assert non_empty == [("kriya/workflow/stage_apply.py", "diffs_to_show")], non_empty
    text = open("kriya/workflow/stage_apply.py", encoding="utf-8").read()
    gate = text[text.index("resolve_approval(approval_callback, diffs_to_show, escalation_reason)") - 40:][:200]
    assert "outcome = await resolve_approval(" in gate, "the change gate awaits the answer through the one approval owner"


def test_no_auto_approval_path_exists_in_the_launch_layer():
    for module in (approval, launch_module):
        text = open(module.__file__, encoding="utf-8").read()
        assert "--yes" not in text and "auto_approve" not in text and "-y" not in re.findall(r"['\"](-y)['\"]", text)


def test_the_waiter_never_reads_the_decision_file_as_authority():
    """Structural: the production code has no reader of a persisted decision document at all (the audit file is written, never read)."""
    import glob
    readers = [p for p in glob.glob("kriya/**/*.py", recursive=True) if "load_launch_review_decision" in open(p, encoding="utf-8").read()]
    assert readers == [], readers
    assert "load_launch_review_decision" not in open(approval.__file__, encoding="utf-8").read()


# --------------------------------------------------------------------------- N1: the real launch callback + the real workflow, end to end

def _run_workflow_in_launch(ws, tmp_path, *, scenario):
    """A real desktop-launch session, the real `cli._approval_decision` as the workflow's approval callback, the real direct workflow. `scenario(canonical, host)` plays the host
    while the workflow waits. -> (result, trace row, whether the workflow's file reached its workspace)."""
    import sqlite3
    from unittest.mock import AsyncMock

    import kriya.cli as cli
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.core.state_paths import trace_db_path
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "human-in-the-loop"
    cfg.autonomy.run_verification_enabled = False
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=["Step 1: Write code", "Design: Write app.py", '[{"filepath": "app.py", "content": "print(1)"}]', "Review: flagged for human judgment"])
    canonical, session, host = _session(ws)
    project = Path(canonical)   # the workflow runs in the launch's own workspace (one run context)
    seen = {}

    async def main_():
        peer = asyncio.ensure_future(scenario(canonical, host))
        seen["result"] = await WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
            goal="Create app", workspace_path=str(project), approval_callback=lambda files, reason: cli._approval_decision(files, reason, False))
        await peer

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    connection = sqlite3.connect(trace_db_path(cfg))
    connection.row_factory = sqlite3.Row
    row = dict(connection.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone())
    connection.close()
    return seen["result"], row, (project / "app.py").exists()


def test_a_person_who_rejects_in_a_launch_ends_the_run_human_rejected(ws, tmp_path):
    async def peer(canonical, host):
        await _wait_pending(canonical)
        await asyncio.to_thread(host.decide, "reject")

    result, row, applied = _run_workflow_in_launch(ws, tmp_path, scenario=peer)
    assert result["review"] == "Rejected by user during approval gate review." and applied is False
    assert row["status"] == "human_rejected" and row["failure_category"] == "human_rejected"


def test_a_person_who_approves_in_a_launch_lets_the_workflow_apply_its_change(ws, tmp_path):
    async def peer(canonical, host):
        await _wait_pending(canonical)
        await asyncio.to_thread(host.decide, "approve")

    result, row, applied = _run_workflow_in_launch(ws, tmp_path, scenario=peer)
    assert applied is True and row["status"] != "human_rejected" and row["status"] != "approval_required"


def test_a_launch_that_cannot_hold_the_review_ends_the_run_approval_required_not_human_rejected(ws, tmp_path, monkeypatch):
    """N1, MEASURED before the fix: the launch answered a plain False for a review it could not record, and the run ended `human_rejected` although no person decided anything."""
    monkeypatch.setattr(launch_module.LaunchSession, "_store_review_diff", lambda self, pending, files: False)   # the exact diff cannot be stored, so the review is not offered

    async def peer(canonical, host):
        return None

    result, row, applied = _run_workflow_in_launch(ws, tmp_path, scenario=peer)
    assert applied is False and result["files"] == []
    assert "no approval decision could be obtained (APPROVAL_LAUNCH_CANNOT_ASK)" in result["review"] and "nobody declined" in result["review"]
    assert row["status"] == "approval_required" and row["failure_category"] == "approval_required"
    assert load_launch_record(canonical_workspace(str(ws)), LID)["approval_required"]["kind"] == "change_approval", "the launch record still says which decision it could not take"


def test_a_review_nobody_decided_ends_the_run_approval_required_not_human_rejected(ws, tmp_path, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", -1.0)   # already expired when recorded: nobody can decide it

    async def peer(canonical, host):
        return None

    result, row, applied = _run_workflow_in_launch(ws, tmp_path, scenario=peer)
    assert applied is False and "APPROVAL_REVIEW_EXPIRED" in result["review"]
    assert row["status"] == "approval_required"
