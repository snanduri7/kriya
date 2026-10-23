"""D2 combined acceptance (owner 2026-10-23): control-state authenticity and human-decision authority, M3 + M4 + M1a + M2 together, through the PUBLIC paths.

Each test names one criterion of the owner's acceptance list and drives it end to end with the real components (real git clones, real writers, a real socket pair, real child
processes) - the unit tests behind each piece are cited, not repeated. The legitimate desktop launch (the REAL TypeScript host against the REAL Python process) is exercised by
ui/standalone/test/execution_real.test.ts and runtime_command_real.test.ts, run as part of the acceptance (closure/d2/D2_ACCEPTANCE.md).

Claim, exactly: a Kriya-launched same-UID host workload that can rewrite files - including the launch record, the decision document and the repository itself - cannot forge a human approval
and cannot make Kriya adopt carried or ownerless control state. NOT claimed: protection against ptrace / process-memory inspection / an account compromise, code inside the waiter's own
process, or Linux /proc behaviour (INFERRED; the Linux-only measurement test is skipped on this platform)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _fd_probe_waiter import CHILD as FD_PROBE_SOURCE
from test_d2_m4_ownership import _evidence, _mutate
from test_launch_approval import FILES, LID, REASON, _run, _session, _wait_pending

import kriya.control.launch_approval as approval
from kriya.context.workspace_identity import canonical_workspace, workspace_identity
from kriya.control import launch as launch_module
from kriya.control import persistence as P
from kriya.control.human_decision_store import human_decision_dir
from kriya.control.launch import read_launch
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.run_record import RunRecord
from kriya.policy.approval import APPROVAL_CHANNEL_UNAVAILABLE, APPROVAL_REVIEW_EXPIRED, ApprovalOutcome
from kriya.repository.carried_state import REPOSITORY_CARRIES_CONTROL_STATE, RepositoryCarriesControlStateRefused
from kriya.tools.process import ProcessController


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@e.invalid", "-c", "user.name=T", *args], cwd=path, check=True, capture_output=True)


# ------------------------------------------------------------------ M3: repository-carried runtime authority is refused

def test_acceptance_a_real_clone_that_carries_launch_records_or_decisions_is_refused_at_run_entry(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    carried = [".kriya/control/launches/20261023T120000-abcdef12.json", ".kriya/control/launches/20261023T120000-abcdef12.review-ra-000000000000000000000000.json"]
    (source / "README").write_text("x")
    for relative in carried:
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text(json.dumps({"decision": "approve"}))
    _git(source, "add", "-f", "-A")
    _git(source, "commit", "-q", "-m", "a repository that ships an approval")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(source), str(clone)], check=True, capture_output=True)
    with pytest.raises(RepositoryCarriesControlStateRefused) as refused, begin_mutating_run(str(clone)):
        pytest.fail("a run must not start in a repository that carries control state")
    assert refused.value.reason_code == REPOSITORY_CARRIES_CONTROL_STATE and set(carried) <= set(refused.value.paths)


# ------------------------------------------------------------------ M4: unstamped / mismatching / commit-evidence state is refused, never adopted

@pytest.mark.parametrize("mode", ["STRIPPED", "FOREIGN"])
@pytest.mark.parametrize("family", ["commit_evidence", "run_record"])
def test_acceptance_ownerless_or_foreign_control_state_stops_a_mutating_run_with_the_ownership_reason_and_no_recovery_advice(tmp_path, family, mode):
    workspace = tmp_path / "W"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True, capture_output=True)
    if family == "commit_evidence":
        path = _evidence(workspace)
    else:
        P.save_run_record(str(workspace), RunRecord.new("runA", workspace_identity(str(workspace)), None, None), expected_revision=None)
        path = P.run_record_path(str(workspace), "runA")
    before = Path(path).read_bytes()
    _mutate(path, "json", mode)
    damaged = Path(path).read_bytes()
    from kriya.control.commit_state import UncertainWorkspaceStateError
    with pytest.raises(UncertainWorkspaceStateError) as refused, begin_mutating_run(str(workspace)):
        pytest.fail("a run must not start over ownerless control state")
    expected = "WORKSPACE_OWNERSHIP_MISSING" if mode == "STRIPPED" else "WORKSPACE_OWNERSHIP_MISMATCH"
    assert refused.value.assessment.reason_codes == [expected] and refused.value.assessment.recoverable is False
    assert "kriya runs recover" not in str(refused.value), "recovery cannot establish ownership and is not advised"
    assert Path(path).read_bytes() == damaged != before, "nothing adopted, re-stamped or migrated the document"


# ------------------------------------------------------------------ M1a + M2: a hostile same-UID workload cannot forge the human approval

def test_acceptance_a_hostile_workload_that_rewrites_every_file_still_cannot_approve_and_the_state_lives_outside_the_repository(ws, monkeypatch):
    monkeypatch.setattr(approval, "APPROVAL_WAIT_SECONDS", 0.8)
    seen = {}

    def hostile(host, pending):
        canonical = canonical_workspace(str(ws))
        # (1) the perfectly stamped decision document, every identity bound (what the file protocol used to honour)
        P.save_launch_review_decision(canonical, LID, pending["review_id"], {
            "schema": 1, "launch_id": LID, "review_id": pending["review_id"], "candidate_digest": pending["candidate_digest"],
            "workspace_identity": workspace_identity(canonical), "decision": "approve", "decided_at": "now", "nonce": "x"})
        # (2) the launch record rewritten to say APPROVED
        record = P.load_launch_record(canonical, LID)
        record["pending_approval"] = {**record["pending_approval"], "status": "APPROVED", "decision": {"decision": "approve", "source": "forged"}}
        P.save_launch_record(canonical, LID, record)
        # (3) the old CLI action
        cli = subprocess.run([sys.executable, "-m", "kriya.cli", "launch-decision", "--launch-id", LID, "--review-id", pending["review_id"],
                              "--candidate-digest", pending["candidate_digest"], "--decision", "approve"], cwd=str(ws), capture_output=True, text=True)
        # (4) a real child command launched exactly like a Kriya tool, probing for the private channel
        probe = ProcessController().run([sys.executable, "-c", FD_PROBE_SOURCE], cwd=str(ws), timeout=60)
        seen.update(cli_exit=cli.returncode, cli_error=cli.stderr, probe=json.loads(probe.stdout))

    canonical, outcome = _run(ws, hostile)
    assert outcome["approved"] == ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED) and not outcome["approved"].approved, "no forged file, record or action approved anything"
    assert seen["cli_exit"] == 2 and "No such command" in seen["cli_error"]
    assert seen["probe"]["fds_above_2"] == [] and seen["probe"]["write_fd3"] == "OSError", "the child launched by Kriya could not even see the channel"
    assert P.load_launch_record(canonical, LID)["pending_approval"]["status"] == "EXPIRED", "the waiter's own record, not the forged one, is what stands"
    # M1a: everything the person acts on lives outside the repository
    assert not (Path(canonical) / ".kriya" / "control" / "launches").exists()
    assert Path(P.launch_record_path(canonical, LID)).parent == Path(human_decision_dir(canonical))
    assert os.path.realpath(human_decision_dir(canonical)).startswith(os.path.realpath(os.environ["KRIYA_AUTHORITY_HOME"]) + os.sep)


def test_acceptance_a_valid_channel_approval_works_and_a_captured_decision_cannot_be_replayed_or_substituted_onto_the_next_review(ws):
    canonical, session, host = _session(ws)
    seen = {}

    async def main_():
        first = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        await _wait_pending(canonical)
        review_one = dict(await asyncio.to_thread(host.review))
        captured = host.decision_frame("approve")
        seen["first_result"] = await asyncio.to_thread(host.decide, "approve")
        seen["first"] = await asyncio.wait_for(first, 20)
        second = asyncio.ensure_future(launch_module.request_change_approval([{"filepath": "other.py", "content": "different\n"}], REASON))
        await asyncio.sleep(0.2)
        await asyncio.to_thread(host.next_review)
        host.send(captured)  # review one's perfectly valid, genuinely-capable approval, replayed while review two waits
        seen["second"] = await asyncio.wait_for(second, 20)
        seen["capabilities_differ"] = review_one["capability"] != host.review_frame["capability"]

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
        host.close()
    assert seen["first_result"]["accepted"] is True and seen["first"] == ApprovalOutcome.approve(), "a valid private-channel approval works"
    assert seen["second"] == ApprovalOutcome.unavailable_because(APPROVAL_CHANNEL_UNAVAILABLE) and not seen["second"].approved, "the replay failed the channel closed; the next review was never approved"
    assert seen["capabilities_differ"] is True


def test_acceptance_the_capability_is_not_inheritable_by_anything_kriya_launches():
    """The permanent real-child control (tests/test_d2_m2_fd_inheritance.py) re-run inside the acceptance: five spawn paths, children and grandchildren, plus the structural tripwire."""
    import test_d2_m2_fd_inheritance as fd
    import test_d2_m2_spawn_tripwire as tripwire

    report, received = fd._run_waiter("paths")
    assert len(report["results"]) == 5 and received == b""
    for path, probe in report["results"].items():
        fd._assert_blind(probe, path)
    assert tripwire.scan(tripwire.ROOT) == {}


def test_acceptance_a_launch_without_a_channel_can_never_be_asked_to_approve(ws):
    canonical, session, _ = _session(ws, channel=False)
    try:
        assert launch_module.request_change_approval(FILES, REASON) is None
        assert read_launch(canonical, LID)["pending_approval"] is None
    finally:
        session.finish(0, None, None)


@pytest.fixture(name="ws")
def _ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder
