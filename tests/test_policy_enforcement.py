"""MA7.3: kriya/policy/enforcement.py - narrow, explicitly-authorized real
enforcement for a fixed set of ExecutionPolicy hard invariants. Mirrors
tests/test_policy_filesystem_authorized_writer.py's own precedent."""

from unittest.mock import MagicMock

import pytest

from kriya.policy.enforcement import HARD_ENFORCED_REASON_CODES, enforce_hard_invariants
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult


def _policy_returning(decision, reason_code):
    policy = MagicMock(spec=ExecutionPolicy)
    policy.evaluate = MagicMock(return_value=PolicyResult(decision=decision, reason_code=reason_code, explanation="x"))
    return policy


@pytest.mark.parametrize("reason_code", sorted(HARD_ENFORCED_REASON_CODES))
def test_each_hard_enforced_reason_code_raises(reason_code):
    policy = _policy_returning(PolicyDecision.DENY, reason_code)
    request = ActionRequest(action_type=ActionType.RUN_COMMAND, command=("x",))
    with pytest.raises(PolicyDeniedError) as exc_info:
        enforce_hard_invariants(policy, request)
    assert exc_info.value.result.reason_code == reason_code
    assert exc_info.value.request is request


def test_a_deny_for_an_unlisted_reason_code_does_not_raise():
    policy = _policy_returning(PolicyDecision.DENY, "COMMAND_NOT_ALLOWLISTED")
    request = ActionRequest(action_type=ActionType.RUN_COMMAND, command=("x",))
    result = enforce_hard_invariants(policy, request)
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "COMMAND_NOT_ALLOWLISTED"


@pytest.mark.parametrize("decision", [PolicyDecision.ALLOW, PolicyDecision.ALLOW_SANDBOXED, PolicyDecision.REQUIRE_APPROVAL])
def test_non_deny_decisions_never_raise_even_with_a_hard_enforced_looking_reason_code(decision):
    policy = _policy_returning(decision, "COMMAND_SUDO_DENIED")
    request = ActionRequest(action_type=ActionType.RUN_COMMAND, command=("x",))
    result = enforce_hard_invariants(policy, request)
    assert result.decision == decision


def test_returns_the_same_policyresult_evaluate_produced_when_not_raising():
    policy = _policy_returning(PolicyDecision.ALLOW, "SOME_ALLOW")
    request = ActionRequest(action_type=ActionType.RUN_COMMAND, command=("x",))
    result = enforce_hard_invariants(policy, request)
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "SOME_ALLOW"


def test_hard_enforced_set_is_exactly_the_documented_five():
    """Pin the exact set - a silent addition/removal here is a real
    behavior change (a new real DENY, or one fewer) that deserves a
    failing test to force a conscious review, not a passive drift."""
    assert HARD_ENFORCED_REASON_CODES == frozenset({
        "COMMAND_SUDO_DENIED",
        "GIT_FORCE_PUSH_DENIED",
        "PROTECTED_REF_MUTATION_DENIED",
        "GIT_CONFIG_MUTATION_DENIED",
        "GIT_REMOTE_MUTATION_DENIED",
    })
