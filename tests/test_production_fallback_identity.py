"""PROD-FALLBACK-QUAL-001: the demo-03 production Developer fallback must
resolve to the qwen3.6 inference identity that qualified 18/18 under policy
/3 (settings digest sha256:0f1e6b5c...), sending a 32768 window.

The fallback entry below is the one in demo-03 `config/generate-production.yaml`
(outside this repository). Its settings with temperature 0.2 and an empty
extra_body were NOT_QUALIFIED: silent reasoning truncated 6 required cases.

PROVIDER-CONTRACT-001 migrated the identity definition (inference settings
/2 = what the /v1 wire carries): the /1 digest the 18/18 record was keyed by
is superseded, so that record is STALE and the fallback is requalified under
the new identity. options.top_k (never applied by /v1) left the request
identity; the served top_k is part of the runtime fingerprint.
"""
from kriya.config import AppConfig
from kriya.core.inference_settings import role_inference_settings
from kriya.core.model_runtime import configured_context_window

QWEN36 = "qwen3.6:35b-a3b-q4_K_M"
# Inference settings /1 (superseded; its qualification record is STALE).
SUPERSEDED_SETTINGS_DIGEST = "sha256:0f1e6b5c1fecf04fb5404f86e02095371a5242250ad26c2b26eb7bf094a1b85c"
# Inference settings /2: reasoning_effort "none" and top_p 0.8 on the wire.
PROVEN_SETTINGS_DIGEST = "sha256:95b9402f0acd4ec0f2785e1dcefa95c26447d20cd951242cc1ddc327feec8577"
PRODUCTION_FALLBACK = {
    "model": QWEN36,
    "base_url": "http://localhost:11434/v1",
    "api_key": "local-key",
    "context_window": 32768,
    "temperature": 0.7,
    "extra_body": {"options": {"num_ctx": 32768, "top_p": 0.8, "top_k": 20}, "reasoning_effort": "none"},
}


def test_the_production_fallback_resolves_to_the_proven_qwen36_identity():
    config = AppConfig(llm_chain=[PRODUCTION_FALLBACK])
    binding = config.llm_chain[0]
    settings = role_inference_settings(config, "developer", QWEN36)
    assert settings.digest == PROVEN_SETTINGS_DIGEST != SUPERSEDED_SETTINGS_DIGEST
    assert settings.temperature == 0.7
    # One reasoning control: the provider's reasoning_effort. Kriya's own
    # reasoning flag (a budget hint only) stays at its default.
    assert settings.reasoning is False and binding.reasoning is False
    # The settings are what /v1 applies: the requested window is runtime
    # identity (budgeted, never sent), top_k is the served model's own.
    assert settings.extra_body == {"top_p": 0.8, "reasoning_effort": "none"}
    assert configured_context_window(binding.extra_body) == 32768 == binding.context_window


def test_the_previous_fallback_settings_are_a_different_identity():
    previous = {**PRODUCTION_FALLBACK, "temperature": 0.2, "reasoning": False, "extra_body": {}}
    config = AppConfig(llm_chain=[previous])
    settings = role_inference_settings(config, "developer", QWEN36)
    assert settings.digest == "sha256:b1012062431b36c56406ae301868e9b6391edb2dc91ffa5358d79ccfbb837338"
    assert settings.digest != PROVEN_SETTINGS_DIGEST
    # reasoning=false is now sent as reasoning_effort "none": the silent
    # reasoning that failed these settings can no longer happen.
    assert settings.extra_body == {"reasoning_effort": "none"}
    assert configured_context_window(config.llm_chain[0].extra_body) is None
