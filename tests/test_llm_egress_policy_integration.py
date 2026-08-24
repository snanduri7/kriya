"""MA4.3's own release-blocking regression suite (see the MA4 design doc's
section 15): proves ExecutionPolicy's audit-only integration into
kriya/core/llm.py can never weaken, override, or interfere with the
existing is_local_url/EgressViolationError enforcement, under every
condition including a misconfigured or outright broken policy engine.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.llm import EgressViolationError, LLMClient
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult


def _mock_response(content="ok"):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_response.usage = None
    return mock_response


@pytest.mark.asyncio
async def test_local_ollama_endpoint_remains_allowed():
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "http://localhost:11434/v1"
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response())
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        result = await llm.complete("system", "user")
    assert result == "ok"


@pytest.mark.asyncio
async def test_non_local_llm_endpoint_remains_rejected():
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "https://api.openai.com/v1"
    llm = LLMClient(cfg)

    with pytest.raises(EgressViolationError):
        await llm.complete("system", "user")


@pytest.mark.asyncio
async def test_policy_allow_cannot_override_local_only_enforcement():
    """Even if ExecutionPolicy.evaluate() were monkeypatched to say ALLOW
    for a remote endpoint (simulating a future MA4.6 rule misfiring), the
    real is_local_url check must still reject it."""
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "https://api.openai.com/v1"
    llm = LLMClient(cfg)

    llm.execution_policy.evaluate = MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.ALLOW,
        reason_code="TEST_FORCED_ALLOW",
        explanation="simulated misconfiguration",
    ))

    with pytest.raises(EgressViolationError):
        await llm.complete("system", "user")


@pytest.mark.asyncio
async def test_policy_misconfiguration_cannot_silently_permit_remote_inference():
    """A policy engine that raises outright (not just mis-decides) must
    still never let a remote call through - the audit call is caught and
    swallowed internally, and the real egress check still runs and still
    rejects."""
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "https://api.openai.com/v1"
    llm = LLMClient(cfg)

    llm.execution_policy.evaluate = MagicMock(side_effect=RuntimeError("policy engine broke"))

    with pytest.raises(EgressViolationError):
        await llm.complete("system", "user")


@pytest.mark.asyncio
async def test_new_policy_path_does_not_swallow_egress_violation_error():
    """The raised exception must be the real EgressViolationError, not
    something wrapped/replaced/swallowed by the new audit call."""
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "https://api.openai.com/v1"
    llm = LLMClient(cfg)

    with pytest.raises(EgressViolationError) as excinfo:
        await llm.complete("system", "user")
    assert "api.openai.com" in str(excinfo.value)


@pytest.mark.asyncio
async def test_audit_call_never_blocks_a_local_call_even_when_policy_denies():
    """ExecutionPolicy today default-denies LLM_NETWORK_ACCESS entirely
    (MA4.6's real network rules haven't landed) - a local, otherwise-legal
    call must still succeed, proving the audit result never gates
    execution."""
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "http://localhost:11434/v1"
    llm = LLMClient(cfg)

    real_result = llm.execution_policy.evaluate(
        ActionRequest(action_type=ActionType.LLM_NETWORK_ACCESS, network_target=cfg.llm.base_url)
    )
    assert real_result.decision == PolicyDecision.DENY  # confirms today's real audit signal

    mock_create = AsyncMock(return_value=_mock_response())
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        result = await llm.complete("system", "user")
    assert result == "ok"


@pytest.mark.asyncio
async def test_complete_with_tools_also_preserves_local_only_enforcement_under_forced_allow():
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "https://api.openai.com/v1"
    llm = LLMClient(cfg)

    llm.execution_policy.evaluate = MagicMock(return_value=PolicyResult(
        decision=PolicyDecision.ALLOW,
        reason_code="TEST_FORCED_ALLOW",
        explanation="simulated misconfiguration",
    ))

    with pytest.raises(EgressViolationError):
        await llm.complete_with_tools(messages=[], tools=[])


@pytest.mark.asyncio
async def test_egress_policy_disabled_still_never_lets_audit_call_raise_out():
    """With autonomy.egress_policy not set to local_only, the real
    enforcement gate is bypassed by design (unrelated to MA4) - but the
    audit call itself, even if it errors, must not propagate and break an
    otherwise-successful local call."""
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "open"
    cfg.llm.base_url = "https://api.openai.com/v1"
    llm = LLMClient(cfg)
    llm.execution_policy.evaluate = MagicMock(side_effect=RuntimeError("policy engine broke"))

    mock_create = AsyncMock(return_value=_mock_response())
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        result = await llm.complete("system", "user")
    assert result == "ok"
