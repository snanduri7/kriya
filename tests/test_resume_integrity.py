"""PRD-008 deterministic resume fingerprint invalidation and refusal."""

import hashlib
import subprocess
from unittest.mock import AsyncMock

import pytest

from kriya.config import AppConfig
from kriya.control.persistence import save_run_record
from kriya.control.run_record import RunLifecycle, RunRecord
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.checkpoint import (
    RESUME_INVALIDATION_MATRIX,
    ResumeAction,
    ResumeStatus,
    compute_config_fingerprint,
    compute_control_plane_hashes,
    compute_workspace_content_hash,
    compute_workspace_fingerprint,
    save_checkpoint,
    validate_resume_against_reality,
)
from kriya.workflow.resume_fingerprints import (
    ARTIFACT_DEPENDENCIES,
    CANDIDATE_HASH_KEY,
    FINGERPRINT_NAMES,
    Fingerprint,
    candidate_snapshot_digest,
    fingerprint_block,
)
from kriya.workflow.resume_fingerprints import (
    CHECKPOINT_KEY as RESUME_FINGERPRINTS_KEY,
)
from kriya.workflow.workflow import WorkflowEngine


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "source.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "source.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    return str(tmp_path)


# PRD-008: the flat checkpoint keys (config_fingerprint, skills_fingerprint,
# ...) and their skip-when-absent comparison are retired; every resume
# fingerprint now lives in the checkpoint's resume_fingerprints block and is
# compared by resume_fingerprints.compare_resume_fingerprints(). Its full
# behaviour is covered in test_prd008_resume_fingerprints.py; these keep the
# validator-level contract.

def _all_artifacts():
    return set(ARTIFACT_DEPENDENCIES)


def _checkpoint(fingerprints):
    # Reusing every artifact includes the candidate, so the checkpoint must
    # carry an intact one; otherwise candidate_integrity rightly invalidates.
    files = {"math.py": "def add(a, b):\n    return a + b\n"}
    return {
        RESUME_FINGERPRINTS_KEY: fingerprint_block(fingerprints),
        "final_files": files, CANDIDATE_HASH_KEY: candidate_snapshot_digest(files),
    }


@pytest.mark.parametrize("fingerprint", FINGERPRINT_NAMES)
def test_each_resume_fingerprint_has_machine_readable_invalidation(fingerprint):
    stored = {name: Fingerprint("same", "basis") for name in FINGERPRINT_NAMES}
    current = dict(stored, **{fingerprint: Fingerprint("different", "basis")})
    result = validate_resume_against_reality(
        _checkpoint(stored), "/unused",
        current_resume_fingerprints=current, reused_artifacts=_all_artifacts(),
    )
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.fingerprint == fingerprint
    assert decision.action == ResumeAction.INVALIDATE
    assert decision.invalidated_stages == RESUME_INVALIDATION_MATRIX[fingerprint][1]


def test_matching_fingerprints_preserve_safe_resume():
    fingerprints = {name: Fingerprint("same", "basis") for name in FINGERPRINT_NAMES}
    result = validate_resume_against_reality(
        _checkpoint(fingerprints), "/unused",
        current_resume_fingerprints=fingerprints, reused_artifacts=_all_artifacts(),
    )
    assert result.status == ResumeStatus.OK
    assert result.decisions == ()


def test_source_change_invalidates_context_candidate_and_verification(git_repo):
    checkpoint = compute_control_plane_hashes(git_repo)
    with open(f"{git_repo}/source.py", "w", encoding="utf-8") as handle:
        handle.write("VALUE = 2\n")
    result = validate_resume_against_reality(checkpoint, git_repo)
    assert result.status == ResumeStatus.NEEDS_REVIEW
    content_decision = next(
        item for item in result.decisions if item.fingerprint == "workspace_content_hash"
    )
    assert content_decision.invalidated_stages == ("context", "candidate", "verification")


def test_uncertain_run_record_refuses_normal_resume():
    record = RunRecord.new("run-1", "workspace-1", "base", "tree")
    record = record.transition(RunLifecycle.RUNNING)
    record = record.begin_commit(
        "tx-1", intent="APPLY_VERIFIED_CANDIDATE", candidate_hash=None,
    )
    result = validate_resume_against_reality({}, "/unused", run_record=record)
    assert result.status == ResumeStatus.REFUSED
    assert result.decisions[0].fingerprint == "commit_state"
    assert result.decisions[0].action == ResumeAction.REFUSE


def test_terminal_uncertain_record_refuses_normal_resume():
    record = RunRecord.new("run-1", "workspace-1", None, None)
    record = record.transition(RunLifecycle.RUNNING)
    record = record.transition(
        RunLifecycle.UNCERTAIN, commit_result="UNCERTAIN",
    )
    result = validate_resume_against_reality({}, "/unused", run_record=record)
    assert result.status == ResumeStatus.REFUSED


def test_validator_without_a_fingerprint_request_compares_nothing():
    # A caller that asks for no fingerprint comparison (the control-plane
    # hash bundle alone) gets none; the workflow always asks.
    result = validate_resume_against_reality({"stage": "planning"}, "/unused")
    assert result.status == ResumeStatus.OK
    assert result.fingerprint_comparisons == ()


def test_legacy_checkpoint_without_resume_fingerprints_is_unverified_not_resumable():
    # PRD-008 semantic change: before, a checkpoint missing a fingerprint
    # skipped that check. Now every fingerprint its reused artifacts depend
    # on is UNVERIFIED, which invalidates like a change.
    current = {name: Fingerprint("same", "basis") for name in FINGERPRINT_NAMES}
    result = validate_resume_against_reality(
        {"stage": "plan", "plan": "P"}, "/unused", current_resume_fingerprints=current,
    )
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert "planning" in result.invalidated_stages
    assert all("predates resume fingerprints" in item.reason
               for item in result.fingerprint_comparisons if item.invalidates)


@pytest.mark.asyncio
async def test_workflow_refuses_uncertain_commit_before_any_model_call(git_repo):
    prior = RunRecord.new("prior-run", "workspace", None, None)
    save_run_record(git_repo, prior, expected_revision=None)
    running = prior.transition(RunLifecycle.RUNNING)
    save_run_record(git_repo, running, expected_revision=1)
    eligible = running.begin_commit(
        "tx-prior", intent="APPLY_VERIFIED_CANDIDATE", candidate_hash=None,
    )
    save_run_record(git_repo, eligible, expected_revision=2)

    goal = "resume safely"
    config = AppConfig()
    save_checkpoint(git_repo, "checkpoint-1", {
        "stage": "plan",
        "workspace_fingerprint": compute_workspace_fingerprint(git_repo),
        "workspace_content_hash": compute_workspace_content_hash(git_repo),
        "config_fingerprint": compute_config_fingerprint(config.model_dump()),
        "goal_fingerprint": hashlib.sha256(f"{goal}\x00".encode()).hexdigest(),
        "_run_record": {"run_id": "prior-run", "revision": eligible.revision},
    })
    llm = LLMClient(config)
    llm.complete = AsyncMock()
    result = await WorkflowEngine(Kernel(config=config), llm).run_generation_workflow(
        goal=goal, workspace_path=git_repo, resume_id="checkpoint-1",
    )
    # PRD-008: the RunCoordinator refuses the whole run before resume logic
    # (or any model call) even starts; the validator's own REFUSED decision
    # stays covered at unit level above.
    assert result["status"] == "needs_review"
    assert result["reason_codes"] == ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"]
    assert result["uncertain_run_ids"] == ["prior-run"]
    assert result["recovery_command"] == "kriya runs recover"
    llm.complete.assert_not_awaited()
