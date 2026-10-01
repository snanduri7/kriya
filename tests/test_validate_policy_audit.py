"""MA4.4's own regression requirement, mirroring MA4.3's for core/llm.py:
ExecutionPolicy's audit-only integration into PolymorphicValidator must
never affect whether ProcessController actually runs a command, under any
condition including a misconfigured or outright broken policy engine.
"""
from unittest.mock import MagicMock

import pytest

from kriya.policy.errors import PolicyDeniedError
from kriya.policy.model import PolicyDecision, PolicyResult
from kriya.tools.validate import PolymorphicValidator


def test_command_still_runs_even_though_policy_denies_it_today(tmp_path):
    """Every real Kriya command hits COMMAND_NOT_ALLOWLISTED or worse today
    (MA4.4's starter allowlist is deliberately narrow) - the actual
    subprocess must still execute regardless."""
    validator = PolymorphicValidator(str(tmp_path))
    result = validator._run_cmd_with_timeout(["pip", "--version"], cwd=str(tmp_path), timeout=30)
    assert result["returncode"] == 0


def test_a_broken_policy_engine_never_blocks_the_real_command(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    validator.execution_policy.evaluate = MagicMock(side_effect=RuntimeError("policy engine broke"))

    result = validator._run_cmd_with_timeout(["pip", "--version"], cwd=str(tmp_path), timeout=30)
    assert result["returncode"] == 0


def test_a_forced_deny_never_blocks_the_real_command(tmp_path):
    """Even an explicit DENY result from policy must not gate execution -
    MA4.4 is audit-only; enforcement is a future, separately-scoped task."""
    validator = PolymorphicValidator(str(tmp_path))
    validator.execution_policy.evaluate = MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.DENY,
        reason_code="TEST_FORCED_DENY",
        explanation="simulated",
    ))

    result = validator._run_cmd_with_timeout(["pip", "--version"], cwd=str(tmp_path), timeout=30)
    assert result["returncode"] == 0


def test_a_hard_enforced_deny_really_blocks_the_real_command(tmp_path):
    """MA7.3: unlike an ordinary DENY (test_a_forced_deny_never_blocks... above),
    COMMAND_SUDO_DENIED is one of the fixed hard-invariant reason_codes
    (kriya.policy.enforcement.HARD_ENFORCED_REASON_CODES) - it really
    raises PolicyDeniedError, and _run_cmd_with_timeout has no try/except
    of its own around _audit_run_command, so the real subprocess never
    runs."""
    validator = PolymorphicValidator(str(tmp_path))
    validator.execution_policy.evaluate = MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.DENY,
        reason_code="COMMAND_SUDO_DENIED",
        explanation="simulated",
    ))

    with pytest.raises(PolicyDeniedError):
        validator._run_cmd_with_timeout(["pip", "--version"], cwd=str(tmp_path), timeout=30)


def test_audit_call_observes_the_real_command_shape(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    captured = {}
    real_evaluate = validator.execution_policy.evaluate

    def spy(request):
        captured["command"] = request.command
        return real_evaluate(request)

    validator.execution_policy.evaluate = spy
    validator._run_cmd_with_timeout(["pytest", "-x"], cwd=str(tmp_path), timeout=30)
    assert captured["command"] == ("pytest", "-x")
