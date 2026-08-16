"""Structured knowledge-fact schema shared by every extraction channel
(kriya/knowledge/channels/*) and consumed by the readiness rubric
(kriya/knowledge/rubric.py). Pure data model, no I/O - reading/writing
these to disk lives in kriya/knowledge/staging.py.
"""
from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# The 10-category taxonomy adapted from EIE's engineering-pack readiness rubric.
# Not EIE's exact category text (not available in this repo) - a generalizable
# shape for "what does a usable skill need to know" that this rubric scores against.
KNOWLEDGE_CATEGORIES = [
    "Metadata",
    "Compatibility",
    "Dependencies",
    "APIs",
    "Configuration",
    "Rules",
    "Examples",
    "Verification",
    "Constraints",
    "BestPractices",
]

# How much to trust a fact's own extraction, independent of whether it has since
# been proven correct by a real run (see KnowledgeFact.verified for that axis).
# Ordered lowest to highest trust; rubric.py relies on this ordering.
CONFIDENCE_TIERS = [
    "llm_inferred_no_quote",  # LLM guessed from prose, no exact source quote
    "llm_from_quote",         # LLM cited a specific line/quote it derived this from
    "human_supplied",         # a human directly answered a scaffold question
    "mechanical",             # deterministic, non-LLM extraction (e.g. manifest parse)
]


class KnowledgeFact(BaseModel):
    """One atomic fact about a skill's subject, tagged with where it came from and
    how much to trust it. Multiple facts accumulate per skill across channels and
    runs; the rubric scores the accumulated set, not any single fact in isolation."""

    category: str = Field(description="One of KNOWLEDGE_CATEGORIES.")
    key: str = Field(description="Short identifier for the fact, e.g. a dependency name or API symbol.")
    value: str = Field(description="The fact itself, in as exact a form as the source allows.")
    source_channel: str = Field(description="Which channel produced this, e.g. 'repo_manifest', 'live_failure', 'human_scaffold'.")
    extraction_confidence: str = Field(description="One of CONFIDENCE_TIERS.")
    provenance: str = Field(description="Exact source quote, manifest path, or other pointer back to where this came from.")
    added_at: str = Field(default_factory=lambda: date.today().isoformat())
    verified: bool = Field(
        default=False,
        description="Whether a real run has since proven this fact correct in practice (mirrors "
                     "kriya/skills/skill.py's rule_provenance verified flag - callers populate this "
                     "from that file rather than rubric.py doing its own I/O to check).",
    )

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str) -> str:
        if v not in KNOWLEDGE_CATEGORIES:
            raise ValueError(f"Unknown knowledge category: {v}")
        return v

    @field_validator("extraction_confidence")
    @classmethod
    def _validate_confidence(cls, v: str) -> str:
        if v not in CONFIDENCE_TIERS:
            raise ValueError(f"Unknown extraction confidence tier: {v}")
        return v


class SkillReadiness(BaseModel):
    """Per-category readiness score (0-4) for one skill, plus an overall summary.
    Produced by kriya/knowledge/rubric.py::score_skill."""

    skill_name: str
    category_levels: dict = Field(default_factory=dict, description="category -> level (0-4)")
    missing_categories: List[str] = Field(default_factory=list, description="Categories with zero facts at all.")
    overall_level: int = Field(default=0, description="Lowest category level - a skill is only as ready as its weakest category.")

    def level_for(self, category: str) -> int:
        return self.category_levels.get(category, 0)
