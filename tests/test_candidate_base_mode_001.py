"""CANDIDATE-MODE-DRIFT-FROM-CHECKOUT-001: an EXISTING file's base mode is authoritative for a content-only candidate edit.

The sandbox's checkout mode (git records only 644/755; ``core.fileMode=false`` drops the executable bit) is never user intent. Flow pinned here:
read base mode -> materialize candidate bytes with it -> bind (mode in the digest) -> commit-time revalidation of the workspace mode -> apply.
A new file is unchanged (it has no base mode): it keeps the candidate's mode.
"""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS

from kriya.repository import commit as commit_module
from kriya.repository.commit import BatchCommitError, FileRevisionConflict, read_file_revision
from kriya.repository.snapshot import raw_digest
from kriya.workflow import terminal_commit
from kriya.workflow.terminal_commit import (
    CandidateFile,
    CandidateMaterializationError,
    commit_terminal_candidate,
    materialize_candidate,
)
from kriya.workflow.verification_binding import VERIFIED_CANDIDATE_EVIDENCE_STALE, bind_candidate

OLD, NEW = b"x = 1\n", b"x = 2\n"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _put(root: Path, name: str, data: bytes, mode: int) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)
    return path


def _pair(tmp_path: Path, base_mode: int, candidate_mode: int = 0o644):
    """A workspace file at ``base_mode`` and a candidate (content-only edit) whose own mode is ``candidate_mode``."""
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    _put(workspace, "a.py", OLD, base_mode)
    _put(candidate, "a.py", NEW, candidate_mode)
    files = [CandidateFile("a.py", read_file_revision(str(workspace / "a.py")), expected_base_exists=True)]
    return workspace, candidate, files


def _commit(writes, workspace: Path, binding, transaction_id: str = "t1"):
    return commit_terminal_candidate(
        writes, workspace_path=str(workspace), transaction_id=transaction_id,
        static_analysis=DISABLED_STATIC_ANALYSIS, verified_candidate=binding)


@pytest.mark.parametrize("base_mode", [0o600, 0o664, 0o644, 0o755, 0o750])
def test_a_content_only_edit_keeps_the_base_mode_through_bind_and_commit(tmp_path, base_mode):
    workspace, candidate, files = _pair(tmp_path, base_mode)  # the sandbox file is 0644 whatever the base was
    writes = materialize_candidate(str(candidate), str(workspace), files)
    assert writes[0].mode == base_mode and writes[0].expected_base_mode == base_mode
    binding = bind_candidate(writes, str(workspace))
    assert binding.entries[0][3] == base_mode  # candidate identity binds the PRESERVED mode
    outcome = _commit(writes, workspace, binding)
    assert outcome.committed
    assert (workspace / "a.py").read_bytes() == NEW
    assert _mode(workspace / "a.py") == base_mode


def test_a_candidate_side_chmod_of_an_existing_file_is_not_intent(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o600, candidate_mode=0o777)
    writes = materialize_candidate(str(candidate), str(workspace), files)
    assert writes[0].mode == 0o600
    assert _commit(writes, workspace, bind_candidate(writes, str(workspace))).committed
    assert _mode(workspace / "a.py") == 0o600


def test_the_bound_mode_follows_the_base_not_the_sandbox(tmp_path):
    """Two bases that differ only in mode bind different digests from the SAME candidate bytes and the same candidate mode."""
    ws_a, cand_a, files_a = _pair(tmp_path / "a", 0o600)
    ws_b, cand_b, files_b = _pair(tmp_path / "b", 0o755)
    digest_a = bind_candidate(materialize_candidate(str(cand_a), str(ws_a), files_a), str(ws_a)).digest
    digest_b = bind_candidate(materialize_candidate(str(cand_b), str(ws_b), files_b), str(ws_b)).digest
    assert digest_a != digest_b


@pytest.mark.parametrize("expected_base_exists", [False, None])
def test_a_new_file_keeps_the_candidate_mode_and_has_no_base_mode(tmp_path, expected_base_exists):
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    workspace.mkdir()
    _put(candidate, "mvnw", b"#!/bin/sh\n", 0o755)
    writes = materialize_candidate(str(candidate), str(workspace), [
        CandidateFile("mvnw", raw_digest(b""), expected_base_exists=expected_base_exists)])
    assert writes[0].mode == 0o755 and writes[0].expected_base_mode is None


def test_a_write_declared_new_carries_no_base_mode_even_if_the_path_now_exists(tmp_path):
    """The commit refuses a create over an existing target; the batch must not pretend that file's mode is a base to preserve."""
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    _put(workspace, "a.py", OLD, 0o600)
    _put(candidate, "a.py", NEW, 0o755)
    (write,) = materialize_candidate(str(candidate), str(workspace), [CandidateFile("a.py", raw_digest(b""), expected_base_exists=False)])
    assert write.expected_base_mode is None and write.mode == 0o755


def test_an_approved_deletion_carries_no_mode_expectation(tmp_path):
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    _put(workspace, "gone.py", OLD, 0o600)
    candidate.mkdir()
    writes = materialize_candidate(str(candidate), str(workspace), [
        CandidateFile("gone.py", read_file_revision(str(workspace / "gone.py")), delete=True)])
    assert writes[0].delete and writes[0].expected_base_mode is None


# ---- the real mechanism: a git-clean file, a real sandbox from create_git_worktree --------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True).stdout.decode()


@pytest.mark.parametrize("base_mode, file_mode", [(0o600, "true"), (0o664, "true"), (0o755, "true"), (0o755, "false")])
def test_a_git_clean_file_keeps_its_mode_through_a_real_sandbox(tmp_path, base_mode, file_mode):
    from kriya.workflow.worktree import create_git_worktree

    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q")
    _git(ws, "config", "core.fileMode", file_mode)
    _put(ws, "run.sh", OLD, base_mode)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "base")
    assert _git(ws, "status", "--porcelain") == "", "precondition: the workspace is git-clean"
    sandbox = Path(create_git_worktree(str(ws)))
    if (base_mode, file_mode) != (0o755, "true"):
        assert _mode(sandbox / "run.sh") != base_mode, "precondition: the checkout normalized the mode (the defect's trigger)"
    (sandbox / "run.sh").write_bytes(NEW)  # a content-only edit
    writes = materialize_candidate(str(sandbox), str(ws), [CandidateFile("run.sh", raw_digest(OLD), expected_base_exists=True)])
    assert _commit(writes, ws, bind_candidate(writes, str(ws))).committed
    assert (ws / "run.sh").read_bytes() == NEW
    assert _mode(ws / "run.sh") == base_mode


# ---- commit-time revalidation ---------------------------------------------------------------------------------------------------------------

def test_a_workspace_chmod_after_binding_is_a_stale_candidate_and_is_not_overwritten(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o644)
    binding = bind_candidate(materialize_candidate(str(candidate), str(workspace), files), str(workspace))
    os.chmod(workspace / "a.py", 0o600)  # the owner tightens the file after the candidate was verified
    outcome = _commit(materialize_candidate(str(candidate), str(workspace), files), workspace, binding)
    assert not outcome.committed and outcome.reason_code == VERIFIED_CANDIDATE_EVIDENCE_STALE
    assert (workspace / "a.py").read_bytes() == OLD and _mode(workspace / "a.py") == 0o600


def test_a_batch_materialized_before_a_chmod_is_refused_at_the_commit(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o644)
    writes = materialize_candidate(str(candidate), str(workspace), files)
    binding = bind_candidate(writes, str(workspace))  # the binding still matches these writes: only the commit can see the chmod
    os.chmod(workspace / "a.py", 0o600)
    outcome = _commit(writes, workspace, binding)
    assert not outcome.committed and outcome.reason_code == "WORKSPACE_REVISION_CONFLICT"
    assert outcome.workspace_state == "UNCHANGED"
    assert (workspace / "a.py").read_bytes() == OLD and _mode(workspace / "a.py") == 0o600


def test_the_preflight_refuses_a_chmod_before_any_staging(tmp_path):
    """Raised directly by the pre-flight (a FileRevisionConflict), not wrapped by the transaction's rollback path: nothing was staged or journaled."""
    workspace, candidate, files = _pair(tmp_path, 0o644)
    writes = materialize_candidate(str(candidate), str(workspace), files)
    os.chmod(workspace / "a.py", 0o600)
    with patch.object(commit_module, "_stage_content", side_effect=AssertionError("staged before the pre-flight refused")):
        with pytest.raises(FileRevisionConflict, match="mode changed"):
            commit_module.commit_revision_grounded_batch(writes, workspace_path=str(workspace), transaction_id="pre")
    assert (workspace / "a.py").read_bytes() == OLD and _mode(workspace / "a.py") == 0o600


def test_a_chmod_during_the_commit_transaction_is_refused_and_rolled_back(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o644)
    writes = materialize_candidate(str(candidate), str(workspace), files)
    real_stage = commit_module._stage_content

    def stage_then_chmod(item, transaction_id, index):
        staged = real_stage(item, transaction_id, index)
        os.chmod(workspace / "a.py", 0o600)  # after the pre-flight, before the replace
        return staged

    with patch.object(commit_module, "_stage_content", stage_then_chmod):
        with pytest.raises(BatchCommitError, match="mode changed") as raised:
            commit_module.commit_revision_grounded_batch(writes, workspace_path=str(workspace), transaction_id="race")
    assert isinstance(raised.value.__cause__, FileRevisionConflict)  # commit_terminal_candidate maps this cause to WORKSPACE_REVISION_CONFLICT
    assert (workspace / "a.py").read_bytes() == OLD and _mode(workspace / "a.py") == 0o600


def test_a_target_removed_after_binding_is_a_conflict(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o644)
    writes = materialize_candidate(str(candidate), str(workspace), files)
    (workspace / "a.py").unlink()
    with pytest.raises(FileRevisionConflict):
        commit_module.commit_revision_grounded_batch(writes, workspace_path=str(workspace), transaction_id="gone")


def test_a_target_that_vanishes_between_the_revision_check_and_the_mode_check_is_a_conflict(tmp_path):
    """Only a race reaches this branch (the existence and revision checks run first), so the primitive is exercised directly."""
    workspace, candidate, files = _pair(tmp_path, 0o644)
    (write,) = materialize_candidate(str(candidate), str(workspace), files)
    (workspace / "a.py").unlink()
    with pytest.raises(FileRevisionConflict, match="no longer exists"):
        commit_module._check_base_mode(write)  # noqa: SLF001 - the unit under test


# ---- fail closed ----------------------------------------------------------------------------------------------------------------------------

def test_an_unreadable_base_mode_stops_materialization(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o644)
    real_lstat = os.lstat
    target = str(workspace / "a.py")

    def lstat(path, *args, **kwargs):
        if str(path) == target:
            raise PermissionError("denied")
        return real_lstat(path, *args, **kwargs)

    with patch.object(terminal_commit.os, "lstat", lstat):
        with pytest.raises(CandidateMaterializationError, match="base file's mode"):
            materialize_candidate(str(candidate), str(workspace), files)


def test_an_unreadable_mode_at_commit_time_refuses_and_changes_nothing(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o644)
    writes = materialize_candidate(str(candidate), str(workspace), files)
    real_lstat = os.lstat
    target = str(workspace / "a.py")

    def lstat(path, *args, **kwargs):
        if str(path) == target:
            raise PermissionError("denied")
        return real_lstat(path, *args, **kwargs)

    with patch.object(commit_module.os, "lstat", lstat):
        with pytest.raises(BatchCommitError, match="revalidate the base mode"):
            commit_module.commit_revision_grounded_batch(writes, workspace_path=str(workspace), transaction_id="nomode")
    assert (workspace / "a.py").read_bytes() == OLD and _mode(workspace / "a.py") == 0o644


def test_a_staged_mode_that_is_not_the_bound_base_mode_is_refused(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o600)
    (write,) = materialize_candidate(str(candidate), str(workspace), files)
    with pytest.raises(BatchCommitError, match="does not change an existing file's mode"):
        commit_module.commit_revision_grounded_batch(
            [replace(write, mode=0o777)], workspace_path=str(workspace), transaction_id="widen")
    assert (workspace / "a.py").read_bytes() == OLD and _mode(workspace / "a.py") == 0o600


def test_a_failure_to_apply_the_mode_rolls_the_commit_back(tmp_path):
    workspace, candidate, files = _pair(tmp_path, 0o600)
    writes = materialize_candidate(str(candidate), str(workspace), files)
    with patch.object(commit_module.os, "chmod", side_effect=PermissionError("denied")):
        with pytest.raises(Exception):  # noqa: B017 - any refusal; the assertion that matters is the unchanged workspace below
            commit_module.commit_revision_grounded_batch(writes, workspace_path=str(workspace), transaction_id="nochmod")
    assert (workspace / "a.py").read_bytes() == OLD and _mode(workspace / "a.py") == 0o600
