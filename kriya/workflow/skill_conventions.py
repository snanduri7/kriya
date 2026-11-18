"""The one place a goal's applicable skills are selected and rendered into model context.

SHARED-DECISION-AUTHORITY-001 (owner ruling 2026-11-10). Skills define which implementation constraints and alternatives
are allowed; the Developers are given them. A choice between mutually exclusive alternatives that spans several subtasks
can only be made once, upstream of those subtasks, by the Planner - so the Planner must see the SAME applicable skills
the Developers will be given. Before this module the selection and rendering lived inline in ``stage_analysis.
analyze_work_unit`` and only the Developer-side convention prompt saw them. They are moved here verbatim, and both
callers use these functions, so there is no second selection path that could drift from the first.

Nothing here stores, compiles or indexes skills: ``SkillEngine`` stays the only store.

This module imports NOTHING from ``kriya`` (ARCH-TESTS-FOUR-FAIL-AT-77603DB-SKILL-CONVENTIONS-CYCLE-001). It is imported
by kernel owner modules (``stage_analysis``), which must stay outside every import cycle, so every collaborator
(``mentions_term``, ``fact_match``, the analyzer, the engine ...) arrives as a parameter. The Planner-side composition
that needs those collaborators, ``planning_skill_constraints``, lives in ``kriya/workflow/skill_extraction.py`` and
reaches the enforce loop through ``EnforceDeps``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, List, Optional, Tuple

_logger = logging.getLogger(__name__)

PLANNING_SKILL_CONSTRAINTS_HEADER = (
    "\n\n=== Applicable skill constraints (the Developers of the subtasks you plan are given these same skills) ===\n"
    "These are the implementation constraints and allowed alternatives that will bind the code. Where they offer "
    "mutually exclusive alternatives and the choice affects more than one subtask, make that choice here, once, in "
    "an integration_relationships entry (see the planner contract); do not leave it for each subtask to decide.\n"
)


def skill_applies_to_goal(
    skill: Any,
    goal: str,
    *,
    repo_slug: str,
    repo_model: Any,
    mentions_term: Callable[[str, str], bool],
    fact_match: Callable[[Any, Any], bool],
    extract_library_versions: Callable[[str], Iterable[Tuple[str, str]]],
    is_version_supported: Callable[[str, str], bool],
    staleness_warning: Callable[[Any, str, str], Optional[str]],
    logger: Any = _logger,
) -> Tuple[bool, List[str]]:
    """Whether ``skill`` is applicable to ``goal`` in this repository, and the staleness warnings it raised.

    The relevance predicate and the version-range / staleness checks exactly as ``analyze_work_unit`` has always
    applied them (name or tag mentioned in the goal, the per-repo auto skill, or a tag naming a dependency or
    framework the repository analysis found; then the goal's mentioned library version against the skill's
    ``supported_versions``)."""
    warnings: List[str] = []
    is_relevant = (
        mentions_term(goal, skill.name) or
        any(mentions_term(goal, tag) for tag in skill.tags) or
        skill.name.lower() == f"auto-{repo_slug}" or
        fact_match(skill, repo_model)
    )
    if is_relevant:
        for lib, ver in extract_library_versions(goal):
            if mentions_term(skill.name, lib) or any(mentions_term(lib, t) for t in skill.tags):
                if skill.supported_versions != "*":
                    if not is_version_supported(ver, skill.supported_versions):
                        logger.info(
                            f"Skipping skill '{skill.name}' because version '{ver}' does not satisfy constraint "
                            f"'{skill.supported_versions}'")
                        return False, warnings
                staleness = staleness_warning(skill, lib, ver)
                if staleness:
                    warnings.append(staleness)
                    logger.info(f"Skill staleness: {staleness}")
    return bool(is_relevant), warnings


def render_skill_conventions(skill: Any, skill_engine: Any, split_rules_by_verification: Callable[[Any], Tuple[List[str], List[str]]]) -> str:
    """The skill's conventions block as a model sees it (rules split trusted / unverified, instructions, examples);
    empty for a skill with neither rules nor instructions."""
    if not (skill.rules or skill.instructions):
        return ""
    text = f"\n\n=== Engineering Skill Conventions: {skill.name} ===\n"
    if skill.rules:
        trusted_rules, unverified_rules = split_rules_by_verification(skill)
        # P-B: a rule proven by a passing run of THIS run is trusted now, though its provenance flips only at the terminal boundary
        trusted_rules, unverified_rules = skill_engine.reclassify_held_rules(skill.name, trusted_rules, unverified_rules)
        if trusted_rules:
            text += "Rules:\n" + "\n".join(f"- {r}" for r in trusted_rules) + "\n"
        if unverified_rules:
            text += (
                "Unverified Rules (auto-extracted, not yet proven by a passing run - "
                "use with appropriate caution, prefer Rules above if they conflict):\n"
                + "\n".join(f"- {r}" for r in unverified_rules) + "\n"
            )
    if skill.instructions:
        text += f"Instructions:\n{skill.instructions}\n"
    if skill.examples:
        text += "Examples:\n"
        for basename, content in skill.examples.items():
            text += f"=== Example File: {basename} ===\n{content}\n"
    return text + render_integration_decisions(skill)


def canonical_decision_id(skill_name: str, local_id: str) -> str:
    """The plan-level identity of a skill-declared integration decision (the skill's name namespaces its local id)."""
    return f"{skill_name}:{local_id}"


def render_integration_decisions(skill: Any) -> str:
    """SKILL-INTEGRATION-DECISION-AUTHORITY-001: the skill's declared integration decisions as every reader of the skill sees them (the
    Planner and each Developer, through ``render_skill_conventions``): the decision id, each alternative id with the skill's own summary
    of what it means, and the participants. The one place an id gets its meaning; empty for a skill that declares none."""
    decisions = list(getattr(skill, "integration_decisions", None) or [])
    if not decisions:
        return ""
    text = (
        "Integration decisions declared by this skill (each alternative id names one of the mutually exclusive ways described above; "
        "when files matching a decision's participants are planned in two or more subtasks, the plan records ONE "
        "integration_relationships entry whose id is the decision id, whose alternatives are exactly these alternative ids and whose "
        "selected_alternative is one of them, naming exactly those subtasks; each of them implements the selected alternative):\n"
    )
    for decision in decisions:
        text += (f"- decision id: {canonical_decision_id(skill.name, decision.id)} "
                 f"(participants: {', '.join(decision.participants)})\n")
        for alternative in decision.alternatives:
            text += f"  - alternative id: {alternative.id} = {alternative.summary}\n"
    return text


