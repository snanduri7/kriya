"""MA4.8's own regression requirement, mirroring MA4.3/4.4/4.5/4.6/4.7:
ExecutionPolicy's audit-only integration into kriya/workflow/worktree.py's
create_git_worktree (the ONE real GIT_WRITE call site in Kriya's pipeline)
must never affect whether the real bootstrap commit happens, under any
condition including a misconfigured or outright broken policy engine.
"""
import subprocess
from unittest.mock import MagicMock

import kriya.workflow.worktree as worktree_mod
from kriya.policy.model import PolicyDecision, PolicyResult
from kriya.workflow.worktree import create_git_worktree


def _init_zero_commit_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)


def test_bootstrap_commit_still_happens_even_though_policy_requires_approval(tmp_path):
    """No real caller construction differs from MA4.8's own honest audit
    signal (GIT_WRITE_REQUIRES_APPROVAL, not ALLOW) - the real commit must
    still happen regardless, since this is audit-only."""
    _init_zero_commit_repo(tmp_path)
    create_git_worktree(str(tmp_path))
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1


def test_a_broken_policy_engine_never_blocks_the_real_bootstrap_commit(tmp_path, monkeypatch):
    _init_zero_commit_repo(tmp_path)
    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", MagicMock(side_effect=RuntimeError("broke")))

    create_git_worktree(str(tmp_path))
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1


def test_a_forced_deny_never_blocks_the_real_bootstrap_commit(tmp_path, monkeypatch):
    _init_zero_commit_repo(tmp_path)
    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.DENY, reason_code="TEST_FORCED_DENY", explanation="simulated",
    )))

    create_git_worktree(str(tmp_path))
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1


def test_audit_call_observes_the_real_bootstrap_command(tmp_path, monkeypatch):
    _init_zero_commit_repo(tmp_path)
    captured = {}
    real_evaluate = worktree_mod._execution_policy.evaluate

    def spy(request):
        captured["command"] = request.command
        return real_evaluate(request)

    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", spy)
    create_git_worktree(str(tmp_path))
    assert captured["command"] == (
        "git", "commit", "--allow-empty", "-m", "Kriya: initial commit (empty) to enable worktree isolation",
    )


def test_audit_call_not_issued_when_repo_already_has_commits(tmp_path, monkeypatch):
    """The bootstrap commit only fires for a zero-commit repo - the audit
    call is scoped to that same real condition, not called unconditionally
    on every create_git_worktree invocation."""
    _init_zero_commit_repo(tmp_path)
    (tmp_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    called = []
    real_evaluate = worktree_mod._execution_policy.evaluate

    def spy(request):
        called.append(request)
        return real_evaluate(request)

    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", spy)
    create_git_worktree(str(tmp_path))
    assert called == []
