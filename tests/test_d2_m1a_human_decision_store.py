"""D2 M1a (owner decision 2026-10-23): human-decision state lives in the operator's authority home, outside the repository.

The launch record, the one decision of each review and the review's diff are authority a person acts on. They are stored at
``<authority home>/launches/<workspace identity>/``; the old ``<workspace>/.kriya/control/launches/`` location is never read, trusted or migrated, so
anything a repository carries there (even a correctly stamped document) is absent and a fresh review is required. The M4 ownership contract is kept.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from kriya.context.workspace_identity import (
    WORKSPACE_OWNERSHIP_MISSING,
    WorkspaceOwnershipMissing,
    ownership_metadata,
    workspace_identity,
)
from kriya.control import launch_approval as approval
from kriya.control import persistence as P
from kriya.control.human_decision_store import HumanDecisionStorePathError, human_decision_dir

LID = "20261023T120000-abcdef12"
RID = "ra-" + "0" * 24
DIGEST = "sha256:" + "0" * 64
RECORD = {"phase": "RUNNING", "pending_approval": None}
DECISION = {"decision": "approve", "review_id": RID, "candidate_digest": DIGEST}
DIFF = {"review_id": RID, "diffs": ["--- a\n+++ b\n"]}


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)


@pytest.fixture(name="ws")
def _ws(tmp_path):
    path = tmp_path / "W"
    path.mkdir()
    _git_init(path)
    return str(path)


def _old_location(ws: str) -> Path:
    return Path(ws) / ".kriya" / "control" / "launches"


def _plant_old(ws: str) -> dict:
    """A repository-carried copy of every human-decision document, CORRECTLY STAMPED for this workspace - the strongest thing a repo could carry."""
    stamp = {"_workspace": ownership_metadata(ws)}
    directory = _old_location(ws)
    directory.mkdir(parents=True)
    planted = {
        "record": directory / f"{LID}.json",
        "decision": directory / f"{LID}.review-{RID}.json",
        "diff": directory / f"{LID}.review-diff-{RID}.json",
    }
    planted["record"].write_text(json.dumps({**RECORD, **stamp, "pending_approval": {"review_id": RID, "candidate_digest": DIGEST, "status": "PENDING"}}))
    planted["decision"].write_text(json.dumps({**DECISION, **stamp}))
    planted["diff"].write_text(json.dumps({**DIFF, **stamp}))
    return planted


def test_the_three_document_kinds_live_in_the_authority_home_not_in_the_workspace(ws):
    directory = human_decision_dir(ws)
    assert directory == os.path.join(os.path.realpath(os.environ["KRIYA_AUTHORITY_HOME"]), "launches", workspace_identity(ws))
    for path in (P.launch_record_path(ws, LID), P.launch_review_decision_path(ws, LID, RID), P.launch_review_diff_path(ws, LID, RID)):
        assert os.path.dirname(path) == directory
        assert not path.startswith(os.path.join(ws, ".kriya"))
    P.save_launch_record(ws, LID, RECORD)
    P.save_launch_review_decision(ws, LID, RID, DECISION)
    P.save_launch_review_diff(ws, LID, RID, DIFF)
    assert sorted(os.listdir(directory)) == sorted([f"{LID}.json", f"{LID}.review-{RID}.json", f"{LID}.review-diff-{RID}.json"])
    assert not (Path(ws) / ".kriya" / "control" / "launches").exists()  # nothing was written into the repository
    assert P.load_launch_record(ws, LID)["phase"] == "RUNNING"
    assert P.load_launch_review_decision(ws, LID, RID)["decision"] == "approve"
    assert P.load_launch_review_diff(ws, LID, RID)["diffs"] == DIFF["diffs"]


def test_the_directories_are_private_and_the_files_are_0600_and_no_staging_file_is_left(ws):
    P.save_launch_record(ws, LID, RECORD)
    P.save_launch_review_decision(ws, LID, RID, DECISION)
    directory = Path(human_decision_dir(ws))
    for level in (directory, directory.parent):
        assert stat.S_IMODE(level.stat().st_mode) == 0o700, level
    for entry in directory.iterdir():
        assert stat.S_IMODE(entry.stat().st_mode) == 0o600, entry
    assert not [name for name in os.listdir(directory) if ".staging-" in name]


def test_a_correctly_stamped_document_carried_in_the_repository_is_never_read_and_a_fresh_review_is_required(ws):
    planted = _plant_old(ws)
    before = {name: path.read_bytes() for name, path in planted.items()}
    assert P.load_launch_record(ws, LID) is None
    assert P.load_launch_review_decision(ws, LID, RID) is None
    assert P.load_launch_review_diff(ws, LID, RID) is None
    with pytest.raises(approval.ApprovalRefused) as refused:
        approval.decide(ws, LID, RID, DIGEST, "approve")
    assert refused.value.code == "LAUNCH_NOT_FOUND"  # the carried pending review is not a review: nothing to approve
    assert {name: path.read_bytes() for name, path in planted.items()} == before  # not migrated, not touched, not deleted


def test_a_new_record_for_the_same_launch_id_does_not_adopt_or_alter_the_old_file(ws):
    planted = _plant_old(ws)
    old_bytes = planted["record"].read_bytes()
    P.save_launch_record(ws, LID, {"phase": "ENDED", "pending_approval": None})
    assert planted["record"].read_bytes() == old_bytes
    assert P.load_launch_record(ws, LID)["phase"] == "ENDED"
    assert sorted(os.listdir(human_decision_dir(ws))) == [f"{LID}.json"]  # only what the new store wrote: no migration of the other two


def test_each_workspace_has_its_own_directory(tmp_path, ws):
    other = tmp_path / "Other"
    other.mkdir()
    _git_init(other)
    P.save_launch_record(ws, LID, {"phase": "RUNNING", "pending_approval": None})
    P.save_launch_record(str(other), LID, {"phase": "ENDED", "pending_approval": None})
    assert human_decision_dir(ws) != human_decision_dir(str(other))
    assert P.load_launch_record(ws, LID)["phase"] == "RUNNING" and P.load_launch_record(str(other), LID)["phase"] == "ENDED"


def test_the_m4_contract_is_kept_an_unstamped_document_is_never_overwritten_and_never_read_as_a_decision(ws):
    P.save_launch_record(ws, LID, RECORD)
    P.save_launch_review_diff(ws, LID, RID, DIFF)
    P.save_launch_review_decision(ws, LID, RID, DECISION)
    record_path, diff_path, decision_path = (P.launch_record_path(ws, LID), P.launch_review_diff_path(ws, LID, RID),
                                             P.launch_review_decision_path(ws, LID, RID))
    for path in (record_path, diff_path, decision_path):
        payload = json.loads(Path(path).read_text())
        payload.pop("_workspace")
        Path(path).write_text(json.dumps(payload))
    stripped = {path: Path(path).read_bytes() for path in (record_path, diff_path, decision_path)}
    with pytest.raises(WorkspaceOwnershipMissing):
        P.save_launch_record(ws, LID, RECORD)
    with pytest.raises(WorkspaceOwnershipMissing):
        P.save_launch_review_diff(ws, LID, RID, DIFF)
    with pytest.raises(P.UnreadableRunRecordError) as unreadable:
        P.load_launch_record(ws, LID)
    assert unreadable.value.reason_code == WORKSPACE_OWNERSHIP_MISSING
    assert P.load_launch_review_decision(ws, LID, RID) is None and P.load_launch_review_diff(ws, LID, RID) is None
    assert {path: Path(path).read_bytes() for path in stripped} == stripped  # nothing was re-stamped


def test_a_decision_is_written_once_and_the_first_writer_wins(ws):
    P.save_launch_review_decision(ws, LID, RID, DECISION)
    first = Path(P.launch_review_decision_path(ws, LID, RID)).read_bytes()
    with pytest.raises(FileExistsError):
        P.save_launch_review_decision(ws, LID, RID, {**DECISION, "decision": "reject"})
    assert Path(P.launch_review_decision_path(ws, LID, RID)).read_bytes() == first
    assert not [name for name in os.listdir(human_decision_dir(ws)) if ".staging-" in name]


def test_an_authority_home_inside_the_repository_is_refused_before_anything_is_written(ws, monkeypatch):
    inside = os.path.join(ws, "carried-authority-home")
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", inside)
    with pytest.raises(HumanDecisionStorePathError):
        P.save_launch_record(ws, LID, RECORD)
    assert not os.path.exists(inside)
    with pytest.raises(HumanDecisionStorePathError):
        P.launch_record_path(ws, LID)



def test_a_directory_that_already_exists_with_looser_permissions_is_tightened_to_private(ws):
    """mkdir(0700) covers a new directory only; a pre-created ``launches/`` or identity directory (0755) must not leave the records readable by others."""
    directory = Path(human_decision_dir(ws))
    directory.mkdir(parents=True)
    for level in (directory, directory.parent):
        level.chmod(0o755)
    P.save_launch_record(ws, LID, RECORD)
    for level in (directory, directory.parent):
        assert stat.S_IMODE(level.stat().st_mode) == 0o700, level
