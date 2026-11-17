"""S8 (C-07, ruling 9): the accepted-candidate snapshot's own checks, on the pure owner (kriya/workflow/accepted_candidate.py).

A snapshot entry is never trusted because it exists. ``replayable_units`` hands a unit's accepted work to the replay only when the plan, the two halves of the
ordered pair (the ControlState's digest and the document's entry), the digest RECOMPUTED from the document's bytes, the real workspace's base and the unit's
planned scope all agree; each test below breaks exactly one of them. The pipeline behaviour (the replay through the unit's gates) is
tests/test_s8_accepted_work_resume.py.
"""
import copy
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from kriya.repository.commit import read_file_revision
from kriya.workflow import accepted_candidate as ac
from kriya.workflow.terminal_commit import CandidateFile, bind_candidate, materialize_candidate

NEW, EXISTING = "shop/new.py", "shop/existing.py"
NEW_SOURCE, EDITED = "def new():\n    return 1\n", "def existing():\n    return 2\n"
PLAN_HASH = "p" * 64


@pytest.fixture
def trees(tmp_path) -> Tuple[Path, Path, Dict[str, str]]:
    """(the real workspace, the plan sandbox holding one unit's accepted work, the base revisions the plan recorded)."""
    workspace, candidate = tmp_path / "workspace", tmp_path / "candidate"
    for root in (workspace, candidate):
        (root / "shop").mkdir(parents=True)
        (root / EXISTING).write_text("def existing():\n    return 0\n")
    revisions = {path: read_file_revision(str(workspace / path)) for path in (NEW, EXISTING)}
    (candidate / NEW).write_text(NEW_SOURCE)
    (candidate / EXISTING).write_text(EDITED)
    return workspace, candidate, revisions


def _entry(trees) -> Dict[str, Any]:
    workspace, candidate, revisions = trees
    entry = ac.capture_unit(paths=[NEW, EXISTING], creates={NEW}, candidate_root=str(candidate), workspace_path=str(workspace), base_revisions=revisions)
    assert entry is not None
    return entry


def _replay(trees, document, *, referenced=None, plan_hash=PLAN_HASH, planned=(NEW, EXISTING)):
    workspace = trees[0]
    return ac.replayable_units(
        document, referenced=referenced if referenced is not None else {"s1": document["units"]["s1"]["digest"]}, plan_hash=plan_hash,
        workspace_path=str(workspace), current_revision=read_file_revision, planned_paths={"s1": list(planned)})


def test_the_entry_is_the_exact_work_and_its_digest_is_the_existing_candidate_binding(trees):
    workspace, candidate, revisions = trees
    entry = _entry(trees)
    assert {item["path"]: item["content"] for item in entry["files"]} == {NEW: NEW_SOURCE, EXISTING: EDITED}
    assert {item["path"]: item["expected_base_exists"] for item in entry["files"]} == {NEW: False, EXISTING: True}
    # no new hash: exactly bind_candidate over the unit's writes against the real workspace (the S7 subject)
    writes = materialize_candidate(str(candidate), str(workspace), [
        CandidateFile(relpath=path, expected_base_revision=revisions[path], expected_base_exists=path != NEW) for path in sorted((NEW, EXISTING))])
    assert entry["digest"] == bind_candidate(writes, str(workspace)).digest
    assert "valid" not in entry and "verdict" not in entry and "passed" not in entry, "no stored verdict"


def test_an_exact_pair_is_replayable_as_a_carried_candidate(trees):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    replayable, refused = _replay(trees, document)
    assert refused == {} and sorted(replayable) == ["s1"]
    carried = replayable["s1"]
    assert sorted(carried.paths) == sorted((NEW, EXISTING)) and all(file.intact() for file in carried.files)
    assert {file.path: file.content for file in carried.files} == {NEW: NEW_SOURCE, EXISTING: EDITED}
    assert carried.binding_digest == document["units"]["s1"]["digest"]


@pytest.mark.parametrize("field, value", [("content", NEW_SOURCE + "# one more byte\n"), ("mode", 0o755), ("expected_base_revision", "0" * 64),
                                          ("expected_base_exists", True), ("path", "shop/renamed.py")])
def test_any_changed_byte_mode_path_or_base_fact_is_a_digest_mismatch(trees, field, value):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    tampered = copy.deepcopy(document)
    target = next(item for item in tampered["units"]["s1"]["files"] if item["path"] == NEW)
    target[field] = value
    replayable, refused = _replay(trees, tampered, planned=(NEW, EXISTING, "shop/renamed.py"))
    assert replayable == {} and refused == {"s1": ac.SNAPSHOT_BYTES_CHANGED}


def test_the_two_halves_must_name_the_same_work(trees):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    replayable, refused = _replay(trees, document, referenced={"s1": "f" * 64})
    assert replayable == {} and refused == {"s1": ac.SNAPSHOT_REFERENCE_MISMATCH}
    # and a re-signed forgery (the entry's own digest recomputed over other bytes) still fails the ControlState's half
    forged = copy.deepcopy(document)
    forged["units"]["s1"]["files"][0]["content"] = "def new():\n    return 'forged'\n"
    workspace = trees[0]
    forged["units"]["s1"]["digest"] = bind_candidate(ac._entry_writes(forged["units"]["s1"], str(workspace)), str(workspace)).digest
    replayable, refused = _replay(trees, forged, referenced={"s1": document["units"]["s1"]["digest"]})
    assert replayable == {} and refused == {"s1": ac.SNAPSHOT_REFERENCE_MISMATCH}


def test_a_state_that_names_a_unit_the_snapshot_does_not_hold_proves_nothing(trees):
    document = ac.snapshot_document(PLAN_HASH, {})
    replayable, refused = _replay(trees, document, referenced={"s1": "a" * 64})
    assert replayable == {} and refused == {"s1": ac.SNAPSHOT_UNIT_MISSING}


def test_a_snapshot_entry_the_state_does_not_name_is_ignored(trees):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    assert _replay(trees, document, referenced={}) == ({}, {})


def test_a_snapshot_taken_under_another_plan_is_not_used(trees):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    for other in ("q" * 64, None, ""):
        replayable, refused = _replay(trees, document, plan_hash=other)
        assert replayable == {} and refused == {"s1": ac.SNAPSHOT_PLAN_CHANGED}


@pytest.mark.parametrize("change", ["base_file_edited", "create_target_now_exists", "base_file_removed"])
def test_a_changed_workspace_base_is_not_the_base_the_work_was_accepted_on(trees, change):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    workspace = trees[0]
    if change == "base_file_edited":
        (workspace / EXISTING).write_text("def existing():\n    return 'edited by someone else'\n")
    elif change == "create_target_now_exists":
        (workspace / NEW).write_text("")              # an EMPTY file has the revision of "no bytes": only the existence fact tells them apart
    else:
        (workspace / EXISTING).unlink()
    replayable, refused = _replay(trees, document)
    assert replayable == {} and refused == {"s1": ac.SNAPSHOT_BASE_CHANGED}


def test_work_outside_the_units_current_planned_scope_is_not_replayed(trees):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    replayable, refused = _replay(trees, document, planned=(NEW,))
    assert replayable == {} and refused == {"s1": ac.SNAPSHOT_OUT_OF_SCOPE}


@pytest.mark.parametrize("document", [None, "text", {}, {"schema_version": 99, "plan_hash": PLAN_HASH, "units": {}},
                                      {"schema_version": 1, "plan_hash": PLAN_HASH, "units": []}])
def test_a_missing_or_unreadable_document_reuses_nothing(trees, document):
    assert _replay(trees, document, referenced={"s1": "a" * 64}) == ({}, {"s1": ac.SNAPSHOT_MISSING})


@pytest.mark.parametrize("damage", ["no_files", "absolute_path", "parent_path", "content_not_text", "no_digest"])
def test_an_entry_that_is_not_an_entry_is_refused_not_guessed(trees, damage):
    document = ac.snapshot_document(PLAN_HASH, {"s1": _entry(trees)})
    recorded = document["units"]["s1"]["digest"]
    entry = document["units"]["s1"]
    if damage == "no_files":
        entry["files"] = []
    elif damage == "absolute_path":
        entry["files"][0]["path"] = "/etc/passwd"
    elif damage == "parent_path":
        entry["files"][0]["path"] = "../outside.py"
    elif damage == "content_not_text":
        entry["files"][0]["content"] = 7
    else:
        entry.pop("digest")
    replayable, refused = _replay(trees, document, referenced={"s1": recorded})
    assert replayable == {} and refused == {"s1": ac.SNAPSHOT_MALFORMED}


def test_work_that_cannot_be_captured_exactly_is_not_captured_at_all(trees):
    workspace, candidate, revisions = trees
    common = dict(creates={NEW}, candidate_root=str(candidate), workspace_path=str(workspace), base_revisions=revisions)
    assert ac.capture_unit(paths=[], **common) is None                                   # wrote nothing
    assert ac.capture_unit(paths=[NEW, "shop/unplanned.py"], **common) is None           # a path with no recorded base
    (candidate / NEW).write_bytes(b"\xff\xfe not utf-8")
    assert ac.capture_unit(paths=[NEW, EXISTING], **common) is None                      # not strict UTF-8: never carried in part
    (candidate / NEW).unlink()
    assert ac.capture_unit(paths=[NEW, EXISTING], **common) is None                      # the file is gone
