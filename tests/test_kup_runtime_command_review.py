"""runtime_command.review (GUI-RUNTIME-COMMAND-APPROVAL-001): the person is shown the EXACT commands Kriya will run, re-proved against the review's fingerprint; anything else is a typed
refusal and nothing is served. Read-only: it approves nothing."""
from __future__ import annotations

import asyncio
import json

import pytest

import kriya.control.launch_approval as approval
from kriya.config import AppConfig
from kriya.control import launch as launch_module
from kriya.control.launch import LaunchSession
from kriya.control.persistence import load_launch_record, save_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.workspace_identity import canonical_workspace, workspace_identity
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation
from kriya.workflow.runtime_command_approval import build_subject

LID = "20261010T101010-abcdef01"
WORKTREE = "/private/var/kriya/worktrees/run-1"
COMMANDS = (("javac", "-d", "classes", "A.java"), ("java", "-cp", "classes", "A"))
FILES = [{"filepath": "a.py", "content": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"}]


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _subject(stdin=None):
    return build_subject(unit_id="s4", criterion="prints Hello from Kriya", working_directory=WORKTREE, commands=[list(c) for c in COMMANDS],
                         input_channel="stdin" if stdin else "none", stdin_payload=stdin, reason="Allow Kriya to execute these command(s)?")


def _review(ws, review_id, launch_id=LID, operation="runtime_command.review"):
    return run_kup_operation(AppConfig(), {"operation": operation, "workspace": str(ws), "launch_id": launch_id, "review_id": review_id})


def _hold(ws, scenario, subject=None):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, "goal", {"ready": True}, {})
    session.open()
    out = {}

    async def main_():
        task = asyncio.ensure_future(launch_module.request_runtime_command_approval(subject or _subject()))
        for _ in range(500):
            record = load_launch_record(canonical, LID)
            if record and record.get("pending_approval"):
                break
            await asyncio.sleep(0.01)
        pending = record["pending_approval"]
        out["scenario"] = await asyncio.to_thread(scenario, canonical, pending)
        out["decision"] = await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    return canonical, out


def _reject(canonical, pending):
    return approval.decide(canonical, LID, pending["review_id"], pending["candidate_digest"], "reject", "runtime_command")


def test_the_review_serves_the_exact_recorded_commands_after_re_proving_them(ws):
    seen = {}

    def scenario(canonical, pending):
        seen["env"] = _review(ws, pending["review_id"])
        return _reject(canonical, pending)
    _hold(ws, scenario)
    env = seen["env"]
    assert env["error"] is None
    data = env["data"]
    assert data["verified"] is True and data["status"] == "PENDING" and data["launch_id"] == LID and data["run_id"] == LID and data["unit_id"] == "s4"
    assert data["commands"] == [list(c) for c in COMMANDS] and data["criterion"] == "prints Hello from Kriya"      # exact, in order
    assert data["working_directory"] == WORKTREE and data["working_directory_kind"] == "ISOLATED_WORKTREE"
    assert data["input_channel"] == "none" and data["stdin_present"] is False and data["environment_names"] == []
    assert data["command_fingerprint"] == _subject().fingerprint and data["expires_at"] and data["requested_at"]


def test_a_decided_review_is_still_readable_with_its_final_status(ws):
    seen = {}

    def scenario(canonical, pending):
        _reject(canonical, pending)
        seen["id"] = pending["review_id"]
    _hold(ws, scenario)
    assert _review(ws, seen["id"])["data"]["status"] == "REJECTED"


@pytest.mark.parametrize("tamper,reason", [
    (lambda p: p.update(commands=[["java", "-jar", "evil.jar"]]), "fingerprint_mismatch"),                      # a different command than the one bound to the digest
    (lambda p: p.update(commands=[list(reversed([list(c) for c in COMMANDS]))[0]]), "fingerprint_mismatch"),   # a command dropped / reordered
    (lambda p: p.update(working_directory="/Users/me/project"), "fingerprint_mismatch"),                        # a cwd the digest does not bind
    (lambda p: p.update(input_channel="argv"), "fingerprint_mismatch"),
    (lambda p: p.update(stdin_sha256="sha256:" + "5" * 64), "fingerprint_mismatch"),
    (lambda p: p.update(command_fingerprint="sha256:" + "6" * 64), "fingerprint_mismatch"),                     # the two recorded digests disagree
    (lambda p: p.update(working_directory_kind="PROJECT_FOLDER"), "working_directory_kind"),                    # the record may not claim the project folder
    (lambda p: p.update(commands=[]), "commands_unreadable"),
    (lambda p: p.update(commands=[["java", 7]]), "commands_unreadable"),
])
def test_a_record_whose_commands_are_not_the_bound_ones_is_never_served(ws, tamper, reason):
    seen = {}

    def scenario(canonical, pending):
        record = load_launch_record(canonical, LID)
        tamper(record["pending_approval"])
        save_launch_record(canonical, LID, record)
        seen["env"] = _review(ws, pending["review_id"])
        record["pending_approval"] = pending
        save_launch_record(canonical, LID, record)
        return _reject(canonical, pending)
    _hold(ws, scenario)
    error = seen["env"]["error"]
    assert error["code"] == policy.REVIEW_COMMAND_UNVERIFIED and error["reason"] == reason and seen["env"]["data"] is None


def test_a_change_review_is_not_served_as_a_runtime_review_and_the_reverse(ws):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, "goal", {}, {})
    session.open()
    try:
        async def main_():
            task = asyncio.ensure_future(launch_module.request_change_approval(FILES, "why"))
            for _ in range(500):
                record = load_launch_record(canonical, LID)
                if record and record.get("pending_approval"):
                    break
                await asyncio.sleep(0.01)
            pending = record["pending_approval"]
            error = _review(ws, pending["review_id"])["error"]
            assert error["code"] == policy.REVIEW_NOT_FOUND and error["reason"] == "wrong_kind"
            approval.decide(canonical, LID, pending["review_id"], pending["candidate_digest"], "reject")
            await asyncio.wait_for(task, 20)
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)


def test_invalid_foreign_and_unknown_requests_are_typed_refusals(ws, tmp_path):
    assert _review(ws, "ra-" + "0" * 24)["error"]["code"] == policy.LAUNCH_NOT_FOUND
    assert _review(ws, "nope")["error"]["code"] == policy.INVALID_REQUEST
    assert _review(ws, "ra-" + "0" * 24, launch_id="bad")["error"]["code"] == policy.INVALID_REQUEST
    other = tmp_path / "other"
    other.mkdir()
    seen = {}

    def scenario(canonical, pending):
        seen["foreign"] = run_kup_operation(AppConfig(), {"operation": "runtime_command.review", "workspace": str(other), "launch_id": LID, "review_id": pending["review_id"]})
        seen["unknown"] = _review(ws, "ra-" + "1" * 24)
        return _reject(canonical, pending)
    _hold(ws, scenario)
    assert seen["foreign"]["error"]["code"] == policy.LAUNCH_NOT_FOUND
    assert seen["unknown"]["error"]["code"] == policy.REVIEW_NOT_FOUND and seen["unknown"]["error"]["reason"] == "not_recorded"


def test_no_stdin_value_exists_in_what_is_served(ws):
    secret = "API_KEY=hunter2-very-secret"
    seen = {}

    def scenario(canonical, pending):
        seen["env"] = _review(ws, pending["review_id"])
        return _reject(canonical, pending)
    _hold(ws, scenario, _subject(stdin=secret))
    text = json.dumps(seen["env"])
    assert "hunter2" not in text and seen["env"]["data"]["stdin_present"] is True and seen["env"]["data"]["input_channel"] == "stdin"
