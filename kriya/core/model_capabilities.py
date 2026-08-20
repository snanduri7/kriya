"""Offline model-protocol conformance checks.

This module never contacts a model endpoint. It validates captured/sample
response shapes so local models can be profiled without a live run.
"""
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List

from kriya.config.config import ModelCapabilities


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


def capabilities_for_model(config, model: str) -> ModelCapabilities:
    if model == config.llm.model:
        return config.llm.capabilities
    for candidate in config.llm_chain:
        if candidate.model == model:
            return candidate.capabilities
    # Explicit role-specific models currently reuse the conservative defaults.
    return ModelCapabilities()


def generation_protocol_for_model(config, model: str) -> GenerationProtocol:
    capabilities = capabilities_for_model(config, model)
    return GenerationProtocol(
        json_mode=capabilities.json_mode,
        reliable_multiline_json=capabilities.reliable_multiline_json,
        streaming=capabilities.streaming,
        preferred_edit_protocol=capabilities.preferred_edit_protocol,
    )
