"""Stage 3 of a work unit: the Architect, Stage 2A and Stage 2B (full modularization W4, E3).

Moved verbatim from ``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``): the design prompt,
the Architect call (or the predetermined/resumed design), the design checkpoint, the Architect file list
(ARCHITECT-FILE-LIST-ESCAPE-FALLBACK-001: an all-escaping list stops the unit, ``architect_file_list_rejected``),
brownfield owner resolution and PRD-021 ownership findings, the MA2.4 post-Architect triage recomputation, Stage
2A (the post-design KnowledgeGuard scan; a rejected new dependency raises) and Stage 2B (design-derived live
lookup and skill bootstrap). Collaborators arrive in ``StageDeps`` (``kriya/workflow/stage_context.py``); this
module never imports ``kriya.workflow.workflow``.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from kriya.agents.contracts import partition_file_list_escapes
from kriya.policy.approval import await_if_awaitable, resolve_approval, resolve_approval_result
from kriya.workflow.file_resolution import (
    _resolve_file_paths_from_design,
    artifact_resolution_scope,
    extract_expected_files,
    identify_redirected_test_obligations,
    planned_new_artifact_paths,
    resolve_planned_artifact_owners,
)
from kriya.workflow.live_lookup import _extract_first_usable, _resolve_via_web_lookup
from kriya.workflow.requirements import requirements_prompt_block
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.run_lifecycle import persist_early_exit_trace, stage_run_trace
from kriya.workflow.stage_context import StageContext, StageDeps
from kriya.workflow.triage import ChangeKind
from kriya.workflow.untrusted_context import outside_untrusted_reference


async def design_work_unit(ctx: StageContext, deps: StageDeps) -> Optional[Dict[str, Any]]:
    """Stage 3: the design and its file list, ownership, triage recomputation, Stage 2A and 2B; the Architect-rejection result when every file-list entry escapes the workspace, else None."""
    # The stage's run-local names (StageContext) and injected collaborators (StageDeps), bound under the names
    # the body used inside run_generation_workflow; the body below is that method's code, moved verbatim.
    goal, workspace_path, state, plan = ctx.goal, ctx.workspace_path, ctx.state, ctx.plan
    repo_context, convention_prompt, requirement_set = ctx.repo_context, ctx.convention_prompt, ctx.requirement_set
    owner_block, owner_candidates = ctx.owner_block, ctx.owner_candidates
    ownership_findings, predetermined_design = ctx.ownership_findings, ctx.predetermined_design
    predetermined_architect_files, resume_state = ctx.predetermined_architect_files, ctx.resume_state
    run_id, stream_callback, skills_prompt = ctx.run_id, ctx.stream_callback, ctx.skills_prompt
    graph_rag_context, reference_context = ctx.graph_rag_context, ctx.reference_context
    graph_retrieval_result, step_callback, trace_id = ctx.graph_retrieval_result, ctx.step_callback, ctx.trace_id
    engineering_route, control, write_scope_mode = ctx.engineering_route, ctx.control, ctx.write_scope_mode
    allowed_write_relpaths, structured_plan = ctx.allowed_write_relpaths, ctx.structured_plan
    current_subtask_id, knowledge_config, guard = ctx.current_subtask_id, ctx.knowledge_config, ctx.guard
    all_gap_report, approval_callback, se = ctx.all_gap_report, ctx.approval_callback, ctx.se
    active_skills, missing_skill_candidates = ctx.active_skills, ctx.missing_skill_candidates
    unverified_relevant, unresolved_skill_gap_names = ctx.unverified_relevant, ctx.unresolved_skill_gap_names
    web_lookup_query_callback, web_lookup_callback = ctx.web_lookup_query_callback, ctx.web_lookup_callback
    skill_gap_callback, _save_stage_checkpoint = ctx.skill_gap_callback, ctx.save_stage_checkpoint
    _record_requirement_lineage = ctx.record_requirement_lineage
    logger, _log_phase_banner, fit_planner_request = deps.logger, deps._log_phase_banner, deps.fit_planner_request
    candidate_model, CandidatePrompts = deps.candidate_model, deps.CandidatePrompts
    include_response_construction_owners = deps.include_response_construction_owners
    find_ownership_findings, settle_findings = deps.find_ownership_findings, deps.settle_findings
    parse_ownership_justifications = deps.parse_ownership_justifications
    findings_prompt_block, ActionRequest = deps.findings_prompt_block, deps.ActionRequest
    ActionType, PolicyDeniedError, PolicyDecision = deps.ActionType, deps.PolicyDeniedError, deps.PolicyDecision
    extract_library_versions, fetch_url_text = deps.extract_library_versions, deps.fetch_url_text
    _stage_skill_conflicts, _write_skill_extraction = deps._stage_skill_conflicts, deps._write_skill_extraction

    # 3. Architect
    design_head = f"Plan:\n{plan}\n\nWorkspace Context:\n{repo_context}"
    design_prompt = design_head + convention_prompt
    if requirement_set is not None:
        # PRD-020: the Architect otherwise sees only the Planner's prose,
        # so a requirement the plan dropped would never reach the design.
        design_prompt += "\n\n" + requirements_prompt_block(requirement_set, instruction=(
            "These are the user's requirements; the plan above does not replace them. Name the "
            "REQ ids each design decision serves, and design for every one of them."
        ))
    if owner_block:
        design_prompt += "\n\n" + owner_block
    # SME review finding (2026-08-15): same gap as Planner's finding 2 -
    # Architect receives the identical convention_prompt content Planner
    # does, but nothing told it to actually use that content either.
    # Same fix, same conditional-on-actually-present-content shape.
    if "Engineering Skill Conventions" in outside_untrusted_reference(convention_prompt):
        design_prompt += (
            "\nReminder: apply the Engineering Skill Conventions above when defining this "
            "design - they document specific mistakes already confirmed to happen for this "
            "exact stack. Your design must not contradict any Rule listed there."
        )
    if predetermined_design is not None:
        design = predetermined_design
        architect_files = predetermined_architect_files
        logger.info("Using predetermined design (bounded subtask execution) - skipping Architect Agent call.")
    elif resume_state and resume_state.get("design"):
        design = resume_state["design"]
        architect_files = resume_state.get("architect_files")
        logger.info(f"Resuming checkpoint '{run_id}': using saved Design, skipping Architect Agent call.")
    else:
        _log_phase_banner("ARCHITECTURE")
        logger.info("Architect Agent defining interface designs...")
        architect_stream = (lambda token: stream_callback("Architect Design", token)) if stream_callback else None
        # ARCHITECT-PROMPT-FIT-001: the same fit as the Planner's request
        # (convention_prompt is skills + graph + fenced reference) - the
        # graph context, then the untrusted reference, get the room the
        # plan, the repository model and every mandatory block leave, for
        # each role candidate's own request.
        design_suffix = design_prompt[len(design_head) + len(convention_prompt):]

        def architect_prompt_for(capacity: Any, candidate: Any) -> str:
            fitted, design_fit = fit_planner_request(
                capacity, system_prompt=deps.architect.system_prompt, head=design_head,
                skills_prompt=skills_prompt, graph_context=graph_rag_context, reference=reference_context,
                suffix=design_suffix, request="architect",
                rebuild_graph=lambda budget: graph_retrieval_result.rebuild_context(workspace_path, budget),
            )
            if design_fit:
                state.record_event(RunEvent(
                    kind="context.request_fit", attempt=0, source="workflow",
                    authority=EventAuthority.ADVISORY,
                    message="Architect request: graph context or reference text reduced to fit the request",
                    details={**design_fit, "model": candidate_model(deps.config, candidate)},
                ))
            return fitted

        architect_prompts = CandidatePrompts(deps.config, deps.architect, "architect", architect_prompt_for)
        _architect_started = time.monotonic()
        design, architect_files = await deps.architect.run_with_file_list(
            architect_prompts.first(),
            stream_callback=architect_stream,
            candidate_prompt=architect_prompts,
        )
        # R1 Deliverable 5 - observational only, same posture as the
        # Planner wrapper immediately above.
        state.architect_calls += 1
        state.architect_llm_seconds += time.monotonic() - _architect_started
        _save_stage_checkpoint("design", plan=plan, design=design, architect_files=architect_files)
    _record_requirement_lineage("design", design)
    # predetermined_architect_files=[] (an EMPTY list, not None) is a
    # deliberate zero-file plan, not a broken one - a bounded subtask
    # execution_role=verification subtask (kriya/workflow/plan_schema.py)
    # legitimately owns no planned_files by construction. Only fall back
    # to heuristic extraction when nothing was actually predetermined
    # (predetermined_architect_files is None): a genuinely missing/
    # malformed Architect file-list block, or an old checkpoint saved
    # before architect_files existed at all. Found live, PRV-05
    # (2026-08-28): before this distinction existed, an intentionally
    # empty predetermined list was indistinguishable from "missing" here
    # (`not []` and `not None` are both True), so a verification-only
    # subtask's empty planned_files would have been silently discarded
    # and replaced with whatever this heuristic happened to regex out of
    # the subtask's own prose description - defeating the whole point of
    # a non-mutating subtask before it even reached the write gate.
    if not architect_files and predetermined_architect_files is None:
        # ARCHITECT-FILE-LIST-ESCAPE-FALLBACK-001: a file list the Architect
        # DID produce, rejected only because entries escape the workspace
        # (../x, an absolute path), is never laundered through the prose
        # heuristic below (which re-derived in-workspace basenames:
        # evil.py from ../outside/evil.py). The escaping entries are
        # dropped and recorded; the in-workspace remainder is the plan. A
        # list whose every entry escapes is a typed plan refusal before
        # any Developer call, the Planner-rejection shape.
        partition = partition_file_list_escapes(design)
        if partition.escaping:
            state.record_event(RunEvent(
                kind="plan.file_list_entries_rejected", attempt=0, source="workflow.architect",
                authority=EventAuthority.AUTHORITATIVE,
                message=(f"{len(partition.escaping)} Architect file-list entries escape the workspace "
                         "and were dropped from the plan"),
                details={"rejected": list(partition.escaping), "kept": list(partition.files or [])},
            ))
        if partition.escaping and not partition.files:
            status = "architect_file_list_rejected"
            plan_issue = f"every Architect file-list entry escapes the workspace: {list(partition.escaping)!r}"
            logger.warning(f"Architect file list rejected ({status}): {plan_issue}")
            if step_callback:
                step_callback(status, plan_issue)
            persist_early_exit_trace(deps, state, stage_run_trace(ctx), status=status, failure_category=status)
            return {
                "status": status, "reason": plan_issue, "plan": plan, "design": design, "goal": goal,
                "workspace_path": workspace_path, "run_id": trace_id,
                "rejected_file_list_entries": list(partition.escaping),
            }
        if partition.files:
            architect_files = partition.files
        else:
            # The Architect's response had no JSON file-list block at all,
            # or one that is malformed for another reason (see
            # ArchitectAgent.run_with_file_list/kriya/agents/contracts.py),
            # or this is an old checkpoint saved before architect_files
            # existed. Fall back to the older heuristic regex extraction
            # rather than fail the run outright over a formatting hiccup
            # in a newer mechanism - kept specifically as this path's
            # safety net, see kriya/agents/contracts.py's module docstring.
            architect_files = _resolve_file_paths_from_design(sorted(extract_expected_files(design)), design)
            logger.warning(
                "Architect file list: structured JSON extraction unavailable - falling back to "
                "heuristic regex extraction over the design's prose."
            )

    # Brownfield ownership outranks creation of a parallel, similarly
    # named artifact. Apply to bounded task/enhancement requests; larger
    # topology changes retain the Architect's explicit file set.
    if (
        engineering_route is not None
        and engineering_route.kind in (ChangeKind.TASK, ChangeKind.ENHANCEMENT)
    ):
        _pre_redirect_architect_files = list(architect_files)
        # FILE-RESOLUTION-SCOPE-ESCAPE-001 (BACKEND-FINAL-CLOSURE-005
        # cohort 2, C2-S4_A): a bounded unit's redirect may never leave
        # its validated write scope (the write authority would refuse
        # the resolver's own target and end the run), and a planned
        # file the validated plan creates is never an "invented
        # duplicate". Both are decided by the resolver from the scope
        # passed HERE, per call - a legacy direct run (no scope) keeps
        # its unrestricted behaviour; a refusal is recorded, never silent.
        _artifact_scope = artifact_resolution_scope(write_scope_mode, allowed_write_relpaths)
        _artifact_resolution = resolve_planned_artifact_owners(
            architect_files, goal, workspace_path, authorized_paths=_artifact_scope,
            new_artifact_paths=planned_new_artifact_paths(structured_plan, current_subtask_id),
        )
        architect_files = _artifact_resolution.resolved
        if _artifact_resolution.new_artifacts_kept:
            state.record_event(RunEvent(
                kind="file_resolution.new_artifact_kept", attempt=0, source="workflow.architect_file_resolution",
                authority=EventAuthority.ADVISORY,
                message="planned new artifact(s) kept as planned (validated action=create): "
                        + ", ".join(_artifact_resolution.new_artifacts_kept),
                details={"paths": list(_artifact_resolution.new_artifacts_kept), "stage": "architect"},
            ))
        for _refusal in _artifact_resolution.refused_redirects:
            state.record_event(RunEvent(
                kind="file_resolution.redirect_refused", attempt=0, source="workflow.architect_file_resolution",
                authority=EventAuthority.AUTHORITATIVE,
                message=(f"{_refusal['reason_code']}: planned '{_refusal['planned']}' resolves to "
                         f"'{_refusal['owner']}' outside this unit's authorized files; the planned path is kept"),
                details={**_refusal, "authorized_paths": list(_artifact_scope or []), "stage": "architect"},
            ))
        # Test-obligation preservation (2026-09-20): captured HERE,
        # before include_response_construction_owners() below can add
        # further entries unrelated to this specific redirect - a
        # planned-but-nonexistent test artifact silently mapped onto an
        # existing owner must not let that redirect alone discharge the
        # acceptance obligation the goal's own test-coverage intent
        # created (see identify_redirected_test_obligations()'s own
        # docstring and kriya/workflow/attempt.py's own consumer for the
        # live incident this closes).
        state.redirected_test_obligations.update(
            identify_redirected_test_obligations(
                _pre_redirect_architect_files, architect_files, workspace_path,
            )
        )
        # Full synchronous tree-walk + per-file read; offload so it
        # doesn't block the event loop inside this async workflow.
        architect_files = await asyncio.to_thread(
            include_response_construction_owners, architect_files, goal, workspace_path,
        )
        # PRD-021: files still new after the deterministic owner rules
        # above, against the grounded candidates shown before planning.
        if owner_candidates:
            new_files = [p for p in architect_files if not os.path.exists(os.path.join(workspace_path, p))]
            design_lines = str(design or "").splitlines()
            ownership_findings = settle_findings(
                find_ownership_findings(
                    [(path, None, "\n".join(line for line in design_lines
                                             if os.path.basename(path) in line))
                     for path in new_files],
                    owner_candidates,
                    touched_paths=[p for p in architect_files if p not in new_files],
                ),
                goal=goal, justifications=parse_ownership_justifications(str(design or "")),
            )
            for finding in ownership_findings:
                state.record_event(RunEvent(
                    kind="ownership.finding", attempt=0, source="workflow.architect_file_resolution",
                    authority=EventAuthority.ADVISORY,
                    message=f"{finding.planned_path} may duplicate {finding.candidate_owner} ({finding.status})",
                    details=finding.to_dict(),
                ))
            state.ownership_findings = list(ownership_findings)
            skills_prompt += "\n\n" + findings_prompt_block(ownership_findings)
    if step_callback:
        step_callback("Design", design)

    # MA2.4 - post-Architect risk recomputation (kriya/workflow/triage.py::
    # EngineeringTriageService.recompute_from_files). architect_files is
    # fully resolved by this point (resumed checkpoint, a fresh Architect
    # call, or the regex-extraction fallback above all converge here) -
    # this is the first point in the pipeline where Kriya knows real
    # touched-file paths instead of MA1's goal-text/known_files estimate.
    # A caught, logged failure never blocks generation - same posture as
    # MA1.3's own classify() call. Still no runtime behavior change as of
    # MA2.4 itself: nothing downstream reads `control` for context/
    # planning/approval/verification decisions yet (MA2.5/MA2.6) - this
    # only updates `control` and `state.engineering_route` so an
    # escalation that happens here is visible in telemetry and available
    # to whichever later MA2 task first reads it.
    if control is not None:
        try:
            recomputed_route = await deps.engineering_triage.recompute_from_files(
                route=control.engineering_route,
                workspace_path=workspace_path,
                planned_files=list(architect_files or []),
            )
            if recomputed_route.max_observed_risk_class > control.engineering_route.max_observed_risk_class:
                revalidation_label = (
                    "post-plan impact revalidation"
                    if predetermined_design is not None else "post-Architect escalation"
                )
                logger.info(
                    f"[Engineering Triage] {revalidation_label}: "
                    f"{control.engineering_route.max_observed_risk_class.name}->"
                    f"{recomputed_route.max_observed_risk_class.name} "
                    f"weight={control.process_profile.execution_weight.value}->"
                    f"{recomputed_route.execution_weight.value} "
                    f"reasons={recomputed_route.reason_codes[len(control.engineering_route.reason_codes):]}"
                )
            control = control.with_route(recomputed_route)
            engineering_route = control.engineering_route
            state.engineering_route = engineering_route
            state.process_profile = control.process_profile
        except Exception as e:
            logger.warning(f"Post-Architect engineering triage recomputation failed, continuing without it: {e}")

    # Stage 2A: Post-architecture dependency scan
    if knowledge_config.check_enabled:
        post_report = guard.check_goal(design, workspace_path)
        initial_libs = {g["library"] for g in all_gap_report.gaps}
        new_gaps = [g for g in post_report.gaps if g["library"] not in initial_libs]

        if new_gaps:
            logger.info(f"Stage 2A: Detected {len(new_gaps)} new library gaps in architect design.")
            # MA4.13 - audit-only today (execution_policy.mode is
            # validated to always be "audit" as of MA4.15 - see
            # kriya/config/config.py::ExecutionPolicyConfig), does not
            # affect the approval_callback decision below. The first
            # real INSTALL_PACKAGE caller besides validate.py's
            # command-shaped detection (MA4.7): this seam already knows
            # the package name directly, so it's constructed here rather
            # than parsed from a command string via
            # extract_install_package_target.
            # MA4.15 - `enforce` is read from config rather than left at
            # _authorize_action's Python-level default, and a genuine
            # DENY is deliberately let through (not swallowed by the
            # broad except below) - it can never actually be raised
            # while the config validator keeps mode pinned to "audit",
            # but a future milestone lifting that restriction shouldn't
            # ALSO have to remember to fix this try/except to stop
            # silently eating a real denial.
            #
            # POL-001: a REQUIRE_APPROVAL verdict here is deliberately
            # NOT re-raised and no approval_callback is passed into
            # _authorize_action for it - _check_package_supply_chain
            # returns REQUIRE_APPROVAL for every non-URL-source package
            # (never a bare ALLOW), so under enforce=True this loop
            # would otherwise ask once per library in new_gaps AND THEN
            # hit the existing single aggregate prompt built from the
            # very same new_gaps a few lines below (`desc`/`reason_str`,
            # "Do you want to proceed with these dependencies?") -
            # doubling (N+1) an approval flow that already covers this
            # exact batch once. DENY (e.g. an install source classified
            # as untrusted) still hard-stops immediately, unconditional
            # on any human answer - that's the real, new protection this
            # gate adds once enforce=True is reachable.
            execution_policy_cfg = deps.config.execution_policy
            if execution_policy_cfg.enabled:
                for g in new_gaps:
                    try:
                        await deps.authorize_action(
                            ActionRequest(
                                action_type=ActionType.INSTALL_PACKAGE, target=g["library"],
                                metadata={"version": g["version"], "risk_level": g["risk_level"]},
                            ),
                            enforce=(execution_policy_cfg.mode == "enforce"),
                        )
                    except PolicyDeniedError as e:
                        if e.result.decision == PolicyDecision.DENY:
                            raise
                        # REQUIRE_APPROVAL: defer to the aggregate
                        # approval_callback prompt below, don't double-ask.
                    except Exception as e:
                        logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)
            desc = "\n".join([
                (
                    f"- {g['library']} (no specific version mentioned) [Risk: {g['risk_level']}]: {g['reason']}"
                    if g['version'] == "unspecified"
                    else f"- {g['library']} (version {g['version']}) [Risk: {g['risk_level']}]: {g['reason']}"
                )
                for g in new_gaps
            ])
            reason_str = (
                f"Knowledge Guard detected new dependency/technology gaps in the proposed architecture:\n{desc}\n"
                f"Do you want to proceed with these dependencies?"
            )
            if approval_callback:
                outcome = await resolve_approval(approval_callback, [], reason_str)
                if outcome.declined:
                    raise ValueError("Workflow aborted: User rejected post-cutoff dependency risk in Stage 2A.")
                if not outcome.approved:  # nobody decided (a launch that cannot ask, an expired review): not approved, and not a rejection either
                    raise ValueError(f"Workflow aborted: approval of the post-cutoff dependency risk could not be obtained in Stage 2A ({outcome.reason_code}).")
            else:
                logger.warning("Stage 2A validation warning: new gaps detected but no approval callback available. Proceeding under default policy.")

    # Stage 2B: Design-derived live lookup. The goal text alone can be vague
    # ("build a message broker app"), but the Architect's design usually names
    # concrete technologies/versions once it makes real decisions - this extends
    # detection past what the pre-Planner skill-gap check (goal text only) could
    # see. Live lookup is tried first (same hard query-safety boundary as Stage 1.2:
    # bare extracted term strings only, never design/goal/code text); if it doesn't
    # find anything usable, this now falls back to asking a human too (same
    # skill_gap_callback as Stage 1.2) rather than silently generating code against
    # a technology Kriya has zero grounding for - deliberately chosen over staying
    # silent, since proceeding ungrounded is more likely to produce something wrong
    # than a single extra confirmation prompt is to annoy. Still silently skips if
    # lookup is disabled entirely, so a project that hasn't opted in sees no
    # behavior change at all.
    if deps.config.autonomy.web_lookup_enabled and deps.config.search.base_url:
        new_design_terms: List[str] = []
        try:
            known_terms = {s.name.lower() for s in se.list_skills()}
            known_terms.update(t.lower() for s in se.list_skills() for t in s.tags)
            known_terms.update(a.lower() for a in active_skills)
            already_considered = {m.lower() for m in missing_skill_candidates}
            already_considered.update(s.name.lower() for s in unverified_relevant)

            for lib, _ver in extract_library_versions(design):
                lib_lower = lib.lower()
                if lib_lower in already_considered:
                    continue
                if any(lib_lower in term or term in lib_lower for term in known_terms):
                    continue
                if lib not in new_design_terms:
                    new_design_terms.append(lib)
        except Exception as ex:
            logger.debug(f"Failed to scan architect design for design-derived lookup candidates: {ex}")

        if new_design_terms:
            if await deps.approve_web_lookup(
                new_design_terms, deps.config.search.base_url, web_lookup_query_callback
            ):
                found = await _resolve_via_web_lookup(
                    new_design_terms, deps.config.search.base_url, deps.config.search.top_k
                )
            else:
                # A declined search must still fall through to the human-ask
                # fallback below, exactly as if lookup had been tried and come
                # up empty - not silently drop this gap-resolution path entirely.
                found = []
            if not found:
                # Pre-existing asymmetry (see Stage 2B's own docstring elsewhere):
                # a search that legitimately finds nothing at all never reaches the
                # human-ask fallback below, unlike Stage 1.2. Not fixed here - out
                # of scope - but these terms genuinely went unaddressed either way,
                # so still worth surfacing in the final report.
                unresolved_skill_gap_names.extend(new_design_terms)
            proceed = bool(found)
            if found and web_lookup_callback:
                try:
                    proceed = (await resolve_approval_result(web_lookup_callback(found))).approved  # a review of externally fetched references (see stage_analysis)
                except Exception as ex:
                    logger.warning(f"web_lookup_callback failed, discarding design-derived references: {ex}")
                    proceed = False
            if proceed:
                for item in found:
                    term = item["term"]
                    try:
                        se.create_skill_skeleton(term)
                        se.discover_and_load()
                        target = se.get_skill(term)
                    except Exception as ex:
                        logger.warning(f"Failed to bootstrap new skill '{term}' from design-derived live lookup: {ex}")
                        continue
                    design_gap_reason = f"The proposed architecture design mentions '{term}', which has no existing Kriya skill."
                    extraction = await _extract_first_usable(
                        deps.skill_gap_agent, target, design_gap_reason, item["candidates"],
                    )
                    source = f"live_lookup:{item['url']}"
                    if not (extraction["rules"] or extraction["examples"] or extraction["conflicts"]) and skill_gap_callback:
                        logger.info(
                            f"Live lookup tried {len(item['candidates'])} reference(s) for design-derived "
                            f"technology '{term}' but none contained anything usable - falling back to asking for a better source."
                        )
                        try:
                            supplied = await await_if_awaitable(skill_gap_callback(
                                design_gap_reason + " Provide a URL, file path, or paste reference content to "
                                "strengthen it, or decline to proceed with best-effort generation.",
                                [term],
                            ))
                        except Exception as ex:
                            logger.warning(f"skill_gap_callback failed for design-derived term '{term}': {ex}")
                            supplied = None

                        reference_text: Optional[str] = None
                        manual_source = "human_text"
                        if supplied:
                            if supplied.startswith("http://") or supplied.startswith("https://"):
                                if deps.config.autonomy.egress_policy == "local_only":
                                    logger.warning(
                                        f"Refusing to fetch external URL '{supplied}' for skill-gap resolution "
                                        "under local_only egress policy. Supply a file path or pasted text instead."
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

                        if reference_text:
                            extraction = await _extract_first_usable(
                                deps.skill_gap_agent, target, design_gap_reason, [{"text": reference_text}],
                            )
                            source = manual_source

                    if extraction["conflicts"]:
                        _stage_skill_conflicts(target, extraction["conflicts"])
                    if extraction["rules"] or extraction["examples"]:
                        _write_skill_extraction(target, extraction, source=source)
                        if extraction["rules"]:
                            skills_prompt += (
                                f"\n\n=== Engineering Skill Conventions: {target.name} (just added, unverified - "
                                "use with appropriate caution) ===\n"
                                "Unverified Rules:\n" + "\n".join(f"- {r}" for r in extraction["rules"]) + "\n"
                            )
                        for basename, content in extraction["examples"].items():
                            skills_prompt += f"=== Example File: {basename} ===\n{content}\n"
                        if target.name not in active_skills:
                            active_skills.append(target.name)
                        logger.info(f"Live lookup bootstrapped new skill '{target.name}' from architect design with {len(extraction['rules'])} rule(s).")
                    else:
                        unresolved_skill_gap_names.append(target.name)
    # What the later stages and the engine read.
    ctx.design, ctx.architect_files, ctx.ownership_findings = design, architect_files, ownership_findings
    ctx.skills_prompt, ctx.control, ctx.engineering_route = skills_prompt, control, engineering_route
    ctx.active_skills, ctx.unresolved_skill_gap_names = active_skills, unresolved_skill_gap_names
    return None
