"""Turns a SkillReadiness score into targeted, per-category questions for a human -
the completion step that fires after every automatic channel (repo_manifest,
live_failure, and later doc-ingestion/registry channels) has already run and the
rubric says a category is still thin. This is what answers the original problem this
whole subsystem exists for: a human doesn't know what to write or how detailed it
needs to be, so instead of a blank rules.txt they get "Dependencies: Level 3, APIs:
Level 1 - what's the exact method signature/callable symbol used for X?"

A human's direct answer to one of these questions is a strong intent signal - same
trust tier as kriya/workflow/skill_extraction.py's own _write_skill_extraction - so
it's recorded straight into rules.txt/knowledge.json via
kriya/knowledge/staging.py::record_direct_fact, skipping the staged/approve flow used
for unattended, automatic extraction.
"""
from typing import List

from kriya.knowledge import staging
from kriya.knowledge.schema import KNOWLEDGE_CATEGORIES, KnowledgeFact, SkillReadiness

# Below this level, a category is considered thin enough to be worth asking about.
# Level 4 ("verified by a real run") is a bonus, not a gate - we never nag for it.
QUESTION_THRESHOLD = 3

_QUESTION_TEMPLATES = {
    "Metadata": "What is this skill's subject, its exact identity/name, and which runtime(s) does it target?",
    "Compatibility": "Which exact versions/version ranges of the dependency does this apply to?",
    "Dependencies": "What are the exact dependency coordinates (group:artifact:version, or package==version)?",
    "APIs": "What's the exact method signature or callable symbol involved, not a description of it?",
    "Configuration": "What's the exact config key/flag/value required (e.g. an exact JVM flag or property name)?",
    "Rules": "What specific, non-obvious rule or gotcha must future generations follow here?",
    "Examples": "Can you provide a real, runnable code example - not a description of one?",
    "Verification": "How can a generated solution be checked as actually correct (a command, a test, an assertion)?",
    "Constraints": "What must never be done here, and what breaks if it is?",
    "BestPractices": "What's the recommended approach here, and why does the alternative fall short?",
}

_DEFAULT_TEMPLATE = "What exact, concrete fact is missing for this category?"


def generate_gap_questions(readiness: SkillReadiness, threshold: int = QUESTION_THRESHOLD) -> List[str]:
    """One question per category still below `threshold`, in the fixed taxonomy order
    so output is stable/scannable run to run. Categories already at or above threshold
    are silent - this only ever asks about genuine gaps, never busywork."""
    questions = []
    for category in KNOWLEDGE_CATEGORIES:
        level = readiness.level_for(category)
        if level >= threshold:
            continue
        template = _QUESTION_TEMPLATES.get(category, _DEFAULT_TEMPLATE)
        questions.append(f"[{category}: Level {level}] {template}")
    return questions


def record_scaffold_answer(skill_folder: str, category: str, answer: str) -> bool:
    """Writes a human's answer to one gap question straight into the skill's approved
    knowledge (bypassing staging - see module docstring). Returns whether it was newly
    written (False if it turned out to duplicate an existing rule)."""
    fact = KnowledgeFact(
        category=category,
        key=answer[:60],
        value=answer,
        source_channel="human_scaffold",
        extraction_confidence="human_supplied",
        provenance=f"human-answered scaffold question for category '{category}'",
    )
    return staging.record_direct_fact(skill_folder, fact)
