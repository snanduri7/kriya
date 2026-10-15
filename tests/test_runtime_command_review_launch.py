"""GUI-RUNTIME-COMMAND-APPROVAL-001, launch layer: the runtime-command review is a typed pending review distinct from a change review, bound to the command fingerprint, answered only
by a decision of its own kind, and never carrying a secret (kriya/control/launch_approval.py, launch.py, cli.py)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from test_launch_approval import FILES, LID, REASON, _session, _wait_pending

import kriya.cli as cli
import kriya.control.launch_approval as approval
from kriya.cli import main
from kriya.control import launch as launch_module
from kriya.control.persistence import launch_review_decision_path, load_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.workspace_identity import workspace_identity
from kriya.workflow.runtime_command_approval import RuntimeCommandDecision, build_subject

CWD = "/private/var/kriya/worktrees/run-1"


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _subject(commands=(("javac", "-d", "classes", "A.java"), ("java", "-cp", "classes", "A")), stdin=None, criterion="The output contains 'Hello from Kriya'"):
    return build_subject(unit_id="s4", criterion=criterion, working_directory=CWD, commands=[list(c) for c in commands], input_channel="stdin" if stdin else "none",
                         stdin_payload=stdin, reason="Allow Kriya to execute these command(s)?")


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)


def _decide(canonical, pending, decision="approve", kind="runtime_command", **override):
    args = {"review_id": pending["review_id"], "candidate_digest": pending["candidate_digest"], **override}
    return approval.decide(canonical, LID, args["review_id"], args["candidate_digest"], decision, kind)


def _run_runtime(ws, scenario, subject=None):
    canonical, session = _session(ws)
    out = {}

    async def main_():
        awaiting = launch_module.request_runtime_command_approval(subject or _subject())
        if awaiting is None:
            out["declined"] = True
            return
        task = asyncio.ensure_future(awaiting)
        pending = await _wait_pending(canonical)
        out["pending"] = pending
        out["scenario"] = await asyncio.to_thread(scenario, canonical, pending)
        out["decision"] = await asyncio.wait_for(task, 20)
        out["record_at_resume"] = load_launch_record(canonical, LID)["pending_approval"]

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    return canonical, out


def test_the_pending_review_is_a_typed_runtime_command_subject_bound_to_the_fingerprint(ws):
    subject = _subject()
    canonical, out = _run_runtime(ws, lambda c, p: _decide(c, p))
    pending = out["pending"]
    assert pending["kind"] == "runtime_command" and pending["status"] == "PENDING" and "files" not in pending      # never represented as files=[]
    assert pending["commands"] == [list(c) for c in subject.commands]                                                # exact resolved commands, in order
    assert pending["working_directory"] == CWD and pending["working_directory_kind"] == "ISOLATED_WORKTREE"
    assert pending["unit_id"] == "s4" and pending["input_channel"] == "none" and pending["stdin_present"] is False and pending["environment_names"] == []
    assert pending["candidate_digest"] == pending["command_fingerprint"] == subject.fingerprint
    assert pending["review_id"] == approval.review_identity(LID, workspace_identity(canonical), 1, subject.fingerprint, subject.reason)
    # approved: the awaiting executor receives the fingerprint the approval is bound to, durable BEFORE it resumes
    assert out["decision"] == RuntimeCommandDecision(subject.fingerprint)
    assert out["record_at_resume"]["status"] == "APPROVED"


def test_reject_is_not_an_approval(ws):
    _canonical, out = _run_runtime(ws, lambda c, p: _decide(c, p, "reject"))
    assert out["decision"] == RuntimeCommandDecision(None) and out["record_at_resume"]["status"] == "REJECTED"


def test_a_stale_fingerprint_is_refused_and_records_nothing(ws):
    def scenario(canonical, pending):
        with pytest.raises(approval.ApprovalRefused) as stale:
            _decide(canonical, pending, candidate_digest="sha256:" + "3" * 64)
        assert stale.value.code == "STALE_REVIEW"
        import os
        assert not os.path.exists(launch_review_decision_path(canonical, LID, pending["review_id"]))
        return _decide(canonical, pending, "reject")
    _canonical, out = _run_runtime(ws, scenario)
    assert out["decision"].approved is False


def test_a_decision_of_the_other_kind_is_refused_both_ways(ws):
    """The change-approval action can never approve a runtime-command review, and the runtime action can never approve a change."""
    def scenario(canonical, pending):
        import os
        for wrong in ("change_approval",):
            with pytest.raises(approval.ApprovalRefused) as refused:
                _decide(canonical, pending, kind=wrong)
            assert refused.value.code == "WRONG_REVIEW_KIND"
        assert not os.path.exists(launch_review_decision_path(canonical, LID, pending["review_id"]))
        return _decide(canonical, pending, "reject")
    _run_runtime(ws, scenario)

    canonical, session = _session(ws, "20261010T101010-abcdef09")
    try:
        async def main_():
            task = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
            pending = await _wait_pending(canonical, "20261010T101010-abcdef09")
            with pytest.raises(approval.ApprovalRefused) as refused:
                approval.decide(canonical, "20261010T101010-abcdef09", pending["review_id"], pending["candidate_digest"], "approve", "runtime_command")
            assert refused.value.code == "WRONG_REVIEW_KIND"
            approval.decide(canonical, "20261010T101010-abcdef09", pending["review_id"], pending["candidate_digest"], "reject", "change_approval")
            assert await asyncio.wait_for(task, 20) is False
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)


def test_an_unknown_kind_is_an_invalid_request(ws):
    def scenario(canonical, pending):
        with pytest.raises(approval.ApprovalRefused) as refused:
            _decide(canonical, pending, kind="always_approve")
        assert refused.value.code == "INVALID_REQUEST"
        return _decide(canonical, pending, "reject")
    _run_runtime(ws, scenario)


def test_no_secret_stdin_or_argument_enters_the_record(ws):
    secret = "TOKEN=hunter2-super-secret-value"
    canonical, out = _run_runtime(ws, lambda c, p: _decide(c, p, "reject"), _subject(stdin=secret))
    record_text = json.dumps(load_launch_record(canonical, LID))
    assert secret not in record_text and "hunter2" not in record_text
    assert out["pending"]["stdin_present"] is True and out["pending"]["stdin_sha256"].startswith("sha256:") and out["pending"]["input_channel"] == "stdin"


def test_commands_that_cannot_be_shown_faithfully_are_not_offered(ws):
    """A secret scrub that would alter an argument means the person would read something other than what would run: no review is offered (declined), never an approval of it."""
    leaking = _subject(commands=(("curl", "-H", "Authorization: Bearer sk-live-0123456789abcdef0123456789abcdef", "http://x"),))
    canonical, out = _run_runtime(ws, lambda c, p: None, leaking)
    assert out == {"declined": True}
    assert load_launch_record(canonical, LID)["pending_approval"] is None
    assert launch_module.request_runtime_command_approval(_subject(commands=())) is None


def test_a_runtime_review_expires_a_pending_change_review_into_the_history(ws):
    canonical, session = _session(ws)
    try:
        async def main_():
            change = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
            first = await _wait_pending(canonical)
            runtime = asyncio.ensure_future(launch_module.request_runtime_command_approval(_subject()))
            for _ in range(500):
                if load_launch_record(canonical, LID)["pending_approval"]["kind"] == "runtime_command":
                    break
                await asyncio.sleep(0.01)
            record = load_launch_record(canonical, LID)
            assert record["pending_approval"]["kind"] == "runtime_command" and record["pending_approval"]["sequence"] == 2
            assert [h["review_id"] for h in record["approval_history"]] == [first["review_id"]] and record["approval_history"][0]["status"] == "EXPIRED"
            approval.decide(canonical, LID, record["pending_approval"]["review_id"], record["pending_approval"]["candidate_digest"], "reject", "runtime_command")
            assert (await asyncio.wait_for(runtime, 20)).approved is False
            change.cancel()
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)


def test_the_cli_action_refuses_the_default_kind_for_a_runtime_review(ws, monkeypatch):
    monkeypatch.chdir(ws)

    def scenario(canonical, pending):
        runner = CliRunner()
        base = ["launch-decision", "--launch-id", LID, "--review-id", pending["review_id"], "--candidate-digest", pending["candidate_digest"], "--decision", "approve"]
        wrong = runner.invoke(main, base)                                    # default --kind change_approval
        right = runner.invoke(main, [*base, "--kind", "runtime_command"])
        return wrong, right
    _canonical, out = _run_runtime(ws, scenario)
    wrong, right = out["scenario"]
    assert wrong.exit_code == 2 and json.loads(wrong.output)["code"] == "WRONG_REVIEW_KIND"
    assert right.exit_code == 0 and json.loads(right.output)["accepted"] is True and out["decision"].approved is True


def test_the_generate_callback_gives_the_executor_a_typed_channel_and_keeps_the_terminal_behaviour():
    import asyncio as aio

    from kriya.workflow.runtime_command_approval import ask
    subject = _subject()
    with patch.object(cli, "_approval_decision", return_value=True) as terminal:        # outside a launch: the unchanged terminal prompt
        assert cli._runtime_command_decision(subject, False) == RuntimeCommandDecision(subject.fingerprint)
        terminal.assert_called_once_with([], subject.reason, False)
    with patch.object(cli, "_approval_decision", return_value=False):
        assert cli._runtime_command_decision(subject, False) == RuntimeCommandDecision(None)
    typed = cli.TypedApprovalCallback(lambda f, r: pytest.fail("legacy channel"), lambda s: cli._runtime_command_decision(s, True))
    with patch.object(cli, "_approval_decision", return_value=True):
        assert aio.run(ask(typed, subject)).approved_fingerprint == subject.fingerprint


def test_inside_a_launch_that_cannot_hold_the_review_the_run_is_stopped_not_approved(ws):
    """`--yes` can never take effect inside a launch; a runtime review that cannot be recorded declines and says which decision stopped the run."""
    canonical, session = _session(ws)
    try:
        for yes in (False, True):
            assert cli._runtime_command_decision(_subject(commands=()), yes) == RuntimeCommandDecision(None)
        record = load_launch_record(canonical, LID)
        assert record["approval_required"]["kind"] == "runtime_command"
    finally:
        session.finish(0, None, None)
