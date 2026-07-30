import yaml

from kriya.config.config import AppConfig, load_config


def test_load_config_with_extra_body(tmp_path):
    custom_yaml = {
        "llm": {
            "provider": "openai",
            "model": "qwen3-coder:30b",
            "temperature": 0.7,
            "extra_body": {
                "options": {
                    "num_ctx": 32768,
                    "top_p": 0.8,
                    "top_k": 20
                }
            }
        }
    }
    
    cfg_file = tmp_path / "custom_config.yaml"
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(custom_yaml, f)
        
    cfg = load_config(str(cfg_file))
    assert isinstance(cfg, AppConfig)
    assert cfg.llm.model == "qwen3-coder:30b"
    assert cfg.llm.temperature == 0.7
    assert cfg.llm.extra_body == {
        "options": {
            "num_ctx": 32768,
            "top_p": 0.8,
            "top_k": 20
        }
    }
