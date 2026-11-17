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

import os
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from kriya.workflow.failure import ENVIRONMENT_VERDICT_FAILURE_TYPES
from kriya.workflow.failure_grounding import error_source_location_matches
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
# R-13: the environment-verdict types are non-candidate by definition. Before, ``verification_infrastructure_failure`` fell through to "a statement about the candidate"
# (MEASURED: it replaced the open finding and could open a new failure family, replenishing the scoped budgets, on its way to the environment stop).
_NON_CANDIDATE_TYPES = frozenset({"general_error", "containment_setup_failed", "plan_scope_conflict"}) | ENVIRONMENT_VERDICT_FAILURE_TYPES

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
    # The validator's own (file, line) locators (Failure.file_locations with a line). They pick the evidence excerpt and let a
    # later request show the source at those lines again (OPEN-FINDING-EVIDENCE-001).
    locations: Tuple[Tuple[str, int], ...] = ()

    def render(self) -> str:
        subject = f" (files: {', '.join(self.files)})" if self.files else ""
        code = f" {self.reason_code}" if self.reason_code else ""
        return f"- [{self.failure_type}{code}]{subject}\n{self.evidence}"


# S8 (C-07, GAP-A1-07): the failure types that may OPEN a failure family, i.e. earn the fresh scoped (targeted / fallback-targeted) allowance of a genuinely different
# failure. A closed allow-list, enumerated from the types the validators, gates and orchestrator produce today (tests/test_s8_failure_family_authority.py fails when a
# producer names a type that is in neither table). An UNLISTED type inherits the family the repair was already addressing: an unknown failure never widens a budget.
# Deliberately a separate question from ``replaces_open_finding`` (what the next request must REMEMBER, where an unknown type stays remembered): MEASURED, an
# orchestrator ``no_progress_retry`` replaced the open finding and, as a "different failure", replenished the scoped budgets.
FAMILY_OPENING_FAILURE_TYPES = frozenset({
    "api_contract_evidence_restore_required", "api_contract_recovery_incomplete", "artifact_registry", "brownfield_ownership_redirect",
    "brownfield_public_api_changed", "candidate_runtime_entrypoint_invalid", "compile", "contract_registry", "cross_package_symbol_mismatch",
    "deterministic_contract_violation", "duplicate_type_across_files", "file_integrity", "file_integrity_unsupported",
    "goal_insufficient_for_verification", "goal_spec_compliance", "incomplete_generation", "managed_service_runtime_plan_gap", "pom_semantic_validation",
    "process_terminating_behavior_tested_in_process", "prose_contamination", "regression_test", "regression_unattributed", "requirements_unresolved",
    "run_verification", "run_verification_hung", "semantic_region_unauthorized", "static_analysis_blocked", "static_analysis_unavailable",
    "static_analysis_unknown", "static_rule_violation", "targeted_test", "test", "test_acceptance", "test_obligation_not_preserved",
    "unrequested_architectural_surface", "unsafe_exception_masking", "unverified_no_change", "verification_authority_required",
    "verification_authority_unavailable", "verification_tree_mutated", "verified_no_change_refused", "verifier_semantic_authority_required",
    "verifier_semantic_expectation_invalid",
})
# The produced types that never open a family, named so that the enumeration is total: the orchestrator's own statements (no new evidence about the candidate), the
# delivery failures, and the operational / resource / internal / environment types the deny-lists below already exclude.
NON_OPENING_FAILURE_TYPES = frozenset({
    "no_progress_retry", "verifier_replay_refused",
    "context_budget_unsatisfiable", "context_edit_protocol_unsatisfiable", "fallback_incompatible", "general_error", "internal_framework_error",
    "output_budget_unsatisfiable", "plan_scope_conflict", "provider_contract", "retry_identity_not_qualified", "test_verification_infrastructure_failure",
    "time_budget_exhausted", "verification_infrastructure_failure", "work_ceiling_exhausted", "workspace_commit", "containment_setup_failed",
}) | DELIVERY_FAILURE_TYPES


def may_open_failure_family(failure: Any) -> bool:
    """Whether ``failure`` may open a NEW failure family (retry_strategy._record_attempt_failure's one question). Typed: the failure's type must be on the allow-list
    AND it must be an authoritative statement about the candidate (``replaces_open_finding``). Within an eligible type the family identity stays the normalized
    validator signature (C-07); this decides only whether the type is eligible at all."""
    return failure.type in FAMILY_OPENING_FAILURE_TYPES and replaces_open_finding(failure)


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


_LINES_OMITTED = "... [lines omitted] ..."


def _render_selected(lines: Sequence[str], selected: Sequence[int]) -> str:
    """The chosen lines in file order, with one marker line wherever lines were left out."""
    out: List[str] = []
    previous = -1
    for index in selected:
        if index != previous + 1:
            out.append(_LINES_OMITTED)
        out.append(lines[index])
        previous = index
    if previous != len(lines) - 1:
        out.append(_LINES_OMITTED)
    return "\n".join(out)


def anchored_excerpt(text: str, locations: Sequence[Tuple[str, int]], limit: int) -> Optional[str]:
    """The lines of ``text`` that report the validator's own locations, each with as much surrounding output as ``limit`` allows.

    OPEN-FINDING-EVIDENCE-001 (Scenario-A run 10, MEASURED): a Maven test failure of 11252 characters carried its assertion at
    offsets 1582 and 9994; a blind head-and-tail cut of 2000 kept the build preamble and the help footer and dropped both. The
    anchor is not a guess about the tool: it is where the output names a location the validator already put on the Failure.
    Returns None when the output names none of them (the caller keeps the head-and-tail cut).
    """
    wanted = {(os.path.basename(path), line) for path, line in locations}
    if not wanted:
        return None
    lines = text.split("\n")
    starts: List[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    anchors: List[int] = []
    seen = set()
    for basename, line_no, match_offset in error_source_location_matches(text):
        key = (basename, line_no)
        if key in wanted and key not in seen:
            seen.add(key)
            index = bisect_right(starts, match_offset) - 1
            if index not in anchors:
                anchors.append(index)
    selected: List[int] = []

    def add(index: int) -> bool:
        if index < 0 or index >= len(lines) or index in selected:
            return False
        candidate = sorted([*selected, index])
        if len(_render_selected(lines, candidate)) > limit:
            return False
        selected[:] = candidate
        return True

    kept = [anchor for anchor in anchors if add(anchor)]   # the anchors first, in the validator's order
    if not kept:
        return None
    # Then grow every kept anchor outwards one line at a time, in turn, until nothing more fits.
    open_ends = {(anchor, step): True for anchor in kept for step in (-1, 1)}
    distance = 0
    while any(open_ends.values()):
        distance += 1
        for anchor in kept:
            for step in (-1, 1):
                if not open_ends[(anchor, step)]:
                    continue
                index = anchor + step * distance
                if index in selected:
                    continue
                if not add(index):
                    open_ends[(anchor, step)] = False
    return _render_selected(lines, selected)


def bounded_evidence(text: str, locations: Sequence[Tuple[str, int]], limit: int) -> str:
    """Validator evidence within ``limit``: anchored on the validator's locations when the output reports them, else both ends."""
    excerpt = anchored_excerpt(text, locations, limit)
    return excerpt if excerpt is not None else _head_and_tail(text, limit)


def finding_from_failure(failure: Any, attempt: int) -> OpenFinding:
    evidence = str(failure.raw_output or failure.message or "")
    paths = [location.filepath for location in failure.file_locations]
    files = tuple(dict.fromkeys([*paths, *failure.likely_files]))
    locations = tuple(dict.fromkeys(
        (location.filepath, location.line) for location in failure.file_locations if location.line))
    return OpenFinding(
        failure_type=failure.type,
        reason_code=(failure.diagnostics or {}).get("reason_code"),
        files=files,
        source=failure.source,
        evidence=bounded_evidence(evidence, locations, MAX_EVIDENCE_CHARS),
        first_attempt=attempt,
        locations=locations,
    )


def replaces_open_finding(failure: Any) -> bool:
    """Whether ``failure`` is an authoritative statement about the CANDIDATE, i.e. the only kind that opens or replaces the open
    finding. A delivery failure, an operational / internal / unclassified failure and an advisory one are not.

    The ONE owner of that question (NON-CANDIDATE-FAILURE-SCOPED-RESET-001): ``update_open_findings`` and the retry loop's
    failure-family inheritance (retry_strategy._record_attempt_failure) both read it, so a failure that leaves the open finding
    in place can never be counted as a new failure family. Scenario-A run 10 (MEASURED): a ``context_budget_unsatisfiable``
    refusal left the assertion finding open and still replenished the scoped targeted / fallback-targeted budgets."""
    if failure.type in DELIVERY_FAILURE_TYPES or not is_candidate_finding(failure.type):
        return False
    return getattr(failure, "authority", "authoritative") == "authoritative"


def update_open_findings(open_findings: Sequence[OpenFinding], failure: Any, attempt: int) -> List[OpenFinding]:
    """The open set after ``failure`` was recorded. Only an authoritative candidate finding changes it."""
    if not replaces_open_finding(failure):
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


def open_finding_locations(open_findings: Sequence[OpenFinding]) -> List[Tuple[str, int]]:
    """The validator-owned (file, line) locators of the open findings, in order, without repeats."""
    return list(dict.fromkeys(location for finding in open_findings for location in finding.locations))


def open_finding_files(open_findings: Sequence[OpenFinding]) -> List[str]:
    """The validator-owned subjects of the open findings, in order, without repeats."""
    return list(dict.fromkeys(path for finding in open_findings for path in finding.files))
