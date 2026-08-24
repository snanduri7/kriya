"""MA6.7: per-subtask checkpoint/resume (kriya/workflow/subtask_checkpoint.py) -
first real pytest coverage for this module. Real git operations against a
real temp repo for the resume-point tests, matching
tests/test_checkpoint_control_plane_hashes.py's own convention."""

import subprocess
import tempfile

import pytest

from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, Subtask
from kriya.workflow.subtask_checkpoint import (
    ResumePointStatus,
    SubtaskCheckpoint,
    load_subtask_checkpoints,
    record_subtask_checkpoint,
    resolve_subtask_resume_point,
    topological_subtask_order,
)
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import SubtaskStatus


def _init_git_repo_with_a_commit(path):
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, capture_output=True)
    with open(f"{path}/a.py", "w") as f:
        f.write("print(1)")
    subprocess.run(["git", "add", "a.py"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True)


@pytest.fixture
def git_repo():
    with tempfile.TemporaryDirectory() as d:
        _init_git_repo_with_a_commit(d)
        yield d


def _subtask(sid, depends_on=None):
    return Subtask(id=sid, description="do a thing", execution_method=ExecutionMethod.MODEL, depends_on=depends_on or [])


def _plan(*subtasks):
    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=list(subtasks))


def _real_hashes(workspace_path):
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=workspace_path, capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace_path, capture_output=True, text=True).stdout.strip()
    return commit, tree


# --- SubtaskCheckpoint round-trip ---

def test_subtask_checkpoint_to_dict_from_dict_round_trips():
    cp = SubtaskCheckpoint(
        subtask_id="s1", status=SubtaskStatus.COMPLETED, base_commit="abc", tree_hash="def",
        plan_hash="planhash", timestamp=123.0,
    )
    restored = SubtaskCheckpoint.from_dict(cp.to_dict())
    assert restored == cp


# --- record_subtask_checkpoint / load_subtask_checkpoints ---

def test_record_subtask_checkpoint_does_not_mutate_input_dict():
    original = {"other_field": "unchanged"}
    cp = SubtaskCheckpoint(subtask_id="s1", status=SubtaskStatus.COMPLETED)
    result = record_subtask_checkpoint(original, cp)
    assert "subtask_checkpoints" not in original
    assert result["other_field"] == "unchanged"
    assert "s1" in result["subtask_checkpoints"]


def test_record_subtask_checkpoint_overwrites_same_subtask_id():
    data = {}
    cp1 = SubtaskCheckpoint(subtask_id="s1", status=SubtaskStatus.FAILED, timestamp=1.0)
    data = record_subtask_checkpoint(data, cp1)
    cp2 = SubtaskCheckpoint(subtask_id="s1", status=SubtaskStatus.COMPLETED, timestamp=2.0)
    data = record_subtask_checkpoint(data, cp2)

    loaded = load_subtask_checkpoints(data)
    assert len(loaded) == 1
    assert loaded["s1"].status == SubtaskStatus.COMPLETED


def test_load_subtask_checkpoints_empty_when_absent():
    assert load_subtask_checkpoints({}) == {}


# --- topological_subtask_order ---

def test_topological_order_respects_dependencies():
    plan = _plan(_subtask("s3", ["s2"]), _subtask("s1"), _subtask("s2", ["s1"]))
    order = topological_subtask_order(plan)
    assert order.index("s1") < order.index("s2") < order.index("s3")


def test_topological_order_is_deterministic_for_independent_subtasks():
    plan = _plan(_subtask("c"), _subtask("a"), _subtask("b"))
    assert topological_subtask_order(plan) == ["a", "b", "c"]


# --- resolve_subtask_resume_point ---

def test_resume_point_fresh_start_when_no_checkpoints_exist():
    plan = _plan(_subtask("s1"), _subtask("s2", ["s1"]))
    result = resolve_subtask_resume_point(plan, {}, "/tmp/irrelevant")
    assert result.status == ResumePointStatus.FRESH_START
    assert result.next_subtask_id == "s1"


def test_resume_point_plan_hash_mismatch_is_needs_review():
    plan = _plan(_subtask("s1"))
    checkpoint_data = {"plan_hash": "stale-hash"}
    result = resolve_subtask_resume_point(plan, checkpoint_data, "/tmp/irrelevant")
    assert result.status == ResumePointStatus.NEEDS_REVIEW
    assert "plan_hash" in result.mismatches[0]


def test_resume_point_resumes_at_next_incomplete_subtask(git_repo):
    plan = _plan(_subtask("s1"), _subtask("s2", ["s1"]))
    commit, tree = _real_hashes(git_repo)
    checkpoint_data = record_subtask_checkpoint(
        {"plan_hash": plan.content_hash()},
        SubtaskCheckpoint(
            subtask_id="s1", status=SubtaskStatus.COMPLETED,
            base_commit=commit, tree_hash=tree, plan_hash=plan.content_hash(),
        ),
    )

    result = resolve_subtask_resume_point(plan, checkpoint_data, git_repo)

    assert result.status == ResumePointStatus.RESUME
    assert result.next_subtask_id == "s2"
    assert result.completed_subtask_ids == ["s1"]


def test_resume_point_already_complete_when_every_subtask_done(git_repo):
    plan = _plan(_subtask("s1"))
    commit, tree = _real_hashes(git_repo)
    checkpoint_data = record_subtask_checkpoint(
        {"plan_hash": plan.content_hash()},
        SubtaskCheckpoint(
            subtask_id="s1", status=SubtaskStatus.COMPLETED,
            base_commit=commit, tree_hash=tree, plan_hash=plan.content_hash(),
        ),
    )

    result = resolve_subtask_resume_point(plan, checkpoint_data, git_repo)

    assert result.status == ResumePointStatus.ALREADY_COMPLETE


def test_resume_point_tree_hash_mismatch_is_needs_review(git_repo):
    plan = _plan(_subtask("s1"), _subtask("s2", ["s1"]))
    checkpoint_data = record_subtask_checkpoint(
        {"plan_hash": plan.content_hash()},
        SubtaskCheckpoint(
            subtask_id="s1", status=SubtaskStatus.COMPLETED,
            base_commit="stale-commit", tree_hash="stale-tree-hash", plan_hash=plan.content_hash(),
        ),
    )

    result = resolve_subtask_resume_point(plan, checkpoint_data, git_repo)

    assert result.status == ResumePointStatus.NEEDS_REVIEW
    assert result.completed_subtask_ids == ["s1"]
    assert "tree_hash" in result.mismatches[0]


def test_resume_point_stops_at_first_non_completed_subtask(git_repo):
    plan = _plan(_subtask("s1"), _subtask("s2", ["s1"]), _subtask("s3", ["s2"]))
    commit, tree = _real_hashes(git_repo)
    checkpoint_data = {"plan_hash": plan.content_hash()}
    checkpoint_data = record_subtask_checkpoint(
        checkpoint_data,
        SubtaskCheckpoint(subtask_id="s1", status=SubtaskStatus.COMPLETED, base_commit=commit, tree_hash=tree, plan_hash=plan.content_hash()),
    )
    checkpoint_data = record_subtask_checkpoint(
        checkpoint_data,
        SubtaskCheckpoint(subtask_id="s2", status=SubtaskStatus.FAILED, plan_hash=plan.content_hash()),
    )

    result = resolve_subtask_resume_point(plan, checkpoint_data, git_repo)

    assert result.status == ResumePointStatus.RESUME
    assert result.completed_subtask_ids == ["s1"]
    assert result.next_subtask_id == "s2"
