"""MA7.6: failure-vocabulary reporting categories (kriya/workflow/failure_reporting.py) -
maps the EXISTING Failure.type/AttributionTier vocabulary onto reporting
categories, per the original MA7 proposal's explicit "reuse the existing
vocabulary, don't invent a new taxonomy" instruction."""

import pytest

from kriya.workflow.failure_reporting import (
    FailureCategory,
    build_failure_report_entry,
    categorize_failure,
)
from kriya.workflow.retry_strategy import _REPAIR_FEEDBACK_FAILURE_TYPES


# The full real Failure.type vocabulary, verified 2026-08-24 by grepping
# every real construction site (Failure(type=...)/_build_quality_gate_failure) -
# see failure_reporting.py's own module docstring. Pinned here so a
# real-world addition/removal of a Failure.type forces a conscious review
# of this mapping instead of silently falling through to UNCLASSIFIED.
_REAL_FAILURE_TYPES = {
    "compile": FailureCategory.BUILD,
    "pom_semantic_validation": FailureCategory.BUILD,
    "cross_package_symbol_mismatch": FailureCategory.BUILD,
    "test": FailureCategory.VERIFICATION,
    "targeted_test": FailureCategory.VERIFICATION,
    "run_verification": FailureCategory.VERIFICATION,
    "run_verification_hung": FailureCategory.VERIFICATION,
    "regression_test": FailureCategory.VERIFICATION,
    "goal_spec_compliance": FailureCategory.VERIFICATION,
    "test_acceptance": FailureCategory.VERIFICATION,
    "incomplete_generation": FailureCategory.GENERATION_COMPLETENESS,
    "static_rule_violation": FailureCategory.GENERATION_COMPLETENESS,
    "structural_corruption": FailureCategory.GENERATION_COMPLETENESS,
    "duplicate_type_across_files": FailureCategory.GENERATION_COMPLETENESS,
    "operation_contract": FailureCategory.GENERATION_COMPLETENESS,
    "anchored_edit": FailureCategory.EDIT_TARGETING,
    "attribution_rejected": FailureCategory.EDIT_TARGETING,
    "unaddressed_error_location": FailureCategory.EDIT_TARGETING,
    "diagnosis_mismatch": FailureCategory.EDIT_TARGETING,
    "misdirected_edit": FailureCategory.EDIT_TARGETING,
    "no_op_edit": FailureCategory.EDIT_TARGETING,
    "time_budget_exhausted": FailureCategory.RESOURCE,
    "general_error": FailureCategory.UNCLASSIFIED,
}


@pytest.mark.parametrize("failure_type,expected_category", sorted(_REAL_FAILURE_TYPES.items()))
def test_every_real_failure_type_maps_to_the_expected_category(failure_type, expected_category):
    assert categorize_failure(failure_type) == expected_category


def test_unrecognized_failure_type_degrades_to_unclassified_not_an_error():
    assert categorize_failure("some_future_failure_type_not_added_yet") == FailureCategory.UNCLASSIFIED


def test_mapping_covers_exactly_the_pinned_real_vocabulary_no_more_no_less():
    from kriya.workflow.failure_reporting import _FAILURE_TYPE_TO_CATEGORY
    assert set(_FAILURE_TYPE_TO_CATEGORY.keys()) == set(_REAL_FAILURE_TYPES.keys())


def test_every_real_repair_feedback_type_is_categorized_not_unclassified():
    """Ties this reporting module to retry_strategy.py's own real,
    authoritative _REPAIR_FEEDBACK_FAILURE_TYPES set. NOT asserting they're
    all EDIT_TARGETING - that set is broader than this module's own
    category boundary (e.g. structural_corruption legitimately reports
    GENERATION_COMPLETENESS here - a broken file's own content, not a
    targeting mistake - while still being repair-feedback-worthy for retry
    purposes; the two vocabularies answer different questions). The real
    invariant: every type retry logic already treats as repair-worthy must
    be a REAL, pinned entry in this module's mapping, never silently
    falling through to UNCLASSIFIED."""
    from kriya.workflow.failure_reporting import _FAILURE_TYPE_TO_CATEGORY
    for failure_type in _REPAIR_FEEDBACK_FAILURE_TYPES:
        assert failure_type in _FAILURE_TYPE_TO_CATEGORY, (
            f"{failure_type!r} is in retry_strategy.py's real _REPAIR_FEEDBACK_FAILURE_TYPES "
            "but has no entry in this module's mapping at all"
        )


def test_build_failure_report_entry_carries_the_real_type_verbatim():
    entry = build_failure_report_entry("compile", attribution_tier="locator")
    assert entry.failure_type == "compile"
    assert entry.category == FailureCategory.BUILD
    assert entry.attribution_tier == "locator"


def test_build_failure_report_entry_attribution_tier_defaults_to_none():
    entry = build_failure_report_entry("general_error")
    assert entry.attribution_tier is None
    assert entry.category == FailureCategory.UNCLASSIFIED
