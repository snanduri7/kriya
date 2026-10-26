"""Stage 0 of a work unit: the KnowledgeGuard check (full modularization W4, E3).

Moved verbatim from ``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``): the guard checks the
goal's libraries against the model's knowledge cutoff and the skills on disk; an unconfirmed gap stops the unit
before any model call (``status: knowledge_gap``, a ``knowledge_gap`` traces.db row with the run's events). The
guard and the full report stay on the ``StageContext`` for the analysis stage (its safety constraints) and the
architecture stage (Stage 2A's post-design scan). Collaborators arrive in ``StageDeps``
(``kriya/workflow/stage_context.py``); this module never imports ``kriya.workflow.workflow``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from kriya.workflow.run_lifecycle import persist_early_exit_trace, stage_run_trace
from kriya.workflow.stage_context import StageContext, StageDeps


async def check_knowledge_guard(ctx: StageContext, deps: StageDeps) -> Optional[Dict[str, Any]]:
    """Stage 0: the KnowledgeGuard check; the ``knowledge_gap`` result when an unconfirmed gap stops the unit, else None."""
    # The stage's run-local names (StageContext) and injected collaborators (StageDeps), bound under the names
    # the body used inside run_generation_workflow; the body below is that method's code, moved verbatim.
    goal, workspace_path, state, step_callback = ctx.goal, ctx.workspace_path, ctx.state, ctx.step_callback
    knowledge_risk_confirmed = ctx.knowledge_risk_confirmed
    resolved_knowledge_coordinates, resume_state = ctx.resolved_knowledge_coordinates, ctx.resume_state
    trace_id, KnowledgeGuard = ctx.trace_id, deps.KnowledgeGuard
    unresolved_knowledge_report = deps.unresolved_knowledge_report

    # 0. KnowledgeGuard Stage 0 Check
    knowledge_config = deps.config.knowledge
    cutoff = deps.config.llm.knowledge_cutoff
    if knowledge_config.training_cutoff != "2023-12-01":
        cutoff = knowledge_config.training_cutoff

    # GIT-MIDRUN-COMMITS-S3-001: while the run's engine shadows the skills root, the guard reads (and scaffolds into) the
    # engine's effective root, never the canonical directory inside the bound candidate repository.
    skills_root = deps.config.paths.skills
    if ctx.skill_engine_override is not None:
        skills_root = ctx.skill_engine_override.effective_skills_dir(skills_root)
    guard = KnowledgeGuard(
        skills_dir=skills_root,
        cutoff_date_str=cutoff,
        offline=knowledge_config.offline_mode,
        memory_dir=deps.config.paths.memory,
        cache_ttl_days=knowledge_config.release_cache_ttl_days,
    )

    all_gap_report = guard.check_goal(goal, workspace_path)
    gap_report = unresolved_knowledge_report(
        all_gap_report, resolved_knowledge_coordinates,
    )
    # A resumed checkpoint proves this gate was already cleared earlier in the
    # same run (goal is drift-checked identical), so it shouldn't re-block here.
    if resume_state:
        knowledge_risk_confirmed = True
    knowledge_gate_confirmed = (
        knowledge_risk_confirmed
        if resolved_knowledge_coordinates is None
        else not gap_report.has_gaps
    )
    if gap_report.has_gaps and not knowledge_gate_confirmed:
        if step_callback:
            step_callback("knowledge_gap", gap_report.format_report())
        # MODEL-EVIDENCE-HARDENING-001: every early exit carries the run's events, including its role metrics (the
        # SHORT row, kriya/workflow/run_lifecycle.py; E5, OI-32).
        persist_early_exit_trace(deps, state, stage_run_trace(ctx), status="knowledge_gap",
                                 failure_category="knowledge_gap")
        return {
            "status": "knowledge_gap",
            "gap_report": gap_report.to_dict(),
            "goal": goal,
            "workspace_path": workspace_path,
            "run_id": trace_id
        }
    # What the later stages and the engine read.
    ctx.knowledge_config, ctx.guard, ctx.all_gap_report = knowledge_config, guard, all_gap_report
    return None
