"""MA4.6's own regression requirement, mirroring MA4.3/4.4/4.5:
ExecutionPolicy's audit-only integration into kriya/tools/web.py's
fetch_url_text must never affect whether the real fetch happens (or is
refused by the existing SSRF guard), under any condition including a
misconfigured or outright broken policy engine.
"""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import kriya.tools.web as web_mod
from kriya.policy.model import PolicyDecision, PolicyResult
from kriya.tools.web import fetch_url_text


@pytest.mark.asyncio
async def test_existing_ssrf_guard_still_refuses_unsafe_url_regardless_of_policy():
    """A local/private target is exactly what MA4.6's own network-egress
    stage would ALLOW - but this file's _is_safe_external_url guard is the
    real, independent enforcement and must still refuse it."""
    with pytest.raises(ValueError, match="not a safe external"):
        await fetch_url_text("http://127.0.0.1:8080/")


@pytest.mark.asyncio
async def test_a_broken_policy_engine_never_blocks_a_real_safe_fetch(monkeypatch):
    monkeypatch.setattr(web_mod._execution_policy, "evaluate", MagicMock(side_effect=RuntimeError("broke")))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>hi</body></html>")

    transport = httpx.MockTransport(handler)
    orig_async_client = web_mod.httpx.AsyncClient
    try:
        web_mod.httpx.AsyncClient = lambda **kw: orig_async_client(transport=transport, **kw)
        result = await fetch_url_text("https://8.8.8.8/")
    finally:
        web_mod.httpx.AsyncClient = orig_async_client
    assert "hi" in result


@pytest.mark.asyncio
async def test_a_forced_allow_never_relaxes_the_real_ssrf_guard(monkeypatch):
    """Even if ExecutionPolicy were monkeypatched to ALLOW a local target
    (simulating a future misconfiguration), the real guard must still
    refuse it - MA4.6 is audit-only, never enforcement."""
    monkeypatch.setattr(web_mod._execution_policy, "evaluate", MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.ALLOW, reason_code="TEST_FORCED_ALLOW", explanation="simulated",
    )))
    with pytest.raises(ValueError, match="not a safe external"):
        await fetch_url_text("http://127.0.0.1:8080/")


@pytest.mark.asyncio
async def test_audit_call_observes_the_real_url(monkeypatch):
    captured = {}
    real_evaluate = web_mod._execution_policy.evaluate

    def spy(request):
        captured["network_target"] = request.network_target
        return real_evaluate(request)

    monkeypatch.setattr(web_mod._execution_policy, "evaluate", spy)
    with pytest.raises(ValueError):
        await fetch_url_text("http://127.0.0.1:8080/")
    assert captured["network_target"] == "http://127.0.0.1:8080/"
