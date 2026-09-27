"""Kriya's static-analysis policy (PRD-031A §7). Pure and deterministic.

Adapters never see it. Inputs: classified findings, confirmed coverage, the
operator's policy settings, and a waiver matcher. Output: one outcome, its
reason codes, and a decision per finding with its basis (policy or waiver).

Outcome precedence: UNKNOWN > UNAVAILABLE > BLOCKED > ACCEPTED_RISK >
PASS_WITH_WARNINGS > PASS. Only findings are waivable; a coverage gap is
decided by policy alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from kriya.static_analysis.model import (
    COVERAGE_PARTIAL,
    COVERAGE_UNSUPPORTED,
    EXISTING_FINDING_BLOCKED,
    FINDING_WARNED,
    NEW_FINDING_BLOCKED,
    PREREQUISITES_MISSING,
    SCAN_INCOMPLETE,
    TARGET_OVERSIZED,
    WAIVER_APPLIED,
    WORSENED_FINDING_BLOCKED,
    Classification,
    ClassifiedFinding,
    CoverageReport,
    CoverageStatus,
    Outcome,
    TargetStatus,
)

ALLOW, WARN, BLOCK, ACCEPTED = "allow", "warn", "block", "accepted"

_PRECEDENCE = (
    Outcome.UNKNOWN, Outcome.UNAVAILABLE, Outcome.BLOCKED,
    Outcome.ACCEPTED_RISK, Outcome.PASS_WITH_WARNINGS, Outcome.PASS,
)
_BLOCK_REASON = {
    Classification.INTRODUCED: NEW_FINDING_BLOCKED,
    Classification.WORSENED: WORSENED_FINDING_BLOCKED,
    Classification.UNCHANGED: EXISTING_FINDING_BLOCKED,
}


@dataclass(frozen=True)
class FindingDecision:
    item: ClassifiedFinding
    decision: str                    # allow | warn | block | accepted
    basis: str                       # policy | waiver
    waiver_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        f = self.item.finding
        return {
            "fingerprint": self.item.fingerprint, "rule_id": f.rule_id, "severity": f.severity.value,
            "classification": self.item.classification.value, "path": f.path,
            "range": {"start_line": f.start_line, "start_col": f.start_col,
                      "end_line": f.end_line, "end_col": f.end_col},
            "cwe": list(f.cwe), "owasp": list(f.owasp), "category": f.category,
            "pre_state": self.item.pre_state, "post_state": self.item.post_state,
            "decision": self.decision, "basis": self.basis, "waiver_id": self.waiver_id,
            "raw_ref": {"scan_id": self.item.side.value, "index": f.raw_index},
            "message": f.message,
        }


@dataclass(frozen=True)
class PolicyVerdict:
    outcome: Outcome
    reason_codes: Tuple[str, ...]
    decisions: Tuple[FindingDecision, ...]


def finding_decision(item: ClassifiedFinding, policy: Any) -> str:
    """policy.<introduced|worsened|existing>.<severity>; resolved is always allowed."""
    if item.classification is Classification.RESOLVED:
        return ALLOW
    table = {
        Classification.INTRODUCED: policy.introduced,
        Classification.WORSENED: policy.worsened,
        Classification.UNCHANGED: policy.existing,
    }[item.classification]
    return getattr(table, item.finding.severity.decided_as.value)


def coverage_effects(coverage: CoverageReport, policy: Any) -> List[Tuple[Outcome, str]]:
    """(outcome contribution, reason code) for every uncovered target class.

    A supported target the scanner did not confirm, or one Kriya did not
    submit because it is oversized, is incomplete evidence: UNKNOWN under
    ``analysis_errors: block``, otherwise partial coverage. It is never
    PASS."""
    effects: List[Tuple[Outcome, str]] = []

    def partial(reason: str) -> None:
        effects.append((Outcome.BLOCKED if policy.partial_coverage == BLOCK else Outcome.PASS_WITH_WARNINGS, reason))

    statuses = {t.status for t in coverage.uncovered}
    for status, reason in ((TargetStatus.NOT_ANALYZED, SCAN_INCOMPLETE), (TargetStatus.OVERSIZED, TARGET_OVERSIZED)):
        if status in statuses:
            if policy.analysis_errors == BLOCK:
                effects.append((Outcome.UNKNOWN, reason))
            else:
                partial(reason)
    if TargetStatus.PREREQUISITE_MISSING in statuses:
        # A coverage condition the operator set to block blocks under any
        # requirement (UNAVAILABLE would let an optional requirement commit).
        effects.append((
            Outcome.BLOCKED if policy.prerequisites_missing == BLOCK else Outcome.PASS_WITH_WARNINGS,
            PREREQUISITES_MISSING,
        ))
    if statuses & {TargetStatus.UNSUPPORTED_LANGUAGE, TargetStatus.NO_RULES}:
        if coverage.status is CoverageStatus.UNSUPPORTED:
            effects.append((
                Outcome.BLOCKED if policy.unsupported_language == BLOCK else Outcome.PASS_WITH_WARNINGS,
                COVERAGE_UNSUPPORTED,
            ))
        else:
            partial(COVERAGE_PARTIAL)
    return effects


WaiverMatcher = Callable[[Sequence[ClassifiedFinding]], Dict[str, Any]]


def decide(
    classified: Sequence[ClassifiedFinding], coverage: CoverageReport, policy: Any, match_waivers: WaiverMatcher,
) -> PolicyVerdict:
    """``match_waivers(blocked findings)`` returns fingerprint -> applied
    waiver (anything with a ``waiver_id``); it is consulted only for
    findings the policy would block."""
    raw = [(item, finding_decision(item, policy)) for item in classified]
    applied = match_waivers([item for item, decision in raw if decision == BLOCK])
    decisions: List[FindingDecision] = []
    contributions: List[Tuple[Outcome, str]] = coverage_effects(coverage, policy)
    for item, decision in raw:
        waiver = applied.get(item.fingerprint) if decision == BLOCK else None
        if waiver is not None:
            decisions.append(FindingDecision(item, ACCEPTED, "waiver", waiver.waiver_id))
            contributions.append((Outcome.ACCEPTED_RISK, WAIVER_APPLIED))
            contributions.append((Outcome.ACCEPTED_RISK, _BLOCK_REASON[item.classification]))
            continue
        decisions.append(FindingDecision(item, decision, "policy"))
        if decision == BLOCK:
            contributions.append((Outcome.BLOCKED, _BLOCK_REASON[item.classification]))
        elif decision == WARN:
            contributions.append((Outcome.PASS_WITH_WARNINGS, FINDING_WARNED))
    outcome = next(
        (candidate for candidate in _PRECEDENCE if any(o is candidate for o, _ in contributions)), Outcome.PASS,
    )
    reasons = tuple(sorted({reason for _, reason in contributions}))
    return PolicyVerdict(outcome=outcome, reason_codes=reasons, decisions=tuple(decisions))


def permits_commit(outcome: Outcome, *, requirement: str, when_unavailable: str) -> bool:
    """The requirement x outcome table (§7.2). UNKNOWN and BLOCKED never
    commit; UNAVAILABLE commits only for an optional requirement whose
    operator policy says warn (production seals requirement=required)."""
    if outcome in (Outcome.PASS, Outcome.PASS_WITH_WARNINGS, Outcome.ACCEPTED_RISK, Outcome.DISABLED):
        return True
    if outcome is Outcome.UNAVAILABLE:
        return requirement == "optional" and when_unavailable == WARN
    return False
