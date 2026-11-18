"""DISPATCH-SETTINGS-IDENTITY-001 (Stage B run 4, F2): the inference-settings
identity a request is ADMITTED under is the identity its binding is QUALIFIED
under.

MEASURED (run 20261118T111000-acc9061c): a fallback binding that declares its
window with the provider-neutral ``context_window`` (no ``extra_body``) was
qualified under settings digest sha256:68eec1fd7261 and refused at dispatch
under sha256:c4f0fd5e0d03 (MODEL_BINDING_NOT_QUALIFIED). The dispatch path
computed the settings from the request body AFTER the declared window had been
translated into the runtime's request option (FALLBACK-CONTEXT-WINDOW-001);
qualification and every other reader compute them from the binding's own
settings. The window is runtime identity (the fingerprint), not an inference
setting; the request still carries it.
"""
from unittest.mock import patch

import pytest
from test_prd017_fallback_transition import FALLBACK, PRIMARY, _cfg, _exact_ollama

from kriya.core import model_admission as admission
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.inference_runtime import runtime_adapter
from kriya.core.inference_settings import (
    binding_inference_settings,
    request_settings,
    role_inference_identities,
    role_inference_settings,
)
from kriya.core.llm import LLMClient
from kriya.core.role_metrics import model_role

# The run 4 shape: the window is declared once, provider-neutrally; no extra_body.
DECLARED_ONLY = dict(context_window=8192, extra_body={})
BINDING_SHAPES = {
    "declared_window_only": DECLARED_ONLY,
    "explicit_option_only": dict(extra_body={"options": {"num_ctx": 8192}}),
    "declared_and_explicit": dict(context_window=8192, extra_body={"options": {"num_ctx": 8192}}),
    "declared_window_with_sampling": dict(context_window=8192, extra_body={"options": {"top_p": 0.8}}),
}


def _production(cfg):
    cfg.runtime_profile = "production"
    return cfg


def _qualify(cfg, model, settings):
    runtime = model_runtime.resolve_configured_model_runtime(cfg, model)
    mq.save_record(mq.build_record(runtime, [mq.CaseResult(c, mq.PASS) for c in mq.CAPABILITIES], settings=settings))


def _fallback_call(cfg):
    fallback = cfg.llm_chain[0]
    # Exactly what attempt.py passes on an escalation (its own extra_body, empty here).
    return dict(model_override=fallback.model, base_url_override=fallback.base_url,
                api_key_override=fallback.api_key, extra_body_override=fallback.extra_body)


def _native(cfg):
    """The run 4 runtime: Ollama's native API carries the window per request,
    so a declared ``context_window`` is translated into the request body."""
    cfg.llm.inference_runtime = "ollama_native"
    for binding in cfg.llm_chain:
        binding.inference_runtime = "ollama_native"
    return cfg


class _Admitted(Exception):
    """Raised by the spy right after the REAL admission passed: the call's
    identity was computed and admitted; no transport is needed to see it."""

    def __init__(self, settings, sent_body):
        super().__init__("admitted")
        self.settings, self.sent_body = settings, sent_body


async def _dispatch(llm, *, tools=False, **kwargs):
    """Run one call up to and including the real ``LLMClient._admit``.
    Returns (admitted settings, the body the request carries); a production
    refusal propagates as ModelAdmissionError."""
    real_admit, real_fingerprint, sent = llm._admit, llm._runtime_fingerprint, {}

    async def fingerprint(model, base_url, api_key, extra_body):
        sent["body"] = extra_body
        return await real_fingerprint(model, base_url, api_key, extra_body)

    def admit(model, fingerprint_, settings):
        real_admit(model, fingerprint_, settings)
        raise _Admitted(settings, sent.get("body"))

    with patch.object(llm, "_runtime_fingerprint", new=fingerprint), patch.object(llm, "_admit", new=admit):
        with pytest.raises(_Admitted) as admitted:
            if tools:
                await llm.complete_with_tools_result([{"role": "user", "content": "u"}], [], **kwargs)
            else:
                await llm.complete_result("s", "u", **kwargs)
    return admitted.value.settings, admitted.value.sent_body


async def _refused(llm, **kwargs):
    with pytest.raises(admission.ModelAdmissionError) as refused:
        await _dispatch(llm, **kwargs)
    assert refused.value.reason_code == admission.MODEL_BINDING_NOT_QUALIFIED
    return refused.value.details


@pytest.mark.asyncio
async def test_the_run4_fallback_shape_dispatches_on_its_qualified_identity(monkeypatch):
    """The reproducer: native runtime, window declared only, qualified exactly
    as `kriya model qualify` keys it (the enumerated identity) -> the
    escalation is admitted (it was refused MODEL_BINDING_NOT_QUALIFIED)."""
    _exact_ollama(monkeypatch)
    cfg = _production(_native(_cfg(**DECLARED_ONLY)))
    qualified = role_inference_settings(cfg, "developer", FALLBACK)
    _qualify(cfg, FALLBACK, qualified)
    with model_role("developer"):
        settings, sent_body = await _dispatch(LLMClient(cfg), **_fallback_call(cfg))
    assert settings.digest == qualified.digest
    assert "options" not in settings.extra_body
    # The declared window is still requested on the wire: only the settings identity excludes it.
    assert sent_body["options"]["num_ctx"] == 8192


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True], ids=["v1", "native"])
@pytest.mark.parametrize("shape", sorted(BINDING_SHAPES))
@pytest.mark.parametrize("target", ["primary", "fallback"])
async def test_enumerated_and_dispatched_identity_are_one_digest_for_every_binding_shape(
        monkeypatch, native, shape, target):
    """Qualification (role_inference_identities) and dispatch (LLMClient)
    produce the identical digest from the identical resolved binding, on the
    completion path and (primary: the fixture fallback has no native tools)
    the native-tool path."""
    _exact_ollama(monkeypatch)
    cfg = _cfg(**BINDING_SHAPES[shape])
    if native:
        _native(cfg)
    if target == "primary":
        cfg.llm.context_window = BINDING_SHAPES[shape].get("context_window", cfg.llm.context_window)
        cfg.llm.extra_body = dict(BINDING_SHAPES[shape].get("extra_body") or {})
        model, call = PRIMARY, {}
    else:
        model, call = FALLBACK, _fallback_call(cfg)
    (label, enumerated), = role_inference_identities(cfg, "developer", model)
    assert label == "normal"
    llm = LLMClient(cfg)
    with model_role("developer"):
        completed, _body = await _dispatch(llm, **call)
        assert completed.digest == enumerated.digest
        if target == "primary":
            tooled, _body = await _dispatch(llm, tools=True, **call)
            assert tooled.digest == enumerated.digest


@pytest.mark.asyncio
async def test_one_material_settings_change_is_still_refused(monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _production(_native(_cfg(**DECLARED_ONLY)))
    _qualify(cfg, FALLBACK, role_inference_settings(cfg, "developer", FALLBACK))
    llm, call = LLMClient(cfg), _fallback_call(cfg)
    with model_role("developer"):
        await _dispatch(llm, **call)
        warmer = await _refused(llm, **{**call, "temperature_override": 0.9})
        sampled = await _refused(llm, **{**call, "extra_body_override": {"options": {"top_p": 0.3}}})
        reasoning = await _refused(llm, **{**call, "reasoning_override": True})
    assert {warmer["status"], sampled["status"], reasoning["status"]} == {mq.MISSING}
    assert len({warmer["inference_settings_digest"], sampled["inference_settings_digest"],
                reasoning["inference_settings_digest"]}) == 3


@pytest.mark.asyncio
async def test_the_window_stays_identity_through_the_runtime_fingerprint(monkeypatch):
    """Not a widening: a record made at one declared window never admits the
    same binding at another (the fingerprint differs), although the settings
    digest does not carry a declared window."""
    _exact_ollama(monkeypatch)
    cfg = _production(_native(_cfg(**DECLARED_ONLY)))
    _qualify(cfg, FALLBACK, role_inference_settings(cfg, "developer", FALLBACK))
    cfg.llm_chain[0].context_window = 16384
    model_runtime.clear_model_runtime_cache()
    with model_role("developer"):
        details = await _refused(LLMClient(cfg), **_fallback_call(cfg))
    assert (details["model"], details["status"]) == (FALLBACK, mq.MISSING)
    assert details["requested_context_window"] == 16384


@pytest.mark.asyncio
async def test_primary_and_fallback_settings_never_bleed_and_no_default_is_substituted(monkeypatch):
    """Each call executes its own binding's settings: the fallback's own
    temperature (never the primary's, never a default), and a record of one
    binding's identity admits no call of the other."""
    _exact_ollama(monkeypatch)
    cfg = _production(_native(_cfg(temperature=0.2, **DECLARED_ONLY)))
    cfg.llm.temperature = 0.7
    primary, fallback = (role_inference_settings(cfg, "developer", model) for model in (PRIMARY, FALLBACK))
    assert (primary.temperature, fallback.temperature) == (0.7, 0.2) and primary.digest != fallback.digest
    _qualify(cfg, PRIMARY, primary)
    llm, call = LLMClient(cfg), _fallback_call(cfg)
    with model_role("developer"):
        details = await _refused(llm, **call)     # the primary's record does not admit the fallback
        assert details["inference_settings_digest"] == fallback.digest
        assert details["inference_settings"]["temperature"] == 0.2
        _qualify(cfg, FALLBACK, fallback)
        admitted, _body = await _dispatch(llm, **call)
        assert (admitted.digest, admitted.temperature) == (fallback.digest, 0.2)
        own, _body = await _dispatch(llm)          # and the primary still runs as itself
        assert own.digest == primary.digest
    substituted = request_settings(temperature=cfg.llm.temperature, reasoning=False, extra_body=None,
                                   runtime=runtime_adapter("ollama_native"))
    assert substituted.digest not in (fallback.digest,)


@pytest.mark.asyncio
async def test_a_refusal_records_the_resolved_non_secret_settings(monkeypatch):
    """F2 evidence: a digest mismatch is diagnosable from the refusal alone -
    the resolved settings components, never a key."""
    _exact_ollama(monkeypatch)
    cfg = _production(_native(_cfg(temperature=0.2, api_key="sk-very-secret-fallback-key", **DECLARED_ONLY)))
    cfg.llm.api_key = "sk-very-secret-primary-key"
    with model_role("developer"):
        details = await _refused(LLMClient(cfg), **_fallback_call(cfg))
    expected = binding_inference_settings(cfg, "developer", cfg.llm_chain[0])
    assert details["inference_settings_digest"] == expected.digest
    assert details["inference_settings"] == expected.identity_fields()
    assert set(details["inference_settings"]) == {"version", "temperature", "reasoning", "extra_body", "server_config"}
    assert details["requested_context_window"] == 8192
    assert details["inference_runtime"] == "ollama_native"
    assert "sk-very-secret" not in repr(details)


