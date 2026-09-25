"""PRD-016: tokenizer-aware dispatch budgeting.

Context assembly keeps using ``context_budget.estimate_tokens`` (``len // 4``)
to decide what goes into a prompt: that allocator already degrades and
records omissions (CTX-001). This module is the last check before a request
leaves Kriya: it counts the FULL dispatch (system prompt, user prompt or the
whole message list, tool schemas, protocol wrappers) against the served
context window and either

- lets the call proceed with its full output budget,
- reduces ``max_tokens`` to what fits (recorded; never below the minimum
  output), or
- refuses with ``ContextBudgetUnsatisfiableError``
  (``CONTEXT_BUDGET_UNSATISFIABLE``) before any inference, because the prompt
  plus the minimum output cannot fit. A local server that is sent an
  over-long prompt may silently drop part of it; Kriya never lets that
  happen to authoritative context.

Counting:
- **exact**: a count provider for this exact tokenizer (keyed by the runtime
  fingerprint's tokenizer digest). The supported local runtime (Ollama 0.34)
  exposes no tokenize endpoint and no tokenizer library is a Kriya
  dependency, so none is registered by default; ``register_exact_counter``
  is the seam for one, and nothing is ever downloaded.
- **qualified bound**: PRD-014 qualification measures real prompt-token
  usage on Java, Python, XML, JSON, Unicode and stack-trace probes and stores
  two ratios for that tokenizer, each the smallest seen less a margin: ASCII
  bytes per token (from the code/markup/trace probes) and non-ASCII bytes
  per token (from the Unicode probe, after its ASCII part). The bound is
  ``ceil(ascii_bytes / ascii_ratio + non_ascii_bytes / non_ascii_ratio)``.
  One combined ratio would either undercount CJK/emoji text or, taken as the
  minimum, roughly double the count for code.
- **default bound**: without qualification, 2.5 ASCII bytes per token (BPE
  tokenizers typically run 3-4.5 on code and prose) and 1.5 non-ASCII bytes
  per token (two tokens per CJK character, nearly three per emoji). This is
  a documented approximation, not a guarantee; qualification replaces it.
Every result records which method was used; the last two are marked
``approximate``. After the call, the provider's real prompt-token count is
compared with the prediction and an under-prediction is logged loudly.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CONTEXT_BUDGET_UNSATISFIABLE = "CONTEXT_BUDGET_UNSATISFIABLE"

DEFAULT_BYTES_PER_TOKEN = 2.5
DEFAULT_NON_ASCII_BYTES_PER_TOKEN = 1.5
# Chat-template tokens per message (role markers, separators) plus a fixed
# allowance for the request's own framing.
PER_MESSAGE_OVERHEAD_TOKENS = 8
REQUEST_OVERHEAD_TOKENS = 16
# The smallest output a call may be left with after its max_tokens is
# reduced to fit; below this the call is refused instead.
DEFAULT_MIN_OUTPUT_TOKENS = 1024
# Hidden reasoning a reasoning model spends before any visible output; used
# when qualification has not measured one.
DEFAULT_REASONING_ALLOWANCE_TOKENS = 2048
# Headroom kept free in the window beyond the counted prompt and the output
# budget, for counting error the bound does not cover.
DISPATCH_SAFETY_MARGIN_TOKENS = 256
# Framing of a two-message (system + user) request, as plan_dispatch counts it.
TWO_MESSAGE_FRAMING_TOKENS = REQUEST_OVERHEAD_TOKENS + 2 * PER_MESSAGE_OVERHEAD_TOKENS


class ContextBudgetUnsatisfiableError(RuntimeError):
    def __init__(self, decision: "DispatchBudget"):
        self.decision = decision
        self.reason_code = CONTEXT_BUDGET_UNSATISFIABLE
        super().__init__(
            f"{CONTEXT_BUDGET_UNSATISFIABLE}: the request needs about {decision.prompt_tokens} prompt tokens "
            f"({decision.counting_method}) plus at least {decision.min_output_tokens} output tokens"
            f"{' and ' + str(decision.reasoning_allowance) + ' reasoning tokens' if decision.reasoning_allowance else ''}"
            f", but the served context window is {decision.context_window} ({decision.window_source}). "
            "Nothing was sent to the model."
        )


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    exact: bool
    method: str


ExactCounter = Callable[[str], int]
_EXACT_COUNTERS: Dict[str, ExactCounter] = {}


def register_exact_counter(tokenizer_digest: str, counter: ExactCounter) -> None:
    """Register an exact local count provider for one tokenizer digest."""
    _EXACT_COUNTERS[tokenizer_digest] = counter


def unregister_exact_counter(tokenizer_digest: str) -> None:
    _EXACT_COUNTERS.pop(tokenizer_digest, None)


def bound_by_bytes(text: str, bytes_per_token: float,
                   non_ascii_bytes_per_token: float = DEFAULT_NON_ASCII_BYTES_PER_TOKEN) -> int:
    if not text:
        return 0
    total = len(text.encode("utf-8"))
    ascii_bytes = sum(1 for ch in text if ord(ch) < 128)
    return math.ceil(ascii_bytes / bytes_per_token + (total - ascii_bytes) / non_ascii_bytes_per_token)


def count_tokens(text: str, *, tokenizer_digest: Optional[str] = None,
                 qualified_bytes_per_token: Optional[float] = None,
                 qualified_non_ascii_bytes_per_token: Optional[float] = None) -> TokenCount:
    counter = _EXACT_COUNTERS.get(tokenizer_digest or "")
    if counter is not None:
        try:
            return TokenCount(int(counter(text)), True, "exact_local_tokenizer")
        except Exception as error:  # a broken provider degrades to the bound, visibly
            logger.warning("Exact token counter for %s failed (%s); using the conservative bound.",
                           tokenizer_digest, error)
    if qualified_bytes_per_token and qualified_bytes_per_token > 0:
        non_ascii = qualified_non_ascii_bytes_per_token or DEFAULT_NON_ASCII_BYTES_PER_TOKEN
        return TokenCount(bound_by_bytes(text, qualified_bytes_per_token, non_ascii), False, "qualified_byte_bound")
    return TokenCount(bound_by_bytes(text, DEFAULT_BYTES_PER_TOKEN), False, "default_byte_bound")


def dispatch_text(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> str:
    """Everything the model will read: every message's content (and tool
    calls / tool results), plus the serialized tool schemas."""
    import json

    parts: List[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            parts.append(json.dumps(content, sort_keys=True))
        if message.get("tool_calls"):
            parts.append(json.dumps(message["tool_calls"], sort_keys=True, default=str))
    if tools:
        parts.append(json.dumps(tools, sort_keys=True))
    return "\n".join(parts)


@dataclass(frozen=True)
class DispatchBudget:
    prompt_tokens: int
    exact: bool
    counting_method: str
    context_window: Optional[int]
    window_source: str
    requested_max_tokens: int
    max_tokens: int
    min_output_tokens: int
    reasoning_allowance: int
    satisfiable: bool
    output_reduced: bool

    @property
    def approximate(self) -> bool:
        return not self.exact

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "exact": self.exact,
            "approximate": self.approximate,
            "counting_method": self.counting_method,
            "context_window": self.context_window,
            "window_source": self.window_source,
            "requested_max_tokens": self.requested_max_tokens,
            "max_tokens": self.max_tokens,
            "min_output_tokens": self.min_output_tokens,
            "reasoning_allowance": self.reasoning_allowance,
            "satisfiable": self.satisfiable,
            "output_reduced": self.output_reduced,
        }


def plan_dispatch(
    *,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    requested_max_tokens: int,
    context_window: Optional[int],
    window_source: str,
    tokenizer_digest: Optional[str] = None,
    qualified_bytes_per_token: Optional[float] = None,
    qualified_non_ascii_bytes_per_token: Optional[float] = None,
    reasoning_allowance: int = 0,
    min_output_tokens: int = DEFAULT_MIN_OUTPUT_TOKENS,
) -> DispatchBudget:
    """Decide the output budget for one request. Raises
    ContextBudgetUnsatisfiableError when even the minimum output cannot fit."""
    counted = count_tokens(
        dispatch_text(messages, tools), tokenizer_digest=tokenizer_digest,
        qualified_bytes_per_token=qualified_bytes_per_token,
        qualified_non_ascii_bytes_per_token=qualified_non_ascii_bytes_per_token,
    )
    overhead = REQUEST_OVERHEAD_TOKENS + PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
    prompt_tokens = counted.tokens + overhead
    minimum = min(min_output_tokens, requested_max_tokens)
    if not context_window:
        return DispatchBudget(
            prompt_tokens, counted.exact, counted.method, None, window_source, requested_max_tokens,
            requested_max_tokens, minimum, reasoning_allowance, True, False,
        )
    # max_tokens bounds hidden reasoning and visible output together, so the
    # smallest acceptable output budget is the minimum visible output plus
    # the reasoning allowance.
    room = context_window - prompt_tokens
    needed = minimum + reasoning_allowance
    decision = DispatchBudget(
        prompt_tokens, counted.exact, counted.method, context_window, window_source, requested_max_tokens,
        max(0, min(requested_max_tokens, room)), minimum, reasoning_allowance,
        room >= needed, room < requested_max_tokens,
    )
    if not decision.satisfiable:
        raise ContextBudgetUnsatisfiableError(decision)
    return decision


def compare_with_usage(decision: Optional[Dict[str, Any]], reported_prompt_tokens: Optional[int],
                       *, model: str) -> Optional[Dict[str, Any]]:
    """Post-call check of the prediction against the provider's own count."""
    if not decision or not reported_prompt_tokens:
        return None
    predicted = decision.get("prompt_tokens") or 0
    under = reported_prompt_tokens > predicted
    if under and not decision.get("exact"):
        logger.warning(
            "Token budget under-predicted for %s: predicted %s prompt tokens (%s), provider reported %s. "
            "Qualify this runtime (kriya model qualify) to measure its tokenizer.",
            model, predicted, decision.get("counting_method"), reported_prompt_tokens,
        )
    return {"reported_prompt_tokens": reported_prompt_tokens, "under_predicted": under}


__all__ = [
    "CONTEXT_BUDGET_UNSATISFIABLE", "ContextBudgetUnsatisfiableError", "DEFAULT_BYTES_PER_TOKEN",
    "DEFAULT_NON_ASCII_BYTES_PER_TOKEN",
    "DEFAULT_MIN_OUTPUT_TOKENS", "DEFAULT_REASONING_ALLOWANCE_TOKENS", "DISPATCH_SAFETY_MARGIN_TOKENS",
    "DispatchBudget", "TWO_MESSAGE_FRAMING_TOKENS", "TokenCount",
    "bound_by_bytes", "compare_with_usage", "count_tokens", "dispatch_text", "plan_dispatch",
    "register_exact_counter", "unregister_exact_counter",
]
