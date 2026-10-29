"""Stage 1 of a work unit: repository analysis, skills, Graph RAG and the learned reference (full modularization
W4, E3).

Moved verbatim from ``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``): the repository
model (``RepositoryAnalyzer``), the convention prompt (supplementary context, the Java toolchain fact, the goal
facts, the KnowledgeGuard safety constraints, the relevant skills' rules/instructions/examples), skill-gap
detection and resolution (1.2: live lookup, then the human), skill-to-skill conflict resolution (1.3), Graph RAG
retrieval (1.5) and the fenced learned reference (1.6, KNOWLEDGE-READPATH-001: handed in by the caller, never
retrieved here). ``record_active_skills`` is the snapshot of the active skills' rules and manifest taken after the
architecture stage's Stage 2B extraction, before the Developer loop. Collaborators arrive in ``StageDeps``
(``kriya/workflow/stage_context.py``); this module never imports ``kriya.workflow.workflow``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from kriya.policy.approval import await_if_awaitable, resolve_approval_result
from kriya.policy.egress import web_lookup_allowed
from kriya.workflow.evidence import EvidenceRecord
from kriya.workflow.live_lookup import _extract_first_usable, _resolve_via_web_lookup
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.stage_context import StageContext, StageDeps
from kriya.workflow.toolchain import _java_toolchain_fact
from kriya.workflow.untrusted_context import fence_untrusted_reference


async def analyze_work_unit(ctx: StageContext, deps: StageDeps) -> None:
    """Stage 1: repository analysis, the convention prompt with skills, skill gaps and conflicts, Graph RAG and the fenced learned reference. Never stops the unit early."""
    # The stage's run-local names (StageContext) and injected collaborators (StageDeps), bound under the names
    # the body used inside run_generation_workflow; the body below is that method's code, moved verbatim.
    goal, workspace_path, state, control = ctx.goal, ctx.workspace_path, ctx.state, ctx.control
    all_gap_report, supplementary_context = ctx.all_gap_report, ctx.supplementary_context
    skill_engine_override, skill_gap_callback = ctx.skill_engine_override, ctx.skill_gap_callback
    skill_conflict_callback, web_lookup_callback = ctx.skill_conflict_callback, ctx.web_lookup_callback
    web_lookup_query_callback, reference_context = ctx.web_lookup_query_callback, ctx.reference_context
    logger, RepositoryAnalyzer, SkillEngine = deps.logger, deps.RepositoryAnalyzer, deps.SkillEngine
    is_accidental_shared_skills_write, _fact_match = deps.is_accidental_shared_skills_write, deps.fact_match
    _mentions_term, extract_library_versions = deps.mentions_term, deps.extract_library_versions
    is_version_supported, _skill_staleness_warning = deps.is_version_supported, deps._skill_staleness_warning
    _split_rules_by_verification = deps._split_rules_by_verification
    _scoped_skill_gap_description = deps._scoped_skill_gap_description
    _filter_misattributed_extraction = deps._filter_misattributed_extraction
    _stage_skill_conflicts, _write_skill_extraction = deps._stage_skill_conflicts, deps._write_skill_extraction
    _sanitize_for_flat_file_line, fetch_url_text = deps._sanitize_for_flat_file_line, deps.fetch_url_text
    find_conflict_resolution = deps.find_conflict_resolution
    record_conflict_resolution, configured_client = deps.record_conflict_resolution, deps.configured_client
    run_deadline, LocalVectorStore, RetrievalLimits = deps.run_deadline, deps.LocalVectorStore, deps.RetrievalLimits
    retrieval_limits_for, retrieve_graph_context = deps.retrieval_limits_for, deps.retrieve_graph_context
    _reserve_graph_context_budget, allocation_window = deps._reserve_graph_context_budget, deps.allocation_window
    agent_request_capacity, decide_localization = deps.agent_request_capacity, deps.decide_localization
    CALLABLE_KINDS, InferenceDeadlineError = deps.CALLABLE_KINDS, deps.InferenceDeadlineError
    developer_goal_facts = deps.developer_goal_facts

    # Initialize trace lists
    active_skills = []
    retrieved_chunks = []
    skill_staleness_warnings: List[str] = []

    # 1. Analyze repository context
    logger.info("Analyzing workspace context...")
    analyzer = RepositoryAnalyzer(workspace_path)
    repo_model = analyzer.analyze()
    # dependency_versions (added for kriya/knowledge/) is excluded here on purpose -
    # this JSON dump is embedded directly into the Planner/Architect prompts below,
    # so including it would mean every generate run's prompt content changes shape
    # depending on what's in the repo's manifests, even though the field's only
    # real consumer is kriya/knowledge/channels/repo_manifest.py, which reads
    # repo_model.dependency_versions directly and doesn't need it duplicated here.
    # root_path (the absolute host path) is excluded too (PRD-032): every
    # path a model sees is workspace-relative, and the structured plan
    # schema refuses an absolute planned path - a model shown the absolute
    # root copied it into planned_files and was refused for Kriya's own echo.
    repo_context = repo_model.model_dump_json(indent=2, exclude={"dependency_versions", "root_path"})
        
    # Load local workspace conventions if present
    repo_slug = os.path.basename(workspace_path).lower().strip(".")
    if not repo_slug:
        repo_slug = "root"
            
    skills_dir = deps.config.paths.skills
    if is_accidental_shared_skills_write(skills_dir, workspace_path):
        logger.warning(
            f"This project's config doesn't set paths.skills, so any skill writes this run "
            f"makes (auto-bootstrapped conventions, skill-gap extraction, staged lesson rules) "
            f"will land in Kriya's own SHARED install skills directory ({os.path.abspath(skills_dir)}) "
            f"instead of a project-local one - every other project using Kriya would inherit "
            f"them. If that's not intended, stop this run and set paths.skills in this "
            f"project's kriya.yaml, e.g. \"./skills\"."
        )
    if skill_engine_override is None:
        se = SkillEngine.from_config(deps.config, workspace_path=workspace_path)
        se.discover_and_load()
    else:
        se = skill_engine_override
        # GIT-MIDRUN-COMMITS-S3-001: the conflict registry and every other same-run skills-root reader/writer below use the
        # engine's effective root (the run-local shadow while one is active), never the canonical directory.
        skills_dir = se.effective_skills_dir(skills_dir)
        
    convention_prompt = ""
    if supplementary_context:
        convention_prompt += supplementary_context
    java_toolchain_fact = _java_toolchain_fact(goal, workspace_path)
    if java_toolchain_fact:
        convention_prompt += f"\n\n=== Environment Fact ===\n{java_toolchain_fact}\n"
    # BACKEND-READINESS-V2 (T2): the deterministic facts about where the goal's names live, as the Planner had them.
    memory_dir = getattr(getattr(deps.config, "paths", None), "memory", None)
    goal_facts_text = developer_goal_facts(
        goal, workspace_path, os.path.join(memory_dir, "dependency_graph.db") if isinstance(memory_dir, str) else None)
    if goal_facts_text:
        convention_prompt += goal_facts_text
    if all_gap_report.has_gaps:
        convention_prompt += "\n\n=== KNOWLEDGE GUARD SAFETY CONSTRAINTS ===\n"
        for g in all_gap_report.gaps:
            date_str = g["release_date"][:10] if g["release_date"] else "Unknown"
            convention_prompt += (
                f"- WARNING: You are writing code using library '{g['library']}' version '{g['version']}'.\n"
                f"  This version was released on {date_str}, which is after your estimated knowledge cutoff date.\n"
                f"  DO NOT invent API methods or configuration parameters. Restrict yourself strictly to known-good patterns.\n"
            )
        convention_prompt += "==========================================\n"
    for skill in se.list_skills():
        # Check matches with repository facts (dependencies and frameworks) -
        # shared with kriya/knowledge/channels/repo_manifest.py so both call
        # sites use one implementation of "is this skill relevant to this repo".
        is_relevant = (
            _mentions_term(goal, skill.name) or
            any(_mentions_term(goal, tag) for tag in skill.tags) or
            skill.name.lower() == f"auto-{repo_slug}" or
            _fact_match(skill, repo_model)
        )
            
        # Check version-range compatibility, and (independently of any
        # range constraint) whether this goal's mentioned version has
        # drifted from what the skill was last verified against - both
        # need the goal's extracted library/version, computed once here
        # rather than gating the extraction behind supported_versions
        # != "*" the way the range check alone used to.
        if is_relevant:
            libs = extract_library_versions(goal)
            for lib, ver in libs:
                if _mentions_term(skill.name, lib) or any(_mentions_term(lib, t) for t in skill.tags):
                    if skill.supported_versions != "*":
                        if not is_version_supported(ver, skill.supported_versions):
                            is_relevant = False
                            logger.info(f"Skipping skill '{skill.name}' because version '{ver}' does not satisfy constraint '{skill.supported_versions}'")
                            break
                    staleness = _skill_staleness_warning(skill, lib, ver)
                    if staleness:
                        skill_staleness_warnings.append(staleness)
                        logger.info(f"Skill staleness: {staleness}")

        if is_relevant:
            active_skills.append(skill.name)
            if skill.rules or skill.instructions:
                convention_prompt += f"\n\n=== Engineering Skill Conventions: {skill.name} ===\n"
                if skill.rules:
                    trusted_rules, unverified_rules = _split_rules_by_verification(skill)
                    # P-B: a rule proven by a passing run of THIS run is trusted now, though its provenance flips only at the terminal boundary
                    trusted_rules, unverified_rules = se.reclassify_held_rules(skill.name, trusted_rules, unverified_rules)
                    if trusted_rules:
                        convention_prompt += "Rules:\n" + "\n".join(f"- {r}" for r in trusted_rules) + "\n"
                    if unverified_rules:
                        convention_prompt += (
                            "Unverified Rules (auto-extracted, not yet proven by a passing run - "
                            "use with appropriate caution, prefer Rules above if they conflict):\n"
                            + "\n".join(f"- {r}" for r in unverified_rules) + "\n"
                        )
                if skill.instructions:
                    convention_prompt += f"Instructions:\n{skill.instructions}\n"
                if skill.examples:
                    convention_prompt += "Examples:\n"
                    for basename, content in skill.examples.items():
                        convention_prompt += f"=== Example File: {basename} ===\n{content}\n"
                logger.info(f"Loaded engineering skill '{skill.name}' for generation context.")

    # Names of every skill-gap candidate (Stage 1.2 goal-text-derived, Stage 2B
    # design-derived) that generation proceeds WITHOUT ever having been backed
    # by verified or newly-extracted information - a human declined/never asked,
    # live lookup found nothing usable and no fallback existed, or extraction
    # itself came up empty. Found live as a real, previously-invisible gap: skill-
    # gap detection and live-lookup resolution both genuinely work, but nothing
    # downstream ever connected a resulting run (pass OR fail) back to "this
    # proceeded on an unresolved knowledge gap" - a fresh-install user with no
    # accumulated skills gets no signal that a shaky success or failure might
    # trace back to this, distinct from and complementary to failure_category
    # (which only covers the retry loop's own failure modes, not this one).
    unresolved_skill_gap_names: List[str] = []

    # 1.2. Skill Gap Detection & Interactive Resolution. Compile/test/run-verification
    # passing can't tell you a skill's CONTENT was wrong to begin with - only whether
    # it was ever proven right (a passing Runtime Verification Gate run, or a human
    # explicitly promoting a rule into it via `kriya skills promote`). Ask at most
    # once per skill per gap - `verification_gap_acknowledged` remembers a decline so
    # future runs don't keep re-asking about a skill the user already said is fine.
    unverified_relevant = [
        s for s in se.list_skills()
        if s.name in active_skills and not se.is_effectively_verified(s) and not s.verification_gap_acknowledged
    ]

    # Also detect goal-mentioned technologies with NO matching skill at all - the
    # check above only fires for a skill that already exists and got matched;
    # something genuinely new to Kriya is otherwise invisible to it.
    missing_skill_candidates: List[str] = []
    try:
        known_terms = set()
        for s in se.list_skills():
            known_terms.add(s.name.lower())
            known_terms.update(t.lower() for t in s.tags)
        for lib, _ver in extract_library_versions(goal):
            lib_lower = lib.lower()
            if not any(lib_lower in term or term in lib_lower for term in known_terms):
                missing_skill_candidates.append(lib)
    except Exception as ex:
        logger.debug(f"Failed to scan for missing-skill candidates: {ex}")

    if (unverified_relevant or missing_skill_candidates) and not skill_gap_callback:
        # No callback wired at all (e.g. `fix`, which doesn't wire one) - a real
        # gap exists but was never even offered a chance to resolve.
        unresolved_skill_gap_names.extend(s.name for s in unverified_relevant)
        unresolved_skill_gap_names.extend(missing_skill_candidates)

    if (unverified_relevant or missing_skill_candidates) and skill_gap_callback:
        reason_parts = []
        if unverified_relevant:
            reason_parts.append(
                f"unverified skill(s) relevant to this goal: {', '.join(s.name for s in unverified_relevant)} "
                "(never had a passing Runtime Verification Gate run, and no rule in them has been human-promoted)"
            )
        if missing_skill_candidates:
            reason_parts.append(f"no skill exists yet for: {', '.join(missing_skill_candidates)}")
        gap_reason = (
            "Kriya doesn't have verified information for: " + "; ".join(reason_parts) +
            ". Provide a URL, file path, or paste reference content to strengthen it, "
            "or decline to proceed with best-effort generation."
        )
        # Try to auto-resolve via live lookup first, before ever asking a human to
        # paste a URL. Query terms here are ALWAYS just the bare skill/library name
        # strings already computed above by code (unverified_relevant skill names,
        # missing_skill_candidates library names) - never free LLM text, never
        # goal/design content - the hard boundary that keeps proprietary project
        # content out of any outbound search query. Off unless a project explicitly
        # opts in via autonomy.web_lookup_enabled AND configures search.base_url.
        # Extraction runs immediately here (not deferred to a shared loop below) so
        # a term only counts as "resolved" - and only gets excluded from the
        # human-ask path further down - if live lookup actually found something
        # USABLE, not merely because a search/fetch call technically succeeded. A
        # term live lookup tried and came up empty on falls through to the normal
        # human-ask path exactly as if lookup had never run at all.
        auto_resolutions: List[Tuple[Any, Dict[str, Any], str]] = []
        if web_lookup_allowed(deps.config):
            lookup_terms = [s.name for s in unverified_relevant] + missing_skill_candidates
            if await deps.approve_web_lookup(lookup_terms, deps.config.search.base_url, web_lookup_query_callback):
                found = await _resolve_via_web_lookup(
                    lookup_terms, deps.config.search.base_url, deps.config.search.top_k
                )
            else:
                found = []
            proceed = bool(found)
            if found and web_lookup_callback:
                try:
                    # a review of externally fetched references: resolved by the approval owner, so an unresolved Task or an unrecognized answer never adopts them
                    proceed = (await resolve_approval_result(web_lookup_callback(found))).approved
                except Exception as ex:
                    logger.warning(f"web_lookup_callback failed, discarding auto-found references: {ex}")
                    proceed = False
            if proceed:
                all_lookup_targets = list(unverified_relevant)
                for item in found:
                    term = item["term"]
                    target = next((s for s in unverified_relevant if s.name == term), None)
                    if not target:
                        try:
                            se.create_skill_skeleton(term)
                            se.discover_and_load()
                            target = se.get_skill(term)
                            all_lookup_targets.append(target)
                        except Exception as ex:
                            logger.warning(f"Failed to bootstrap new skill '{term}' from live lookup: {ex}")
                            continue
                    scoped_gap_reason = _scoped_skill_gap_description(term)
                    extraction = await _extract_first_usable(deps.skill_gap_agent, target, scoped_gap_reason, item["candidates"])
                    lookup_siblings = [s for s in all_lookup_targets if s.name != target.name]
                    extraction = _filter_misattributed_extraction(extraction, target, lookup_siblings)
                    if extraction["rules"] or extraction["examples"] or extraction["conflicts"]:
                        auto_resolutions.append((target, extraction, f"live_lookup:{item['url']}"))
                        logger.info(f"Live lookup found usable information for '{term}'.")
                    else:
                        logger.info(
                            f"Live lookup tried {len(item['candidates'])} reference(s) for '{term}' but none "
                            "contained anything usable - falling back to asking for a better source."
                        )

        resolved_names = {t.name for t, _, _ in auto_resolutions}
        remaining_unverified = [s for s in unverified_relevant if s.name not in resolved_names]
        remaining_missing = [m for m in missing_skill_candidates if m not in resolved_names]

        supplied = None
        if remaining_unverified or remaining_missing:
            try:
                supplied = await await_if_awaitable(skill_gap_callback(gap_reason, [s.name for s in remaining_unverified] + remaining_missing))  # supplied CONTENT, not a decision
            except Exception as ex:
                logger.warning(f"skill_gap_callback failed, proceeding without it: {ex}")
                supplied = None

        reference_text: Optional[str] = None
        manual_source = "human_text"
        if supplied:
            if supplied.startswith("http://") or supplied.startswith("https://"):
                if deps.config.autonomy.egress_policy == "local_only":
                    logger.warning(
                        f"Refusing to fetch external URL '{supplied}' for skill-gap resolution under "
                        "local_only egress policy. Supply a file path or pasted text instead."
                    )
                else:
                    try:
                        reference_text = await fetch_url_text(supplied)
                        manual_source = f"human_url:{supplied}"
                    except Exception as ex:
                        logger.warning(f"Failed to fetch skill-gap reference URL '{supplied}': {ex}")
            elif os.path.isfile(supplied):
                try:
                    with open(supplied, "r", encoding="utf-8", errors="replace") as fh:
                        reference_text = fh.read()
                    manual_source = f"human_file:{supplied}"
                except Exception as ex:
                    logger.warning(f"Failed to read skill-gap reference file '{supplied}': {ex}")
            else:
                reference_text = supplied

        manual_resolutions: List[Tuple[Any, Dict[str, Any], str]] = []
        if reference_text:
            target_skills = list(remaining_unverified)
            if not target_skills and remaining_missing:
                new_name = remaining_missing[0]
                try:
                    se.create_skill_skeleton(new_name)
                    se.discover_and_load()
                    target_skills = [se.get_skill(new_name)]
                except Exception as ex:
                    logger.warning(f"Failed to bootstrap new skill '{new_name}': {ex}")
            for t in target_skills:
                scoped_gap_reason = _scoped_skill_gap_description(t.name)
                extraction = await _extract_first_usable(deps.skill_gap_agent, t, scoped_gap_reason, [{"text": reference_text}])
                siblings = [s for s in target_skills if s.name != t.name]
                extraction = _filter_misattributed_extraction(extraction, t, siblings)
                manual_resolutions.append((t, extraction, manual_source))
        else:
            for s in remaining_unverified:
                se.mark_gap_acknowledged(s.name)
            unresolved_skill_gap_names.extend(s.name for s in remaining_unverified)
            unresolved_skill_gap_names.extend(remaining_missing)

        for target, extraction, source in auto_resolutions + manual_resolutions:
            if extraction["conflicts"]:
                _stage_skill_conflicts(target, extraction["conflicts"])
                logger.info(f"Flagged {len(extraction['conflicts'])} conflicting candidate rule(s) for skill '{target.name}' for human review.")
            if extraction["rules"] or extraction["examples"]:
                _write_skill_extraction(target, extraction, source=source)
                # Fold the newly ingested content into THIS run's context
                # immediately, not just future runs - labeled unverified since it
                # was just extracted and hasn't been through Runtime Verification.
                if extraction["rules"]:
                    # Sanitized the same way _write_skill_extraction() sanitizes before
                    # writing to rules.txt (embedded newlines/whitespace runs collapsed) -
                    # not just cosmetic. Section 1.3's conflict-resolution removal step
                    # does a plain convention_prompt.replace(f"- {rule}\n", ...) against
                    # skill.rules loaded fresh from disk (post-sanitize) after this run's
                    # se.discover_and_load() reload; embedding the RAW extraction text here
                    # would silently desync the two, letting the string-replace no-op and
                    # leaving a "resolved" losing rule still visible to Planner/Architect
                    # (2026-08-15 SME review, stage 5, Finding 2 - flagged by independent
                    # review as a gap this same-run reload newly makes reachable).
                    convention_prompt += (
                        f"\n\n=== Engineering Skill Conventions: {target.name} (just added, unverified - "
                        "use with appropriate caution) ===\n"
                        "Unverified Rules:\n" + "\n".join(
                            f"- {_sanitize_for_flat_file_line(r)}" for r in extraction["rules"]
                        ) + "\n"
                    )
                for basename, content in extraction["examples"].items():
                    convention_prompt += f"=== Example File: {basename} ===\n{content}\n"
                if target.name not in active_skills:
                    active_skills.append(target.name)
                logger.info(f"Strengthened skill '{target.name}' with {len(extraction['rules'])} new rule(s) and {len(extraction['examples'])} example(s) from supplied reference.")
            else:
                logger.info(f"Supplied reference material didn't contain anything usable for skill '{target.name}'.")
                unresolved_skill_gap_names.append(target.name)

    # 1.3. Skill-to-Skill Conflict Detection & Resolution. Two independently
    # correct skills can still conflict when both are active in the same run (e.g.
    # two broker skills each pinning a different value for what must be a single
    # shared setting) - checked here, after active_skills is finalized (including
    # anything the gap-detection step above just bootstrapped), so the comparison
    # always sees the actual skill set this run will use. A previously-resolved
    # pair is applied silently from the registry; a new one asks once and the
    # answer is remembered for future runs.
    #
    # Reload from disk first - same staleness the later Runtime-Verification rule
    # snapshot already guards against (see its own reload + comment further down):
    # 1.2's extraction writes (auto_resolutions/manual_resolutions, just above) append
    # directly to rules.txt without refreshing SkillEngine's in-memory cache, so
    # se.get_skill(...) below could otherwise still return the pre-extraction (often
    # empty) rule list for a skill resolved moments ago in this very run - silently
    # skipping the exact contradiction this check exists to catch (2026-08-15 SME
    # review, stage 5, Finding 2).
    if skill_conflict_callback and len(active_skills) >= 2:
        se.discover_and_load()
        sorted_active = sorted(set(active_skills))
        for idx_a in range(len(sorted_active)):
            for idx_b in range(idx_a + 1, len(sorted_active)):
                name_a, name_b = sorted_active[idx_a], sorted_active[idx_b]
                try:
                    skill_a = se.get_skill(name_a)
                    skill_b = se.get_skill(name_b)
                except KeyError:
                    continue
                if not skill_a.rules or not skill_b.rules:
                    continue

                try:
                    conflicts = await deps.skill_gap_agent.check_skill_conflicts(
                        name_a, skill_a.rules, name_b, skill_b.rules
                    )
                except Exception as ex:
                    logger.warning(f"Skill conflict check failed for '{name_a}' vs '{name_b}': {ex}")
                    continue

                for conflict in conflicts:
                    rule_a, rule_b = conflict["rule_a"], conflict["rule_b"]
                    resolution = find_conflict_resolution(skills_dir, name_a, rule_a, name_b, rule_b)
                    if resolution is None:
                        try:
                            raw = await await_if_awaitable(skill_conflict_callback(name_a, rule_a, name_b, rule_b, conflict.get("explanation", "")))
                        except Exception as ex:
                            logger.warning(f"skill_conflict_callback failed, proceeding without resolving: {ex}")
                            raw = None
                        if raw in ("prefer_a", "prefer_b", "both_ok"):
                            resolution = raw
                            record_conflict_resolution(skills_dir, name_a, rule_a, name_b, rule_b, resolution)
                        else:
                            # No explicit human decision (e.g. -y, or a callback
                            # failure) - proceed without excluding either rule for
                            # THIS run only; don't persist a non-decision.
                            resolution = "both_ok"

                    if resolution == "prefer_a":
                        convention_prompt = convention_prompt.replace(f"- {rule_b}\n", "")
                        logger.info(f"Skill conflict resolved: '{name_a}' rule takes precedence over '{name_b}' for this run.")
                    elif resolution == "prefer_b":
                        convention_prompt = convention_prompt.replace(f"- {rule_a}\n", "")
                        logger.info(f"Skill conflict resolved: '{name_b}' rule takes precedence over '{name_a}' for this run.")

    # 1.5. Graph RAG Context Retrieval
    matched_files = []
    related_files = []
    graph_rag_context = ""
    graph_retrieval_result: Optional[Any] = None
    # The Planner request's own capacity (its binding, planner_max_tokens),
    # resolved once and only if a Planner request is sized at all.
    planner_capacity_cache: List[Any] = []

    def planner_capacity() -> Any:
        if not planner_capacity_cache:
            planner_capacity_cache.append(agent_request_capacity(deps.config, deps.planner, "planner"))
        return planner_capacity_cache[0]

    # CTX-001 P1 C2 production integration: path -> candidate member/
    # class NAMES parsed from this run's own vector hits, before
    # workflow.py's own file-level collapse below - see
    # AttemptContext.retrieval_member_hints' own docstring (attempt.py)
    # for why these are candidates only, validated later, never trusted
    # here.
    retrieval_member_hints: Dict[str, List[str]] = {}
    # CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001: path -> digest-bound
    # declaration lines of the structural localization candidates.
    retrieval_symbol_loci: Dict[str, List[Tuple[int, str]]] = {}
    # PRE-PLAN GROUNDING (2026-09-19): declared here (not only inside the
    # try block below) so both degrade safely to {} - never populated,
    # never referenced as fact - whenever Graph RAG retrieval itself is
    # unavailable/fails, exactly like every other variable in this stage.
    verified_grounding: Dict[str, List[str]] = {}
    hypothesis_candidates: Dict[str, List[str]] = {}
    try:
        vector_index_path = os.path.join(deps.config.paths.memory, "vector_index.db")
        db_path = os.path.join(deps.config.paths.memory, "dependency_graph.db")
            
        if os.path.exists(vector_index_path):
            embed_client = configured_client(deps.config)
            vector_store = LocalVectorStore(vector_index_path)
                
            # MA2.6 - retrieval_limits_for(control.process_profile.context_depth)
            # replaces the hardcoded top_k=5/max_hops=2 below ONLY when both
            # process_profiles.enabled and enforce_context_depth are explicitly
            # on (same opt-in gating as MA2.5's approval clause) - otherwise
            # NARROW's own values (kriya/workflow/context_budget.py) are
            # IDENTICAL to what these calls have always hardcoded, so an
            # unconfigured project sees zero behavior change either way.
            retrieval_limits = RetrievalLimits(top_k=5, max_hops=2, max_neighborhood_results=30)
            if (
                control is not None
                and deps.config.process_profiles.enabled
                and deps.config.process_profiles.enforce_context_depth
            ):
                retrieval_limits = retrieval_limits_for(control.process_profile.context_depth)

            # PRD-027: the retrieval itself lives in graph_retrieval.py so the
            # context-recall certification suite measures this exact code path.
            try:
                retrieval = await retrieve_graph_context(
                    goal, workspace_path,
                    embed_client=embed_client, vector_store=vector_store,
                    dependency_graph_path=db_path, limits=retrieval_limits,
                    embedding_model=deps.config.embedding.model,
                    deadline=run_deadline(deps.config),
                    # The graph pool, never more than the Planner's own request
                    # has room for beside its system prompt and the text known
                    # so far (PROMPT-BUDGET-FIT-001A; the rest is fitted when
                    # the request is complete).
                    budget_limit=lambda: min(
                        _reserve_graph_context_budget(allocation_window(deps.config), convention_prompt),
                        planner_capacity().allocator_units(planner_capacity().room(
                            deps.planner.system_prompt, goal, repo_context, convention_prompt)),
                    ),
                )
            finally:
                # RESOURCE-SQLITE-CLOSE-001: used for this one call only.
                vector_store.close()
            graph_retrieval_result = retrieval
            if retrieval.semantic_unavailable:
                # EMBEDDING-CONTRACT-001: a run that retrieved without its
                # semantic leg says so - never a silent lexical-only run.
                state.record_event(RunEvent(
                    kind="retrieval.semantic_unavailable", attempt=0, source="graph_retrieval",
                    authority=EventAuthority.ADVISORY,
                    message=f"semantic retrieval unavailable ({retrieval.semantic_unavailable}); "
                            "lexical and graph retrieval only",
                    details={"reason_code": retrieval.semantic_unavailable},
                ))
            # CI-6: at most one schema-constrained model call, and only
            # when deterministic localization is ambiguous (calibrated
            # separation); its choice can only reorder current candidates.
            if retrieval.localization_source == "code_intelligence" and retrieval.localization:
                decision = await decide_localization(
                    deps.llm, goal, retrieval.localization, retrieval.separation,
                    current=lambda symbol_id: symbol_id in retrieval.current_member_ids or any(
                        c.symbol_id == symbol_id and c.kind not in CALLABLE_KINDS
                        for c in retrieval.localization),
                )
                if decision.decision is not None:
                    retrieval.adopt_decision(decision.decision.target_symbol_ids)
                state.record_event(RunEvent(
                    kind="localization.decision", attempt=0, source="graph_retrieval",
                    authority=EventAuthority.ADVISORY,
                    message=f"localization {decision.reason_code}"
                            f"{' (model consulted)' if decision.called else ''}",
                    details={**decision.to_dict(), "separation": retrieval.separation},
                ))
            # Code Intelligence R1 slice 2: which candidate generation
            # answered, its ranked candidates (with channels) and what
            # only the legacy leg contributed - the evidence the legacy
            # path's removal criterion reads.
            state.record_event(RunEvent(
                kind="retrieval.code_intelligence", attempt=0, source="graph_retrieval",
                authority=EventAuthority.ADVISORY,
                message=f"localization by {retrieval.localization_source}: "
                        f"{len(retrieval.localization)} candidate(s), "
                        f"{len(retrieval.legacy_only_files)} legacy-only file(s)",
                details={
                    "source": retrieval.localization_source,
                    "candidates": [candidate.to_dict() for candidate in retrieval.localization],
                    "legacy_only_files": list(retrieval.legacy_only_files),
                    "separation": retrieval.separation,
                },
            ))
            retrieved_chunks.extend(retrieval.retrieved_chunks)
            retrieval_member_hints = retrieval.retrieval_member_hints
            retrieval_symbol_loci = retrieval.retrieval_symbol_loci
            verified_grounding = retrieval.verified_grounding
            hypothesis_candidates = retrieval.hypothesis_candidates
            if retrieval.matched:
                matched_files = retrieval.matched_files
                related_files = retrieval.related_files
                graph_rag_context = retrieval.graph_rag_context
                # PRD027-PRECISION-001: which hits seeded the graph walk,
                # and why (a disagreement of the two legs seeds none).
                state.record_event(RunEvent(
                    kind="retrieval.expansion_seeds", attempt=0, source="graph_retrieval",
                    authority=EventAuthority.ADVISORY,
                    message=f"{retrieval.expansion_seed_reason}: {len(retrieval.expansion_seed_files)} "
                            f"of {len(retrieval.matched_files)} matched files seeded graph expansion",
                    details={
                        "reason_code": retrieval.expansion_seed_reason,
                        "seed_files": list(retrieval.expansion_seed_files),
                        "matched_files": list(retrieval.matched_files),
                    },
                ))
    except InferenceDeadlineError:
        raise  # the run's own deadline, not a retrieval failure
    except Exception as ex:
        logger.warning(f"Failed to query Graph RAG: {ex}")
            
    skills_prompt = convention_prompt
    if graph_rag_context:
        convention_prompt = skills_prompt + graph_rag_context
    else:
        convention_prompt = skills_prompt
            
    # 1.6. Learned knowledge (KNOWLEDGE-READPATH-001): retrieved once per
    # user intent by the caller, from the user's own words
    # (kriya/memory/learned_knowledge.py), and handed in as
    # reference_context - never retrieved here from `goal`, which inside a
    # unit is Planner/MilestonePlanner text. AUTH-GOAL-CONTAMINATION-001:
    # it is model context only, fenced as untrusted; `goal` (the authority
    # for requirements, scope, contracts and exits) never sees it. It
    # travels in the learned-reference slot, so the Developer reads it too
    # (budgeted by allocate_context_budget), not only Planner/Architect.
    learned_rag_context = fence_untrusted_reference(reference_context)
    convention_prompt += learned_rag_context
    # What the later stages and the engine read.
    ctx.active_skills, ctx.retrieved_chunks = active_skills, retrieved_chunks
    ctx.skill_staleness_warnings, ctx.repo_model = skill_staleness_warnings, repo_model
    ctx.repo_context, ctx.repo_slug, ctx.se, ctx.skills_dir = repo_context, repo_slug, se, skills_dir
    ctx.convention_prompt, ctx.unresolved_skill_gap_names = convention_prompt, unresolved_skill_gap_names
    ctx.unverified_relevant, ctx.missing_skill_candidates = unverified_relevant, missing_skill_candidates
    ctx.matched_files, ctx.related_files, ctx.graph_rag_context = matched_files, related_files, graph_rag_context
    ctx.graph_retrieval_result, ctx.retrieval_member_hints = graph_retrieval_result, retrieval_member_hints
    ctx.retrieval_symbol_loci, ctx.verified_grounding = retrieval_symbol_loci, verified_grounding
    ctx.hypothesis_candidates, ctx.skills_prompt = hypothesis_candidates, skills_prompt
    ctx.learned_rag_context = learned_rag_context


def record_active_skills(ctx: StageContext, deps: StageDeps) -> None:
    """The active skills' rule snapshot and manifest (an ``active_skills`` evidence record), after Stage 2B."""
    # The stage's run-local names (StageContext) and injected collaborators (StageDeps), bound under the names
    # the body used inside run_generation_workflow; the body below is that method's code, moved verbatim.
    active_skills, se, skill_engine_override = ctx.active_skills, ctx.se, ctx.skill_engine_override
    state = ctx.state
    logger = deps.logger

    # Snapshot each active skill's rule set now, before the Developer retry loop -
    # this is what "this run's active context" actually contains (all extraction
    # is done by this point). If a Runtime Verification run later passes, only
    # these specific rule texts get marked verified per-skill, not whatever
    # rules.txt happens to contain by the time verification finishes.
    # Reload from disk first - extraction writes (Stage 1.2/2B) append directly to
    # rules.txt without refreshing SkillEngine's in-memory cache for skills that
    # already existed (only brand-new skills get an explicit reload when
    # bootstrapped), so the cache could otherwise be missing rules just written.
    if skill_engine_override is None:
        se.discover_and_load()
    active_skill_rules_snapshot: Dict[str, List[str]] = {}
    for active_skill_name in active_skills:
        try:
            active_skill_rules_snapshot[active_skill_name] = list(se.get_skill(active_skill_name).rules)
        except Exception as ex:
            logger.debug(f"Failed to snapshot rules for skill '{active_skill_name}': {ex}")
    try:
        active_skill_manifest = se.manifest_for(active_skills)
    except Exception as ex:
        # Same failure mode as se.get_skill() above (an active_skills entry
        # that isn't actually resolvable, e.g. a bootstrapped skill whose
        # source_path never got set) - manifest_for() calls get_skill()
        # internally with no guard of its own. This manifest is local
        # provenance evidence only (recorded into EvidenceRecord below,
        # never read by any gate), so degrading to an empty list here is
        # strictly safer than letting an unresolvable skill name crash an
        # otherwise-successful run right before the Developer stage,
        # discarding all prior Plan/Design work.
        logger.debug(f"Failed to build active-skill manifest: {ex}")
        active_skill_manifest = []
    state.record_evidence(EvidenceRecord(
        kind="active_skills", source="skill_engine", attempt=0,
        payload={"skills": active_skill_manifest},
    ))
    # What the later stages and the engine read.
    ctx.active_skill_rules_snapshot, ctx.active_skill_manifest = active_skill_rules_snapshot, active_skill_manifest
