"""Skill-gap rule/example extraction: dedup, misattribution filtering, conflict staging, and verification-status splitting. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


_RULE_DEDUP_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "must", "to", "of", "in", "on",
    "for", "and", "or", "not", "be", "this", "that", "with", "as", "when", "if",
    "do", "does", "it", "its", "your", "you", "will", "can", "e", "g", "see", "at",
    "by", "from", "into", "than", "any", "no", "so", "such", "which", "even",
    "though", "either", "both", "same", "before", "after", "then", "here", "there",
})


def _rule_content_words(text: str) -> set:
    words = re.findall(r"[a-z0-9][a-z0-9._:/-]*", text.lower())
    return {w for w in words if w not in _RULE_DEDUP_STOPWORDS and len(w) > 1}


def _is_near_duplicate_rule(candidate: str, existing_rules: Iterable[str], min_words: int = 4, threshold: float = 0.5) -> bool:
    """Best-effort, no-LLM check for whether `candidate` just restates an existing
    rule in different words - exact-string dedup (see _write_skill_extraction) misses
    this, since independent SkillGapAgent extraction calls against overlapping
    reference material phrase the same underlying fact differently each time
    (confirmed live: qpid/rules.txt accumulated ~11 such near-duplicates across one
    session's repeated skill-gap prompts, all exact-string-distinct). Uses a content-
    word overlap coefficient (intersection / smaller set's size, stopwords stripped)
    rather than Jaccard similarity, since Jaccard penalizes a short rule being fully
    subsumed by a longer, more detailed one - exactly the real pattern observed
    (a terse rephrasing vs. the original's fuller explanation)."""
    candidate_words = _rule_content_words(candidate)
    if len(candidate_words) < min_words:
        return False
    for existing in existing_rules:
        existing_words = _rule_content_words(existing)
        smaller = min(len(candidate_words), len(existing_words))
        if smaller < min_words:
            continue
        overlap = len(candidate_words & existing_words) / smaller
        if overlap >= threshold:
            return True
    return False


def _sanitize_for_flat_file_line(text: str) -> str:
    """Collapses embedded newlines (and any other run of whitespace) to a
    single space before a piece of extracted text is written into
    rules.txt/staged_rules.txt. Every reader of these flat files parses
    strictly line-by-line (kriya/skills/skill.py's discover_and_load(),
    kriya/cli.py's skills_list/skills_approve) - an unsanitized embedded
    newline silently splits into multiple unrelated "rules" with no error
    on read-back (2026-08-12 SME review). Also strips leading/trailing
    whitespace, matching every reader's own .strip() on each line."""
    return " ".join(text.split())


def _scoped_skill_gap_description(skill_name: str) -> str:
    """Per-skill extraction gap description, used instead of reusing the full
    (possibly multi-skill) human-facing gap_reason text for every skill resolved
    from one skill-gap prompt - the shared, ambiguous description was the real
    cause of a confirmed live bug: when several skills are simultaneously
    unverified for one goal, Kriya asks a SINGLE combined question ("unverified
    skill(s) relevant to this goal: qpid, ignite-java17..."), but a human only
    supplies ONE reference in response. Every co-flagged skill's extraction call
    was reusing that same combined description, so a model extracting against
    Ignite-only reference material while told the gap was "qpid, ignite-java17"
    had no unambiguous signal that *this* call was about qpid specifically -
    confirmed live: Ignite-specific rules got written into qpid/rules.txt this
    way. Narrowing the description to name only the one skill this call is
    actually for gives the model's own "return empty if irrelevant" instruction
    (see SkillGapAgent.system_prompt) something unambiguous to act on."""
    return (
        f"Kriya doesn't have verified information for the skill '{skill_name}' (never had a "
        "passing Runtime Verification Gate run, and no rule in it has been human-promoted). "
        f"Extract ONLY rules/examples that are actually about '{skill_name}' from the reference "
        "material below. If the material is about a different technology entirely (even if that "
        "technology was also mentioned as part of a separate, unrelated gap in the same run), "
        "return empty lists/objects for all fields rather than forcing something irrelevant."
    )


def _loose_identity_words(text: str) -> set:
    """Tokenizer for _likely_misattributed_sibling only - splits on ANY non-
    alphanumeric character (dots, colons, slashes, hyphens included), unlike
    _rule_content_words which deliberately keeps those joined for whole-rule-
    phrasing comparison. An identity term like "ignite" needs to be found
    inside a Maven coordinate ("org.apache.ignite:ignite-core") or a package
    import ("org.apache.ignite.Ignition") or a filename ("ignite-config.xml"),
    none of which _rule_content_words' tokenizer would split apart."""
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2 and w not in _RULE_DEDUP_STOPWORDS}


# Words too generic to discriminate between two skills even when they show up
# in a skill's own name/tags - e.g. "apache" is shared by "apache-ignite" and
# any org.apache.* package (Qpid, Artemis, ...), so without this exclusion a
# genuinely qpid-only rule mentioning "org.apache.qpid..." would spuriously
# "hit" ignite-java17's identity via the word "apache" alone, masking a real
# misattribution the other direction. Only applied to a skill's own identity
# fingerprint, not to arbitrary rule/example text - a name or tag needs to be
# distinctive to be useful as an identity signal.
_IDENTITY_GENERIC_WORDS = frozenset({"apache", "org", "com", "software", "foundation", "project", "java"})


def _skill_identity_words(skill: Any) -> set:
    words = set(_loose_identity_words(skill.name))
    for tag in skill.tags:
        words.update(_loose_identity_words(tag))
    return words - _IDENTITY_GENERIC_WORDS


def _likely_misattributed_sibling(text: str, target: Any, siblings: Iterable[Any]) -> Optional[str]:
    """Deterministic (no LLM call) code-level guard against the same multi-
    skill-gap-prompt misattribution bug _scoped_skill_gap_description addresses
    at the prompt level - a properly scoped prompt reduces the failure rate but
    isn't a guarantee the model always complies, so this is a second, cheap
    check in the same spirit as _is_near_duplicate_rule. Compares a candidate
    rule/example's own identity words against the TARGET skill's identity (name
    + tags) versus each SIBLING skill's (the other skills co-flagged in the
    same combined gap prompt - the only other plausible source of this
    content). Only flags when the target's OWN identity terms are completely
    ABSENT from the text while a sibling's ARE present - a specific, concrete
    signal, not a vague topical-similarity guess, so a rule that genuinely
    mentions both skills (a legitimate comparison/contrast) is never wrongly
    dropped. A false negative here (missing a real misattribution) is far
    cheaper than a false positive (discarding genuinely on-topic content), so
    the check is deliberately conservative in that direction.

    Abstains entirely (returns None) when the TARGET's own identity word set
    is empty - e.g. a skill literally named "java", with no other tags, has
    nothing left after _IDENTITY_GENERIC_WORDS subtracts "java" itself. An
    empty target identity means this heuristic has no signal, not that the
    target's identity is definitionally absent from every text - without
    this check, EVERY rule extracted for such a skill would fail the
    "target's own words present" short-circuit unconditionally and get
    compared ONLY against siblings, so any incidental word overlap with a
    sibling wrongly flags genuinely on-topic content as misattributed
    (2026-08-12 SME review) - the opposite of this function's own stated
    "false negative is cheaper than a false positive" design."""
    text_words = _loose_identity_words(text)
    target_words = _skill_identity_words(target)
    if not text_words or not target_words:
        return None
    if target_words & text_words:
        return None
    for sibling in siblings:
        sibling_words = _skill_identity_words(sibling)
        if sibling_words and (text_words & sibling_words):
            return sibling.name
    return None


def _filter_misattributed_extraction(extraction: Dict[str, Any], target: Any, siblings: List[Any]) -> Dict[str, Any]:
    """Applies _likely_misattributed_sibling to every candidate rule/example in
    an extraction result, dropping (not redirecting - a wrong redirect could
    actively corrupt a different skill, a wrong drop just loses one candidate
    that can be re-supplied on a future run) anything that looks like it
    belongs to a co-flagged sibling skill instead of the target."""
    if not siblings:
        return extraction
    kept_rules = []
    for r in extraction.get("rules") or []:
        sibling = _likely_misattributed_sibling(r, target, siblings)
        if sibling:
            logger.warning(f"Dropping a rule extracted for skill '{target.name}' - identity-term match suggests it's actually about co-flagged sibling skill '{sibling}': {r[:100]}")
            continue
        kept_rules.append(r)
    kept_examples = {}
    for basename, content in (extraction.get("examples") or {}).items():
        sibling = _likely_misattributed_sibling(f"{basename} {content}", target, siblings)
        if sibling:
            logger.warning(f"Dropping example '{basename}' extracted for skill '{target.name}' - identity-term match suggests it's actually about co-flagged sibling skill '{sibling}'.")
            continue
        kept_examples[basename] = content
    return {"rules": kept_rules, "examples": kept_examples, "conflicts": extraction.get("conflicts") or []}


def _write_skill_extraction(skill: Any, extraction: Dict[str, Any], source: str = "unknown", *, engine: Any = None) -> bool:
    """Writes newly extracted rules/examples straight into a skill's own files - per
    the design decision that user-supplied-in-response-to-a-direct-question content is
    a strong enough intent signal to skip the staged/approve flow used for unattended
    lesson extraction. `skill` is the already-loaded Skill object (has source_path set
    by SkillEngine.discover_and_load), avoiding a redundant re-scan of the skills dir.

    `source` (e.g. "live_lookup:<url>", "human_url:<url>", "human_text") is recorded
    per new rule in a parallel provenance file (kriya/skills/skill.py -
    record_rule_provenance) - not a rules.txt format change, so existing skills need
    no migration. Every newly-written rule starts unverified there until a passing
    Runtime Verification run proves it (see mark_rules_verified).

    C-04: every byte goes through ``engine`` (the Skills owner's one write entry, `SkillEngine.write_skill_path`). A write the owner
    refuses (the shipped library, a root bound to another project) changes nothing and returns False; the run goes on."""
    from kriya.skills.skill import SkillWriteRefused, record_rule_provenance, skill_write_owner
    if not skill.source_path:
        return False
    engine = skill_write_owner(engine, os.path.dirname(os.path.abspath(skill.source_path)))
    try:
        engine.authorize_skill_write(os.path.join(skill.source_path, "rules.txt"))
    except SkillWriteRefused as refusal:
        logger.warning(f"Extracted content for skill '{skill.name}' not written ({refusal.reason_code}): {refusal}")
        return False

    new_rules = extraction.get("rules") or []
    if new_rules:
        existing = set(skill.rules)
        to_add = []
        effective_existing = list(skill.rules)
        for r in new_rules:
            r = _sanitize_for_flat_file_line(r)
            if not r or r in existing:
                continue
            if _is_near_duplicate_rule(r, effective_existing):
                logger.info(f"Skipping near-duplicate rule for skill '{skill.name}' (already covered by an existing rule): {r[:100]}")
                continue
            to_add.append(r)
            effective_existing.append(r)
        if to_add:
            rules_file = os.path.join(skill.source_path, "rules.txt")
            engine.write_skill_path(rules_file, _existing_text(rules_file) + "".join(f"\n{r}" for r in to_add))
            engine.commit_skill_change(rules_file, f"Kriya: add {len(to_add)} rule(s) to skill '{skill.name}' from supplied reference material")
            for r in to_add:
                record_rule_provenance(skill.source_path, r, source, engine=engine)

    new_examples = extraction.get("examples") or {}
    if new_examples:
        examples_dir = os.path.join(skill.source_path, "examples")
        for basename, content in new_examples.items():
            safe_basename = os.path.basename(basename)
            if not safe_basename:
                continue
            example_path = os.path.join(examples_dir, safe_basename)
            # An existing example file represents previously-curated/verified content
            # (often hand-written or fixed after a real live failure) - a fresh,
            # unreviewed extraction must never silently clobber it. Confirmed live:
            # this exact path overwrote a verified exec-maven-plugin/compiler-plugin
            # pom.xml example with a bare-dependencies-only version extracted from
            # generic reference material, discarding real prior work. Same protective
            # philosophy as the rules.txt dedup above - existing content wins, new
            # content is additive only.
            if os.path.exists(example_path):
                logger.info(f"Skipping example '{safe_basename}' for skill '{skill.name}' - a file already exists at that path and existing examples are never overwritten by extraction.")
                continue
            engine.write_skill_path(example_path, content)
            engine.commit_skill_change(example_path, f"Kriya: add example '{safe_basename}' to skill '{skill.name}' from supplied reference material")
    return True


def _existing_text(path: str) -> str:
    """The file's present text ('' when absent): an append is a whole-file write through the one entry."""
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _stage_skill_conflicts(skill: Any, conflicts: List[Dict[str, str]], *, engine: Any = None) -> None:
    """Surfaces candidate rules that contradict a skill's existing rules into the same
    staged_rules.txt file (and 'kriya skills list' display) already used for
    auto-extracted lessons, so a human notices and resolves them - rather than either
    silently discarding the new information or silently overwriting the existing rule."""
    if not skill.source_path or not conflicts:
        return
    from kriya.skills.skill import SkillWriteRefused, skill_write_owner
    engine = skill_write_owner(engine, os.path.dirname(os.path.abspath(skill.source_path)))
    staged_file = os.path.join(skill.source_path, "staged_rules.txt")
    lines = []
    for c in conflicts:
        candidate = _sanitize_for_flat_file_line(c.get("candidate_rule", ""))
        existing = _sanitize_for_flat_file_line(c.get("conflicts_with", ""))
        reason = _sanitize_for_flat_file_line(c.get("reason", ""))
        if candidate:
            lines.append(f"\n[CONFLICT] {candidate} -- conflicts with existing rule: '{existing}' ({reason})")
    try:
        engine.write_skill_path(staged_file, _existing_text(staged_file) + "".join(lines))
    except SkillWriteRefused as refusal:
        logger.warning(f"Conflicting candidate rule(s) for skill '{skill.name}' not staged ({refusal.reason_code}): {refusal}")
        return
    engine.commit_skill_change(staged_file, f"Kriya: flag {len(conflicts)} conflicting candidate rule(s) for skill '{skill.name}'")


def _skill_verification_context(skill: Any, goal: str) -> str:
    """Best-effort description of what was actually verified (e.g. "qpid 9.2.1"),
    recorded as advisory provenance on the skill (visible via 'kriya skills list'/
    'show') so a human can judge staleness themselves later - a pinned version gets
    yanked, a new major version changes the config shape, etc. Deliberately not used
    to automatically re-trigger anything; reuses the same version-extraction already
    used for supported_versions filtering and missing-skill detection."""
    try:
        from kriya.skills.skill import mentions_term
        from kriya.tools.knowledge import extract_library_versions
        for lib, ver in extract_library_versions(goal):
            if mentions_term(skill.name, lib) or any(mentions_term(lib, t) for t in skill.tags):
                return f"{lib} {ver}"
    except Exception as ex:
        logger.debug(f"Failed to compute skill verification context: {ex}")
    return "version unspecified"


def _skill_staleness_warning(skill: Any, goal_lib: str, goal_ver: str) -> Optional[str]:
    """Cheap staleness nudge for an active skill whose verified_context names
    a DIFFERENT version of the same library the current goal mentions -
    architectural add-on from a 2026-08-12 SME review: verified_context's own
    field docstring already says "advisory provenance... a pinned version
    gets yanked, a new major version changes the config shape" but nothing
    ever read it back to check. Reuses the exact (lib, ver) pair the caller
    already extracted for supported_versions filtering - no new extraction
    call. Purely observational: never blocks generation or un-verifies
    anything, matching that same docstring's "not used to automatically
    re-trigger anything" - just surfaces what a human would otherwise have
    to notice themselves via `kriya skills show`."""
    if not skill.verified_context or skill.verified_context == "version unspecified":
        return None
    verified_prefix = f"{goal_lib} "
    if not skill.verified_context.startswith(verified_prefix):
        return None
    verified_ver = skill.verified_context[len(verified_prefix):].strip()
    if verified_ver and verified_ver != goal_ver:
        when = f" on {skill.verified_at}" if skill.verified_at else ""
        return (
            f"Skill '{skill.name}' was last verified against {goal_lib} {verified_ver}{when}, "
            f"but this goal mentions {goal_lib} {goal_ver} - its rules may not all still apply."
        )
    return None


def _split_rules_by_verification(skill: Any) -> Tuple[List[str], List[str]]:
    """Splits a skill's rules into (trusted, unverified), in rule order. C-06: a projection of the Skills owner's decision, made when the
    skill was loaded (`SkillEngine._trusted_rules`: the skill's location, its provenance records and the project the run works for); this
    function reads no file and decides nothing. A rule the owner did not trust is unverified - a workspace skill's rules, a rule a run
    wrote and no committed run has verified, a rule another project's run verified, every rule of a skill whose provenance cannot be read.

    A `Skill` that was not loaded from a folder (no ``source_path``: built in memory, never by the engine) has no owner decision; its
    rules are returned as trusted, as before."""
    if not skill.source_path:
        return list(skill.rules), []
    remaining = list(skill.trusted_rules)
    trusted, unverified = [], []
    for r in skill.rules:
        if r in remaining:
            remaining.remove(r)
            trusted.append(r)
        else:
            unverified.append(r)
    return trusted, unverified


def planning_skill_constraints(config: Any, workspace_path: str, goal: str, *, skill_engine: Any = None) -> Tuple[str, List[str]]:
    """The applicable skills' conventions for the Planner: ``(block, skill_names)``, ``("", [])`` when none apply.

    Selected by ``skill_applies_to_goal`` over the goal the plan serves, and rendered by ``render_skill_conventions``
    - the same two functions the Developer-side analysis uses - so the Planner sees the constraint the Developers will
    be expected to obey, and only the skills applicable to this goal (never the whole skills directory). Lossless: no
    rule is summarised or dropped to save tokens. Read-only: it never writes, bootstraps or marks a skill.

    Moved here verbatim from ``skill_conventions.py`` (ARCH-TESTS-FOUR-FAIL-AT-77603DB-SKILL-CONVENTIONS-CYCLE-001): its
    imports below are all-import-cycle members, and ``skill_conventions`` is imported by kernel owner modules that must
    stay outside every cycle. The enforce loop receives this function through ``EnforceDeps`` and never imports this
    module."""
    from kriya.analyzer.analyzer import RepositoryAnalyzer
    from kriya.skills.skill import SkillEngine, fact_match, is_version_supported, mentions_term
    from kriya.tools.knowledge import extract_library_versions
    from kriya.workflow.skill_conventions import (
        PLANNING_SKILL_CONSTRAINTS_HEADER,
        render_skill_conventions,
        skill_applies_to_goal,
    )

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
