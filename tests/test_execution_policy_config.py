"""MA4.15: ExecutionPolicyConfig (kriya/config/config.py) - the AUDIT vs
ENFORCE mode switch, and the fail-loud-not-silent guarantee that "enforce"
can't be quietly set today."""

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


def test_mode_enforce_is_rejected_at_validation_time():
    with pytest.raises(ValidationError) as exc_info:
        ExecutionPolicyConfig(mode="enforce")
    assert "enforce" in str(exc_info.value).lower()


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


def test_app_config_rejects_enforce_mode_nested():
    with pytest.raises(ValidationError):
        AppConfig(execution_policy={"mode": "enforce"})
