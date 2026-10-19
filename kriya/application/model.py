"""ModelService (slice B2): what a run in a folder would have available, in Kriya's own words, extracted unchanged from
``kriya/kup/context_ops.py`` (``execution.context`` and ``execution.model_probe``).

``context`` is CONFIGURATION- and FILE-derived and starts nothing: no plugin is imported, no MCP server is spawned, no network is touched.
It reports the Skills Kriya would discover (metadata only - a skill's rule text may carry proprietary documentation and is never returned),
the tools configured for the folder (plugin names and MCP server NAMES with their declared capability profile - never a command, argument,
environment variable or credential), and the model each role would use. Health is deliberately NOT reported here: an MCP server's health can
only be learned by starting it, which is an operator action (``kriya mcp inspect``) and not a read.

``probe`` decides WHICH endpoints are asked and what each answer means for each role: one probe per DISTINCT (endpoint, credential), all
under ONE overall deadline. The HTTP request itself (``GET <base_url>/models``, the egress check before it, the size cap) is the caller's
``probe`` callable: APPLICATION opens no network connection (tests/architecture/test_direct_access_owners.py).
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Callable, Dict, List

from kriya.context.model import RequestContext
from kriya.foundation.redaction import safe_endpoint
from kriya.skills.service import MAX_SKILLS, SkillService

ROLES = ("planner", "architect", "developer", "reviewer", "run_verifier", "skill_gap", "spec_compliance")
ProbeEndpoint = Callable[[str, str, str], Dict[str, Any]]  # (endpoint_url, api_key, egress_policy) -> {endpoint, state, reason, models}


def routing(cfg) -> List[Dict[str, Any]]:
    """One row per role: the binding it would use and where that came from. Pure function of the resolved configuration."""
    rows = []
    for role in ROLES:
        if role == "developer":
            binding, chain, source = cfg.llm, cfg.llm_chain, "default"
        else:
            role_cfg = getattr(cfg.agent_llms, role)
            binding, chain = (role_cfg.llm or cfg.llm), (role_cfg.llm_chain or [])
            source = "override" if role_cfg.llm is not None else "default"
        rows.append({
            "role": role, "provider": binding.provider, "model": binding.model, "endpoint": safe_endpoint(binding.base_url),
            "context_window": binding.context_window, "source": source,
            "fallback": [entry.model for entry in chain],
        })
    return rows


def skills_summary(cfg, workspace: str) -> Dict[str, Any]:
    """The Skills metadata block of ``execution.context``: built from ``SkillService`` (the one owner of how a skill is described), unchanged on the wire."""
    descriptors, total = SkillService(cfg, workspace).list()
    items = [{
        "name": d.name, "description": d.description, "category": d.category, "tags": list(d.tags), "supported_versions": d.supported_versions,
        "verified": d.verified, "verified_context": d.verified_context, "verified_at": d.verified_at, "gap_acknowledged": d.verification_gap_acknowledged,
        "origin": d.origin, "rule_count": d.rule_count, "has_instructions": d.has_instructions, "example_count": d.example_count,
        "staged_rule_count": d.staged_rule_count,
    } for d in descriptors]
    return {"items": items, "total": total, "truncated": total > MAX_SKILLS, "selection": "automatic",
            "selection_note": "Kriya selects Skills automatically for each run from the goal and the repository; there is no manual selection. The Skills a run used are recorded with the run.",
            "management": "Skills are created, reviewed and promoted with `kriya skills` in a terminal; this application does not change them."}


def tools_summary(cfg) -> Dict[str, Any]:
    servers = []
    for name in sorted(cfg.mcp):
        caps = cfg.mcp[name].capabilities
        servers.append({"name": name, "declared_capabilities": {
            "workspace_read": bool(getattr(caps, "workspace_read", False)), "workspace_write": bool(getattr(caps, "workspace_write", False)),
            "network": str(getattr(caps, "network", "denied"))}})
    return {"plugins": {"directory": cfg.plugins.directory, "enabled": list(cfg.plugins.enabled)}, "mcp_servers": servers,
            "health": "not_checked",
            "management": "Tools, MCP servers and their invocation approvals are operator-managed (configuration and `kriya mcp`); this application shows them and changes nothing.",
            "health_note": "A server's health is learned only by starting it (`kriya mcp inspect`); opening a folder never starts one."}


class ModelService:
    def skills(self, ctx: RequestContext, cfg, workspace: str) -> Dict[str, Any]:
        """The Skills block of ``context`` (the caller has entered the folder: skill discovery reads the process working directory)."""
        return skills_summary(cfg, workspace)

    def context(self, ctx: RequestContext, cfg, skills: Dict[str, Any]) -> Dict[str, Any]:
        """Routing, Skills (``skills``, from ``ModelService.skills``), tools and egress of a resolved configuration."""
        return {"routing": routing(cfg), "skills": skills, "tools": tools_summary(cfg),
                "egress": {"policy": cfg.autonomy.egress_policy, "web_lookup_enabled": bool(cfg.autonomy.web_lookup_enabled and cfg.search.base_url)}}

    def probe(self, ctx: RequestContext, cfg, *, probe: ProbeEndpoint, deadline_seconds: float, max_endpoints: int) -> Dict[str, Any]:
        """Every configured binding (default, per-role overrides, fallback chain) checked against ONE probe of its distinct (endpoint,
        credential), all probes in parallel under one ``deadline_seconds``; at most ``max_endpoints`` are probed and the rest reported."""
        wanted: List[Dict[str, Any]] = []
        sources = [("default", cfg.llm.base_url, cfg.llm.api_key, cfg.llm.model)]
        for role in ROLES:
            if role == "developer":
                continue
            role_cfg = getattr(cfg.agent_llms, role)
            if role_cfg.llm is not None:
                sources.append((role, role_cfg.llm.base_url, role_cfg.llm.api_key, role_cfg.llm.model))
        for entry in cfg.llm_chain:
            sources.append(("fallback", entry.base_url, entry.api_key, entry.model))
        # one probe per distinct (endpoint, credential): two bindings that differ only in the key are judged with their own key. Probes run in parallel under ONE overall deadline.
        distinct: Dict[tuple, str] = {}
        for _who, base_url, api_key, _model in sources:
            distinct.setdefault((safe_endpoint(base_url), api_key), base_url)
        skipped = len(distinct) > max_endpoints
        chosen = dict(list(distinct.items())[:max_endpoints])
        results: Dict[tuple, Dict[str, Any]] = {}
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(chosen)), thread_name_prefix="kup-probe")
        try:
            futures = {pool.submit(probe, url, key[1], cfg.autonomy.egress_policy): key for key, url in chosen.items()}
            done, pending = concurrent.futures.wait(futures, timeout=deadline_seconds)
            for future in done:
                results[futures[future]] = future.result()
            for future in pending:  # a resolver or connect that outlived the deadline: reported, and the worker thread is left to finish on its own
                results[futures[future]] = {"endpoint": futures[future][0], "state": "unreachable", "reason": f"no answer within {deadline_seconds:g} s", "models": []}
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for who, base_url, api_key, model in sources:
            key = (safe_endpoint(base_url), api_key)
            answer = results.get(key, {"endpoint": key[0], "state": "not_probed", "reason": f"more than {max_endpoints} distinct endpoints are configured; the rest are not checked", "models": []})
            state = answer["state"]
            wanted.append({"for": who, "model": model, "endpoint": key[0],
                           "available": (model in answer["models"]) if state == "reachable" else None,
                           "state": "model_present" if state == "reachable" and model in answer["models"] else "model_missing" if state == "reachable" else state})
        endpoint_rows = [{"endpoint": k[0], "state": v["state"], "reason": v["reason"], "model_count": len(v["models"])} for k, v in results.items()]
        return {"available": True, "unavailable_reason": None, "egress_policy": cfg.autonomy.egress_policy,
                "endpoints": endpoint_rows, "roles": wanted, "timeout_seconds": deadline_seconds, "truncated": skipped,
                "note": "A single bounded GET /models per distinct local endpoint, made now. It proves the server answered and lists the models it serves; it does not prove a model can complete a request."}


__all__ = ["ModelService", "ProbeEndpoint", "ROLES", "routing", "skills_summary", "tools_summary"]
