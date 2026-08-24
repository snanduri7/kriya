"""MA7.7: adversarial policy suite - decision correctness AND effect
prevention, through the REAL (unmocked) ExecutionPolicy engine.

Decision-correctness for every hard-invariant rule (force-push spelling
variants, protected-ref deletion via push vs. branch -D, git config/remote
mutation, sudo) is already thoroughly covered by
tests/test_policy_git_destructive.py and tests/test_policy_command_allowlist.py
- not duplicated here. What was still missing after MA7.3: proof that the
REAL, unmocked ExecutionPolicy's decision for each of the 5 hard-enforced
reason_codes actually flows through enforce_hard_invariants() into a real
PolicyDeniedError - MA7.3's own tests only exercised 2 of the 5
(GIT_FORCE_PUSH_DENIED, COMMAND_SUDO_DENIED) end-to-end, and even those
used a MagicMock forcing the decision rather than a real adversarial
command through the real engine. This file closes both gaps: all 5 reason
codes through the real engine, plus one genuine effect-prevention test
where a real subprocess would have run (a detectable side-effect file) if
enforcement had failed to block it."""

import pytest

from kriya.policy.enforcement import enforce_hard_invariants
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision
from kriya.tools.validate import PolymorphicValidator


# --- Decision AND effect, through the real (unmocked) ExecutionPolicy ---

@pytest.mark.parametrize("description,request_kwargs,expected_reason_code", [
    (
        "sudo-prefixed command",
        dict(action_type=ActionType.RUN_COMMAND, command=("sudo", "rm", "-rf", "/")),
        "COMMAND_SUDO_DENIED",
    ),
    (
        "force-push to a branch",
        dict(action_type=ActionType.GIT_WRITE, command=("git", "push", "--force", "origin", "main")),
        "GIT_FORCE_PUSH_DENIED",
    ),
    (
        "protected branch deletion via git branch -D",
        dict(action_type=ActionType.GIT_WRITE, command=("git", "branch", "-D", "main")),
        "PROTECTED_REF_MUTATION_DENIED",
    ),
    (
        "protected ref deletion via git push --delete",
        dict(action_type=ActionType.GIT_WRITE, command=("git", "push", "origin", "--delete", "main")),
        "PROTECTED_REF_MUTATION_DENIED",
    ),
    (
        "git config mutation",
        dict(action_type=ActionType.GIT_WRITE, command=("git", "config", "user.email", "attacker@evil.example")),
        "GIT_CONFIG_MUTATION_DENIED",
    ),
    (
        "git remote URL rewrite",
        dict(action_type=ActionType.GIT_WRITE, command=("git", "remote", "set-url", "origin", "https://evil.example/repo.git")),
        "GIT_REMOTE_MUTATION_DENIED",
    ),
])
def test_real_adversarial_command_is_denied_and_raises(description, request_kwargs, expected_reason_code):
    policy = ExecutionPolicy()  # real engine, no mocking
    request = ActionRequest(**request_kwargs)

    with pytest.raises(PolicyDeniedError) as exc_info:
        enforce_hard_invariants(policy, request)

    assert exc_info.value.result.reason_code == expected_reason_code, description


def test_an_ordinary_non_adversarial_command_is_not_blocked_by_this_mechanism():
    """Negative control - enforce_hard_invariants must stay narrow. An
    ordinary git commit (real behavior: REQUIRE_APPROVAL, not one of the 5
    hard-enforced codes) must pass through unraised - if this DID raise,
    every real bootstrap commit in kriya/workflow/worktree.py would break."""
    policy = ExecutionPolicy()
    request = ActionRequest(action_type=ActionType.GIT_WRITE, command=("git", "commit", "-m", "normal work"))
    result = enforce_hard_invariants(policy, request)  # must not raise
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.reason_code == "GIT_WRITE_REQUIRES_APPROVAL"


# --- Genuine effect prevention: a real subprocess that WOULD have run ---

def test_adversarial_sudo_command_never_actually_executes(tmp_path):
    """Unlike MA7.3's own test (which mocked the policy decision to
    confirm the wiring), this drives a REAL adversarial command through
    the REAL ExecutionPolicy and proves the real subprocess never runs -
    a marker file only `sudo` (or a command chained after it) could create
    is confirmed absent afterward, not just "the function returned False.\""""
    marker = tmp_path / "sudo_ran.marker"
    validator = PolymorphicValidator(str(tmp_path))
    # No mocking of validator.execution_policy - this is the real engine.

    with pytest.raises(PolicyDeniedError):
        validator._run_cmd_with_timeout(
            ["sudo", "touch", str(marker)], cwd=str(tmp_path), timeout=10,
        )

    assert not marker.exists(), "the adversarial sudo command's real side effect happened - enforcement failed"
