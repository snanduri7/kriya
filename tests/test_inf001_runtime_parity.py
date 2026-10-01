"""INF-001 golden parity: what Kriya sends to its OpenAI-compatible
transport, and the runtime fingerprint digest of a fixed Ollama probe.

Pinned at 3c9f7db (before the runtime port existed); re-pinned by
PROVIDER-CONTRACT-001, which changed both deliberately. The /v1 wire now
carries only what Ollama's /v1 applies (never ``options.*``, which it
ignores; ``reasoning:false`` as ``reasoning_effort: "none"``), every request
carries Kriya's own timeout, and a stream is one request (never resent in
another shape). The fingerprint gained the served model's parameters and the
adapter version /3, so records keyed by the old digest are STALE."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from test_prd013_model_runtime import _probe

from kriya.config import AppConfig, FallbackModelConfig, ModelCapabilities
from kriya.core.llm import LLMClient

TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
SUPERSEDED_DIGEST_AT_3C9F7DB = "f7e50f499ae08b807b49cc8df48b65a812cd428786e86cec6144f98c00fd2185"
DIGEST_PROVIDER_CONTRACT_001 = "b4d914f5a283253d884e63d08e7c528300cf832c398669dad621be38de059d3c"


def _response():
    response = MagicMock()
    response.choices = [MagicMock()]
    message = response.choices[0].message
    message.content, message.reasoning, message.reasoning_content, message.tool_calls = "ok", None, None, None
    response.choices[0].finish_reason = "stop"
    # No measured prompt count: a canned response has no request to measure.
    response.usage = MagicMock(prompt_tokens=0, completion_tokens=2)
    return response


def _chunk():
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content, chunk.choices[0].delta.reasoning = "ok", None
    chunk.choices[0].finish_reason = "stop"
    chunk.usage = None
    return chunk


def _config():
    config = AppConfig()
    config.llm.model = "primary:1"
    config.llm.extra_body = {"options": {"top_p": 0.8}}
    config.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    config.llm_chain = [FallbackModelConfig(
        model="fb:1", context_window=16384, temperature=0.3, extra_body={"reasoning_effort": "none"},
        capabilities=ModelCapabilities(native_tool_calls=True))]
    return config


TIMEOUTS = []


def _shape(kwargs):
    TIMEOUTS.append(kwargs.get("timeout"))
    return {key: ([m["role"] for m in value] if key == "messages" else value) for key, value in kwargs.items()
            if key != "timeout"}


def _capture():
    out = {}

    async def run():
        llm = LLMClient(_config())
        create = AsyncMock(return_value=_response())
        with patch.object(llm.client.chat.completions, "create", new=create):
            await llm.complete("s", "u")
            out["plain"] = _shape(create.call_args.kwargs)
            await llm.complete("s", "u", json_mode=True)
            out["json"] = _shape(create.call_args.kwargs)
            await llm.complete("s", "u", model_override="fb:1")
            out["fallback"] = _shape(create.call_args.kwargs)
            await llm.complete_with_tools([{"role": "user", "content": "hi"}], TOOLS)
            out["tools"] = _shape(create.call_args.kwargs)
        calls = []

        async def stream():
            yield _chunk()

        async def streaming_create(**kwargs):
            calls.append(_shape(kwargs))
            return stream()

        streaming = LLMClient(_config())
        with patch.object(streaming.client.chat.completions, "create", new=streaming_create):
            await streaming.complete("s", "u", stream_callback=lambda token: None)
        out["stream"] = calls

    asyncio.run(run())
    return out


WIRE_PROVIDER_CONTRACT_001 = {
    "plain": {"model": "primary:1", "messages": ["system", "user"], "temperature": 0.2, "max_tokens": 4096,
              "extra_body": {"top_p": 0.8, "reasoning_effort": "none"}, "response_format": None},
    "json": {"model": "primary:1", "messages": ["system", "user"], "temperature": 0.2, "max_tokens": 4096,
             "extra_body": {"top_p": 0.8, "reasoning_effort": "none"},
             "response_format": {"type": "json_object"}},
    "fallback": {"model": "fb:1", "messages": ["system", "user"], "temperature": 0.3, "max_tokens": 16094,
                 "extra_body": {"reasoning_effort": "none"}, "response_format": None},
    "tools": {"model": "primary:1", "messages": ["user"], "tools": TOOLS, "tool_choice": "auto", "temperature": 0.2,
              "max_tokens": 4096, "extra_body": {"top_p": 0.8, "reasoning_effort": "none"}},
    "stream": [
        {"model": "primary:1", "messages": ["system", "user"], "temperature": 0.2, "max_tokens": 4096,
         "stream": True, "stream_options": {"include_usage": True},
         "extra_body": {"top_p": 0.8, "reasoning_effort": "none"}, "response_format": None},
    ],
}


def test_the_wire_is_unchanged():
    TIMEOUTS.clear()
    assert _capture() == WIRE_PROVIDER_CONTRACT_001
    # Every request carries Kriya's own timeout (llm.transport), never the SDK default.
    assert TIMEOUTS and all(isinstance(t, httpx.Timeout) and t.connect == 10.0 and t.read == 900.0
                            for t in TIMEOUTS)


def test_the_runtime_fingerprint_digest_is_unchanged():
    assert _probe().digest == DIGEST_PROVIDER_CONTRACT_001 != SUPERSEDED_DIGEST_AT_3C9F7DB
