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
EXPECTED_ADAPTER = os.environ.get("KRIYA_LIVE_ADAPTER", "kriya-ollama-native/3")
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


def bind_primary(config: AppConfig, *, model: str = PRIMARY) -> str:
    """Bind ``config`` to the live primary: the operator release configuration's binding when ``KRIYA_RELEASE_CONFIG`` names one, else the
    environment's endpoint and model. Nothing else about the configuration changes. Returns the model name."""
    release = release_primary_binding(config.llm)
    if release is not None:
        config.llm = release
        model = release.model
    else:
        config.llm.base_url = BASE_URL
        config.llm.model = model
        config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
    config.llm_chain = []
    return model


def identity_problems(fingerprint: Any, assessment: Any, *, expected_window: int = EXPECTED_CONTEXT_WINDOW,
                      expected_adapter: str = EXPECTED_ADAPTER) -> list:
    """Why the resolved Developer identity is not the qualified production identity ([] when it is)."""
    problems = []
    if fingerprint.effective_context_window != expected_window:
        problems.append(f"effective context window {fingerprint.effective_context_window} != {expected_window}")
    if assessment.status != mq.QUALIFIED:
        problems.append(f"developer qualification is {assessment.status}")
    if not fingerprint.exact:
        problems.append("the runtime fingerprint is not exact")
    if fingerprint.adapter_version != expected_adapter:
        problems.append(f"runtime adapter {fingerprint.adapter_version!r} != {expected_adapter!r}")
    return problems


def verify_identity(config: AppConfig, model: str) -> Dict[str, str]:
    """The fail-closed live-identity preflight: the exact runtime fingerprint, a QUALIFIED Developer, the expected adapter and served context. A
    failure is UNAVAILABLE (a fixture/configuration error), never a skip and never a product result. Returns the identity to persist as evidence."""
    clear_model_runtime_cache()
    try:
        fingerprint = resolve_configured_model_runtime(config, model, fresh=True)
    except Exception as error:  # noqa: BLE001 - reported as UNAVAILABLE, never skipped
        pytest.fail(f"UNAVAILABLE: the live endpoint {config.llm.base_url} could not be identified: {error}", pytrace=False)
    settings = role_inference_settings(config, "developer", model)
    assessment = mq.assess(fingerprint, mq.required_capabilities(config, "developer", model), settings=settings)
    problems = identity_problems(fingerprint, assessment)
    if problems:
        pytest.fail("UNAVAILABLE (live identity): " + "; ".join(problems) + f". Qualify it first: kriya model qualify --model {model}", pytrace=False)
    return {"model": model, "runtime_fingerprint": fingerprint.digest, "runtime_exact": str(fingerprint.exact),
            "adapter": fingerprint.adapter_version, "effective_context_window": str(fingerprint.effective_context_window),
            "qualification": assessment.status, "inference_settings_digest": settings.digest}


def qualified_live_config(tmp_path: Any, monkeypatch: Any, *, model: str = PRIMARY) -> Tuple[AppConfig, Dict[str, str]]:
    for variable, name in TRUSTED_STORES:
        store = tmp_path / "trusted" / name
        store.mkdir(parents=True, exist_ok=True)
        (store / "sentinel.json").write_text('{"owner": "operator"}\n')
        monkeypatch.setenv(variable, str(store))
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    model = bind_primary(config, model=model)
    config.autonomy.mode = "guardrails"
    config.autonomy.run_verification_enabled = False
    config.paths.skills = str(tmp_path / "skills")
    return config, verify_identity(config, model)
