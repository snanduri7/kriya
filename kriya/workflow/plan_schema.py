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


_GLOB_WILDCARD_CHARS = frozenset("*?[")


def _non_blank_relative_path(path: str, *, label: str) -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        raise ValueError(f"{label} must not be blank")
    if cleaned.startswith("/") or cleaned.startswith("\\") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise ValueError(f"{label} must be workspace-relative, not absolute: {cleaned!r}")
    if ".." in cleaned.replace("\\", "/").split("/"):
        raise ValueError(f"{label} must not contain '..' path-traversal segments: {cleaned!r}")
    # Found live, PRV-05 (2026-08-28): the Planner returned
    # "src/main/java/**/*.java" as a planned_files[].path - a glob pattern,
    # not a real file. Nothing here rejected it, so every downstream
    # consumer (Developer generation, the write loop, the compile gate)
    # treated the literal string "**/*.java" as an actual filename for the
    # rest of the run, writing real Java source to a file literally named
    # that on disk - every retry failed identically ("class X is public,
    # should be declared in a file named X.java") since javac can never
    # resolve a wildcard as a class-name-matching path, and the model's own
    # (correct) diagnosis had nowhere to go: it's never asked to name a real
    # file. A PlannedFile names exactly ONE concrete file; a subtask that
    # genuinely needs to touch every file in a directory must enumerate
    # them, not describe them with a pattern - matching this model's own
    # docstring ("this model only enforces the path SHAPE"), a wildcard is
    # a shape defect, not a repo-reality one, so it belongs here rather than
    # in plan_validation.py's cross-field checks.
    if any(char in cleaned for char in _GLOB_WILDCARD_CHARS):
        raise ValueError(
            f"{label} must name one concrete file, not a glob/wildcard pattern: {cleaned!r}"
        )
    return cleaned


class ExecutionMethod(str, Enum):
    """How a Subtask gets carried out. TOOL subtasks never invoke the
    Developer/LLM at all (MA6 invariant 3) - SubtaskExecutor runs the named
    tool directly via the kernel's ComponentRegistry("tool", ...)."""

    MODEL = "model"
    TOOL = "tool"


class ExecutionRole(str, Enum):
    """WHAT a Subtask is for, orthogonal to execution_method (HOW it runs).
    Found live, PRV-05 (2026-08-28): enforce mode forbids execution_method=
    tool entirely (TOOL_SUBTASK_UNSUPPORTED_IN_ENFORCE) while also requiring
    every execution_method=model subtask to declare planned_files
    (MODEL_SUBTASK_MISSING_PLANNED_FILES) - leaving no legal shape for a
    genuinely non-mutating regression/runtime verification step. The
    Planner's own repeated plan (3 attempts, byte-identical) was not a bad
    plan; the schema had no category for what it was correctly trying to
    express (see this field's own validators on Subtask for the concrete
    shape a VERIFICATION-role subtask must take).

    Deliberately an explicit field the Planner sets, not something inferred
    from planned_files being empty - an inferred signal encodes intent
    indirectly and makes future plan validation harder to reason about (the
    same reasoning already applied to VerificationMethod.verifier_kind's own
    "explicit semantic contract; avoids inferring runtime authority from a
    technology, filename, or wording heuristic" design)."""

    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"


class FileOwnershipRelation(str, Enum):
    """Where a file sits relative to a given subtask in the validated plan's
    dependency DAG - PRV-05 (2026-08-28)'s answer to "who owns this file,
    and are we there yet", shared by EngineeringPlan.classify_file_ownership()
    below. UNOWNED (no subtask in the plan declares the path at all) is
    deliberately distinct from UNRELATED (owned, but by a subtask with no
    dependency-ordering relationship to the one asking) - an UNOWNED file
    can never legitimately become PENDING (nothing in the plan will ever
    "reach" it), while an UNRELATED one is a real plan-scope conflict, not a
    timing question."""

    CURRENT = "current"
    FUTURE_ORDERED = "future_ordered"
    PAST_ORDERED = "past_ordered"
    UNRELATED = "unrelated"
    UNOWNED = "unowned"


class VerificationMethodType(str, Enum):
    """TOOL verification is deterministic (compile/test/lint/a registered
    validator). JUDGMENT is anything that can't be deterministically
    checked - MA6.11 routes an unresolved JUDGMENT criterion to
    NEEDS_REVIEW rather than letting the Implementer self-grade it."""

    TOOL = "tool"
    JUDGMENT = "judgment"


class VerifierKind(str, Enum):
    COMPILE = "compile"
    TEST = "test"
    APPLICATION_RUNTIME = "application_runtime"
    COMMAND = "command"
    JUDGMENT = "judgment"


BUILTIN_QUALITY_GATE_VERIFIERS = frozenset({
    "quality_gates", "compile", "test", "tests", "regression",
})


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
    verifier_kind: Optional[VerifierKind] = None
    # Explicit semantic contract; avoids inferring runtime authority from a
    # technology, filename, or wording heuristic.
    requires_runtime_execution: bool = False

    @model_validator(mode="after")
    def _tool_name_matches_type(self) -> "VerificationMethod":
        if self.type == VerificationMethodType.TOOL and not self.tool_name:
            raise ValueError("verification method type=tool requires a tool_name")
        if self.type == VerificationMethodType.JUDGMENT and self.tool_name:
            raise ValueError(
                "verification method type=judgment must not set tool_name - nothing deterministic runs it"
            )
        inferred = {
            "compile": VerifierKind.COMPILE,
            "test": VerifierKind.TEST,
            "tests": VerifierKind.TEST,
            "regression": VerifierKind.TEST,
            "quality_gates": VerifierKind.COMPILE,
        }.get(self.tool_name or "")
        if self.verifier_kind is None:
            if self.type == VerificationMethodType.TOOL:
                # A registered tool outside the known compile/test set (a
                # custom health-check, deploy-smoke-test, etc.) still runs
                # something concrete - default it to COMMAND rather than
                # None, or requires_application_runtime would silently
                # drop an explicit requires_runtime_execution=True for
                # every non-builtin tool.
                self.verifier_kind = inferred if inferred is not None else VerifierKind.COMMAND
            else:
                self.verifier_kind = VerifierKind.JUDGMENT
        if inferred is not None and self.verifier_kind is not inferred:
            raise ValueError(
                f"tool_name={self.tool_name} requires verifier_kind={inferred.value}"
            )
        return self

    @property
    def requires_application_runtime(self) -> bool:
        return bool(
            self.requires_runtime_execution
            and self.verifier_kind in (
                VerifierKind.APPLICATION_RUNTIME,
                VerifierKind.COMMAND,
                VerifierKind.JUDGMENT,
            )
        )


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


class GlobalInvariant(BaseModel):
    """One plan-wide constraint, with a stable id a Subtask references
    instead of restating the free-text statement. PRV-06 (2026-08-28, real
    live-validation finding): before this type existed, global_invariants
    was List[str] and Subtask.relevant_global_invariants was ALSO List[str],
    with plan_validation.py checking literal set membership between the
    two - i.e. natural language was doing double duty as both MEANING and
    IDENTITY. A compound invariant ("...retrieve the value from that
    service and print it.") naturally decomposes into narrower per-subtask
    statements ("...retrieve the value from that service.") that are
    semantically correct but never byte-identical to the original sentence,
    so the exact-match check failed and stayed failed across two full
    bounded repair rounds (the model was never told verbatim reuse was the
    contract, and had no other way to satisfy it) - a total run failure on
    an otherwise well-decomposed plan. Splitting statement (what the LLM
    authors and reasons about) from id (what the validator checks
    referential integrity against) removes that failure mode without
    fuzzy/substring/semantic matching - referencing an id is exactly as
    reliable as the existing provides/requires slug contract already is
    (zero mismatches across the same live run that broke on invariant
    text)."""

    id: str
    statement: str

    @field_validator("id")
    @classmethod
    def _non_blank_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("global invariant id must not be blank")
        return v

    @field_validator("statement")
    @classmethod
    def _non_blank_statement(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("global invariant statement must not be blank")
        return v


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
    # Defaults to IMPLEMENTATION - every plan/test/checkpoint that predates
    # this field (2026-08-28) deserializes as an ordinary implementation
    # subtask, unchanged behavior, no migration needed.
    execution_role: ExecutionRole = ExecutionRole.IMPLEMENTATION
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    planned_files: List[PlannedFile] = Field(default_factory=list)
    acceptance_criteria_ids: List[str] = Field(default_factory=list)
    verification: List[VerificationMethod] = Field(default_factory=list)
    provides: List[str] = Field(default_factory=list)
    requires: List[str] = Field(default_factory=list)
    # References GlobalInvariant.id, never restated invariant text - see
    # GlobalInvariant's own docstring for why (PRV-06).
    relevant_global_invariant_ids: List[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _non_blank_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("subtask id must not be blank")
        return v

    @field_validator("provides", "requires", "relevant_global_invariant_ids")
    @classmethod
    def _normalize_semantic_entries(cls, values: List[str]) -> List[str]:
        return [(value or "").strip() for value in values]

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
        if self.execution_role == ExecutionRole.VERIFICATION:
            # Mirrors PRV-04's own EXISTING_CONTRACT_PRESERVATION reasoning at
            # the workflow-engine level ("verification must not alter
            # persistent application architecture") - here it's structural,
            # enforced on the plan itself before anything runs: a
            # verification-role subtask requires zero writable files (forbid
            # repository writes) and at least one concrete verifier
            # (bounded verifier commands/evidence, not just a description).
            if self.planned_files:
                raise ValueError(
                    f"subtask {self.id!r} has execution_role=verification but declares "
                    f"planned_files {[pf.path for pf in self.planned_files]!r} - a verification-only "
                    "subtask must never own writable files; give it execution_role=implementation "
                    "instead if it genuinely edits files"
                )
            if not self.verification:
                raise ValueError(
                    f"subtask {self.id!r} has execution_role=verification but declares no "
                    "verification entries - a verification-only subtask must name at least one "
                    "concrete verifier (compile/test/application_runtime/judgment), not just a "
                    "description"
                )
        if self.id in self.depends_on:
            raise ValueError(f"subtask {self.id!r} cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"subtask {self.id!r} lists a duplicate dependency in depends_on")
        for field_name in ("provides", "requires", "relevant_global_invariant_ids"):
            values = getattr(self, field_name)
            if any(not (value or "").strip() for value in values):
                raise ValueError(f"subtask {self.id!r} has a blank {field_name} entry")
            if len(set(values)) != len(values):
                raise ValueError(f"subtask {self.id!r} has duplicate {field_name} entries")
        overlap = sorted(set(self.provides) & set(self.requires))
        if overlap:
            raise ValueError(f"subtask {self.id!r} both provides and requires {overlap}")
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
    global_invariants: List[GlobalInvariant] = Field(default_factory=list)

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

    @field_validator("global_invariants")
    @classmethod
    def _valid_global_invariants(cls, values: List[GlobalInvariant]) -> List[GlobalInvariant]:
        ids = [gi.id for gi in values]
        if len(set(ids)) != len(ids):
            raise ValueError("global_invariants must not contain duplicate ids")
        return values

    def subtask_by_id(self, subtask_id: str) -> Optional[Subtask]:
        for subtask in self.subtasks:
            if subtask.id == subtask_id:
                return subtask
        return None

    def file_owner(self, path: str) -> Optional[Subtask]:
        owners = [st for st in self.subtasks if any(pf.path == path for pf in st.planned_files)]
        return owners[0] if len(owners) == 1 else None

    def _transitive_dependency_ids(self, subtask_id: str) -> "set[str]":
        """Every OTHER subtask id `subtask_id` depends on, directly or
        transitively (own id never included). Assumes the graph is already
        known-acyclic (plan_validation.validate_plan() rejects cycles before
        a plan is execution-authorized - see this module's own docstring) -
        a cycle would recurse forever without the visited-guard below."""
        by_id = {st.id: st for st in self.subtasks}
        visited: "set[str]" = set()

        def resolve(sid: str) -> "set[str]":
            if sid in visited:
                return set()
            visited.add(sid)
            result: "set[str]" = set()
            for dep in by_id[sid].depends_on if sid in by_id else []:
                if dep not in by_id:
                    continue
                result.add(dep)
                result |= resolve(dep)
            return result

        return resolve(subtask_id)

    def classify_file_ownership(
        self, current_subtask_id: str, path: str,
    ) -> "FileOwnershipRelation":
        """Where `path` sits relative to `current_subtask_id` in the
        validated plan's dependency DAG - PRV-05 (2026-08-28)'s ownership
        helper, shared by stage-aware migration validation
        (kriya/workflow/migration.py) and retry attribution alike, so both
        answer "who owns this file, and are we there yet" the same way.

        Deliberately re-derives the owner set directly from planned_files
        rather than calling file_owner() above - a file legitimately owned
        by TWO OR MORE subtasks in a validated, dependency-ordered sequential
        chain (plan_validation.py's _forms_sequential_ownership_chain, e.g.
        this exact PRV-05 plan's JsonService.java: both s1 and s2 declare
        it) is not "ambiguous" here the way file_owner()'s single-owner-only
        lookup treats it - CURRENT must fire whenever current_subtask_id is
        ANY one of the (validated-sequential) co-owners, not just when it's
        the sole owner."""
        owners = [st.id for st in self.subtasks if any(pf.path == path for pf in st.planned_files)]
        if not owners:
            return FileOwnershipRelation.UNOWNED
        if current_subtask_id in owners:
            return FileOwnershipRelation.CURRENT
        current_deps = self._transitive_dependency_ids(current_subtask_id)
        if any(owner in current_deps for owner in owners):
            return FileOwnershipRelation.PAST_ORDERED
        if all(current_subtask_id in self._transitive_dependency_ids(owner) for owner in owners):
            return FileOwnershipRelation.FUTURE_ORDERED
        return FileOwnershipRelation.UNRELATED

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


class PlannerStructuredOutput(BaseModel):
    """MA6.3 Stage A - the JSON block PlannerAgent (kriya/agents/agent.py)
    is asked to emit ALONGSIDE its existing prose plan, extracted by
    kriya.agents.contracts.parse_planner_structured_output(). Deliberately
    narrower than EngineeringPlan itself: plan_id (generated per run) and
    kind (already deterministically classified by EngineeringTriageService,
    kriya/workflow/triage.py - never re-asked of the model, the same
    "never asked of a model" discipline triage.py's own ImpactVector
    already follows) are supplied by the CALLER, via
    build_engineering_plan_from_planner_output() below, not requested
    here.

    Stage A only PARSES this - Kriya still executes off the existing prose
    plan path (MA6 spec section 40: "do not jump directly to Stage C with
    a local model"). A malformed or missing structured block must never
    break generation; every extraction failure degrades to "no structured
    plan available," identical to today's prose-only behavior - see
    parse_planner_structured_output's own contract."""

    subtasks: List[Subtask] = Field(default_factory=list)
    acceptance_criteria: List[AcceptanceCriterion] = Field(default_factory=list)
    extension_points: List[str] = Field(default_factory=list)
    refactor_baseline: Optional[str] = None
    global_invariants: List[GlobalInvariant] = Field(default_factory=list)


def build_engineering_plan_from_planner_output(
    output: PlannerStructuredOutput, *, plan_id: str, kind: ChangeKind
) -> Optional[EngineeringPlan]:
    """None (never raises) when output.subtasks is empty - EngineeringPlan
    requires at least one subtask; a Stage A response with zero subtasks
    (a model that only produced prose, or an empty/degenerate JSON block)
    simply has no structured plan to build yet, the same "degrade, don't
    break" contract as every extraction step in this pipeline. The result,
    even when non-None, is NOT execution-authorized - still must pass
    through plan_validation.validate_plan() (MA6.2) before anything acts
    on it, same as any other EngineeringPlan."""
    if not output.subtasks:
        return None
    return EngineeringPlan(
        plan_id=plan_id, kind=kind, subtasks=output.subtasks,
        acceptance_criteria=output.acceptance_criteria, extension_points=output.extension_points,
        refactor_baseline=output.refactor_baseline, global_invariants=output.global_invariants,
    )
