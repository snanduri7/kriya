"""KUP ``execution.context`` and ``execution.model_probe``: what a run in a folder would have available, in Kriya's own words (end-user experience).

``execution.context`` is CONFIGURATION- and FILE-derived and starts nothing: no plugin is imported, no MCP server is spawned, no network is touched.
It reports the Skills Kriya would discover (metadata only - a skill's rule text may carry proprietary documentation and is never returned), the tools
configured for the folder (plugin names and MCP server NAMES with their declared capability profile - never a command, argument, environment variable or
credential), and the model each role would use. Health is deliberately NOT reported here: an MCP server's health can only be learned by starting it, which is
an operator action (``kriya mcp inspect``) and not a read.

``execution.model_probe`` is the one bounded network read: one ``GET <base_url>/models`` per DISTINCT local endpoint (the exact probe ``kriya doctor`` makes,
3 s timeout, response capped), refused - never attempted - for an endpoint that is not local under the ``local_only`` egress policy. Credentials are never
sent to a refused endpoint and never returned.

Both resolve the configuration exactly as ``execution.preflight`` does (the folder is the process working directory), so what is shown is what a run would use.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from kriya.control.workspace_identity import workspace_identity
from kriya.core.llm import is_local_url
from kriya.kup.execution import _CWD_LOCK, _load_in
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.workspace_ops import validated_workspace

ROLES = ("planner", "architect", "developer", "reviewer", "run_verifier", "skill_gap", "spec_compliance")
MAX_SKILLS = 500
PROBE_TIMEOUT_SECONDS = 3.0
PROBE_MAX_BYTES = 1024 * 1024
MAX_PROBED_MODELS_LISTED = 200


def _safe_endpoint(url: str) -> str:
    """The endpoint with any ``user:password@`` removed - an endpoint is shown, a credential never is."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return "(unparseable endpoint)"


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
            "role": role, "provider": binding.provider, "model": binding.model, "endpoint": _safe_endpoint(binding.base_url),
            "context_window": binding.context_window, "source": source,
            "fallback": [entry.model for entry in chain],
        })
    return rows


def _staged_rule_count(skill_dir: Optional[str]) -> int:
    if not skill_dir:
        return 0
    try:
        with open(os.path.join(skill_dir, "staged_rules.txt"), "r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def skills_summary(cfg, workspace: str) -> Dict[str, Any]:
    from kriya.skills.skill import SkillEngine, get_global_skills_dir

    engine = SkillEngine.from_config(cfg, workspace_path=workspace)
    engine.discover_and_load()
    shared = os.path.realpath(get_global_skills_dir())
    items = []
    for skill in sorted(engine.list_skills(), key=lambda s: s.name.lower())[:MAX_SKILLS]:
        path = os.path.realpath(skill.source_path) if skill.source_path else None
        origin = "kriya_install" if path and (path == shared or path.startswith(shared + os.sep)) else "workspace_or_configured"
        items.append({
            "name": skill.name, "description": skill.description, "category": skill.category, "tags": list(skill.tags)[:20],
            "supported_versions": skill.supported_versions, "verified": skill.verified, "verified_context": skill.verified_context,
            "verified_at": skill.verified_at, "gap_acknowledged": skill.verification_gap_acknowledged, "origin": origin,
            "rule_count": len(skill.rules), "has_instructions": bool(skill.instructions.strip()), "example_count": len(skill.examples),
            "staged_rule_count": _staged_rule_count(skill.source_path),
        })
    total = len(engine.list_skills())
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


def context(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    _requested, workspace = validated_workspace(request)
    _state, wcfg, authority, failure = _load_in(workspace)
    data: Dict[str, Any] = {"workspace": {"path": workspace, "name": os.path.basename(workspace.rstrip(os.sep)) or workspace, "identity": workspace_identity(workspace)},
                            "available": wcfg is not None, "unavailable_reason": failure, "authority_status": authority["status"]}
    if wcfg is None:
        data.update(routing=[], skills=None, tools=None, egress=None)
    else:
        with _CWD_LOCK:  # skill discovery also looks at the process working directory (skills/ beside the configuration)
            previous = os.getcwd()
            os.chdir(workspace)
            try:
                skills = skills_summary(wcfg, workspace)
            finally:
                os.chdir(previous)
        data.update(routing=routing(wcfg), skills=skills, tools=tools_summary(wcfg),
                    egress={"policy": wcfg.autonomy.egress_policy, "web_lookup_enabled": bool(wcfg.autonomy.web_lookup_enabled and wcfg.search.base_url)})
    return envelope("execution.context", request_id, data=data, source=None, consistency=live_consistency())


def _probe(endpoint_url: str, api_key: str, egress_policy: str) -> Dict[str, Any]:
    safe = _safe_endpoint(endpoint_url)
    if egress_policy == "local_only" and not is_local_url(endpoint_url):
        return {"endpoint": safe, "state": "not_probed", "reason": "the endpoint is not local and the egress policy is local_only", "models": []}
    url = f"{endpoint_url.rstrip('/')}/models"
    request = urllib.request.Request(url=url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310 - scheme/host gated by the egress check above
            body = response.read(PROBE_MAX_BYTES + 1)
            status = response.getcode()
        if len(body) > PROBE_MAX_BYTES:
            return {"endpoint": safe, "state": "unreadable", "reason": "the model list is larger than the allowed size", "models": []}
        payload = json.loads(body.decode("utf-8", errors="replace"))
        listed = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)] if isinstance(payload, dict) else []
        return {"endpoint": safe, "state": "reachable" if status == 200 else "error", "reason": None if status == 200 else f"HTTP {status}", "models": listed[:MAX_PROBED_MODELS_LISTED]}
    except urllib.error.HTTPError as error:
        return {"endpoint": safe, "state": "error", "reason": f"HTTP {error.code}", "models": []}
    except urllib.error.URLError as error:
        return {"endpoint": safe, "state": "unreachable", "reason": str(error.reason)[:200], "models": []}
    except (OSError, ValueError) as error:
        return {"endpoint": safe, "state": "unreadable", "reason": f"{type(error).__name__}", "models": []}


def model_probe(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    _requested, workspace = validated_workspace(request)
    _state, wcfg, authority, failure = _load_in(workspace)
    if wcfg is None:
        return envelope("execution.model_probe", request_id, data={"available": False, "unavailable_reason": failure, "authority_status": authority["status"], "endpoints": [], "roles": []},
                        source=None, consistency=live_consistency())
    bindings: Dict[str, Dict[str, Any]] = {}  # per DISTINCT endpoint: one probe; the key never contains a credential
    wanted: List[Dict[str, Any]] = []
    sources = [("default", wcfg.llm.base_url, wcfg.llm.api_key, wcfg.llm.model)]
    for role in ROLES:
        if role == "developer":
            continue
        role_cfg = getattr(wcfg.agent_llms, role)
        if role_cfg.llm is not None:
            sources.append((role, role_cfg.llm.base_url, role_cfg.llm.api_key, role_cfg.llm.model))
    for entry in wcfg.llm_chain:
        sources.append(("fallback", entry.base_url, entry.api_key, entry.model))
    for who, base_url, api_key, model in sources:
        key = _safe_endpoint(base_url)
        if key not in bindings:
            bindings[key] = _probe(base_url, api_key, wcfg.autonomy.egress_policy)
        probe = bindings[key]
        state = probe["state"]
        wanted.append({"for": who, "model": model, "endpoint": key,
                       "available": (model in probe["models"]) if state == "reachable" else None,
                       "state": "model_present" if state == "reachable" and model in probe["models"] else "model_missing" if state == "reachable" else state})
    data = {"available": True, "unavailable_reason": None, "authority_status": authority["status"], "egress_policy": wcfg.autonomy.egress_policy,
            "endpoints": [{"endpoint": k, "state": v["state"], "reason": v["reason"], "model_count": len(v["models"])} for k, v in bindings.items()],
            "roles": wanted, "timeout_seconds": PROBE_TIMEOUT_SECONDS,
            "note": "A single bounded GET /models per distinct local endpoint, made now. It proves the server answered and lists the models it serves; it does not prove a model can complete a request."}
    return envelope("execution.model_probe", request_id, data=data, source=None, consistency=live_consistency())

