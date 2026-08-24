"""EngineeringPlan / Subtask schema - MA6.1 of the Milestone Agent (MA6)
structured-execution implementation plan (see kriya/control/__init__.py for
MA5's own principle statement; this is the analogous statement for MA6:
Planner output must be a validated, executable DATA STRUCTURE before
anything touches it, never prose parsed ad hoc downstream).

Pydantic models, following kriya/agents/contracts.py's existing precedent
for schemas something ELSE parses programmatically, not just prose a
human/the Reviewer reads. Deliberately narrow: this module defines SHAPE
and local, single-field validation only. Cross-field/whole-plan validation
(acyclic dependency graph, all referenced files exist-or-marked-new,
acceptance coverage, tool_name resolves to a real registered tool,
ImpactVector recomputation, ...) is kriya/workflow/plan_validation.py's job
(MA6.2), not this module's - an EngineeringPlan built here is syntactically
well-formed, NOT yet execution-authorized. SubtaskExecutor (MA6.5) must
never run against a plan that hasn't been through plan_validation first.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from kriya.workflow.triage import ChangeKind


def _non_blank_relative_path(path: str, *, label: str) -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        raise ValueError(f"{label} must not be blank")
    if cleaned.startswith("/") or cleaned.startswith("\\") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise ValueError(f"{label} must be workspace-relative, not absolute: {cleaned!r}")
    if ".." in cleaned.replace("\\", "/").split("/"):
        raise ValueError(f"{label} must not contain '..' path-traversal segments: {cleaned!r}")
    return cleaned


class ExecutionMethod(str, Enum):
    """How a Subtask gets carried out. TOOL subtasks never invoke the
    Developer/LLM at all (MA6 invariant 3) - SubtaskExecutor runs the named
    tool directly via the kernel's ComponentRegistry("tool", ...)."""

    MODEL = "model"
    TOOL = "tool"


class VerificationMethodType(str, Enum):
    """TOOL verification is deterministic (compile/test/lint/a registered
    validator). JUDGMENT is anything that can't be deterministically
    checked - MA6.11 routes an unresolved JUDGMENT criterion to
    NEEDS_REVIEW rather than letting the Implementer self-grade it."""

    TOOL = "tool"
    JUDGMENT = "judgment"


class FileAction(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class PlannedFile(BaseModel):
    """One file a Subtask declares it will touch. plan_validation.py
    (MA6.2) checks this against real repo state: `action=modify`/`delete`
    files must already exist, `action=create` files must not (or must be
    explicitly re-marked if they do) - this model only enforces the path
    shape itself, not repo reality."""

    path: str
    action: FileAction
    reason: str = ""

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _non_blank_relative_path(v, label="planned file path")


class VerificationMethod(BaseModel):
    """A concrete check a Subtask (or the plan's global acceptance
    criteria) is verified by. tool_name is required exactly when
    type=tool, and meaningless otherwise - see the model_validator below.
    Whether tool_name actually resolves to a REGISTERED tool is
    plan_validation.py's job (MA6.2), not this class's."""

    type: VerificationMethodType
    description: str
    tool_name: Optional[str] = None

    @model_validator(mode="after")
    def _tool_name_matches_type(self) -> "VerificationMethod":
        if self.type == VerificationMethodType.TOOL and not self.tool_name:
            raise ValueError("verification method type=tool requires a tool_name")
        if self.type == VerificationMethodType.JUDGMENT and self.tool_name:
            raise ValueError(
                "verification method type=judgment must not set tool_name - nothing deterministic runs it"
            )
        return self


class AcceptanceCriterion(BaseModel):
    """One item of the plan's GLOBAL acceptance surface (as distinct from a
    single Subtask's own `verification` list) - plan_validation.py's
    "acceptance coverage is complete" check (MA6.2) confirms every id here
    is actually addressed by at least one subtask before the plan is
    considered executable."""

    id: str
    description: str
    method: VerificationMethodType = VerificationMethodType.JUDGMENT
    tool_name: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _non_blank_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("acceptance criterion id must not be blank")
        return v

    @model_validator(mode="after")
    def _tool_name_matches_method(self) -> "AcceptanceCriterion":
        if self.method == VerificationMethodType.TOOL and not self.tool_name:
            raise ValueError(f"acceptance criterion {self.id!r} has method=tool but no tool_name")
        if self.method == VerificationMethodType.JUDGMENT and self.tool_name:
            raise ValueError(f"acceptance criterion {self.id!r} has method=judgment but sets tool_name")
        return self


class Subtask(BaseModel):
    """One execution unit within an EngineeringPlan - MA6 invariant 2: the
    Developer/tool receives exactly one of these at a time, never the
    whole plan. `depends_on` names other Subtask ids in the SAME plan;
    plan_validation.py (MA6.2) is what actually confirms those ids exist
    and the resulting dependency graph is acyclic - this class only
    enforces that a subtask doesn't trivially depend on itself.

    tool_arguments is passed straight through as `tool.execute(**tool_arguments)`
    (kriya/tools/tool.py::BaseTool.execute already validates kwargs against
    the tool's own arguments_schema and raises ToolExecutionError on a
    mismatch) - only meaningful, and only ever non-empty, when
    execution_method=tool."""

    id: str
    description: str
    execution_method: ExecutionMethod
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    planned_files: List[PlannedFile] = Field(default_factory=list)
    acceptance_criteria_ids: List[str] = Field(default_factory=list)
    verification: List[VerificationMethod] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _non_blank_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("subtask id must not be blank")
        return v

    @model_validator(mode="after")
    def _execution_method_invariants(self) -> "Subtask":
        if self.execution_method == ExecutionMethod.TOOL and not self.tool_name:
            raise ValueError(f"subtask {self.id!r} has execution_method=tool but no tool_name")
        if self.execution_method == ExecutionMethod.MODEL and self.tool_name:
            raise ValueError(
                f"subtask {self.id!r} has execution_method=model but sets tool_name - "
                "tool_name only applies to execution_method=tool"
            )
        if self.execution_method == ExecutionMethod.MODEL and self.tool_arguments:
            raise ValueError(
                f"subtask {self.id!r} has execution_method=model but sets tool_arguments - "
                "tool_arguments only applies to execution_method=tool"
            )
        if self.id in self.depends_on:
            raise ValueError(f"subtask {self.id!r} cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"subtask {self.id!r} lists a duplicate dependency in depends_on")
        return self


class EngineeringPlan(BaseModel):
    """The Planner's full structured output for one generation/milestone
    request (MA6.3 Stage A: produced ALONGSIDE the existing prose plan,
    not yet the sole execution path). Not itself execution-authorized -
    always pass through plan_validation.validate_plan() (MA6.2) before a
    WorkflowController/SubtaskExecutor touches it (MA6 invariant 1)."""

    plan_id: str
    kind: ChangeKind
    subtasks: List[Subtask]
    acceptance_criteria: List[AcceptanceCriterion] = Field(default_factory=list)
    extension_points: List[str] = Field(default_factory=list)
    refactor_baseline: Optional[str] = None

    @field_validator("plan_id")
    @classmethod
    def _non_blank_plan_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("plan_id must not be blank")
        return v

    @field_validator("subtasks")
    @classmethod
    def _non_empty_subtasks(cls, v: List[Subtask]) -> List[Subtask]:
        if not v:
            raise ValueError("plan must contain at least one subtask")
        return v

    def subtask_by_id(self, subtask_id: str) -> Optional[Subtask]:
        for subtask in self.subtasks:
            if subtask.id == subtask_id:
                return subtask
        return None

    def content_hash(self) -> str:
        """Stable sha256 over the plan's full validated content - MA6.7's
        per-subtask checkpoint record's plan_hash field, and MA6.13's
        shadow-mode comparison, both key off this. Unlike
        kriya.control.state.ControlState.content_hash there are no
        timestamps to exclude: an EngineeringPlan is immutable and
        hash-addressed once validated (MA6 invariant 9), so ANY field
        change is meant to produce a different hash."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
