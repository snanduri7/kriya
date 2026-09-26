"""PRD-015: the normalized completion result at the LLM boundary.

Provider-specific response handling (reasoning wrappers, Ollama's separate
``reasoning`` field, native vs. textual tool-call formats, finish reasons,
usage fields) is interpreted here and in ``kriya/core/llm.py`` only.
Downstream code reads ``CompletionResult`` fields instead of inferring
truncation, tool calls or parser state from raw text.

``status`` separates protocol outcomes from semantic model output:
- ``OK``: complete content (and/or tool calls) the caller may interpret;
- ``EMPTY_CONTENT``: the model produced nothing usable;
- ``MALFORMED_STRUCTURED_OUTPUT``: structured output was requested and the
  content does not parse as JSON (the text is kept for the caller's own
  recovery, but it is never presented as a valid structured answer);
- ``OUTPUT_TRUNCATED``: the provider stopped at the output budget
  (``finish_reason == "length"``); the content is incomplete;
- ``BACKEND_ERROR`` / ``TIMEOUT``: the request failed;
- ``CANCELLED``: the call was cancelled (recorded, then re-raised; the
  cancellation itself is never swallowed).
Hidden reasoning is never part of ``content``: its presence and size are
recorded, its text is not.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class CompletionStatus(str, Enum):
    OK = "OK"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    MALFORMED_STRUCTURED_OUTPUT = "MALFORMED_STRUCTURED_OUTPUT"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    BACKEND_ERROR = "BACKEND_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


PROTOCOL_FAILURE_STATUSES = frozenset({
    CompletionStatus.EMPTY_CONTENT, CompletionStatus.MALFORMED_STRUCTURED_OUTPUT,
    CompletionStatus.OUTPUT_TRUNCATED, CompletionStatus.BACKEND_ERROR,
    CompletionStatus.TIMEOUT, CompletionStatus.CANCELLED,
})


@dataclass
class CompletionResult:
    status: CompletionStatus
    content: str = ""
    model: str = ""
    runtime_fingerprint: Optional[str] = None
    runtime_fingerprint_exact: bool = False
    # MODEL-EVIDENCE-HARDENING-001: the executed inference identity (the
    # digest of the settings this request actually sent).
    inference_settings_digest: Optional[str] = None
    # What Kriya asked for: json_mode, streaming, tools, and whether the
    # JSON-mode-with-reasoning fallback (retry without response_format) or
    # the empty-content floor retry was used.
    protocol: Dict[str, Any] = field(default_factory=dict)
    reasoning_present: bool = False
    reasoning_chars: int = 0
    reasoning_source: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    tokens_estimated: bool = True
    elapsed_seconds: float = 0.0
    parser_status: str = "not_applicable"
    backend_status: str = "ok"
    backend_error: Optional[str] = None
    max_tokens: Optional[int] = None
    # Response identifiers only (id, served model, system_fingerprint);
    # never headers, keys or message text.
    provider_metadata: Dict[str, Any] = field(default_factory=dict)
    # PRD-016: the pre-dispatch budget decision for this call, when made.
    budget: Optional[Dict[str, Any]] = None
    # The exception behind BACKEND_ERROR/TIMEOUT (not serialized) so the
    # string-returning compatibility API can re-raise it unchanged.
    error: Optional[BaseException] = field(default=None, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.status is CompletionStatus.OK

    @property
    def truncated(self) -> bool:
        return self.status is CompletionStatus.OUTPUT_TRUNCATED

    def to_telemetry(self) -> Dict[str, Any]:
        """Persistable, secret-free protocol outcome for model-use telemetry."""
        return {
            "status": self.status.value,
            "model": self.model,
            "runtime_fingerprint": self.runtime_fingerprint,
            "runtime_fingerprint_exact": self.runtime_fingerprint_exact,
            "inference_settings_digest": self.inference_settings_digest,
            "protocol": dict(self.protocol),
            "reasoning_present": self.reasoning_present,
            "reasoning_chars": self.reasoning_chars,
            "reasoning_source": self.reasoning_source,
            "tool_call_count": len(self.tool_calls),
            "tool_call_sources": sorted({call.get("source", "native") for call in self.tool_calls}),
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_estimated": self.tokens_estimated,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "parser_status": self.parser_status,
            "backend_status": self.backend_status,
            "backend_error": self.backend_error,
            "max_tokens": self.max_tokens,
            "output_truncated": self.truncated,
            "content_chars": len(self.content),
            "provider_metadata": dict(self.provider_metadata),
            "budget": dict(self.budget) if self.budget else None,
        }


class CompletionProtocolError(RuntimeError):
    """A normalized completion that a caller requiring complete, valid
    output cannot accept. ``reason_code`` is the CompletionStatus value."""

    def __init__(self, result: CompletionResult, message: str = ""):
        self.result = result
        self.reason_code = result.status.value
        super().__init__(
            f"{self.reason_code}: {message or 'model output is not a complete, valid response'} "
            f"(model {result.model!r}, finish_reason={result.finish_reason!r})"
        )


# --------------------------------------------------------------------------
# Reasoning
# --------------------------------------------------------------------------

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def split_reasoning(content: str, *, anywhere: bool = False) -> Tuple[str, int]:
    """(visible content, hidden reasoning chars).

    Reasoning precedes the answer, so by default only a LEADING
    ``<think>...</think>`` block is removed, or a leading ``<think>`` block
    cut off before it closed (then nothing is visible). Generated source
    that merely contains the tag text later on is left alone.
    ``anywhere=True`` (models configured as reasoning models: the
    long-standing behaviour) also removes complete blocks elsewhere and
    text ending in a lone ``</think>`` whose opening tag the chat template
    consumed."""
    text = (content or "").lstrip()
    hidden = 0
    if text.startswith("<think>"):
        end = text.find("</think>")
        if end == -1:
            return "", len(text)
        hidden += end + len("</think>")
        text = text[end + len("</think>"):]
    elif anywhere and "</think>" in text and "<think>" not in text.split("</think>", 1)[0]:
        head, _, tail = text.partition("</think>")
        hidden += len(head) + len("</think>")
        text = tail
    if anywhere:
        for match in _THINK_BLOCK.findall(text):
            hidden += len(match)
        text = _THINK_BLOCK.sub("", text)
    return text.strip(), hidden


# --------------------------------------------------------------------------
# Structured output
# --------------------------------------------------------------------------

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n```\s*$", re.DOTALL)


def structured_parse_status(content: str) -> str:
    """"ok" when the content (optionally one markdown fence) is JSON, else
    "malformed". Callers keep their own lenient extraction; this status only
    stops a malformed answer from being reported as a valid one."""
    text = (content or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        json.loads(text)
        return "ok"
    except (json.JSONDecodeError, TypeError, ValueError):
        return "malformed"


# --------------------------------------------------------------------------
# Textual tool calls (when a backend's own parser did not convert them)
# --------------------------------------------------------------------------

_HERMES_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_QWEN_XML_TOOL_CALL = re.compile(
    r"<tool_call>\s*<function=([^>\s]+)>\s*(.*?)\s*</function>\s*</tool_call>", re.DOTALL,
)
_QWEN_XML_PARAMETER = re.compile(r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>", re.DOTALL)


def parse_textual_tool_calls(content: str) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """(calls, remaining content, errors) for Hermes-style JSON
    ``<tool_call>{...}</tool_call>`` and Qwen XML
    ``<tool_call><function=name><parameter=p>v</parameter></function></tool_call>``.
    A malformed block is reported as an error and never turned into a call."""
    calls: List[Dict[str, Any]] = []
    errors: List[str] = []
    text = content or ""

    def _xml(match: "re.Match[str]") -> str:
        arguments: Dict[str, Any] = {}
        for name, raw in _QWEN_XML_PARAMETER.findall(match.group(2)):
            try:
                arguments[name] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                arguments[name] = raw
        calls.append({"id": f"text-{len(calls)}", "name": match.group(1), "arguments": arguments,
                      "source": "qwen_xml"})
        return ""

    text = _QWEN_XML_TOOL_CALL.sub(_xml, text)

    def _hermes(match: "re.Match[str]") -> str:
        try:
            payload = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError) as error:
            errors.append(f"malformed <tool_call> JSON: {error}")
            return match.group(0)
        name = payload.get("name") if isinstance(payload, dict) else None
        arguments = payload.get("arguments", {}) if isinstance(payload, dict) else None
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, ValueError):
                arguments = None
        if not isinstance(name, str) or not isinstance(arguments, dict):
            errors.append("<tool_call> JSON lacks a string name and an object of arguments")
            return match.group(0)
        calls.append({"id": f"text-{len(calls)}", "name": name, "arguments": arguments, "source": "hermes"})
        return ""

    text = _HERMES_TOOL_CALL.sub(_hermes, text)
    if "<tool_call>" in text and not errors:
        errors.append("a <tool_call> block could not be parsed")
    return calls, text.strip(), errors


def classify(
    *,
    content: str,
    finish_reason: Optional[str],
    tool_calls: List[Dict[str, Any]],
    structured: bool,
) -> Tuple[CompletionStatus, str]:
    """(status, parser_status) for a completed (non-error) response.
    Truncation outranks everything: incomplete output is never OK."""
    parser_status = structured_parse_status(content) if structured and content else "not_applicable"
    if finish_reason == "length":
        return CompletionStatus.OUTPUT_TRUNCATED, parser_status
    if not content and not tool_calls:
        return CompletionStatus.EMPTY_CONTENT, parser_status
    if structured and parser_status == "malformed":
        return CompletionStatus.MALFORMED_STRUCTURED_OUTPUT, parser_status
    return CompletionStatus.OK, parser_status


__all__ = [
    "CompletionProtocolError", "CompletionResult", "CompletionStatus", "PROTOCOL_FAILURE_STATUSES",
    "classify", "parse_textual_tool_calls", "split_reasoning", "structured_parse_status",
]
