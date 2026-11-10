"""The one place a goal's applicable skills are selected and rendered into model context.

SHARED-DECISION-AUTHORITY-001 (owner ruling 2026-11-10). Skills define which implementation constraints and alternatives
are allowed; the Developers are given them. A choice between mutually exclusive alternatives that spans several subtasks
can only be made once, upstream of those subtasks, by the Planner - so the Planner must see the SAME applicable skills
the Developers will be given. Before this module the selection and rendering lived inline in ``stage_analysis.
analyze_work_unit`` and only the Developer-side convention prompt saw them. They are moved here verbatim, and both
callers use these functions, so there is no second selection path that could drift from the first.

Nothing here stores, compiles or indexes skills: ``SkillEngine`` stays the only store.
"""
from __future__ import annotations

import logging
import os
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
    return text


def planning_skill_constraints(config: Any, workspace_path: str, goal: str, *, skill_engine: Any = None) -> Tuple[str, List[str]]:
    """The applicable skills' conventions for the Planner: ``(block, skill_names)``, ``("", [])`` when none apply.

    Selected by ``skill_applies_to_goal`` over the goal the plan serves, and rendered by ``render_skill_conventions``
    - the same two functions the Developer-side analysis uses - so the Planner sees the constraint the Developers will
    be expected to obey, and only the skills applicable to this goal (never the whole skills directory). Lossless: no
    rule is summarised or dropped to save tokens. Read-only: it never writes, bootstraps or marks a skill."""
    from kriya.analyzer.analyzer import RepositoryAnalyzer
    from kriya.skills.skill import SkillEngine, fact_match, is_version_supported, mentions_term
    from kriya.tools.knowledge import extract_library_versions
    from kriya.workflow.skill_extraction import _skill_staleness_warning, _split_rules_by_verification

    engine = skill_engine
    if engine is None:
        engine = SkillEngine.from_config(config, workspace_path=workspace_path)
        engine.discover_and_load()
    repo_model = RepositoryAnalyzer(workspace_path).analyze()
    repo_slug = os.path.basename(workspace_path).lower().strip(".") or "root"
    blocks: List[str] = []
    names: List[str] = []
    for skill in engine.list_skills():
        applies, _ = skill_applies_to_goal(
            skill, goal, repo_slug=repo_slug, repo_model=repo_model, mentions_term=mentions_term, fact_match=fact_match,
            extract_library_versions=extract_library_versions, is_version_supported=is_version_supported,
            staleness_warning=_skill_staleness_warning)
        if not applies:
            continue
        rendered = render_skill_conventions(skill, engine, _split_rules_by_verification)
        if rendered:
            blocks.append(rendered)
            names.append(skill.name)
    if not blocks:
        return "", []
    return PLANNING_SKILL_CONSTRAINTS_HEADER + "".join(blocks), names
