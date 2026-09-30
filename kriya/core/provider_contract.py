"""PROVIDER-CONTRACT-001: the provider-neutral Kriya <-> inference contract.

Invariant: any model or runtime setting Kriya records, budgets against,
qualifies or treats as authoritative is either proven to reach the provider
with the intended semantics, or Kriya fails closed. Desired configuration is
not effective inference identity.

A runtime adapter (kriya/core/inference_runtime.py, INF-001) declares, per
semantic setting, how it can carry it (``Support``) and turns a binding's
settings into a ``ProviderRequestPlan``: the exact wire body plus, for every
setting, what was requested, what is effective and how that is known
(``Provenance``). Nothing here names a provider: workflow code sees only this
vocabulary."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple


class Support(str, Enum):
    SUPPORTED = "supported"                    # sent per request, applied by the provider
    SERVER_CONFIG_ONLY = "server_config_only"  # only the served model's configuration sets it
    OBSERVABLE_ONLY = "observable_only"        # cannot be set, can be observed
    UNSUPPORTED = "unsupported"                # can be neither set nor verified


class Provenance(str, Enum):
    REQUEST = "request"                          # carried by the request, a supported field
    SERVER_MODEL_CONFIG = "server_model_config"  # the served model's own configuration (observed)
    SERVER_OBSERVED = "server_observed"          # observed on the running server
    INFERRED = "inferred"
    UNVERIFIED = "unverified"                    # nothing proves what the provider applies


# Semantic inference settings (the vocabulary every adapter maps).
SEMANTIC_SETTINGS: Tuple[str, ...] = (
    "context_window", "temperature", "top_p", "top_k", "min_p", "repeat_penalty",
    "presence_penalty", "frequency_penalty", "seed", "reasoning", "keep_alive",
)
# Transport features an adapter declares.
PROVIDER_FEATURES: Tuple[str, ...] = (
    "stream_usage", "truncate_control", "prompt_usage", "served_context_observation",
    "server_parameter_observation",
)

# Typed reason codes.
PROVIDER_SETTING_UNSUPPORTED = "PROVIDER_SETTING_UNSUPPORTED"
PROVIDER_SETTING_NOT_EFFECTIVE = "PROVIDER_SETTING_NOT_EFFECTIVE"
PROVIDER_SETTING_UNKNOWN = "PROVIDER_SETTING_UNKNOWN"
PROVIDER_SETTING_CONFLICT = "PROVIDER_SETTING_CONFLICT"
SERVED_CONTEXT_BELOW_REQUESTED = "SERVED_CONTEXT_BELOW_REQUESTED"
RUNTIME_CONTEXT_IDENTITY_MISMATCH = "RUNTIME_CONTEXT_IDENTITY_MISMATCH"
PROVIDER_PROMPT_TRUNCATED = "PROVIDER_PROMPT_TRUNCATED"
QUALIFICATION_IDENTITY_UNVERIFIED = "QUALIFICATION_IDENTITY_UNVERIFIED"

PROVIDER_CONTRACT_REASON_CODES = frozenset({
    PROVIDER_SETTING_UNSUPPORTED, PROVIDER_SETTING_NOT_EFFECTIVE, PROVIDER_SETTING_UNKNOWN,
    PROVIDER_SETTING_CONFLICT, SERVED_CONTEXT_BELOW_REQUESTED, RUNTIME_CONTEXT_IDENTITY_MISMATCH,
    PROVIDER_PROMPT_TRUNCATED, QUALIFICATION_IDENTITY_UNVERIFIED,
})


class ProviderContractError(ValueError):
    """A request refused (before inference) or a response rejected (after
    it) because what the provider applies is not what Kriya relies on."""

    def __init__(self, reason_code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.details = dict(details or {})


@dataclass(frozen=True)
class ProviderCapabilities:
    """How an adapter carries each semantic setting and which transport
    features it has. A setting it does not list is UNSUPPORTED."""

    settings: Mapping[str, Support] = field(default_factory=dict)
    features: Mapping[str, Support] = field(default_factory=dict)

    def setting(self, name: str) -> Support:
        return self.settings.get(name, Support.UNSUPPORTED)

    def feature(self, name: str) -> Support:
        return self.features.get(name, Support.UNSUPPORTED)

    def to_dict(self) -> Dict[str, Any]:
        return {"settings": {k: v.value for k, v in sorted(self.settings.items())},
                "features": {k: v.value for k, v in sorted(self.features.items())}}


@dataclass(frozen=True)
class SettingState:
    """One semantic setting of one request: what Kriya asked for, what the
    provider applies, and how that is known."""

    name: str
    requested: Any
    effective: Any
    provenance: Provenance
    support: Support

    @property
    def effective_as_requested(self) -> bool:
        return self.requested is None or self.effective == self.requested

    def to_dict(self) -> Dict[str, Any]:
        return {"requested": self.requested, "effective": self.effective,
                "provenance": self.provenance.value, "support": self.support.value}


@dataclass(frozen=True)
class ProviderRequestPlan:
    """A binding's settings as one provider request: the exact wire body and
    every setting's requested/effective state. ``unknown`` lists request
    fields no adapter vocabulary names (sent only outside production)."""

    wire_body: Dict[str, Any]
    settings: Tuple[SettingState, ...]
    unknown: Tuple[str, ...] = ()
    conflicts: Tuple[str, ...] = ()

    def setting(self, name: str) -> Optional[SettingState]:
        return next((state for state in self.settings if state.name == name), None)

    def not_effective(self) -> Tuple[SettingState, ...]:
        """Requested settings verified to differ at the provider."""
        return tuple(state for state in self.settings
                     if state.requested is not None and state.provenance is not Provenance.UNVERIFIED
                     and not state.effective_as_requested)

    def unverified(self) -> Tuple[SettingState, ...]:
        return tuple(state for state in self.settings
                     if state.requested is not None and state.provenance is Provenance.UNVERIFIED)

    def identity(self) -> Dict[str, Any]:
        """The effective inference identity these settings contribute:
        effective values with their provenance (the requested value is kept
        only where it differs, so an unapplied request is visible)."""
        identity: Dict[str, Any] = {}
        for state in sorted(self.settings, key=lambda s: s.name):
            entry: Dict[str, Any] = {"effective": state.effective, "provenance": state.provenance.value}
            if not state.effective_as_requested:
                entry["requested"] = state.requested
            identity[state.name] = entry
        if self.unknown:
            identity["unknown_request_fields"] = sorted(self.unknown)
        return identity

    def to_dict(self) -> Dict[str, Any]:
        return {"settings": {s.name: s.to_dict() for s in self.settings}, "unknown": list(self.unknown),
                "conflicts": list(self.conflicts)}

    def enforce(self, *, strict: bool) -> None:
        """Refuse before inference what cannot be proven: conflicting or
        unknown authoritative fields, and (strict) any requested setting the
        provider verifiably does not apply."""
        if self.conflicts:
            raise ProviderContractError(PROVIDER_SETTING_CONFLICT, "; ".join(self.conflicts),
                                        {"conflicts": list(self.conflicts)})
        if strict and self.unknown:
            raise ProviderContractError(
                PROVIDER_SETTING_UNKNOWN,
                f"request field(s) {sorted(self.unknown)} are not part of the provider contract; production "
                "never forwards an unvalidated field", {"unknown": sorted(self.unknown)})
        missing = self.not_effective()
        if strict and missing:
            raise ProviderContractError(
                PROVIDER_SETTING_NOT_EFFECTIVE,
                "; ".join(f"{s.name} requested {s.requested!r} but the provider applies {s.effective!r} "
                          f"({s.provenance.value})" for s in missing),
                {"settings": {s.name: s.to_dict() for s in missing}})


def identity_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ContextWindowState:
    """Requested, served and budget context of one call (tokens).

    - requested: what the binding asks for;
    - served: what the provider has loaded, with its provenance (None when
      unobserved);
    - budget: what prompt admission uses - never above requested, never
      above an observed served window."""

    requested: Optional[int]
    served: Optional[int]
    served_provenance: Provenance
    budget: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return {"requested_context_window": self.requested, "served_context_window": self.served,
                "served_context_provenance": self.served_provenance.value, "budget_context_window": self.budget}


def context_window_state(requested: Optional[int], served: Optional[int],
                         served_provenance: Provenance, *, exact: bool) -> ContextWindowState:
    """The budget window for ``requested`` given an observation. A served
    window below the requested one is always refused (the prompt Kriya
    admits could be truncated); a different served window under an exact
    identity policy is refused too. Otherwise the budget is the requested
    window (never enlarged because the server happens to serve more)."""
    if served is not None and requested is not None:
        if served < requested:
            raise ProviderContractError(
                SERVED_CONTEXT_BELOW_REQUESTED,
                f"the provider serves a {served}-token context window; {requested} was requested",
                {"requested": requested, "served": served, "provenance": served_provenance.value})
        if exact and served != requested:
            raise ProviderContractError(
                RUNTIME_CONTEXT_IDENTITY_MISMATCH,
                f"the provider serves a {served}-token context window; the runtime identity requires exactly "
                f"{requested}. Pin the served window in the model's server configuration (kriya model pin)",
                {"requested": requested, "served": served, "provenance": served_provenance.value})
    budget = requested if requested is not None else served
    return ContextWindowState(requested, served, served_provenance if served is not None else Provenance.UNVERIFIED,
                              budget)


def budget_window(requested: Optional[int], served: Optional[int]) -> Optional[int]:
    """The window prompts are sized to before dispatch: the requested one,
    never enlarged because the provider happens to serve more (and never
    above what it serves - dispatch refuses that request anyway). The one
    rule allocation, routing and qualification share with the dispatch
    check (context_window_state)."""
    if requested and served:
        return min(requested, served)
    return requested or served


# Tokenizer bound used when a runtime's own is not measured: calibrated
# 2026-09-30 on generic code, prose, JSON and whitespace-heavy text (Ollama,
# qwen3-coder 1.5-6.83 bytes/token, qwen3.6 1.5-5.86); the default leaves
# margin above the largest observation.
DEFAULT_BYTES_PER_TOKEN_CEILING = 8.0


_WHITESPACE_RUN = re.compile(r"\s+")


def consumption_bytes(text: str) -> int:
    """The UTF-8 size of ``text`` with every whitespace run counted as one
    byte: a tokenizer can merge a run of indentation into a single token,
    so raw bytes would overstate the minimum token count of whitespace-dense
    text; this size stays a lower-bound basis whatever the layout."""
    return len(_WHITESPACE_RUN.sub(" ", text).encode("utf-8"))


def check_prompt_consumption(*, dispatched_bytes: int, reported_prompt_tokens: Optional[int],
                             bytes_per_token_ceiling: Optional[float]) -> Optional[Dict[str, Any]]:
    """Defence in depth against silent provider-side prompt truncation: a
    real prompt of ``dispatched_bytes`` cannot tokenize to fewer than
    ``dispatched_bytes / ceiling`` tokens, so a provider that reports
    evaluating fewer dropped input. Returns the evidence when it did (the
    caller raises PROVIDER_PROMPT_TRUNCATED), None otherwise or when the
    provider reported no usage."""
    if not reported_prompt_tokens or dispatched_bytes <= 0:
        return None
    ceiling = bytes_per_token_ceiling or DEFAULT_BYTES_PER_TOKEN_CEILING
    minimum = int(dispatched_bytes / ceiling)
    if reported_prompt_tokens >= minimum:
        return None
    return {"dispatched_bytes": dispatched_bytes, "reported_prompt_tokens": reported_prompt_tokens,
            "minimum_expected_tokens": minimum, "bytes_per_token_ceiling": ceiling}


__all__ = [
    "ContextWindowState", "DEFAULT_BYTES_PER_TOKEN_CEILING", "PROVIDER_CONTRACT_REASON_CODES",
    "PROVIDER_FEATURES", "PROVIDER_PROMPT_TRUNCATED", "PROVIDER_SETTING_CONFLICT",
    "PROVIDER_SETTING_NOT_EFFECTIVE", "PROVIDER_SETTING_UNKNOWN", "PROVIDER_SETTING_UNSUPPORTED",
    "Provenance", "ProviderCapabilities", "ProviderContractError", "ProviderRequestPlan",
    "QUALIFICATION_IDENTITY_UNVERIFIED", "RUNTIME_CONTEXT_IDENTITY_MISMATCH", "SEMANTIC_SETTINGS",
    "SERVED_CONTEXT_BELOW_REQUESTED", "SettingState", "Support", "budget_window", "check_prompt_consumption",
    "consumption_bytes",
    "context_window_state", "identity_digest",
]
