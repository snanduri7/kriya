import pytest

from kriya.config import AppConfig, FallbackModelConfig, LLMConfig, ModelCapabilities
from kriya.core.model_capabilities import (
    KNOWN_MODEL_PROFILES,
    UNVERIFIED_MODEL_CONSERVATIVE_PROFILE,
    ResolvedCapabilityProfile,
    capabilities_for_model,
    generation_protocol_for_model,
    resolve_model_capability_profile,
    validate_tool_call_sample,
)

# MODEL-001 P1 (2026-09-17) evidence: exact values live-measured by
# spikes/model001_campaign/probe_capabilities.py, recorded in
# spikes/model001_campaign/capability_profiles.json and
# docs/assurance/MODEL_001_CAMPAIGN.md. Redeclared here (not imported from
# spikes/) so this test suite fails loudly if KNOWN_MODEL_PROFILES ever
# silently drifts from the evidence that justified it - production code
# must never depend on spikes/, but this test's OWN expectation is allowed
# to cite the same evidence independently.
_M1 = "qwen3-coder:30b"
_M2 = "qwen3.6:35b-a3b-q4_K_M"
_M3 = "qwen3.5:9B"
_CAMPAIGN_EVIDENCED_CAPABILITIES = dict(
    native_tool_calls=True, json_mode=True, reliable_multiline_json=True,
    streaming=True, preferred_edit_protocol="small_native_tools",
)


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


def test_generation_protocol_uses_active_fallback_model_profile():
    from kriya.config import FallbackModelConfig

    cfg = AppConfig()
    cfg.llm_chain = [FallbackModelConfig(
        model="local-full-file",
        capabilities=ModelCapabilities(
            json_mode=False,
            reliable_multiline_json=False,
            streaming=False,
            preferred_edit_protocol="full_file",
        ),
    )]

    protocol = generation_protocol_for_model(cfg, "local-full-file")

    assert protocol.json_mode is False
    assert protocol.streaming is False
    assert protocol.preferred_edit_protocol == "full_file"


# =====================================================================
# MODEL-001 P1 - production capability contract
# =====================================================================

def _assert_matches_campaign_evidence(capabilities: ModelCapabilities) -> None:
    for field_name, expected in _CAMPAIGN_EVIDENCED_CAPABILITIES.items():
        assert getattr(capabilities, field_name) == expected, field_name


# --- 1/2/3: known M1/M2/M3 profile resolution (untouched primary binding) ---

def test_known_m1_profile_resolves_via_untouched_primary_binding():
    cfg = AppConfig()
    cfg.llm.model = _M1  # capabilities left as bare defaults - never touched

    result = resolve_model_capability_profile(cfg, _M1)

    assert result.source == "known_production_profile"
    _assert_matches_campaign_evidence(result.capabilities)


def test_known_m2_profile_resolves_via_untouched_llm_chain_binding():
    cfg = AppConfig()
    cfg.llm_chain = [FallbackModelConfig(model=_M2)]  # capabilities untouched

    result = resolve_model_capability_profile(cfg, _M2)

    assert result.source == "known_production_profile"
    _assert_matches_campaign_evidence(result.capabilities)


def test_known_m3_profile_resolves_via_untouched_agent_llms_binding():
    cfg = AppConfig()
    cfg.agent_llms.reviewer.llm = LLMConfig(model=_M3)  # capabilities untouched

    result = resolve_model_capability_profile(cfg, _M3)

    assert result.source == "known_production_profile"
    _assert_matches_campaign_evidence(result.capabilities)


def test_known_profiles_registry_has_exactly_the_three_evidenced_models():
    assert set(KNOWN_MODEL_PROFILES.keys()) == {
        "qwen3-coder:30b", "qwen3.6:35b-a3b-q4_k_m", "qwen3.5:9b",
    }


# --- 4: unknown model does not inherit M1/M2/M3 ---

def test_unknown_model_with_no_binding_gets_conservative_profile_not_m1():
    cfg = AppConfig()
    cfg.llm.model = _M1  # a real, known binding exists ... for a DIFFERENT model

    result = resolve_model_capability_profile(cfg, "totally-unconfigured-model:1b")

    assert result.source == "unverified_conservative_default"
    assert result.capabilities == UNVERIFIED_MODEL_CONSERVATIVE_PROFILE
    assert result.capabilities.native_tool_calls is False
    assert result.capabilities != cfg.llm.capabilities or cfg.llm.capabilities == ModelCapabilities()


def test_unknown_model_fails_closed_on_tool_calls_and_prefers_full_file_edit():
    cfg = AppConfig()
    result = resolve_model_capability_profile(cfg, "brand-new-local-model:7b")

    assert result.capabilities.native_tool_calls is False
    assert result.capabilities.json_mode is False
    assert result.capabilities.preferred_edit_protocol == "full_file"


# --- required closure check: explicit-default override provenance ---

def test_explicit_capability_value_equal_to_default_still_counts_as_explicit():
    """The exact required invariant: a user who explicitly configures a
    capability value that happens to equal ModelCapabilities' own class
    default must still be treated as an explicit override, not silently
    folded into "untouched, use the known/conservative profile instead."
    Uses the REAL config-loading shape (a raw dict matching what
    load_config() actually constructs AppConfig from - see
    test_config_loading_path_preserves_resolution_contract's own docstring
    for why this shape, not a bare AppConfig() + attribute mutation, is the
    one that matters) so this is proof against the real mechanism
    (pydantic's model_fields_set), not an assumption.

    "unexplained-model:1b" is chosen deliberately NOT in KNOWN_MODEL_PROFILES:
    if explicitness were (incorrectly) detected by value-equality against
    the bare class default, native_tool_calls=True here would be
    indistinguishable from "never touched," and resolution would
    incorrectly report source=unverified_conservative_default with
    native_tool_calls=False instead of honoring the user's real, explicit
    True."""
    # A: absent/untouched - no capabilities key in the user's own config at all.
    absent = AppConfig(**{"llm": {"model": "unexplained-model:1b"}}, )
    resolved_absent = resolve_model_capability_profile(absent, "unexplained-model:1b")

    # B: explicitly supplied, value equal to the class default (native_tool_calls
    # defaults to True already - this is the exact edge case).
    explicit_default = AppConfig(**{
        "llm": {"model": "unexplained-model:1b", "capabilities": {"native_tool_calls": True}},
    })
    resolved_explicit = resolve_model_capability_profile(explicit_default, "unexplained-model:1b")

    assert resolved_absent.source == "unverified_conservative_default"
    assert resolved_absent.capabilities.native_tool_calls is False

    assert resolved_explicit.source == "explicit_primary"
    assert resolved_explicit.capabilities.native_tool_calls is True
    # The rest of the explicit binding's fields fall back to ModelCapabilities'
    # own remaining class defaults, per pydantic's ordinary field-default
    # behavior for a partially-specified object - only native_tool_calls was
    # actually written by the user, so only it is asserted here.

    # The two ARE value-equal on native_tool_calls specifically, proving this
    # genuinely is the "equal to default" edge case, not a different value
    # that a weaker check would have caught anyway.
    assert resolved_absent.capabilities.native_tool_calls != resolved_explicit.capabilities.native_tool_calls


def test_capabilities_model_fields_set_is_the_real_provenance_mechanism():
    """Direct, minimal proof of the underlying pydantic mechanism this
    package's explicitness check now relies on (kriya/core/
    model_capabilities.py::_resolve_for_binding) - kept alongside the
    higher-level resolution test above so a future reader can see the raw
    mechanism in isolation, not just its effect through resolution."""
    untouched = AppConfig(**{"llm": {"model": "x"}}).llm.capabilities
    explicit_same_value = AppConfig(**{
        "llm": {"model": "x", "capabilities": {"native_tool_calls": True}},
    }).llm.capabilities

    assert untouched == explicit_same_value  # value-equal
    assert untouched.model_fields_set == set()
    assert explicit_same_value.model_fields_set == {"native_tool_calls"}  # but provenance differs


# --- 5/6: explicit valid override / invalid override ---

def test_explicit_valid_override_wins_over_known_registry_entry():
    cfg = AppConfig()
    cfg.llm.model = _M1
    cfg.llm.capabilities.native_tool_calls = False  # explicit, deliberately diverges from evidence

    result = resolve_model_capability_profile(cfg, _M1)

    assert result.source == "explicit_primary"
    assert result.capabilities.native_tool_calls is False


def test_invalid_capability_override_rejected_by_type_validation():
    with pytest.raises(Exception):
        ModelCapabilities(max_tool_argument_chars="not-a-number")

    with pytest.raises(Exception):
        ModelCapabilities(max_tool_argument_chars=-1)  # ge=256 constraint


# --- 7: deterministic precedence, exercised end to end ---

def test_precedence_explicit_beats_known_beats_conservative():
    cfg = AppConfig()
    # known: matches KNOWN_MODEL_PROFILES via untouched llm_chain binding
    cfg.llm_chain = [FallbackModelConfig(model=_M2)]
    # explicit: primary model IS a known model, but its capabilities are touched
    # (native_tool_calls's own class default is True - False is a real, provable
    # divergence, unlike reassigning a field to its own default value would be)
    cfg.llm.model = _M1
    cfg.llm.capabilities.native_tool_calls = False

    explicit = resolve_model_capability_profile(cfg, _M1)
    known = resolve_model_capability_profile(cfg, _M2)
    conservative = resolve_model_capability_profile(cfg, "never-configured:1b")

    assert explicit.source == "explicit_primary"
    assert explicit.capabilities.native_tool_calls is False
    assert known.source == "known_production_profile"
    assert conservative.source == "unverified_conservative_default"


# --- 8/9: alias/tag normalization, near-match must NOT bind ---

def test_case_insensitive_tag_normalization_matches_same_model():
    cfg = AppConfig()
    cfg.llm.model = "qwen3.5:9B"

    result = resolve_model_capability_profile(cfg, "qwen3.5:9b")  # different case only

    assert result.source == "known_production_profile"
    _assert_matches_campaign_evidence(result.capabilities)


def test_near_match_model_name_does_not_receive_wrong_profile():
    cfg = AppConfig()
    cfg.llm.model = _M3  # qwen3.5:9B, a known model

    near_miss = resolve_model_capability_profile(cfg, "qwen3.5:9b-mlx")
    also_near_miss = resolve_model_capability_profile(cfg, "qwen3.5:35b-a3b")

    assert near_miss.source == "unverified_conservative_default"
    assert near_miss.capabilities.native_tool_calls is False
    assert also_near_miss.source == "unverified_conservative_default"


# --- 10/11/12/13: primary / llm_chain / agent_llms all resolve distinctly ---

def test_primary_model_gets_its_own_profile():
    cfg = AppConfig()
    cfg.llm.model = "primary-model:1b"
    cfg.llm.capabilities.preferred_edit_protocol = "full_file"

    result = resolve_model_capability_profile(cfg, "primary-model:1b")

    assert result.source == "explicit_primary"
    assert result.capabilities.preferred_edit_protocol == "full_file"


def test_llm_chain_model_gets_its_own_profile_independent_of_primary():
    cfg = AppConfig()
    cfg.llm.model = "primary-model:1b"  # unconfigured, unknown - resolves conservative
    cfg.llm_chain = [FallbackModelConfig(
        model="fallback-model:1b",
        capabilities=ModelCapabilities(reliable_multiline_json=True, preferred_edit_protocol="full_file"),
    )]

    primary = resolve_model_capability_profile(cfg, "primary-model:1b")
    fallback = resolve_model_capability_profile(cfg, "fallback-model:1b")

    assert primary.source == "unverified_conservative_default"
    assert primary.capabilities.native_tool_calls is False
    assert fallback.source == "explicit_llm_chain"
    assert fallback.capabilities.reliable_multiline_json is True
    assert fallback.capabilities.native_tool_calls is True  # this binding's own explicit-object default


def test_agent_llms_role_model_gets_its_own_profile_independent_of_primary():
    cfg = AppConfig()
    cfg.llm.model = "primary-model:1b"
    cfg.agent_llms.reviewer.llm = LLMConfig(
        model="reviewer-only-model:1b",
        capabilities=ModelCapabilities(json_mode=False),
    )

    primary = resolve_model_capability_profile(cfg, "primary-model:1b")
    reviewer = resolve_model_capability_profile(cfg, "reviewer-only-model:1b")

    assert primary.source == "unverified_conservative_default"  # unconfigured, unknown model
    assert reviewer.source == "explicit_agent_llms"
    assert reviewer.capabilities.json_mode is False


def test_different_models_in_one_llm_chain_retain_distinct_profiles():
    cfg = AppConfig()
    cfg.llm_chain = [
        FallbackModelConfig(model="chain-a:1b", capabilities=ModelCapabilities(preferred_edit_protocol="full_file")),
        FallbackModelConfig(model="chain-b:1b", capabilities=ModelCapabilities(preferred_edit_protocol="small_native_tools", native_tool_calls=False)),
    ]

    a = resolve_model_capability_profile(cfg, "chain-a:1b")
    b = resolve_model_capability_profile(cfg, "chain-b:1b")

    assert a.capabilities.preferred_edit_protocol == "full_file"
    assert b.capabilities.preferred_edit_protocol == "small_native_tools"
    assert b.capabilities.native_tool_calls is False
    assert a.capabilities.native_tool_calls is True


# --- 14/15/16/17: consumers use the effective profile ---

def test_tool_call_consumer_uses_effective_profile_native_tool_calls():
    cfg = AppConfig()
    cfg.llm.model = "no-tools-model:1b"
    cfg.llm.capabilities.native_tool_calls = False

    resolved = capabilities_for_model(cfg, "no-tools-model:1b")
    sample = validate_tool_call_sample('{"x": 1}', resolved)

    assert not sample.compatible
    assert any("native tool calls are disabled" in v for v in sample.violations)


def test_json_structured_output_consumer_uses_effective_profile():
    cfg = AppConfig()
    protocol = generation_protocol_for_model(cfg, "brand-new-model:1b")

    assert protocol.json_mode is False  # conservative default, not the bare-default True


def test_edit_protocol_consumer_uses_effective_profile():
    cfg = AppConfig()
    protocol_unknown = generation_protocol_for_model(cfg, "brand-new-model:1b")
    protocol_known = generation_protocol_for_model(cfg, _M1.upper())  # case-normalized

    assert protocol_unknown.preferred_edit_protocol == "full_file"
    assert protocol_known.preferred_edit_protocol == "small_native_tools"


def test_missing_required_capability_fails_safely_not_silently():
    cfg = AppConfig()
    resolved = capabilities_for_model(cfg, "never-seen-before:1b")

    # Fails safely means: the consumer's own existing gate correctly refuses,
    # not that resolution itself raises - matches complete_with_tools's real
    # production behavior (ModelCapabilityError), proven at the LLMClient
    # level in tests/test_llm_extra.py.
    assert resolved.native_tool_calls is False


# --- 18: capability profile cannot widen authority ---

_AUTHORITY_SHAPED_TERMS = (
    "path", "file", "network", "host", "url", "approve", "approval",
    "authority", "policy", "command", "execute", "write", "delete", "acquire",
)


def test_capability_profile_field_set_carries_no_authority_shaped_fields():
    field_names = set(ModelCapabilities.model_fields.keys())
    for name in field_names:
        for term in _AUTHORITY_SHAPED_TERMS:
            assert term not in name, f"ModelCapabilities.{name} looks authority-shaped ({term!r})"


def test_resolved_profile_for_unknown_model_never_exceeds_known_profile_authority_relevant_fields():
    # native_tool_calls is the one field that gates a real production
    # authority-adjacent decision (whether Kriya will even attempt to parse
    # model-issued tool calls at all) - an unknown model must never be MORE
    # permissive here than a known, evidenced one.
    cfg = AppConfig()
    unknown = resolve_model_capability_profile(cfg, "never-configured:1b")
    known = KNOWN_MODEL_PROFILES["qwen3-coder:30b"]

    assert unknown.capabilities.native_tool_calls is False
    assert known.native_tool_calls is True  # known profile is real evidence, not a comparison bug


# --- 19: config serialization/loading preserves the contract ---

def test_config_loading_path_preserves_resolution_contract():
    """Uses the REAL config-loading shape, not a generic pydantic round-trip:
    kriya/config/config.py::load_config() always constructs AppConfig from a
    single raw, already-merged dict (kriya/config/config.py:1264,
    `AppConfig(**state.config_dict)`) - confirmed by grep as the ONLY
    production AppConfig(**...) construction site anywhere in kriya/. A
    user's llm.capabilities override replaces that whole sub-dict wholesale
    (kriya/config/config.py:1177-1189's "simple deep merge of level-1
    dicts" - .update() at the llm level, not a recursive per-field merge),
    so only the keys actually written by the user ever reach
    ModelCapabilities's own constructor - which is exactly what makes
    model_fields_set an accurate provenance signal for the real path this
    test reproduces."""
    merged = {
        "llm": {"model": _M2},  # capabilities key absent entirely - untouched
        "llm_chain": [{
            "model": "explicit-fallback:1b",
            "capabilities": {"json_mode": False},  # exactly what the user wrote, nothing more
        }],
    }
    cfg = AppConfig(**merged)

    resolved_primary = resolve_model_capability_profile(cfg, _M2)
    assert resolved_primary.source == "known_production_profile"
    _assert_matches_campaign_evidence(resolved_primary.capabilities)

    resolved_fallback = resolve_model_capability_profile(cfg, "explicit-fallback:1b")
    assert resolved_fallback.source == "explicit_llm_chain"
    assert resolved_fallback.capabilities.json_mode is False


def test_exclude_unset_serialization_preserves_provenance_on_reload():
    """The provenance-preserving pydantic idiom (model_dump(exclude_unset=True),
    not a plain model_dump()) round-trips correctly - documented here as the
    correct technique, in case any future Kriya code path needs to
    serialize/reload a config and must not silently convert "known
    production profile" resolutions into "explicit, using whatever the
    untouched default happened to be" ones. A plain model_dump() does NOT
    have this property (every field looks "set" once dumped, even an
    untouched one) - no production code path does that round-trip today
    (verified: the only two kernel.config.model_dump() call sites,
    kriya/workflow/workflow.py:1036/1720, feed compute_config_fingerprint(),
    never AppConfig(**...) reconstruction), so this is a disclosed,
    deliberately-not-engineered-around edge case, not a live production bug."""
    cfg = AppConfig()
    cfg.llm.model = _M2  # capabilities left untouched

    as_dict = cfg.model_dump(exclude_unset=True)
    reloaded = AppConfig(**as_dict)

    original = resolve_model_capability_profile(cfg, _M2)
    round_tripped = resolve_model_capability_profile(reloaded, _M2)
    assert original.source == round_tripped.source == "known_production_profile"
    assert original.capabilities == round_tripped.capabilities


# --- 20: effective profile / provenance observability ---

def test_resolved_profile_exposes_model_and_source_for_observability():
    cfg = AppConfig()
    cfg.llm.model = _M1

    result = resolve_model_capability_profile(cfg, _M1)

    assert isinstance(result, ResolvedCapabilityProfile)
    assert result.model == _M1
    assert result.source in {
        "explicit_primary", "explicit_llm_chain", "explicit_agent_llms",
        "known_production_profile", "unverified_conservative_default",
    }


def test_resolution_logging_is_deduplicated_per_model_source_pair(caplog):
    import logging as _logging
    cfg = AppConfig()
    cfg.llm.model = _M1

    with caplog.at_level(_logging.INFO, logger="kriya.core.model_capabilities"):
        resolve_model_capability_profile(cfg, _M1)
        resolve_model_capability_profile(cfg, _M1)
        resolve_model_capability_profile(cfg, _M1)

    resolution_logs = [r for r in caplog.records if "Model capability profile resolved" in r.getMessage() and _M1 in r.getMessage()]
    # De-duplicated across repeated calls with the same (model, source) pair -
    # not a hard "exactly once ever" (another test in this same process may
    # have already logged this pair first), but never one log line per call.
    assert len(resolution_logs) <= 1
