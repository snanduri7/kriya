"""Readiness rubric: scores a skill's accumulated KnowledgeFacts per category on a
0-4 scale, adapted from EIE's engineering-pack readiness taxonomy. Pure function,
no I/O - callers (staging.py, the CLI) are responsible for gathering the fact list
and populating each fact's `verified` flag from rule_provenance.json before calling
this, since that's the only field here that reflects real-world outcome rather than
extraction quality.

Level meanings (uniform across categories, deliberately simple for slice 1 - a
category-specific rubric is a natural refinement once this backbone is validated):
  0 - no facts at all for this category.
  1 - only unlinked, LLM-guessed facts (extraction_confidence == "llm_inferred_no_quote").
  2 - at least one fact grounded in a real quote or supplied directly by a human.
  3 - at least one mechanically-extracted fact (deterministic, no LLM involved).
  4 - at least one fact that has since been verified correct by a real run.
"""
from kriya.knowledge.schema import KNOWLEDGE_CATEGORIES, SkillReadiness


def _category_level(facts_for_category: list) -> int:
    if not facts_for_category:
        return 0
    if any(f.verified for f in facts_for_category):
        return 4
    if any(f.extraction_confidence == "mechanical" for f in facts_for_category):
        return 3
    if any(f.extraction_confidence in ("llm_from_quote", "human_supplied") for f in facts_for_category):
        return 2
    return 1


def score_skill(skill_name: str, facts: list) -> SkillReadiness:
    """facts: list[KnowledgeFact] for this skill, from any combination of channels
    (staged and/or already-approved) - the caller decides which set to score."""
    by_category: dict = {cat: [] for cat in KNOWLEDGE_CATEGORIES}
    for fact in facts:
        if fact.category in by_category:
            by_category[fact.category].append(fact)

    category_levels = {cat: _category_level(fs) for cat, fs in by_category.items()}
    missing = [cat for cat, level in category_levels.items() if level == 0]
    overall = min(category_levels.values()) if category_levels else 0

    return SkillReadiness(
        skill_name=skill_name,
        category_levels=category_levels,
        missing_categories=missing,
        overall_level=overall,
    )
