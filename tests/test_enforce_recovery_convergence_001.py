"""ENFORCE-RECOVERY-CONVERGENCE-001 (audit root R4): recovery convergence and verification parity in the enforce loop.

Root invariant: a recovery may change an upstream stage, but the run ends successfully only after every completed
stage that depended on it has converged again, and a recovery result is accepted on exactly the declared verification
its planned execution needs.

The loop is real; units are scripted at ``run_generation_workflow`` (``_runner``), except
``test_a_real_unit_...`` where the unit, its gates and its no-change settlement are real and only the model
transport is scripted. Sealed pre-fix reproducers: ``verify-r1-r6-ce1efb7-2026-11-11/repro/test_r4_reopened_
dependents.py`` and ``test_crosschain_ce_owner_recovery_acceptance.py`` (both inverted here).
"""
import contextlib
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_e12_enforce_characterization import (
    _COMPILE,
    _VALID,
    C,
    _enforce_engine,
    _once_for_s1,
    _plan_of,
    _recovery_plan,
    _unit,
)

from kriya.workflow.enforce_loop import (
    DEPENDENT_REVERIFICATION_CONTEXT,
    DEPENDENT_REVERIFIED_UNCHANGED,
    ENFORCE_CONVERGENCE_NOT_REACHED,
    TOOL_STAGE_INVALIDATED_NOT_REPLAYABLE,
)
from kriya.workflow.plan_schema import ExecutionMethod, ExecutionRole, FileAction, PlannedFile, Subtask
from kriya.workflow.recovery_plan import RecoveryAction
from kriya.workflow.workflow_controller import WorkflowController
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

K1 = "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"
REVERIFY = "dependent_reverification"


class Forever:
    """A scripted answer served on every call of its (stage, role)."""

    def __init__(self, answer):
        self.answer = answer


def _needs(path="a.py"):
    return {"_skip_write": True, "quality_gates_passed": False, "status": "failed", "plan_scope_conflict": {
        "classification": "PLAN_SCOPE_INSUFFICIENT", "required_files": [path], "failure_type": "COMPILE_ERROR",
        "reason": f"{path} must change"}}


def _unchanged(**extra):
    """The unit's characterized answer for an identical regeneration under ``reopened_owner`` (K1): the typed
    one-attempt stop after its candidate gates passed, nothing written, the declared verification evidence carried."""
    return {"_skip_write": True, "quality_gates_passed": False, "status": "failed", "files": [],
            "candidate_gates_passed": True, "terminal_regression_passed": False, "no_progress_reason": K1, **extra}


def _runner(calls, script):
    """``run_generation_workflow``: ``script[(stage, role)]`` is a list consumed in order or a ``Forever``. A call
    writes its allowed files with call-unique bytes unless the answer says ``_skip_write`` or gives ``_content``."""
    async def run(**kwargs):
        calls.append(kwargs)
        sid, role = kwargs["current_subtask_id"], kwargs["execution_scope"].split("role=")[1]
        queue = script.get((sid, role))
        scripted = dict(queue.answer) if isinstance(queue, Forever) else dict(queue.pop(0)) if queue else {}
        files = list(kwargs["allowed_write_relpaths"] or [])
        content = scripted.pop("_content", None)
        if not scripted.pop("_skip_write", False):
            for rel in files:
                with open(os.path.join(kwargs["workspace_path"], rel), "w", encoding="utf-8") as handle:
                    handle.write(content if content is not None else f"# {rel} by {sid} call {len(calls)}\n")
        evidence = [{"type": vm["type"], "tool_name": vm.get("tool_name"), "description": vm["description"],
                     "passed": True} for vm in kwargs.get("required_verification") or []]
        answer = {"status": "success", "quality_gates_passed": True, "files": files, "verification_results": evidence}
        answer.update(scripted)
        return answer
    return run


async def _run(tmp_path, plan, script, *, extra=(), resume=False, engine=None):
    calls = []
    we = engine or _enforce_engine(tmp_path)
    we.run_generation_workflow = _runner(calls, script)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)))
        stack.enter_context(patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan))
        stack.enter_context(patch(f"{C}.validate_plan", new=AsyncMock(return_value=_VALID)))
        stack.enter_context(patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace))
        for extra_patch in extra:
            stack.enter_context(extra_patch)
        result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce",
                                                      resume=resume)
    return result, calls


def _roles(calls):
    return [call["execution_scope"] for call in calls]


def _ran(calls, sid):
    return [call["execution_scope"].split("role=")[1] for call in calls if call["current_subtask_id"] == sid]


def _results(result):
    return [(item["subtask_id"], item["status"]) for item in result.legacy_result["subtask_results"]]


def _codes(result, sid):
    (entry,) = [item for item in result.legacy_result["subtask_results"] if item["subtask_id"] == sid]
    return list(entry.get("reason_codes") or [])


def _persisted(tmp_path):
    with open(tmp_path / ".kriya/control/plans/run1.json", encoding="utf-8") as handle:
        plan_document = json.load(handle)
    with open(tmp_path / ".kriya/control/state.json", encoding="utf-8") as handle:
        state = json.load(handle)
    return plan_document, state


def _chain():
    return _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]), _unit("s2", "c.py", depends_on=["s1"]))


def _assert_converged(tmp_path, result, stages):
    """Terminal success means ONE truth: the result, the returned ControlState, state.json and the plan document."""
    legacy = result.legacy_result
    assert legacy["status"] == "success" and legacy["quality_gates_passed"] is True
    assert sorted(_results(result)) == sorted((sid, "completed") for sid in stages)   # exactly one entry per stage
    assert dict(result.control_state.subtask_states) == {sid: "completed" for sid in stages}
    plan_document, state = _persisted(tmp_path)
    assert plan_document["lifecycle_state"] == "completed"
    assert plan_document["stage_states"] == {sid: "completed" for sid in stages}
    assert state["subtask_states"] == {sid: "completed" for sid in stages}


# ============================================================================ re-entry, result authority, cascade

@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_completed_dependent_of_a_recovered_owner_is_reentered_before_the_run_can_succeed(tmp_path):
    """The sealed R4 reproducer, inverted: s2's failure reopens s0; s1 (completed, depends on s0) is re-entered as a
    dependent reverification with ``reopened_owner`` and the generic context; s1 changed, so s2 is re-entered too."""
    result, calls = await _run(tmp_path, _chain(), {("s2", "planned"): [_needs()]})
    assert _roles(calls) == [
        "subtask=s0 role=planned", "subtask=s1 role=planned", "subtask=s2 role=planned",
        "subtask=s0 role=owner_recovery", "subtask=s2 role=consumer_retry",
        f"subtask=s1 role={REVERIFY}", f"subtask=s2 role={REVERIFY}"]
    reverification = calls[5]
    assert reverification["reopened_owner"] is True
    assert reverification["recovery_contract_block"] == DEPENDENT_REVERIFICATION_CONTEXT
    assert calls[1]["reopened_owner"] is False and calls[1]["recovery_contract_block"] == ""
    _assert_converged(tmp_path, result, ("s0", "s1", "s2"))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_byte_equal_verified_reverification_completes_the_stage_and_invalidates_nothing(tmp_path):
    """s1's reverification is the characterized unchanged form: completed again, said so, and s2 - which already ran
    against these bytes - is not re-entered."""
    result, calls = await _run(tmp_path, _chain(), {("s2", "planned"): [_needs()], ("s1", REVERIFY): [_unchanged()]})
    assert _ran(calls, "s1") == ["planned", REVERIFY] and _ran(calls, "s2") == ["planned", "consumer_retry"]
    assert _codes(result, "s1") == [DEPENDENT_REVERIFIED_UNCHANGED]
    assert (tmp_path / "b.py").read_text() == "# b.py by s1 call 2\n"
    _assert_converged(tmp_path, result, ("s0", "s1", "s2"))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("answer, status, code", [
    (_unchanged(candidate_gates_passed=False), "failed", None),                      # the gates did not pass
    (_unchanged(verification_results=[]), "needs_review", "VERIFICATION_EVIDENCE_MISSING"),   # no declared evidence
    ({"_skip_write": True, "quality_gates_passed": False, "status": "failed", "files": [],
      "candidate_gates_passed": True}, "failed", None),                              # a planned no-change refusal
    ({**_unchanged(), "_skip_write": False}, "failed", None),                        # the typed stop, bytes retained
    (_unchanged(files=["b.py"], final_review_refusal={"candidate_applied": True, "reason_code": "OUTPUT_TRUNCATED"}),
     "failed", None),                                                                # the typed stop, a commit
], ids=["gates_not_passed", "verification_missing", "not_the_typed_stop", "bytes_retained", "committed"])
async def test_only_the_characterized_unchanged_form_is_a_reverification(tmp_path, answer, status, code):
    result, calls = await _run(tmp_path, _chain(), {("s2", "planned"): [_needs()], ("s1", REVERIFY): [answer]})
    assert result.legacy_result["status"] == status and result.legacy_result["quality_gates_passed"] is False
    assert ("s1", status) in _results(result) and ("s1", "completed") not in _results(result)
    if code:
        assert code in _codes(result, "s1")
    assert _ran(calls, "s2") == ["planned", "consumer_retry"]          # nothing past the failed re-entry ran


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_unchanged_form_on_an_ordinary_planned_stage_is_not_a_reverification(tmp_path):
    """Normal planned execution is untouched: the same answer from a stage that is NOT a convergence re-entry fails."""
    result, calls = await _run(tmp_path, _chain(), {("s1", "planned"): [_unchanged()]})
    assert _roles(calls) == ["subtask=s0 role=planned", "subtask=s1 role=planned"]
    assert result.legacy_result["status"] == "failed" and ("s1", "failed") in _results(result)


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_stale_result_is_not_current_once_its_stage_is_invalidated(tmp_path):
    """s1 completed, was invalidated, and its re-entry failed: the result holds ONE s1 entry, the failed one; the
    pre-invalidation ``completed`` is gone and every persisted document says the same."""
    failed = {"_skip_write": True, "quality_gates_passed": False, "status": "failed", "files": []}
    result, _calls = await _run(tmp_path, _chain(), {("s2", "planned"): [_needs()], ("s1", REVERIFY): [failed]})
    assert _results(result) == [("s0", "completed"), ("s2", "completed"), ("s1", "failed")]
    assert result.legacy_result["status"] == "failed"
    assert result.control_state.subtask_states["s1"] == "failed"
    plan_document, state = _persisted(tmp_path)
    assert plan_document["stage_states"]["s1"] == "failed" and plan_document["lifecycle_state"] == "failed"
    assert state["subtask_states"]["s1"] == "failed"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_dependents_are_pending_and_resultless_while_the_owner_is_recovered(tmp_path):
    """At the moment the reopened owner runs, its completed dependent is already ``pending`` on disk."""
    seen = {}

    def at_owner(call_kwargs):
        from kriya.control.persistence import load_control_state
        seen.update(load_control_state(str(tmp_path)).subtask_states)

    calls = []
    we = _enforce_engine(tmp_path)
    inner = _runner(calls, {("s2", "planned"): [_needs()], ("s1", REVERIFY): [_unchanged()]})

    async def run(**kwargs):
        if kwargs["execution_scope"].endswith("role=owner_recovery"):
            at_owner(kwargs)
        return await inner(**kwargs)

    with patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch(f"{C}.build_engineering_plan_from_planner_output", return_value=_chain()), \
         patch(f"{C}.validate_plan", new=AsyncMock(return_value=_VALID)), \
         patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace):
        we.run_generation_workflow = run
        result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    assert (seen["s0"], seen["s1"], seen["s2"]) == ("in_progress", "pending", "pending")
    _assert_converged(tmp_path, result, ("s0", "s1", "s2"))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_changed_reverification_cascades_through_every_level(tmp_path):
    """Four levels. s3's failure reopens s0; s3 is retried at once (against stale s1, s2). s1 and s2 are re-entered
    and change, so s3 - completed in between - is stale again and is re-entered last."""
    plan = _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]),
                    _unit("s2", "c.py", depends_on=["s1"]), _unit("s3", "d.py", depends_on=["s2"]))
    result, calls = await _run(tmp_path, plan, {("s3", "planned"): [_needs()]})
    assert _roles(calls)[4:] == [
        "subtask=s0 role=owner_recovery", "subtask=s3 role=consumer_retry",
        f"subtask=s1 role={REVERIFY}", f"subtask=s2 role={REVERIFY}", f"subtask=s3 role={REVERIFY}"]
    _assert_converged(tmp_path, result, ("s0", "s1", "s2", "s3"))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_unchanged_reverifications_do_not_reenter_the_consumer(tmp_path):
    """The same four levels with s1 and s2 reverified unchanged: s3's consumer retry already ran against these bytes."""
    plan = _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]),
                    _unit("s2", "c.py", depends_on=["s1"]), _unit("s3", "d.py", depends_on=["s2"]))
    result, calls = await _run(tmp_path, plan, {("s3", "planned"): [_needs()], ("s1", REVERIFY): [_unchanged()],
                                                ("s2", REVERIFY): [_unchanged()]})
    assert _ran(calls, "s3") == ["planned", "consumer_retry"]
    assert _codes(result, "s1") == _codes(result, "s2") == [DEPENDENT_REVERIFIED_UNCHANGED]
    _assert_converged(tmp_path, result, ("s0", "s1", "s2", "s3"))


# ============================================================================ termination

def _fan():
    return _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]), _unit("s2", "c.py", depends_on=["s0"]))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_repeated_convergence_state_stops_typed(tmp_path):
    """s1 and s2 both depend on s0 and want different bytes of a.py. The owner alternates X2 / X3 / X2 while b.py and
    c.py stay the same, so the state "s1 to re-enter, these bytes" comes round a second time: typed stop, no success,
    and s1 is not run again."""
    b, c = {"_content": "B\n"}, {"_content": "C\n"}
    script = {
        ("s1", "planned"): [dict(b)], ("s2", "planned"): [_needs()],
        ("s0", "owner_recovery"): [{"_content": "X2\n"}, {"_content": "X3\n"}, {"_content": "X2\n"}],
        ("s2", "consumer_retry"): [dict(c), dict(c)], ("s1", "consumer_retry"): [dict(b)],
        ("s1", REVERIFY): [_needs()], ("s2", REVERIFY): [_needs()],
    }
    result, calls = await _run(tmp_path, _fan(), script)
    assert _ran(calls, "s0") == ["planned", "owner_recovery", "owner_recovery", "owner_recovery"]
    assert _ran(calls, "s1") == ["planned", REVERIFY, "consumer_retry"]        # the second re-entry never ran
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False
    assert ("s1", "needs_review") in _results(result) and _codes(result, "s1") == [ENFORCE_CONVERGENCE_NOT_REACHED]
    plan_document, state = _persisted(tmp_path)
    assert plan_document["stage_states"]["s1"] == "needs_review" and plan_document["lifecycle_state"] == "needs_review"
    assert state["subtask_states"]["s1"] == "needs_review"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_new_bytes_every_time_are_stopped_by_the_owners_work_ceiling(tmp_path):
    """Every re-entry asks for the owner again and every write is new, so no state repeats. The run still ends, never a success.
    S8 (C-08): the ONE owner of this plan is invoked at most UNIT_INVOCATION_CEILING times (the planned run and six recoveries), which is
    reached before the plan-derived convergence cap (3 stages x 3 cycles = 9 recoveries); the seventh recovery is not invoked."""
    from kriya.control.work_ledger import UNIT_INVOCATION_CEILING

    script = {("s2", "planned"): [_needs()], ("s1", REVERIFY): Forever(_needs()), ("s2", REVERIFY): Forever(_needs())}
    result, calls = await _run(tmp_path, _fan(), script)
    assert len(_ran(calls, "s0")) == UNIT_INVOCATION_CEILING and _ran(calls, "s0").count("owner_recovery") == UNIT_INVOCATION_CEILING - 1
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False
    # MEASURED 2026-11-17: the result names PLAN_SCOPE_REVISION_REQUIRED, not the ceiling that stopped the owner (reported to the owner as
    # a diagnostic defect); the reason is therefore not pinned here, only the safe stop.


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_second_invalidation_of_the_same_stage_by_the_same_cause_is_legitimate(tmp_path):
    """s2's failure reopens s0 and s1 is re-entered (changed); the re-entered s2 fails again on a.py, s0 is reopened
    again and s1 is re-entered a SECOND time for the same owner and the same consumer. Nothing repeats, so it runs."""
    script = {("s2", "planned"): [_needs()], ("s2", REVERIFY): [_needs()], ("s1", REVERIFY): [{}, _unchanged()]}
    result, calls = await _run(tmp_path, _chain(), script)
    assert _ran(calls, "s1") == ["planned", REVERIFY, REVERIFY]
    assert _ran(calls, "s0") == ["planned", "owner_recovery", "owner_recovery"]
    _assert_converged(tmp_path, result, ("s0", "s1", "s2"))


# ============================================================================ the third site: verification-owner reopen

_NO_CHANGE_POSSIBLE = {"_skip_write": True, "quality_gates_passed": False, "status": "failed", "no_progress_reason": K1}


def _verifier(depends_on):
    return Subtask(id="sv", description="verify", execution_method=ExecutionMethod.MODEL,
                   execution_role=ExecutionRole.VERIFICATION, depends_on=list(depends_on), verification=[_COMPILE])


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_verification_owner_reopen_invalidates_the_owners_other_completed_dependents(tmp_path):
    """sv fails and its owner s0 is reopened and changes; s1 also depends on s0 and had completed: re-entered."""
    plan = _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]), _verifier(["s0"]))
    result, calls = await _run(tmp_path, plan, {("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)]})
    assert _roles(calls) == [
        "subtask=s0 role=planned", "subtask=s1 role=planned", "subtask=sv role=planned",
        "subtask=s0 role=verification_owner_recovery", "subtask=sv role=verification_rerun",
        f"subtask=s1 role={REVERIFY}"]
    _assert_converged(tmp_path, result, ("s0", "s1", "sv"))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_reentered_verification_only_unit_is_not_a_reopened_owner(tmp_path):
    """A verification-only stage owns no file: its re-entry is told why it runs but has nothing to regenerate."""
    plan = _plan_of(_unit("s0", "a.py"), _verifier(["s0"]), _unit("sz", "c.py", depends_on=["s0"]))   # order: s0, sv, sz
    result, calls = await _run(tmp_path, plan, {("sz", "planned"): [_needs()]})
    (reverification,) = [call for call in calls if call["execution_scope"] == f"subtask=sv role={REVERIFY}"]
    assert reverification["reopened_owner"] is False
    assert reverification["recovery_contract_block"] == DEPENDENT_REVERIFICATION_CONTEXT
    _assert_converged(tmp_path, result, ("s0", "sv", "sz"))


# ============================================================================ a completed TOOL dependent: refuse before mutating

def _tool(depends_on):
    return Subtask(id="t1", description="run a command", execution_method=ExecutionMethod.TOOL, tool_name="shell",
                   tool_arguments={"command": "true"}, depends_on=list(depends_on))


def _tool_patches(executor):
    return [patch(f"{C}.subtask_executor.execute", new=executor)]


def _assert_tool_refusal(tmp_path, result, calls, executor, *, failing, owner_bytes, forbidden_role):
    assert all(not scope.endswith(f"role={forbidden_role}") for scope in _roles(calls))    # the owner was never invoked
    assert all(REVERIFY not in scope for scope in _roles(calls))                             # nothing was re-fed
    assert executor.await_count == 1                                                          # the effect ran once
    assert (tmp_path / "a.py").read_text() == owner_bytes
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False
    assert TOOL_STAGE_INVALIDATED_NOT_REPLAYABLE in _codes(result, failing)
    assert ("s0", "completed") in _results(result) and ("t1", "completed") in _results(result)
    assert result.control_state.subtask_states["t1"] == "completed"
    assert result.control_state.subtask_states["s0"] == "completed"
    plan_document, state = _persisted(tmp_path)
    assert state["subtask_states"]["t1"] == "completed" and plan_document["stage_states"]["t1"] == "completed"
    (event,) = [e for e in legacy["plan_recovery_events"] if e.get("blocked_owners")]
    assert event["blocked_owners"] == {"s0": ["t1"]} and event["failed_subtask"] == failing
    assert event["reason_codes"] == [TOOL_STAGE_INVALIDATED_NOT_REPLAYABLE] and event["owner_recovery_passed"] is False


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_owner_recovery_that_would_invalidate_a_completed_tool_is_refused_before_the_owner_runs(tmp_path):
    plan = _plan_of(_unit("s0", "a.py"), _tool(["s0"]), _unit("s2", "c.py", depends_on=["t1"]))
    executor = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    result, calls = await _run(tmp_path, plan, {("s2", "planned"): [_needs()]}, extra=_tool_patches(executor))
    _assert_tool_refusal(tmp_path, result, calls, executor, failing="s2", owner_bytes="# a.py by s0 call 1\n",
                         forbidden_role="owner_recovery")
    assert "PLAN_SCOPE_REVISION_REQUIRED" in _codes(result, "s2")       # the original conflict is still reported


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_may_change_recovery_that_would_invalidate_a_completed_tool_is_refused_before_the_owner_runs(tmp_path):
    plan = _plan_of(Subtask(id="s0", description="write", execution_method=ExecutionMethod.MODEL, verification=[_COMPILE],
                            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE),
                                           PlannedFile(path="dep0.py", action=FileAction.CREATE)]),
                    _tool(["s0"]), _unit("s1", "c.py", depends_on=["t1"]))
    executor = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    recovery = _recovery_plan(plan, [RecoveryAction.MAY_CHANGE])
    extra = _tool_patches(executor) + [
        patch(f"{C}.build_recovery_execution_plan", side_effect=_once_for_s1(recovery)),
        patch(f"{C}._build_may_change_recovery_context", return_value="may change")]
    result, calls = await _run(tmp_path, plan, {("s1", "planned"): [_needs("dep0.py")]}, extra=extra)
    _assert_tool_refusal(tmp_path, result, calls, executor, failing="s1", owner_bytes="# a.py by s0 call 1\n",
                         forbidden_role="may_change_owner_recovery")


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_verification_owner_reopen_that_would_invalidate_a_completed_tool_is_refused_before_the_owner_runs(tmp_path):
    plan = _plan_of(_unit("s0", "a.py"), _tool(["s0"]), _verifier(["s0", "t1"]))    # the TOOL completes before sv
    executor = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    result, calls = await _run(tmp_path, plan, {("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)]}, extra=_tool_patches(executor))
    _assert_tool_refusal(tmp_path, result, calls, executor, failing="sv", owner_bytes="# a.py by s0 call 1\n",
                         forbidden_role="verification_owner_recovery")
    assert _ran(calls, "sv") == ["planned"]                              # no rerun: the failure stands


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_tool_that_is_not_a_dependent_of_the_owner_blocks_nothing(tmp_path):
    """The preflight reads the owner's transitive dependents only: a completed TOOL elsewhere in the plan is untouched
    and the recovery runs."""
    plan = _plan_of(_unit("s0", "a.py"), _tool([]), _unit("s2", "c.py", depends_on=["s0", "t1"]))
    executor = AsyncMock(return_value=SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool"))
    result, calls = await _run(tmp_path, plan, {("s2", "planned"): [_needs()]}, extra=_tool_patches(executor))
    assert _ran(calls, "s0") == ["planned", "owner_recovery"] and executor.await_count == 1
    assert result.legacy_result["status"] == "success" and result.control_state.subtask_states["t1"] == "completed"


# ============================================================================ resume

@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_resumed_completed_stage_is_reentered_only_when_this_run_invalidated_it(tmp_path):
    """First run: s0 and s1 complete, s2 fails. Resume: s0 and s1 are skipped as before (never run as planned); s2's
    failure reopens s0, so s1 - completed only in the resumed state - is re-entered; s0 itself is not."""
    first, _ = await _run(tmp_path, _chain(), {("s2", "planned"): [
        {"_skip_write": True, "quality_gates_passed": False, "status": "failed"}]})
    assert first.legacy_result["status"] == "failed"
    result, calls = await _run(tmp_path, _chain(), {("s2", "planned"): [_needs()]}, resume=True)
    assert result.legacy_result["resume_decision"]["reused_subtasks"] == ["s0", "s1"]
    assert _roles(calls) == ["subtask=s2 role=planned", "subtask=s0 role=owner_recovery",
                             "subtask=s2 role=consumer_retry", f"subtask=s1 role={REVERIFY}",
                             f"subtask=s2 role={REVERIFY}"]
    _assert_converged(tmp_path, result, ("s0", "s1", "s2"))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_ordinary_resume_still_skips_every_completed_stage(tmp_path):
    first, _ = await _run(tmp_path, _chain(), {("s2", "planned"): [
        {"_skip_write": True, "quality_gates_passed": False, "status": "failed"}]})
    assert first.legacy_result["status"] == "failed"
    result, calls = await _run(tmp_path, _chain(), {}, resume=True)
    assert _roles(calls) == ["subtask=s2 role=planned"]
    _assert_converged(tmp_path, result, ("s0", "s1", "s2"))


# ============================================================================ verification parity and self-correction

@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_owner_recovery_without_its_declared_verification_evidence_is_refused(tmp_path):
    """The sealed cross-chain reproducer, inverted: the same answer the planned path refuses is refused on recovery."""
    script = {("s2", "planned"): [_needs()], ("s0", "owner_recovery"): [{"verification_results": []}]}
    result, calls = await _run(tmp_path, _chain(), script)
    assert "subtask=s2 role=consumer_retry" not in _roles(calls)
    assert result.legacy_result["status"] == "needs_review"
    assert ("s0", "needs_review") in _results(result) and _codes(result, "s0") == ["PLAN_RECOVERY_OWNER_FAILED"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_owner_recovery_with_failed_declared_verification_is_refused(tmp_path):
    failed = [{"type": "tool", "tool_name": "compile", "description": "compiles", "passed": False}]
    script = {("s2", "planned"): [_needs()], ("s0", "owner_recovery"): [{"verification_results": failed}]}
    result, calls = await _run(tmp_path, _chain(), script)
    assert "subtask=s2 role=consumer_retry" not in _roles(calls) and result.legacy_result["status"] == "needs_review"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_owner_that_declares_no_verification_is_asked_for_none_on_recovery(tmp_path):
    """No stricter than the planned path: no declaration, no evidence required."""
    plan = _plan_of(Subtask(id="s0", description="write a.py", execution_method=ExecutionMethod.MODEL,
                            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)]),
                    _unit("s2", "c.py", depends_on=["s0"]))
    script = {("s2", "planned"): [_needs()], ("s0", "owner_recovery"): [{"verification_results": None}]}
    result, calls = await _run(tmp_path, plan, script)
    assert _ran(calls, "s2") == ["planned", "consumer_retry"]
    _assert_converged(tmp_path, result, ("s0", "s2"))


def _may_change_plan():
    return _plan_of(Subtask(id="s0", description="write dep0.py", execution_method=ExecutionMethod.MODEL,
                            planned_files=[PlannedFile(path="dep0.py", action=FileAction.CREATE)], verification=[_COMPILE]),
                    _unit("s1", "a.py", depends_on=["s0"]))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("owner_answer, accepted", [({}, True), ({"verification_results": []}, False)],
                         ids=["evidence", "no_evidence"])
async def test_a_changed_may_change_owner_needs_its_declared_verification_evidence(tmp_path, owner_answer, accepted):
    plan = _may_change_plan()
    extra = [patch(f"{C}.build_recovery_execution_plan", side_effect=_once_for_s1(_recovery_plan(plan, [RecoveryAction.MAY_CHANGE]))),
             patch(f"{C}._build_may_change_recovery_context", return_value="may change")]
    script = {("s1", "planned"): [_needs("dep0.py")], ("s0", "may_change_owner_recovery"): [owner_answer]}
    result, calls = await _run(tmp_path, plan, script, extra=extra)
    assert ("subtask=s1 role=consumer_retry" in _roles(calls)) is accepted
    assert (result.legacy_result["status"] == "success") is accepted
    (event,) = [e for e in result.legacy_result["plan_recovery_events"] if e.get("may_change_outcome")]
    assert event["may_change_outcome"] == ("changed" if accepted else "failed")


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("owner_answer, changed", [({}, True), (_unchanged(), False)], ids=["changed", "unchanged"])
async def test_a_may_change_owner_invalidates_through_the_same_helper_and_restores_exactly(tmp_path, owner_answer, changed):
    """sm completed between the MAY_CHANGE owner s0 and the failing s1. Changed owner: sm is re-entered, as after an
    ordinary owner recovery. Unchanged owner (nothing retained): sm's state AND its result are restored, nothing is
    re-entered, and the run stops typed as before."""
    plan = _plan_of(Subtask(id="s0", description="write dep0.py", execution_method=ExecutionMethod.MODEL,
                            planned_files=[PlannedFile(path="dep0.py", action=FileAction.CREATE)], verification=[_COMPILE]),
                    _unit("sm", "b.py", depends_on=["s0"]), _unit("s1", "a.py", depends_on=["sm"]))
    extra = [patch(f"{C}.build_recovery_execution_plan", side_effect=_once_for_s1(_recovery_plan(plan, [RecoveryAction.MAY_CHANGE]))),
             patch(f"{C}._build_may_change_recovery_context", return_value="may change")]
    script = {("s1", "planned"): [_needs("dep0.py")], ("s0", "may_change_owner_recovery"): [owner_answer],
              ("sm", REVERIFY): [_unchanged()]}
    result, calls = await _run(tmp_path, plan, script, extra=extra)
    if changed:
        assert _ran(calls, "sm") == ["planned", REVERIFY]
        _assert_converged(tmp_path, result, ("s0", "sm", "s1"))
    else:
        assert _ran(calls, "sm") == ["planned"] and _ran(calls, "s1") == ["planned"]
        assert result.legacy_result["status"] == "needs_review"
        assert ("sm", "completed") in _results(result) and len(_results(result)) == 3
        assert result.control_state.subtask_states["sm"] == "completed"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("declares_verification", [True, False])
async def test_self_correction_runs_only_for_an_owner_that_declares_no_verification(tmp_path, declares_verification):
    """The self-correction result carries no verification evidence: an owner that declares verification is never
    self-corrected (it goes straight to the governed owner recovery); any other owner keeps self-correction first."""
    owner = Subtask(id="s0", description="write a.py", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
                    verification=[_COMPILE] if declares_verification else [])
    plan = _plan_of(owner, _unit("s2", "c.py", depends_on=["s0"]))

    async def self_correct(**kwargs):
        (tmp_path / "a.py").write_text("# a.py self-corrected\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    self_correction = AsyncMock(side_effect=self_correct)
    result, calls = await _run(tmp_path, plan, {("s2", "planned"): [_needs()]},
                               extra=[patch(f"{C}._attempt_owner_recovery_self_correction", new=self_correction)])
    assert self_correction.await_count == (0 if declares_verification else 1)
    assert ("subtask=s0 role=owner_recovery" in _roles(calls)) is declares_verification
    _assert_converged(tmp_path, result, ("s0", "s2"))


# ============================================================================ a REAL unit as the re-entered dependent

def _real_unit_run(tmp_path):
    """One enforce run with REAL units (Developer protocol, candidate transaction, gates, no-change settlement; only
    the model transport and the two validator calls are scripted). s0 owns service.py, s1 owns helper.py and depends
    on s0, s2 owns extra.py and depends on s1. s2's first answer is a scripted scope conflict naming service.py;
    the reopened s0 writes new bytes; the re-entered s1 regenerates helper.py byte-identical."""
    import asyncio
    import subprocess

    from _protocol_responses import sentinel

    from kriya.config import AppConfig
    from kriya.core import model_runtime
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.plan_schema import EngineeringPlan
    from kriya.workflow.state import GenerationState
    from kriya.workflow.workflow import WorkflowEngine

    files = {"s0": "shop/service.py", "s1": "shop/helper.py", "s2": "shop/extra.py"}
    base = {"shop/service.py": "def page_size():\n    return 5\n", "shop/helper.py": "def helper():\n    return 1\n",
            "shop/extra.py": "def extra():\n    return 0\n"}
    first = {"s0": "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n",
             "s1": "def helper():\n    return 2\n", "s2": "def extra():\n    return 3\n"}
    recovered_owner = "PAGE_SIZE = 10\n\n\ndef page_size():\n    return PAGE_SIZE\n"

    def unit(sid, depends_on, provides, requires):
        return {"id": sid, "description": f"update {files[sid]}", "execution_method": "model",
                "planned_files": [{"path": files[sid], "action": "modify"}], "depends_on": depends_on,
                "provides": provides, "requires": requires, "relevant_global_invariant_ids": ["gi1"],
                "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}

    plan = EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [],
        "subtasks": [unit("s0", [], ["page_size_configured"], []),
                     unit("s1", ["s0"], ["helper_ready"], ["page_size_configured"]),
                     unit("s2", ["s1"], [], ["helper_ready"])]})

    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    for path, content in base.items():
        (workspace / path).write_text(content)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)

    active = {}
    seen = {"units": [], "gates": [], "developer_prompts": []}

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        opening = (system_prompt or "").splitlines()[0] if system_prompt else ""
        sid = active["id"]
        if "File List Planner" in opening:
            content = '{"files": ["%s"]}' % files[sid]
        elif "Developer Agent" in opening:
            seen["developer_prompts"].append((active["scope"], (system_prompt or "") + "\n" + (user_prompt or "")))
            body = recovered_owner if active["scope"].endswith("role=owner_recovery") else first[sid]
            content = sentinel(files[sid], analysis="update.", content=body)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    real_workflow = WorkflowEngine.run_generation_workflow
    real_gate = GenerationState.record_gate_outcome

    async def run_unit(self, *args, **kwargs):
        active["id"], active["scope"] = kwargs.get("current_subtask_id"), str(kwargs.get("execution_scope"))
        if active["scope"] == "subtask=s2 role=planned":
            result = {"quality_gates_passed": False, "status": "failed", "files": [], "plan_scope_conflict": {
                "classification": "PLAN_SCOPE_INSUFFICIENT", "required_files": [files["s0"]],
                "failure_type": "COMPILE_ERROR", "reason": "service.py must change"}}
        else:
            result = await real_workflow(self, *args, **kwargs)
        seen["units"].append((active["scope"], kwargs.get("reopened_owner"), result))
        return result

    def gate(state, outcome):
        seen["gates"].append((active["scope"], outcome.get("type"), outcome.get("success")))
        return real_gate(state, outcome)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value="structured plan")
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_gate_outcome", new=gate), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=run_unit), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda self, *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda self, *a, **k: {"success": True, "output": "tests/test_shop.py .\n1 passed in 0.01s"}), \
         patch(f"{C}.parse_planner_structured_output", return_value=(object(), None)), \
         patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan):
        result = asyncio.run(WorkflowController(engine).execute("Make the page size configurable", str(workspace),
                                                                migration_mode="enforce"))
    return result, seen, workspace, files, first, recovered_owner


def test_a_real_unit_reentered_as_a_dependent_reverifies_unchanged_after_its_gates_ran(tmp_path):
    result, seen, workspace, files, first, recovered_owner = _real_unit_run(tmp_path)
    scopes = [scope for scope, _reopened, _result in seen["units"]]
    assert scopes == ["subtask=s0 role=planned", "subtask=s1 role=planned", "subtask=s2 role=planned",
                      "subtask=s0 role=owner_recovery", "subtask=s2 role=consumer_retry",
                      f"subtask=s1 role={REVERIFY}"]
    scope, reopened, unit_result = seen["units"][-1]
    assert reopened is True
    # the unit's own answer: the typed one-attempt stop, AFTER its gates ran in this very invocation
    assert unit_result["no_progress_reason"] == K1 and unit_result["quality_gates_passed"] is False
    assert unit_result["candidate_gates_passed"] is True and unit_result["files"] == []
    assert [(kind, ok) for where, kind, ok in seen["gates"] if where == scope] == [
        ("compile", True), ("regression_test", True), ("verified_no_change_refused", False)]
    assert unit_result["verification_results"] == [
        {"type": "tool", "description": "compile", "tool_name": "compile", "passed": True,
         "source": "authoritative_gate_outcome"}]
    reverification_prompts = [prompt for where, prompt in seen["developer_prompts"] if where == scope]
    assert len(reverification_prompts) == 1 and "dependent reverification" in reverification_prompts[0]
    # the loop's reading: completed again, said so, nothing downstream re-entered, bytes as they were
    legacy = result.legacy_result
    assert [(item["subtask_id"], item["status"]) for item in legacy["subtask_results"]] == [
        ("s0", "completed"), ("s2", "completed"), ("s1", "completed")]
    assert _codes(result, "s1") == [DEPENDENT_REVERIFIED_UNCHANGED]
    assert dict(result.control_state.subtask_states) == {"s0": "completed", "s1": "completed", "s2": "completed"}
    assert legacy["status"] == "success", legacy
    assert (workspace / files["s1"]).read_text() == first["s1"]
    assert (workspace / files["s0"]).read_text() == recovered_owner
