"""CAGC-0 rendering, caps and refit (KRIYA_CAGC v0.7 §4, §8, §14 test_cagc_render)."""
import hashlib

import pytest

from kriya.capabilities.guidance import (
    CapabilityGuidance,
    Domain,
    GuidanceRule,
    Operation,
    RepoContext,
    Role,
    SelectionFacts,
    compose_guidance,
    refit_guidance,
)
from kriya.capabilities.guidance.render import FOOTER, HEADER
from kriya.workflow.context_budget import estimate_tokens


def _rule(rule_id, text, priority, role=Role.DEVELOPER):
    return GuidanceRule(rule_id, text, frozenset({role}), frozenset(), frozenset(), priority, "test")


REGISTRY = (
    CapabilityGuidance("java", 2, Domain.LANGUAGE, (
        _rule("java.dev.b", "Second java rule.", 20),
        _rule("java.dev.a", "First java rule.", 10),
    )),
    CapabilityGuidance("maven", 1, Domain.BUILD, (_rule("maven.dev.a", "Maven rule.", 5),)),
    CapabilityGuidance("spring", 3, Domain.FRAMEWORK, (_rule("spring.dev.a", "Spring rule.", 40),)),
    CapabilityGuidance("python", 1, Domain.LANGUAGE, ()),
    CapabilityGuidance("pip", 1, Domain.BUILD, ()),
    CapabilityGuidance("gradle", 1, Domain.BUILD, ()),
    CapabilityGuidance("spring_xml", 1, Domain.FRAMEWORK, ()),
    CapabilityGuidance("spring_config", 1, Domain.FRAMEWORK, ()),
)


def _facts(**overrides):
    values = dict(role=Role.DEVELOPER, operation=Operation.EDIT, context=RepoContext.EXISTING,
                  target_paths=("pom.xml", "A.java"), target_languages=frozenset({"java"}),
                  target_symbol_kinds=frozenset(), target_annotations=frozenset({"Service"}),
                  target_imports_spring=False, build_systems=frozenset(), spring_repository=False)
    values.update(overrides)
    return SelectionFacts(**values)


EXPECTED = ("\n\n" + HEADER + "\n[spring]\n- Spring rule.\n[maven]\n- Maven rule.\n[java]\n- First java rule.\n"
            "- Second java rule.\n" + FOOTER + "\n")


def test_render_is_byte_stable_and_ordered():
    block = compose_guidance(_facts(), registry=REGISTRY)
    assert block.text == EXPECTED
    assert compose_guidance(_facts(), registry=REGISTRY) == block
    assert block.rule_ids == ("spring.dev.a", "maven.dev.a", "java.dev.a", "java.dev.b")
    assert block.digest == hashlib.sha256(EXPECTED.encode()).hexdigest()
    assert block.estimated_tokens == estimate_tokens(EXPECTED)


def test_selected_versus_rendered_ids():
    block = compose_guidance(_facts(target_paths=("A.java",), target_annotations=frozenset()), registry=REGISTRY)
    assert block.selected_capability_ids == ("java@2",)
    empty_registry = tuple(CapabilityGuidance(c.capability_id, c.version, c.domain, ()) for c in REGISTRY)
    empty = compose_guidance(_facts(), registry=empty_registry)
    assert empty.text == "" and empty.estimated_tokens == 0
    assert empty.selected_capability_ids == ("spring@3", "maven@1", "java@2")
    assert empty.rendered_capability_ids == ()
    assert empty.digest == hashlib.sha256(b"").hexdigest()


def test_no_rule_no_header():
    block = compose_guidance(_facts(target_paths=(), target_languages=frozenset(), target_annotations=frozenset()),
                             registry=REGISTRY)
    assert block.text == "" and HEADER not in block.text


def _partition(block):
    return set(block.rule_ids), set(block.dropped_by_cap_rule_ids), set(block.dropped_by_fit_rule_ids)


APPLICABLE = {"spring.dev.a", "maven.dev.a", "java.dev.a", "java.dev.b"}


@pytest.mark.parametrize("cap", [0, 30, 45, 60, 10_000])
def test_cap_and_fit_drops_are_disjoint_and_complete(cap):
    block = compose_guidance(_facts(), cap_tokens=cap, registry=REGISTRY)
    for budget in (0, 20, 40, 10_000):
        refit = refit_guidance(block, budget)
        visible, cap_dropped, fit_dropped = _partition(refit)
        assert not (visible & cap_dropped) and not (visible & fit_dropped) and not (cap_dropped & fit_dropped)
        assert visible | cap_dropped | fit_dropped == APPLICABLE
        assert refit.base_rule_entries == block.base_rule_entries
        assert set(refit.visible_rule_entries) <= set(refit.base_rule_entries)
        order = [refit.base_rule_entries.index(entry) for entry in refit.visible_rule_entries]
        assert order == sorted(order)


def test_per_capability_cap_drops_whole_rules():
    long_text = "x" * 190 + "."
    registry = (CapabilityGuidance("java", 1, Domain.LANGUAGE, (
        _rule("java.dev.a", long_text, 1), _rule("java.dev.b", long_text, 2))),) + REGISTRY[1:]
    block = compose_guidance(_facts(), registry=registry)
    assert "java.dev.a" in block.rule_ids and block.dropped_by_cap_rule_ids == ("java.dev.b",)


def test_total_cap_keeps_by_priority():
    block = compose_guidance(_facts(), cap_tokens=estimate_tokens(
        "\n\n" + HEADER + "\n[maven]\n- Maven rule.\n" + FOOTER + "\n"), registry=REGISTRY)
    assert block.rule_ids == ("maven.dev.a",)
    assert set(block.dropped_by_cap_rule_ids) == APPLICABLE - {"maven.dev.a"}


def test_repeated_refits_restore_fit_drops_but_never_cap_drops():
    base = compose_guidance(_facts(), cap_tokens=52, registry=REGISTRY)
    assert base.dropped_by_cap_rule_ids, "this cap must drop something"
    high = refit_guidance(base, 10_000)
    low = refit_guidance(base, 40)
    again = refit_guidance(base, 10_000)
    assert high == again and high.rule_ids == base.rule_ids
    assert len(low.rule_ids) < len(high.rule_ids)
    assert not set(base.dropped_by_cap_rule_ids) & set(again.rule_ids)
    # A refit of a refit is never cumulative: it reads only the baseline.
    assert refit_guidance(low, 10_000) == high


def test_refit_reads_only_the_baseline_never_text_or_registry():
    base = compose_guidance(_facts(), registry=REGISTRY)
    tampered = base.__class__(**{**base.__dict__, "text": "garbage", "visible_rule_entries": ()})
    assert refit_guidance(tampered, 10_000).text == base.text


def test_refit_never_truncates_a_rule_and_keeps_priority_order():
    base = compose_guidance(_facts(), registry=REGISTRY)
    for budget in range(0, base.estimated_tokens + 2):
        block = refit_guidance(base, budget)
        assert block.estimated_tokens <= budget or block.text == ""
        for entry in block.visible_rule_entries:
            assert f"- {entry.text}\n" in block.text
        kept = sorted(block.visible_rule_entries, key=lambda r: (r.priority, r.rule_id))
        dropped = [r for r in base.base_rule_entries if r not in block.visible_rule_entries]
        if kept and dropped:
            assert max((r.priority, r.rule_id) for r in kept) < min((r.priority, r.rule_id) for r in dropped)


def test_empty_fit_records_every_base_rule_as_fit_dropped():
    base = compose_guidance(_facts(), registry=REGISTRY)
    empty = refit_guidance(base, 0)
    assert empty.text == "" and empty.estimated_tokens == 0 and empty.rule_ids == ()
    assert set(empty.dropped_by_fit_rule_ids) == APPLICABLE
    assert empty.selected_capability_ids == base.selected_capability_ids
    assert empty.digest == hashlib.sha256(b"").hexdigest()


def test_refit_stops_at_the_first_rule_that_does_not_fit():
    """A later (lower-priority) rule never displaces or skips past an earlier
    one: the fit is monotone in the budget."""
    registry = (CapabilityGuidance("java", 1, Domain.LANGUAGE, (
        _rule("java.dev.big", "y" * 150 + ".", 1), _rule("java.dev.small", "Small.", 2))),) + REGISTRY[1:]
    base = compose_guidance(_facts(target_annotations=frozenset(), target_paths=("A.java",)), registry=registry)
    assert base.rule_ids == ("java.dev.big", "java.dev.small")
    block = refit_guidance(base, base.estimated_tokens - 1)
    assert block.rule_ids == ("java.dev.big",)
    block = refit_guidance(base, 50)
    assert block.rule_ids == () and set(block.dropped_by_fit_rule_ids) == {"java.dev.big", "java.dev.small"}
