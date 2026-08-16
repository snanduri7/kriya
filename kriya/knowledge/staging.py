"""Reads/writes the structured knowledge files that live alongside a skill's existing
rules.txt/staged_rules.txt: `staged_knowledge.json` (pending, unreviewed facts from
any channel) and `knowledge.json` (facts that have been promoted/approved). Deliberately
a NEW, separate pair of files - this never touches or reformats rules.txt/staged_rules.txt's
existing flat-line format, so `kriya skills list`/today's `skills approve` keep working
exactly as before regardless of whether this module is ever called.

Dedup and provenance reuse the same machinery already proven for the richer, human-
supplied-content path in kriya/workflow/skill_extraction.py, rather than the weaker
exact-string-only dedup the old inline lesson-extraction block used.
"""
import json
import logging
import os
from typing import List

from kriya.knowledge.schema import KnowledgeFact
from kriya.skills.skill import git_commit_if_tracked, record_rule_provenance
from kriya.workflow.skill_extraction import _is_near_duplicate_rule, _sanitize_for_flat_file_line

logger = logging.getLogger(__name__)

STAGED_KNOWLEDGE_FILENAME = "staged_knowledge.json"
KNOWLEDGE_FILENAME = "knowledge.json"


def _staged_path(skill_folder: str) -> str:
    return os.path.join(skill_folder, STAGED_KNOWLEDGE_FILENAME)


def _knowledge_path(skill_folder: str) -> str:
    return os.path.join(skill_folder, KNOWLEDGE_FILENAME)


def _load_facts(path: str) -> List[KnowledgeFact]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [KnowledgeFact.model_validate(item) for item in raw]
    except Exception as e:
        logger.warning(f"Failed to load knowledge facts from '{path}' (non-fatal): {e}")
        return []


def _save_facts(path: str, facts: List[KnowledgeFact]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([f.model_dump() for f in facts], f, indent=2)


def load_staged(skill_folder: str) -> List[KnowledgeFact]:
    return _load_facts(_staged_path(skill_folder))


def load_knowledge(skill_folder: str) -> List[KnowledgeFact]:
    return _load_facts(_knowledge_path(skill_folder))


def stage_facts(skill_folder: str, facts: List[KnowledgeFact]) -> List[KnowledgeFact]:
    """Appends `facts` to staged_knowledge.json, skipping anything that's an exact or
    near-duplicate of a fact already staged or already approved for this skill. Returns
    the facts actually written (a subset of the input, in the same order), so callers
    can log/report what was newly staged. Best-effort like every other skill-file write
    in this subsystem - never raises, since a failure here shouldn't fail the generation
    run that triggered it."""
    if not facts:
        return []
    try:
        os.makedirs(skill_folder, exist_ok=True)
        existing = load_staged(skill_folder) + load_knowledge(skill_folder)
        existing_values = [f.value for f in existing]

        written: List[KnowledgeFact] = []
        for fact in facts:
            value = _sanitize_for_flat_file_line(fact.value)
            if not value:
                continue
            if value in existing_values or _is_near_duplicate_rule(value, existing_values):
                logger.info(f"Skipping near-duplicate knowledge fact for '{skill_folder}': {value[:100]}")
                continue
            fact = fact.model_copy(update={"value": value})
            written.append(fact)
            existing_values.append(value)

        if written:
            all_staged = load_staged(skill_folder) + written
            _save_facts(_staged_path(skill_folder), all_staged)
        return written
    except Exception as e:
        logger.warning(f"Failed to stage knowledge facts for '{skill_folder}' (non-fatal): {e}")
        return []


def promote_staged(skill_folder: str) -> List[KnowledgeFact]:
    """The structured analogue of `kriya skills approve`: moves every staged fact into
    knowledge.json (the approved record the readiness rubric scores) and flattens each
    fact's value into a rules.txt line - via the same append + record_rule_provenance
    pattern skill_extraction.py already uses for human-supplied content - so approved
    knowledge reaches generation prompts through the format SkillEngine already loads,
    with no change needed to the loading/prompt-building path. Returns the promoted facts."""
    staged = load_staged(skill_folder)
    if not staged:
        return []

    rules_file = os.path.join(skill_folder, "rules.txt")
    existing_rules: List[str] = []
    if os.path.exists(rules_file):
        with open(rules_file, "r", encoding="utf-8") as rf:
            existing_rules = [line.strip() for line in rf if line.strip()]

    promoted: List[KnowledgeFact] = []
    new_rules: List[KnowledgeFact] = []
    for fact in staged:
        if fact.value in existing_rules or _is_near_duplicate_rule(fact.value, existing_rules):
            promoted.append(fact)  # already effectively covered - still clears staging
            continue
        new_rules.append(fact)
        existing_rules.append(fact.value)
        promoted.append(fact)

    if new_rules:
        with open(rules_file, "a", encoding="utf-8") as rf:
            for fact in new_rules:
                rf.write(f"\n{fact.value}")
        for fact in new_rules:
            record_rule_provenance(skill_folder, fact.value, source=f"knowledge_channel:{fact.source_channel}")
        git_commit_if_tracked(rules_file, f"Kriya: promote {len(new_rules)} structured knowledge fact(s) to skill '{os.path.basename(skill_folder)}'")

    knowledge = load_knowledge(skill_folder) + promoted
    _save_facts(_knowledge_path(skill_folder), knowledge)
    git_commit_if_tracked(_knowledge_path(skill_folder), f"Kriya: record {len(promoted)} promoted knowledge fact(s) for skill '{os.path.basename(skill_folder)}'")

    os.remove(_staged_path(skill_folder))
    return promoted


def record_direct_fact(skill_folder: str, fact: KnowledgeFact) -> bool:
    """Writes a single fact straight to rules.txt + knowledge.json, skipping
    staged_knowledge.json entirely - for kriya/knowledge/scaffold.py's human-answered
    gap questions, where a human directly answering a targeted question is already a
    strong intent signal (same trust tier as skill_extraction.py's own
    _write_skill_extraction, which skips staging for the same reason). Returns whether
    the fact was newly written (False if it was a duplicate of an existing rule)."""
    os.makedirs(skill_folder, exist_ok=True)
    value = _sanitize_for_flat_file_line(fact.value)
    if not value:
        return False
    fact = fact.model_copy(update={"value": value})

    rules_file = os.path.join(skill_folder, "rules.txt")
    existing_rules: List[str] = []
    if os.path.exists(rules_file):
        with open(rules_file, "r", encoding="utf-8") as rf:
            existing_rules = [line.strip() for line in rf if line.strip()]

    if value in existing_rules or _is_near_duplicate_rule(value, existing_rules):
        logger.info(f"Skipping near-duplicate direct knowledge fact for '{skill_folder}': {value[:100]}")
        return False

    with open(rules_file, "a", encoding="utf-8") as rf:
        rf.write(f"\n{value}")
    record_rule_provenance(skill_folder, value, source=f"knowledge_channel:{fact.source_channel}")
    git_commit_if_tracked(rules_file, f"Kriya: add human-answered knowledge fact to skill '{os.path.basename(skill_folder)}'")

    knowledge = load_knowledge(skill_folder) + [fact]
    _save_facts(_knowledge_path(skill_folder), knowledge)
    git_commit_if_tracked(_knowledge_path(skill_folder), f"Kriya: record human-answered knowledge fact for skill '{os.path.basename(skill_folder)}'")
    return True
