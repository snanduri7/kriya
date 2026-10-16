"""KUP ``execution.context`` and ``execution.model_probe``: what a run in a folder would have available, in Kriya's own words (end-user experience).

Transport over ``kriya.application.ModelService`` (full modularization B2): argument validation, the folder's configuration resolved exactly as
``execution.preflight`` does (``_load_in``: the folder is the process working directory, so what is shown is what a run would use), entering the
folder around skill discovery, and the envelope. ``execution.context`` starts nothing: no plugin is imported, no MCP server is spawned, no network
is touched.

``execution.model_probe`` is the one bounded network read. The HTTP request lives here (``_probe``: one ``GET <base_url>/models`` per DISTINCT local
endpoint - the exact probe ``kriya doctor`` makes, 3 s timeout, response capped - refused, never attempted, for an endpoint that is not local under
the ``local_only`` egress policy; credentials are never sent to a refused endpoint and never returned). Which endpoints are asked, the overall
deadline and what each answer means per role are ``ModelService.probe``.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict

from kriya.application import ModelService, safe_endpoint
from kriya.context import RequestContext
from kriya.core.llm import is_local_url
from kriya.kup.execution import _CWD_LOCK, _load_in
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.workspace_ops import validated_workspace

PROBE_TIMEOUT_SECONDS = 3.0
PROBE_DEADLINE_SECONDS = 4.0  # the WHOLE probe, however many endpoints: the persistent service answers one request at a time, so this read must be short
PROBE_MAX_ENDPOINTS = 6
PROBE_MAX_BYTES = 1024 * 1024
MAX_PROBED_MODELS_LISTED = 200


def context(cfg, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    _requested, workspace = validated_workspace(request)
    _state, wcfg, authority, failure = _load_in(workspace)
    ctx = RequestContext.local(workspace, request_id=request_id)
    data: Dict[str, Any] = {"workspace": {"path": workspace, "name": os.path.basename(workspace.rstrip(os.sep)) or workspace, "identity": ctx.workspace.workspace_id},
                            "available": wcfg is not None, "unavailable_reason": failure, "authority_status": authority["status"]}
    if wcfg is None:
        data.update(routing=[], skills=None, tools=None, egress=None)
    else:
        with _CWD_LOCK:  # skill discovery also looks at the process working directory (skills/ beside the configuration)
            previous = os.getcwd()
            os.chdir(workspace)
            try:
                skills = ModelService().skills(ctx, wcfg, workspace)
            finally:
                os.chdir(previous)
        data.update(ModelService().context(ctx, wcfg, skills))
    return envelope("execution.context", request_id, data=data, source=None, consistency=live_consistency())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is never followed: the egress check judged the CONFIGURED endpoint only, and a followed redirect would reach an unchecked host with the credential."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _probe(endpoint_url: str, api_key: str, egress_policy: str) -> Dict[str, Any]:
    safe = safe_endpoint(endpoint_url)
    if egress_policy == "local_only" and not is_local_url(endpoint_url):
        return {"endpoint": safe, "state": "not_probed", "reason": "the endpoint is not local and the egress policy is local_only", "models": []}
    url = f"{endpoint_url.rstrip('/')}/models"
    request = urllib.request.Request(url=url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with _OPENER.open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310 - scheme/host gated by the egress check above; redirects are refused
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
    # _probe, PROBE_DEADLINE_SECONDS and PROBE_MAX_ENDPOINTS are read here, at call time (the transport's bounds; tests patch them on this module)
    data = ModelService().probe(RequestContext.local(workspace, request_id=request_id), wcfg, probe=_probe, deadline_seconds=PROBE_DEADLINE_SECONDS,
                                max_endpoints=PROBE_MAX_ENDPOINTS)
    data["authority_status"] = authority["status"]
    return envelope("execution.model_probe", request_id, data=data, source=None, consistency=live_consistency())
