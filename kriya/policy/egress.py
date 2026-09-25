"""PRD-012: one vocabulary for outbound-network authority.

Kriya's network authority is decided by several independent owners, each
with its own enum or flag:
- kriya/tools/containment.py::NetworkAuthority: contained processes (SEC-001/005/006);
- kriya/mcp/capability.py::MCPNetworkAuthority: MCP servers (TOOL-003);
- autonomy.egress_policy: model endpoints (LLMClient, embeddings);
- autonomy.web_lookup_enabled / search.base_url: live lookup;
- knowledge.offline_mode: registry release metadata.

This module does not replace any of them and never decides anything itself.
It names the one capability class each owner's decision belongs to, so
every decision can be recorded, compared and audited the same way.

The existing enums are deliberately NOT extended with new members:
OCIContainmentBackend.prepare() has no exhaustive-match guard, so a shared
new member could silently fall back to UNRESTRICTED. The mapping here keys
on each enum's ``value`` and raises on anything it does not know. A test
iterates every member of every source enum, so a new member cannot go
unmapped.

Pure: no I/O, and no imports outside kriya.policy (the mcp -> policy and
tools -> policy dependency direction is preserved).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from kriya.policy.telemetry import scrub_potential_secrets


class EgressCapability(str, Enum):
    """The capability classes PRD-012 names. Order is least to most authority."""

    DENIED = "denied"
    # Package registries only, via the SEC-006 scoped proxy: the hosts are
    # autonomy.acquisition_registry_hosts, never repository/model content.
    REGISTRY_ONLY = "registry_only"
    # Public, read-only knowledge (live lookup results, registry release
    # metadata) behind an explicit opt-in; queries are sanitized.
    APPROVED_PUBLIC_KNOWLEDGE = "approved_public_knowledge"
    # Exactly the destinations a trusted configuration names (model
    # endpoints, an MCP server's network_hosts).
    EXPLICIT_DESTINATIONS = "explicit_destinations"
    # Any destination: host execution, or an explicitly privileged setting.
    UNRESTRICTED = "unrestricted"


# Every value any owner's enum/flag uses, to its class. Total over the
# known values; an unknown value raises instead of guessing.
_CAPABILITY_BY_VALUE: Dict[str, EgressCapability] = {
    "denied": EgressCapability.DENIED,
    "dependency_registry_only": EgressCapability.REGISTRY_ONLY,
    "registry_only": EgressCapability.REGISTRY_ONLY,
    "approved_public_knowledge": EgressCapability.APPROVED_PUBLIC_KNOWLEDGE,
    "explicit_destinations": EgressCapability.EXPLICIT_DESTINATIONS,
    "unrestricted": EgressCapability.UNRESTRICTED,
}


def capability_for(authority: Any) -> EgressCapability:
    """The class of a NetworkAuthority / MCPNetworkAuthority member (or its
    string value). Raises ValueError on an unmapped value - never a default."""
    value = getattr(authority, "value", authority)
    try:
        return _CAPABILITY_BY_VALUE[value]
    except (KeyError, TypeError):
        raise ValueError(f"No egress capability class is defined for network authority {authority!r}") from None


@dataclass(frozen=True)
class EgressDecision:
    """One recorded authority decision for one outbound channel.

    ``authority_source`` names the trusted configuration field(s) (or fixed
    platform policy) the destinations come from - never repository or model
    content. ``containment`` is the containment identity the channel runs
    under, when it runs in one."""

    channel: str
    capability: EgressCapability
    allowed: bool
    authority_source: str
    destinations: Tuple[str, ...] = ()
    reason: str = ""
    containment: Optional[Mapping[str, Any]] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "channel": self.channel,
            "capability": self.capability.value,
            "allowed": self.allowed,
            "authority_source": self.authority_source,
            "destinations": [scrub_potential_secrets(d) for d in self.destinations],
            "reason": scrub_potential_secrets(self.reason),
        }
        if self.containment is not None:
            record["containment"] = dict(self.containment)
        return record


__all__ = ["EgressCapability", "EgressDecision", "capability_for"]
