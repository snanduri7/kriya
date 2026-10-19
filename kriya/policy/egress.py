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
tools -> policy dependency direction is preserved). The one exception is
``is_local_url`` (moved here unchanged from kriya/core/llm.py in slice C3):
the local/private-address definition that both the hard egress boundary in
kriya/core/llm.py and ExecutionPolicy's egress stage use; it resolves the
host name (``socket.getaddrinfo``) and fails closed on anything it cannot
resolve or parse.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse

from kriya.foundation.redaction import redact_text

logger = logging.getLogger(__name__)


def is_local_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            # Fail CLOSED, not open - found live, 2026-08-12 (SME architecture
            # review): a malformed/typo'd base_url (e.g. missing "http://" in
            # a config file) parses with hostname=None via urlparse, and this
            # branch was previously treating that as local/allowed - the
            # opposite of the fail-closed behavior the except block below
            # already implements for every OTHER failure mode. This function
            # is a hard safety boundary (see kriya/core/llm.py's egress
            # enforcement, and CLAUDE.md's "Egress control" section) - an
            # unparseable hostname is exactly the kind of ambiguous input
            # that should never be treated as "safe."
            logger.debug(f"is_local_url check found no hostname for '{url}', treating as non-local (fail closed)")
            return False

        if hostname.lower() in {"localhost", "127.0.0.1", "[::1]"}:
            return True
        if hostname.lower().endswith(".local"):
            return True
            
        addr_info = socket.getaddrinfo(hostname, None)
        for _family, _, _, _, sockaddr in addr_info:
            ip = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
                continue
            else:
                return False
        return True
    except Exception as e:
        logger.debug(f"is_local_url check failed for '{url}', treating as non-local (fail closed): {e}")
        return False


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
            "destinations": [redact_text(d) for d in self.destinations],
            "reason": redact_text(self.reason),
        }
        if self.containment is not None:
            record["containment"] = dict(self.containment)
        return record


__all__ = ["EgressCapability", "EgressDecision", "capability_for"]
