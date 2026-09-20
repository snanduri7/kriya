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


def test_runtime_profile_hardened_overrides_the_documented_fields():
    # SEC-009 P1: runtime_profile is PLATFORM_POLICY - a repository-sourced
    # kriya.yaml can no longer set it through load_config() at all (see
    # tests/test_sec009_config_authority.py for that denial coverage, which
    # this test previously exercised unintentionally via a tmp_path config
    # file). This test's actual subject - what the "hardened" preset expands
    # to - is exercised directly via the extracted mapping function
    # load_config() itself calls, independent of the authority gate.
    from kriya.config.config import runtime_profile_preset_fields

    fields = runtime_profile_preset_fields("hardened")

    assert fields[("workflow_controller", "enabled")] is True
    assert fields[("workflow_controller", "mode")] == "enforce"
    assert fields[("engineering_triage", "shadow_mode")] is False
    assert fields[("process_profiles", "enabled")] is True


def test_runtime_profile_hardened_does_not_touch_execution_policy_mode():
    """execution_policy.mode is a distinct, separately authorized decision
    (POL-001-P2, 2026-09-10) - the "hardened" preset does not silently
    reach around or override whatever a project explicitly set it to,
    in either direction."""
    cfg_audit = AppConfig(runtime_profile="hardened")
    assert cfg_audit.execution_policy.mode == "audit"

    cfg_enforce = AppConfig(runtime_profile="hardened", execution_policy={"mode": "enforce"})
    assert cfg_enforce.execution_policy.mode == "enforce"


def test_runtime_profile_rejects_an_unknown_value():
    with pytest.raises(Exception):
        AppConfig(runtime_profile="turbo")


def test_runtime_profile_rejects_conflicting_individual_settings(tmp_path):
    config_file = tmp_path / "kriya.yaml"
    with open(config_file, "w") as f:
        yaml.dump({
            "runtime_profile": "hardened",
            "workflow_controller": {"enabled": False, "mode": "shadow"},
        }, f)

    with pytest.raises(ValueError, match="cannot be combined"):
        load_config(str(config_file))


@pytest.mark.parametrize("profile", ["legacy", "validated", "hardened"])
def test_runtime_profile_accepts_supported_presets(profile):
    assert AppConfig(runtime_profile=profile).runtime_profile == profile


def test_load_custom_config(tmp_path):
    # SEC-009 P1: llm.base_url is SECURITY_AUTHORITY (it redirects where
    # Kriya sends requests) and this config_file lives outside the test's
    # CWD/workspace, so it is no longer authorized to set it - see
    # tests/test_sec009_config_authority.py for the dedicated coverage of
    # that denial. This test keeps only the REPOSITORY_SAFE fields it was
    # actually exercising (model name, log level).
    custom_yaml = {
        "llm": {
            "provider": "openai",
            "model": "mistral-7b",
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
