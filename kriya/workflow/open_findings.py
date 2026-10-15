"""Open semantic findings: what a validator said is wrong with the CANDIDATE, kept until a validator says otherwise.

D1 (live Greenfield failure, run 20261015T152407-5f87716a). A repair request used to carry only the latest failure. When a
later response failed on its SHAPE alone (CONFLICTING_DEVELOPER_RESPONSE, an anchor, a mismatched diagnosis), the substantive
defect the validator had already found was still true of the candidate but vanished from every following request.

Two kinds of failure, kept apart:

* DELIVERY failures judge only the Developer's response (its shape, anchors, consistency). They say nothing about whether the
  candidate is correct, so they neither open nor close a finding. They are attempt-local and are never accumulated.
* CANDIDATE findings (any validator-owned type that is not a delivery failure or an operational/internal catch-all) are statements about the candidate
  itself. An authoritative one becomes the open set; it is replaced by the next authoritative candidate finding, because
  gates evaluate a candidate in a fixed order and raise the first violation, so a candidate that reaches another gate has
  passed (or is no longer subject to) the earlier check. Nothing else clears it. Every other failure type (resource, internal,
  unclassified) says nothing about the candidate and leaves the set unchanged.

A finding's subject (type, reason code, files, source, validator evidence) is copied from the validator's own Failure and is
immutable: model text never edits it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from kriya.workflow.failure_reporting import FailureCategory, categorize_failure

# Failures that judge only the Developer's RESPONSE. (Single definition: retry_strategy's family inheritance uses it too.)
DELIVERY_FAILURE_TYPES = frozenset({
    "anchored_edit",
    "attribution_rejected",
    "diagnosis_mismatch",
    "misdirected_edit",
    "no_op_edit",
    "operation_contract",
    "structural_corruption",
    "unaddressed_error_location",
})

# Failures that say nothing about the candidate's content: operational / internal / unclassified catch-alls. Everything else that is
# authoritative and not a delivery failure is a statement about the candidate and is KEPT - a validator type the category table does not
# know yet must default to remembered, not forgotten (review of D1: candidate_runtime_entrypoint_invalid, prose_contamination, ...).
_NON_CANDIDATE_CATEGORIES = frozenset({FailureCategory.RESOURCE, FailureCategory.INTERNAL})
_NON_CANDIDATE_TYPES = frozenset({"general_error", "containment_setup_failed", "plan_scope_conflict"})

MAX_OPEN_FINDINGS = 3
MAX_EVIDENCE_CHARS = 2000


@dataclass(frozen=True)
class OpenFinding:
    """One unresolved validator finding about the candidate (immutable)."""

    failure_type: str
    reason_code: Optional[str]
    files: Tuple[str, ...]
    source: str
    evidence: str
    first_attempt: int

    def render(self) -> str:
        subject = f" (files: {', '.join(self.files)})" if self.files else ""
        code = f" {self.reason_code}" if self.reason_code else ""
        return f"- [{self.failure_type}{code}]{subject}\n{self.evidence}"


def is_candidate_finding(failure_type: str) -> bool:
    return (failure_type not in DELIVERY_FAILURE_TYPES and failure_type not in _NON_CANDIDATE_TYPES
            and categorize_failure(failure_type) not in _NON_CANDIDATE_CATEGORIES)


def _head_and_tail(text: str, limit: int) -> str:
    """Build and test output carries its decisive lines at the end: keep both ends."""
    if len(text) <= limit:
        return text
    marker = "\n... [middle omitted] ...\n"
    keep = limit - len(marker)
    return text[: keep // 2] + marker + text[-(keep - keep // 2):]


def finding_from_failure(failure: Any, attempt: int) -> OpenFinding:
    evidence = str(failure.raw_output or failure.message or "")
    locations = [location.filepath for location in failure.file_locations]
    files = tuple(dict.fromkeys([*locations, *failure.likely_files]))
    return OpenFinding(
        failure_type=failure.type,
        reason_code=(failure.diagnostics or {}).get("reason_code"),
        files=files,
        source=failure.source,
        evidence=_head_and_tail(evidence, MAX_EVIDENCE_CHARS),
        first_attempt=attempt,
    )


def update_open_findings(open_findings: Sequence[OpenFinding], failure: Any, attempt: int) -> List[OpenFinding]:
    """The open set after ``failure`` was recorded. Only an authoritative candidate finding changes it."""
    if failure.type in DELIVERY_FAILURE_TYPES or not is_candidate_finding(failure.type):
        return list(open_findings)
    if getattr(failure, "authority", "authoritative") != "authoritative":
        return list(open_findings)
    return [finding_from_failure(failure, attempt)]


def render_open_findings(open_findings: Sequence[OpenFinding]) -> str:
    """The model-facing block, bounded by MAX_OPEN_FINDINGS x MAX_EVIDENCE_CHARS."""
    if not open_findings:
        return ""
    blocks = "\n".join(finding.render() for finding in open_findings[:MAX_OPEN_FINDINGS])
    return (
        "Still unresolved from an earlier attempt (no later validator has cleared this finding, so it still applies and "
        "must also be fixed):\n" + blocks
    )


def open_finding_files(open_findings: Sequence[OpenFinding]) -> List[str]:
    """The validator-owned subjects of the open findings, in order, without repeats."""
    return list(dict.fromkeys(path for finding in open_findings for path in finding.files))
