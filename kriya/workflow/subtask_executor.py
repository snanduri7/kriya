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
from typing import Any, List, Optional, Sequence

from kriya.core.registry import ComponentRegistryError
from kriya.policy.errors import PolicyDeniedError
from kriya.tools.tool import ModelInvocationRefused, ModelToolInvocation, ToolExecutionError, execute_model_authored
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


def _autonomy_mode(kernel: Any) -> Optional[str]:
    """The run's autonomy.mode, None when the kernel carries no readable one (the invocation then counts as
    human-in-the-loop, the strictest)."""
    mode = getattr(getattr(getattr(kernel, "config", None), "autonomy", None), "mode", None)
    return mode if isinstance(mode, str) else None


def _hitl_blocks_tool_without_confirmation(tool: Any, kernel: Any) -> bool:
    """True when the tool asks for confirmation when an operator runs it and the run is human-in-the-loop (which has no per-run review for a planner-chosen tool call)."""
    return bool(getattr(tool, "requires_confirmation", False)) and _autonomy_mode(kernel) == "human-in-the-loop"


async def _execute_tool_subtask(subtask: Subtask, kernel: Any, tool_roots: Sequence[str] = ()) -> SubtaskResult:
    try:
        tool = kernel.registry.get("tool", subtask.tool_name)
    except (ComponentRegistryError, Exception) as e:
        logger.warning(
            f"SubtaskExecutor: subtask {subtask.id!r} references tool_name "
            f"{subtask.tool_name!r}, not resolvable in the kernel's tool registry: {e}"
        )
        return SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.NEEDS_REVIEW,
            execution_method=ExecutionMethod.TOOL.value,
            error=f"tool_name {subtask.tool_name!r} not registered",
        )

    # Review of the runtime-approval stack (2026-10-15), B3: a tool that asks to be confirmed when an operator runs it (`kriya tools execute` honours `requires_confirmation`) must not run unasked
    # from a planner-emitted subtask. Human-in-the-loop has no per-run review subject here, so it stops BEFORE the call (nothing executed) with a typed reason; every other mode is unchanged.
    if _hitl_blocks_tool_without_confirmation(tool, kernel):
        logger.warning(f"SubtaskExecutor: subtask {subtask.id!r} tool {subtask.tool_name!r} requires confirmation and human-in-the-loop mode cannot approve a planner-chosen tool call; not executed")
        return SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.NEEDS_REVIEW, execution_method=ExecutionMethod.TOOL.value,
            error=f"{TOOL_CONFIRMATION_UNSUPPORTED}: tool {subtask.tool_name!r} requires your confirmation to run and this version cannot ask you from a planned step. Nothing was run.",
            reason_codes=(TOOL_CONFIRMATION_UNSUPPORTED,),
        )

    # S1: the model-authored entry point - authorized by the tool's own declaration over these roots before anything
    # runs; a refusal is typed (TOOLBOX code, NEEDS_REVIEW) or the reused authority's PolicyDeniedError (FAILED, below).
    invocation = ModelToolInvocation(roots=tuple(tool_roots), autonomy_mode=_autonomy_mode(kernel))
    try:
        output = await execute_model_authored(tool, subtask.tool_arguments, invocation)
    except ModelInvocationRefused as e:
        logger.warning(f"SubtaskExecutor: subtask {subtask.id!r} tool {subtask.tool_name!r} refused: {e}")
        return SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.NEEDS_REVIEW, execution_method=ExecutionMethod.TOOL.value,
            error=str(e), reason_codes=(e.reason_code,),
        )
    except ToolExecutionError as e:
        logger.info(f"SubtaskExecutor: subtask {subtask.id!r} tool {subtask.tool_name!r} failed: {e}")
        # TOOL-001 (2026-09-13): BaseTool.execute() always wraps the REAL
        # underlying exception into ToolExecutionError via `raise ... from
        # e` (kriya/tools/tool.py) - when that real cause is a
        # PolicyDeniedError (ExecutionPolicy DENY/REQUIRE_APPROVAL,
        # including TOOL-002's MCP invocation denial), its own
        # `result.reason_code` is surfaced here as structured evidence
        # (Task 9: "policy/authority decision where available") rather
        # than only living inside the free-text error string - never
        # re-derived or re-evaluated, purely read off the exception this
        # tool call already raised.
        reason_codes: tuple = ()
        if isinstance(e.__cause__, PolicyDeniedError):
            reason_codes = (e.__cause__.result.reason_code,)
        return SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.FAILED,
            execution_method=ExecutionMethod.TOOL.value, error=str(e),
            reason_codes=reason_codes,
        )

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
    path-bearing call (TOOL_WORKSPACE_ROOT_UNAVAILABLE)."""
    if subtask.execution_method == ExecutionMethod.TOOL:
        if kernel is None:
            raise ValueError(f"subtask {subtask.id!r} is execution_method=tool but no kernel was supplied")
        return await _execute_tool_subtask(subtask, kernel, tool_roots)

    if developer_agent is None:
        raise ValueError(f"subtask {subtask.id!r} is execution_method=model but no developer_agent was supplied")
    return await _execute_model_subtask(subtask, context, developer_agent, **run_generation_kwargs)
