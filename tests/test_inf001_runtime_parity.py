"""INF-001 golden parity: what Kriya sends to its OpenAI-compatible
transport, and the runtime fingerprint digest of a fixed Ollama probe, pinned
at 3c9f7db (before the runtime port existed). Moving the transport and the
probe behind an adapter must leave every byte of both unchanged - the demo-03
QUALIFIED records are keyed by that digest."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from test_prd013_model_runtime import _probe

from kriya.config import AppConfig, FallbackModelConfig, ModelCapabilities
from kriya.core.llm import LLMClient

TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
DIGEST_AT_3C9F7DB = "f7e50f499ae08b807b49cc8df48b65a812cd428786e86cec6144f98c00fd2185"


def _response():
    response = MagicMock()
    response.choices = [MagicMock()]
    message = response.choices[0].message
    message.content, message.reasoning, message.reasoning_content, message.tool_calls = "ok", None, None, None
    response.choices[0].finish_reason = "stop"
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=2)
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


def _shape(kwargs):
    return {key: ([m["role"] for m in value] if key == "messages" else value) for key, value in kwargs.items()}


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
            if "stream_options" in kwargs:
                raise ValueError("stream_options unsupported")
            return stream()

        streaming = LLMClient(_config())
        with patch.object(streaming.client.chat.completions, "create", new=streaming_create):
            await streaming.complete("s", "u", stream_callback=lambda token: None)
        out["stream"] = calls

    asyncio.run(run())
    return out


WIRE_AT_3C9F7DB = {
    "plain": {"model": "primary:1", "messages": ["system", "user"], "temperature": 0.2, "max_tokens": 4096,
              "extra_body": {"options": {"top_p": 0.8, "num_ctx": 32768}}, "response_format": None},
    "json": {"model": "primary:1", "messages": ["system", "user"], "temperature": 0.2, "max_tokens": 4096,
             "extra_body": {"options": {"top_p": 0.8, "num_ctx": 32768}},
             "response_format": {"type": "json_object"}},
    "fallback": {"model": "fb:1", "messages": ["system", "user"], "temperature": 0.3, "max_tokens": 16094,
                 "extra_body": {"reasoning_effort": "none", "options": {"num_ctx": 16384}},
                 "response_format": None},
    "tools": {"model": "primary:1", "messages": ["user"], "tools": TOOLS, "tool_choice": "auto", "temperature": 0.2,
              "max_tokens": 4096, "extra_body": {"options": {"top_p": 0.8, "num_ctx": 32768}}},
    "stream": [
        {"model": "primary:1", "messages": ["system", "user"], "temperature": 0.2, "max_tokens": 4096,
         "stream": True, "stream_options": {"include_usage": True},
         "extra_body": {"options": {"top_p": 0.8, "num_ctx": 32768}}, "response_format": None},
        {"model": "primary:1", "messages": ["system", "user"], "temperature": 0.2, "max_tokens": 4096,
         "stream": True, "extra_body": {"options": {"top_p": 0.8, "num_ctx": 32768}}, "response_format": None},
    ],
}


def test_the_wire_is_unchanged():
    assert _capture() == WIRE_AT_3C9F7DB


def test_the_runtime_fingerprint_digest_is_unchanged():
    assert _probe().digest == DIGEST_AT_3C9F7DB
