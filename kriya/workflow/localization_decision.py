"""CI-6: ambiguity-only LLM localization (Code Intelligence R1 slice 2).

Deterministic localization answers first. Only when its top candidate is not
clearly separated (``is_clear``, calibrated on loc-N) is the model asked -
once - to choose among the candidates it was shown, through a
schema-constrained response (``LLMClient.complete_result(response_schema=)``;
only a runtime declaring ``json_schema_output`` is asked, any other is
skipped with a typed reason). The schema enumerates exactly the candidate
symbol ids, the whole response is parsed as one JSON document (never a
substring), and every returned id is validated against the CURRENT symbol
table: an unknown, stale or unshown id rejects the decision. The decision only
reorders localization candidates; it is never write authority - the
Developer still re-reads exact current bytes for any target.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DECISION_VERSION = "kriya-localization-decision/1"
# Calibration (loc-N, 724 cases, 5 repositories): top-1 resting on exact
# evidence and leading top-2 by at least this much was correct 95.3 % of the
# time (233 cases; every synthetic compiler/stack/traceback case), and the
# rate is flat for margins 10-40. Anything else is ambiguous.
CLEAR_MARGIN = 10.0
MAX_CANDIDATES = 12
MAX_TARGETS = 3

# Reason codes (closed).
CLEAR = "LOCALIZATION_CLEAR"
NO_CANDIDATES = "LOCALIZATION_NO_CANDIDATES"
SINGLE_CANDIDATE = "LOCALIZATION_SINGLE_CANDIDATE"
SCHEMA_UNSUPPORTED = "LOCALIZATION_SCHEMA_OUTPUT_UNSUPPORTED"
DECIDED = "LOCALIZATION_DECIDED"
MALFORMED = "LOCALIZATION_MALFORMED_OUTPUT"
UNKNOWN_SYMBOL = "LOCALIZATION_UNKNOWN_SYMBOL"
CALL_FAILED = "LOCALIZATION_CALL_FAILED"
REASON_CODES = (CLEAR, NO_CANDIDATES, SINGLE_CANDIDATE, SCHEMA_UNSUPPORTED, DECIDED, MALFORMED, UNKNOWN_SYMBOL,
                CALL_FAILED)

SYSTEM_PROMPT = (
    "You are Kriya's localization step. A change request and a ranked list of code candidates found "
    "deterministically are given. Choose the candidate member(s) the request requires changing "
    "(target_symbol_ids, at most 3), and optionally collaborators, tests and configuration entries that "
    "matter, using only the listed ids. Give one short reason per target. Answer only with the JSON object."
)


def is_clear(separation: Optional[Dict[str, Any]]) -> bool:
    """The calibrated deterministic decision: no model call is needed."""
    return bool(separation) and bool(separation.get("exact")) and separation.get("margin", 0.0) >= CLEAR_MARGIN


@dataclass(frozen=True)
class LocalizationDecision:
    target_symbol_ids: Tuple[str, ...]
    collaborator_symbol_ids: Tuple[str, ...] = ()
    test_symbol_ids: Tuple[str, ...] = ()
    config_symbol_ids: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()


@dataclass
class DecisionOutcome:
    """What the step did: called or not, why, and its measured cost."""

    reason_code: str
    called: bool = False
    decision: Optional[LocalizationDecision] = None
    rejected_ids: List[str] = field(default_factory=list)
    prompt_tokens: Optional[int] = None
    seconds: Optional[float] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"reason_code": self.reason_code, "called": self.called, "version": DECISION_VERSION,
                "targets": list(self.decision.target_symbol_ids) if self.decision else [],
                "rejected_ids": list(self.rejected_ids), "prompt_tokens": self.prompt_tokens,
                "seconds": None if self.seconds is None else round(self.seconds, 3), "detail": self.detail[:300]}


def decision_schema(candidate_ids: Sequence[str]) -> Dict[str, Any]:
    """The response schema: every id field enumerates exactly the shown
    candidates; no other property is allowed."""
    ids = {"type": "array", "items": {"type": "string", "enum": list(candidate_ids)}}
    return {
        "type": "object",
        "properties": {
            "target_symbol_ids": {**ids, "minItems": 1, "maxItems": MAX_TARGETS},
            "collaborator_symbol_ids": ids, "test_symbol_ids": ids, "config_symbol_ids": ids,
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_TARGETS},
        },
        "required": ["target_symbol_ids", "reasons"],
        "additionalProperties": False,
    }


def render_request(goal: str, candidates: Sequence[Any]) -> str:
    lines = [f"Change request:\n{goal.strip()}\n", "Candidates (deterministic rank; id, kind, location, signature, "
             "evidence channels):"]
    for rank, candidate in enumerate(candidates, 1):
        signature = " ".join(candidate.signature.split())[:160]
        lines.append(f"{rank}. id={candidate.symbol_id} [{candidate.kind}] {candidate.path}"
                     f"{' - ' + signature if signature else ''} (via {', '.join(c for c, _ in candidate.channels)})")
    return "\n".join(lines)


class DecisionRejected(Exception):
    def __init__(self, reason_code: str, detail: str, rejected: Sequence[str] = ()) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.rejected = list(rejected)


def parse_decision(content: str, shown_ids: Sequence[str],
                   current: Callable[[str], bool]) -> LocalizationDecision:
    """The whole response as one JSON object, every id among the shown
    candidates and present in the current symbol table (``current``)."""
    try:
        document = json.loads(content)
    except (TypeError, ValueError) as error:
        raise DecisionRejected(MALFORMED, f"not one JSON document: {error}") from None
    if not isinstance(document, dict) or set(document) - {
            "target_symbol_ids", "collaborator_symbol_ids", "test_symbol_ids", "config_symbol_ids", "reasons"}:
        raise DecisionRejected(MALFORMED, "not a LocalizationDecision object")
    fields: Dict[str, Tuple[str, ...]] = {}
    for name in ("target_symbol_ids", "collaborator_symbol_ids", "test_symbol_ids", "config_symbol_ids", "reasons"):
        value = document.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise DecisionRejected(MALFORMED, f"{name} is not a list of strings")
        fields[name] = tuple(dict.fromkeys(value))
    if not fields["target_symbol_ids"]:
        raise DecisionRejected(MALFORMED, "no target")
    shown = set(shown_ids)
    ids = [i for name, values in fields.items() if name != "reasons" for i in values]
    unknown = [i for i in ids if i not in shown or not current(i)]
    if unknown:
        raise DecisionRejected(UNKNOWN_SYMBOL, "ids outside the shown, current candidates", unknown)
    return LocalizationDecision(fields["target_symbol_ids"][:MAX_TARGETS], fields["collaborator_symbol_ids"],
                                fields["test_symbol_ids"], fields["config_symbol_ids"], fields["reasons"])


async def decide(llm: Any, goal: str, candidates: Sequence[Any], separation: Optional[Dict[str, Any]],
                 current: Callable[[str], bool]) -> DecisionOutcome:
    """At most one schema-constrained model call, only when deterministic
    localization is ambiguous."""
    from kriya.core.llm import StructuredOutputUnsupportedError
    from kriya.core.role_metrics import model_role
    from kriya.core.token_budget import ContextBudgetUnsatisfiableError

    shown = list(candidates[:MAX_CANDIDATES])
    if not shown:
        return DecisionOutcome(NO_CANDIDATES)
    if is_clear(separation):
        return DecisionOutcome(CLEAR)
    if len(shown) == 1:
        return DecisionOutcome(SINGLE_CANDIDATE)
    ids = [candidate.symbol_id for candidate in shown]
    started = time.monotonic()
    try:
        with model_role("localization"):
            result = await llm.complete_result(SYSTEM_PROMPT, render_request(goal, shown),
                                               response_schema=decision_schema(ids))
    except StructuredOutputUnsupportedError as error:
        return DecisionOutcome(SCHEMA_UNSUPPORTED, detail=str(error))
    except ContextBudgetUnsatisfiableError as error:
        return DecisionOutcome(CALL_FAILED, called=False, seconds=time.monotonic() - started, detail=str(error))
    seconds = time.monotonic() - started
    if not result.ok or not result.content:
        return DecisionOutcome(CALL_FAILED, called=True, seconds=seconds, prompt_tokens=result.prompt_tokens,
                               detail=str(result.error or result.status.value))
    try:
        decision = parse_decision(result.content, ids, current)
    except DecisionRejected as rejected:
        return DecisionOutcome(rejected.reason_code, called=True, rejected_ids=rejected.rejected,
                               prompt_tokens=result.prompt_tokens, seconds=seconds, detail=str(rejected))
    return DecisionOutcome(DECIDED, called=True, decision=decision, prompt_tokens=result.prompt_tokens,
                           seconds=seconds)


def apply_decision(candidates: Sequence[Any], decision: LocalizationDecision) -> List[Any]:
    """The chosen targets first (in the model's order), then every other
    candidate in its deterministic order - nothing is dropped or invented."""
    chosen = [c for i in decision.target_symbol_ids for c in candidates if c.symbol_id == i]
    return chosen + [c for c in candidates if c.symbol_id not in decision.target_symbol_ids]
