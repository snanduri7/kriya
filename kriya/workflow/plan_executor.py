"""PRD-008A: the one executor every ExecutionPlan runs through.

    ExecutionPlan -> execute_plan() -> for each WorkUnit: the existing
    generation primitive (WorkflowEngine.run_generation_workflow)

execute_plan() owns what must be identical for a direct goal and a
milestone sequence: execution order, the WorkUnit lifecycle, the active
work-unit record (which attributes every commit cycle and checkpoint),
dependency blocking, plan-wide phases, and the terminal decision. It never
generates, verifies, commits or recovers anything itself - those stay in
run_generation_workflow / run_attempt and the PRD-004/005/008 machinery the
primitive already calls, under the RunCoordinator ownership its caller holds.

A PlanDriver supplies what is genuinely source-specific (how a milestone's
goal text and context are assembled, its artifact/contract bookkeeping, its
failure callback). A driver hook never decides safety: it cannot mark a
unit VERIFIED, skip a unit, or declare the plan successful.
"""
from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from kriya.control.run_coordinator import annotate_run, require_mutating_run
from kriya.workflow.execution_plan import (
    ExecutionPlan,
    PlanSourceKind,
    TerminalPhase,
    WorkUnit,
    WorkUnitRole,
    WorkUnitState,
    WorkUnitStatus,
)
from kriya.workflow.plan_adapters import DIRECT_WORK_UNIT_KIND, direct_execution_plan, work_unit_record

logger = logging.getLogger(__name__)

# WorkUnit lifecycle reason codes.
COMPLETION_REUSED = "COMPLETION_REUSED"
WORK_UNIT_FAILED = "WORK_UNIT_FAILED"
WORK_UNIT_PRECONDITION_FAILED = "WORK_UNIT_PRECONDITION_FAILED"
WORK_UNIT_EXCEPTION = "WORK_UNIT_EXCEPTION"
DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
PLAN_HALTED_AFTER_FAILURE = "PLAN_HALTED_AFTER_FAILURE"
TERMINAL_PHASE_FAILED = "TERMINAL_PHASE_FAILED"
PLAN_TERMINAL_MISMATCH = "PLAN_TERMINAL_MISMATCH"
COMPLETION_REUSE_INCONSISTENT = "COMPLETION_REUSE_INCONSISTENT"
STALE_COMPLETION = "STALE_COMPLETION"

# Checkpoint selection events (PRD-008 S4c values, kept: persisted sidecar
# assessments and RunRecords already carry them; the names are historical -
# they apply to every work unit).
CHECKPOINT_SELECTED = "MILESTONE_CHECKPOINT_SELECTED"
NO_COMPATIBLE_CHECKPOINT = "NO_COMPATIBLE_MILESTONE_CHECKPOINT"
CHECKPOINT_IDENTITY_MISMATCH = "CHECKPOINT_IDENTITY_MISMATCH"

_ACTIVE_PLAN: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("kriya_active_plan", default=None)


class NestedExecutionPlanError(RuntimeError):
    """A plan was started inside another plan's unit. A unit invocation
    must carry its WorkUnitInvocation; wrapping it in a second (direct)
    plan would overwrite the active unit record and misattribute commits."""


@dataclass(frozen=True)
class WorkUnitInvocation:
    """Passed to run_generation_workflow(work_unit=...) by whatever executes
    a unit of an outer plan: "run exactly this unit", never "this is a new
    user intent - wrap it in a direct plan". STRUCTURED subtasks carry one
    too (their plan is not yet executed by execute_plan; see handover)."""

    source_kind: PlanSourceKind
    plan_id: str
    work_unit_id: str
    plan_fingerprint: Optional[str] = None

    @classmethod
    def for_unit(cls, plan: ExecutionPlan, unit: WorkUnit) -> "WorkUnitInvocation":
        return cls(plan.source_kind, plan.plan_id, unit.id, plan.fingerprint)


class PlanDriver:
    """Source-specific behaviour around the common executor. Defaults are
    the direct (one plain unit) behaviour."""

    async def before_unit(self, plan: ExecutionPlan, unit: WorkUnit) -> Optional[Dict[str, Any]]:
        """Preconditions and bookkeeping before a unit runs. A returned
        result stops the plan with that result; the unit never runs."""
        return None

    def on_checkpoint_selection(self, event: Dict[str, Any]) -> None:
        """Record which checkpoint (if any) was offered to a unit. The
        selection itself is common (select_work_unit_checkpoint)."""
        logger.info("Checkpoint selection: %s", event)

    async def run_unit(
        self, plan: ExecutionPlan, unit: WorkUnit, invocation: WorkUnitInvocation,
        *, resume: bool, resume_id: Optional[str],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def check_passed_unit(self, unit: WorkUnit, result: Dict[str, Any]) -> Dict[str, Any]:
        """A check after the unit's own gates passed; may turn it into a
        failure (never the other way round - the executor re-reads
        quality_gates_passed from what this returns)."""
        return result

    def retry_failed_unit(self, unit: WorkUnit, result: Dict[str, Any]) -> bool:
        return False

    async def complete_unit(self, unit: WorkUnit, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Post-success bookkeeping. A returned result fails the unit and
        stops the plan with that result."""
        return None

    async def unit_failed(self, unit: WorkUnit, result: Dict[str, Any]) -> Dict[str, Any]:
        """The plan's result when ``unit`` failed."""
        return result

    async def run_phase(self, phase: TerminalPhase) -> Optional[Dict[str, Any]]:
        """Run a plan-wide phase; a returned result is its failure."""
        return None

    def plan_result(self, plan: ExecutionPlan, last_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The plan's result once every unit is VERIFIED."""
        return dict(last_result or {})


def _passed(result: Mapping[str, Any]) -> bool:
    return bool(result.get("quality_gates_passed"))


def _claims_success(result: Any) -> bool:
    return isinstance(result, Mapping) and (
        result.get("quality_gates_passed") is True or str(result.get("status", "")).lower() == "success"
    )


class _Lifecycle:
    """Every unit's WorkUnitState, mirrored onto the owning RunRecord."""

    def __init__(self, plan: ExecutionPlan, workspace_path: str):
        self.plan = plan
        self.workspace_path = workspace_path
        self.states: Dict[str, WorkUnitState] = {unit.id: WorkUnitState(unit.id) for unit in plan.work_units}
        self._publish(execution_plan=plan.to_dict())

    def status(self, unit_id: str) -> WorkUnitStatus:
        return self.states[unit_id].status

    def set(
        self, unit_id: str, status: WorkUnitStatus, reason_codes: Iterable[str] = (),
        blocked_by: Iterable[str] = (), *, publish: bool = True,
    ) -> None:
        self.states[unit_id] = WorkUnitState(unit_id, status, tuple(reason_codes), tuple(blocked_by))
        logger.info("Work unit '%s': %s %s", unit_id, status.value, ",".join(reason_codes))
        if publish:
            self._publish()

    def block_after(self, failed_id: str) -> None:
        """Everything not yet VERIFIED after a failure: dependents are
        BLOCKED by the failed dependency; independent units are BLOCKED
        because the plan halts (today's sequential stop-on-failure policy)."""
        dependents = set(self.plan.descendants(failed_id))
        for unit in self.plan.execution_order():
            if unit.id == failed_id or self.status(unit.id) in (WorkUnitStatus.VERIFIED, WorkUnitStatus.FAILED):
                continue
            code = DEPENDENCY_FAILED if unit.id in dependents else PLAN_HALTED_AFTER_FAILURE
            self.set(unit.id, WorkUnitStatus.BLOCKED, (code,), (failed_id,), publish=False)
        self._publish()

    def all_verified(self) -> bool:
        return all(state.status is WorkUnitStatus.VERIFIED for state in self.states.values())

    def _publish(self, **extra: Any) -> None:
        error = annotate_run(
            self.workspace_path,
            work_unit_states={unit_id: state.to_dict() for unit_id, state in self.states.items()},
            **extra,
        )
        if error:
            logger.warning(f"Could not record work-unit lifecycle on the run record: {error}")


def _set_active_unit(workspace_path: str, record: Optional[Dict[str, Any]]) -> None:
    """RunRecord.active_work_unit: begin_commit copies it into each commit
    cycle and every checkpoint saves it (PRD-008 S4c attribution)."""
    error = annotate_run(workspace_path, active_work_unit=record)
    if error:
        logger.warning(f"Could not record the active work unit on the run record: {error}")


_WORK_UNIT_IDENTITY_KEYS = ("kind", "group_id", "milestone_id", "definition_digest")


def same_work_unit(candidate: Any, record: Mapping[str, Any]) -> bool:
    """Whether a persisted work-unit record names ``record``'s unit."""
    return isinstance(candidate, Mapping) and all(
        candidate.get(key) == record.get(key) for key in _WORK_UNIT_IDENTITY_KEYS
    )


def _checkpoint_belongs_to(checkpoint: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    unit = checkpoint.get("work_unit")
    if unit is not None:
        return same_work_unit(unit, record)
    # Compatibility reader: a checkpoint saved before its unit was recorded.
    # One with no milestone group can only be a pre-PRD-008A direct run's;
    # a pre-S4c milestone checkpoint is never offered (S4c).
    return record.get("kind") == DIRECT_WORK_UNIT_KIND and not checkpoint.get("milestone_group_id")


def select_work_unit_checkpoint(
    workspace_path: str, record: Optional[Mapping[str, Any]], *,
    resume: bool, resume_id: Optional[str], single_unit_plan: bool = False,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """(resume, resume_id, event) for one unit's generation call.

    Selection is by work-unit identity only and never declares a checkpoint
    safe: the chosen one still goes through PRD-008's
    validate_resume_against_reality(). A bare ``resume=True`` is never
    passed on - that would let the primitive pick the newest checkpoint in
    the workspace, whichever unit made it.

    An explicit ``resume_id`` in a single-unit plan can only mean that unit,
    so it is passed through for PRD-008 to judge (a mismatch is its typed
    ``resume_refused``, never a silent fresh run). In a multi-unit plan the
    same id reaches every unit, so it is offered only to the unit it
    belongs to."""
    if not resume and not resume_id:
        return False, None, None
    if record is None:
        return resume, resume_id, None
    base = {
        "milestone_id": record.get("milestone_id"), "unit_kind": record.get("kind"),
        "work_unit_id": record.get("work_unit_id") or record.get("milestone_id"),
    }
    if resume_id and single_unit_plan:
        return False, resume_id, dict(base, code=CHECKPOINT_SELECTED, checkpoint=resume_id, explicit=True)
    from kriya.control.workspace_identity import WorkspaceOwnershipError
    from kriya.workflow.checkpoint import list_checkpoints

    try:
        checkpoints = list_checkpoints(workspace_path)
    except WorkspaceOwnershipError:
        # Another workspace's state in this one is never silently skipped.
        raise
    except Exception:
        checkpoints = []
    if resume_id:
        target = next((item for item in checkpoints if item.get("run_id") == resume_id), None)
        if target is not None and _checkpoint_belongs_to(target, record):
            return False, resume_id, dict(base, code=CHECKPOINT_SELECTED, checkpoint=resume_id, explicit=True)
        return False, None, dict(base, code=CHECKPOINT_IDENTITY_MISMATCH, checkpoint=resume_id, explicit=True)
    compatible = [item for item in checkpoints if _checkpoint_belongs_to(item, record)]
    if not compatible:
        logger.warning("No saved checkpoint found for this work unit - starting a fresh run instead.")
        return False, None, dict(base, code=NO_COMPATIBLE_CHECKPOINT, checkpoint=None, explicit=False)
    newest = max(compatible, key=lambda item: item.get("saved_at", 0))
    return False, newest["run_id"], dict(base, code=CHECKPOINT_SELECTED, checkpoint=newest["run_id"], explicit=False)


async def execute_plan(
    plan: ExecutionPlan, driver: PlanDriver, workspace_path: str, *,
    resume: bool = False, resume_id: Optional[str] = None,
    reusable_unit_ids: Iterable[str] = (), stale_unit_ids: Iterable[str] = (),
) -> Dict[str, Any]:
    """Execute ``plan`` under the caller's RunCoordinator ownership.

    ``reusable_unit_ids`` are units whose completion PRD-008 (S4b/S4c) has
    already validated from durable evidence; they are VERIFIED without
    running. Nothing else is ever skipped. ``stale_unit_ids`` are units whose
    earlier completion was invalidated; they start STALE and rerun."""
    if _ACTIVE_PLAN.get() is not None:
        raise NestedExecutionPlanError(
            f"plan {plan.plan_id!r} started inside plan {_ACTIVE_PLAN.get()!r}'s unit"
        )
    require_mutating_run(workspace_path)
    token = _ACTIVE_PLAN.set(plan.plan_id)
    try:
        return await _execute(
            plan, driver, workspace_path, resume, resume_id, set(reusable_unit_ids), set(stale_unit_ids),
        )
    finally:
        _ACTIVE_PLAN.reset(token)


async def _execute(
    plan: ExecutionPlan, driver: PlanDriver, workspace_path: str,
    resume: bool, resume_id: Optional[str], reusable: set, stale: set,
) -> Dict[str, Any]:
    lifecycle = _Lifecycle(plan, workspace_path)
    # A reused unit's inputs are its ancestors' outputs: reuse is coherent
    # only when every ancestor is reused too. PRD-008 S4b already reruns the
    # descendants of anything it reruns, so a disagreement here is a defect
    # in the evidence handed over - fail closed, never quietly rerun a unit
    # the durable state still records as complete.
    inconsistent = sorted(
        unit.id for unit in plan.work_units
        if unit.id in reusable and any(ancestor not in reusable for ancestor in plan.ancestors(unit.id))
    )
    if inconsistent:
        for unit_id in inconsistent:
            lifecycle.set(unit_id, WorkUnitStatus.STALE, (COMPLETION_REUSE_INCONSISTENT,), publish=False)
        lifecycle._publish()
        logger.error(f"Completed work units {inconsistent} are reusable but an ancestor is not - refusing.")
        return {
            "status": "needs_review",
            "quality_gates_passed": False,
            "reason_codes": [COMPLETION_REUSE_INCONSISTENT],
            "work_unit_ids": inconsistent,
        }
    for unit in plan.execution_order():
        if unit.id in reusable:
            lifecycle.set(unit.id, WorkUnitStatus.VERIFIED, (COMPLETION_REUSED,), publish=False)
        elif unit.id in stale:
            lifecycle.set(unit.id, WorkUnitStatus.STALE, (STALE_COMPLETION,), publish=False)
    lifecycle._publish()

    phases_done = False
    last_result: Optional[Dict[str, Any]] = None
    for unit in plan.execution_order():
        if unit.role is WorkUnitRole.INTEGRATION and not phases_done:
            phases_done = True
            failure = await _run_phases(plan, driver, lifecycle, unit)
            if failure is not None:
                return _terminal(lifecycle, failure)
        if lifecycle.status(unit.id) is WorkUnitStatus.VERIFIED:
            logger.info(f"Work unit '{unit.id}' already completed (resume) - skipping.")
            continue

        stop = await driver.before_unit(plan, unit)
        if stop is not None:
            lifecycle.set(unit.id, WorkUnitStatus.BLOCKED, (WORK_UNIT_PRECONDITION_FAILED, _status_code(stop)))
            lifecycle.block_after(unit.id)
            return _terminal(lifecycle, stop)

        record = work_unit_record(plan, unit.id)
        invocation = WorkUnitInvocation.for_unit(plan, unit)
        _set_active_unit(workspace_path, record)
        try:
            while True:
                call_resume, call_resume_id, selection = select_work_unit_checkpoint(
                    workspace_path, record, resume=resume, resume_id=resume_id,
                    single_unit_plan=len(plan.work_units) == 1,
                )
                if selection is not None:
                    driver.on_checkpoint_selection(selection)
                lifecycle.set(unit.id, WorkUnitStatus.RUNNING)
                result = await driver.run_unit(plan, unit, invocation, resume=call_resume, resume_id=call_resume_id)
                if _passed(result):
                    result = await driver.check_passed_unit(unit, result)
                    if _passed(result):
                        break
                if driver.retry_failed_unit(unit, result):
                    continue
                lifecycle.set(unit.id, WorkUnitStatus.FAILED, (WORK_UNIT_FAILED, _status_code(result)))
                lifecycle.block_after(unit.id)
                return _terminal(lifecycle, await driver.unit_failed(unit, result))

            failure = await driver.complete_unit(unit, result)
            if failure is not None:
                lifecycle.set(unit.id, WorkUnitStatus.FAILED, (WORK_UNIT_FAILED, _status_code(failure)))
                lifecycle.block_after(unit.id)
                return _terminal(lifecycle, failure)
            lifecycle.set(unit.id, WorkUnitStatus.VERIFIED)
            last_result = result
        except BaseException as error:
            if lifecycle.status(unit.id) is WorkUnitStatus.RUNNING:
                lifecycle.set(unit.id, WorkUnitStatus.FAILED, (WORK_UNIT_EXCEPTION, type(error).__name__))
                lifecycle.block_after(unit.id)
            raise
        finally:
            _set_active_unit(workspace_path, None)

    if not phases_done:
        failure = await _run_phases(plan, driver, lifecycle, None)
        if failure is not None:
            return _terminal(lifecycle, failure)
    return _terminal(lifecycle, driver.plan_result(plan, last_result))


async def _run_phases(
    plan: ExecutionPlan, driver: PlanDriver, lifecycle: _Lifecycle, gated_unit: Optional[WorkUnit],
) -> Optional[Dict[str, Any]]:
    """Plan-wide phases run once every PRIMARY unit is VERIFIED. A failure
    blocks the gated (integration) unit."""
    for phase in plan.terminal_phases:
        failure = await driver.run_phase(phase)
        if failure is not None:
            logger.warning(f"Plan phase '{phase.value}' failed: {_status_code(failure)}")
            if gated_unit is not None:
                lifecycle.set(gated_unit.id, WorkUnitStatus.BLOCKED, (TERMINAL_PHASE_FAILED, phase.value))
            return failure
    return None


def _status_code(result: Mapping[str, Any]) -> str:
    return f"STATUS:{result.get('status') or ('passed' if _passed(result) else 'failed')}"


def _terminal(lifecycle: _Lifecycle, result: Dict[str, Any]) -> Dict[str, Any]:
    """The one terminal decision: a plan is successful only when every unit
    is VERIFIED. A result claiming success otherwise is a contradiction and
    fails closed - partial completion is never SUCCESS."""
    if _claims_success(result) and not lifecycle.all_verified():
        unverified = sorted(uid for uid, s in lifecycle.states.items() if s.status is not WorkUnitStatus.VERIFIED)
        logger.error(f"Plan result claims success but units {unverified} are not VERIFIED - failing closed.")
        return {
            "status": "needs_review",
            "quality_gates_passed": False,
            "reason_codes": [PLAN_TERMINAL_MISMATCH],
            "unverified_work_units": unverified,
            "result": result,
            "work_unit_states": _state_payload(lifecycle),
        }
    if len(lifecycle.plan.work_units) > 1 and isinstance(result, dict):
        # A multi-unit plan's result names each unit's outcome (BLOCKED
        # reasons included). A one-unit plan's result is its unit's own
        # result, unchanged.
        result = {**result, "work_unit_states": _state_payload(lifecycle)}
    return result


def _state_payload(lifecycle: _Lifecycle) -> Dict[str, Any]:
    return {unit_id: state.to_dict() for unit_id, state in lifecycle.states.items()}


# ------------------------------------------------------------------ direct

class DirectPlanDriver(PlanDriver):
    """A direct goal's single unit: run the generation primitive once with
    the caller's own arguments; its result is the plan's result, unchanged."""

    def __init__(self, generate: Callable[..., Awaitable[Dict[str, Any]]], call_args: Mapping[str, Any]):
        self._generate = generate
        self._call_args = dict(call_args)

    async def run_unit(self, plan, unit, invocation, *, resume, resume_id):
        return await self._generate(**{
            **self._call_args, "goal": unit.goal, "resume": resume, "resume_id": resume_id,
            "work_unit": invocation,
        })


async def execute_direct_goal(
    generate: Callable[..., Awaitable[Dict[str, Any]]], call_args: Mapping[str, Any],
) -> Dict[str, Any]:
    """A direct goal as a one-unit ExecutionPlan through execute_plan()."""
    workspace_path = call_args["workspace_path"]
    context = require_mutating_run(workspace_path)
    plan = direct_execution_plan(call_args["goal"], plan_id=context.run_id)
    return await execute_plan(
        plan, DirectPlanDriver(generate, call_args), workspace_path,
        resume=bool(call_args.get("resume")), resume_id=call_args.get("resume_id"),
    )
