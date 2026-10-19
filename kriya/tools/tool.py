import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from kriya.config.authority import is_human_in_the_loop, parse_autonomy_mode

logger = logging.getLogger(__name__)


class ToolExecutionError(Exception):
    """Base class for exceptions raised during tool execution."""
    pass

class BaseTool(ABC):
    """Base class that all Kriya tools must implement to fit into the framework."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier name of the tool."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Detailed description of the tool and its behavior."""
        pass

    @property
    @abstractmethod
    def arguments_schema(self) -> Type[BaseModel]:
        """Pydantic model class representing arguments structure and validation."""
        pass

    @property
    def requires_confirmation(self) -> bool:
        """Whether callers (e.g. the CLI) should prompt for confirmation before executing this tool."""
        return False

    @property
    def planned_governance(self) -> Any:
        """S18: how a PLANNED (model-authored) call to this tool is governed - one ``ToolGovernance`` value. None (this
        default) = undeclared = unplannable. A declaration is honoured only for the shipped tools listed in
        ``_SHIPPED_PLANNED_DECLARATIONS`` (resolve_planned_tool_governance); any other tool, whatever it declares, is
        treated as undeclared. The operator's own invocation (`kriya tools execute`) never consults it."""
        return None

    def mutates_workspace(self, arguments: Dict[str, Any]) -> bool:
        """PRD-006: whether this call may change files, so a direct CLI
        invocation must own the workspace (the same run-ownership gateway
        as ``generate``). Fails closed: every tool, including MCP tools whose
        effects Kriya cannot know, is mutating unless it proves otherwise
        for these exact arguments."""
        return True

    async def execute(self, **kwargs: Any) -> Any:
        """Validates inputs against arguments_schema and calls _run."""
        try:
            # Validate input using Pydantic
            validated = self.arguments_schema(**kwargs)
            logger.info(f"Executing tool '{self.name}' with arguments: {validated.model_dump()}")
            return await self._run(validated)
        except ValidationError as ve:
            logger.error(f"Validation failed for tool '{self.name}': {ve}")
            raise ToolExecutionError(f"Argument validation failed: {ve.errors()}") from ve
        except Exception as e:
            logger.error(f"Error during execution of tool '{self.name}': {e}", exc_info=True)
            raise ToolExecutionError(f"Tool execution failed: {e}") from e

    @abstractmethod
    async def _run(self, args: Any) -> Any:
        """Internal execution logic implementation."""
        pass

    def authorize_model_invocation(self, args: BaseModel, invocation: "ModelToolInvocation") -> BaseModel:
        """S1: how a MODEL-AUTHORED call to this tool is governed; returns the arguments to run with (paths resolved
        to exactly what is authorized) or raises before anything runs. This default is for a tool that declares
        nothing: refused wherever every change is reviewed by a human (a planned step has no review subject, and no
        authority is inferred from a missing confirmation flag or a self-declared non-mutating call); elsewhere the
        tool's own authority governs it, the TOOL-001 contract (owner question, S1 report)."""
        if invocation.reviews_every_change:
            raise ModelInvocationRefused(
                TOOL_MODEL_AUTHORITY_UNDECLARED,
                f"tool {self.name!r} does not declare how a planned (model-authored) call to it is governed, and "
                "this run reviews every change; a planned step cannot ask you. Nothing was run.",
            )
        return args

    async def _run_model_authored(self, args: Any, invocation: "ModelToolInvocation") -> Any:
        """Runs an already-authorized model-authored call; a tool whose effect must stay inside the invocation's
        roots while it runs (e.g. a directory walk) overrides this."""
        return await self._run(args)


# =====================================================================
# S1 (OI-88): model-authored tool calls
# =====================================================================
#
# Who authored a call is structural, never inferred from text: `BaseTool.execute(**arguments)` is the OPERATOR's entry
# (`kriya tools execute`, a person naming the tool and its arguments), unchanged; `execute_model_authored()` below is the
# only entry for a call whose tool and arguments are model output (a planner-emitted TOOL subtask). The arguments arrive as
# ONE mapping, never spread into keyword parameters, so no planner-chosen key can bind to an authority parameter.
#
# The decision is the tool's own (`BaseTool.authorize_model_invocation`), because only the tool knows which of its
# arguments name paths and which operations mutate; each core tool reuses the EXISTING authority for the equivalent
# agent-generated effect (AuthorizedFileReader for a model-requested read, AuthorizedFileWriter for a model-directed
# write, the PLAT-039 trusted-control-path denial for repository metadata, TOOL-002/003 for MCP). A tool that declares
# nothing is refused wherever every change is reviewed by a human (human-in-the-loop), because a planned step has no
# review subject: no authority is inferred from a missing confirmation flag or a self-declared "non-mutating".

HUMAN_IN_THE_LOOP = "human-in-the-loop"

# The closed table of TOOLBOX refusal reason codes (policy keys on reason_code; a code outside this table is refused at
# construction). Refusals that come from a reused AUTHORITY mechanism keep that owner's own code (PolicyDeniedError).
TOOL_NOT_GOVERNED = "TOOL_NOT_GOVERNED"
TOOL_MODEL_AUTHORITY_UNDECLARED = "TOOL_MODEL_AUTHORITY_UNDECLARED"
TOOL_MUTATION_REVIEW_UNAVAILABLE = "TOOL_MUTATION_REVIEW_UNAVAILABLE"
TOOL_WORKSPACE_ROOT_UNAVAILABLE = "TOOL_WORKSPACE_ROOT_UNAVAILABLE"
# S18 (OI-127): a tool whose declaration is OPERATOR_ONLY (planned shell / validate_refactor) and a declaration that is not
# a ToolGovernance value. An undeclared tool (or a non-shipped tool, whatever it declares) keeps
# TOOL_MODEL_AUTHORITY_UNDECLARED: one concept, one code.
TOOL_OPERATOR_ONLY = "TOOL_OPERATOR_ONLY"
TOOL_GOVERNANCE_INVALID = "TOOL_GOVERNANCE_INVALID"
MODEL_TOOL_REFUSAL_REASON_CODES = frozenset({
    TOOL_NOT_GOVERNED, TOOL_MODEL_AUTHORITY_UNDECLARED, TOOL_MUTATION_REVIEW_UNAVAILABLE, TOOL_WORKSPACE_ROOT_UNAVAILABLE,
    TOOL_OPERATOR_ONLY, TOOL_GOVERNANCE_INVALID,
})


class ModelInvocationRefused(ToolExecutionError):
    """A model-authored tool call refused by TOOLBOX before anything ran."""

    def __init__(self, reason_code: str, explanation: str) -> None:
        if reason_code not in MODEL_TOOL_REFUSAL_REASON_CODES:
            raise ValueError(f"unknown model-tool refusal reason code {reason_code!r}")
        super().__init__(f"{reason_code}: {explanation}")
        self.reason_code = reason_code
        self.explanation = explanation


@dataclass(frozen=True)
class ModelToolInvocation:
    """The authority context the model-authored caller supplies.

    ``roots``: the workspace roots this call may touch, the owner workspace first (a relative path resolves against it,
    exactly where the tool would have resolved it from the run's working folder), then any further readable root (the
    run's candidate worktree). Empty = unknown, and every path-bearing call is refused.
    ``autonomy_mode``: the run's ``autonomy.mode``; unknown (None) counts as human-in-the-loop (the strictest)."""

    roots: Tuple[str, ...] = ()
    autonomy_mode: Optional[str] = None

    @property
    def reviews_every_change(self) -> bool:
        # OI-135: unknown (None) is the strictest reading; any other value goes through the fail-closed reader.
        return self.autonomy_mode is None or is_human_in_the_loop(self.autonomy_mode)

    def resolve(self, path: str) -> str:
        """The absolute path the tool's own effect would use (``os.path.abspath`` of it against the owner workspace:
        lexical ``..``/``.``/separator normalization, ``~`` literal), so what is authorized is exactly what runs."""
        if not self.roots:
            raise ModelInvocationRefused(
                TOOL_WORKSPACE_ROOT_UNAVAILABLE,
                f"no workspace root was supplied for this planned tool call, so its path {path!r} cannot be authorized.",
            )
        return os.path.abspath(os.path.join(self.roots[0], path))


# =====================================================================
# S18 (OI-127, OI-88): the ONE planned-tool governance resolution
# =====================================================================
#
# Every tool declares how a PLANNED call to it is governed (BaseTool.planned_governance). One resolver below turns that
# declaration, the invocation origin (this resolver is for model-authored calls only; the operator's `kriya tools
# execute` is a separate authority and never consults it), the run's autonomy mode and the current containment backend
# into one decision. The Planner catalogue projects it, plan validation enforces it and the executor rechecks it
# (kriya/workflow/subtask_executor.plannable_tool_names and execute_model_authored below). There is no other policy.
#
# Option B (S18 preflight, handover/MODULARIZATION/S18-PREFLIGHT.md): planned shell / validate_refactor are OPERATOR_ONLY
# because no backend available today can prove that a planned command's effect stays out of later evidence. A
# declaration is honoured only for the shipped tools in _SHIPPED_PLANNED_DECLARATIONS, and only when the declaring
# property is defined on that exact class in the shipped file. Any other tool (a third-party plugin or a subclass),
# whatever it declares, is undeclared = unplannable until a plugin-approval path exists. Plugins run in-process: this
# stops a plugin from declaring itself plannable through the tool contract; it is not a defence against hostile
# in-process code.


class ToolGovernance(str, Enum):
    READ_ONLY = "read_only"  # reads only; each path-bearing call is still authorized by its own reader
    GOVERNED_SIDE_EFFECT = "governed_side_effect"  # a side effect whose every call passes its own durable authority (TOOL-002)
    OPERATOR_ONLY = "operator_only"  # only an operator may invoke it; never plannable


PLANNABLE_TOOL_GOVERNANCE = frozenset({ToolGovernance.READ_ONLY, ToolGovernance.GOVERNED_SIDE_EFFECT})

_DISTRIBUTION_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
_CORE_TOOLS_FILE = os.path.join(_DISTRIBUTION_ROOT, "plugins", "core_tools", "__init__.py")
# The closed table of tools whose planned-call declaration is honoured: class name -> the shipped file that must define
# that class's planned_governance.
_SHIPPED_PLANNED_DECLARATIONS: Dict[str, str] = {
    "FilesystemTool": _CORE_TOOLS_FILE,
    "ShellTool": _CORE_TOOLS_FILE,
    "GitTool": _CORE_TOOLS_FILE,
    "SearchTool": _CORE_TOOLS_FILE,
    "ASTTool": _CORE_TOOLS_FILE,
    "ValidationTool": os.path.join(_DISTRIBUTION_ROOT, "plugins", "core_tools", "validation_tool.py"),
    "MCPTool": os.path.join(_DISTRIBUTION_ROOT, "kriya", "mcp", "mcp.py"),
}


def _shipped_declaration(tool: Any) -> bool:
    """True only when ``tool``'s exact class is listed and defines planned_governance itself, in its shipped file."""
    cls = type(tool)
    expected = _SHIPPED_PLANNED_DECLARATIONS.get(cls.__qualname__)
    if expected is None:
        return False
    declared = cls.__dict__.get("planned_governance")
    code = getattr(getattr(declared, "fget", declared), "__code__", None)
    return code is not None and os.path.realpath(code.co_filename) == expected


@dataclass(frozen=True)
class ToolGovernanceDecision:
    """The one decision on whether a tool may be the subject of a planned step, with every input it was made from."""

    plannable: bool
    governance: Optional[ToolGovernance]
    reason_code: Optional[str]
    autonomy_mode: Optional[str]
    containment_backend: Optional[str]
    explanation: str


def resolve_planned_tool_governance(tool: Any, *, autonomy_mode: Optional[str],
                                    containment_backend: Optional[str] = None) -> ToolGovernanceDecision:
    """THE governance resolution for a planned (model-authored) tool call. ``autonomy_mode``: the run's mode, None when
    no configuration is readable (the strictest reading); any other non-mode value raises AutonomyModeError (typed).
    ``containment_backend`` is recorded; under Option B it decides nothing (no backend proves a planned command's
    effect stays out of later evidence). Never raises for a tool: an unreadable declaration is TOOL_GOVERNANCE_INVALID."""
    mode = None if autonomy_mode is None else parse_autonomy_mode(autonomy_mode)

    def decide(governance: Optional[ToolGovernance], reason_code: Optional[str], explanation: str) -> ToolGovernanceDecision:
        return ToolGovernanceDecision(plannable=reason_code is None, governance=governance, reason_code=reason_code,
                                      autonomy_mode=mode, containment_backend=containment_backend,
                                      explanation=explanation)

    if not isinstance(tool, BaseTool):
        return decide(None, TOOL_NOT_GOVERNED, f"{type(tool).__name__} is not a Kriya tool.")
    if not _shipped_declaration(tool):
        return decide(None, TOOL_MODEL_AUTHORITY_UNDECLARED,
                      f"tool {type(tool).__name__!r} has no honoured planned-call declaration (only Kriya's shipped "
                      "tools and MCP tools can be planned); it can be run by an operator, never by a planned step.")
    try:
        declared = tool.planned_governance
    except Exception as error:  # a broken declaration is refused, never guessed
        return decide(None, TOOL_GOVERNANCE_INVALID, f"its planned-call declaration could not be read: {error}")
    if declared is None:
        return decide(None, TOOL_MODEL_AUTHORITY_UNDECLARED, "it declares no planned-call governance.")
    if type(declared) is not ToolGovernance:
        return decide(None, TOOL_GOVERNANCE_INVALID, "its planned-call declaration is not a ToolGovernance value.")
    if declared not in PLANNABLE_TOOL_GOVERNANCE:
        return decide(declared, TOOL_OPERATOR_ONLY,
                      "it runs commands or builds whose effects Kriya cannot keep out of the run's evidence, so only "
                      "an operator may run it (`kriya tools execute`); a planned step never does.")
    return decide(declared, None, "plannable")


async def execute_model_authored(tool: Any, arguments: Mapping[str, Any], invocation: ModelToolInvocation) -> Any:
    """The one entry point for a MODEL-AUTHORED tool call: validate, authorize (the tool's own declaration, every mode),
    then run. Raises ModelInvocationRefused (TOOLBOX refusal), or ToolExecutionError whose ``__cause__`` is the reused
    authority's PolicyDeniedError, before anything runs; failures while running are wrapped exactly as
    ``BaseTool.execute`` wraps them."""
    if not isinstance(tool, BaseTool):
        raise ModelInvocationRefused(
            TOOL_NOT_GOVERNED, f"{type(tool).__name__} is not a Kriya tool; a planned step reaches tools only through "
                               "the governed tool boundary.")
    # S18: the executor's recheck of the one governance resolution, before arguments are even validated.
    decision = resolve_planned_tool_governance(tool, autonomy_mode=invocation.autonomy_mode)
    if not decision.plannable:
        raise ModelInvocationRefused(
            decision.reason_code, f"tool {tool.name!r} cannot be run by a planned step: {decision.explanation} "
                                  "Nothing was run.")
    try:
        validated = tool.arguments_schema(**dict(arguments))
    except ValidationError as ve:
        logger.error(f"Validation failed for tool '{tool.name}': {ve}")
        raise ToolExecutionError(f"Argument validation failed: {ve.errors()}") from ve
    try:
        authorized = tool.authorize_model_invocation(validated, invocation)
    except ToolExecutionError:
        raise
    except Exception as e:  # the reused authority's denial (PolicyDeniedError), or a broken check: refused either way
        logger.warning(f"Model-authored call to tool '{tool.name}' refused: {e}")
        raise ToolExecutionError(f"Tool call refused: {e}") from e
    try:
        logger.info(f"Executing tool '{tool.name}' (model-authored) with arguments: {authorized.model_dump()}")
        return await tool._run_model_authored(authorized, invocation)
    except Exception as e:
        logger.error(f"Error during execution of tool '{tool.name}': {e}", exc_info=True)
        raise ToolExecutionError(f"Tool execution failed: {e}") from e
