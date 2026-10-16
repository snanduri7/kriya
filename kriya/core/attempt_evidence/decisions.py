"""Typed decision records (full modularization D1; MODULE-CONTRACTS §5, §12).

One frozen record per Scenario-04 decision kind, recorded in the attempt evidence store under its own kind:

==================================  ==================================  ============================================
record                              kind                                ``decision`` (closed vocabulary)
==================================  ==================================  ============================================
``AttributionDecisionRecord``       ``attribution.decision``            ``ATTRIBUTION_DECISIONS``
``RetryTargetDecisionRecord``       ``retry_target.decision``           ``RETRY_TARGET_ORIGINS`` (§12 ``origin``)
``CandidateTransactionRecord``      ``candidate_transaction.decision``  ``CANDIDATE_TRANSACTION_DECISIONS``
``BudgetDecisionRecord``            ``budget.decision``                 ``BUDGET_ACTIONS`` (§12 ``action``)
``FallbackDecisionRecord``          ``fallback.decision`` (existing)    ``FALLBACK_DECISIONS``
==================================  ==================================  ============================================

EVIDENCE is a layer below the KERNEL, so these are the RECORDED form of the kernel's decision types (``AttributionDecision``,
``RetryTargetDecision``, ``CandidateTransaction``, ``BudgetDecision``, ``FallbackDecision`` arrive in F2/G2/H2): primitive fields only
(strings, numbers, tuples, digests), every closed value validated on construction so a record that does not fit the vocabulary is refused
(``ValueError``) before anything is written. The kernel slices convert their types into these records and test that their enum values
equal the tables below (``ATTRIBUTION_PROVENANCES``, ``EVIDENCE_CLASSES``, ``BUDGET_PHASES``, ...); this module never imports the kernel.

Content never sits in a payload (model prose, reasoning text, source bytes): a record names it by ``evidence_ref`` / digest only.

``emit_decision(record)`` records one under the current scope and never raises (the recorder is observational, design invariant I-2).
No production code emits these yet: F5/G5/H5 add the emitters. A typed ``fallback.decision`` payload carries ``phase`` = ``typed``
(``FALLBACK_TYPED_PHASE``) so it never matches the existing ``call``/``routing`` fallback records the recorder and ``explain`` read.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Optional, Protocol, Tuple

from kriya.core.attempt_evidence import scope

logger = logging.getLogger(__name__)

DECISION_SCHEMA = "kriya.decision/1"

ATTRIBUTION_KIND = "attribution.decision"
RETRY_TARGET_KIND = "retry_target.decision"
CANDIDATE_TRANSACTION_KIND = "candidate_transaction.decision"
BUDGET_KIND = "budget.decision"
FALLBACK_KIND = "fallback.decision"
DECISION_KINDS: Tuple[str, ...] = (ATTRIBUTION_KIND, RETRY_TARGET_KIND, CANDIDATE_TRANSACTION_KIND, BUDGET_KIND, FALLBACK_KIND)

# §12 vocabularies, recorded as their string values (the kernel enums arrive in F2/G2/H2 and must equal these).
EVIDENCE_CLASSES: Tuple[str, ...] = ("derived_deterministic", "authoritative_structural", "other_deterministic", "model_claimed")
ATTRIBUTION_PROVENANCES: Tuple[str, ...] = ("deterministic_exact_locator", "structured_authoritative_file_set", "structural_dependency",
                                            "model_self_diagnosis", "model_triage")
CONFIDENCES: Tuple[str, ...] = ("high", "medium", "low")
ATTRIBUTION_DECISIONS: Tuple[str, ...] = ("attributed", "unattributed")
RETRY_TARGET_ORIGINS: Tuple[str, ...] = ("original", "expansion", "fallback")
PROPOSAL_PARSES: Tuple[str, ...] = ("ok", "invalid")
INDEPENDENCE_VERDICTS: Tuple[str, ...] = ("independent", "dependent")
CANDIDATE_TRANSACTION_DECISIONS: Tuple[str, ...] = ("staged", "partially_staged", "refused")
BUDGET_PHASES: Tuple[str, ...] = ("planner", "developer", "repair", "verification", "fallback_reserve", "human_wait", "liveness")
BUDGET_ACTIONS: Tuple[str, ...] = ("admit", "refuse", "warn")
BUDGET_ESTIMATE_SOURCES: Tuple[str, ...] = ("provider_model_timing", "configured_fallback")
FALLBACK_DECISIONS: Tuple[str, ...] = ("selected", "refused")
FALLBACK_TYPED_PHASE = "typed"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _member(name: str, value: Any, allowed: Tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} {value!r} is not one of {allowed}")


def _text(name: str, value: Any, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def _texts(name: str, values: Any) -> None:
    if not isinstance(values, tuple) or not all(isinstance(item, str) and item for item in values):
        raise ValueError(f"{name} must be a tuple of non-empty strings, got {values!r}")


def _pairs(name: str, values: Any) -> None:
    if not isinstance(values, tuple) or not all(isinstance(item, tuple) and len(item) == 2 and all(isinstance(part, str) and part for part in item)
                                                for item in values):
        raise ValueError(f"{name} must be a tuple of (str, str) pairs, got {values!r}")


def _digest(name: str, value: Any, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise ValueError(f"{name} must be a 'sha256:<64 hex>' digest, got {value!r}")


def _seconds(name: str, value: Any, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:  # NaN is not a measurement
        raise ValueError(f"{name} must be a number of seconds, got {value!r}")


class DecisionRecord(Protocol):
    """MODULE-CONTRACTS §5: what every typed decision record carries."""

    kind: ClassVar[str]
    decision: str
    reason_code: str
    inputs_digest: str
    attempt: int
    unit: Optional[str]

    def payload(self) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class _Decision:
    """The fields every record shares (validated here; each record adds its own)."""

    decision: str
    reason_code: str
    inputs_digest: str
    attempt: int
    unit: Optional[str]

    kind: ClassVar[str] = ""
    decisions: ClassVar[Tuple[str, ...]] = ()

    def __post_init__(self) -> None:
        _member(f"{self.kind} decision", self.decision, self.decisions)
        _text("reason_code", self.reason_code)
        _digest("inputs_digest", self.inputs_digest)
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValueError(f"attempt must be a non-negative int, got {self.attempt!r}")
        _text("unit", self.unit, optional=True)

    def _common(self) -> Dict[str, Any]:
        return {"decision_schema": DECISION_SCHEMA, "decision": self.decision, "reason_code": self.reason_code,
                "inputs_digest": self.inputs_digest, "attempt": self.attempt, "unit": self.unit}


@dataclass(frozen=True)
class LocatorRef:
    """§12 ``Locator``: a deterministic file locator as recorded."""

    file: str
    line: Optional[int]
    symbol: Optional[str]
    evidence_class: str
    source: str

    def __post_init__(self) -> None:
        _text("locator file", self.file)
        if self.line is not None and (isinstance(self.line, bool) or not isinstance(self.line, int) or self.line < 1):
            raise ValueError(f"locator line must be a positive int, got {self.line!r}")
        _text("locator symbol", self.symbol, optional=True)
        _member("locator evidence_class", self.evidence_class, EVIDENCE_CLASSES)
        _text("locator source", self.source)

    def payload(self) -> Dict[str, Any]:
        return {"file": self.file, "line": self.line, "symbol": self.symbol, "evidence_class": self.evidence_class, "source": self.source}


@dataclass(frozen=True)
class AttributionSourceRef:
    """§12 ``AttributionSource`` as recorded: authority is read from these fields, never from which optional field was set. The
    source's reasoning text is content, named by ``evidence_ref`` only."""

    tier: str
    evidence_class: str
    provenance: str
    authority_level: int
    files: Tuple[str, ...]
    confidence: str
    evidence_ref: str
    exhausted: bool

    def __post_init__(self) -> None:
        _text("source tier", self.tier)
        _member("source evidence_class", self.evidence_class, EVIDENCE_CLASSES)
        _member("source provenance", self.provenance, ATTRIBUTION_PROVENANCES)
        if isinstance(self.authority_level, bool) or not isinstance(self.authority_level, int):
            raise ValueError(f"authority_level must be an int, got {self.authority_level!r}")
        _texts("source files", self.files)
        _member("source confidence", self.confidence, CONFIDENCES)
        _text("evidence_ref", self.evidence_ref)
        if not isinstance(self.exhausted, bool):
            raise ValueError(f"exhausted must be a bool, got {self.exhausted!r}")

    def payload(self) -> Dict[str, Any]:
        return {"tier": self.tier, "evidence_class": self.evidence_class, "provenance": self.provenance,
                "authority_level": self.authority_level, "files": list(self.files), "confidence": self.confidence,
                "evidence_ref": self.evidence_ref, "exhausted": self.exhausted}


def _sources(name: str, values: Any) -> None:
    if not isinstance(values, tuple) or not all(isinstance(item, AttributionSourceRef) for item in values):
        raise ValueError(f"{name} must be a tuple of AttributionSourceRef, got {values!r}")


@dataclass(frozen=True)
class AttributionDecisionRecord(_Decision):
    """§12 ``AttributionDecision``: which source attributed one finding, what else was considered and which advisory evidence was ignored.
    ``attributed`` names a ``selected`` source; ``unattributed`` has none."""

    finding_id: str = ""
    selected: Optional[AttributionSourceRef] = None
    considered: Tuple[AttributionSourceRef, ...] = ()
    ignored_advisory: Tuple[AttributionSourceRef, ...] = ()
    precedence_rule: str = ""

    kind: ClassVar[str] = ATTRIBUTION_KIND
    decisions: ClassVar[Tuple[str, ...]] = ATTRIBUTION_DECISIONS

    def __post_init__(self) -> None:
        super().__post_init__()
        _text("finding_id", self.finding_id)
        if (self.decision == "attributed") != isinstance(self.selected, AttributionSourceRef):
            raise ValueError("an attributed decision names exactly one selected AttributionSourceRef; an unattributed one names none")
        _sources("considered", self.considered)
        _sources("ignored_advisory", self.ignored_advisory)
        _text("precedence_rule", self.precedence_rule)

    def payload(self) -> Dict[str, Any]:
        return {**self._common(), "finding_id": self.finding_id,
                "selected": self.selected.payload() if self.selected is not None else None,
                "considered": [item.payload() for item in self.considered],
                "ignored_advisory": [item.payload() for item in self.ignored_advisory], "precedence_rule": self.precedence_rule}


@dataclass(frozen=True)
class RetryTargetDecisionRecord(_Decision):
    """§12 ``RetryTargetDecision`` (owner directive 2 #6): ONE record answers which findings were unresolved, the authoritative locator,
    the advisory evidence, the rule applied, what was selected and what was rejected and why. ``decision`` is the §12 ``origin``."""

    findings: Tuple[str, ...] = ()
    attribution: Tuple[str, ...] = ()  # the finding ids whose attribution.decision records this target follows
    selected_files: Tuple[str, ...] = ()
    read_only_context: Tuple[str, ...] = ()
    ignored_advisory: Tuple[Tuple[str, str], ...] = ()      # (file, why)
    rejected_candidates: Tuple[Tuple[str, str], ...] = ()   # (file, reason)
    authoritative_locator: Optional[LocatorRef] = None
    advisory_evidence: Tuple[AttributionSourceRef, ...] = ()
    scope_filter: str = ""
    precedence_rule: str = ""

    kind: ClassVar[str] = RETRY_TARGET_KIND
    decisions: ClassVar[Tuple[str, ...]] = RETRY_TARGET_ORIGINS

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("findings", "attribution", "selected_files", "read_only_context"):
            _texts(name, getattr(self, name))
        _pairs("ignored_advisory", self.ignored_advisory)
        _pairs("rejected_candidates", self.rejected_candidates)
        if self.authoritative_locator is not None and not isinstance(self.authoritative_locator, LocatorRef):
            raise ValueError(f"authoritative_locator must be a LocatorRef, got {self.authoritative_locator!r}")
        _sources("advisory_evidence", self.advisory_evidence)
        _text("scope_filter", self.scope_filter)
        _text("precedence_rule", self.precedence_rule)

    def payload(self) -> Dict[str, Any]:
        return {**self._common(), "origin": self.decision, "findings": list(self.findings), "attribution": list(self.attribution),
                "selected_files": list(self.selected_files), "read_only_context": list(self.read_only_context),
                "ignored_advisory": [list(pair) for pair in self.ignored_advisory],
                "rejected_candidates": [list(pair) for pair in self.rejected_candidates],
                "authoritative_locator": self.authoritative_locator.payload() if self.authoritative_locator is not None else None,
                "advisory_evidence": [item.payload() for item in self.advisory_evidence],
                "scope_filter": self.scope_filter, "precedence_rule": self.precedence_rule}


@dataclass(frozen=True)
class FileProposalRef:
    """§12 ``FileProposal`` as recorded (the edit result itself stays in the ``candidate.change`` records)."""

    path: str
    response_seq: int
    parse: str
    reason_code: Optional[str]
    staged_digest: Optional[str]
    findings_addressed: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text("proposal path", self.path)
        if isinstance(self.response_seq, bool) or not isinstance(self.response_seq, int) or self.response_seq < 0:
            raise ValueError(f"response_seq must be a non-negative int, got {self.response_seq!r}")
        _member("proposal parse", self.parse, PROPOSAL_PARSES)
        _text("proposal reason_code", self.reason_code, optional=True)
        _digest("staged_digest", self.staged_digest, optional=True)
        _texts("findings_addressed", self.findings_addressed)

    def payload(self) -> Dict[str, Any]:
        return {"path": self.path, "response_seq": self.response_seq, "parse": self.parse, "reason_code": self.reason_code,
                "staged_digest": self.staged_digest, "findings_addressed": list(self.findings_addressed)}


@dataclass(frozen=True)
class CandidateTransactionRecord(_Decision):
    """§12 ``CandidateTransaction``: the per-file proposals of one attempt, the independence proof between them, what was staged, what was
    refused and why, and which files had to stay atomic. ``staged_identity`` is the digest source review and the gates later see."""

    transaction_id: str = ""
    proposals: Tuple[FileProposalRef, ...] = ()
    independence: Tuple[Tuple[str, str, str, str], ...] = ()  # (a, b, verdict, basis)
    staged: Tuple[str, ...] = ()
    refused: Tuple[Tuple[str, str], ...] = ()                  # (path, reason)
    atomic_groups: Tuple[Tuple[str, ...], ...] = ()
    staged_identity: Optional[str] = None

    kind: ClassVar[str] = CANDIDATE_TRANSACTION_KIND
    decisions: ClassVar[Tuple[str, ...]] = CANDIDATE_TRANSACTION_DECISIONS

    def __post_init__(self) -> None:
        super().__post_init__()
        _text("transaction_id", self.transaction_id)
        if not isinstance(self.proposals, tuple) or not all(isinstance(item, FileProposalRef) for item in self.proposals):
            raise ValueError(f"proposals must be a tuple of FileProposalRef, got {self.proposals!r}")
        if not isinstance(self.independence, tuple) or not all(
                isinstance(item, tuple) and len(item) == 4 and all(isinstance(part, str) and part for part in item)
                and item[2] in INDEPENDENCE_VERDICTS for item in self.independence):
            raise ValueError(f"independence must be (a, b, {'|'.join(INDEPENDENCE_VERDICTS)}, basis) tuples, got {self.independence!r}")
        _texts("staged", self.staged)
        _pairs("refused", self.refused)
        if not isinstance(self.atomic_groups, tuple) or not all(isinstance(group, tuple) for group in self.atomic_groups):
            raise ValueError(f"atomic_groups must be a tuple of tuples, got {self.atomic_groups!r}")
        for group in self.atomic_groups:
            _texts("atomic group", group)
        _digest("staged_identity", self.staged_identity, optional=True)
        if (self.decision == "refused") != (not self.staged):
            raise ValueError("a refused transaction stages nothing; a staged or partially staged one stages at least one file")
        if self.decision == "partially_staged" and not self.refused:
            raise ValueError("a partially staged transaction refuses at least one file")

    def payload(self) -> Dict[str, Any]:
        return {**self._common(), "transaction_id": self.transaction_id, "proposals": [item.payload() for item in self.proposals],
                "independence": [list(item) for item in self.independence], "staged": list(self.staged),
                "refused": [list(pair) for pair in self.refused], "atomic_groups": [list(group) for group in self.atomic_groups],
                "staged_identity": self.staged_identity}


@dataclass(frozen=True)
class BudgetViewRef:
    """§12 ``BudgetView`` as recorded."""

    phase: str
    allowed_seconds: Optional[float]
    consumed_seconds: float
    remaining_seconds: Optional[float]
    liveness_remaining: Optional[float]

    def __post_init__(self) -> None:
        _member("view phase", self.phase, BUDGET_PHASES)
        _seconds("allowed_seconds", self.allowed_seconds, optional=True)
        _seconds("consumed_seconds", self.consumed_seconds)
        _seconds("remaining_seconds", self.remaining_seconds, optional=True)
        _seconds("liveness_remaining", self.liveness_remaining, optional=True)

    def payload(self) -> Dict[str, Any]:
        return {"phase": self.phase, "allowed_seconds": self.allowed_seconds, "consumed_seconds": self.consumed_seconds,
                "remaining_seconds": self.remaining_seconds, "liveness_remaining": self.liveness_remaining}


@dataclass(frozen=True)
class BudgetDecisionRecord(_Decision):
    """§12 ``BudgetDecision``: one admission decision of a budget phase. ``decision`` is the §12 ``action``."""

    phase: str = ""
    estimate_seconds: float = 0.0
    estimate_source: str = ""
    provider: str = ""
    model: str = ""
    file_count: int = 0
    view: Optional[BudgetViewRef] = None

    kind: ClassVar[str] = BUDGET_KIND
    decisions: ClassVar[Tuple[str, ...]] = BUDGET_ACTIONS

    def __post_init__(self) -> None:
        super().__post_init__()
        _member("phase", self.phase, BUDGET_PHASES)
        _seconds("estimate_seconds", self.estimate_seconds)
        _member("estimate_source", self.estimate_source, BUDGET_ESTIMATE_SOURCES)
        _text("provider", self.provider)
        _text("model", self.model)
        if isinstance(self.file_count, bool) or not isinstance(self.file_count, int) or self.file_count < 0:
            raise ValueError(f"file_count must be a non-negative int, got {self.file_count!r}")
        if not isinstance(self.view, BudgetViewRef) or self.view.phase != self.phase:
            raise ValueError("a budget decision carries the BudgetViewRef of its own phase")

    def payload(self) -> Dict[str, Any]:
        return {**self._common(), "action": self.decision, "phase": self.phase, "estimate_seconds": self.estimate_seconds,
                "estimate_source": self.estimate_source, "provider": self.provider, "model": self.model,
                "file_count": self.file_count, "view": self.view.payload() if self.view is not None else None}


@dataclass(frozen=True)
class FallbackDecisionRecord(_Decision):
    """§12 ``FallbackDecision`` (policy unchanged): the candidates, the one selected (or none), the skipped ones and why, the eligibility
    rule and the timing estimate it relied on. Recorded under the EXISTING ``fallback.decision`` kind with ``phase`` = ``typed``."""

    role: str = ""
    candidates: Tuple[str, ...] = ()
    selected: Optional[str] = None
    skipped: Tuple[Tuple[str, str], ...] = ()   # (candidate, reason)
    eligibility_rule: str = ""
    timing_estimate: Optional[BudgetDecisionRecord] = None

    kind: ClassVar[str] = FALLBACK_KIND
    decisions: ClassVar[Tuple[str, ...]] = FALLBACK_DECISIONS

    def __post_init__(self) -> None:
        super().__post_init__()
        _text("role", self.role)
        _texts("candidates", self.candidates)
        if (self.decision == "selected") != (self.selected is not None):
            raise ValueError("a selected fallback names its selected candidate; a refused one names none")
        if self.selected is not None and self.selected not in self.candidates:
            raise ValueError(f"selected {self.selected!r} is not one of the candidates {self.candidates!r}")
        _pairs("skipped", self.skipped)
        _text("eligibility_rule", self.eligibility_rule)
        if self.timing_estimate is not None and not isinstance(self.timing_estimate, BudgetDecisionRecord):
            raise ValueError(f"timing_estimate must be a BudgetDecisionRecord, got {self.timing_estimate!r}")

    def payload(self) -> Dict[str, Any]:
        return {**self._common(), "phase": FALLBACK_TYPED_PHASE, "role": self.role, "candidates": list(self.candidates),
                "selected": self.selected, "skipped": [list(pair) for pair in self.skipped], "eligibility_rule": self.eligibility_rule,
                "timing_estimate": self.timing_estimate.payload() if self.timing_estimate is not None else None}


RECORD_TYPES: Tuple[type, ...] = (AttributionDecisionRecord, RetryTargetDecisionRecord, CandidateTransactionRecord, BudgetDecisionRecord,
                                  FallbackDecisionRecord)


def emit_decision(record: DecisionRecord) -> None:
    """Record ``record`` under the current attempt-evidence scope (``scope.emit(record.kind, record.payload())``). Never raises: a record
    that cannot be built into a payload is logged and dropped; a kind the store refuses becomes a counted ``recorder.gap``."""
    try:
        kind, payload = record.kind, record.payload()
    except Exception as error:  # observational: never alters the run
        logger.warning("Attempt evidence: decision record not built (%s: %s)", type(error).__name__, error)
        return
    scope.emit(kind, payload)


__all__ = [
    "ATTRIBUTION_DECISIONS", "ATTRIBUTION_KIND", "ATTRIBUTION_PROVENANCES", "AttributionDecisionRecord", "AttributionSourceRef",
    "BUDGET_ACTIONS", "BUDGET_ESTIMATE_SOURCES", "BUDGET_KIND", "BUDGET_PHASES", "BudgetDecisionRecord", "BudgetViewRef",
    "CANDIDATE_TRANSACTION_DECISIONS", "CANDIDATE_TRANSACTION_KIND", "CONFIDENCES", "CandidateTransactionRecord", "DECISION_KINDS",
    "DECISION_SCHEMA", "DecisionRecord", "EVIDENCE_CLASSES", "FALLBACK_DECISIONS", "FALLBACK_KIND", "FALLBACK_TYPED_PHASE",
    "FallbackDecisionRecord", "FileProposalRef", "INDEPENDENCE_VERDICTS", "LocatorRef", "PROPOSAL_PARSES", "RECORD_TYPES",
    "RETRY_TARGET_KIND", "RETRY_TARGET_ORIGINS", "RetryTargetDecisionRecord", "emit_decision",
]
