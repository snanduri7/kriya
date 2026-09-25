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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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
            + (f", but the largest allowed context window is {decision.context_window} "
               f"({decision.qualification_source}; preferred {decision.preferred_context_window}). "
               if getattr(decision, "context_expanded", False) else
               f", but the served context window is {decision.context_window} ({decision.window_source}). ")
            + "Nothing was sent to the model."
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


# --- CTX-001 / PRD-016 adaptive budget -------------------------------------
# A binding's configured window (num_ctx) and max_tokens are its PREFERRED
# operating values. In ``adaptive`` mode a request whose required prompt plus
# expected output does not fit the preferred window may be sent with a
# larger context tier, but only a QUALIFIED one (a current PRD-014 record for
# the runtime at that num_ctx, or - while no qualification data exists for
# it - a tier the operator explicitly declared safe), never above the hard
# policy ceiling or the model's trained length, and always the SMALLEST tier
# that fits. The output allowance grows above max_tokens only for a GROUNDED
# expectation (e.g. the size of a file being rewritten), never because a
# model produced more than expected. ``strict`` mode never exceeds the
# preferred values.
POLICY_ADAPTIVE = "adaptive"
POLICY_STRICT = "strict"
POLICY_MODES = (POLICY_ADAPTIVE, POLICY_STRICT)

TIER_SOURCE_PREFERRED = "preferred"
TIER_SOURCE_QUALIFICATION_RECORD = "qualification_record"
TIER_SOURCE_OPERATOR_DECLARED = "operator_declared"

OUTPUT_BUDGET_UNSATISFIABLE = "OUTPUT_BUDGET_UNSATISFIABLE"


@dataclass(frozen=True)
class ContextTier:
    """A context window a request may be sent with, and why it is allowed."""
    tokens: int
    source: str


@dataclass(frozen=True)
class OutputExpectation:
    """Output a request is expected to need, and what that is grounded in
    (never the model's own behaviour)."""
    tokens: int
    grounding: str


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
    # Adaptive selection evidence (all defaulted: a request with only its
    # preferred tier and no expectation records "preferred").
    preferred_context_window: Optional[int] = None
    expected_output_tokens: Optional[int] = None
    output_grounding: Optional[str] = None
    context_expanded: bool = False
    output_expanded: bool = False
    selection_reason: str = "fits_preferred"
    qualification_source: str = TIER_SOURCE_PREFERRED
    hard_context_ceiling: Optional[int] = None
    hard_output_ceiling: Optional[int] = None
    policy_mode: str = POLICY_ADAPTIVE
    safety_margin: int = 0
    considered_tiers: Tuple[Dict[str, Any], ...] = ()

    @property
    def approximate(self) -> bool:
        return not self.exact

    @property
    def expanded(self) -> bool:
        return self.context_expanded or self.output_expanded

    @property
    def required_tokens(self) -> int:
        needed = self.expected_output_tokens or self.min_output_tokens
        return self.prompt_tokens + needed + self.reasoning_allowance + self.safety_margin

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
            "policy_mode": self.policy_mode,
            "preferred_context_window": self.preferred_context_window,
            "preferred_output_tokens": self.requested_max_tokens,
            "selected_context_window": self.context_window,
            "selected_output_tokens": self.max_tokens,
            "required_tokens": self.required_tokens,
            "expected_output_tokens": self.expected_output_tokens,
            "output_grounding": self.output_grounding,
            "context_expanded": self.context_expanded,
            "output_expanded": self.output_expanded,
            "selection_reason": self.selection_reason,
            "qualification_source": self.qualification_source,
            "hard_context_ceiling": self.hard_context_ceiling,
            "hard_output_ceiling": self.hard_output_ceiling,
            "safety_margin": self.safety_margin,
            "considered_tiers": [dict(tier) for tier in self.considered_tiers],
        }


class OutputBudgetUnsatisfiableError(ContextBudgetUnsatisfiableError):
    """The grounded expected output cannot be produced within any allowed
    output budget, although the prompt itself fits. A subclass so every
    handler of an unsatisfiable budget handles it; ``reason_code`` tells the
    two apart."""

    def __init__(self, decision: "DispatchBudget", detail: str):
        RuntimeError.__init__(
            self,
            f"{OUTPUT_BUDGET_UNSATISFIABLE}: {detail} Nothing was sent to the model.",
        )
        self.decision = decision
        self.reason_code = OUTPUT_BUDGET_UNSATISFIABLE


def _candidate_tiers(preferred: int, tiers: Sequence[ContextTier], mode: str,
                     ceiling: Optional[int]) -> List[ContextTier]:
    """The preferred window, then (adaptive only) every larger allowed tier
    up to the ceiling, ascending; the first source listed for a size wins."""
    candidates = [ContextTier(preferred, TIER_SOURCE_PREFERRED)]
    if mode != POLICY_ADAPTIVE:
        return candidates
    seen = {preferred}
    for tier in sorted(tiers, key=lambda t: t.tokens):
        if tier.tokens <= preferred or tier.tokens in seen:
            continue
        if ceiling is not None and tier.tokens > ceiling:
            continue
        seen.add(tier.tokens)
        candidates.append(tier)
    return candidates


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
    tiers: Sequence[ContextTier] = (),
    policy_mode: str = POLICY_ADAPTIVE,
    expected_output: Optional[OutputExpectation] = None,
    hard_context_ceiling: Optional[int] = None,
    hard_output_ceiling: Optional[int] = None,
    safety_margin: int = DISPATCH_SAFETY_MARGIN_TOKENS,
) -> DispatchBudget:
    """Decide the context window and output budget of one request.

    ``context_window`` is the preferred (served) window, ``requested_max_tokens``
    the preferred output. Raises ContextBudgetUnsatisfiableError when no
    allowed window holds the prompt plus the minimum output, and
    OutputBudgetUnsatisfiableError when the prompt fits but the grounded
    expected output cannot."""
    counted = count_tokens(
        dispatch_text(messages, tools), tokenizer_digest=tokenizer_digest,
        qualified_bytes_per_token=qualified_bytes_per_token,
        qualified_non_ascii_bytes_per_token=qualified_non_ascii_bytes_per_token,
    )
    overhead = REQUEST_OVERHEAD_TOKENS + PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
    prompt_tokens = counted.tokens + overhead
    minimum = min(min_output_tokens, requested_max_tokens)
    grounded = expected_output if expected_output and expected_output.tokens > 0 else None
    common = dict(
        prompt_tokens=prompt_tokens, exact=counted.exact, counting_method=counted.method,
        window_source=window_source, requested_max_tokens=requested_max_tokens,
        min_output_tokens=minimum, reasoning_allowance=reasoning_allowance,
        preferred_context_window=context_window,
        expected_output_tokens=grounded.tokens if grounded else None,
        output_grounding=grounded.grounding if grounded else None,
        hard_context_ceiling=hard_context_ceiling, hard_output_ceiling=hard_output_ceiling,
        policy_mode=policy_mode, safety_margin=safety_margin,
    )

    # The output allowance: the preferred max_tokens, enlarged (adaptive
    # only) to a grounded expectation, never above the hard output ceiling.
    allowance = requested_max_tokens
    if grounded and grounded.tokens > requested_max_tokens and policy_mode == POLICY_ADAPTIVE:
        allowance = grounded.tokens
        if hard_output_ceiling is not None:
            allowance = min(allowance, hard_output_ceiling)
    needed_output = grounded.tokens if grounded else minimum

    if not context_window:
        return DispatchBudget(
            context_window=None, max_tokens=allowance, satisfiable=True, output_reduced=False,
            output_expanded=allowance > requested_max_tokens, selection_reason="window_unknown", **common,
        )

    considered: List[Dict[str, Any]] = []
    # max_tokens bounds hidden reasoning and visible output together, so a
    # window fits when it holds the prompt, the needed output, the reasoning
    # allowance and the safety margin.
    candidates = _candidate_tiers(context_window, tiers, policy_mode, hard_context_ceiling)
    chosen: Optional[ContextTier] = None
    for tier in candidates:
        room = tier.tokens - prompt_tokens - safety_margin
        fits = room >= needed_output + reasoning_allowance and needed_output <= allowance
        considered.append({"tokens": tier.tokens, "source": tier.source, "room": room, "fits": fits})
        if fits:
            chosen = tier
            break

    if chosen is None:
        largest = candidates[-1]
        room = largest.tokens - prompt_tokens - safety_margin
        decision = DispatchBudget(
            context_window=largest.tokens, max_tokens=max(0, min(allowance, room)), satisfiable=False,
            output_reduced=room < requested_max_tokens, context_expanded=largest.tokens != context_window,
            selection_reason="no_allowed_window_fits", qualification_source=largest.source,
            considered_tiers=tuple(considered), **common,
        )
        prompt_fits = room >= minimum + reasoning_allowance
        if grounded and prompt_fits:
            ceiling_note = (f" (hard output ceiling {hard_output_ceiling})"
                            if hard_output_ceiling is not None and grounded.tokens > hard_output_ceiling else "")
            strict_note = " (strict budget policy: output is never enlarged)" \
                if policy_mode == POLICY_STRICT and grounded.tokens > requested_max_tokens else ""
            raise OutputBudgetUnsatisfiableError(
                decision,
                f"the request is expected to need about {grounded.tokens} output tokens ({grounded.grounding}), "
                f"but at most {min(allowance, room)} fit{ceiling_note}{strict_note}: the prompt needs about "
                f"{prompt_tokens} tokens and the largest allowed window is {largest.tokens} "
                f"({largest.source}).",
            )
        raise ContextBudgetUnsatisfiableError(decision)

    room = chosen.tokens - prompt_tokens - safety_margin
    max_tokens = max(0, min(allowance, room))
    expanded_context = chosen.tokens != context_window
    if expanded_context and grounded and prompt_tokens + minimum + reasoning_allowance + safety_margin <= context_window:
        reason = "grounded_output_exceeds_preferred_window"
    elif expanded_context:
        reason = "prompt_exceeds_preferred_window"
    elif max_tokens > requested_max_tokens:
        reason = "grounded_output_exceeds_preferred_output"
    else:
        reason = "fits_preferred"
    return DispatchBudget(
        context_window=chosen.tokens, max_tokens=max_tokens, satisfiable=True,
        output_reduced=max_tokens < requested_max_tokens, context_expanded=expanded_context,
        output_expanded=max_tokens > requested_max_tokens, selection_reason=reason,
        qualification_source=chosen.source, considered_tiers=tuple(considered), **common,
    )


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
    "CONTEXT_BUDGET_UNSATISFIABLE", "ContextBudgetUnsatisfiableError", "ContextTier",
    "OUTPUT_BUDGET_UNSATISFIABLE", "OutputBudgetUnsatisfiableError", "OutputExpectation",
    "POLICY_ADAPTIVE", "POLICY_MODES", "POLICY_STRICT", "TIER_SOURCE_OPERATOR_DECLARED",
    "TIER_SOURCE_PREFERRED", "TIER_SOURCE_QUALIFICATION_RECORD", "DEFAULT_BYTES_PER_TOKEN",
    "DEFAULT_NON_ASCII_BYTES_PER_TOKEN",
    "DEFAULT_MIN_OUTPUT_TOKENS", "DEFAULT_REASONING_ALLOWANCE_TOKENS", "DISPATCH_SAFETY_MARGIN_TOKENS",
    "DispatchBudget", "TWO_MESSAGE_FRAMING_TOKENS", "TokenCount",
    "bound_by_bytes", "compare_with_usage", "count_tokens", "dispatch_text", "plan_dispatch",
    "register_exact_counter", "unregister_exact_counter",
]
