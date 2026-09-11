"""MA4.8's own regression requirement, mirroring MA4.3/4.4/4.5/4.6/4.7:
ExecutionPolicy's audit-only integration into kriya/workflow/worktree.py's
create_git_worktree (the ONE real GIT_WRITE call site in Kriya's pipeline)
must never affect whether the real bootstrap commit happens, under any
condition including a misconfigured or outright broken policy engine.
"""
import os
import subprocess
from unittest.mock import MagicMock

import pytest

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


def test_a_hard_enforced_deny_blocks_the_real_bootstrap_commit(tmp_path, monkeypatch):
    """MA7.3: unlike an ordinary DENY (test_a_forced_deny_never_blocks... above),
    one of the fixed hard-invariant reason_codes (kriya.policy.enforcement.
    HARD_ENFORCED_REASON_CODES) really refuses - the bootstrap commit's own
    try/except catches the resulting PolicyDeniedError and skips the real
    commit (logged as "creating an initial empty commit failed"), exactly
    like any other failure at this step. With no bootstrap commit, the repo
    stays at zero commits and `git worktree add --detach` (later in
    create_git_worktree) has no HEAD to detach at and fails for real -
    that's the SAME pre-existing "falls back to the unisolated workspace"
    path any other bootstrap failure already takes (see this function's own
    "no commits yet" comment), not something this test's fix needs to (or
    should) paper over."""
    _init_zero_commit_repo(tmp_path)
    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.DENY, reason_code="GIT_FORCE_PUSH_DENIED", explanation="simulated",
    )))

    with pytest.raises(subprocess.CalledProcessError):
        create_git_worktree(str(tmp_path))

    # git log on a zero-commit repo exits non-zero ("does not have any
    # commits yet") rather than succeeding with empty output - that failure
    # itself IS the proof no bootstrap commit was made.
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert log.returncode != 0
    assert log.stdout.strip() == ""


def test_audit_call_observes_the_real_bootstrap_command(tmp_path, monkeypatch):
    _init_zero_commit_repo(tmp_path)
    captured = {}
    real_evaluate = worktree_mod._execution_policy.evaluate

    def spy(request):
        captured["command"] = request.command
        return real_evaluate(request)

    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", spy)
    create_git_worktree(str(tmp_path))
    # SEC-001-P1 (2026-09-11): worktree.py now prepends
    # `-c core.hooksPath=/dev/null` to every git invocation capable of
    # triggering a repository hook, including this bootstrap commit.
    assert captured["command"] == (
        "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Kriya", "-c", "user.email=kriya@local",
        "commit", "--allow-empty", "-m", "Kriya: initial commit (empty) to enable worktree isolation",
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


# --- POL-001-P3: the recognizer's real effect on the real bootstrap path ---

def test_zero_commit_bootstrap_policy_verdict_is_now_allow_not_require_approval(tmp_path, monkeypatch):
    """The recognizer added to kriya/policy/execution.py::_check_git_destructive
    changes what the REAL bootstrap commit's own evaluate() call returns -
    this is the actual gap POL-001-P3 closes (REQUIRE_APPROVAL was an honest
    but wrong verdict for a Kriya-internal, zero-file-change control-plane
    action). worktree.py's own _audit_git_write/enforce_hard_invariants
    call is mode-independent and never blocked on REQUIRE_APPROVAL either
    way, so this proves the SEMANTIC fix, independent of whether anything
    was ever functionally blocked before."""
    _init_zero_commit_repo(tmp_path)
    captured = []
    real_evaluate = worktree_mod._execution_policy.evaluate

    def spy(request):
        result = real_evaluate(request)
        captured.append(result)
        return result

    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", spy)
    worktree_path = create_git_worktree(str(tmp_path))

    assert len(captured) == 1
    assert captured[0].decision == PolicyDecision.ALLOW
    assert captured[0].reason_code == "KRIYA_INTERNAL_BOOTSTRAP_COMMIT_ALLOWED"

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1
    # Worktree isolation actually proceeded past the bootstrap step.
    assert os.path.isdir(worktree_path)

    # The bootstrap commit is --allow-empty - it must not have created or
    # changed any file in the real user workspace.
    tracked = subprocess.run(
        ["git", "show", "--stat", "--format=", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert tracked.stdout.strip() == ""


def test_greenfield_bootstrap_policy_verdict_is_allow(tmp_path, monkeypatch):
    """_bootstrap_greenfield_repository issues TWO real GIT_WRITE requests,
    `git init` then the bootstrap commit - both must resolve to their own
    dedicated ALLOW, not just one of the two (an `any()` check over the
    captured results would silently pass even if `git init` were still
    stuck at REQUIRE_APPROVAL, which is exactly what a first draft of this
    test did - caught and fixed by explicitly asserting both, in order)."""
    captured = []
    real_evaluate = worktree_mod._execution_policy.evaluate

    def spy(request):
        result = real_evaluate(request)
        captured.append(result)
        return result

    monkeypatch.setattr(worktree_mod._execution_policy, "evaluate", spy)
    # tmp_path is NOT a git repo at all yet - triggers _bootstrap_greenfield_repository.
    worktree_path = create_git_worktree(str(tmp_path))

    assert len(captured) == 2
    assert captured[0].decision == PolicyDecision.ALLOW
    assert captured[0].reason_code == "KRIYA_INTERNAL_BOOTSTRAP_INIT_ALLOWED"
    assert captured[1].decision == PolicyDecision.ALLOW
    assert captured[1].reason_code == "KRIYA_INTERNAL_BOOTSTRAP_COMMIT_ALLOWED"

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1
    assert os.path.isdir(worktree_path)
    assert os.path.isdir(worktree_path)
