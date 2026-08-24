"""WorkflowController - MA6.8/MA6.9 of the MA6 structured-execution
implementation plan. The shell that will eventually own orchestration
(MA6 invariant 10). Every ChangeKind still routes actual generation work
to the SAME place - the existing, untouched run_generation_workflow(),
called via _run_legacy_generation() (MA6.10's own explicit first step:
"WorkflowController -> LegacyGenerationAdapter -> run_generation_workflow(),
then responsibilities extracted gradually") - that does not change here.
Does NOT rewrite run_generation_workflow() (MA6 hard constraint) - the
legacy call is byte-for-byte what every existing caller already makes.

MA6.9 gives the four kinds real, DISTINCT control-plane bookkeeping ahead
of that call, honestly scoped to what's actually available without
touching run_generation_workflow's internals:
  - MILESTONE attaches milestone_group_id/current_milestone_id to
    ControlState from the SAME milestone_group_id/milestone_index kwargs
    run_generation_workflow already accepts (kriya/workflow/milestones.py's
    orchestrator already threads these through every call - this is new
    bookkeeping on the CONTROLLER side, not a new capability on the legacy
    side).
  - REFACTOR captures a real git base_commit/tree_hash baseline (reusing
    kriya/workflow/checkpoint.py's own hash primitives, MA5.9) onto
    ControlState BEFORE generation starts - the concrete anchor
    plan_validation.py's (MA6.2) `refactor_baseline` field exists to be
    checked against, once a real EngineeringPlan reaches this controller.
  - TASK/ENHANCEMENT get no extra bookkeeping in this slice - genuinely
    differentiating their SHAPE (enhancement's capability-context/plan/
    implement/verify sequencing; task's reproduce/plan-fix/verify) requires
    the gradual internal extraction from run_generation_workflow() that
    MA6.10 does in a specific order (context assembly, then planning,
    approval, verification orchestration, architecture/design prep, and
    the Developer attempt loop LAST) - none of which exists yet. Claiming
    real per-kind shape here before that extraction lands would be
    speculative machinery with nothing behind it, not a genuine capability.

Not wired into any CLI/repl command path yet - kriya.config's own
migration-mode gate (legacy/shadow/enforce, `workflow_controller.enabled:
false` by default) is MA6.13's job, mirroring MA4.15/MA5's own "ship the
mechanism, default it off" pattern. This class is importable and
independently testable today, invoked by nothing in the default
`kriya generate` path until that gate exists.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from kriya.control.state import ControlState
from kriya.workflow.checkpoint import compute_base_commit, compute_tree_hash, new_run_id
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_types import WorkflowResult

logger = logging.getLogger(__name__)


class WorkflowController:
    """Wraps an existing WorkflowEngine (kriya/workflow/workflow.py) -
    never constructs its own agents/LLM client, and deliberately reuses
    that engine's OWN `engineering_triage` (EngineeringTriageService)
    instance rather than building a second one, so triage state/kernel
    wiring stays exactly what a real caller already set up."""

    def __init__(self, workflow_engine: Any) -> None:
        self.workflow_engine = workflow_engine

    async def execute(
        self,
        goal: str,
        workspace_path: str,
        *,
        known_files: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        milestone_group_id: Optional[str] = None,
        milestone_index: Optional[int] = None,
        milestone_total: Optional[int] = None,
        **legacy_kwargs: Any,
    ) -> WorkflowResult:
        run_id = run_id or new_run_id()

        route = await self.workflow_engine.engineering_triage.classify(
            goal, workspace_path, known_files=known_files,
        )
        control_context = WorkflowControlContext.for_route(route)
        control_state = ControlState.new(
            run_id=run_id,
            engineering_route=route,
            process_profile=control_context.process_profile,
            milestone_group_id=milestone_group_id,
        )

        if route.kind == ChangeKind.MILESTONE:
            control_state = self._attach_milestone_metadata(
                control_state, milestone_group_id, milestone_index,
            )
        elif route.kind == ChangeKind.REFACTOR:
            control_state = self._attach_refactor_baseline(control_state, workspace_path)
        # TASK/ENHANCEMENT: no extra bookkeeping in this slice - see this
        # module's own docstring for why that's a deliberate scoping
        # decision, not an oversight.

        legacy_result = await self._run_legacy_generation(
            goal, workspace_path,
            milestone_group_id=milestone_group_id, milestone_index=milestone_index,
            milestone_total=milestone_total, **legacy_kwargs,
        )

        return WorkflowResult(run_id=run_id, control_state=control_state, route=route, legacy_result=legacy_result)

    def _attach_milestone_metadata(
        self, control_state: ControlState, milestone_group_id: Optional[str], milestone_index: Optional[int],
    ) -> ControlState:
        if milestone_group_id is None:
            return control_state
        current_milestone_id = (
            f"{milestone_group_id}:{milestone_index}" if milestone_index is not None else milestone_group_id
        )
        return control_state.with_updates(
            milestone_group_id=milestone_group_id, current_milestone_id=current_milestone_id,
        )

    def _attach_refactor_baseline(self, control_state: ControlState, workspace_path: str) -> ControlState:
        base_commit = compute_base_commit(workspace_path)
        tree_hash = compute_tree_hash(workspace_path)
        if base_commit is None and tree_hash is None:
            return control_state
        return control_state.with_updates(base_commit=base_commit, tree_hash=tree_hash)

    async def _run_legacy_generation(self, goal: str, workspace_path: str, **legacy_kwargs: Any) -> Dict[str, Any]:
        """MA6.10's LegacyGenerationAdapter, in its very first, unmodified
        form: exactly the call every existing caller (kriya/cli.py's
        `generate` command) already makes, so this slice changes zero
        behavior of the actual generation pipeline - only wraps it with
        control-plane bookkeeping around the outside."""
        return await self.workflow_engine.run_generation_workflow(goal, workspace_path, **legacy_kwargs)
