from kriya.config import AppConfig, ModelCapabilities
from kriya.core.model_capabilities import capabilities_for_model, validate_tool_call_sample


def test_offline_conformance_rejects_oversized_or_malformed_tool_arguments():
    capabilities = ModelCapabilities(max_tool_argument_chars=256)

    oversized = validate_tool_call_sample(
        '{"content": "' + ("x" * 300) + '"}', capabilities,
    )
    malformed = validate_tool_call_sample("<apply_patch>", capabilities)

    assert not oversized.compatible
    assert any("safe limit" in violation for violation in oversized.violations)
    assert not malformed.compatible
    assert any("valid JSON" in violation for violation in malformed.violations)


def test_primary_model_capabilities_are_explicit_and_local_configured():
    cfg = AppConfig()
    cfg.llm.capabilities.native_tool_calls = False

    resolved = capabilities_for_model(cfg, cfg.llm.model)

    assert not resolved.native_tool_calls
    assert resolved.preferred_edit_protocol == "small_native_tools"
