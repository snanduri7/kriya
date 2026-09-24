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


@pytest.mark.parametrize(
    "fingerprint",
    [
        "config_fingerprint", "goal_fingerprint", "approved_plan_hash",
        "obligation_ledger_hash", "skills_fingerprint",
        "model_runtime_fingerprint", "containment_fingerprint",
        "toolchain_fingerprint", "verification_policy_fingerprint",
    ],
)
def test_each_safety_fingerprint_has_machine_readable_invalidation(fingerprint):
    result = validate_resume_against_reality(
        {fingerprint: "before"}, "/unused",
        current_fingerprints={fingerprint: "after"},
    )
    assert result.status == ResumeStatus.NEEDS_REVIEW
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.fingerprint == fingerprint
    assert decision.action == ResumeAction.INVALIDATE
    assert decision.invalidated_stages == RESUME_INVALIDATION_MATRIX[fingerprint][1]


def test_matching_fingerprints_preserve_safe_resume():
    fingerprints = {
        key: "same" for key in RESUME_INVALIDATION_MATRIX
        if key not in {"base_commit", "tree_hash", "workspace_content_hash", "commit_state"}
    }
    result = validate_resume_against_reality(
        fingerprints, "/unused", current_fingerprints=fingerprints,
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


def test_legacy_checkpoint_without_new_optional_fingerprints_remains_loadable():
    result = validate_resume_against_reality({"stage": "planning"}, "/unused")
    assert result.status == ResumeStatus.OK


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
    assert result["status"] == "resume_refused"
    assert result["reason_codes"] == ["UNCERTAIN_COMMIT_STATE"]
    assert result["resume_decisions"][0]["action"] == "refuse"
    llm.complete.assert_not_awaited()
