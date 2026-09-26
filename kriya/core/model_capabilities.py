"""Offline model-protocol conformance checks.

This module never contacts a model endpoint. It validates captured/sample
response shapes so local models can be profiled without a live run.

MODEL-001 P1 (2026-09-17): production capability contract closing the gap
the MODEL-001 campaign found - capability resolution/profile selection
existed only as campaign/manual machinery (spikes/model001_campaign/), not
as a Kriya-owned, deterministic contract. `resolve_model_capability_profile`
is the one production entry point every capability-sensitive call site
should route through; `capabilities_for_model`/`generation_protocol_for_model`
are thin, backward-compatible wrappers over it, not a second implementation.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from kriya.config.config import ModelCapabilities

logger = logging.getLogger(__name__)


class ModelCapabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConformanceResult:
    compatible: bool
    violations: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GenerationProtocol:
    """The ordinary text-generation protocol measured safe for one model."""

    json_mode: bool
    reliable_multiline_json: bool
    streaming: bool
    preferred_edit_protocol: str


def validate_tool_call_sample(
    arguments_text: str, capabilities: ModelCapabilities,
) -> ConformanceResult:
    violations: List[str] = []
    if not capabilities.native_tool_calls:
        violations.append("native tool calls are disabled for this model")
    if len(arguments_text) > capabilities.max_tool_argument_chars:
        violations.append(
            f"tool arguments exceed the measured safe limit of "
            f"{capabilities.max_tool_argument_chars} characters"
        )
    try:
        parsed = json.loads(arguments_text)
        if not isinstance(parsed, dict):
            violations.append("tool arguments must decode to an object")
    except (json.JSONDecodeError, TypeError):
        violations.append("tool arguments are not valid JSON")
    return ConformanceResult(compatible=not violations, violations=violations)


# KNOWN_MODEL_PROFILES is hardcoded Python data, never a config field - a
# repository/user config can never inject or override an entry here (unlike
# llm.capabilities / llm_chain[].capabilities / agent_llms.*.llm.capabilities,
# which remain ordinary AppConfig fields under SEC-009's existing blanket
# classification of llm/llm_chain/agent_llms, unchanged by this module).
# Seeded ONLY from live-measured MODEL-001 campaign evidence
# (spikes/model001_campaign/capability_profiles.json, probed 2026-09-17,
# corrected after the campaign's own first probe attempt produced a false
# json_mode=false/reliable_multiline_json=false reading for a reasoning-
# capable model - see that file and docs/assurance/MODEL_001_CAMPAIGN.md).
# Do not add a model here without equivalent live evidence; do not widen an
# existing entry to "make a workflow pass" without new evidence either.
#
# Keys are the pre-normalized (_normalize_model_identity) form - lookups
# always normalize first, entries here are written already-normalized so a
# reader can see the exact match key at a glance.
KNOWN_MODEL_PROFILES: Dict[str, ModelCapabilities] = {
    "qwen3-coder:30b": ModelCapabilities(
        native_tool_calls=True,
        json_mode=True,
        reliable_multiline_json=True,
        streaming=True,
        max_tool_argument_chars=8192,
        preferred_edit_protocol="small_native_tools",
    ),
    "qwen3.6:35b-a3b-q4_k_m": ModelCapabilities(
        native_tool_calls=True,
        json_mode=True,
        reliable_multiline_json=True,
        streaming=True,
        max_tool_argument_chars=8192,
        preferred_edit_protocol="small_native_tools",
    ),
    "qwen3.5:9b": ModelCapabilities(
        native_tool_calls=True,
        json_mode=True,
        reliable_multiline_json=True,
        streaming=True,
        max_tool_argument_chars=8192,
        preferred_edit_protocol="small_native_tools",
    ),
}

# The conservative posture for a model with neither an explicit,
# user-provided capabilities override NOR a KNOWN_MODEL_PROFILES entry.
# Deliberately NOT the bare ModelCapabilities() dataclass default - that
# default happens to assume native_tool_calls=True/json_mode=True, which is
# an assumption baked into the class, not a verified fact about any
# specific model. Using it silently for a genuinely unverified model is
# exactly the MODEL-001 gap ("ad-hoc/manual discovery is insufficient" -
# nothing here was ever discovered at all). This profile instead fails
# closed at the one consumer that already enforces native_tool_calls
# (LLMClient.complete_with_tools -> ModelCapabilityError) and prefers the
# safer full_file edit protocol (agent.py's own existing preference order
# already treats full_file as the safe fallback relative to
# small_native_tools) rather than assuming small, precise anchored edits
# will parse correctly against a model nobody has measured.
UNVERIFIED_MODEL_CONSERVATIVE_PROFILE = ModelCapabilities(
    native_tool_calls=False,
    json_mode=False,
    reliable_multiline_json=False,
    streaming=True,
    max_tool_argument_chars=8192,
    preferred_edit_protocol="full_file",
)

# Per-process, per-(model, source) log de-duplication - resolution can be
# called once per completion, and this module must never log per-token/
# per-call noise. Not persisted, not a security control, purely to keep
# logs readable across a long-running session with many calls to the same
# model/source pair.
_ALREADY_LOGGED_RESOLUTIONS: set = set()

_AGENT_ROLE_NAMES = (
    "planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance",
)


def _normalize_model_identity(model: str) -> str:
    """The ONE explicit, deterministic normalization rule this module
    applies: case-folding the full model string. Never strips or splits a
    quantization/tag suffix, never does prefix or fuzzy matching -
    "qwen3.5:9B" and "qwen3.5:9b" are the same identity (confirmed live:
    Ollama itself resolves model tags case-insensitively), but "qwen3.5:9b"
    and "qwen3.5:9b-mlx" or "qwen3.5:35b-a3b" remain distinct identities on
    purpose - MODEL-001's own explicit requirement: a near-match name must
    never bind to a different model's capabilities."""
    return (model or "").strip().lower()


@dataclass(frozen=True)
class ResolvedCapabilityProfile:
    """Provenance-carrying capability resolution result. The effective
    ModelCapabilities alone doesn't say WHERE it came from, which MODEL-001's
    own observability requirement needs (source is one of: explicit_primary,
    explicit_llm_chain, explicit_agent_llms, known_production_profile,
    unverified_conservative_default)."""

    model: str
    capabilities: ModelCapabilities
    source: str


def _log_resolution_once(profile: "ResolvedCapabilityProfile") -> None:
    key = (profile.model, profile.source)
    if key in _ALREADY_LOGGED_RESOLUTIONS:
        return
    _ALREADY_LOGGED_RESOLUTIONS.add(key)
    logger.info(
        "Model capability profile resolved [Model: %s, Source: %s, "
        "native_tool_calls=%s, json_mode=%s, reliable_multiline_json=%s, "
        "preferred_edit_protocol=%s]",
        profile.model, profile.source,
        profile.capabilities.native_tool_calls,
        profile.capabilities.json_mode,
        profile.capabilities.reliable_multiline_json,
        profile.capabilities.preferred_edit_protocol,
    )


def _resolve_for_binding(model: str, capabilities: ModelCapabilities, explicit_source: str) -> ResolvedCapabilityProfile:
    """A config binding was found for this model identity (primary llm, an
    llm_chain entry, or an agent_llms role's own llm/llm_chain). Decides
    whether that binding's own .capabilities is a real, explicit override
    or just the untouched ModelCapabilities() class default - in the
    latter case, a KNOWN_MODEL_PROFILES entry (real evidence) outranks the
    untouched default, and the absence of one falls to the conservative
    profile rather than silently trusting an unverified default.

    Explicitness is determined by pydantic's own field-set provenance
    (capabilities.model_fields_set), never by value-comparison against the
    bare defaults - a value-equality check cannot distinguish "the user
    explicitly wrote native_tool_calls: true" from "nobody touched this
    block at all" when the explicit value happens to equal the class
    default, which is a real, required invariant (confirmed live against
    the actual kriya/config/config.py::load_config() merge path: a user
    override's llm.capabilities dict replaces the whole sub-dict wholesale
    rather than deep-merging field-by-field, so only the keys the user
    actually wrote ever reach ModelCapabilities's own constructor, and
    pydantic tracks exactly those in model_fields_set regardless of
    whether the object was built from that merged dict, from a bare
    AppConfig() plus a later plain attribute assignment, or by passing an
    already-constructed ModelCapabilities instance as a constructor kwarg -
    all three patterns verified experimentally, not assumed)."""
    if capabilities.model_fields_set:
        return ResolvedCapabilityProfile(model=model, capabilities=capabilities, source=explicit_source)
    known = KNOWN_MODEL_PROFILES.get(_normalize_model_identity(model))
    if known is not None:
        return ResolvedCapabilityProfile(model=model, capabilities=known, source="known_production_profile")
    return ResolvedCapabilityProfile(
        model=model, capabilities=UNVERIFIED_MODEL_CONSERVATIVE_PROFILE,
        source="unverified_conservative_default",
    )


def is_campaign_named_model(model: str) -> bool:
    """Whether this model identity has a MODEL-001 campaign entry in
    KNOWN_MODEL_PROFILES (same exact, case-folded match as every other lookup
    here). Independent of which capability profile a config resolves to: the
    packaged default config declares llm.capabilities explicitly, so a loaded
    config resolves as explicit_primary even for a campaign model."""
    return _normalize_model_identity(model) in KNOWN_MODEL_PROFILES


def resolve_model_capability_profile(config, model: str) -> ResolvedCapabilityProfile:
    """Deterministic capability-profile resolution - the one production
    contract every capability-sensitive call site should route through.

    Precedence:
      1. explicit validated override - a config binding for THIS exact
         model identity (primary llm, an llm_chain entry, or any
         agent_llms role's own llm/llm_chain) whose .capabilities has a
         non-empty model_fields_set - i.e. at least one field was actually
         present in the user's own config, per pydantic's own field-set
         provenance (see _resolve_for_binding's docstring) - never a
         value-equality guess, so an explicit override remains explicit
         even when its value happens to equal the class default.
      2. known production profile - KNOWN_MODEL_PROFILES, live-measured
         MODEL-001 campaign evidence, used when a binding exists for this
         model but its own .capabilities was never touched.
      3. safe fallback / fail closed - UNVERIFIED_MODEL_CONSERVATIVE_PROFILE,
         for a model with no binding anywhere in config and no production
         evidence, or a binding with neither an explicit override nor
         known evidence.

    Model identity match is case-folded only (see _normalize_model_identity)
    - never fuzzy, never prefix-based. Never contacts a model endpoint.
    Capability profiles describe communication protocol only - they carry
    no filesystem/network/tool-approval/command authority and can never
    widen what a model is authorized to do (see ModelCapabilities' own
    field set, and kriya/config/authority.py's unchanged, pre-existing
    SEC-009 classification of llm/llm_chain/agent_llms - this module adds
    no new config-authority surface)."""
    normalized_target = _normalize_model_identity(model)

    if _normalize_model_identity(config.llm.model) == normalized_target:
        result = _resolve_for_binding(model, config.llm.capabilities, "explicit_primary")
        _log_resolution_once(result)
        return result

    for candidate in config.llm_chain:
        if _normalize_model_identity(candidate.model) == normalized_target:
            result = _resolve_for_binding(model, candidate.capabilities, "explicit_llm_chain")
            _log_resolution_once(result)
            return result

    for role_name in _AGENT_ROLE_NAMES:
        role_cfg = getattr(config.agent_llms, role_name, None)
        if role_cfg is None:
            continue
        if role_cfg.llm is not None and _normalize_model_identity(role_cfg.llm.model) == normalized_target:
            result = _resolve_for_binding(model, role_cfg.llm.capabilities, "explicit_agent_llms")
            _log_resolution_once(result)
            return result
        for candidate in role_cfg.llm_chain:
            if _normalize_model_identity(candidate.model) == normalized_target:
                result = _resolve_for_binding(model, candidate.capabilities, "explicit_agent_llms")
                _log_resolution_once(result)
                return result

    known = KNOWN_MODEL_PROFILES.get(normalized_target)
    if known is not None:
        result = ResolvedCapabilityProfile(model=model, capabilities=known, source="known_production_profile")
        _log_resolution_once(result)
        return result

    result = ResolvedCapabilityProfile(
        model=model, capabilities=UNVERIFIED_MODEL_CONSERVATIVE_PROFILE,
        source="unverified_conservative_default",
    )
    _log_resolution_once(result)
    return result


def capabilities_for_model(config, model: str) -> ModelCapabilities:
    """Thin, backward-compatible wrapper over resolve_model_capability_profile -
    existing call sites that only need the capabilities (not provenance)
    keep working unchanged."""
    return resolve_model_capability_profile(config, model).capabilities


def generation_protocol_for_model(config, model: str) -> GenerationProtocol:
    capabilities = capabilities_for_model(config, model)
    return GenerationProtocol(
        json_mode=capabilities.json_mode,
        reliable_multiline_json=capabilities.reliable_multiline_json,
        streaming=capabilities.streaming,
        preferred_edit_protocol=capabilities.preferred_edit_protocol,
    )
