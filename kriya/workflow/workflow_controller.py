"""WorkflowController - MA6.8/6.9/6.10/6.13/6.14 of the MA6 structured-
execution implementation plan. The shell that will eventually own
orchestration (MA6 invariant 10). Every ChangeKind still routes the REAL
outcome to the SAME place - the existing, untouched run_generation_workflow(),
called via _run_legacy_generation() (MA6.10's own explicit first step:
"WorkflowController -> LegacyGenerationAdapter -> run_generation_workflow(),
then responsibilities extracted gradually"). Does NOT rewrite
run_generation_workflow() (MA6 hard constraint) - the legacy call is
byte-for-byte what every existing caller already makes.

MA6.9 gives the four kinds real, DISTINCT control-plane bookkeeping ahead
of that call: MILESTONE attaches milestone_group_id/current_milestone_id;
REFACTOR captures a real git base_commit/tree_hash baseline. TASK/
ENHANCEMENT get no extra bookkeeping - genuinely differentiating their
SHAPE requires MA6.10's gradual internal extraction from
run_generation_workflow(), which has not happened.

MA6.10/6.13's remaining, real scope in this slice: `migration_mode="shadow"`
builds an actual EngineeringPlan (Planner -> parse_planner_structured_output,
MA6.3) validates it (plan_validation.validate_plan, MA6.2) and, if valid,
runs EVERY subtask through the real SubtaskExecutor (MA6.5) against a
deliberately minimal, on-disk-only ContextPackage (NOT the full
ContextOrchestrator/Graph RAG retrieval MA5.7 already does for the legacy
path - that integration is real scope MA6.10 does not attempt in this
slice). Critically: this NEVER replaces or influences the real outcome -
_run_legacy_generation still runs unconditionally and its result is what
WorkflowResult.legacy_result reports. Why the new path can't safely own
the real outcome yet: SubtaskExecutor stops at "get file content or a
tool result" - it does not itself apply edits, run compile/test
verification, or gate on approval, all of which still live only in
run_generation_workflow()'s own Quality Gates loop. Any exception in the
shadow path is caught and logged, never allowed to fail the real run.

`migration_mode="enforce"` (WorkflowController fully owns orchestration)
is refused here defensively - kriya.config.config.WorkflowControllerConfig's
own validator already rejects it at config-load time (MA6.14), but this
class checks again rather than trusting every caller to have gone through
that config path, matching this codebase's "fail loud, never silently do
something unsafe" convention (kriya/core/llm.py's egress check, this
project's own precedent).

MA7.1: wired into `kriya generate`'s real call path (kriya/cli.py's
`_run_generation` helper, all three run_generation_workflow call sites in
the `generate` command) - still gated by `workflow_controller.enabled:
false` by default in kriya.yaml, mirroring MA4.15/MA5's own "ship the
mechanism, default it off" pattern, so a stock install's behavior is
byte-for-byte unchanged. Setting `workflow_controller.enabled: true` (mode
stays "shadow", "enforce" is refused) is what actually makes this class
construct and run for a real `kriya generate` call. `kriya fix` and
`kriya plan-milestones` are NOT wired - deliberately out of MA7.1's scope,
left for a later increment.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from kriya.agents.contracts import parse_planner_structured_output
from kriya.control.decisions import Decision, DecisionLedger
from kriya.control.state import ControlState
from kriya.workflow import subtask_executor
from kriya.workflow.checkpoint import compute_base_commit, compute_tree_hash, new_run_id
from kriya.workflow.context_package import ContextPackage, build_context_package, make_context_item
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.plan_schema import EngineeringPlan, build_engineering_plan_from_planner_output
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.subtask_checkpoint import topological_subtask_order
from kriya.workflow.subtask_context_projection import project_for_subtask
from kriya.workflow.subtask_telemetry import (
    record_context_package_for_subtask,
    record_plan_created,
    record_subtask_attempt,
    record_undeclared_file_touch,
)
from kriya.workflow.triage import ChangeKind
from kriya.workflow.verification_report import build_verification_report
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus, VerificationReport, WorkflowResult

logger = logging.getLogger(__name__)

_VALID_MIGRATION_MODES = ("legacy", "shadow")


class WorkflowControllerConfigurationError(ValueError):
    pass


class WorkflowController:
    """Wraps an existing WorkflowEngine (kriya/workflow/workflow.py) -
    never constructs its own agents/LLM client, and deliberately reuses
    that engine's OWN `engineering_triage`/`planner`/`developer`/`kernel`
    rather than building second copies, so triage state/kernel wiring
    stays exactly what a real caller already set up."""

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
        migration_mode: str = "legacy",
        **legacy_kwargs: Any,
    ) -> WorkflowResult:
        if migration_mode not in _VALID_MIGRATION_MODES:
            raise WorkflowControllerConfigurationError(
                f"migration_mode must be one of {_VALID_MIGRATION_MODES!r}, got {migration_mode!r} - "
                "'enforce' is not safe yet (see this module's own docstring)."
            )

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

        subtask_results: Tuple[SubtaskResult, ...] = ()
        decisions: Tuple[Decision, ...] = ()
        verification_report: Optional[VerificationReport] = None
        if migration_mode == "shadow":
            try:
                plan, subtask_results, decisions, verification_report, notes = await self._run_structured_shadow(
                    goal, workspace_path, route, run_id,
                )
                if plan is not None:
                    control_state = control_state.with_updates(current_plan_hash=plan.content_hash())
                # Always logged, even with zero notes - a clean run (every
                # subtask COMPLETED, nothing to flag) is real, useful
                # evidence the shadow path executed at all, not an absence
                # of output. Discovered via live validation (MA7.1): the
                # previous `if notes:` gate meant a fully successful shadow
                # run left NO trace anywhere - the only way to confirm it
                # happened was inferring it from LLM call timing in the raw
                # log, which is not something a real operator should have
                # to do.
                summary = (
                    f"plan={plan.plan_id if plan else None} subtasks={len(subtask_results)} "
                    f"decisions={len(decisions)} verdict={verification_report.verdict.value if verification_report else None}"
                )
                if notes:
                    summary += f" notes={'; '.join(notes)}"
                logger.info(f"WorkflowController shadow run {run_id!r}: {summary}")
            except Exception as e:
                # The shadow path is strictly observational - see this
                # module's own docstring. A bug in Stage A parsing, plan
                # validation, or SubtaskExecutor must never take down the
                # real generation run underneath it.
                logger.warning(f"WorkflowController shadow run {run_id!r} failed (non-fatal): {e}")
                subtask_results = ()
                decisions = ()
                verification_report = None

        legacy_result = await self._run_legacy_generation(
            goal, workspace_path,
            milestone_group_id=milestone_group_id, milestone_index=milestone_index,
            milestone_total=milestone_total, **legacy_kwargs,
        )

        return WorkflowResult(
            run_id=run_id, control_state=control_state, route=route,
            legacy_result=legacy_result, subtask_results=subtask_results,
            decisions=decisions, verification_report=verification_report,
        )

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

    async def _run_structured_shadow(
        self, goal: str, workspace_path: str, route: Any, run_id: str,
    ) -> Tuple[
        Optional[EngineeringPlan], Tuple[SubtaskResult, ...],
        Tuple[Decision, ...], Optional[VerificationReport], List[str],
    ]:
        """Builds a real EngineeringPlan and runs SubtaskExecutor against
        every subtask - purely observational, see this module's own
        docstring for why the result never touches the real outcome.
        Uses a plain `goal` prompt for the Planner call, NOT the richer,
        RAG/skills/conventions-assembled plan_prompt run_generation_workflow()
        builds internally - a real, honestly-tracked gap (not silently
        pretended away) between what this shadow path sees and what the
        legacy path actually sees; closing it means integrating
        ContextOrchestrator (MA5.7) here, out of scope for this slice.

        MA7.1: also drives subtask_telemetry (MA6.12) and
        build_verification_report (MA6.11) - both existed as pure,
        independently-tested functions since MA6 but were never actually
        CALLED anywhere, including this method, until now (MA7.0's
        reachability inventory flagged this as a second, smaller dead spot
        beyond WorkflowController's own lack of a caller). The returned
        DecisionLedger is in-memory only here - persisting it to
        .kriya/control/decisions.jsonl is left to a future increment once
        this path is more than observational, same reasoning as
        ControlState/ContractRegistry/ArtifactRegistry not being persisted
        from here either. The VerificationReport is necessarily
        UNRESOLVED-heavy in shadow mode: SubtaskExecutor never runs real
        compile/test/tool verification (see this module's own docstring),
        so build_verification_report is called with no tool_results/
        judgment_results - an honest reflection of what shadow mode can
        actually attest to, not a simulated pass."""
        notes: List[str] = []
        ledger = DecisionLedger()

        plan_text = await self.workflow_engine.planner.run(goal)
        structured_output, parse_issue = parse_planner_structured_output(plan_text)
        if structured_output is None:
            notes.append(f"no structured plan: {parse_issue}")
            return None, (), (), None, notes

        plan = build_engineering_plan_from_planner_output(structured_output, plan_id=run_id, kind=route.kind)
        if plan is None:
            notes.append("structured output parsed but produced zero subtasks")
            return None, (), (), None, notes

        record_plan_created(ledger, plan)

        kernel = getattr(self.workflow_engine, "kernel", None)
        available_tool_names = None
        if kernel is not None:
            try:
                available_tool_names = kernel.registry.list_components("tool")
            except Exception as e:
                logger.debug(f"WorkflowController shadow run {run_id!r}: could not list registered tools: {e}")

        validation = await validate_plan(
            plan, workspace_path=workspace_path, available_tool_names=available_tool_names,
            route=route, triage_service=self.workflow_engine.engineering_triage,
        )
        if not validation.valid:
            notes.append(f"plan failed validation: {validation.errors}")
            return plan, (), ledger.all(), None, notes

        context = self._build_minimal_context(plan, workspace_path)

        results: List[SubtaskResult] = []
        for subtask_id in topological_subtask_order(plan):
            subtask = plan.subtask_by_id(subtask_id)
            if subtask is None:
                continue
            projected = project_for_subtask(context, subtask)
            record_context_package_for_subtask(ledger, plan, subtask_id, projected)
            result = await subtask_executor.execute(
                subtask=subtask, plan=plan, context=projected,
                kernel=kernel, developer_agent=self.workflow_engine.developer,
            )
            results.append(result)
            record_subtask_attempt(ledger, plan, result, attempt=1)
            if result.undeclared_files:
                record_undeclared_file_touch(ledger, plan, result)
            if result.status != SubtaskStatus.COMPLETED:
                notes.append(f"stopped at subtask {subtask_id!r}: status={result.status.value}")
                break

        report = build_verification_report(plan.acceptance_criteria)
        return plan, tuple(results), ledger.all(), report, notes

    def _build_minimal_context(self, plan: EngineeringPlan, workspace_path: str) -> ContextPackage:
        """Deliberately MINIMAL: reads each planned file's CURRENT on-disk
        content directly - no Graph RAG/vector retrieval/dependency-graph
        expansion (ContextOrchestrator, MA5.7). Real enough to let a
        MODEL-tagged subtask see existing content for files it modifies;
        narrower than what run_generation_workflow's own context assembly
        actually retrieves, which is exactly why this path stays
        shadow-only rather than becoming the source of truth."""
        items = []
        seen = set()
        for subtask in plan.subtasks:
            for pf in subtask.planned_files:
                if pf.path in seen:
                    continue
                seen.add(pf.path)
                full_path = os.path.join(workspace_path, pf.path)
                if not os.path.isfile(full_path):
                    continue
                try:
                    with open(full_path, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except Exception as e:
                    logger.debug(f"WorkflowController shadow context: could not read {pf.path!r}: {e}")
                    continue
                items.append(make_context_item(
                    path=pf.path, content=content, reason="planned file, current on-disk content",
                    source_type="named_in_request", trust_level="repository",
                ))
        return build_context_package(relevant_files=tuple(items))

    async def _run_legacy_generation(self, goal: str, workspace_path: str, **legacy_kwargs: Any) -> Dict[str, Any]:
        """MA6.10's LegacyGenerationAdapter, in its very first, unmodified
        form: exactly the call every existing caller (kriya/cli.py's
        `generate` command) already makes, so this slice changes zero
        behavior of the actual generation pipeline - only wraps it with
        control-plane bookkeeping (and, in shadow mode, an observational
        run alongside it) around the outside."""
        return await self.workflow_engine.run_generation_workflow(goal, workspace_path, **legacy_kwargs)
