"""The work-unit runner (full modularization W4, E3): drives a work unit's design stages in their fixed order.

``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``) opens the run (state, resume, trace
identity, verification contract), fills one ``StageContext`` and calls ``run_design_stages``; the stages are

0. ``stage_knowledge_guard.check_knowledge_guard`` - may stop the unit (``knowledge_gap``);
1. ``stage_analysis.analyze_work_unit`` - never stops the unit;
   then the engine's ``open_checkpoints`` hook, at exactly the point the stage checkpoints' workspace/config/goal
   fingerprints were always computed (after any skill bootstrap of stage 1, before the Planner), and whose saver the
   planning and architecture stages use;
2. ``stage_planning.plan_work_unit`` - may stop the unit (a Planner rejection);
3. ``stage_architecture.design_work_unit`` - may stop the unit (``architect_file_list_rejected``) or raise (a
   rejected Stage 2A dependency);
and finally ``stage_analysis.record_active_skills`` (the active-skill snapshot, after Stage 2B's extraction).

A stage's early result is returned unchanged, so the engine returns exactly the dict the inline code returned. The
engine then reads the stage outputs from the ``StageContext`` and runs the Developer loop. This module never imports
``kriya.workflow.workflow``.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from kriya.workflow.stage_analysis import analyze_work_unit, record_active_skills
from kriya.workflow.stage_architecture import design_work_unit
from kriya.workflow.stage_context import StageContext, StageDeps
from kriya.workflow.stage_knowledge_guard import check_knowledge_guard
from kriya.workflow.stage_planning import plan_work_unit


async def run_design_stages(
    ctx: StageContext, deps: StageDeps, *, open_checkpoints: Callable[[], Callable[..., None]],
) -> Optional[Dict[str, Any]]:
    """Stages 0-3 of one work unit; a stage's early result (the unit stops), or None when the unit goes on to the
    Developer loop with every stage output on ``ctx``."""
    stop = await check_knowledge_guard(ctx, deps)
    if stop is not None:
        return stop
    await analyze_work_unit(ctx, deps)
    ctx.save_stage_checkpoint = open_checkpoints()
    stop = await plan_work_unit(ctx, deps)
    if stop is not None:
        return stop
    stop = await design_work_unit(ctx, deps)
    if stop is not None:
        return stop
    record_active_skills(ctx, deps)
    return None


__all__ = ["run_design_stages"]
