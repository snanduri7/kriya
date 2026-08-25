import os

import pytest
import yaml

from kriya.config.config import AppConfig, load_config


def test_load_default_config():
    # Load with no path, should load default_config.yaml automatically
    cfg = load_config()
    assert isinstance(cfg, AppConfig)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.base_url == "http://localhost:11434/v1"
    
    # Read default_config.yaml dynamically as a reference to check
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref_path = os.path.join(base_dir, "kriya", "config", "default_config.yaml")
    if os.path.exists(ref_path):
        with open(ref_path, "r", encoding="utf-8") as f:
            ref_data = yaml.safe_load(f) or {}
        expected_model = ref_data.get("llm", {}).get("model")
        if expected_model:
            assert cfg.llm.model == expected_model
            return
            
    assert isinstance(cfg.llm.model, str) and len(cfg.llm.model) > 0

def test_routing_enabled_by_default():
    """Locks in the deliberate 2026-08-02 default flip: routing.enabled is now
    True out of the box (was False). Explicit commands are unaffected either
    way - routing only activates when a typed REPL line's first word doesn't
    already match a real command name (kriya/repl.py::_route_line)."""
    assert AppConfig().routing.enabled is True
    assert load_config().routing.enabled is True


def test_workflow_controller_stays_disabled_by_default():
    """TRIED AND REVERTED same day (2026-08-24): enabled=true was briefly
    the default, reasoned as zero-risk since shadow mode never affects real
    generation output. That missed a real regression - shadow mode makes
    its own real Planner/Architect/SubtaskExecutor LLM calls through the
    real WorkflowEngine, independent of run_generation_workflow(). A common
    test pattern in this suite (tests/test_file_goal.py and others) mocks
    ONLY WorkflowEngine.run_generation_workflow, not Kernel/LLMClient -
    previously sufficient to guarantee zero real network calls end to end.
    With workflow_controller.enabled=true by default, that same pattern let
    shadow's own agent calls silently reach a real, unmocked LLMClient,
    hanging/failing against a live endpoint the test never expected to hit
    (confirmed live: tests/test_cli_smoke.py and tests/test_file_goal.py
    both broke/hung). Reverted before ever reaching origin. This test locks
    the safe default back in - see kriya/config/config.py::
    WorkflowControllerConfig's own docstring for the full account before
    trying this again; it needs a real test-suite audit first (every
    run_generation_workflow-only mock site), not a casual re-flip."""
    assert AppConfig().workflow_controller.enabled is False
    assert AppConfig().workflow_controller.mode == "shadow"
    assert load_config().workflow_controller.enabled is False
    assert load_config().workflow_controller.mode == "shadow"


def test_runtime_profile_defaults_to_none_and_changes_nothing():
    """runtime_profile (2026-08-25, external review P2) - the default
    (unset) must leave every underlying field exactly as it already
    behaves, matching every existing kriya.yaml unchanged."""
    assert AppConfig().runtime_profile is None
    cfg = load_config()
    assert cfg.runtime_profile is None
    assert cfg.workflow_controller.enabled is False
    assert cfg.engineering_triage.shadow_mode is True
    assert cfg.process_profiles.enabled is False


def test_runtime_profile_hardened_overrides_the_documented_fields(tmp_path):
    config_file = tmp_path / "kriya.yaml"
    with open(config_file, "w") as f:
        yaml.dump({"runtime_profile": "hardened"}, f)

    cfg = load_config(str(config_file))

    assert cfg.workflow_controller.enabled is True
    assert cfg.workflow_controller.mode == "enforce"
    assert cfg.engineering_triage.shadow_mode is False
    assert cfg.process_profiles.enabled is True


def test_runtime_profile_hardened_does_not_touch_execution_policy_mode():
    """execution_policy.mode="enforce" has always been a distinct, separately
    authorized decision (its own validator hard-rejects it) - this preset
    does not silently reach around that restriction."""
    with pytest.raises(Exception):
        AppConfig(runtime_profile="hardened", execution_policy={"mode": "enforce"})


def test_runtime_profile_rejects_an_unknown_value():
    with pytest.raises(Exception):
        AppConfig(runtime_profile="turbo")


def test_runtime_profile_hardened_overrides_even_if_the_user_config_set_the_fields_differently(tmp_path):
    """A strict preset, not a partial merge - picking "hardened" wins over
    whatever the user's own kriya.yaml happened to also set for these
    specific fields, so there's never a question of which one applies."""
    config_file = tmp_path / "kriya.yaml"
    with open(config_file, "w") as f:
        yaml.dump({
            "runtime_profile": "hardened",
            "workflow_controller": {"enabled": False, "mode": "shadow"},
        }, f)

    cfg = load_config(str(config_file))

    assert cfg.workflow_controller.enabled is True
    assert cfg.workflow_controller.mode == "enforce"


def test_load_custom_config(tmp_path):
    custom_yaml = {
        "llm": {
            "provider": "openai",
            "model": "mistral-7b",
            "base_url": "http://localhost:8000/v1"
        },
        "logging": {
            "level": "DEBUG"
        }
    }
    
    config_file = tmp_path / "custom_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(custom_yaml, f)
        
    cfg = load_config(str(config_file))
    
    assert cfg.llm.model == "mistral-7b"
    assert cfg.llm.base_url == "http://localhost:8000/v1"
    assert cfg.logging.level == "DEBUG"
    # Fallback/default still applies to other items
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref_path = os.path.join(base_dir, "kriya", "config", "default_config.yaml")
    if os.path.exists(ref_path):
        with open(ref_path, "r", encoding="utf-8") as f:
            ref_data = yaml.safe_load(f) or {}
        expected_temp = ref_data.get("llm", {}).get("temperature")
        if expected_temp is not None:
            assert cfg.llm.temperature == expected_temp
            return
    assert isinstance(cfg.llm.temperature, float)
    assert cfg.paths.skills.endswith("skills")

def test_load_invalid_config(tmp_path):
    invalid_yaml = {
        "llm": {
            "temperature": "not-a-float"
        }
    }
    
    config_file = tmp_path / "invalid_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(invalid_yaml, f)
        
    with pytest.raises(Exception):
        load_config(str(config_file))
