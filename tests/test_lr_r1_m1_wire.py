"""LR-R1-M1.4: the adapters' exact request body (``wire_payload``) and the
untrimmed transport content (design §5.3, D3; test T9 wire part).

For both shipped adapters and every request shape, the body evidence
reports (``wire_payload``) is the very object the transport sent, and
``raw_content`` is the provider's content byte for byte (the adapter still
returns ``content`` stripped, exactly as before).
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.core.inference_runtime import ChatRequest


def _adapters():
    import kriya.core.model_runtime  # noqa: F401 - registers the shipped adapters
    from kriya.core.inference_runtime import runtime_adapter

    return {"openai": runtime_adapter(None), "native": runtime_adapter("ollama_native")}


def _request(**overrides):
    values = dict(model="m:1", messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                  temperature=0.7, max_tokens=64, extra_body={"options": {"num_ctx": 8192}, "think": False},
                  response_format={"type": "json_object"}, timeout=12.5)
    values.update(overrides)
    return ChatRequest(**values)


# -- OpenAI-compatible transport ------------------------------------------------------------

def _openai_client(content):
    message = SimpleNamespace(content=content, reasoning="r-text", tool_calls=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2), id="x", model="m:1")
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


def test_openai_non_stream_posts_exactly_the_wire_payload():
    adapter = _adapters()["openai"]
    client = _openai_client("  padded content \n")
    request = _request()
    out = asyncio.run(adapter.complete(client, request))
    kwargs = client.chat.completions.create.await_args.kwargs
    assert {k: v for k, v in kwargs.items() if k != "timeout"} == adapter.wire_payload(request, stream=False)
    assert kwargs["timeout"] == 12.5
    assert out.raw_content == "  padded content \n" and out.content == "padded content"
    assert out.reasoning_text == "r-text" and out.reasoning_chars == 6


def test_openai_stream_posts_exactly_the_wire_payload():
    adapter = _adapters()["openai"]

    def chunk(content=None, reasoning=None, finish=None):
        delta = SimpleNamespace(content=content, reasoning=reasoning)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)

    async def stream():
        for item in (chunk(reasoning="th"), chunk(" a"), chunk("b \n", finish="stop")):
            yield item
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=stream())
    request = _request(stream_callback=lambda _piece: None)
    out = asyncio.run(adapter.complete(client, request))
    kwargs = client.chat.completions.create.await_args.kwargs
    assert {k: v for k, v in kwargs.items() if k != "timeout"} == adapter.wire_payload(request, stream=True)
    assert kwargs["stream"] is True
    assert out.raw_content == " ab \n" and out.content == "ab" and out.reasoning_text == "th"


def test_openai_tool_call_posts_exactly_the_wire_payload():
    adapter = _adapters()["openai"]
    client = _openai_client("tool text")
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    request = _request(tools=tools, response_format=None)
    asyncio.run(adapter.complete_with_tools(client, request))
    kwargs = client.chat.completions.create.await_args.kwargs
    assert {k: v for k, v in kwargs.items() if k != "timeout"} == adapter.wire_payload(request, stream=False)
    assert kwargs["tools"] == tools and kwargs["tool_choice"] == "auto" and "response_format" not in kwargs


# -- native /api/chat -----------------------------------------------------------------------

class _Http:
    def __init__(self, body, lines=()):
        self.timeout = 30
        self.body, self.lines = body, list(lines)
        self.posted = []

    async def post(self, url, *, json, headers, timeout):
        self.posted.append(json)
        return SimpleNamespace(status_code=200, text="", json=lambda: self.body)

    def stream(self, method, url, *, json, headers, timeout):
        self.posted.append(json)
        lines = self.lines

        class _Response:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def aiter_lines(self):
                for line in lines:
                    yield line
        return _Response()


def _native_client(http):
    return SimpleNamespace(base_url="http://localhost:11434/v1", api_key="", _client=http)


@pytest.mark.parametrize("stream", [False, True])
def test_native_posts_exactly_the_wire_payload(stream):
    adapter = _adapters()["native"]
    body = {"message": {"content": "  raw \n", "thinking": "deep"}, "done": True, "done_reason": "stop"}
    lines = [json.dumps({"message": {"content": "  ra", "thinking": "de"}}),
             json.dumps({"message": {"content": "w \n", "thinking": "ep"}, "done": True, "done_reason": "stop"})]
    http = _Http(body, lines)
    request = _request(stream_callback=(lambda _p: None) if stream else None)
    out = asyncio.run(adapter.complete(_native_client(http), request))
    assert http.posted == [adapter.wire_payload(request, stream=stream)]
    assert out.raw_content == "  raw \n" and out.content == "raw" and out.reasoning_text == "deep"


def test_a_port_without_a_declared_body_reports_none():
    from kriya.core.inference_runtime import InferenceRuntimePort

    assert InferenceRuntimePort.wire_payload(MagicMock(), _request(), stream=False) is None
