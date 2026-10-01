"""PLANNER-ROBUST-001 P2/P3 (2026-09-19): the ONE canonical semantic check
for "does this Subtask's/verification's/acceptance criterion's own
tool_name resolve to a REAL, currently Planner-visible registered Kriya
tool" - extracted from plan_validation.py::validate_plan() (which already
held the most complete production semantics for this check: subtask-level
tool_name, verification[].tool_name with the BUILTIN_QUALITY_GATE_VERIFIERS
exemption, AND acceptance_criteria[].tool_name with the same exemption) so
BOTH WorkflowController's `validate_plan()` and the legacy
`run_generation_workflow()`'s own `classify_plan_completeness()` consult
the exact same membership semantics - "ONE PLAN CONTRACT -> ONE VALIDATION
SEMANTICS -> MULTIPLE ORCHESTRATORS".

Deliberately narrow and dependency-free: this module imports ONLY from
kriya.workflow.plan_schema (ExecutionMethod, VerificationMethodType,
BUILTIN_QUALITY_GATE_VERIFIERS) - nothing from workflow.py,
workflow_controller.py, file_resolution.py, or the kernel/registry
directly, so both existing owners can import this module with zero risk of
a circular import, and it never reaches into global Kernel state itself -
`available_tool_names` is always an explicit, caller-supplied snapshot
(see `validate_tool_capability_membership`'s own docstring for the
snapshot/TOCTOU invariant this relies on).

This module does NOT own: schema/Pydantic validation (plan_schema.py),
path authority (also plan_schema.py, inside PlannedFile's own field
validator), dependency-graph/ownership/semantic-contract/brownfield
checks (all WorkflowController-specific orchestration concerns that stay
in plan_validation.py's own validate_plan(), never dragged into the
legacy path). It owns exactly one thing: tool-name registry membership."""
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from kriya.workflow.plan_schema import (
    BUILTIN_QUALITY_GATE_VERIFIERS,
    EngineeringPlan,
    ExecutionMethod,
    VerificationMethodType,
)

# The one, shared reason code both validate_plan() (WorkflowController path)
# and classify_plan_completeness() (legacy path) attach when this check
# fails - see each caller's own comment for how it flows into their
# respective repair-guidance/terminal-classification vocabulary.
UNREGISTERED_TOOL_NAME_REASON_CODE = "UNREGISTERED_TOOL_NAME"


@dataclass
class ToolCapabilityValidationResult:
    """valid=False means at least one execution_method=tool Subtask.
    tool_name, verification[].tool_name, or acceptance_criteria[].
    tool_name names something outside available_tool_names (and, for the
    latter two, also outside BUILTIN_QUALITY_GATE_VERIFIERS - a plan is
    always free to reference the compile/test quality gates by name
    without those needing to be "registered" tools). errors are the same
    human-readable strings validate_plan() has always produced for this
    check (preserved byte-for-byte so existing test/log expectations that
    already assert this exact wording keep working); reason_codes is
    UNREGISTERED_TOOL_NAME_REASON_CODE.py exactly when errors is non-
    empty, never a per-violation code - one concept, one code, matching
    every other structured-plan reason code's own granularity."""

    valid: bool
    errors: List[str] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)


def validate_tool_capability_membership(
    plan: EngineeringPlan,
    *,
    available_tool_names: Optional[Iterable[str]],
) -> ToolCapabilityValidationResult:
    """available_tool_names=None SKIPS this check entirely (mirrors
    validate_plan()'s own pre-existing convention for this exact
    parameter - "only safe for contexts guaranteed not to contain
    TOOL-tagged subtasks"). available_tool_names=[] (a real, empty
    registry) is NOT the same as None - it runs the check for real and
    correctly fails every execution_method=tool subtask, since an empty
    catalog can authorize nothing. This distinction is deliberate and
    tested (PLANNER-ROBUST-001 test S): a caller must pass the real
    snapshot it has, never omit the argument merely because the registry
    happens to be empty right now.

    Membership is checked case-insensitively (`.lower()` on both sides),
    matching kriya/core/registry.py::ComponentRegistry.register()/get()'s
    own normalization exactly - a Subtask.tool_name that would actually
    resolve at real dispatch time (TOOL-001's `kernel.registry.get("tool",
    subtask.tool_name)`) must never be rejected here merely for a case
    difference; validation and execution must never diverge on this.

    Callers own the ONE registry read (kernel.registry.list_components
    ("tool")) per planning operation and pass the resulting collection
    in - this function never queries a registry itself, so it cannot
    itself cause a second, potentially-differently-timed read (the
    TOCTOU/snapshot concern P3 raises). O(subtasks + verification items +
    acceptance criteria) with an O(1) average-case set membership test
    per name - never a per-subtask registry re-scan, never repository-
    sized work."""
    if available_tool_names is None:
        return ToolCapabilityValidationResult(valid=True)

    available = {name.lower() for name in available_tool_names}
    errors: List[str] = []
    for st in plan.subtasks:
        if st.execution_method == ExecutionMethod.TOOL and (st.tool_name or "").lower() not in available:
            errors.append(f"subtask {st.id!r} references unregistered tool_name {st.tool_name!r}")
        for vm in st.verification:
            if (
                vm.type == VerificationMethodType.TOOL
                and (vm.tool_name or "").lower() not in available
                and vm.tool_name not in BUILTIN_QUALITY_GATE_VERIFIERS
            ):
                errors.append(
                    f"subtask {st.id!r} verification references unregistered tool_name {vm.tool_name!r}"
                )
    for ac in plan.acceptance_criteria:
        if (
            ac.method == VerificationMethodType.TOOL
            and (ac.tool_name or "").lower() not in available
            and ac.tool_name not in BUILTIN_QUALITY_GATE_VERIFIERS
        ):
            errors.append(f"acceptance criterion {ac.id!r} references unregistered tool_name {ac.tool_name!r}")

    reason_codes = [UNREGISTERED_TOOL_NAME_REASON_CODE] if errors else []
    return ToolCapabilityValidationResult(valid=not errors, errors=errors, reason_codes=reason_codes)
