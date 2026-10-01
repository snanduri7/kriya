"""The qualified live-model identity preflight shared by the Wave 7 live
tiers (PRD-032 chaos, PRD-033 metrics, PRD-035 certification).

A live case must run as the exact production identity it claims: a real
local endpoint, an exact runtime fingerprint, a QUALIFIED Developer and the
expected served context window. Anything else is a FAILURE reported as
UNAVAILABLE, never a skip. Every trusted store is isolated inside the test's
tmp_path, with a sentinel, so a tree snapshot sees any write to one.

PRD-036 (``KRIYA_RELEASE_CONFIG``): the primary and fallback model bindings
(model, endpoint, sampling, provider options, output budget, capabilities)
come from the operator production configuration, so the identity the live
matrix certifies is exactly the one released. Only the bindings are taken;
the cases keep their own workflow settings.
"""
import os
from typing import Any, Dict, Optional, Tuple

import pytest
import yaml

from kriya.config.config import AppConfig, FallbackModelConfig, LLMConfig, load_config
from kriya.core import model_qualification as mq
from kriya.core.inference_settings import role_inference_settings
from kriya.core.model_runtime import clear_model_runtime_cache, resolve_configured_model_runtime

BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
PRIMARY = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
EXPECTED_CONTEXT_WINDOW = int(os.environ.get("KRIYA_LIVE_CONTEXT_WINDOW", "32768"))
RELEASE_CONFIG = os.environ.get("KRIYA_RELEASE_CONFIG")
TRUSTED_STORES = (("KRIYA_STATIC_ANALYSIS_HOME", "waivers"), ("KRIYA_AUTHORITY_HOME", "authority"),
                  ("KRIYA_MCP_APPROVAL_HOME", "mcp"), ("KRIYA_ADJUDICATION_HOME", "adjudications"))


def _release_document() -> Dict[str, Any]:
    with open(RELEASE_CONFIG, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def release_primary_binding(packaged: LLMConfig) -> Optional[LLMConfig]:
    """The release configuration's primary binding over the packaged one
    (one level deep, exactly as load_config merges), or None without one."""
    return LLMConfig(**{**packaged.model_dump(), **_release_document()["llm"]}) if RELEASE_CONFIG else None


def release_fallback_binding() -> Optional[FallbackModelConfig]:
    """The release configuration's first Developer fallback, or None without one."""
    if not RELEASE_CONFIG:
        return None
    chain = _release_document().get("llm_chain") or []
    return FallbackModelConfig(**chain[0]) if chain else None


def qualified_live_config(tmp_path: Any, monkeypatch: Any, *, model: str = PRIMARY) -> Tuple[AppConfig, Dict[str, str]]:
    for variable, name in TRUSTED_STORES:
        store = tmp_path / "trusted" / name
        store.mkdir(parents=True, exist_ok=True)
        (store / "sentinel.json").write_text('{"owner": "operator"}\n')
        monkeypatch.setenv(variable, str(store))
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    release = release_primary_binding(config.llm)
    if release is not None:
        config.llm = release
        model = release.model
    else:
        config.llm.base_url = BASE_URL
        config.llm.model = model
        config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
    config.llm_chain = []
    config.autonomy.mode = "guardrails"
    config.autonomy.run_verification_enabled = False
    config.paths.skills = str(tmp_path / "skills")
    clear_model_runtime_cache()
    try:
        fingerprint = resolve_configured_model_runtime(config, model, fresh=True)
    except Exception as error:  # noqa: BLE001 - reported as UNAVAILABLE, never skipped
        pytest.fail(f"UNAVAILABLE: the live endpoint {config.llm.base_url} could not be identified: {error}", pytrace=False)
    assessment = mq.assess(fingerprint, mq.required_capabilities(config, "developer", model),
                           settings=role_inference_settings(config, "developer", model))
    problems = []
    if fingerprint.effective_context_window != EXPECTED_CONTEXT_WINDOW:
        problems.append(f"effective context window {fingerprint.effective_context_window} != {EXPECTED_CONTEXT_WINDOW}")
    if assessment.status != mq.QUALIFIED:
        problems.append(f"developer qualification is {assessment.status}")
    if not fingerprint.exact:
        problems.append("the runtime fingerprint is not exact")
    if problems:
        pytest.fail("UNAVAILABLE (live identity): " + "; ".join(problems)
                    + f". Qualify it first: kriya model qualify --model {model}", pytrace=False)
    settings = role_inference_settings(config, "developer", model)
    return config, {"model": model, "runtime_fingerprint": fingerprint.digest, "runtime_exact": str(fingerprint.exact),
                    "qualification": assessment.status, "inference_settings_digest": settings.digest}
