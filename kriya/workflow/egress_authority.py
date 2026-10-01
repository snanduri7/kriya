"""PRD-012: the per-run egress authority record.

``describe_run_egress(cfg)`` lists, for every outbound channel Kriya has, the
capability class it runs under (kriya/policy/egress.py), whether it is
allowed, the destinations, and the trusted configuration field (or fixed
platform policy) they come from. It is derived only from configuration:
repository or model content can request a destination, but never appears
as an authority source.

The workflow records it once per run as the persisted ``egress.authority``
run event. The real enforcement stays with each channel's own owner
(LLMClient/OllamaEmbeddingClient egress checks, ContainmentProfile network
authority, TOOL-003 MCP containment, the live-lookup approval gate); this
module describes their configured decisions and changes none of them.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Tuple

from kriya.policy.egress import EgressCapability, EgressDecision, capability_for


def _model_endpoints(cfg: Any) -> Iterable[Tuple[str, str]]:
    yield "llm.base_url", cfg.llm.base_url
    for index, fallback in enumerate(cfg.llm_chain):
        yield f"llm_chain[{index}].base_url", fallback.base_url
    for role, role_cfg in cfg.agent_llms:
        if role_cfg.llm is not None:
            yield f"agent_llms.{role}.llm.base_url", role_cfg.llm.base_url
        for index, fallback in enumerate(role_cfg.llm_chain):
            yield f"agent_llms.{role}.llm_chain[{index}].base_url", fallback.base_url
    yield "embedding.base_url", cfg.embedding.base_url


def _model_decision(cfg: Any, field: str, url: str) -> EgressDecision:
    from kriya.core.llm import is_local_url

    local_only = cfg.autonomy.egress_policy == "local_only"
    allowed = not local_only or is_local_url(url)
    return EgressDecision(
        channel="model_endpoint", capability=EgressCapability.EXPLICIT_DESTINATIONS, allowed=allowed,
        authority_source=field, destinations=(url,),
        reason=(
            "egress_policy local_only: loopback/private endpoints only" if local_only
            else f"egress_policy {cfg.autonomy.egress_policy}"
        ) + ("" if allowed else "; this endpoint is not local and every call to it is refused"),
    )


def describe_run_egress(cfg: Any) -> List[EgressDecision]:
    from kriya.tools.knowledge import REGISTRY_METADATA_HOSTS

    autonomy = cfg.autonomy
    contained = autonomy.contained_execution_required is True
    containment = {"required": contained, "backend": autonomy.containment_backend}
    decisions = [_model_decision(cfg, field, url) for field, url in _model_endpoints(cfg)]

    offline = cfg.knowledge.offline_mode
    decisions.append(EgressDecision(
        channel="registry_metadata",
        capability=EgressCapability.DENIED if offline else EgressCapability.APPROVED_PUBLIC_KNOWLEDGE,
        allowed=not offline, authority_source="knowledge.offline_mode (fixed platform registry hosts)",
        destinations=() if offline else REGISTRY_METADATA_HOSTS,
        reason="KnowledgeGuard release-date lookups; cache only" if offline
        else "KnowledgeGuard release-date lookups for libraries named in the goal",
    ))

    lookup = bool(autonomy.web_lookup_enabled and cfg.search.base_url)
    decisions.append(EgressDecision(
        channel="web_lookup",
        capability=EgressCapability.APPROVED_PUBLIC_KNOWLEDGE if lookup else EgressCapability.DENIED,
        allowed=lookup, authority_source="autonomy.web_lookup_enabled + search.base_url",
        destinations=(cfg.search.base_url,) if lookup else (),
        reason="sanitized public terms only, per-query approval; fetched pages must be public addresses"
        if lookup else "live lookup disabled",
    ))

    decisions.append(EgressDecision(
        channel="verification_execution",
        capability=EgressCapability.DENIED if contained else EgressCapability.UNRESTRICTED,
        allowed=not contained, authority_source="autonomy.contained_execution_required",
        reason="compile/test/run of generated code: no network inside containment" if contained
        else "host execution has the host's network",
        containment=containment,
    ))
    decisions.append(EgressDecision(
        channel="dependency_acquisition",
        capability=EgressCapability.REGISTRY_ONLY if contained else EgressCapability.UNRESTRICTED,
        allowed=True, authority_source="autonomy.acquisition_registry_hosts" if contained
        else "autonomy.contained_execution_required (host execution)",
        destinations=tuple(sorted(set(autonomy.acquisition_registry_hosts))) if contained else (),
        reason="registry-scoped proxy; every other host is refused" if contained
        else "host execution has the host's network",
        containment=containment,
    ))
    shell_denied = autonomy.shell_network == "denied"
    shell_capability = (
        EgressCapability.DENIED if shell_denied
        else EgressCapability.UNRESTRICTED
    )
    decisions.append(EgressDecision(
        channel="shell_tool",
        capability=shell_capability, allowed=not shell_denied,
        authority_source="autonomy.shell_network",
        reason=(
            "non-package-manager shell commands have no network; recognized package managers are "
            "registry-scoped (contained) or denied"
        ) if shell_denied else "privileged: shell commands keep the network of their execution environment",
        containment=containment,
    ))
    mcp_contained = autonomy.mcp_contained_execution_required is True
    for name, server in sorted(cfg.mcp.items()):
        declared = capability_for(server.capabilities.network)
        unenforceable = mcp_contained and declared is EgressCapability.EXPLICIT_DESTINATIONS
        decisions.append(EgressDecision(
            channel=f"mcp_server:{name}",
            capability=declared if mcp_contained else EgressCapability.UNRESTRICTED,
            allowed=not unenforceable and (declared is not EgressCapability.DENIED or not mcp_contained),
            authority_source=f"mcp.{name}.capabilities.network",
            destinations=tuple(server.capabilities.network_hosts) if mcp_contained else (),
            reason=(
                "explicit_destinations is not enforceable for MCP yet: the server is refused (TOOL-003)"
                if unenforceable else "enforced at the OCI boundary" if mcp_contained
                else "host execution: the declared profile is not enforced"
            ),
            containment={"required": mcp_contained, "backend": autonomy.containment_backend},
        ))
    return decisions


def egress_authority_details(cfg: Any) -> dict:
    return {"decisions": [decision.to_dict() for decision in describe_run_egress(cfg)]}


__all__ = ["describe_run_egress", "egress_authority_details"]
