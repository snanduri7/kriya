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


def test_workflow_controller_shadow_mode_enabled_by_default():
    """Locks in the deliberate 2026-08-24 default flip: workflow_controller.
    enabled is now True (mode stays "shadow") out of the box - was False,
    meaning MA5/6's ControlState/ContractRegistry/ArtifactRegistry/
    ContextOrchestrator/DecisionLedger machinery was fully unreachable in
    any real run (MA7.0's "INERT" finding). Shadow mode is provably
    non-mutating (hard-stops on any TOOL-tagged subtask, wrapped in a broad
    try/except - see kriya/workflow/workflow_controller.py), so this is
    zero-risk to real generation output; mode stays "shadow", not "enforce"
    - enforce is deliberately still opt-in, validated live on only one
    project/goal shape so far."""
    assert AppConfig().workflow_controller.enabled is True
    assert AppConfig().workflow_controller.mode == "shadow"
    assert load_config().workflow_controller.enabled is True
    assert load_config().workflow_controller.mode == "shadow"


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
