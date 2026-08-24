"""WorkflowController - MA6.8 of the MA6 structured-execution
implementation plan. The shell that will eventually own orchestration
(MA6 invariant 10) - for THIS incremental slice, every ChangeKind routes
to the same place: the existing, untouched run_generation_workflow(),
called via _run_legacy_generation() (MA6.10's own explicit first step:
"WorkflowController -> LegacyGenerationAdapter -> run_generation_workflow(),
then responsibilities extracted gradually"). kind-specific SHAPE (MA6.9's
task/enhancement/refactor/milestone adapters) and real structured-plan
execution (subtask_executor.py, wired in via MA6.9/6.10) come later - this
class only establishes the entry point and the
triage -> ControlState -> kind-dispatch skeleton around it. Does NOT
rewrite run_generation_workflow() (MA6 hard constraint) - the legacy call
below is byte-for-byte what every existing caller already makes.

Not wired into any CLI/repl command path yet - kriya.config's own
migration-mode gate (legacy/shadow/enforce, `workflow_controller.enabled:
false` by default) is MA6.13's job, mirroring MA4.15/MA5's own "ship the
mechanism, default it off" pattern. This class is importable and
independently testable today, invoked by nothing in the default
`kriya generate` path until that gate exists.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from kriya.control.state import ControlState
from kriya.workflow.checkpoint import new_run_id
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
        )

        # Every entry currently points at the same handler - deliberate,
        # not a placeholder oversight: MA6.9 replaces individual entries
        # with real per-kind adapters one at a time, each swap independent
        # of the others, without ever touching this dispatch shape itself.
        dispatch: Dict[ChangeKind, Callable] = {
            ChangeKind.TASK: self._run_legacy_generation,
            ChangeKind.ENHANCEMENT: self._run_legacy_generation,
            ChangeKind.REFACTOR: self._run_legacy_generation,
            ChangeKind.MILESTONE: self._run_legacy_generation,
        }
        handler = dispatch[route.kind]
        legacy_result = await handler(goal, workspace_path, **legacy_kwargs)

        return WorkflowResult(run_id=run_id, control_state=control_state, route=route, legacy_result=legacy_result)

    async def _run_legacy_generation(self, goal: str, workspace_path: str, **legacy_kwargs: Any) -> Dict[str, Any]:
        """MA6.10's LegacyGenerationAdapter, in its very first, unmodified
        form: exactly the call every existing caller (kriya/cli.py's
        `generate` command) already makes, so this slice changes zero
        behavior of the actual generation pipeline - only wraps it with
        control-plane bookkeeping around the outside."""
        return await self.workflow_engine.run_generation_workflow(goal, workspace_path, **legacy_kwargs)
