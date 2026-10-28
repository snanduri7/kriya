"""RDCG B-006 (owner decision 2026-10-28, A + B): the exact candidate bytes of every non-delete write of an UNCERTAIN commit stay available through the canonical digest-bound staged-copy mechanism.

MEASURED before the fix (WAVE2_B006_CHARACTERIZATION_2026-10-28, the historical three-file fault re-run at 7d1b0d9): apply CONSUMES a write's staged copy (``os.replace``), so a write that was applied and then restored by the in-process
rollback had no candidate bytes anywhere durable; with the rollback also failing for another write the commit was UNCERTAIN and ``recover --complete-partial`` refused ("calc.py: expected exactly one staged candidate file, found 0")
although the evidence, the RunRecord's open cycle and the other staged copies were all intact. The same loss existed after a successful apply whose COMMITTED evidence could not be persisted (the candidate then lived only in the
mutable workspace). The fix re-creates the consumed copies from the in-memory candidate bytes through ``_stage_content`` (same prefix, same operation index, the evidence's recorded mode) BEFORE the rollback and on the
evidence-write failure; the existing settle rule keeps them while IN_PROGRESS/UNCERTAIN and removes them once settled. Recovery is unchanged. These tests are the deterministic in-repo equivalent of the historical fault
(``faults``/``pipeline`` of the external RDCG harness are not available in the suite)."""
import errno
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS

import kriya.repository.commit as commit_module
from kriya.control.persistence import scan_run_records
from kriya.control.recovery import (
    OUTCOME_COMMITTED,
    OUTCOME_MANUAL,
    OUTCOME_PARTIAL,
    STATUS_CLEAN,
    assess_recovery,
    canonical_workspace,
    recover_workspace,
)
from kriya.control.run_coordinator import begin_mutating_run, transition_mutating_run
from kriya.control.run_record import RunLifecycle
from kriya.workflow.edit_safety import (
    CommitState,
    StagedFileWrite,
    UncertainCommitError,
    commit_revision_grounded_batch,
    load_commit_evidence,
    read_file_revision,
    stage_file_prefix,
)
from kriya.workflow.terminal_commit import commit_terminal_candidate
from kriya.workflow.verification_binding import bind_candidate

ROOT = Path(__file__).resolve().parents[1]
TX = "b006"
BEFORE = {"a.py": b"A = 1\n", "b.py": b"B = 1\n", "c.py": b"C = 1\n", "gone.py": b"GONE = 1\n"}
AFTER = {"a.py": b"A = 2\n", "b.py": b"B = 2\n", "c.py": b"C = 2\n"}
WRITE_ORDER = ["a.py", "b.py", "gone.py", "c.py"]       # operation indexes 0, 1, 2 (delete), 3
FOREIGN = b"# edited by the owner after the commit\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _workspace(tmp_path) -> str:
    folder = tmp_path / "ws"
    folder.mkdir()
    for name, data in BEFORE.items():
        (folder / name).write_bytes(data)
        os.chmod(folder / name, 0o644)
    return canonical_workspace(str(folder))


def _writes(workspace: str, inherit_mode: bool = False):
    def write(name):
        path = os.path.join(workspace, name)
        if name == "gone.py":
            return StagedFileWrite(target_path=path, content="", base_path=path, expected_base_revision=read_file_revision(path), expected_base_exists=True,
                                   content_bytes=None, mode=None, delete=True)
        return StagedFileWrite(target_path=path, content=AFTER[name].decode(), base_path=path, expected_base_revision=read_file_revision(path),
                               expected_base_exists=True, content_bytes=AFTER[name], mode=None if inherit_mode and name == "a.py" else 0o644)
    return [write(name) for name in WRITE_ORDER]


def _terminal_commit(workspace: str, tx: str = TX, inherit_mode: bool = False):
    writes = _writes(workspace, inherit_mode)
    with begin_mutating_run(workspace) as context:
        transition_mutating_run(context, RunLifecycle.RUNNING)
        transition_mutating_run(context, RunLifecycle.CANDIDATE)
        return commit_terminal_candidate(
            writes, workspace_path=workspace, static_analysis=DISABLED_STATIC_ANALYSIS, transaction_id=tx, verified_candidate=bind_candidate(writes, workspace),
        )


def _evidence(workspace: str, tx: str = TX):
    return load_commit_evidence(os.path.join(workspace, ".kriya", "control", "commits", f"{tx}.json"), workspace_path=workspace)


def _staged(workspace: str, tx: str = TX):
    prefix = stage_file_prefix(tx)
    return sorted(os.path.join(directory, name) for directory, _dirs, names in os.walk(workspace) for name in names if name.startswith(prefix))


def _staged_by_index(workspace: str, tx: str = TX):
    prefix = stage_file_prefix(tx)
    return {int(os.path.basename(path)[len(prefix):].split("-", 1)[0]): path for path in _staged(workspace, tx)}


def _read(workspace: str, name: str) -> bytes:
    return Path(workspace, name).read_bytes()


def _forward_fail(monkeypatch, target_name: str, before_raising=None):
    """ENOSPC on the forward apply (staged -> target) of ``target_name`` only; the rollback restores use other primitives."""
    real = os.replace

    def replace(source, target):
        if os.path.basename(source).startswith(".kriya-stage-") and os.path.basename(target) == target_name:
            if before_raising is not None:
                before_raising()
            raise OSError(errno.ENOSPC, "injected ENOSPC at apply")
        return real(source, target)

    monkeypatch.setattr(os, "replace", replace)


def _rollback_restore_fails(monkeypatch, target_name: str):
    real = commit_module._atomic_write_bytes

    def restore(path, data, *args, **kwargs):
        if os.path.basename(path) == target_name:
            raise OSError(errno.EIO, "injected EIO at rollback")
        return real(path, data, *args, **kwargs)

    monkeypatch.setattr(commit_module, "_atomic_write_bytes", restore)


def _rollback_uncertain(tmp_path, monkeypatch):
    """The historical fault: apply failure at c.py, rollback failure restoring b.py. a.py is applied then restored (its staged copy consumed), b.py stays applied, c.py was never applied."""
    workspace = _workspace(tmp_path)
    _forward_fail(monkeypatch, "c.py")
    _rollback_restore_fails(monkeypatch, "b.py")
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    return workspace, outcome


def _commit_evidence_write_fails(monkeypatch, mutate_workspace=None):
    real = commit_module._persist_commit_evidence

    def persist(workspace_path, evidence):
        if evidence.state is CommitState.COMMITTED:
            if mutate_workspace is not None:
                mutate_workspace()
            raise OSError(errno.ENOSPC, "injected failure persisting COMMITTED evidence")
        return real(workspace_path, evidence)

    monkeypatch.setattr(commit_module, "_persist_commit_evidence", persist)


def _assert_exact_candidate_copies(workspace: str, tx: str = TX):
    """One staged copy per non-delete operation (none for the delete), holding the exact candidate bytes and the recorded mode, bound to the evidence's after-state digest."""
    evidence = _evidence(workspace, tx)
    staged = _staged_by_index(workspace, tx)
    expected_indexes = [index for index, operation in enumerate(evidence.operations) if operation["operation"] != "delete"]
    assert sorted(staged) == expected_indexes, f"staged copies by operation index: {sorted(staged)} != {expected_indexes}"
    for index in expected_indexes:
        operation = evidence.operations[index]
        data = Path(staged[index]).read_bytes()
        assert data == AFTER[os.path.basename(operation["target_path"])]
        assert _sha(data) == operation["after"]["sha256"] and (os.stat(staged[index]).st_mode & 0o7777) == operation["after"]["mode"]
    return staged


# ------------------------------------------------------------------ normal success and clean failure: unchanged contracts
def test_a_normal_success_commits_and_leaves_no_staged_copy(tmp_path):
    workspace = _workspace(tmp_path)
    outcome = _terminal_commit(workspace)
    assert outcome.committed and outcome.commit_result == "COMMITTED"
    assert _evidence(workspace).state is CommitState.COMMITTED and _staged(workspace) == []
    assert {name: _read(workspace, name) for name in AFTER} == AFTER and not Path(workspace, "gone.py").exists()


def test_a_clean_failed_commit_may_re_stage_but_settles_rolled_back_and_the_existing_cleanup_removes_every_copy(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    calls = []
    real_stage = commit_module._stage_content
    monkeypatch.setattr(commit_module, "_stage_content", lambda *a, **k: calls.append((a[2], k.get("mode"))) or real_stage(*a, **k))
    _forward_fail(monkeypatch, "c.py")
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    assert outcome.commit_result == "ROLLED_BACK" and not outcome.committed
    assert _evidence(workspace).state is CommitState.ROLLED_BACK
    assert _staged(workspace) == [], "a settled ROLLED_BACK transaction leaves no staged copy, re-staged ones included"
    assert {name: _read(workspace, name) for name in BEFORE} == BEFORE
    assert sorted(index for index, mode in calls if mode is not None) == [0, 1], "the applied writes a.py and b.py were re-staged before the rollback (indexes 0 and 1)"
    assert assess_recovery(workspace).status == STATUS_CLEAN


# ------------------------------------------------------------------ mechanism 1: apply failure + incomplete rollback
def test_rollback_uncertainty_keeps_the_exact_candidate_for_every_non_delete_write_and_nothing_is_fabricated(tmp_path, monkeypatch):
    workspace, outcome = _rollback_uncertain(tmp_path, monkeypatch)
    assert outcome.commit_result == "UNCERTAIN" and outcome.reason_code == "WORKSPACE_COMMIT_UNCERTAIN" and not outcome.committed
    assert isinstance(outcome.error, UncertainCommitError) and "could not be preserved" not in str(outcome.error)
    assert _evidence(workspace).state is CommitState.UNCERTAIN
    assert [r.lifecycle_state for r in scan_run_records(workspace).records] == [RunLifecycle.UNCERTAIN]
    assert (_read(workspace, "a.py"), _read(workspace, "b.py"), _read(workspace, "c.py")) == (BEFORE["a.py"], AFTER["b.py"], BEFORE["c.py"])
    _assert_exact_candidate_copies(workspace)
    # b.py's restore failed so it is still the candidate, a.py/c.py are at base and gone.py was restored: the per-operation states recovery will reason from
    [finding] = assess_recovery(workspace).evidence
    assert [(item.target_path, item.classification) for item in finding.operations] == [
        ("a.py", "NOT_APPLIED"), ("b.py", "APPLIED"), ("gone.py", "NOT_APPLIED"), ("c.py", "NOT_APPLIED"),
    ]


def test_a_fresh_recovery_completes_the_partial_commit_from_the_staged_copies_alone_and_resolution_removes_them(tmp_path, monkeypatch):
    workspace, _ = _rollback_uncertain(tmp_path, monkeypatch)
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_PARTIAL and finding.roll_forward_refusal is None
    assert not Path(workspace, ".kriya", "checkpoints").exists() and not Path(workspace, ".kriya", "worktrees").exists(), "no checkpoint or sandbox exists to fall back on"
    report = recover_workspace(workspace, complete_partial=True)
    assert report.after.status == STATUS_CLEAN and not report.errors
    assert {name: _read(workspace, name) for name in AFTER} == AFTER and not Path(workspace, "gone.py").exists()
    assert _staged(workspace) == [], "explicit resolution removes the retained copies"
    assert _evidence(workspace).state is CommitState.COMMITTED


_RECOVER_IN_A_FRESH_PROCESS = r'''
import json, sys
from kriya.control.recovery import assess_recovery, recover_workspace
workspace = sys.argv[1]
[finding] = assess_recovery(workspace).evidence
before = {"outcome": finding.outcome, "refusal": finding.roll_forward_refusal}
report = recover_workspace(workspace, complete_partial=True)
print("RESULT " + json.dumps({"before": before, "status": report.after.status, "errors": list(report.errors)}))
'''


def test_the_staged_candidate_survives_process_loss_a_genuinely_fresh_process_completes_the_commit(tmp_path, monkeypatch):
    workspace, _ = _rollback_uncertain(tmp_path, monkeypatch)
    result = subprocess.run([sys.executable, "-c", _RECOVER_IN_A_FRESH_PROCESS, workspace], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(next(line for line in result.stdout.splitlines() if line.startswith("RESULT "))[len("RESULT "):])
    assert payload["before"] == {"outcome": OUTCOME_PARTIAL, "refusal": None} and payload["status"] == STATUS_CLEAN and payload["errors"] == []
    assert {name: _read(workspace, name) for name in AFTER} == AFTER and _staged(workspace) == []


def test_a_workspace_mutation_after_uncertainty_is_refused_and_never_changes_the_preserved_candidate(tmp_path, monkeypatch):
    workspace, _ = _rollback_uncertain(tmp_path, monkeypatch)
    before = {index: Path(path).read_bytes() for index, path in _assert_exact_candidate_copies(workspace).items()}
    Path(workspace, "a.py").write_bytes(FOREIGN)                       # foreign content in a not-applied target
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_MANUAL, "a file matching neither recorded state is never rolled forward over"
    report = recover_workspace(workspace, complete_partial=True)
    assert report.rolled_forward == [] and _read(workspace, "a.py") == FOREIGN and _read(workspace, "c.py") == BEFORE["c.py"]
    after = {index: Path(path).read_bytes() for index, path in _assert_exact_candidate_copies(workspace).items()}
    assert after == before, "the retained candidate is not derived from, or changed by, the mutated workspace"


def test_a_tampered_re_staged_candidate_is_never_rolled_forward(tmp_path, monkeypatch):
    workspace, _ = _rollback_uncertain(tmp_path, monkeypatch)
    Path(_staged_by_index(workspace)[0]).write_bytes(b"A = 666\n")     # a.py's re-created copy
    [finding] = assess_recovery(workspace).evidence
    assert "staged file does not hold the candidate" in finding.roll_forward_refusal
    assert recover_workspace(workspace, complete_partial=True).rolled_forward == [] and _read(workspace, "a.py") == BEFORE["a.py"]


def test_no_new_commit_starts_while_a_prior_one_is_uncertain_and_nothing_retries_it(tmp_path, monkeypatch):
    workspace, _ = _rollback_uncertain(tmp_path, monkeypatch)
    staged_before = _staged(workspace)
    with pytest.raises(UncertainCommitError, match="prior commit intent is uncertain"):
        commit_revision_grounded_batch(_writes(workspace), workspace_path=workspace, transaction_id="b006-retry")
    assert _staged(workspace) == staged_before and not Path(workspace, ".kriya", "control", "commits", "b006-retry.json").exists()


def test_the_candidate_copy_comes_from_the_in_memory_bytes_not_from_the_workspace(tmp_path, monkeypatch):
    """A foreign edit lands in the applied a.py just before the failure is raised: its re-created copy must still be the exact candidate."""
    workspace = _workspace(tmp_path)
    _forward_fail(monkeypatch, "c.py", before_raising=lambda: Path(workspace, "a.py").write_bytes(FOREIGN))
    _rollback_restore_fails(monkeypatch, "b.py")
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    assert outcome.commit_result == "UNCERTAIN"
    _assert_exact_candidate_copies(workspace)


# ------------------------------------------------------------------ mechanism 2: applied, but the COMMITTED evidence cannot be persisted
def test_committed_evidence_write_failure_stays_uncertain_and_keeps_the_exact_candidate_for_every_non_delete_write(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    _commit_evidence_write_fails(monkeypatch)
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    assert outcome.commit_result == "UNCERTAIN" and outcome.reason_code == "WORKSPACE_COMMIT_UNCERTAIN" and not outcome.committed
    assert isinstance(outcome.error, UncertainCommitError) and "committed evidence could not be persisted" in str(outcome.error) and "could not be preserved" not in str(outcome.error)
    assert _evidence(workspace).state is CommitState.IN_PROGRESS, "the failed COMMITTED write leaves the existing IN_PROGRESS intent, which the gate treats as uncertain"
    assert [r.lifecycle_state for r in scan_run_records(workspace).records] == [RunLifecycle.UNCERTAIN]
    assert {name: _read(workspace, name) for name in AFTER} == AFTER
    _assert_exact_candidate_copies(workspace)


def test_after_a_committed_evidence_write_failure_the_workspace_may_change_but_the_preserved_candidate_does_not(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    _commit_evidence_write_fails(monkeypatch, mutate_workspace=lambda: Path(workspace, "a.py").write_bytes(FOREIGN))
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    assert outcome.commit_result == "UNCERTAIN" and _read(workspace, "a.py") == FOREIGN
    before = {index: Path(path).read_bytes() for index, path in _assert_exact_candidate_copies(workspace).items()}  # exact candidate bytes, not the mutated workspace
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_MANUAL, "recovery does not take the mutated workspace for the candidate"
    assert recover_workspace(workspace, complete_partial=True).rolled_forward == [] and _read(workspace, "a.py") == FOREIGN
    assert {index: Path(path).read_bytes() for index, path in _assert_exact_candidate_copies(workspace).items()} == before


def test_the_re_created_copy_takes_its_mode_from_the_evidence_not_from_the_possibly_changed_target(tmp_path, monkeypatch):
    """a.py inherits its base mode (0o640, recorded in the evidence); a foreign chmod lands on the applied file before the failure is raised: the copy must still carry the recorded mode."""
    workspace = _workspace(tmp_path)
    os.chmod(os.path.join(workspace, "a.py"), 0o640)
    _commit_evidence_write_fails(monkeypatch, mutate_workspace=lambda: os.chmod(os.path.join(workspace, "a.py"), 0o600))
    outcome = _terminal_commit(workspace, inherit_mode=True)
    monkeypatch.undo()
    assert outcome.commit_result == "UNCERTAIN"
    evidence = _evidence(workspace)
    assert evidence.operations[0]["after"]["mode"] == 0o640
    assert os.stat(_staged_by_index(workspace)[0]).st_mode & 0o7777 == 0o640


def test_recovery_proves_committed_and_resolution_removes_the_retained_copies(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    _commit_evidence_write_fails(monkeypatch)
    _terminal_commit(workspace)
    monkeypatch.undo()
    [finding] = assess_recovery(workspace).evidence
    assert finding.outcome == OUTCOME_COMMITTED
    report = recover_workspace(workspace)
    assert report.after.status == STATUS_CLEAN and not report.errors
    assert _staged(workspace) == [] and _evidence(workspace).state is CommitState.COMMITTED


# ------------------------------------------------------------------ preservation failure: reported, still UNCERTAIN, recovery fails closed
def _restaging_fails(monkeypatch, only=None):
    real = commit_module._stage_content

    def stage(item, transaction_id, index, **kwargs):
        if kwargs.get("mode") is not None and (only is None or os.path.basename(item.target_path) in only):
            raise OSError(errno.ENOSPC, "injected ENOSPC re-staging the candidate")
        return real(item, transaction_id, index, **kwargs)

    monkeypatch.setattr(commit_module, "_stage_content", stage)


def test_a_preservation_failure_during_rollback_uncertainty_is_reported_and_recovery_fails_closed(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    _restaging_fails(monkeypatch)
    _forward_fail(monkeypatch, "c.py")
    _rollback_restore_fails(monkeypatch, "b.py")
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    assert outcome.commit_result == "UNCERTAIN" and _evidence(workspace).state is CommitState.UNCERTAIN, "never ROLLED_BACK or COMMITTED"
    message = str(outcome.error)
    assert "Candidate bytes could not be preserved for recovery of: a.py" in message and "b.py" in message and "injected ENOSPC re-staging" in message
    assert sorted(_staged_by_index(workspace)) == [3], "only c.py still has the copy it was originally staged with; nothing was fabricated for the others"
    [finding] = assess_recovery(workspace).evidence
    assert "expected exactly one staged candidate file, found 0" in finding.roll_forward_refusal
    assert recover_workspace(workspace, complete_partial=True).rolled_forward == []


def test_a_preservation_failure_after_a_committed_evidence_write_failure_is_reported_and_stays_uncertain(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    _restaging_fails(monkeypatch, only={"b.py"})
    _commit_evidence_write_fails(monkeypatch)
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    assert outcome.commit_result == "UNCERTAIN" and _evidence(workspace).state is CommitState.IN_PROGRESS
    assert "Candidate bytes could not be preserved for recovery of: b.py" in str(outcome.error) and "a.py" not in str(outcome.error).split("preserved for recovery of:")[1]
    assert sorted(_staged_by_index(workspace)) == [0, 3]


def test_a_preservation_failure_does_not_turn_a_fully_successful_rollback_into_uncertainty(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    _restaging_fails(monkeypatch)
    _forward_fail(monkeypatch, "c.py")
    outcome = _terminal_commit(workspace)
    monkeypatch.undo()
    assert outcome.commit_result == "ROLLED_BACK" and _evidence(workspace).state is CommitState.ROLLED_BACK
    assert {name: _read(workspace, name) for name in BEFORE} == BEFORE and _staged(workspace) == []
