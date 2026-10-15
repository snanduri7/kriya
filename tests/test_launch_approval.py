"""GUI final: human review of a change inside a desktop launch (kriya/control/launch_approval.py).

The change gate's callback stays the one continuation point; these tests pin that the decision is Kriya-authoritative, bound to the exact pending
review (launch, workspace, candidate digest, review id), durable BEFORE the workflow resumes, and fail-closed on every stale, replayed, foreign or
late request."""
from __future__ import annotations

import asyncio
import json
import os
import re

import pytest
from click.testing import CliRunner

import kriya.control.launch_approval as approval
from kriya.cli import main
from kriya.config import AppConfig
from kriya.control import launch as launch_module
from kriya.control.launch import LaunchSession
from kriya.control.persistence import launch_review_decision_path, load_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.workspace_identity import canonical_workspace, workspace_identity
from kriya.kup.cli_ops import run_kup_operation

LID = "20261010T101010-abcdef01"
OTHER_LID = "20261010T101010-abcdef02"
FILES = [{"filepath": "hello.py", "content": "--- a/hello.py\n+++ b/hello.py\n@@ -1 +1 @@\n-old\n+new\n"}]
REASON = "Human-in-the-loop review policy\n\n=== Automated Code Review ===\nLooks right."


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _session(ws, launch_id=LID):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), launch_id, "goal", {"ready": True}, {})
    session.open()
    return canonical, session


def _pending(canonical, launch_id=LID):
    return load_launch_record(canonical, launch_id)["pending_approval"]


async def _wait_pending(canonical, launch_id=LID):
    for _ in range(500):
        record = load_launch_record(canonical, launch_id)
        if record and record.get("pending_approval"):
            return record["pending_approval"]
        await asyncio.sleep(0.01)
    raise AssertionError("no pending review was recorded")


def _decide(canonical, pending, decision="approve", **override):
    args = {"launch_id": LID, "review_id": pending["review_id"], "candidate_digest": pending["candidate_digest"], "decision": decision, **override}
    return approval.decide(canonical, args["launch_id"], args["review_id"], args["candidate_digest"], args["decision"])


def _run(ws, scenario, *, files=FILES, reason=REASON):
    """Drive the real session inside a real run lock: `scenario(canonical, pending)` plays the part of the desktop host while the 'workflow' awaits."""
    canonical, session = _session(ws)
    outcome = {}

    async def main_():
        awaiting = launch_module.request_change_approval(files, reason)
        assert awaiting is not None
        task = asyncio.ensure_future(awaiting)
        pending = await _wait_pending(canonical)
        outcome["scenario"] = await asyncio.to_thread(scenario, canonical, pending)
        outcome["approved"] = await asyncio.wait_for(task, 20)
        outcome["record_at_resume"] = load_launch_record(canonical, LID)["pending_approval"]

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    return canonical, outcome


def _refusal(canonical, pending, decision="approve", **override):
    with pytest.raises(approval.ApprovalRefused) as caught:
        _decide(canonical, pending, decision, **override)
    return caught.value.code


# ------------------------------------------------------------------ the decision itself

def test_valid_approval_is_durable_before_the_workflow_resumes(ws):
    canonical, outcome = _run(ws, lambda c, p: _decide(c, p, "approve"))
    assert outcome["approved"] is True
    assert outcome["scenario"]["accepted"] is True and outcome["scenario"]["decision"] == "approve"
    # the record the workflow sees AT THE MOMENT it resumes already says APPROVED
    assert outcome["record_at_resume"]["status"] == "APPROVED" and outcome["record_at_resume"]["decision"]["decision"] == "approve"
    assert os.path.exists(launch_review_decision_path(canonical, LID, outcome["record_at_resume"]["review_id"]))


def test_valid_rejection_declines_and_is_recorded(ws):
    _canonical, outcome = _run(ws, lambda c, p: _decide(c, p, "reject"))
    assert outcome["approved"] is False
    assert outcome["record_at_resume"]["status"] == "REJECTED" and outcome["record_at_resume"]["decision"]["decision"] == "reject"


def test_the_pending_review_carries_the_exact_identities_the_decision_is_bound_to(ws):
    seen = {}

    def scenario(canonical, pending):
        seen.update(pending)
        return _decide(canonical, pending)

    canonical, _ = _run(ws, scenario)
    assert seen["status"] == "PENDING" and seen["kind"] == "change_approval" and seen["reason"] == REASON
    assert seen["candidate_digest"] == approval.candidate_digest(FILES) and re.match(r"^ra-[0-9a-f]{24}$", seen["review_id"])
    assert seen["files"][0]["filepath"] == "hello.py" and seen["files"][0]["lines_added"] == 1 and seen["files"][0]["lines_removed"] == 1
    assert seen["review_id"] == approval.review_identity(LID, workspace_identity(canonical), 1, seen["candidate_digest"], REASON)


def test_a_duplicate_approval_is_refused_before_and_after_the_waiter_acts(ws, monkeypatch):
    monkeypatch.setattr(approval, "POLL_SECONDS", 5.0)  # the waiter has NOT seen the first decision when the second arrives

    def scenario(canonical, pending):
        first = _decide(canonical, pending, "approve")
        second = _refusal(canonical, pending, "reject")
        monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)
        return first, second

    canonical, outcome = _run(ws, scenario)
    assert outcome["scenario"][1] == "ALREADY_DECIDED" and outcome["approved"] is True, "the second (reject) never overwrote the first (approve)"
    assert _refusal(canonical, outcome["record_at_resume"]) in ("ALREADY_DECIDED", "LAUNCH_ENDED")


def test_a_stale_digest_or_review_id_fails_closed_and_the_run_keeps_waiting(ws):
    def scenario(canonical, pending):
        codes = [_refusal(canonical, pending, candidate_digest="sha256:" + "0" * 64),
                 _refusal(canonical, pending, review_id="ra-" + "0" * 24)]
        return codes, _decide(canonical, pending, "approve")

    _canonical, outcome = _run(ws, scenario)
    assert outcome["scenario"][0] == ["STALE_REVIEW", "STALE_REVIEW"] and outcome["approved"] is True


def test_a_review_superseded_by_a_new_candidate_cannot_be_approved_any_more(ws):
    canonical, session = _session(ws)
    seen = {}

    async def main_():
        first = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        old = await _wait_pending(canonical)
        seen["old"] = old
        second = asyncio.ensure_future(launch_module.request_change_approval([{"filepath": "hello.py", "content": "different diff\n"}], REASON))
        for _ in range(500):
            if _pending(canonical)["review_id"] != old["review_id"]:
                break
            await asyncio.sleep(0.01)
        seen["code"] = await asyncio.to_thread(_refusal, canonical, old)
        new = _pending(canonical)
        await asyncio.to_thread(_decide, canonical, new, "approve")
        seen["second"] = await asyncio.wait_for(second, 20)
        first.cancel()

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    assert seen["code"] == "STALE_REVIEW" and seen["second"] is True
    history = load_launch_record(canonical, LID)["approval_history"]
    assert history and history[0]["review_id"] == seen["old"]["review_id"] and history[0]["status"] == "EXPIRED"


def test_wrong_run_wrong_workspace_and_a_decision_after_the_run_ended_are_refused(ws, tmp_path, monkeypatch):
    def scenario(canonical, pending):
        wrong_run = approval.ApprovalRefused
        with pytest.raises(wrong_run) as run_error:
            approval.decide(canonical, OTHER_LID, pending["review_id"], pending["candidate_digest"], "approve")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with pytest.raises(wrong_run) as folder_error:
            approval.decide(str(elsewhere), LID, pending["review_id"], pending["candidate_digest"], "approve")
        monkeypatch.setattr(approval, "workspace_identity", lambda _path: "not-this-workspace")
        identity_code = _refusal(canonical, pending)
        monkeypatch.undo()
        monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)
        _decide(canonical, pending, "approve")
        return run_error.value.code, folder_error.value.code, identity_code

    canonical, outcome = _run(ws, scenario)
    assert outcome["scenario"] == ("LAUNCH_NOT_FOUND", "LAUNCH_NOT_FOUND", "WRONG_WORKSPACE")
    ended = load_launch_record(canonical, LID)
    assert ended["phase"] == "ENDED"
    with pytest.raises(approval.ApprovalRefused) as late:
        approval.decide(canonical, LID, outcome["record_at_resume"]["review_id"], outcome["record_at_resume"]["candidate_digest"], "approve")
    assert late.value.code == "LAUNCH_ENDED"


@pytest.mark.parametrize("override", [
    {"review_id": "not-a-review"}, {"review_id": "ra-XYZ"}, {"candidate_digest": "sha256:abc"}, {"candidate_digest": "../../etc/passwd"},
    {"decision": "approve-always"}, {"decision": "Approve"}, {"decision": ""}, {"launch_id": "../escape"}, {"launch_id": "20261010T101010-ABCDEF01"},
])
def test_malformed_requests_are_refused_before_any_state_is_read(ws, override):
    canonical, session = _session(ws)
    try:
        pending = {"review_id": "ra-" + "0" * 24, "candidate_digest": "sha256:" + "0" * 64}
        args = {"launch_id": LID, **pending, "decision": "approve", **override}
        with pytest.raises(approval.ApprovalRefused) as caught:
            approval.decide(canonical, args["launch_id"], args["review_id"], args["candidate_digest"], args["decision"])
        assert caught.value.code == "INVALID_REQUEST"
    finally:
        session.finish(0, None, None)


def test_a_review_whose_process_is_gone_cannot_be_approved(ws):
    canonical, session = _session(ws)
    try:
        with begin_mutating_run(canonical):  # the launch is RUNNING and records its review ...
            awaiting = launch_module.request_change_approval(FILES, REASON)
            awaiting.close()
            pending = _pending(canonical)
        # ... and then the process that held the workspace is gone (lock released, record never sealed)
        assert _refusal(canonical, pending) == "LAUNCH_NOT_ACTIVE"
        assert not os.path.exists(launch_review_decision_path(canonical, LID, pending["review_id"]))
    finally:
        session.finish(0, None, None)


def test_a_launch_that_never_started_running_has_nothing_to_approve(ws):
    canonical, session = _session(ws)
    try:
        awaiting = launch_module.request_change_approval(FILES, REASON)
        awaiting.close()
        assert _refusal(canonical, _pending(canonical)) == "NOT_PENDING"
    finally:
        session.finish(0, None, None)


def test_a_review_nobody_answers_expires_and_declines(ws, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.05)
    canonical, session = _session(ws)
    try:
        with begin_mutating_run(canonical):
            assert asyncio.run(asyncio.wait_for(launch_module.request_change_approval(FILES, REASON), 10)) is False
        record = _pending(canonical)
        assert record["status"] == "EXPIRED" and record["decision"]["source"] == "timeout"
        assert _refusal(canonical, record) in ("REVIEW_EXPIRED", "NOT_PENDING")
    finally:
        session.finish(0, None, None)


def test_a_run_interrupted_while_the_review_waits_closes_the_review_as_undecided(ws):
    canonical, session = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        await _wait_pending(canonical)
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
    record = _pending(canonical)
    assert seen["cancelled"] is True
    assert record["status"] == "EXPIRED" and record["decision"]["source"] == "interrupted", "an interrupted run must not leave its review PENDING"


def test_a_launch_that_ends_with_an_undecided_review_never_leaves_it_pending(ws):
    canonical, session = _session(ws)
    awaiting = launch_module.request_change_approval(FILES, REASON)
    awaiting.close()  # the wait never began (a crash between the callback and the await)
    assert _pending(canonical)["status"] == "PENDING"
    session.finish(1, None, "killed")
    record = _pending(canonical)
    assert record["status"] == "EXPIRED" and record["decision"]["source"] == "launch-ended"


def test_a_vanished_host_declines_instead_of_waiting_forever(ws, monkeypatch):
    real = os.getppid()
    canonical, session = _session(ws)  # the launch opened under `real`
    monkeypatch.setattr(approval.os, "getppid", lambda: real + 1)  # the host died before the review was even requested
    try:
        with begin_mutating_run(canonical):
            assert asyncio.run(asyncio.wait_for(launch_module.request_change_approval(FILES, REASON), 10)) is False
            record = _pending(canonical)
            assert record["decision"]["source"] == "host-gone"
            # the run still holds the workspace, but the review is over: a late decision is refused and nothing is written for it
            assert _refusal(canonical, record) == "REVIEW_EXPIRED"
            assert not os.path.exists(launch_review_decision_path(canonical, LID, record["review_id"]))
    finally:
        session.finish(0, None, None)


@pytest.mark.parametrize("forgery", [
    {"candidate_digest": "sha256:" + "1" * 64}, {"workspace_identity": "someone-elses-workspace"}, {"launch_id": OTHER_LID}, {"review_id": "ra-" + "9" * 24},
    {"decision": "approve-always"}, {"schema": 2},
])
def test_a_decision_file_that_does_not_bind_every_identity_is_not_a_decision(ws, monkeypatch, forgery):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.3)
    canonical, session = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        pending = await _wait_pending(canonical)
        from kriya.control.persistence import save_launch_review_decision
        payload = {"schema": 1, "launch_id": LID, "review_id": pending["review_id"], "candidate_digest": pending["candidate_digest"],
                   "workspace_identity": workspace_identity(canonical), "decision": "approve", "decided_at": "now", "nonce": "x", **forgery}
        await asyncio.to_thread(save_launch_review_decision, canonical, LID, pending["review_id"], payload)
        seen["approved"] = await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    assert seen["approved"] is False, f"a forged approval ({forgery}) was honoured"
    assert _pending(canonical)["status"] == "EXPIRED"


def test_a_pending_review_whose_time_has_passed_cannot_be_decided_even_if_the_waiter_has_not_noticed(ws, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", -1.0)  # already expired when recorded
    monkeypatch.setattr(approval, "POLL_SECONDS", 5.0)  # and the waiter has not looked yet

    def scenario(canonical, pending):
        code = _refusal(canonical, pending)
        assert not os.path.exists(launch_review_decision_path(canonical, LID, pending["review_id"]))
        return code

    canonical, session = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        pending = await _wait_pending(canonical)
        seen["code"] = await asyncio.to_thread(scenario, canonical, pending)
        monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)
        seen["approved"] = await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    assert seen["code"] == "REVIEW_EXPIRED" and seen["approved"] is False


def test_a_decision_that_lost_the_race_is_reported_as_already_decided(ws, monkeypatch):
    def scenario(canonical, pending):
        real = approval.load_launch_review_decision

        def other_writer_won(*args):
            stored = real(*args)
            return {**stored, "nonce": "somebody-else"} if stored else stored
        monkeypatch.setattr(approval, "load_launch_review_decision", other_writer_won)
        code = _refusal(canonical, pending)
        monkeypatch.setattr(approval, "load_launch_review_decision", real)
        return code

    canonical, session = _session(ws)
    seen = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        pending = await _wait_pending(canonical)
        seen["code"] = await asyncio.to_thread(scenario, canonical, pending)
        task.cancel()

    monkeypatch.setattr(approval, "POLL_SECONDS", 5.0)
    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    assert seen["code"] == "ALREADY_DECIDED"


def test_a_decision_file_can_never_be_overwritten(ws):
    from kriya.control import persistence
    canonical, session = _session(ws)
    try:
        review = "ra-" + "5" * 24
        persistence.save_launch_review_decision(canonical, LID, review, {"decision": "approve"})
        with pytest.raises(FileExistsError):
            persistence.save_launch_review_decision(canonical, LID, review, {"decision": "reject"})
        assert persistence.load_launch_review_decision(canonical, LID, review)["decision"] == "approve"
        leftovers = [n for n in os.listdir(os.path.dirname(launch_review_decision_path(canonical, LID, review))) if ".staging-" in n]
        assert leftovers == [], "no staging file is left behind"
    finally:
        session.finish(0, None, None)


def test_of_many_simultaneous_decisions_exactly_one_is_accepted_and_it_is_the_one_on_disk(ws):
    """MEASURED defect (independent review F2): the exclusive create was check-then-replace, so two concurrent deciders could both be told 'accepted' with
    contradictory decisions. Real concurrency: many threads race to decide the same review."""
    import threading
    canonical, session = _session(ws)
    results = []
    try:
        with begin_mutating_run(canonical):
            awaiting = launch_module.request_change_approval(FILES, REASON)
            awaiting.close()
            pending = _pending(canonical)
            gate = threading.Barrier(12)

            def contender(index):
                gate.wait()
                try:
                    results.append((index, _decide(canonical, pending, "approve" if index % 2 == 0 else "reject")["decision"]))
                except approval.ApprovalRefused as refusal:
                    results.append((index, refusal.code))

            threads = [threading.Thread(target=contender, args=(i,)) for i in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        accepted = [r for r in results if r[1] in ("approve", "reject")]
        refused = [r for r in results if r[1] not in ("approve", "reject")]
        assert len(results) == 12 and len(accepted) == 1, results
        assert all(code == "ALREADY_DECIDED" for _i, code in refused), results
        from kriya.control.persistence import load_launch_review_decision
        assert load_launch_review_decision(canonical, LID, pending["review_id"])["decision"] == accepted[0][1], "the accepted decision is the one that is on disk"
    finally:
        session.finish(0, None, None)


def test_an_unstamped_decision_file_is_not_a_decision(ws):
    from kriya.control import persistence
    canonical, session = _session(ws)
    try:
        review = "ra-" + "6" * 24
        path = launch_review_decision_path(canonical, LID, review)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "launch_id": LID, "review_id": review, "decision": "approve"}, handle)  # written by something other than Kriya's control writer
        assert persistence.load_launch_review_decision(canonical, LID, review) is None
    finally:
        session.finish(0, None, None)


def test_a_decision_that_arrives_after_the_review_expired_is_not_honoured_even_if_it_is_valid(ws, monkeypatch):
    """Consume-time expiry (review F3): a perfectly bound decision file that is already on disk when the waiter looks, but dates from after the review's recorded end,
    changes nothing - whoever wrote it."""
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", -1.0)  # the review is past its end the moment it is recorded
    canonical, session = _session(ws)
    try:
        with begin_mutating_run(canonical):
            awaiting = launch_module.request_change_approval(FILES, REASON)
            pending = _pending(canonical)
            from kriya.control.persistence import save_launch_review_decision
            payload = {"schema": 1, "launch_id": LID, "review_id": pending["review_id"], "candidate_digest": pending["candidate_digest"],
                       "workspace_identity": workspace_identity(canonical), "decision": "approve", "decided_at": "now", "nonce": "x"}
            save_launch_review_decision(canonical, LID, pending["review_id"], payload)  # present BEFORE the waiter's first look
            assert asyncio.run(asyncio.wait_for(awaiting, 10)) is False
        assert _pending(canonical)["status"] == "EXPIRED" and _pending(canonical)["decision"]["source"] == "timeout"
    finally:
        session.finish(0, None, None)


def test_resolving_a_review_requires_it_to_be_the_pending_one(ws):
    canonical, session = _session(ws)
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


def test_a_review_that_ended_without_a_decision_cannot_be_decided_later_while_the_run_lives(ws):
    canonical, session = _session(ws)
    try:
        with begin_mutating_run(canonical):
            awaiting = launch_module.request_change_approval(FILES, REASON)
            awaiting.close()
            pending = _pending(canonical)
            assert session.resolve_review(pending["review_id"], "EXPIRED", {"decision": None, "source": "timeout"}) is True
            assert _refusal(canonical, _pending(canonical)) == "REVIEW_EXPIRED"
            assert not os.path.exists(launch_review_decision_path(canonical, LID, pending["review_id"]))
    finally:
        session.finish(0, None, None)


def test_if_the_approval_cannot_be_recorded_nothing_is_approved(ws, monkeypatch):
    def scenario(canonical, pending):
        real_save = launch_module.save_launch_record

        def failing(workspace, launch_id, record):
            if (record.get("pending_approval") or {}).get("status") == "APPROVED":
                raise OSError("disk full")
            return real_save(workspace, launch_id, record)
        monkeypatch.setattr(launch_module, "save_launch_record", failing)
        return _decide(canonical, pending, "approve")

    canonical, session = _session(ws)
    outcome = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        pending = await _wait_pending(canonical)
        await asyncio.to_thread(scenario, canonical, pending)
        outcome["approved"] = await asyncio.wait_for(task, 20)

    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 5.0)  # was 0.5: under 8-way load the decision thread landed after the review expired (REVIEW_EXPIRED); this test is about a failed save, not expiry
    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        monkeypatch.undo()
        session.finish(0, None, None)
    assert outcome["approved"] is False, "an approval that could not be made durable must never let the workflow continue"


# ------------------------------------------------------------------ the callback and the typed action

def test_launch_status_exposes_the_pending_review_to_the_desktop(ws):
    seen = {}

    def scenario(canonical, pending):
        view = run_kup_operation(AppConfig(), {"operation": "launch.status", "workspace": str(ws), "launch_id": LID})["data"]["launch"]
        seen["pending"] = view["pending_approval"]
        return _decide(canonical, pending, "reject")

    _run(ws, scenario)
    assert seen["pending"]["status"] == "PENDING" and seen["pending"]["reason"] == REASON and seen["pending"]["files"][0]["filepath"] == "hello.py"


def test_the_cli_action_answers_json_and_exits_non_zero_when_refused(ws, monkeypatch):
    monkeypatch.chdir(ws)

    def scenario(canonical, pending):
        runner = CliRunner()
        base = ["launch-decision", "--launch-id", LID, "--review-id", pending["review_id"], "--candidate-digest", pending["candidate_digest"]]
        refused = runner.invoke(main, [*base[:-1], "sha256:" + "2" * 64, "--decision", "approve"])
        accepted = runner.invoke(main, [*base, "--decision", "approve"])
        return refused, accepted

    _canonical, outcome = _run(ws, scenario)
    refused, accepted = outcome["scenario"]
    assert refused.exit_code == 2 and json.loads(refused.output)["code"] == "STALE_REVIEW"
    assert accepted.exit_code == 0 and json.loads(accepted.output)["accepted"] is True and outcome["approved"] is True


def test_the_cli_action_has_no_option_that_could_broaden_authority():
    names = {param.name for param in main.commands["launch-decision"].params}
    assert names == {"launch_id", "review_id", "candidate_digest", "decision"}, "no -y, no always-approve, no autonomy selector"


def test_inside_a_launch_a_change_is_held_for_review_and_everything_else_is_declined_synchronously(ws):
    import inspect

    import kriya.cli as cli
    canonical, session = _session(ws)
    try:
        for yes in (False, True):  # `--yes` can never take effect inside a launch
            awaiting = cli._approval_decision(FILES, REASON, yes)
            assert inspect.iscoroutine(awaiting), "a change is answered by the durable review (an awaitable), never by a boolean"
            awaiting.close()
            assert _pending(canonical)["status"] == "PENDING"
            declined = cli._approval_decision([], "Knowledge Guard found gaps", yes)
            assert declined is False, "every non-change approval is a plain False: those call sites do not await, and a coroutine would read as truthy (approval)"
        assert load_launch_record(canonical, LID)["approval_required"]["kind"] == "other_approval"
    finally:
        session.finish(0, None, None)


def test_outside_a_launch_the_callback_is_the_terminal_behaviour_unchanged(monkeypatch):
    import kriya.cli as cli
    asked = []
    monkeypatch.setattr(cli.click, "confirm", lambda text, **kw: asked.append(text) or True)
    assert cli._approval_decision(FILES, REASON, False) is True and len(asked) == 1, "no launch: the person is asked at the terminal"
    asked.clear()
    assert cli._approval_decision(FILES, REASON, True) is True and asked == [], "--yes keeps its terminal meaning outside a launch"
    monkeypatch.setattr(cli.click, "confirm", lambda text, **kw: False)
    assert cli._approval_decision(FILES, REASON, False) is False


def test_the_generate_callback_delegates_to_the_tested_function():
    import inspect

    import kriya.cli as cli
    source = inspect.getsource(cli._generate_impl)
    nested = source[source.index("    def on_approval("):]
    nested = nested[: nested.index("\n    def ", 10)]
    assert "return _approval_decision(files, reason, yes)" in nested


def test_only_the_change_gate_passes_a_non_empty_change_set_to_an_approval_callback():
    """The knowledge-gap site (workflow.py) calls the callback WITHOUT awaiting it: an awaitable returned there would read as truthy, i.e. APPROVE. So the awaiting
    callback must only ever be handed work that awaits it - the change gate - and every other call site must keep passing an empty change set."""
    import glob

    non_empty = []
    for path in sorted(glob.glob("kriya/workflow/*.py")):
        text = open(path, encoding="utf-8").read()
        for match in re.finditer(r"approval_callback\(\s*([^,)]*(?:\([^)]*\))?[^,)]*),", text):
            first = match.group(1).strip()
            if first not in ("[]", "diffs_to_show or []"):
                non_empty.append((path, first))
    assert non_empty == [("kriya/workflow/workflow.py", "diffs_to_show")], non_empty
    text = open("kriya/workflow/workflow.py", encoding="utf-8").read()
    gate = text[text.index("approved = approval_callback(diffs_to_show, escalation_reason)"):][:200]
    assert "asyncio.iscoroutine(approved)" in gate and "await approved" in gate


def test_no_auto_approval_path_exists_in_the_launch_layer():
    for module in (approval, launch_module):
        text = open(module.__file__, encoding="utf-8").read()
        assert "--yes" not in text and "auto_approve" not in text and "-y" not in re.findall(r"['\"](-y)['\"]", text)
