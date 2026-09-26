"""MODEL-QUAL-IDENTITY-001: the inference settings a model is called with.

A PRD-013 ``ModelRuntimeFingerprint`` identifies the served runtime: the
artifact, the provider, Kriya's protocol adapter, and the ``num_ctx`` window.
It says nothing about what else a request asks for. ``reasoning_effort``,
``think``, sampling options (``top_p``, ``top_k``, ``min_p``,
``repeat_penalty``, ``seed``...) and ``temperature`` all change what the
same runtime produces. A qwen3.6 runtime that qualifies with
``reasoning_effort: none`` fails the same cases at its default reasoning.

``InferenceSettings`` is the normalized set of those settings, as a role
actually sends them. A qualification record is keyed by the
**qualification identity**: the runtime digest plus the settings digest
(``qualification_identity``). The runtime fingerprint itself is unchanged,
so role independence, role metrics, the routing table and the runtime store
keep their keys.

Rules:
- The settings are what the request carries: ``temperature``, the
  ``reasoning`` flag (it changes Kriya's own budget floor and reasoning
  handling), and the whole ``extra_body`` except the per-request context
  window PRD-016 chooses (the runtime adapter's field,
  ``model_runtime.without_context_window``; already part of the runtime
  fingerprint).
- ``max_tokens`` is not identity: PRD-016 changes it per call. The
  binding's configured output ceiling is kept as metadata only.
- Normalization: keys sorted; an empty ``options`` equals an absent one,
  which equals ``extra_body: None`` or ``{}``; an integral float equals the
  integer (``1.0`` == ``1``).
- Per role, the temperature is the one the role's calls send
  (``binding_inference_settings``): the Developer always sends the primary
  ``llm.temperature`` (a fallback's own ``temperature`` field is not sent on
  that path); a role with its own ``agent_llms`` binding sends that
  binding's; a role on the primary binding sends ``llm.temperature``, except
  the Reviewer, which sends ``llm.reviewer_temperature`` when set.
- ``llm.retry_temperature`` is a per-call override on Developer retries, not
  a role setting. A retry call sent with it is a different inference
  identity at dispatch, so it gets no qualified tier or measured limit
  unless that identity was qualified.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

INFERENCE_SETTINGS_VERSION = 1



def _normalize(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return str(value)


def normalized_extra_body(extra_body: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """``extra_body`` without the per-call options, with an empty
    ``options`` dropped and numbers normalized."""
    from kriya.core.model_runtime import without_context_window

    body = copy.deepcopy(dict(extra_body)) if isinstance(extra_body, Mapping) else {}
    return _normalize(without_context_window(body))


@dataclass(frozen=True)
class InferenceSettings:
    temperature: Optional[float]
    reasoning: bool
    extra_body_json: str = "{}"
    # Metadata only: PRD-016 sizes max_tokens per call.
    output_ceiling: Optional[int] = field(default=None, compare=False)

    @property
    def extra_body(self) -> Dict[str, Any]:
        return json.loads(self.extra_body_json)

    def identity_fields(self) -> Dict[str, Any]:
        return {
            "version": INFERENCE_SETTINGS_VERSION,
            "temperature": _normalize(self.temperature),
            "reasoning": bool(self.reasoning),
            "extra_body": self.extra_body,
        }

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.identity_fields(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {**self.identity_fields(), "digest": self.digest, "output_ceiling": self.output_ceiling}


def request_settings(*, temperature: Optional[float], reasoning: bool,
                     extra_body: Optional[Mapping[str, Any]],
                     output_ceiling: Optional[int] = None) -> InferenceSettings:
    """The settings of one request, as sent."""
    body = normalized_extra_body(extra_body)
    return InferenceSettings(
        temperature=None if temperature is None else float(temperature),
        reasoning=bool(reasoning),
        extra_body_json=json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        output_ceiling=output_ceiling,
    )


def _role_temperature(config: Any, role: str, binding: Any) -> Optional[float]:
    llm = config.llm
    if role == "developer" or binding is llm:
        if role == "reviewer" and getattr(llm, "reviewer_temperature", None) is not None:
            return llm.reviewer_temperature
        return llm.temperature
    own = getattr(binding, "temperature", None)
    return own if own is not None else llm.temperature


def binding_inference_settings(config: Any, role: str, binding: Any = None) -> InferenceSettings:
    """What ``role``'s calls to ``binding`` (default: the primary llm) send.
    See the module docstring for the per-role temperature rule."""
    from kriya.core.model_runtime import binding_output_tokens

    binding = binding if binding is not None else config.llm
    return request_settings(
        temperature=_role_temperature(config, role, binding),
        reasoning=bool(getattr(binding, "reasoning", False)),
        extra_body=getattr(binding, "extra_body", None),
        output_ceiling=binding_output_tokens(config, None if binding is config.llm else binding),
    )


def role_binding_for_model(config: Any, role: str, model: str) -> Any:
    """The binding ``role`` calls ``model`` through: the Developer's primary
    llm or an llm_chain entry; another role's own agent_llms binding or chain
    entry, else the primary llm. Falls back to the primary llm."""
    target = (model or "").casefold()
    llm = config.llm
    if role == "developer":
        candidates = [llm, *config.llm_chain]
    else:
        role_cfg = getattr(config.agent_llms, role, None)
        candidates = []
        if role_cfg is not None:
            candidates.extend(([role_cfg.llm] if role_cfg.llm is not None else []) + list(role_cfg.llm_chain))
        candidates.append(llm)
    return next((c for c in candidates if (c.model or "").casefold() == target), llm)


def role_inference_settings(config: Any, role: str, model: str) -> InferenceSettings:
    """The settings ``role`` sends ``model`` with (one identity per role)."""
    return binding_inference_settings(config, role, role_binding_for_model(config, role, model))


def qualification_identity(runtime_digest: str, settings: InferenceSettings) -> str:
    """The key of a qualification record: the exact runtime plus the
    inference settings it was qualified with."""
    canonical = json.dumps({"runtime": runtime_digest, "inference_settings": settings.digest},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "INFERENCE_SETTINGS_VERSION", "InferenceSettings", "binding_inference_settings",
    "normalized_extra_body", "qualification_identity", "request_settings", "role_binding_for_model",
    "role_inference_settings",
]
