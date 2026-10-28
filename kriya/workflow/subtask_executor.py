"""SubtaskExecutor - MA6.5 of the MA6 structured-execution implementation
plan. Executes exactly ONE validated Subtask (MA6.1) against the EXISTING
Developer/tool machinery - never a new autonomous agent (MA6 invariant 12),
never more than one subtask per call (MA6 invariant 2).

MODEL-tagged subtasks: narrows the whole-request ContextPackage down to
this subtask's own slice (MA6.4's project_for_subtask) and calls the
already-existing DeveloperAgent.run_generation with
known_target_files=this subtask's planned files - "one of the biggest
improvements for local-model reliability" per the MA6 spec, since the
Developer no longer sees the full milestone/plan/repo/retry history for
every single subtask. This is a SINGLE attempt, not a retry loop -
Developer failure/retry machinery remains authoritative and lives one
layer up (workflow.py's existing Quality Gates loop, still unmodified by
this module; MA6.6/6.9/6.10 are what eventually route that loop's calls
through here).

TOOL-tagged subtasks: never invoke the Developer/LLM at all (MA6 invariant
3) - resolves subtask.tool_name directly through the kernel's
ComponentRegistry("tool", ...) and calls the governed MODEL-AUTHORED entry
point kriya.tools.tool.execute_model_authored(tool, subtask.tool_arguments,
invocation) (S1, see below). An unregistered tool_name should already have been
rejected by plan_validation.py (MA6.2) before a plan reaches this module;
if one somehow still shows up here (a stale plan, a validator bypassed by
a caller error), execute() fails closed with NEEDS_REVIEW rather than
crashing or silently skipping the subtask.

A TOOL subtask's execution IS a real side effect (unlike a MODEL subtask,
which only returns content this module never itself applies), and its tool
and arguments are MODEL OUTPUT. S1 (OI-88, owner contract 2026-10-19): a
model-authored call is never the operator's call. `kriya tools execute`
(a person) keeps calling BaseTool.execute(); this module calls ONLY
execute_model_authored(), which hands the tool the invocation's roots (the
owner workspace and the plan's candidate worktree, supplied by the enforce
loop) and the run's autonomy mode, and runs nothing until the tool's own
authorize_model_invocation() allowed it - each tool reusing the existing
authority for the equivalent agent-generated effect (AuthorizedFileReader /
AuthorizedFileWriter / the trusted-control-path denial for the core tools,
TOOL-002/TOOL-003 for MCPTool, POL-001/SEC-005 inside ShellTool), and a tool
that declares nothing refused in human-in-the-loop. This module still
consults no policy engine of its own and never matches tool names
(Invariant: one security decision, one authoritative owner); it only says
WHO authored the call and over WHICH roots, and records the typed refusal. Any caller that isn't itself an authoritative, real
execution path (e.g. an observational/shadow context whose own contract
requires no mutation) MUST still decide not to route a TOOL-tagged
subtask into this function's real dispatch at all - see
kriya/workflow/workflow_controller.py's _run_structured_shadow for the
real precedent (hard-stops on any TOOL-tagged subtask, since shadow's own
contract is non-mutating by design, unrelated to whether TOOL execution
is safe). TOOL-001 (2026-09-13) made `_run_structured_enforce` - the
real, mutating, authoritative enforce-mode path - the first caller that
DOES route TOOL-tagged subtasks here for real, precisely because each
tool's own governance was already sufficient by then; see that method's
own per-subtask loop for the full account.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from kriya.config.authority import AutonomyModeError, autonomy_mode_of, is_human_in_the_loop
from kriya.core.registry import ComponentRegistryError
from kriya.policy.errors import PolicyDeniedError
from kriya.tools.tool import (
    ModelInvocationRefused,
    ModelToolInvocation,
    ToolEffectBoundary,
    ToolExecutionError,
    effect_boundary_of,
    execute_model_authored,
    resolve_planned_tool_governance,
    revalidate_model_authored,
)
from kriya.workflow.context_package import ContextPackage
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, Subtask
from kriya.workflow.subtask_context_projection import project_for_subtask
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

logger = logging.getLogger(__name__)


def _render_context_package(package: ContextPackage) -> str:
    """A minimal prompt-string rendering of a (subtask-narrowed)
    ContextPackage - deliberately not added as a method on ContextPackage
    itself (kriya/workflow/context_package.py, MA5.6), which stays a pure,
    hashable data shape with no opinion on prompt formatting. Mirrors
    kriya/workflow/context_projection.py's FileProjection.render() framing
    (`--- path [metadata] ---\\ncontent`) for consistency with the other
    context-rendering convention already established in this package."""
    sections: List[str] = []
    if package.conventions:
        sections.append(f"--- conventions ---\n{package.conventions}")
    for item in package.relevant_files:
        sections.append(
            f"--- {item.path} [source={item.source_type} trust={item.trust_level} "
            f"reason={item.reason}] ---\n{item.content}"
        )
    if package.contract_entries:
        sections.append(f"--- contracts ---\n{list(package.contract_entries)}")
    if package.artifact_entries:
        sections.append(f"--- artifacts ---\n{list(package.artifact_entries)}")
    return "\n\n".join(sections)


async def _execute_model_subtask(
    subtask: Subtask,
    context: ContextPackage,
    developer_agent: Any,
    **run_generation_kwargs: Any,
) -> SubtaskResult:
    narrowed = project_for_subtask(context, subtask)
    design_context = _render_context_package(narrowed)
    target_files = [pf.path for pf in subtask.planned_files if pf.action != FileAction.DELETE]

    try:
        files = await developer_agent.run_generation(
            task_description=subtask.description,
            design_context=design_context,
            existing_code_context="",
            known_target_files=target_files or None,
            **run_generation_kwargs,
        )
    except Exception as e:
        logger.warning(f"SubtaskExecutor: subtask {subtask.id!r} model execution failed: {e}")
        return SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.FAILED,
            execution_method=ExecutionMethod.MODEL.value, error=str(e),
        )

    declared = {pf.path for pf in subtask.planned_files}
    returned_paths = {
        (entry.get("filepath") or entry.get("path"))
        for entry in files
        if entry.get("filepath") or entry.get("path")
    }
    undeclared = tuple(sorted(returned_paths - declared))

    return SubtaskResult(
        subtask_id=subtask.id,
        status=SubtaskStatus.COMPLETED,
        execution_method=ExecutionMethod.MODEL.value,
        files=tuple(files),
        undeclared_files=undeclared,
    )


TOOL_CONFIRMATION_UNSUPPORTED = "TOOL_CONFIRMATION_UNSUPPORTED"


@dataclass(frozen=True)
class PlannedToolNames:
    """S18: ONE snapshot of the tool registry for one planning operation. ``registered``: every registered tool name
    (verification[] / acceptance tool_name membership keeps checking these); ``plannable``: the names the one governance
    resolution (kriya.tools.tool.resolve_planned_tool_governance) admits as a planned step's tool - the only names the
    Planner is shown and the only names plan validation accepts for a TOOL subtask."""

    registered: Tuple[str, ...]
    plannable: Tuple[str, ...]


def plannable_tool_names(registry: Any, config: Any) -> PlannedToolNames:
    """S18: the ONE projection of the planned-tool governance resolution, used by the Planner catalogue
    (stage_planning, planner_repair prompts), plan validation (enforce_loop, shadow_mode, stage_planning) and - through
    execute_model_authored - rechecked by the executor. A registry that cannot be listed admits NO tool (fail closed:
    both tuples empty, so every TOOL subtask is refused at validation). Raises AutonomyModeError for a malformed mode."""
    try:
        registered = tuple(sorted(registry.list_components("tool")))
    except Exception as e:
        logger.warning(f"SubtaskExecutor: the tool registry could not be listed ({e}); no tool is plannable this time")
        return PlannedToolNames(registered=(), plannable=())
    mode = _config_autonomy_mode(config)
    containment = getattr(getattr(config, "autonomy", None), "containment_backend", None)
    plannable = []
    for name in registered:
        try:
            tool = registry.get("tool", name)
        except Exception as e:
            logger.warning(f"SubtaskExecutor: registered tool {name!r} could not be resolved ({e}); not plannable")
            continue
        if resolve_planned_tool_governance(tool, autonomy_mode=mode, containment_backend=containment).plannable:
            plannable.append(name)
    return PlannedToolNames(registered=registered, plannable=tuple(plannable))


def _config_autonomy_mode(config: Any) -> Optional[str]:
    """OI-135: None when there is no configuration or no mode in it (the strictest reading downstream); otherwise the
    mode through the one fail-closed reader - a mode outside the vocabulary (null included) raises AutonomyModeError."""
    autonomy = getattr(config, "autonomy", None)
    if autonomy is None or not hasattr(autonomy, "mode"):
        return None  # no readable mode at all: the strictest reading downstream (never a permissive one)
    return autonomy_mode_of(config)


def _autonomy_mode(kernel: Any) -> Optional[str]:
    """The run's autonomy.mode, None when the kernel carries no readable one (the invocation then counts as
    human-in-the-loop, the strictest)."""
    return _config_autonomy_mode(getattr(kernel, "config", None))


def _hitl_blocks_tool_without_confirmation(tool: Any, kernel: Any) -> bool:
    """True when the tool asks for confirmation when an operator runs it and the run is human-in-the-loop (which has no per-run review for a planner-chosen tool call)."""
    mode = _autonomy_mode(kernel)
    return bool(getattr(tool, "requires_confirmation", False)) and mode is not None and is_human_in_the_loop(mode)


# S18d (OI-150 a): per MODEL subtask id, the files that unit actually applied to the run's candidate - Kriya's own
# record of the candidate transaction (ControlState.subtask_written_files, from the attempt's all_files_written: a write
# that left the bytes unchanged is not a change). The enforce loop passes it; None = no evidence.
CandidateWrites = Mapping[str, Sequence[str]]


def _changed_the_candidate(subtask_id: str, candidate_writes: Optional[CandidateWrites]) -> bool:
    """Whether the MODEL unit ``subtask_id`` changed the candidate, by the observed record only - never its label,
    role, name or description. No record for it (no evidence passed, a resumed or unknown unit) counts as a change:
    the S18c rule, fail closed."""
    if candidate_writes is None or subtask_id not in candidate_writes:
        return True
    return bool(candidate_writes[subtask_id])


def reads_candidate_view(subtask: Subtask, plan: EngineeringPlan,
                         candidate_writes: Optional[CandidateWrites] = None) -> bool:
    """S18c (OI-147) + S18d (OI-150 a): whether a planned read in ``subtask`` describes the run's candidate. Its
    depends_on (transitively) is the plan's own data flow, the one signal of which repository state the read reasons
    about; along it, a MODEL unit that observably changed the candidate (``candidate_writes``) means the candidate as
    that unit left it. A MODEL unit that changed nothing (a verification unit, a verified NO CHANGE) and a TOOL step
    (reads change nothing) are walked through to their own dependencies. No change-producing ancestry = a baseline
    read of the owner workspace, whatever position the executor's order gives it (its id tie-break runs the Planner's
    worked example B, "before it changes", after the MODEL steps). No per-step flag exists or is inferred from text. An
    id the plan does not hold contributes nothing (plan validation already refuses one)."""
    by_id = {candidate.id: candidate for candidate in plan.subtasks}
    pending, seen = list(subtask.depends_on), set()
    while pending:
        dependency = by_id.get(pending.pop())
        if dependency is None or dependency.id in seen:
            continue
        if dependency.execution_method == ExecutionMethod.MODEL and _changed_the_candidate(dependency.id,
                                                                                            candidate_writes):
            return True
        seen.add(dependency.id)
        pending.extend(dependency.depends_on)
    return False


def _tool_error_result(subtask: Subtask, error: Exception, effect_boundary: ToolEffectBoundary) -> SubtaskResult:
    """The typed result of a TOOL refusal or failure, shared by the execute path and the revalidate-only path (so authority never diverges between them). ``effect_boundary`` is
    the fact the execution owner attached (or, for a refusal raised here before the owner was reached, the caller's position); it is transported, never inferred from ``error``."""
    if isinstance(error, (AutonomyModeError, ModelInvocationRefused)):  # S18 / OI-135 malformed mode; S1 TOOLBOX refusal: typed NEEDS_REVIEW, never a permissive reading
        logger.warning(f"SubtaskExecutor: subtask {subtask.id!r} tool {subtask.tool_name!r} refused: {error}")
        return SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.NEEDS_REVIEW, execution_method=ExecutionMethod.TOOL.value,
            error=str(error), reason_codes=(error.reason_code,), effect_boundary=effect_boundary,
        )
    logger.info(f"SubtaskExecutor: subtask {subtask.id!r} tool {subtask.tool_name!r} failed: {error}")
    # TOOL-001 (2026-09-13): BaseTool.execute() always wraps the REAL underlying exception into ToolExecutionError via `raise ... from e` (kriya/tools/tool.py) - when
    # that real cause is a PolicyDeniedError (ExecutionPolicy DENY/REQUIRE_APPROVAL, including TOOL-002's MCP invocation denial), its own `result.reason_code` is
    # surfaced here as structured evidence rather than only living inside the free-text error string - never re-derived or re-evaluated.
    reason_codes: tuple = ()
    if isinstance(error.__cause__, PolicyDeniedError):
        reason_codes = (error.__cause__.result.reason_code,)
    return SubtaskResult(
        subtask_id=subtask.id, status=SubtaskStatus.FAILED,
        execution_method=ExecutionMethod.TOOL.value, error=str(error),
        reason_codes=reason_codes, effect_boundary=effect_boundary,
    )


def _tool_preflight(subtask: Subtask, kernel: Any, tool_roots: Sequence[str], plan: EngineeringPlan,
                    candidate_writes: Optional[CandidateWrites]) -> Tuple[Optional[Any], Optional[ModelToolInvocation], Optional[SubtaskResult]]:
    """Everything that happens BEFORE a TOOL subtask's authority decision and effect: the registry lookup, the mode, the human-in-the-loop confirmation stop and the
    invocation. Returns (tool, invocation, None), or (None, None, the typed refusal result) with nothing run."""
    try:
        tool = kernel.registry.get("tool", subtask.tool_name)
    except (ComponentRegistryError, Exception) as e:
        logger.warning(
            f"SubtaskExecutor: subtask {subtask.id!r} references tool_name "
            f"{subtask.tool_name!r}, not resolvable in the kernel's tool registry: {e}"
        )
        return None, None, SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.NEEDS_REVIEW,
            execution_method=ExecutionMethod.TOOL.value,
            error=f"tool_name {subtask.tool_name!r} not registered", effect_boundary=ToolEffectBoundary.PROVEN_PRE_EFFECT,  # nothing was invoked: the owner is not reached
        )

    # OI-135 (S18): a malformed mode is a typed stop before anything else, never a permissive reading.
    try:
        _autonomy_mode(kernel)
    except AutonomyModeError as e:
        return None, None, _tool_error_result(subtask, e, ToolEffectBoundary.PROVEN_PRE_EFFECT)  # before the owner is reached

    # Review of the runtime-approval stack (2026-10-15), B3: a tool that asks to be confirmed when an operator runs it (`kriya tools execute` honours `requires_confirmation`) must not run unconfirmed
    # from a planner-emitted subtask. Human-in-the-loop has no per-run review subject here, so it stops BEFORE the call (nothing executed) with a typed reason; every other mode is unchanged.
    if _hitl_blocks_tool_without_confirmation(tool, kernel):
        logger.warning(f"SubtaskExecutor: subtask {subtask.id!r} tool {subtask.tool_name!r} requires confirmation and human-in-the-loop mode cannot approve a planner-chosen tool call; not executed.")
        return None, None, SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.NEEDS_REVIEW, execution_method=ExecutionMethod.TOOL.value,
            error=f"{TOOL_CONFIRMATION_UNSUPPORTED}: tool {subtask.tool_name!r} requires your confirmation to run and this version cannot ask you from a planned step. Nothing was run.",
            reason_codes=(TOOL_CONFIRMATION_UNSUPPORTED,), effect_boundary=ToolEffectBoundary.PROVEN_PRE_EFFECT,  # nothing was invoked: the owner is not reached
        )

    # S1: the model-authored entry point - authorized by the tool's own declaration over these roots before anything runs.
    invocation = ModelToolInvocation(roots=tuple(tool_roots), autonomy_mode=_autonomy_mode(kernel),
                                     reads_candidate=reads_candidate_view(subtask, plan, candidate_writes))
    return tool, invocation, None


def revalidate_tool_subtask_authority(subtask: Subtask, kernel: Any, tool_roots: Sequence[str], plan: EngineeringPlan,
                                      candidate_writes: Optional[CandidateWrites]) -> Optional[SubtaskResult]:
    """D1: the AUTHORITY decision of a TOOL subtask with NO effect. A resume uses it for a TOOL execution that is already durably COMPLETED: authority may be revalidated, but
    the side effect is never run again for that execution identity. Returns None when the call is still authorized, else the same typed refusal result the execute path returns."""
    tool, invocation, refusal = _tool_preflight(subtask, kernel, tool_roots, plan, candidate_writes)
    if refusal is not None:
        return refusal
    try:
        revalidate_model_authored(tool, subtask.tool_arguments, invocation)
    except (AutonomyModeError, ModelInvocationRefused, ToolExecutionError) as e:
        return _tool_error_result(subtask, e, effect_boundary_of(e))
    return None


async def _execute_tool_subtask(subtask: Subtask, kernel: Any, tool_roots: Sequence[str],
                                plan: EngineeringPlan, candidate_writes: Optional[CandidateWrites]) -> SubtaskResult:
    tool, invocation, refusal = _tool_preflight(subtask, kernel, tool_roots, plan, candidate_writes)
    if refusal is not None:
        return refusal
    try:
        output = await execute_model_authored(tool, subtask.tool_arguments, invocation)
    except (AutonomyModeError, ModelInvocationRefused, ToolExecutionError) as e:
        return _tool_error_result(subtask, e, effect_boundary_of(e))

    return SubtaskResult(
        subtask_id=subtask.id, status=SubtaskStatus.COMPLETED,
        execution_method=ExecutionMethod.TOOL.value, tool_output=output,
    )


async def execute(
    *,
    subtask: Subtask,
    plan: EngineeringPlan,
    context: ContextPackage,
    kernel: Optional[Any] = None,
    developer_agent: Optional[Any] = None,
    tool_roots: Sequence[str] = (),
    candidate_writes: Optional[CandidateWrites] = None,
    **run_generation_kwargs: Any,
) -> SubtaskResult:
    """Executes exactly one Subtask. `plan` is accepted (and not otherwise
    used here) so a caller/future extension always has it available for
    cross-subtask context without SubtaskExecutor needing a second
    signature change later - e.g. MA6.6's failure/retry metadata mapping
    reads plan_id/subtask relationships from the SAME plan object a
    caller already threaded through this call.

    A TOOL-tagged subtask requires `kernel`; a MODEL-tagged subtask
    requires `developer_agent` (both Optional only because they're
    mutually irrelevant to the other execution_method - passing neither
    for the wrong kind is a caller error, surfaced as a clear ValueError
    rather than a confusing AttributeError deeper in the call).

    ``tool_roots`` (S1): the workspace roots a TOOL subtask's model-authored
    call may touch, the owner workspace first; empty refuses every
    path-bearing call (TOOL_WORKSPACE_ROOT_UNAVAILABLE). S18c (OI-147): ``plan``
    decides which root a TOOL read describes; S18d (OI-150 a): with ``candidate_writes``,
    the observed effect of each MODEL ancestor (reads_candidate_view). A MODEL
    subtask ignores it."""
    if subtask.execution_method == ExecutionMethod.TOOL:
        if kernel is None:
            raise ValueError(f"subtask {subtask.id!r} is execution_method=tool but no kernel was supplied")
        return await _execute_tool_subtask(subtask, kernel, tool_roots, plan, candidate_writes)

    if developer_agent is None:
        raise ValueError(f"subtask {subtask.id!r} is execution_method=model but no developer_agent was supplied")
    return await _execute_model_subtask(subtask, context, developer_agent, **run_generation_kwargs)
