"""MA5.9: checkpoint control-plane hash bundle + resume-vs-reality
validation (kriya/workflow/checkpoint.py) - real git operations against a
real temp repo (never mocked, since the whole point is proving these are
real commit/tree hashes), and validate_resume_against_reality()'s
"only checks what both sides have" behavior."""

import subprocess
import tempfile

import pytest

from kriya.control.artifacts import ArtifactRecord, ArtifactRegistry
from kriya.control.contracts import ContractRegistry
from kriya.control.state import ControlState
from kriya.workflow.checkpoint import (
    ResumeStatus,
    compute_base_commit,
    compute_control_plane_hashes,
    compute_registry_hash,
    compute_tree_hash,
    validate_resume_against_reality,
)


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


# --- compute_base_commit / compute_tree_hash: real git ---

def test_compute_base_commit_returns_the_real_head_sha(git_repo):
    commit = compute_base_commit(git_repo)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True)
    assert commit == result.stdout.strip()


def test_compute_tree_hash_returns_the_real_tree_object(git_repo):
    tree_hash = compute_tree_hash(git_repo)
    result = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=git_repo, capture_output=True, text=True)
    assert tree_hash == result.stdout.strip()


def test_tree_hash_changes_when_content_actually_changes(git_repo):
    before = compute_tree_hash(git_repo)
    with open(f"{git_repo}/a.py", "w") as f:
        f.write("print(2)")
    subprocess.run(["git", "add", "a.py"], cwd=git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=git_repo, capture_output=True)
    after = compute_tree_hash(git_repo)
    assert before != after


def test_returns_none_for_a_non_git_directory():
    with tempfile.TemporaryDirectory() as not_a_repo:
        assert compute_base_commit(not_a_repo) is None
        assert compute_tree_hash(not_a_repo) is None


# --- compute_registry_hash ---

def test_compute_registry_hash_is_stable_and_content_sensitive():
    reg = ContractRegistry()
    reg.register("M1:X", "X", "M1", shape={"a": 1})
    hash_a = compute_registry_hash(reg.to_dict())
    hash_b = compute_registry_hash(reg.to_dict())
    assert hash_a == hash_b
    reg.approve("M1:X")
    assert compute_registry_hash(reg.to_dict()) != hash_a


# --- compute_control_plane_hashes ---

def test_bundle_includes_git_derived_fields(git_repo):
    bundle = compute_control_plane_hashes(git_repo)
    assert bundle["base_commit"] == compute_base_commit(git_repo)
    assert bundle["tree_hash"] == compute_tree_hash(git_repo)
    assert bundle["schema_version"] == 1


def test_bundle_omits_none_for_arguments_not_supplied(git_repo):
    bundle = compute_control_plane_hashes(git_repo)
    assert bundle["control_state_hash"] is None
    assert bundle["contract_hash"] is None
    assert bundle["artifact_registry_hash"] is None
    assert bundle["context_package_hash"] is None
    assert bundle["plan_hash"] is None


def test_bundle_includes_hashes_for_every_object_supplied(git_repo):
    control_state = ControlState.new(run_id="run-1")
    contracts = ContractRegistry()
    artifacts = ArtifactRegistry()
    bundle = compute_control_plane_hashes(
        git_repo, control_state=control_state, contract_registry=contracts, artifact_registry=artifacts,
        plan_hash="planhash123",
    )
    assert bundle["control_state_hash"] == control_state.content_hash()
    assert bundle["contract_hash"] == compute_registry_hash(contracts.to_dict())
    assert bundle["artifact_registry_hash"] == compute_registry_hash(artifacts.to_dict())
    assert bundle["plan_hash"] == "planhash123"


# --- validate_resume_against_reality ---

def test_validation_ok_when_everything_matches(git_repo):
    checkpoint_data = compute_control_plane_hashes(git_repo)
    result = validate_resume_against_reality(checkpoint_data, git_repo)
    assert result.status == ResumeStatus.OK
    assert result.mismatches == ()


def test_validation_needs_review_on_base_commit_drift(git_repo):
    checkpoint_data = compute_control_plane_hashes(git_repo)
    with open(f"{git_repo}/b.py", "w") as f:
        f.write("print(2)")
    subprocess.run(["git", "add", "b.py"], cwd=git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=git_repo, capture_output=True)

    result = validate_resume_against_reality(checkpoint_data, git_repo)
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert any("base_commit" in m for m in result.mismatches)


def test_validation_needs_review_on_control_state_drift(git_repo):
    control_state = ControlState.new(run_id="run-1")
    checkpoint_data = compute_control_plane_hashes(git_repo, control_state=control_state)

    drifted_state = control_state.with_updates(current_milestone_id="M2")
    result = validate_resume_against_reality(checkpoint_data, git_repo, control_state=drifted_state)
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert any("control_state" in m for m in result.mismatches)


def test_validation_needs_review_on_contract_registry_drift(git_repo):
    contracts = ContractRegistry()
    contracts.register("M1:X", "X", "M1", shape={})
    checkpoint_data = compute_control_plane_hashes(git_repo, contract_registry=contracts)

    contracts.approve("M1:X")  # mutates state after the checkpoint was taken
    result = validate_resume_against_reality(checkpoint_data, git_repo, contract_registry=contracts)
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert any("contract_hash" in m for m in result.mismatches)


def test_legacy_checkpoint_with_no_control_plane_fields_is_ok():
    """Do not require all fields for legacy checkpoints - a checkpoint
    saved before MA5 (or by a caller not yet using these fields) has none
    of these keys at all, and validation must not treat their absence as
    a mismatch."""
    result = validate_resume_against_reality({"stage": "quality_gates"}, "/some/workspace")
    assert result.status == ResumeStatus.OK


def test_missing_live_object_skips_that_specific_check_not_a_mismatch(git_repo):
    """The checkpoint HAS a control_state_hash, but the caller doesn't
    supply a live ControlState to compare it against (e.g. hasn't built
    one yet on this resume path) - that check is skipped, not failed."""
    control_state = ControlState.new(run_id="run-1")
    checkpoint_data = compute_control_plane_hashes(git_repo, control_state=control_state)
    result = validate_resume_against_reality(checkpoint_data, git_repo, control_state=None)
    assert result.status == ResumeStatus.OK
