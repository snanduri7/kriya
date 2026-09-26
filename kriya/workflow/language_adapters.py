"""PRD-028: the language-adapter authority contract.

A language adapter declares what Kriya can do deterministically for source
files of one language. Each capability is SUPPORTED, PARTIAL or
UNSUPPORTED, and every authority decision that depends on a capability
asks this registry rather than switching on file extensions itself.

The capabilities:
- ``symbol_identity``: a stable, deterministic member identity.
- ``member_boundaries``: exact member line ranges from the real source.
- ``references``: who uses a symbol (the dependency graph).
- ``exact_source``: exact member-body recovery.
- ``editable_region``: region-level mutation authority (semantic regions).
- ``verification_hooks``: deterministic compile/test gates.

Java and Python adapt what already exists. Every other language is
UNSUPPORTED until it is certified; never "treated like" a supported one.
This is deliberately not a plugin framework: adding a language means
adding one ``LanguageAdapter`` entry here, backed by real implementations
and tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from kriya.workflow.context_source import MemberBoundary, java_member_boundaries, python_member_boundaries


class Capability(str, Enum):
    SYMBOL_IDENTITY = "symbol_identity"
    MEMBER_BOUNDARIES = "member_boundaries"
    REFERENCES = "references"
    EXACT_SOURCE = "exact_source"
    EDITABLE_REGION = "editable_region"
    VERIFICATION_HOOKS = "verification_hooks"


class CapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class LanguageAdapter:
    language: str
    extensions: Tuple[str, ...]
    capabilities: Mapping[Capability, CapabilityStatus]
    # Why each non-SUPPORTED capability is limited - reported, never inferred.
    limitations: Mapping[Capability, str]
    member_boundaries: Optional[Callable[[str], List[MemberBoundary]]]

    def status(self, capability: Capability) -> CapabilityStatus:
        return self.capabilities.get(capability, CapabilityStatus.UNSUPPORTED)

    def to_dict(self) -> Dict[str, object]:
        return {
            "language": self.language,
            "extensions": list(self.extensions),
            "capabilities": {capability.value: self.status(capability).value for capability in Capability},
            "limitations": {capability.value: text for capability, text in self.limitations.items()},
        }


JAVA_ADAPTER = LanguageAdapter(
    language="java",
    extensions=(".java",),
    capabilities={
        Capability.SYMBOL_IDENTITY: CapabilityStatus.SUPPORTED,
        Capability.MEMBER_BOUNDARIES: CapabilityStatus.SUPPORTED,
        Capability.REFERENCES: CapabilityStatus.PARTIAL,
        Capability.EXACT_SOURCE: CapabilityStatus.SUPPORTED,
        Capability.EDITABLE_REGION: CapabilityStatus.SUPPORTED,
        Capability.VERIFICATION_HOOKS: CapabilityStatus.SUPPORTED,
    },
    limitations={
        Capability.REFERENCES: "dependency-graph call/import edges are name-based, not type-resolved",
    },
    member_boundaries=java_member_boundaries,
)

PYTHON_ADAPTER = LanguageAdapter(
    language="python",
    extensions=(".py",),
    capabilities={
        Capability.SYMBOL_IDENTITY: CapabilityStatus.SUPPORTED,
        Capability.MEMBER_BOUNDARIES: CapabilityStatus.SUPPORTED,
        Capability.REFERENCES: CapabilityStatus.PARTIAL,
        Capability.EXACT_SOURCE: CapabilityStatus.SUPPORTED,
        Capability.EDITABLE_REGION: CapabilityStatus.UNSUPPORTED,
        Capability.VERIFICATION_HOOKS: CapabilityStatus.SUPPORTED,
    },
    limitations={
        Capability.REFERENCES: "dependency-graph call/import edges are name-based, not type-resolved",
        Capability.EDITABLE_REGION: "semantic-region authority covers Java only; Python edits have file-level authority",
    },
    member_boundaries=python_member_boundaries,
)

REGISTRY: Tuple[LanguageAdapter, ...] = (JAVA_ADAPTER, PYTHON_ADAPTER)
_BY_EXTENSION: Dict[str, LanguageAdapter] = {
    extension: adapter for adapter in REGISTRY for extension in adapter.extensions
}


def adapter_for(path: str) -> Optional[LanguageAdapter]:
    """The adapter for ``path``'s language, or None: unsupported, never a
    guessed nearest language."""
    return _BY_EXTENSION.get(os.path.splitext(path)[1].lower())


def capability_status(path: str, capability: Capability) -> CapabilityStatus:
    adapter = adapter_for(path)
    return adapter.status(capability) if adapter is not None else CapabilityStatus.UNSUPPORTED


def adapter_member_boundaries(path: str, content: str) -> Optional[List[MemberBoundary]]:
    """Member boundaries through the adapter contract. None means the
    language has no member-boundary capability (an explicit unsupported
    signal); an empty list means a supported file with no members."""
    adapter = adapter_for(path)
    if adapter is None or adapter.member_boundaries is None:
        return None
    if adapter.status(Capability.MEMBER_BOUNDARIES) is CapabilityStatus.UNSUPPORTED:
        return None
    return adapter.member_boundaries(content)


def capability_table() -> Dict[str, Dict[str, object]]:
    """Every registered adapter's capabilities (doctor and telemetry)."""
    return {adapter.language: adapter.to_dict() for adapter in REGISTRY}
