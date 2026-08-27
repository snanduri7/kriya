"""Failure-vocabulary reporting categories - MA7.6 of the MA7 hardening
plan. The original MA7 proposal's own explicit instruction (item 10):
reuse Kriya's EXISTING failure vocabulary - Failure.type
(kriya/workflow/failure.py) and AttributionTier (kriya/workflow/
attribution.py) - rather than invent a second, parallel taxonomy. This
module is a pure, additive REPORTING view over both: it groups every real
Failure.type string into one of a handful of human-facing categories, and
pairs that with AttributionTier as-is (that vocabulary already answers
"how confidently was this failure localized" and needs no translation of
its own).

Not to be confused with kriya/workflow/failure.py's FailureAttributionKind
(SOURCE_DEFECT/PLAN_SCOPE_DEFECT/VERIFICATION_CONTRACT_DEFECT/TEST_DEFECT/
INFRASTRUCTURE_DEFECT) - that answers a different question (WHO owns the
repair, consumed authoritatively by retry_strategy.py) than this module's
FailureCategory (a REPORTING-only "what kind of thing went wrong" bucket).
Neither is a substitute for the other; see FailureAttributionKind's own
docstring for the full distinction.

Never consulted by retry logic, policy, or anything authoritative -
Failure.type/AttributionTier themselves remain the sole source of truth
for retry scoping (kriya/workflow/retry_strategy.py's own
_REPAIR_FEEDBACK_FAILURE_TYPES set, for example, is untouched by this
module and must stay that way). This exists purely so a human/dashboard
looking at gate_outcomes or traces.db can ask "what KIND of thing keeps
failing" without re-deriving the grouping from scratch each time.

The real Failure.type vocabulary (verified 2026-08-24 by grepping every
real Failure(type=...)/_build_quality_gate_failure(type_=...) construction
site, not just failure.py's own docstring - that docstring predates
several of these and is missing operation_contract, no_op_edit,
duplicate_type_across_files, time_budget_exhausted, test_acceptance, and
targeted_test; worth reconciling separately, not done here) has 23 real
values, mapped below into 6 categories."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class FailureCategory(str, Enum):
    """One coarse "what kind of thing went wrong" bucket. A REPORTING
    grouping only - retry scoping continues to key off the specific
    Failure.type string, never this coarser category."""

    BUILD = "build"
    """Never compiled/parsed - the code itself is malformed at the
    language/build-tool level."""

    VERIFICATION = "verification"
    """Compiled fine, but didn't behave or prove correctly - a test,
    run-time check, regression suite, or explicit goal requirement wasn't
    satisfied."""

    GENERATION_COMPLETENESS = "generation_completeness"
    """The model's response itself was malformed, incomplete, or matched a
    known-bad pattern - a problem with WHAT was generated, independent of
    where it was aimed or whether it compiles."""

    EDIT_TARGETING = "edit_targeting"
    """The response's edit/content was aimed at, or applied to, the wrong
    place - or claimed to change something and didn't."""

    RESOURCE = "resource"
    """An operational/resource constraint, not a code-correctness issue -
    today, only the generation time budget running out before a pass could
    even start."""

    UNCLASSIFIED = "unclassified"
    """A bare, non-QualityGateFailure exception with no specific
    Failure.type of its own, or any future Failure.type this mapping
    doesn't know about yet - reports here rather than raising, so a new
    failure.py addition degrades gracefully instead of breaking whatever
    calls categorize_failure()."""


# Every real Failure.type value found in the codebase (2026-08-24), see
# this module's own docstring for how it was verified. Deliberately a
# flat dict, not derived from failure.py's own docstring text - that
# docstring is documentation, not a machine-readable source of truth, and
# was already found to be missing 6 real values.
_FAILURE_TYPE_TO_CATEGORY: Dict[str, FailureCategory] = {
    # BUILD - never compiled/parsed
    "compile": FailureCategory.BUILD,
    "pom_semantic_validation": FailureCategory.BUILD,
    "cross_package_symbol_mismatch": FailureCategory.BUILD,

    # VERIFICATION - compiled, but didn't prove correct
    "test": FailureCategory.VERIFICATION,
    "targeted_test": FailureCategory.VERIFICATION,
    "run_verification": FailureCategory.VERIFICATION,
    "run_verification_hung": FailureCategory.VERIFICATION,
    "regression_test": FailureCategory.VERIFICATION,
    "goal_spec_compliance": FailureCategory.VERIFICATION,
    "test_acceptance": FailureCategory.VERIFICATION,

    # GENERATION_COMPLETENESS - the response itself was wrong-shaped
    "incomplete_generation": FailureCategory.GENERATION_COMPLETENESS,
    "static_rule_violation": FailureCategory.GENERATION_COMPLETENESS,
    "structural_corruption": FailureCategory.GENERATION_COMPLETENESS,
    "duplicate_type_across_files": FailureCategory.GENERATION_COMPLETENESS,
    "operation_contract": FailureCategory.GENERATION_COMPLETENESS,

    # EDIT_TARGETING - aimed at, or applied to, the wrong place
    "anchored_edit": FailureCategory.EDIT_TARGETING,
    "attribution_rejected": FailureCategory.EDIT_TARGETING,
    "unaddressed_error_location": FailureCategory.EDIT_TARGETING,
    "diagnosis_mismatch": FailureCategory.EDIT_TARGETING,
    "misdirected_edit": FailureCategory.EDIT_TARGETING,
    "no_op_edit": FailureCategory.EDIT_TARGETING,

    # RESOURCE - operational constraint, not a code defect
    "time_budget_exhausted": FailureCategory.RESOURCE,

    # UNCLASSIFIED - explicit fallback
    "general_error": FailureCategory.UNCLASSIFIED,
}


def categorize_failure(failure_type: str) -> FailureCategory:
    """Never raises on an unrecognized failure_type - reports UNCLASSIFIED
    instead, so a future addition to failure.py's own vocabulary that
    hasn't been added here yet degrades gracefully rather than crashing
    whatever reporting/telemetry consumer calls this."""
    return _FAILURE_TYPE_TO_CATEGORY.get(failure_type, FailureCategory.UNCLASSIFIED)


@dataclass(frozen=True)
class FailureReportEntry:
    """One reporting-ready row: the real Failure.type verbatim (never
    discarded - the category is a grouping ON TOP of it, not a
    replacement), the coarse category, and - when available -
    AttributionTier's own localization-confidence label. `attribution_tier`
    is deliberately typed as a plain Optional[str], not re-imported as
    AttributionResult.tier's own type, to avoid this reporting-only module
    depending on kriya.workflow.attribution's real, larger surface for a
    single field."""

    failure_type: str
    category: FailureCategory
    attribution_tier: Optional[str] = None


def build_failure_report_entry(failure_type: str, attribution_tier: Optional[str] = None) -> FailureReportEntry:
    return FailureReportEntry(
        failure_type=failure_type,
        category=categorize_failure(failure_type),
        attribution_tier=attribution_tier,
    )


def dominant_category(entries: "list[dict]") -> Optional[str]:
    """The single most-common category across a run's failure_report
    entries (kriya/core/trace.py's persisted JSON shape - plain dicts with
    a `category` key, not FailureReportEntry objects, since this is meant
    to run on data read back OUT of traces.db, not on live objects). None
    for an empty list (a run that succeeded on its first attempt, or
    predates this field). Ties break on first-seen order - acceptable for
    a compact display hint, not a statistical claim."""
    if not entries:
        return None
    counts: Dict[str, int] = {}
    for entry in entries:
        cat = entry.get("category")
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])
