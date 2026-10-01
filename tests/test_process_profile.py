import pytest
from pydantic import ValidationError

from kriya.config.config import ProcessProfilesConfig
from kriya.workflow.process_profile import (
    HEAVY_PROFILE,
    LIGHT_PROFILE,
    STANDARD_PROFILE,
    ContextDepth,
    VerificationTier,
    process_profile_for,
)
from kriya.workflow.triage import ExecutionWeight


def test_process_profile_for_resolves_exact_spec_values():
    assert process_profile_for(ExecutionWeight.LIGHT) is LIGHT_PROFILE
    assert LIGHT_PROFILE.context_depth == ContextDepth.NARROW
    assert LIGHT_PROFILE.human_review_required is False
    assert LIGHT_PROFILE.auto_merge_allowed is True
    assert LIGHT_PROFILE.full_test_suite_required is False

    assert process_profile_for(ExecutionWeight.STANDARD) is STANDARD_PROFILE
    assert STANDARD_PROFILE.context_depth == ContextDepth.DEPENDENCY_AWARE
    assert STANDARD_PROFILE.human_review_required is True
    assert STANDARD_PROFILE.auto_merge_allowed is False

    assert process_profile_for(ExecutionWeight.HEAVY) is HEAVY_PROFILE
    assert HEAVY_PROFILE.context_depth == ContextDepth.IMPACT_WIDE
    assert HEAVY_PROFILE.verification_tier == VerificationTier.HEAVY


def test_structured_subtasks_and_contract_analysis_never_claimed_true():
    """MA2.1's own guardrail: no capability that doesn't exist yet
    (subtask executor - MA6; ContractRegistry - MA5) may be claimed by any
    profile, in any weight."""
    for profile in (LIGHT_PROFILE, STANDARD_PROFILE, HEAVY_PROFILE):
        assert profile.structured_subtasks_required is False
        assert profile.contract_analysis_required is False


def test_to_dict_marks_heavy_extended_checks_unavailable():
    """MA2.6b: HEAVY must self-report that its extended verification tooling
    doesn't exist yet, guaranteed by ProcessProfile.to_dict() itself rather
    than left to a call site to remember."""
    assert LIGHT_PROFILE.to_dict()["heavy_extended_checks_not_yet_available"] is False
    assert STANDARD_PROFILE.to_dict()["heavy_extended_checks_not_yet_available"] is False
    assert HEAVY_PROFILE.to_dict()["heavy_extended_checks_not_yet_available"] is True


def test_to_dict_is_json_serializable():
    import json
    for profile in (LIGHT_PROFILE, STANDARD_PROFILE, HEAVY_PROFILE):
        json.dumps(profile.to_dict())  # must not raise


def test_enforce_verification_depth_rejected_at_config_construction():
    """MA2.6b explicit decision: 'a triage misclassification cannot reduce
    regression-test coverage in MA2' - enforced by rejecting the flag
    outright rather than silently accepting and ignoring it."""
    with pytest.raises(ValidationError, match="not implemented yet"):
        ProcessProfilesConfig(enforce_verification_depth=True)


def test_other_process_profiles_flags_remain_settable():
    """Only enforce_verification_depth is rejected - the other MA2.5/MA2.6a
    flags this design already trusts to change real behavior are untouched."""
    cfg = ProcessProfilesConfig(enabled=True, enforce_approval=True, enforce_context_depth=True)
    assert cfg.enabled is True
    assert cfg.enforce_approval is True
    assert cfg.enforce_context_depth is True
    assert cfg.enforce_verification_depth is False
