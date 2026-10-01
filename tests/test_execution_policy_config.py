"""MA4.15 / POL-001-P2 (2026-09-10): ExecutionPolicyConfig (kriya/config/
config.py) - the AUDIT vs ENFORCE mode switch. "enforce" was rejected at
validation time pending an explicit, confirmed-with-the-user rollout
decision (see kriya/config/config.py::ExecutionPolicyConfig's own
docstring and docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md's POL-001
entry) - that decision has now been made, so "enforce" is a real, selectable
value. The default stays "audit" - no existing project's config is silently
migrated to enforce by this change."""

import pytest
from pydantic import ValidationError

from kriya.config import AppConfig
from kriya.config.config import ExecutionPolicyConfig


def test_defaults_match_todays_already_live_behavior():
    cfg = ExecutionPolicyConfig()
    assert cfg.enabled is True
    assert cfg.mode == "audit"


def test_mode_audit_is_accepted():
    cfg = ExecutionPolicyConfig(mode="audit")
    assert cfg.mode == "audit"


def test_mode_enforce_is_now_accepted():
    cfg = ExecutionPolicyConfig(mode="enforce")
    assert cfg.mode == "enforce"


def test_mode_garbage_value_is_rejected():
    with pytest.raises(ValidationError):
        ExecutionPolicyConfig(mode="sometimes")


def test_enabled_can_be_turned_off():
    cfg = ExecutionPolicyConfig(enabled=False)
    assert cfg.enabled is False


def test_app_config_carries_execution_policy_with_safe_defaults():
    cfg = AppConfig()
    assert cfg.execution_policy.enabled is True
    assert cfg.execution_policy.mode == "audit"


def test_app_config_accepts_enforce_mode_nested():
    cfg = AppConfig(execution_policy={"mode": "enforce"})
    assert cfg.execution_policy.mode == "enforce"


def test_app_config_still_rejects_garbage_mode_nested():
    with pytest.raises(ValidationError):
        AppConfig(execution_policy={"mode": "sometimes"})
