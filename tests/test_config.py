import os
import pytest
import yaml
from kriya.config.config import load_config, AppConfig

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
    assert cfg.llm.temperature == 0.2
    assert cfg.paths.skills == "./skills"

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
