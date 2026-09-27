"""Provider registry (PRD-031A §4.1).

The only place a provider name is resolved to an implementation. Built-in
providers are listed in ``kriya.static_analysis.adapters.BUILTIN_PROVIDERS``
and imported lazily, so a provider's module is loaded only when selected.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Dict, Optional, Type

from kriya.static_analysis.adapters import BUILTIN_PROVIDERS
from kriya.static_analysis.port import ExecutionContext, ProviderFactory, StaticAnalysisPort


@dataclass(frozen=True)
class ProviderRegistration:
    factory: ProviderFactory
    # pydantic model validating static_analysis.providers.<name> at config load.
    settings_model: Optional[Type[Any]]


_REGISTRY: Dict[str, ProviderRegistration] = {}


class ProviderNotRegisteredError(LookupError):
    pass


def register_provider(name: str, factory: ProviderFactory, settings_model: Optional[Type[Any]] = None) -> None:
    _REGISTRY[name] = ProviderRegistration(factory=factory, settings_model=settings_model)


def registration(name: str) -> ProviderRegistration:
    if name not in _REGISTRY and name in BUILTIN_PROVIDERS:
        importlib.import_module(BUILTIN_PROVIDERS[name])
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ProviderNotRegisteredError(f"static-analysis provider {name!r} is not registered") from None


def is_registered(name: str) -> bool:
    try:
        registration(name)
    except ProviderNotRegisteredError:
        return False
    return True


def validate_provider_settings(name: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a provider's settings with its own model; returns them normalized."""
    model = registration(name).settings_model
    if model is None:
        return dict(settings)
    return model.model_validate(settings).model_dump()


def create_provider(name: str, settings: Dict[str, Any], context: ExecutionContext) -> StaticAnalysisPort:
    return registration(name).factory(validate_provider_settings(name, settings), context)
