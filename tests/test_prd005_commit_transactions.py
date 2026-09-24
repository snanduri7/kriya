"""PRD-005 proofs for the real-workspace batch transaction boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kriya.workflow.edit_safety import (
    BatchCommitError,
    CommitState,
    FileRevisionConflict,
    StagedFileWrite,
    UncertainCommitError,
    commit_revision_grounded_batch,
    commit_state_for_transaction,
    content_revision,
    find_uncertain_commit_evidence,
    load_commit_evidence,
)


def _mixed_batch(workspace: Path):
    created = workspace / "created.txt"
    modified = workspace / "modified.txt"
    deleted = workspace / "deleted.txt"
    modified.write_text("old modified\n")
    deleted.write_text("old deleted\n")
    os.chmod(modified, 0o754)
    os.chmod(deleted, 0o640)
    writes = [
        StagedFileWrite(
            str(created), "new created\n", str(created), content_revision(""),
            expected_base_exists=False,
        ),
        StagedFileWrite(
            str(modified), "new modified\n", str(modified), content_revision("old modified\n"),
            expected_base_exists=True,
        ),
        StagedFileWrite(
            str(deleted), "", str(deleted), content_revision("old deleted\n"),
            delete=True, expected_base_exists=True,
        ),
    ]
    return created, modified, deleted, writes


def _assert_original_state(created: Path, modified: Path, deleted: Path):
    assert not created.exists()
    assert modified.read_text() == "old modified\n"
    assert deleted.read_text() == "old deleted\n"
    assert modified.stat().st_mode & 0o777 == 0o754
    assert deleted.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("failure_target", ["created.txt", "modified.txt", "deleted.txt"])
def test_mixed_batch_fault_after_each_mutation_rolls_back_every_path(tmp_path, failure_target):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    real_replace = os.replace
    real_unlink = os.unlink
    injected = False

    def replace_then_fail(source, target):
        nonlocal injected
        real_replace(source, target)
        if Path(target).name == failure_target and not injected:
            injected = True
            raise OSError(f"fault after {failure_target}")

    def unlink_then_fail(target):
        nonlocal injected
        real_unlink(target)
        if Path(target).name == failure_target and not injected:
            injected = True
            raise OSError(f"fault after {failure_target}")

    with patch("kriya.workflow.edit_safety.os.replace", side_effect=replace_then_fail), patch(
        "kriya.workflow.edit_safety.os.unlink", side_effect=unlink_then_fail,
    ):
        with pytest.raises(BatchCommitError, match="rolled back"):
            commit_revision_grounded_batch(writes, workspace_path=str(tmp_path))

    _assert_original_state(created, modified, deleted)
    evidence_files = list((tmp_path / ".kriya/control/commits").glob("*.json"))
    assert len(evidence_files) == 1
    assert load_commit_evidence(str(evidence_files[0])).state is CommitState.ROLLED_BACK


def test_mixed_batch_success_is_one_committed_result_and_preserves_mode(tmp_path):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    assert commit_state_for_transaction(str(tmp_path), "mixed-success") is CommitState.NOT_STARTED
    result = commit_revision_grounded_batch(
        writes, workspace_path=str(tmp_path), transaction_id="mixed-success",
    )

    assert created.read_text() == "new created\n"
    assert modified.read_text() == "new modified\n"
    assert not deleted.exists()
    assert modified.stat().st_mode & 0o777 == 0o754
    assert result.evidence.state is CommitState.COMMITTED
    persisted = load_commit_evidence(
        str(tmp_path / ".kriya/control/commits/mixed-success.json")
    )
    assert persisted.state is CommitState.COMMITTED
    assert commit_state_for_transaction(str(tmp_path), "mixed-success") is CommitState.COMMITTED
    assert persisted.result_revisions == {
        Path(path).name: revision for path, revision in result.items()
    }


def test_any_revision_conflict_changes_zero_paths_and_creates_no_intent(tmp_path):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    deleted.write_text("external change\n")

    with pytest.raises(FileRevisionConflict, match="Refusing stale batch write"):
        commit_revision_grounded_batch(writes, workspace_path=str(tmp_path))

    assert not created.exists()
    assert modified.read_text() == "old modified\n"
    assert deleted.read_text() == "external change\n"
    assert not (tmp_path / ".kriya/control/commits").exists()


@pytest.mark.parametrize(
    ("invalid", "expected_exception"),
    [
        ("alias", BatchCommitError),
        ("escape", BatchCommitError),
        ("missing_modify", FileRevisionConflict),
        ("missing_delete", BatchCommitError),
    ],
)
def test_invalid_batch_shapes_fail_before_mutation(tmp_path, invalid, expected_exception):
    target = tmp_path / "target.txt"
    target.write_text("old\n")
    valid = StagedFileWrite(
        str(target), "new\n", str(target), content_revision("old\n"),
        expected_base_exists=True,
    )
    if invalid == "alias":
        writes = [valid, StagedFileWrite(
            str(tmp_path / "." / "target.txt"), "other\n", str(target),
            content_revision("old\n"), expected_base_exists=True,
        )]
    elif invalid == "escape":
        outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
        writes = [StagedFileWrite(
            str(outside), "bad\n", str(outside), content_revision(""),
            expected_base_exists=False,
        )]
    elif invalid == "missing_modify":
        missing = tmp_path / "missing.txt"
        writes = [StagedFileWrite(
            str(missing), "bad\n", str(missing), content_revision(""),
            expected_base_exists=True,
        )]
    else:
        missing = tmp_path / "missing.txt"
        writes = [StagedFileWrite(
            str(missing), "", str(missing), content_revision(""), delete=True,
            expected_base_exists=False,
        )]

    with pytest.raises(expected_exception):
        commit_revision_grounded_batch(writes, workspace_path=str(tmp_path))
    assert target.read_text() == "old\n"
    assert not (tmp_path / ".kriya/control/commits").exists()


def test_sigkill_leaves_uncertain_intent_and_next_commit_fails_closed(tmp_path):
    target = tmp_path / "first.txt"
    other = tmp_path / "second.txt"
    target.write_text("old first\n")
    other.write_text("old second\n")
    script = r'''
import os, signal, sys
import kriya.workflow.edit_safety as module
from kriya.workflow.edit_safety import StagedFileWrite, commit_revision_grounded_batch, content_revision
workspace, first, second = sys.argv[1:]
real_replace = module.os.replace
def replace_then_kill(source, destination):
    real_replace(source, destination)
    if destination == first:
        os.kill(os.getpid(), signal.SIGKILL)
module.os.replace = replace_then_kill
commit_revision_grounded_batch([
    StagedFileWrite(first, "new first\n", first, content_revision("old first\n"), expected_base_exists=True),
    StagedFileWrite(second, "new second\n", second, content_revision("old second\n"), expected_base_exists=True),
], workspace_path=workspace, transaction_id="crash-proof")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(target), str(other)],
        cwd=str(tmp_path), capture_output=True, text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
    )
    assert completed.returncode < 0
    assert target.read_text() == "new first\n"
    assert other.read_text() == "old second\n"
    uncertain = find_uncertain_commit_evidence(str(tmp_path))
    assert [item.transaction_id for item in uncertain] == ["crash-proof"]
    assert uncertain[0].state is CommitState.IN_PROGRESS

    with pytest.raises(UncertainCommitError, match="prior commit intent is uncertain"):
        commit_revision_grounded_batch([], workspace_path=str(tmp_path))


def test_commit_evidence_contains_no_source_bytes(tmp_path):
    secret = "SOURCE-CONTENT-MUST-NOT-BE-PERSISTED"
    target = tmp_path / "app.txt"
    result = commit_revision_grounded_batch([
        StagedFileWrite(
            str(target), secret, str(target), content_revision(""), expected_base_exists=False,
        )
    ], workspace_path=str(tmp_path), transaction_id="redaction")
    payload = json.loads(
        (tmp_path / ".kriya/control/commits/redaction.json").read_text()
    )
    assert secret not in json.dumps(payload)
    assert result.evidence.state is CommitState.COMMITTED


# --- PRD-005 reopening: faults at every boundary, byte/mode fidelity -------

import kriya.workflow.edit_safety as edit_safety_module  # noqa: E402


def _stage_files(root: Path):
    return sorted(p.name for p in root.rglob(".kriya-stage-*"))


def _source_snapshot(root: Path):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
        for p in root.rglob("*") if p.is_file() and ".kriya" not in p.parts
    }


def test_intent_persistence_failure_changes_nothing_and_leaves_no_trace(tmp_path):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    before = _source_snapshot(tmp_path)
    with patch.object(edit_safety_module, "_persist_commit_evidence",
                      side_effect=OSError("control dir read-only")):
        with pytest.raises(BatchCommitError, match="intent could not be persisted"):
            commit_revision_grounded_batch(
                writes, workspace_path=str(tmp_path), transaction_id="no-intent",
            )
    assert _source_snapshot(tmp_path) == before
    assert _stage_files(tmp_path) == []
    assert commit_state_for_transaction(str(tmp_path), "no-intent") is CommitState.NOT_STARTED


def test_staging_failure_removes_every_staged_file_and_records_rolled_back(tmp_path):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    before = _source_snapshot(tmp_path)
    real_stage = edit_safety_module._stage_content
    calls = []

    def stage_then_fail(item, transaction_id, index):
        calls.append(index)
        if len(calls) == 2:
            raise OSError("disk full while staging")
        return real_stage(item, transaction_id, index)

    with patch.object(edit_safety_module, "_stage_content", side_effect=stage_then_fail):
        with pytest.raises(BatchCommitError, match="staging failed before mutation"):
            commit_revision_grounded_batch(
                writes, workspace_path=str(tmp_path), transaction_id="stage-fault",
            )
    assert _source_snapshot(tmp_path) == before
    assert _stage_files(tmp_path) == []
    assert commit_state_for_transaction(str(tmp_path), "stage-fault") is CommitState.ROLLED_BACK
    assert find_uncertain_commit_evidence(str(tmp_path)) == ()


@pytest.mark.parametrize("failure_target", ["created.txt", "modified.txt"])
def test_fault_before_replace_takes_effect_rolls_back(tmp_path, failure_target):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    real_replace = os.replace

    def fail_before(source, target):
        # Only the forward apply (staged -> target); rollback restores must work.
        if Path(target).name == failure_target and Path(source).name.startswith(".kriya-stage-"):
            raise OSError("fault before replace")
        return real_replace(source, target)

    with patch("kriya.workflow.edit_safety.os.replace", side_effect=fail_before):
        with pytest.raises(BatchCommitError, match="rolled back"):
            commit_revision_grounded_batch(writes, workspace_path=str(tmp_path))
    _assert_original_state(created, modified, deleted)
    assert _stage_files(tmp_path) == []


def test_rollback_failure_is_uncertain_not_an_ordinary_failure(tmp_path):
    created, modified, deleted, writes = _mixed_batch(tmp_path)

    def unlink_fails_for_delete(target):
        if Path(target).name == "deleted.txt":
            raise OSError("delete step failed")
        return os.remove(target)

    with patch("kriya.workflow.edit_safety.os.unlink", side_effect=unlink_fails_for_delete), \
            patch.object(edit_safety_module, "_atomic_write_bytes",
                         side_effect=OSError("cannot restore modified.txt")):
        with pytest.raises(UncertainCommitError, match="rollback also failed"):
            commit_revision_grounded_batch(
                writes, workspace_path=str(tmp_path), transaction_id="rollback-fault",
            )
    assert commit_state_for_transaction(str(tmp_path), "rollback-fault") is CommitState.UNCERTAIN
    with pytest.raises(UncertainCommitError, match="prior commit intent is uncertain"):
        commit_revision_grounded_batch([], workspace_path=str(tmp_path))


def test_rolled_back_evidence_write_failure_is_uncertain(tmp_path):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    real_persist = edit_safety_module._persist_commit_evidence
    real_replace = os.replace

    def persist_only_in_progress(workspace, evidence):
        if evidence.state is CommitState.ROLLED_BACK:
            raise OSError("evidence disk full")
        return real_persist(workspace, evidence)

    def fail_modified(source, target):
        # Only the forward apply (staged -> target); rollback restores must work.
        if Path(target).name == "modified.txt" and Path(source).name.startswith(".kriya-stage-"):
            raise OSError("fault")
        return real_replace(source, target)

    with patch.object(edit_safety_module, "_persist_commit_evidence",
                      side_effect=persist_only_in_progress), \
            patch("kriya.workflow.edit_safety.os.replace", side_effect=fail_modified):
        with pytest.raises(UncertainCommitError, match="rolled-back evidence could not be persisted"):
            commit_revision_grounded_batch(
                writes, workspace_path=str(tmp_path), transaction_id="evidence-fault",
            )
    _assert_original_state(created, modified, deleted)
    assert commit_state_for_transaction(str(tmp_path), "evidence-fault") is CommitState.IN_PROGRESS


@pytest.mark.parametrize("changed", ["created.txt", "modified.txt", "deleted.txt"])
def test_conflict_at_any_position_changes_zero_paths(tmp_path, changed):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    (tmp_path / changed).write_text("external change\n")
    before = _source_snapshot(tmp_path)
    with pytest.raises(FileRevisionConflict):
        commit_revision_grounded_batch(writes, workspace_path=str(tmp_path))
    assert _source_snapshot(tmp_path) == before
    assert not (tmp_path / ".kriya/control/commits").exists()


def test_committed_bytes_equal_verified_bytes_and_new_file_mode_is_kept(tmp_path):
    crlf = tmp_path / "windows.txt"
    crlf.write_bytes(b"old\r\n")
    raw = b"caf\xe9 latin-1 \x00 bytes\r\n"
    writes = [
        StagedFileWrite(
            str(crlf), "new\n", str(crlf), edit_safety_module.read_file_revision(str(crlf)),
            expected_base_exists=True, content_bytes=b"new\r\n",
        ),
        StagedFileWrite(
            str(tmp_path / "binaryish.dat"), raw.decode("utf-8", errors="replace"),
            str(tmp_path / "binaryish.dat"), content_revision(""),
            expected_base_exists=False, content_bytes=raw,
        ),
        StagedFileWrite(
            str(tmp_path / "mvnw"), "#!/bin/sh\n", str(tmp_path / "mvnw"), content_revision(""),
            expected_base_exists=False, mode=0o755,
        ),
    ]
    commit_revision_grounded_batch(writes, workspace_path=str(tmp_path))
    assert crlf.read_bytes() == b"new\r\n"
    assert (tmp_path / "binaryish.dat").read_bytes() == raw
    assert (tmp_path / "mvnw").stat().st_mode & 0o777 == 0o755


def test_symlink_target_is_refused_before_mutation(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("old\n")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    with pytest.raises(BatchCommitError, match="symbolic link"):
        commit_revision_grounded_batch([
            StagedFileWrite(str(link), "new\n", str(link), content_revision("old\n"),
                            expected_base_exists=True),
        ], workspace_path=str(tmp_path))
    assert link.is_symlink() and real.read_text() == "old\n"


def test_base_path_outside_workspace_is_refused(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-base.txt"
    outside.write_text("x\n")
    target = tmp_path / "t.txt"
    with pytest.raises(BatchCommitError, match="escapes workspace"):
        commit_revision_grounded_batch([
            StagedFileWrite(str(target), "new\n", str(outside), content_revision("x\n"),
                            expected_base_exists=None),
        ], workspace_path=str(tmp_path))
    assert not target.exists()


def test_empty_batch_writes_no_evidence(tmp_path):
    result = commit_revision_grounded_batch([], workspace_path=str(tmp_path))
    assert dict(result) == {}
    assert not (tmp_path / ".kriya/control/commits").exists()


def test_evidence_records_candidate_revisions_and_stage_prefix(tmp_path):
    created, modified, deleted, writes = _mixed_batch(tmp_path)
    result = commit_revision_grounded_batch(
        writes, workspace_path=str(tmp_path), transaction_id="traceable",
    )
    by_target = {op["target_path"]: op for op in result.evidence.operations}
    assert by_target["created.txt"]["candidate_revision"] == content_revision("new created\n")
    assert by_target["created.txt"]["stage_prefix"] == ".kriya-stage-traceable-"
    assert by_target["deleted.txt"]["candidate_revision"] is None


def test_terminal_evidence_is_pruned_but_uncertain_never_is(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("0")
    for index in range(edit_safety_module._COMMIT_EVIDENCE_RETAINED_TERMINAL + 5):
        commit_revision_grounded_batch([
            StagedFileWrite(str(target), str(index + 1), str(target),
                            content_revision(target.read_text()), expected_base_exists=True),
        ], workspace_path=str(tmp_path), transaction_id=f"t{index}")
    files = list((tmp_path / ".kriya/control/commits").glob("*.json"))
    assert len(files) == edit_safety_module._COMMIT_EVIDENCE_RETAINED_TERMINAL
