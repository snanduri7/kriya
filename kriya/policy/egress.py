"""PRD-012: one vocabulary for outbound-network authority.

Kriya's network authority is decided by several independent owners, each
with its own enum or flag:
- kriya/tools/containment.py::NetworkAuthority: contained processes (SEC-001/005/006);
- kriya/mcp/capability.py::MCPNetworkAuthority: MCP servers (TOOL-003);
- autonomy.egress_policy: model endpoints (LLMClient, embeddings);
- autonomy.web_lookup_enabled / search.base_url: live lookup;
- knowledge.offline_mode: registry release metadata.

This module does not replace any of them and decides nothing itself, with one
exception (B2, Wave-2): the two live-lookup decisions that several owners used
to recompute from partial settings - ``web_lookup_allowed`` and
``dependency_lookup_decision`` below - live here so every consumer and the
egress report (kriya/workflow/egress_authority.py) ask the same function.
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



def web_lookup_allowed(cfg: Any) -> bool:
    """The one decision for live (search) lookup: the operator enabled it AND configured the search endpoint. Every consumer
    (the stages, the retry loop, the application projection, this report) asks here; unreadable policy state is a refusal."""
    try:
        return bool(cfg.autonomy.web_lookup_enabled and cfg.search.base_url)
    except AttributeError:
        return False


# B2: the Maven Central class/coordinate lookup of the javac/Maven compile gates and the resolve_missing_dependency tool
# (kriya/tools/resolver.py). It runs in the Kriya process against this fixed public host - not inside containment.
DEPENDENCY_LOOKUP_HOSTS = ("search.maven.org",)


def dependency_lookup_decision(cfg: Any) -> EgressDecision:
    """B2: the ONE decision whether a compile gate or the repair tool may contact the public dependency registry. Allowed only when
    the egress policy is open, live lookup is enabled and configured (``web_lookup_allowed``) and ``knowledge.offline_mode`` is off.
    The gates consume this decision (``PolymorphicValidator.dependency_lookup_allowed``) and the egress report records it, so what
    runs and what is reported cannot differ. Missing or unreadable policy state denies."""
    try:
        refusals = []
        if cfg.autonomy.egress_policy != "unrestricted":
            refusals.append("autonomy.egress_policy is not unrestricted")
        if not web_lookup_allowed(cfg):
            refusals.append("live lookup is not enabled and configured (autonomy.web_lookup_enabled + search.base_url)")
        if cfg.knowledge.offline_mode:
            refusals.append("knowledge.offline_mode")
    except AttributeError:
        refusals = ["policy state unavailable"]
    allowed = not refusals
    return EgressDecision(
        channel="dependency_lookup",
        capability=EgressCapability.APPROVED_PUBLIC_KNOWLEDGE if allowed else EgressCapability.DENIED,
        allowed=allowed,
        authority_source="autonomy.egress_policy + autonomy.web_lookup_enabled + search.base_url + knowledge.offline_mode",
        destinations=DEPENDENCY_LOOKUP_HOSTS if allowed else (),
        reason=("compile-gate and repair-tool class lookups against the public dependency registry; sanitized class names only"
                if allowed else "refused: " + "; ".join(refusals)),
    )


__all__ = ["DEPENDENCY_LOOKUP_HOSTS", "EgressCapability", "EgressDecision", "capability_for", "dependency_lookup_decision",
           "web_lookup_allowed"]
