"""PRD-008 S4: `kriya runs status|recover|prune` and evidence-based recovery.

Every crash window is produced by a real process killed with os._exit (no
finally blocks, no settle, no cleanup) at an exact point of a real commit,
then recovered through kriya/control/recovery.py and the CLI:

  crash point              evidence       recovery
  before IN_PROGRESS       none           cycle NOT_COMMITTED
  after staging, before    IN_PROGRESS    ROLLED_BACK (workspace unchanged),
    the first replace                     staged temp files removed
  between replaces         IN_PROGRESS    PARTIAL: only --complete-partial,
                                          only with RunRecord proof
  after every replace      IN_PROGRESS    COMMITTED
  in a stage, no commit    -              crashed run -> RECOVERED/FAILED

RECOVERED never means SUCCESS: terminal_status is NEEDS_REVIEW whenever the
workspace changed, FAILURE otherwise.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import kriya.workflow.edit_safety as edit_safety_module
from kriya.cli import main as cli_main
from kriya.control.commit_state import UncertainWorkspaceStateError, assess_workspace_commit_state
from kriya.control.persistence import load_run_record, run_record_path, scan_run_records
from kriya.control.recovery import (
    OP_APPLIED,
    OP_FOREIGN,
    OP_NOT_APPLIED,
    OUTCOME_MANUAL,
    OUTCOME_PARTIAL,
    STATUS_CLEAN,
    STATUS_COMPLETE_PARTIAL_REQUIRED,
    STATUS_MANUAL_ACTION_REQUIRED,
    STATUS_RECOVERY_AVAILABLE,
    STATUS_RUN_ACTIVE,
    assess_recovery,
    canonical_workspace,
    recover_workspace,
)
from kriya.control.retention import prune_run_state
from kriya.control.run_coordinator import begin_mutating_run, transition_mutating_run
from kriya.control.run_ownership import WorkspaceLockHeldError, acquire_run_lock
from kriya.control.run_record import (
    IllegalRunTransitionError,
    RunLifecycle,
    RunRecord,
    UnsupportedRunRecordError,
)
from kriya.workflow.edit_safety import (
    CommitState,
    StagedFileWrite,
    load_commit_evidence,
    read_file_revision,
    stage_file_prefix,
)
from kriya.workflow.terminal_commit import commit_terminal_candidate

ROOT = Path(__file__).resolve().parents[1]

A_BEFORE, A_AFTER = b"A = 1\n", b"A = 2\n"
NEW_AFTER = b"NEW = 1\n"
GONE_BEFORE = b"GONE = 1\n"

# A real candidate commit (modify a.py, create pkg/new.py in a directory that
# does not exist yet, delete gone.py), killed at ``crash_at``: "evidenceN" =
# the Nth commit-evidence replace, "stageN" = the Nth staged-file replace,
# "running" = before any commit. ``run`` makes it a real mutating run whose
# terminal commit records its cycle; ``bare`` commits with no run at all.
_CRASH_SCRIPT = r'''
import os, sys
from kriya.control.run_coordinator import begin_mutating_run, transition_mutating_run
from kriya.control.run_record import RunLifecycle
from kriya.workflow.edit_safety import StagedFileWrite, commit_revision_grounded_batch, read_file_revision
from kriya.workflow.terminal_commit import commit_terminal_candidate

workspace, crash_at, mode = sys.argv[1], sys.argv[2], sys.argv[3]
real_replace = os.replace
calls = {"stage": 0, "evidence": 0}

def crashing_replace(src, dst):
    name = os.path.basename(src)
    kind = "stage" if name.startswith(".kriya-stage-") else "evidence" if name.startswith(".commit-") else None
    if kind:
        calls[kind] += 1
        if f"{kind}{calls[kind]}" == crash_at:
            os._exit(9)
    return real_replace(src, dst)

def write(root, rel, data, exists, delete=False):
    path = os.path.join(root, rel)
    return StagedFileWrite(
        target_path=path, content=data.decode(), base_path=path,
        expected_base_revision=read_file_revision(path), expected_base_exists=exists,
        content_bytes=None if delete else data, mode=None if delete else 0o644, delete=delete,
    )

def commit(root):
    writes = [write(root, "a.py", b"A = 2\n", True), write(root, "pkg/new.py", b"NEW = 1\n", False),
              write(root, "gone.py", b"", True, delete=True)]
    os.replace = crashing_replace
    if mode == "run":
        commit_terminal_candidate(writes, workspace_path=root, transaction_id="tx1")
    else:
        commit_revision_grounded_batch(writes, workspace_path=root, transaction_id="tx1")

if mode == "run":
    with begin_mutating_run(workspace) as ctx:
        transition_mutating_run(ctx, RunLifecycle.RUNNING)
        transition_mutating_run(ctx, RunLifecycle.CANDIDATE)
        if crash_at == "running":
            os._exit(9)
        commit(ctx.workspace_path)
else:
    commit(os.path.realpath(workspace))
os._exit(0)
'''


def _workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    (workspace / "a.py").write_bytes(A_BEFORE)
    (workspace / "gone.py").write_bytes(GONE_BEFORE)
    os.chmod(workspace / "a.py", 0o644)
    os.chmod(workspace / "gone.py", 0o644)
    return workspace


def _crash(workspace, crash_at, mode="run"):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT, str(workspace), crash_at, mode],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 9, result.stdout + result.stderr
    return canonical_workspace(str(workspace))


def _only_record(workspace):
    scan = scan_run_records(workspace)
    assert not scan.unreadable
    [record] = scan.records
    return record


def _evidence(workspace):
    return load_commit_evidence(os.path.join(workspace, ".kriya/control/commits/tx1.json"))


def _staged(workspace):
    prefix = stage_file_prefix("tx1")
    return sorted(
        os.path.join(directory, name)
        for directory, _dirs, names in os.walk(workspace) for name in names if name.startswith(prefix)
    )


def _source_snapshot(workspace):
    """Source files only: not Kriya state, not staged temp files."""
    root = Path(workspace)
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode & 0o7777)
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".kriya" not in path.parts and not path.name.startswith(".kriya-stage-")
    }


def _tree_snapshot(workspace):
    root = Path(workspace)
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _assert_gate_refuses(workspace):
    with pytest.raises(UncertainWorkspaceStateError):
        with begin_mutating_run(workspace):
            pass


def _assert_gate_admits(workspace):
    assert assess_workspace_commit_state(workspace).safe
    with begin_mutating_run(workspace):
        pass


def _cli(*args):
    return CliRunner().invoke(cli_main, list(args), catch_exceptions=False)


# ---------------------------------------------------------------- crash windows

def test_crash_before_commit_evidence_settles_the_cycle_not_committed(tmp_path):
    workspace = _crash(_workspace(tmp_path), "evidence1")
    before = _source_snapshot(workspace)
    assert _only_record(workspace).lifecycle_state is RunLifecycle.COMMIT_ELIGIBLE
    assert not os.path.exists(os.path.join(workspace, ".kriya/control/commits/tx1.json"))
    _assert_gate_refuses(workspace)
    assert assess_recovery(workspace).status == STATUS_RECOVERY_AVAILABLE

    report = recover_workspace(workspace)

    assert report.after.status == STATUS_CLEAN and not report.errors
    record = _only_record(workspace)
    assert record.lifecycle_state is RunLifecycle.RECOVERED
    assert record.commits[0]["result"] == "NOT_COMMITTED"
    assert (record.commit_result, record.terminal_status) == ("NOT_COMMITTED", "FAILURE")
    assert record.recovery["prior_lifecycle_state"] == "COMMIT_ELIGIBLE"
    assert record.recovery["tool"] == "kriya runs recover"
    assert _source_snapshot(workspace) == before
    _assert_gate_admits(workspace)


def test_crash_after_staging_settles_rolled_back_and_removes_staged_files(tmp_path):
    original = _source_snapshot(_workspace(tmp_path / "original"))
    workspace = _crash(_workspace(tmp_path), "stage1")
    assert _source_snapshot(workspace) == original
    assert len(_staged(workspace)) == 2
    assessment = assess_recovery(workspace)
    [finding] = assessment.evidence
    assert [op.classification for op in finding.operations] == [OP_NOT_APPLIED] * 3

    report = recover_workspace(workspace)

    assert report.after.status == STATUS_CLEAN and not report.errors
    evidence = _evidence(workspace)
    assert evidence.state is CommitState.ROLLED_BACK
    assert evidence.recovery["outcome"] == "NOT_APPLIED" and evidence.recovery["prior_state"] == "in_progress"
    assert _staged(workspace) == [] and len(report.removed_staged_files) == 2
    record = _only_record(workspace)
    assert (record.lifecycle_state, record.commit_result, record.terminal_status) == (
        RunLifecycle.RECOVERED, "ROLLED_BACK", "FAILURE",
    )
    assert _source_snapshot(workspace) == original
    _assert_gate_admits(workspace)


def test_crash_after_every_replace_settles_committed_but_never_success(tmp_path):
    workspace = _crash(_workspace(tmp_path), "evidence2")
    assert _evidence(workspace).state is CommitState.IN_PROGRESS

    report = recover_workspace(workspace)

    assert report.after.status == STATUS_CLEAN and not report.errors
    evidence = _evidence(workspace)
    assert evidence.state is CommitState.COMMITTED and evidence.recovery["outcome"] == "COMMITTED"
    assert set(evidence.result_revisions) == {"a.py", "pkg/new.py", "gone.py"}
    record = _only_record(workspace)
    assert (record.lifecycle_state, record.commit_result, record.terminal_status) == (
        RunLifecycle.RECOVERED, "COMMITTED", "NEEDS_REVIEW",
    )
    assert Path(workspace, "a.py").read_bytes() == A_AFTER
    _assert_gate_admits(workspace)


def test_partial_commit_is_finished_only_by_complete_partial(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_PARTIAL and finding.roll_forward_refusal is None
    assert [op.classification for op in finding.operations] == [OP_APPLIED, OP_NOT_APPLIED, OP_NOT_APPLIED]
    assert assess_recovery(workspace).status == STATUS_COMPLETE_PARTIAL_REQUIRED

    sources, record_before = _source_snapshot(workspace), _only_record(workspace)
    plain = recover_workspace(workspace)
    assert plain.after.status == STATUS_COMPLETE_PARTIAL_REQUIRED
    assert _source_snapshot(workspace) == sources and _only_record(workspace) == record_before
    assert _evidence(workspace).state is CommitState.IN_PROGRESS
    _assert_gate_refuses(workspace)

    report = recover_workspace(workspace, complete_partial=True)

    assert report.after.status == STATUS_CLEAN and not report.errors
    assert sorted(report.rolled_forward) == ["gone.py", "pkg/new.py"]
    assert Path(workspace, "a.py").read_bytes() == A_AFTER
    assert Path(workspace, "pkg/new.py").read_bytes() == NEW_AFTER
    assert os.stat(Path(workspace, "pkg/new.py")).st_mode & 0o7777 == 0o644
    assert not Path(workspace, "gone.py").exists()
    assert _staged(workspace) == []
    evidence = _evidence(workspace)
    assert evidence.state is CommitState.COMMITTED and evidence.recovery["outcome"] == "ROLLED_FORWARD"
    record = _only_record(workspace)
    assert (record.lifecycle_state, record.commit_result, record.terminal_status) == (
        RunLifecycle.RECOVERED, "COMMITTED", "NEEDS_REVIEW",
    )
    assert record.recovery["complete_partial"] is True
    _assert_gate_admits(workspace)


def test_partial_commit_without_a_run_record_is_never_rolled_forward(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2", mode="bare")
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_PARTIAL
    assert "COMMIT_ELIGIBLE" in finding.roll_forward_refusal
    assert assess_recovery(workspace).status == STATUS_MANUAL_ACTION_REQUIRED
    sources = _source_snapshot(workspace)

    report = recover_workspace(workspace, complete_partial=True)

    assert report.rolled_forward == [] and _source_snapshot(workspace) == sources
    assert _evidence(workspace).state is CommitState.IN_PROGRESS


def test_foreign_content_blocks_recovery_until_the_operator_restores_a_recorded_state(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    Path(workspace, "pkg").mkdir(exist_ok=True)
    Path(workspace, "pkg/new.py").write_bytes(b"hand edit\n")
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_MANUAL
    foreign = next(op for op in finding.operations if op.target_path == "pkg/new.py")
    assert foreign.classification == OP_FOREIGN
    assert foreign.expected_after["sha256"] == hashlib.sha256(NEW_AFTER).hexdigest()
    assert foreign.current["sha256"] == hashlib.sha256(b"hand edit\n").hexdigest()

    blocked = recover_workspace(workspace, complete_partial=True)
    assert blocked.rolled_forward == [] and blocked.after.status == STATUS_MANUAL_ACTION_REQUIRED
    assert Path(workspace, "pkg/new.py").read_bytes() == b"hand edit\n"

    Path(workspace, "pkg/new.py").unlink()  # back to its recorded before state
    assert recover_workspace(workspace, complete_partial=True).after.status == STATUS_CLEAN
    assert Path(workspace, "pkg/new.py").read_bytes() == NEW_AFTER


def test_tampered_staged_candidate_is_never_rolled_forward(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    staged = next(path for path in _staged(workspace) if os.path.basename(path).startswith(
        stage_file_prefix("tx1") + "1-"))
    Path(staged).write_bytes(b"NEW = 666\n")
    [finding] = assess_recovery(workspace).evidence
    assert "staged file does not hold the candidate" in finding.roll_forward_refusal

    report = recover_workspace(workspace, complete_partial=True)

    assert report.rolled_forward == [] and not Path(workspace, "pkg/new.py").exists()


def test_candidate_hash_must_match_between_run_record_and_evidence(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    path = run_record_path(workspace, _only_record(workspace).run_id)
    payload = json.loads(Path(path).read_text())
    payload["commits"][0]["candidate_hash"] = "0" * 64
    Path(path).write_text(json.dumps(payload))
    [finding] = assess_recovery(workspace).evidence
    assert "commit-eligible" in finding.roll_forward_refusal

    assert recover_workspace(workspace, complete_partial=True).rolled_forward == []
    assert not Path(workspace, "pkg/new.py").exists()


def test_crashed_run_without_a_commit_is_recovered_as_failed(tmp_path):
    workspace = _crash(_workspace(tmp_path), "running")
    record = _only_record(workspace)
    assert record.lifecycle_state is RunLifecycle.CANDIDATE and not record.commit_state_unknown
    [finding] = assess_recovery(workspace).records
    assert finding.proposed_terminal_status == "FAILURE"

    recover_workspace(workspace)

    record = _only_record(workspace)
    assert (record.lifecycle_state, record.commit_result, record.terminal_status) == (
        RunLifecycle.RECOVERED, "NO_COMMIT", "FAILURE",
    )


def test_unreadable_evidence_blocks_its_run_and_is_never_touched(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    evidence_path = Path(workspace, ".kriya/control/commits/tx1.json")
    evidence_path.write_text("{ torn")
    assessment = assess_recovery(workspace)
    assert assessment.status == STATUS_MANUAL_ACTION_REQUIRED
    [record] = assessment.records
    assert "unreadable" in record.blocked_reasons[0]

    recover_workspace(workspace, complete_partial=True)

    assert evidence_path.read_text() == "{ torn"
    assert _only_record(workspace).lifecycle_state is RunLifecycle.COMMIT_ELIGIBLE


# ---------------------------------------------------------------- in-process interrupts

def _interrupted_commit(tmp_path, monkeypatch, *, rollback_fails=False):
    """Ctrl-C (KeyboardInterrupt) on the second staged replace of a real
    terminal commit inside a real mutating run."""
    workspace = canonical_workspace(str(_workspace(tmp_path)))
    real_replace = os.replace
    calls = []

    def interrupting_replace(source, target):
        if os.path.basename(source).startswith(".kriya-stage-"):
            calls.append(target)
            if len(calls) == 2:
                raise KeyboardInterrupt
        return real_replace(source, target)

    def write(rel, data, exists):
        path = os.path.join(workspace, rel)
        return StagedFileWrite(path, data.decode(), path, read_file_revision(path),
                               expected_base_exists=exists, content_bytes=data, mode=0o644)

    if rollback_fails:
        monkeypatch.setattr(edit_safety_module, "_atomic_write_bytes",
                            lambda *_: (_ for _ in ()).throw(OSError("restore failed")))
    with pytest.raises(KeyboardInterrupt):
        with begin_mutating_run(workspace) as context:
            transition_mutating_run(context, RunLifecycle.RUNNING)
            transition_mutating_run(context, RunLifecycle.CANDIDATE)
            monkeypatch.setattr(os, "replace", interrupting_replace)
            commit_terminal_candidate(
                [write("a.py", A_AFTER, True), write("pkg/new.py", NEW_AFTER, False)],
                workspace_path=workspace, transaction_id="tx1",
            )
    monkeypatch.setattr(os, "replace", real_replace)
    return workspace


def test_interrupt_mid_apply_rolls_back_in_process_and_needs_no_recovery(tmp_path, monkeypatch):
    workspace = _interrupted_commit(tmp_path, monkeypatch)
    assert Path(workspace, "a.py").read_bytes() == A_BEFORE
    assert not Path(workspace, "pkg/new.py").exists() and _staged(workspace) == []
    assert _evidence(workspace).state is CommitState.ROLLED_BACK
    record = _only_record(workspace)
    assert (record.lifecycle_state, record.commit_result) == (RunLifecycle.FAILURE, "ROLLED_BACK")
    assert assess_recovery(workspace).status == STATUS_CLEAN
    _assert_gate_admits(workspace)


def test_interrupt_with_a_failed_rollback_keeps_the_candidate_for_complete_partial(tmp_path, monkeypatch):
    workspace = _interrupted_commit(tmp_path, monkeypatch, rollback_fails=True)
    monkeypatch.undo()
    assert _evidence(workspace).state is CommitState.UNCERTAIN
    assert _only_record(workspace).lifecycle_state is RunLifecycle.UNCERTAIN
    assert len(_staged(workspace)) == 1  # kept: the only copy of pkg/new.py's bytes
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_PARTIAL and finding.roll_forward_refusal is None

    report = recover_workspace(workspace, complete_partial=True)

    assert report.after.status == STATUS_CLEAN and not report.errors
    assert Path(workspace, "pkg/new.py").read_bytes() == NEW_AFTER and _staged(workspace) == []
    record = _only_record(workspace)
    assert (record.lifecycle_state, record.commit_result, record.terminal_status) == (
        RunLifecycle.RECOVERED, "COMMITTED", "NEEDS_REVIEW",
    )
    assert record.recovery["prior_lifecycle_state"] == "UNCERTAIN"


def test_staged_files_of_a_transaction_whose_id_extends_this_one_are_never_claimed(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    # Transaction "tx1-1", operation 0: a bare prefix test would read it as
    # tx1's operation 1, find two candidates and refuse, or delete it.
    other = Path(workspace, stage_file_prefix("tx1-1") + "0-zzzz")
    other.write_bytes(b"another transaction\n")
    [finding] = assess_recovery(workspace).evidence
    assert finding.roll_forward_refusal is None
    assert str(other) not in finding.leftover_staged_files

    assert recover_workspace(workspace, complete_partial=True).after.status == STATUS_CLEAN
    assert other.read_bytes() == b"another transaction\n"


# ---------------------------------------------------------------- lock and read-only

def test_status_is_read_only(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    tree = _tree_snapshot(workspace)
    assert assess_recovery(workspace).status == STATUS_COMPLETE_PARTIAL_REQUIRED
    assert _cli("runs", "status", "--workspace", workspace).exit_code == 1
    assert _tree_snapshot(workspace) == tree

    pristine = tmp_path / "pristine"
    pristine.mkdir()
    assert assess_recovery(str(pristine)).status == STATUS_CLEAN
    assert not (pristine / ".kriya").exists()


def test_a_live_run_is_reported_and_never_recovered_or_pruned(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    tree = _tree_snapshot(workspace)
    with acquire_run_lock(workspace):
        assert assess_recovery(workspace).status == STATUS_RUN_ACTIVE
        assert assess_recovery(workspace).records == ()
        with pytest.raises(WorkspaceLockHeldError):
            recover_workspace(workspace, complete_partial=True)
        assert _cli("runs", "status", "--workspace", workspace).exit_code == 3
        assert _cli("runs", "recover", "--workspace", workspace).exit_code == 3
        assert _cli("runs", "prune", "--workspace", workspace).exit_code == 3
    after = _tree_snapshot(workspace)
    after.pop(".kriya/run.lock", None)
    tree.pop(".kriya/run.lock", None)
    assert after == tree


# ---------------------------------------------------------------- CLI

def test_cli_status_recover_json_and_exit_codes(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    status = _cli("runs", "status", "--workspace", workspace, "--json")
    assert status.exit_code == 1
    assert json.loads(status.output)["status"] == STATUS_COMPLETE_PARTIAL_REQUIRED

    plain = _cli("runs", "recover", "--workspace", workspace)
    assert plain.exit_code == 1 and "--complete-partial" in plain.output

    done = _cli("runs", "recover", "--workspace", workspace, "--complete-partial", "--json")
    assert done.exit_code == 0, done.output
    payload = json.loads(done.output)
    assert payload["status"] == STATUS_CLEAN
    assert payload["recovered_runs"][0]["terminal_status"] == "NEEDS_REVIEW"
    assert _cli("runs", "status", "--workspace", workspace).exit_code == 0


def test_runs_commands_are_reachable_when_configuration_authority_is_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority-home"))
    workspace = _crash(_workspace(tmp_path), "evidence1")
    Path(workspace, "kriya.yaml").write_text(yaml.safe_dump({"mcp": {"hostile": {"command": "/bin/sh"}}}))
    monkeypatch.chdir(workspace)

    assert _cli("version").exit_code == 1  # ordinary commands are denied
    assert _cli("runs", "status").exit_code == 1
    assert _cli("runs", "recover").exit_code == 0
    assert _cli("runs", "prune", "--dry-run").exit_code == 0
    assert _only_record(workspace).lifecycle_state is RunLifecycle.RECOVERED


def test_prune_keeps_what_recovery_still_needs_and_removes_recovered_runs(tmp_path):
    workspace = _crash(_workspace(tmp_path), "stage2")
    run_id = _only_record(workspace).run_id
    assert _cli("runs", "prune", "--workspace", workspace, "--keep", "0").exit_code == 0
    assert load_run_record(workspace, run_id) is not None  # unsettled: protected

    recover_workspace(workspace, complete_partial=True)
    dry = _cli("runs", "prune", "--workspace", workspace, "--keep", "0", "--dry-run", "--json")
    assert json.loads(dry.output)["pruned_run_ids"] == [run_id]
    assert load_run_record(workspace, run_id) is not None

    report = prune_run_state(workspace, keep_terminal_runs=0)
    assert report.pruned_run_ids == [run_id] and report.pruned_evidence_ids == ["tx1"]
    assert load_run_record(workspace, run_id) is None


# ---------------------------------------------------------------- RunRecord v3

def _eligible(run_id="run-1"):
    record = RunRecord.new(run_id, "ws", None, None).transition(RunLifecycle.RUNNING)
    return record.begin_commit("tx-a", intent="APPLY_VERIFIED_CANDIDATE", candidate_hash="h")


def test_recover_settles_exactly_the_open_cycles_with_proven_results():
    record = _eligible()
    with pytest.raises(IllegalRunTransitionError, match="exactly the open cycles"):
        record.recover({}, {})
    with pytest.raises(IllegalRunTransitionError, match="exactly the open cycles"):
        record.recover({"tx-a": "COMMITTED", "tx-other": "COMMITTED"}, {})
    with pytest.raises(IllegalRunTransitionError, match="proven"):
        record.recover({"tx-a": "UNCERTAIN"}, {})
    recovered = record.recover({"tx-a": "COMMITTED"}, {"tool": "t"})
    assert recovered.terminal and not recovered.commit_state_unknown and not recovered.needs_recovery
    assert recovered.lifecycle_state is RunLifecycle.RECOVERED
    assert recovered.revision == record.revision + 1
    assert recovered.recovery["settled_cycles"] == {"tx-a": "COMMITTED"}


def test_recover_accepts_an_uncertain_record_and_nothing_already_settled():
    uncertain = _eligible().settle_commit("UNCERTAIN")
    assert uncertain.lifecycle_state is RunLifecycle.UNCERTAIN
    recovered = uncertain.recover({"tx-a": "ROLLED_BACK"}, {})
    assert (recovered.commit_result, recovered.terminal_status) == ("ROLLED_BACK", "FAILURE")
    for settled in (
        _eligible().settle_commit("COMMITTED").transition(RunLifecycle.SUCCESS),
        RunRecord.new("r", "ws", None, None).transition(RunLifecycle.FAILURE),
        recovered,
    ):
        with pytest.raises(IllegalRunTransitionError, match="nothing to recover"):
            settled.recover({}, {})


def test_uncertain_record_without_a_commit_cycle_is_recovered_for_review():
    record = RunRecord.new("r", "ws", None, None).transition(RunLifecycle.RUNNING)
    recovered = record.transition(RunLifecycle.UNCERTAIN).recover({}, {})
    assert (recovered.commit_result, recovered.terminal_status) == ("NO_COMMIT", "NEEDS_REVIEW")


def test_recovered_is_terminal_and_never_success():
    recovered = _eligible().recover({"tx-a": "COMMITTED"}, {})
    with pytest.raises(IllegalRunTransitionError):
        recovered.transition(RunLifecycle.SUCCESS)
    with pytest.raises(IllegalRunTransitionError):
        recovered.annotate(goal_hash="x")
    assert RunRecord.from_dict(recovered.to_dict()) == recovered


def test_v2_record_loads_as_v3_and_an_unknown_schema_fails_closed():
    payload = _eligible().to_dict()
    payload["schema_version"] = 2
    payload.pop("recovery")
    record = RunRecord.from_dict(payload)
    assert record.schema_version == 3 and record.recovery is None
    assert RunRecord.from_dict(record.to_dict()) == record
    with pytest.raises(UnsupportedRunRecordError):
        RunRecord.from_dict(dict(payload, schema_version=4))
