"""Stage 2 of a work unit: the Planner (full modularization W4, E3).

Moved verbatim from ``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``): the plan prompt
(goal, repository model, convention prompt, PRD-021 owner candidates, PRD-020 requirements, the skill reminder, the
registered tool catalog, the pre-plan repository grounding), the Planner call (or the predetermined/resumed plan),
the plan checkpoint, the bounded structured-plan repair (PLANNER-ROBUST-001) and the completeness/authority
classification that stops the unit before the Architect (``planner_output_incomplete`` /
``planner_output_schema_invalid`` / ``planner_output_unauthorized_path``). Collaborators arrive in ``StageDeps``
(``kriya/workflow/stage_context.py``); this module never imports ``kriya.workflow.workflow``.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from kriya.agents.contracts import parse_planner_structured_output
from kriya.workflow.file_resolution import classify_plan_completeness
from kriya.workflow.requirements import requirements_prompt_block
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.run_lifecycle import persist_early_exit_trace, stage_run_trace
from kriya.workflow.stage_context import StageContext, StageDeps
from kriya.workflow.subtask_executor import plannable_tool_names
from kriya.workflow.triage import ChangeKind
from kriya.workflow.untrusted_context import outside_untrusted_reference


async def plan_work_unit(ctx: StageContext, deps: StageDeps) -> Optional[Dict[str, Any]]:
    """Stage 2: the plan; the Planner-rejection result when the plan is not complete, else None."""
    # The stage's run-local names (StageContext) and injected collaborators (StageDeps), bound under the names
    # the body used inside run_generation_workflow; the body below is that method's code, moved verbatim.
    goal, workspace_path, state, repo_context = ctx.goal, ctx.workspace_path, ctx.state, ctx.repo_context
    convention_prompt, engineering_route = ctx.convention_prompt, ctx.engineering_route
    predetermined_plan, requirement_set = ctx.predetermined_plan, ctx.requirement_set
    verified_grounding, hypothesis_candidates = ctx.verified_grounding, ctx.hypothesis_candidates
    resume_state, run_id, stream_callback = ctx.resume_state, ctx.run_id, ctx.stream_callback
    skills_prompt, graph_rag_context = ctx.skills_prompt, ctx.graph_rag_context
    reference_context, graph_retrieval_result = ctx.reference_context, ctx.graph_retrieval_result
    step_callback, trace_id = ctx.step_callback, ctx.trace_id
    _save_stage_checkpoint, _record_requirement_lineage = ctx.save_stage_checkpoint, ctx.record_requirement_lineage
    logger, grounded_owner_candidates = deps.logger, deps.grounded_owner_candidates
    owner_candidates_prompt_block, _log_phase_banner = deps.owner_candidates_prompt_block, deps._log_phase_banner
    fit_planner_request, candidate_model = deps.fit_planner_request, deps.candidate_model
    CandidatePrompts, planner_response_model = deps.CandidatePrompts, deps.planner_response_model
    record_planner_outcome = deps.record_planner_outcome
    planner_outcome_for_completeness = deps.planner_outcome_for_completeness
    STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS = deps.STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
    classify_structured_plan_parse_issue = deps.classify_structured_plan_parse_issue
    build_structured_plan_repair_prompt = deps.build_structured_plan_repair_prompt

    # 2. Plan
    plan_head = f"Goal: {goal}\n\nWorkspace Context:\n{repo_context}"
    if state.error_context:
        plan_head = f"Fix the following compile/test error:\n{state.error_context}\n\n" + plan_head
    plan_prompt = plan_head + convention_prompt
    # PRD-021: on a brownfield change (the routes the deterministic owner
    # rules apply to), existing files whose responsibility the goal names
    # are shown before planning - a suspicion, never a rule.
    owner_candidates = []
    if (
        engineering_route is not None
        and engineering_route.kind in (ChangeKind.TASK, ChangeKind.ENHANCEMENT)
        and predetermined_plan is None
    ):
        try:
            owner_candidates = await asyncio.to_thread(grounded_owner_candidates, workspace_path, goal)
        except Exception as exc:
            logger.warning(f"Grounded owner candidates unavailable: {exc}")
    owner_block = owner_candidates_prompt_block(
        owner_candidates, where="as a key of the JSON file-list block")
    if owner_block:
        plan_prompt += "\n\n" + owner_block
    ownership_findings = []
    if requirement_set is not None:
        plan_prompt += "\n\n" + requirements_prompt_block(requirement_set, instruction=(
            "Mark each plan step with the REQ ids it serves, e.g. (REQ-2). Never drop, merge or "
            "reword a requirement; one you cannot plan for stays open, it is not removed."
        ))
    # SME review finding 2 (2026-08-15): Planner receives the identical
    # convention_prompt content Architect/Developer do, but - unlike
    # Developer's own skill_reminder in _fill_missing_content() - nothing
    # told it to actually use that content. "Passive reference material
    # is never enough - the model needs explicit checklists/instructions"
    # is an already-documented durable lesson in this codebase; this is
    # the same fix, same conditional-on-actually-present-content shape,
    # applied to the one stage that was missing it.
    if "Engineering Skill Conventions" in outside_untrusted_reference(convention_prompt):
        plan_prompt += (
            "\nReminder: apply the Engineering Skill Conventions above when drafting this "
            "plan - they document specific mistakes already confirmed to happen for this "
            "exact stack. Your plan must not contradict any Rule listed there."
        )

    # PLANNER-ROBUST-001 (2026-09-19): the same real, already-resolved
    # tool-registry source WorkflowController's own structured-planning
    # loop already uses (kernel.registry.list_components("tool")) -
    # reused as-is, never a hardcoded duplicate list, never a name this
    # runtime doesn't actually have registered. Surfaced to the Planner
    # here (never invented in the system prompt itself, which has no
    # per-run context) so a subtask that genuinely needs execution_
    # method="tool" can name a real Subtask.tool_name instead of
    # guessing - see kriya/agents/agent.py::PlannerAgent.system_prompt
    # for the schema-level distinction this pairs with (Subtask.
    # tool_name vs. a verification[] entry's own, differently-
    # vocabularied tool_name).
    # S18 (OI-127): the Planner is shown only the tools the one planned-tool governance resolution admits
    # (subtask_executor.plannable_tool_names; a registry that cannot be listed admits none); the completeness checks
    # below validate against the SAME snapshot.
    tool_catalogue = plannable_tool_names(deps.registry, deps.config)
    available_tool_names = list(tool_catalogue.registered)
    planned_tool_names = list(tool_catalogue.plannable)
    if planned_tool_names:
        plan_prompt += (
            "\n\nRegistered Kriya tools available for a subtask's own execution_method=\"tool\" "
            f"(Subtask.tool_name must be exactly one of these, never invented): "
            f"{', '.join(planned_tool_names)}."
        )

    # PRE-PLAN GROUNDING (2026-09-19, VAL-001 G1 follow-up): the real,
    # live incident this closes - the Planner named a specific function
    # (`_csharp_walk_invocation_expression`) that never existed anywhere
    # in the corpus, then everything downstream (Architect, Developer)
    # treated that fabrication as fact for 8 attempts before it was
    # caught. verified_grounding/hypothesis_candidates (built above,
    # same retrieval pass, reusing member_boundaries_for/member_ids_
    # matching_name - no new resolver) are rendered as two EXPLICITLY
    # separate, labeled sections, never merged into one "here is the
    # code" blob: the Planner may treat only the first as fact.
    if verified_grounding or hypothesis_candidates:
        grounding_block = "\n\n=== Repository Grounding (from retrieval, before this plan) ===\n"
        if verified_grounding:
            grounding_block += (
                "VERIFIED (confirmed to exist right now, by name, in the current repository "
                "- safe to reference by this exact name):\n"
            )
            for path, member_ids in verified_grounding.items():
                grounding_block += f"  {path}: {', '.join(sorted(member_ids))}\n"
        if hypothesis_candidates:
            grounding_block += (
                "UNCONFIRMED CANDIDATES (retrieval matched this file for the goal, but this "
                "exact name could not be confirmed against current repository structure - "
                "stale, ambiguous, or an unsupported language for name verification; treat as "
                "a lead to investigate, never as a fact):\n"
            )
            for path, names in hypothesis_candidates.items():
                grounding_block += f"  {path}: {', '.join(sorted(names))}\n"
        grounding_block += (
            "Planning specificity must not exceed repository evidence specificity: only name a "
            "specific function/method/class in your plan if it appears in the VERIFIED list "
            "above. For anything else - including every UNCONFIRMED CANDIDATE - describe the "
            "target descriptively (e.g. \"the C# call-site handler\") rather than inventing a "
            "specific name; Kriya will investigate further before mutating."
        )
        plan_prompt += grounding_block

    # MODEL-EVIDENCE-HARDENING-001: set only when this run's Planner
    # produced the plan (never for a predetermined or resumed one), so
    # only a real response's outcome reaches the role metrics.
    plan_response_model: Optional[str] = None
    if predetermined_plan is not None:
        plan = predetermined_plan
        logger.info("Using predetermined plan (bounded subtask execution) - skipping Planner Agent call.")
    elif resume_state and resume_state.get("plan"):
        plan = resume_state["plan"]
        logger.info(f"Resuming checkpoint '{run_id}': using saved Plan, skipping Planner Agent call.")
    else:
        _log_phase_banner("PLANNING")
        logger.info("Planner Agent drafting execution steps...")
        plan_stream = (lambda token: stream_callback("Planning", token)) if stream_callback else None
        # PROMPT-BUDGET-FIT-001A: the graph context and the reference get
        # the room this request has left after its system prompt and
        # every mandatory section (convention_prompt is skills + graph +
        # fenced reference, in that order) - for each role candidate's
        # own request (PROMPT-FIT-ROLE-CHAIN-001).
        plan_suffix = plan_prompt[len(plan_head) + len(convention_prompt):]

        def planner_prompt_for(capacity: Any, candidate: Any) -> str:
            fitted, plan_fit = fit_planner_request(
                capacity, system_prompt=deps.planner.system_prompt, head=plan_head,
                skills_prompt=skills_prompt, graph_context=graph_rag_context, reference=reference_context,
                suffix=plan_suffix,
                rebuild_graph=lambda budget: graph_retrieval_result.rebuild_context(workspace_path, budget),
            )
            if plan_fit:
                state.record_event(RunEvent(
                    kind="context.request_fit", attempt=0, source="workflow",
                    authority=EventAuthority.ADVISORY,
                    message="Planner request: graph context or reference text reduced to fit the request",
                    details={**plan_fit, "model": candidate_model(deps.config, candidate)},
                ))
            return fitted

        planner_prompts = CandidatePrompts(deps.config, deps.planner, "planner", planner_prompt_for)
        _planner_started = time.monotonic()
        plan = await deps.planner.run(
            planner_prompts.first(),
            stream_callback=plan_stream,
            candidate_prompt=planner_prompts,
        )
        plan_response_model = planner_response_model(deps.planner)
        # R1 Deliverable 5 - observational only, same posture as
        # attempt.py's Developer-call wrapper: read after the await
        # already returned, never influences plan/control flow.
        state.planner_calls += 1
        state.planner_llm_seconds += time.monotonic() - _planner_started
        _save_stage_checkpoint("plan", plan=plan)

        # MA6.3 Stage A - parse only, never act on the result yet (MA6
        # spec section 40: "do not jump directly to Stage C with a
        # local model"). Kriya still executes off the prose `plan`
        # above unchanged; this purely observes whether/how well the
        # model followed PlannerAgent's new structured-JSON instruction,
        # so that signal exists before any later stage starts relying
        # on it. A missing/malformed block is expected and unlogged at
        # warning level - see parse_planner_structured_output's own
        # "never raises, always degrades cleanly" contract.
        _structured_plan, _structured_plan_issue = parse_planner_structured_output(plan)
        if _structured_plan is not None:
            logger.info(
                f"Planner structured plan (Stage A, observed only): "
                f"{len(_structured_plan.subtasks)} subtask(s), "
                f"{len(_structured_plan.acceptance_criteria)} acceptance criteria."
            )
        else:
            logger.debug(f"Planner structured plan (Stage A) not available: {_structured_plan_issue}")

    # SME review finding 1 (2026-08-15): PlannerAgent had zero output-
    # sanity check of any kind, unlike ArchitectAgent.run_with_file_list()
    # which at least validates its JSON block. Reproduced twice live (a
    # reasoning model can burn its whole token budget on invisible
    # thinking and return empty content, or run out of budget mid-
    # response leaving an unclosed code fence) that a truncated/empty
    # plan would flow silently into Architect with nothing anywhere
    # detecting or explaining why. Same clean-early-return shape already
    # used above for the KnowledgeGuard gap check, not a raised
    # exception - this is a real, expected-to-happen outcome the caller
    # should be able to handle/retry, not a crash.
    #
    # VAL-001 G1-R3 (2026-09-18): classify_plan_completeness() replaces
    # the old bare count("```") % 2 check with structural, evidence-
    # based classification - see that function's own docstring
    # (kriya/workflow/file_resolution.py) for the real live incident and
    # full design rationale. Three distinct fail-closed outcomes now
    # exist, each with its own accurate status/reason - a genuinely
    # complete, schema-valid structured plan is never blocked merely for
    # a cosmetic surrounding-fence mismatch, and an unauthorized/
    # semantically invalid one is never silently waved through just
    # because Stage A is elsewhere advisory-only (this check is NOT
    # Stage A - it is authoritative here, gating whether Architect is
    # ever reached at all). No Planner retry is added here - each
    # branch below returns exactly once, the same single-attempt
    # contract the old check already had.
    #
    # available_tool_names is passed through here too (PLANNER-
    # ROBUST-001 P2/P3, 2026-09-19) - the EXACT SAME snapshot already
    # captured above for the prompt (never a second, independently-
    # timed registry read): the catalog advertised to the Planner and
    # the catalog its response is validated against must always be
    # the same one, or a tool registered/unregistered between the two
    # reads could silently authorize (or reject) something the
    # Planner was never actually shown. This is also what makes the
    # legacy path share WorkflowController's own tool-capability
    # membership semantics (kriya/workflow/planner_validation.py) for
    # the first time - previously this path had no such check at all.
    plan_completeness = classify_plan_completeness(plan, available_tool_names=available_tool_names,
                                                   plannable_tool_names=planned_tool_names)
    if plan_response_model is not None:
        record_planner_outcome(deps.planner, planner_outcome_for_completeness(plan_completeness.classification),
                               model=plan_response_model)
    # PLANNER-ROBUST-001 (2026-09-19): bounded structured-plan repair,
    # reusing WorkflowController's own existing PLAN_REPAIR primitives
    # (kriya/workflow/planner_repair.py) rather than a second,
    # independently-maintained repair mechanism - see that module's own
    # docstring for the full extraction rationale and the live G1
    # incident this closes (a single schema-invalid subtask - e.g. a
    # TOOL-execution-method subtask missing its required tool_name -
    # discarded an otherwise-correct Planner response and terminated
    # the whole run before Architect, with zero repair opportunity).
    #
    # Only ever attempted for "schema_invalid" - a structurally
    # parseable, COMPLETE response whose structured JSON block failed
    # PlannerStructuredOutput's own schema validation. Deliberately
    # NEVER attempted for "unauthorized_path" (an authority/security
    # classification - repairing it would convert a security rejection
    # into an opportunity to resubmit a differently-worded but still-
    # illegitimate plan) or "incomplete_truncated" (a genuinely
    # different failure shape - a truncated/empty response gives a
    # repair prompt nothing real to correct; unlike unauthorized_path,
    # this is not an authority concern, simply not this mechanism's
    # job). Bounded at the SAME STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
    # WorkflowController's own loop already uses - never increased,
    # never a second policy.
    #
    # The legacy path has no ObligationLedger of its own, so
    # must_preserve/validation_evidence/route_kind/extension_candidates/
    # repository_candidates are simply never passed (every one already
    # defaults to None/empty in build_structured_plan_repair_prompt,
    # producing the identical core, reason-code-driven correction text).
    # On success, the repaired response becomes the new `plan` used for
    # Architect below, exactly as the original would have been had it
    # been schema-valid the first time. On repeated failure (or a
    # repaired response that is itself unauthorized_path/still
    # schema_invalid/truncated), execution falls through to the SAME
    # terminal-rejection block immediately below, completely
    # unmodified - a repaired plan is revalidated through the EXACT
    # same classify_plan_completeness() call as any other plan, never a
    # relaxed or bypassed check.
    legacy_plan_repair_attempts = 0
    while (
        plan_completeness.classification == "schema_invalid"
        and legacy_plan_repair_attempts < STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS
    ):
        repair_reason_codes = classify_structured_plan_parse_issue(
            plan_completeness.reason, plan_completeness.reason_codes,
        )
        state.record_event(RunEvent(
            kind="structured_plan_validation", attempt=0, source="workflow.legacy_planner_repair",
            authority=EventAuthority.ADVISORY,
            message=(
                "Legacy Planner output failed structured-plan schema validation "
                f"(repair_attempts_so_far={legacy_plan_repair_attempts})."
            ),
            details={
                "valid": False, "repair_attempts": legacy_plan_repair_attempts,
                "reason_codes": repair_reason_codes,
            },
        ))
        repair_prompt = build_structured_plan_repair_prompt(
            goal, plan,
            [plan_completeness.reason or "structured plan schema validation failed"],
            repair_reason_codes, legacy_plan_repair_attempts + 1,
            available_tool_names=planned_tool_names,
        )
        legacy_plan_repair_attempts += 1
        state.record_event(RunEvent(
            kind="structured_plan_repair_requested", attempt=0, source="workflow.legacy_planner_repair",
            authority=EventAuthority.ADVISORY,
            message=(
                f"Requesting bounded Planner repair (attempt "
                f"{legacy_plan_repair_attempts}/{STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS})."
            ),
            details={"repair_attempt": legacy_plan_repair_attempts, "reason_codes": repair_reason_codes},
        ))
        _repair_started = time.monotonic()
        # No max_tokens_override passed: self.planner already carries
        # max_output_tokens=config.llm.planner_max_tokens from its own
        # construction (WorkflowEngine.__init__, R3 fix) - the exact
        # same Planner-stage budget the initial call above used, never
        # a silent fallback to the general llm.max_tokens default.
        plan = await deps.planner.run(repair_prompt)
        plan_response_model = planner_response_model(deps.planner)
        state.planner_calls += 1
        state.planner_llm_seconds += time.monotonic() - _repair_started
        _save_stage_checkpoint("plan", plan=plan)
        # Same captured available_tool_names snapshot as the initial
        # classification above - a repaired response is revalidated
        # against the identical catalog, never a re-read that could
        # have drifted mid-operation (P5: no weaker validation path
        # for a repaired plan than for the initial one).
        plan_completeness = classify_plan_completeness(plan, available_tool_names=available_tool_names,
                                                   plannable_tool_names=planned_tool_names)
        record_planner_outcome(deps.planner, planner_outcome_for_completeness(plan_completeness.classification),
                               model=plan_response_model)
        state.record_event(RunEvent(
            kind="structured_plan_repair_result", attempt=0, source="workflow.legacy_planner_repair",
            authority=EventAuthority.ADVISORY,
            message=f"Repair attempt {legacy_plan_repair_attempts} result: {plan_completeness.classification}.",
            details={
                "repair_attempt": legacy_plan_repair_attempts,
                "classification": plan_completeness.classification,
                "accepted": plan_completeness.classification == "complete",
            },
        ))
    if plan_completeness.classification != "complete":
        plan_issue = plan_completeness.reason or "plan failed completeness/authority classification"
        status = {
            "unauthorized_path": "planner_output_unauthorized_path",
            "schema_invalid": "planner_output_schema_invalid",
        }.get(plan_completeness.classification, "planner_output_incomplete")
        logger.warning(f"Planner output rejected before Architect ({status}): {plan_issue}")
        if step_callback:
            step_callback(status, plan_issue)
        # The Planner's calls and outcomes survive the rejection (the SHORT row, kriya/workflow/run_lifecycle.py).
        persist_early_exit_trace(deps, state, stage_run_trace(ctx), status=status, failure_category=status)
        return {
            "status": status,
            "reason": plan_issue,
            "plan": plan,
            "goal": goal,
            "workspace_path": workspace_path,
            "run_id": trace_id
        }

    if step_callback:
        step_callback("Plan", plan)
    _record_requirement_lineage("plan", plan)
    # What the later stages and the engine read.
    ctx.plan, ctx.plan_prompt, ctx.owner_candidates = plan, plan_prompt, owner_candidates
    ctx.owner_block, ctx.ownership_findings = owner_block, ownership_findings
    return None
