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
ComponentRegistry("tool", ...) and calls tool.execute(**subtask.
tool_arguments). An unregistered tool_name should already have been
rejected by plan_validation.py (MA6.2) before a plan reaches this module;
if one somehow still shows up here (a stale plan, a validator bypassed by
a caller error), execute() fails closed with NEEDS_REVIEW rather than
crashing or silently skipping the subtask.

CALLER RESPONSIBILITY, not enforced here: a TOOL subtask's execution IS a
real side effect (unlike a MODEL subtask, which only returns content this
module never itself applies) - "shell"/"git" are always-registered real
tools (plugins/core_tools) capable of arbitrary command execution / real
git mutation, and this module consults no policy engine before running
one. Any caller that isn't itself an authoritative, real execution path
(e.g. an observational/shadow context whose own contract requires no
mutation) MUST decide not to route a TOOL-tagged subtask into this
function's real dispatch at all - see kriya/workflow/workflow_controller.py's
_run_structured_shadow for the real precedent (hard-stops on any
TOOL-tagged subtask rather than letting shadow mode run one for real).
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from kriya.core.registry import ComponentRegistryError
from kriya.tools.tool import ToolExecutionError
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


async def _execute_tool_subtask(subtask: Subtask, kernel: Any) -> SubtaskResult:
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

    try:
        output = await tool.execute(**subtask.tool_arguments)
    except ToolExecutionError as e:
        logger.info(f"SubtaskExecutor: subtask {subtask.id!r} tool {subtask.tool_name!r} failed: {e}")
        return SubtaskResult(
            subtask_id=subtask.id, status=SubtaskStatus.FAILED,
            execution_method=ExecutionMethod.TOOL.value, error=str(e),
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
    rather than a confusing AttributeError deeper in the call)."""
    if subtask.execution_method == ExecutionMethod.TOOL:
        if kernel is None:
            raise ValueError(f"subtask {subtask.id!r} is execution_method=tool but no kernel was supplied")
        return await _execute_tool_subtask(subtask, kernel)

    if developer_agent is None:
        raise ValueError(f"subtask {subtask.id!r} is execution_method=model but no developer_agent was supplied")
    return await _execute_model_subtask(subtask, context, developer_agent, **run_generation_kwargs)
