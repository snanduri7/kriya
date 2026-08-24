"""MA4.10: the trust-model vocabulary itself (kriya/policy/trust.py). Pure
data, no wiring into any real call site yet - see that module's own
docstring. These tests exist to lock the priority ORDER (a literal port of
the design doc's Priority 0-5 list) and the outranks() direction, since both
are easy to accidentally invert later without anything else catching it."""

import dataclasses

import pytest

from kriya.policy.trust import TrustedContent, TrustLevel, outranks


def test_trust_levels_match_design_docs_priority_list_in_order():
    assert list(TrustLevel) == [
        TrustLevel.PLATFORM,
        TrustLevel.USER_INSTRUCTION,
        TrustLevel.APPROVED_PROJECT_POLICY,
        TrustLevel.MILESTONE,
        TrustLevel.REPOSITORY,
        TrustLevel.EXTERNAL,
    ]
    assert TrustLevel.PLATFORM.value == 0
    assert TrustLevel.USER_INSTRUCTION.value == 1
    assert TrustLevel.APPROVED_PROJECT_POLICY.value == 2
    assert TrustLevel.MILESTONE.value == 3
    assert TrustLevel.REPOSITORY.value == 4
    assert TrustLevel.EXTERNAL.value == 5


def test_platform_outranks_every_other_level():
    for level in TrustLevel:
        if level is TrustLevel.PLATFORM:
            continue
        assert outranks(TrustLevel.PLATFORM, level) is True
        assert outranks(level, TrustLevel.PLATFORM) is False


def test_external_outranks_nothing():
    for level in TrustLevel:
        if level is TrustLevel.EXTERNAL:
            continue
        assert outranks(TrustLevel.EXTERNAL, level) is False


def test_a_level_never_outranks_itself():
    for level in TrustLevel:
        assert outranks(level, level) is False


def test_outranks_reflects_the_full_declared_order_pairwise():
    levels = list(TrustLevel)
    for i, higher in enumerate(levels):
        for lower in levels[i + 1:]:
            assert outranks(higher, lower) is True
            assert outranks(lower, higher) is False


def test_repository_outranks_external_but_not_milestone():
    """Concrete instance of the doc's own example: repo content still beats
    purely external content, but loses to a milestone spec / frozen
    contract."""
    assert outranks(TrustLevel.REPOSITORY, TrustLevel.EXTERNAL) is True
    assert outranks(TrustLevel.REPOSITORY, TrustLevel.MILESTONE) is False


def test_trusted_content_requires_trust_level_and_source_explicitly():
    content = TrustedContent(
        content="some retrieved text",
        trust_level=TrustLevel.EXTERNAL,
        source="kriya learn: https://example.com/doc",
    )
    assert content.trust_level is TrustLevel.EXTERNAL
    assert content.source == "kriya learn: https://example.com/doc"
    assert content.metadata == {}

    with pytest.raises(TypeError):
        TrustedContent(content="x")  # trust_level/source are not optional


def test_trusted_content_is_frozen():
    content = TrustedContent(content="x", trust_level=TrustLevel.REPOSITORY, source="repository file: a.py")
    with pytest.raises(dataclasses.FrozenInstanceError):
        content.trust_level = TrustLevel.PLATFORM


def test_trust_level_is_an_int_enum_for_direct_ordering_comparisons():
    assert isinstance(TrustLevel.PLATFORM, int)
    assert TrustLevel.PLATFORM < TrustLevel.EXTERNAL
    assert min(TrustLevel) is TrustLevel.PLATFORM
    assert max(TrustLevel) is TrustLevel.EXTERNAL
