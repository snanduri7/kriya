"""PRD-010 real local-model fingerprint and qualification evidence."""
import os

import pytest

from kriya.config import AppConfig
from kriya.core.model_capabilities import resolve_model_capability_profile
from kriya.production_doctor import probe_llm_runtime


@pytest.mark.live_model
def test_production_doctor_recognizes_real_runtime_fingerprint_and_qualification():
    cfg = AppConfig()
    cfg.llm.base_url = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
    cfg.llm.model = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
    cfg.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")

    runtime = probe_llm_runtime(cfg)
    qualification = resolve_model_capability_profile(cfg, cfg.llm.model)

    assert runtime["selected_model"] is not None
    assert runtime["native_metadata"]
    assert runtime["fingerprint"] and len(runtime["fingerprint"]) == 64
    assert qualification.source == "known_production_profile"
