"""D2 M2 slice 3: the CLI side of the private review channel - ``kriya generate --launch-id`` takes the host's inherited socket FIRST, a review is decided over it, the channel
is closed when the launch seals, and a launch started without one can never be asked to approve anything (no file fallback).

Runs the real ``main`` through ``CliRunner`` with a controlled workflow body (the pattern of tests/test_kup_execution.py); the host end is a scripted peer on a real socket pair
(tests/_review_channel_host.py). The channel protocol itself is pinned in tests/test_d2_m2_review_channel.py; the TypeScript host against the real process in
ui/standalone/test/execution_real.test.ts."""
from __future__ import annotations

import asyncio
import json
import socket
import threading

import pytest
from _review_channel_host import ScriptedHost
from click.testing import CliRunner

import kriya.cli as cli
from kriya.cli import main
from kriya.context.workspace_identity import canonical_workspace
from kriya.control import launch as launch_module
from kriya.control import review_channel as rc
from kriya.control.persistence import load_launch_record
from kriya.policy.approval import APPROVAL_LAUNCH_CANNOT_ASK, ApprovalOutcome

LID = "20261010T101010-abcdef01"
GOAL = "Add a clamp function with tests."
FILES = [{"filepath": "hello.py", "content": "--- a/hello.py\n+++ b/hello.py\n@@ -1 +1 @@\n-old\n+new\n"}]
READY = {"ready": True, "blockers": [], "state": {"directory": "/x", "source": "default"}}


@pytest.fixture(name="ws")
def _ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


@pytest.fixture(autouse=True)
def _ready_preflight(monkeypatch):
    """The launch's own preflight is not under test here: it is ready."""
    from kriya.application import RunService
    monkeypatch.setattr(RunService, "build_preflight", lambda self, ctx, workspace, config, tool_visibility=None: dict(READY))


def _install_workflow(monkeypatch, seen):
    """A controlled workflow body: it asks the launch for a change review exactly as the change gate does, and records the outcome."""
    def fake(ctx, goal, file, yes, knowledge_policy, ack, resume, resume_id, json_output, from_milestones, output):
        awaiting = launch_module.request_change_approval(FILES, "reason")
        seen["awaitable"] = awaiting is not None
        seen["outcome"] = asyncio.run(awaiting) if awaiting is not None else ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK)
        output.result = {"status": "success" if seen["outcome"].approved else "approval_required", "quality_gates_passed": seen["outcome"].approved}
    monkeypatch.setattr(cli, "_generate_impl", fake)


def _launch(ws, monkeypatch):
    monkeypatch.chdir(ws)
    return CliRunner().invoke(main, ["generate", "--json", "--launch-id", LID], input=GOAL)


def test_the_launched_process_takes_the_channel_first_decides_the_review_over_it_and_closes_it_when_the_launch_seals(ws, monkeypatch):
    waiter_sock, host_sock = socket.socketpair()
    order, seen, host_result = [], {}, {}
    real_validate = launch_module.validate_goal

    def opener(fd=rc.CHANNEL_FD, **kwargs):
        order.append(("channel", fd))
        return rc.ReviewChannel(waiter_sock)

    def spying_validate(goal):
        order.append(("goal",))
        return real_validate(goal)

    monkeypatch.setattr(rc, "open_review_channel_from_fd", opener)
    monkeypatch.setattr(launch_module, "validate_goal", spying_validate)
    _install_workflow(monkeypatch, seen)
    host = ScriptedHost(host_sock, str(ws))

    def person():
        host_result["review"] = dict(host.review())
        host_result["result"] = host.decide("approve")
        try:  # the launch sealing closes the waiter's end: the host sees end of file
            host.next_frame(timeout=10)
        except EOFError:
            host_result["eof"] = True

    thread = threading.Thread(target=person)
    thread.start()
    result = _launch(ws, monkeypatch)
    thread.join(timeout=20)
    host.close()
    assert result.exit_code == 0, result.output
    assert order[0] == ("channel", 3) and order.index(("channel", 3)) < order.index(("goal",)), "the descriptor is taken over before anything else happens"
    assert seen["outcome"] == ApprovalOutcome.approve() and host_result["result"]["accepted"] is True
    assert host_result["review"]["launch_id"] == LID and host_result["review"]["kind"] == "change_approval"
    assert host_result.get("eof") is True, "the channel is closed when the launch seals"
    record = load_launch_record(canonical_workspace(str(ws)), LID)
    assert record["pending_approval"]["status"] == "APPROVED" and record["pending_approval"]["decision"]["source"] == "review-channel"
    assert host_result["review"]["capability"] not in json.dumps(record)


def test_a_launch_started_without_a_channel_is_never_offered_a_review_and_the_run_is_unavailable_not_approved(ws, monkeypatch):
    def refuse(fd=rc.CHANNEL_FD, **kwargs):
        raise rc.ChannelUnavailable("CHANNEL_NOT_PROVIDED")

    monkeypatch.setattr(rc, "open_review_channel_from_fd", refuse)
    seen = {}
    _install_workflow(monkeypatch, seen)
    result = _launch(ws, monkeypatch)
    assert result.exit_code == 0, result.output
    assert seen["awaitable"] is False, "no channel: the change review is not even offered"
    assert seen["outcome"] == ApprovalOutcome.unavailable_because(APPROVAL_LAUNCH_CANNOT_ASK) and not seen["outcome"].approved
    assert load_launch_record(canonical_workspace(str(ws)), LID)["pending_approval"] is None


def test_the_production_opener_refuses_what_is_not_a_socket_so_a_stray_descriptor_is_not_taken_for_a_channel(ws, monkeypatch):
    """No opener patch: the real one, pointed at a descriptor that is a pipe (never the process's own fd 3)."""
    import os
    read_end, write_end = os.pipe()
    try:
        monkeypatch.setattr(rc, "CHANNEL_FD", read_end)
        seen = {}
        _install_workflow(monkeypatch, seen)
        result = _launch(ws, monkeypatch)
        assert result.exit_code == 0, result.output
        assert seen["awaitable"] is False and not seen["outcome"].approved
    finally:
        os.close(read_end)
        os.close(write_end)
