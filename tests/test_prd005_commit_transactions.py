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
