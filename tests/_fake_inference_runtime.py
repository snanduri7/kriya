"""A deterministic inference runtime adapter for tests (INF-001).

Registered only by tests (the ``fake_runtime`` fixture pattern below); nothing
in kriya/ registers it, so a configuration can never select canned answers in
production (tests/test_inf001_runtime_port.py asserts that)."""
import asyncio
from typing import Any, Callable, Dict, List, Optional

from kriya.core.inference_runtime import (
    ChatRequest,
    ChatResponse,
    InferenceRuntimePort,
    RawToolCall,
    RuntimeCapabilities,
    RuntimeErrorKind,
)
from kriya.core.model_runtime import ModelRuntimeFingerprint, endpoint_identity

# The fake runtime's own per-request window field (when it takes one).
FAKE_WINDOW_FIELD = "fake_window"


class FakeTimeout(Exception):
    pass


class FakeServerError(Exception):
    pass


class FakeRuntimeAdapter(InferenceRuntimePort):
    """Answers from ``replies`` (content strings, ChatResponse objects, or
    exceptions to raise) in order; records every request and every probe."""

    def __init__(self, name: str = "fake", *, per_request_context_window: bool = False,
                 served_window: Optional[int] = 8192, provider_version: str = "1.0",
                 weights_digest: str = "sha256:weights", replies: Optional[List[Any]] = None,
                 models: Optional[List[str]] = None) -> None:
        self.name = name
        self.capabilities = RuntimeCapabilities(
            per_request_context_window=per_request_context_window, native_identity_probe=True)
        self.served_window = served_window
        self.provider_version = provider_version
        self.weights_digest = weights_digest
        self.replies = list(replies or [])
        self.models = list(models or [])
        self.requests: List[ChatRequest] = []
        self.probes: List[Dict[str, Any]] = []

    # -- window ---------------------------------------------------------------
    def configured_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Optional[int]:
        if not self.capabilities.per_request_context_window or not isinstance(extra_body, dict):
            return None
        value = extra_body.get(FAKE_WINDOW_FIELD)
        return value if isinstance(value, int) and value > 0 else None

    def with_context_window(self, extra_body: Optional[Dict[str, Any]], tokens: int) -> Dict[str, Any]:
        body = dict(extra_body or {})
        if self.capabilities.per_request_context_window:
            body[FAKE_WINDOW_FIELD] = int(tokens)
        return body

    def without_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {k: v for k, v in (extra_body or {}).items() if k != FAKE_WINDOW_FIELD}

    # -- identity ---------------------------------------------------------------
    def probe(self, *, base_url: str, model: str, api_key: str, egress_policy: str,
              configured_context: Optional[int], kriya_protocol: str,
              transport: Optional[Callable[..., Any]] = None) -> ModelRuntimeFingerprint:
        self.probes.append({"model": model, "configured_context": configured_context})
        served = configured_context if self.capabilities.per_request_context_window else self.served_window
        return ModelRuntimeFingerprint(
            alias=model, endpoint=endpoint_identity(base_url), provider=self.name,
            provider_version=self.provider_version, artifact_digest=f"sha256:artifact-{model}",
            weights_digest=self.weights_digest, tokenizer_digest="sha256:tok",
            configured_context_window=configured_context, effective_context_window=served,
            model_context_length=262144, kriya_protocol=kriya_protocol,
        )

    # -- transport ----------------------------------------------------------------
    def _next(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else "ok"
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, ChatResponse):
            return reply
        return ChatResponse(content=reply, prompt_tokens=11, completion_tokens=3, finish_reason="stop")

    async def complete(self, client: Any, request: ChatRequest) -> ChatResponse:
        await asyncio.sleep(0)
        response = self._next(request)
        if request.stream_callback is not None:
            for token in response.content.split(" "):
                request.stream_callback(token)
        return response

    async def complete_with_tools(self, client: Any, request: ChatRequest) -> ChatResponse:
        await asyncio.sleep(0)
        return self._next(request)

    def classify_error(self, error: BaseException) -> RuntimeErrorKind:
        if isinstance(error, FakeTimeout):
            return RuntimeErrorKind.TIMEOUT
        if isinstance(error, FakeServerError):
            return RuntimeErrorKind.SERVER
        return RuntimeErrorKind.REQUEST

    def list_models(self, base_url: str, api_key: str, fetch_json: Callable[..., Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{"id": model} for model in self.models]


def tool_reply(name: str, arguments: str) -> ChatResponse:
    return ChatResponse(content="", finish_reason="tool_calls", prompt_tokens=5, completion_tokens=2,
                        tool_calls=[RawToolCall("call-1", name, arguments)])
