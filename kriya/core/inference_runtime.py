"""INF-001: the inference runtime port.

Layering: ``workflow / agents -> LLMClient (the inference service) ->
InferenceRuntimePort -> runtime adapter``.

Kriya keeps every decision above the port: model routing, qualification,
fallback, retry, context and output budgets, egress and other authority,
evidence, verification and run state (kriya/core/llm.py and above). A
runtime adapter owns only what differs between inference runtimes:

- the transport: translating a ChatRequest into the runtime's request and
  its response (content, reasoning, usage, finish reason, tool calls) back;
- how a per-request context window is expressed, and whether the runtime
  takes one at all (``capabilities.per_request_context_window``);
- identifying the served model and runtime (``probe`` -> the exact runtime
  fingerprint qualification is keyed by);
- model discovery (``list_models``) and classifying transport errors.

Workflow and agent code never import an adapter; they call LLMClient. The
packaged default adapter (the local runtime behind the OpenAI-compatible API
plus its native identity probes) is registered by kriya/core/model_runtime.py,
the one module that knows a provider's name. Tests register a deterministic
fake. A binding selects its adapter with ``inference_runtime`` (None: the
default); an unknown name is refused, never mapped to the default.

Extension point - VllmRuntimeAdapter (not implemented; needs separate
approval): subclass OpenAICompatibleTransport (vLLM serves the same chat
API), set ``per_request_context_window=False`` (``max_model_len`` is fixed at
server start, so ``with_context_window`` returns the body unchanged and the
dispatch budget labels the window ``provider_managed`` unless the probe
reports it), implement ``probe`` from the server's version and model
endpoints (an exact artifact digest and server version, or the fingerprint
stays non-exact and never QUALIFIED), and register it under its own name.
The same weights served by it are a different runtime digest, so they never
share qualification evidence with the default runtime. Nothing above the
port changes.
"""

import abc
import asyncio
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class RuntimeErrorKind(str, Enum):
    """A transport failure, in terms Kriya's policy can act on."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    SERVER = "server"
    # The runtime rejected this request's shape (or an unclassified error):
    # the only kind a changed request (e.g. without response_format) may fix.
    REQUEST = "request"


@dataclass(frozen=True)
class RuntimeCapabilities:
    """What a runtime supports, independent of the model it serves (a
    model's own capabilities are its qualification's)."""

    per_request_context_window: bool
    native_identity_probe: bool


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: List[Dict[str, Any]]
    temperature: Optional[float]
    max_tokens: int
    extra_body: Optional[Dict[str, Any]] = None
    response_format: Optional[Dict[str, Any]] = None
    stream_callback: Optional[Callable[[str], None]] = None
    tools: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class RawToolCall:
    """A native tool call as the runtime returned it; arguments are the raw
    text (Kriya parses and validates them against the model's limits)."""

    id: str
    name: str
    arguments: Any


@dataclass
class ChatResponse:
    content: str
    reasoning_chars: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: Optional[str] = None
    provider_metadata: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[RawToolCall] = field(default_factory=list)

    def to_raw(self) -> Dict[str, Any]:
        """The raw-field dict LLMClient's normalizer reads (the historical
        ``_request_once`` shape)."""
        return {
            "content": self.content, "reasoning_chars": self.reasoning_chars,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "finish_reason": self.finish_reason, "provider_metadata": self.provider_metadata,
        }


class InferenceRuntimePort(abc.ABC):
    """One inference runtime's translation layer (see the module docstring).
    Adapters are stateless and shared: every method takes what it needs."""

    name: str
    capabilities: RuntimeCapabilities

    # -- per-request context window --------------------------------------
    @abc.abstractmethod
    def configured_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Optional[int]:
        """The context window a request body asks for, if any."""

    @abc.abstractmethod
    def with_context_window(self, extra_body: Optional[Dict[str, Any]], tokens: int) -> Dict[str, Any]:
        """A copy of ``extra_body`` asking for ``tokens`` (unchanged when the
        runtime takes no per-request window)."""

    @abc.abstractmethod
    def without_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """A copy of ``extra_body`` without the window request (the window is
        runtime identity, never an inference setting)."""

    def supports_per_request_context_window(self, fingerprint: Any) -> bool:
        """Whether this exact runtime takes its window per request, so a
        PRD-016 context tier can be selected for one request."""
        return self.capabilities.per_request_context_window and bool(getattr(fingerprint, "exact", False))

    # -- identity ----------------------------------------------------------
    @abc.abstractmethod
    def probe(self, *, base_url: str, model: str, api_key: str, egress_policy: str,
              configured_context: Optional[int], kriya_protocol: str,
              transport: Optional[Callable[..., Any]] = None) -> Any:
        """The runtime fingerprint (kriya.core.model_runtime.ModelRuntimeFingerprint)
        of ``model`` as this runtime serves it. Never raises."""

    # -- transport -----------------------------------------------------------
    @abc.abstractmethod
    async def complete(self, client: Any, request: ChatRequest) -> ChatResponse:
        """One text completion (streamed through ``request.stream_callback``
        when set). Raises the transport's own exceptions; classify them with
        ``classify_error``. ``asyncio.CancelledError`` always propagates."""

    @abc.abstractmethod
    async def complete_with_tools(self, client: Any, request: ChatRequest) -> ChatResponse:
        """One tool-calling turn (``request.tools``, tool choice left to the
        model), never streamed."""

    @abc.abstractmethod
    def classify_error(self, error: BaseException) -> RuntimeErrorKind:
        """The kind of a transport failure raised by this adapter."""

    @abc.abstractmethod
    def list_models(self, base_url: str, api_key: str,
                    fetch_json: Callable[..., Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The models the runtime serves (health and discovery), fetched with
        the caller's ``fetch_json(url, api_key=...)`` (which owns egress and
        timeouts)."""


def _str_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _provider_metadata(response: Any) -> Dict[str, Any]:
    """Response identifiers only; anything that is not a plain string is dropped."""
    return {
        key: value
        for key in ("id", "model", "system_fingerprint")
        if isinstance(value := getattr(response, key, None), str) and value
    }


class OpenAICompatibleTransport(InferenceRuntimePort, abc.ABC):
    """The OpenAI-compatible chat API over an ``openai.AsyncOpenAI``-shaped
    client (the handle LLMClient owns). Every provider field is read
    defensively (``getattr(..., None)`` and a type check, never a bare
    attribute access): a runtime that omits ``usage`` degrades the token
    counts to 0 (estimated later by LLMClient), one that omits
    ``finish_reason`` degrades it to None - never a fabricated "stop"
    (VAL-001 G1-R3)."""

    async def complete(self, client: Any, request: ChatRequest) -> ChatResponse:
        common = dict(model=request.model, messages=request.messages, temperature=request.temperature,
                      max_tokens=request.max_tokens)
        if request.stream_callback is None:
            response = await client.chat.completions.create(
                **common, extra_body=request.extra_body, response_format=request.response_format,
            )
            out = ChatResponse(content="", provider_metadata=_provider_metadata(response))
            self._read_usage(out, getattr(response, "usage", None))
            if response.choices:
                out.finish_reason = _str_or_none(getattr(response.choices[0], "finish_reason", None))
            message = response.choices[0].message
            reasoning = _str_or_none(getattr(message, "reasoning", None)) or _str_or_none(
                getattr(message, "reasoning_content", None))
            out.reasoning_chars = len(reasoning) if reasoning else 0
            out.content = (message.content or "").strip()
            return out
        try:
            stream = await client.chat.completions.create(
                **common, stream=True, stream_options={"include_usage": True},
                extra_body=request.extra_body, response_format=request.response_format,
            )
        except Exception as error:  # CancelledError is not an Exception: it propagates
            logger.debug("Streaming request with stream_options failed, retrying without it "
                         "(server may not support it): %s", error)
            stream = await client.chat.completions.create(
                **common, stream=True, extra_body=request.extra_body, response_format=request.response_format,
            )
        out = ChatResponse(content="")
        chunks: List[str] = []
        async for chunk in stream:
            if not out.provider_metadata:
                out.provider_metadata = _provider_metadata(chunk)
            self._read_usage(out, getattr(chunk, "usage", None))
            if chunk.choices:
                # The finish_reason-carrying chunk is typically the LAST one
                # with empty delta content: only a real value overwrites, so
                # an earlier chunk's null can never clobber a later real one.
                chunk_finish_reason = _str_or_none(getattr(chunk.choices[0], "finish_reason", None))
                if chunk_finish_reason:
                    out.finish_reason = chunk_finish_reason
                delta = chunk.choices[0].delta
                reasoning_delta = _str_or_none(getattr(delta, "reasoning", None))
                if reasoning_delta:
                    out.reasoning_chars += len(reasoning_delta)
                if delta.content:
                    chunks.append(delta.content)
                    request.stream_callback(delta.content)
        out.content = "".join(chunks).strip()
        return out

    async def complete_with_tools(self, client: Any, request: ChatRequest) -> ChatResponse:
        response = await client.chat.completions.create(
            model=request.model, messages=request.messages, tools=request.tools, tool_choice="auto",
            temperature=request.temperature, max_tokens=request.max_tokens, extra_body=request.extra_body,
        )
        message = response.choices[0].message
        reasoning = _str_or_none(getattr(message, "reasoning", None))
        out = ChatResponse(
            content=message.content or "", reasoning_chars=len(reasoning) if reasoning else 0,
            finish_reason=_str_or_none(getattr(response.choices[0], "finish_reason", None)),
            provider_metadata=_provider_metadata(response),
            tool_calls=[RawToolCall(tc.id, tc.function.name, tc.function.arguments)
                        for tc in (message.tool_calls or [])],
        )
        self._read_usage(out, getattr(response, "usage", None))
        return out

    @staticmethod
    def _read_usage(out: ChatResponse, usage: Any) -> None:
        if usage:
            out.prompt_tokens = _int_or_zero(getattr(usage, "prompt_tokens", 0))
            out.completion_tokens = _int_or_zero(getattr(usage, "completion_tokens", 0))

    def classify_error(self, error: BaseException) -> RuntimeErrorKind:
        """A connection refused, an expired API key, a rate limit or a server
        error says nothing about whether the request's shape was accepted, so
        LLMClient never retries those with a changed request (2026-08-12 SME
        review). Everything else is REQUEST - deliberately an exclusion list,
        not an allowlist of e.g. BadRequestError: the response_format retry
        exists for real backend quirks whose error shape is not the same on
        every OpenAI-compatible server, so an unrecognized error gets the
        benefit of the doubt."""
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            InternalServerError,
            PermissionDeniedError,
            RateLimitError,
        )

        if isinstance(error, (APITimeoutError, asyncio.TimeoutError, TimeoutError)):
            return RuntimeErrorKind.TIMEOUT
        for types, kind in (
            (APIConnectionError, RuntimeErrorKind.CONNECTION),
            ((AuthenticationError, PermissionDeniedError), RuntimeErrorKind.AUTHENTICATION),
            (RateLimitError, RuntimeErrorKind.RATE_LIMITED),
            (InternalServerError, RuntimeErrorKind.SERVER),
        ):
            if isinstance(error, types):
                return kind
        return RuntimeErrorKind.REQUEST

    def list_models(self, base_url: str, api_key: str,
                    fetch_json: Callable[..., Dict[str, Any]]) -> List[Dict[str, Any]]:
        listing = fetch_json(f"{base_url.rstrip('/')}/models", api_key=api_key)
        return [item for item in listing.get("data", []) if isinstance(item, dict)]


# --------------------------------------------------------------------------
# Registry: adapters by name, the default one, lookups cached for the process.
# --------------------------------------------------------------------------

class UnknownRuntimeAdapterError(ValueError):
    """A binding names an inference runtime no adapter is registered for.
    Refused - never served by the default adapter instead."""


_REGISTRY: Dict[str, InferenceRuntimePort] = {}
_DEFAULT: List[str] = []
_LOCK = threading.Lock()


def register_runtime_adapter(adapter: InferenceRuntimePort, *, default: bool = False) -> None:
    with _LOCK:
        _REGISTRY[adapter.name] = adapter
        if default:
            _DEFAULT[:] = [adapter.name]


def unregister_runtime_adapter(name: str) -> None:
    with _LOCK:
        _REGISTRY.pop(name, None)


def runtime_adapter(name: Optional[str] = None) -> InferenceRuntimePort:
    """The adapter registered as ``name`` (None: the default runtime)."""
    if not _DEFAULT:
        import kriya.core.model_runtime  # noqa: F401 - registers the packaged default adapter

    key = name or _DEFAULT[0]
    adapter = _REGISTRY.get(key)
    if adapter is None:
        raise UnknownRuntimeAdapterError(
            f"inference_runtime {key!r} has no registered adapter (registered: {', '.join(sorted(_REGISTRY))})"
        )
    return adapter


def runtime_for_binding(binding: Any) -> InferenceRuntimePort:
    """The adapter a model binding (config object or binding dict) selects."""
    name = binding.get("inference_runtime") if isinstance(binding, dict) else getattr(binding, "inference_runtime", None)
    return runtime_adapter(name if isinstance(name, str) else None)
