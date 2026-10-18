"""E12 characterization (full modularization W4): the decisions of ``WorkflowController._run_structured_shadow``,
``_write_enforce_trace`` and ``_run_structured_enforce`` that no existing test killed under a pre-move mutation
(b834d71; the decision-to-killer table is in handover/MODULARIZATION/SLICE-PLAN.md section 9, E12 row).

Every pin drives a REAL controller entry point - ``WorkflowController.execute`` or the method name the controller keeps
after the move (``_run_structured_shadow`` / ``_write_enforce_trace`` / ``_run_structured_enforce``) - with a strict
engine double (tests/_strict_doubles.py) and only the controller-module names the suite already patches replaced, so
each pin passes on the pre-move bytes and on the moved bytes alike and fails under the mutation named in its
docstring. No golden: every assertion is on an explicit value.
"""
import contextlib
import json
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config, strict_engine, strict_kernel

from kriya.core import role_metrics as rm
from kriya.core.llm import InferenceDeadlineError
from kriya.core.state_paths import trace_db_path
from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
)
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import (
    AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
    SHADOW_PLANNER_ROLE,
    WorkflowController,
)
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus

C = "kriya.workflow.workflow_controller"


def _route():
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    )


# ============================================================================ shadow mode

def _shadow_engine(kernel=None):
    we = strict_engine(strict_config(autonomy={"spec_compliance_enabled": False}))
    we.engineering_triage.classify = AsyncMock(return_value=_route())
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = kernel
    we.developer = MagicMock(name="developer")
    return we


def _plan(*subtasks):
    return EngineeringPlan(plan_id="run1", kind=ChangeKind.TASK, subtasks=list(subtasks),
                           acceptance_criteria=[AcceptanceCriterion(id="ac1", description="thing works")])


def _model(sid, path="a.py", action=FileAction.CREATE, depends_on=()):
    return Subtask(id=sid, description=f"do {sid}", execution_method=ExecutionMethod.MODEL,
                   planned_files=[PlannedFile(path=path, action=action)], depends_on=list(depends_on))


async def _shadow(we, tmp_path, plan, *, valid=True, results=None, parse=None):
    """The controller's shadow method (the name it keeps), its collaborators patched where the suite patches them."""
    executor = AsyncMock(side_effect=list(results or []))
    parse = parse if parse is not None else (MagicMock(), None)
    controller = WorkflowController(we)
    from kriya.control.state import ControlState
    from kriya.workflow.control_context import WorkflowControlContext
    route = _route()
    with patch(f"{C}.parse_planner_structured_output", return_value=parse), \
         patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan), \
         patch(f"{C}.validate_plan", new=AsyncMock(return_value=PlanValidationResult(
             valid=valid, errors=[] if valid else ["bad plan"]))), \
         patch(f"{C}.subtask_executor.execute", new=executor):
        out = await controller._run_structured_shadow(
            "goal", str(tmp_path), route, "run1", WorkflowControlContext.for_route(route),
            ControlState.new(run_id="run1", engineering_route=route))
    return out, executor


@pytest.mark.asyncio
async def test_shadow_without_a_structured_plan_returns_only_the_parse_note(tmp_path):
    """SH02 (the note dropped) / SH03 (another typed outcome code)."""
    outcomes = []
    with patch(f"{C}.record_planner_outcome", side_effect=lambda planner, outcome, **kw: outcomes.append((outcome, kw))):
        (plan, results, decisions, report, notes), executor = await _shadow(
            _shadow_engine(), tmp_path, None, parse=(None, "no fenced JSON block found"))
    assert (plan, results, decisions, report) == (None, (), (), None)
    assert notes == ["no structured plan: no fenced JSON block found"]
    ((outcome, kwargs),) = outcomes
    assert outcome == rm.STRUCTURED_MALFORMED and kwargs["role"] == SHADOW_PLANNER_ROLE
    executor.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_zero_subtasks_records_the_empty_plan_outcome(tmp_path):
    """SH03: the typed outcome of an empty structured plan is classified from STRUCTURED_PLAN_EMPTY, in the shadow
    role bucket."""
    codes = []
    with patch(f"{C}.classify_planner_outcome", side_effect=lambda c, valid=False: codes.append((list(c), valid)) or rm.STRUCTURED_OTHER_FAILURE), \
         patch(f"{C}.record_planner_outcome") as record:
        (plan, results, decisions, report, notes), _ = await _shadow(_shadow_engine(), tmp_path, None)
    assert codes == [(["STRUCTURED_PLAN_EMPTY"], False)]
    assert record.call_args.kwargs["role"] == SHADOW_PLANNER_ROLE
    assert (plan, results, decisions, report) == (None, (), (), None)
    assert notes == ["structured output parsed but produced zero subtasks"]


@pytest.mark.asyncio
async def test_shadow_invalid_plan_returns_the_plan_and_its_ledger(tmp_path):
    """SH04 (the plan not returned): execute() then binds the plan hash to the ControlState."""
    plan = _plan(_model("s1"))
    (got, results, decisions, report, notes), executor = await _shadow(_shadow_engine(), tmp_path, plan, valid=False)
    assert got is not None and got.plan_id == "run1"
    assert results == () and report is None
    assert [d.type for d in decisions] == ["plan_created"]
    assert notes == ["plan failed validation: ['bad plan']"]
    executor.assert_not_called()
    we = _shadow_engine()
    with patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan), \
         patch(f"{C}.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=False, errors=["x"]))):
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="shadow")
    assert result.control_state.current_plan_hash == plan.content_hash()


@pytest.mark.asyncio
async def test_shadow_canonicalizes_planned_actions_against_the_workspace(tmp_path):
    """SH05: a CREATE of a file the workspace already holds is canonicalized to MODIFY before anything runs."""
    (tmp_path / "a.py").write_text("x = 1\n")
    plan = _plan(_model("s1", "a.py", FileAction.CREATE))
    done = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")
    (got, _results, _d, _r, _n), executor = await _shadow(_shadow_engine(), tmp_path, plan, results=[done])
    assert got.subtask_by_id("s1").planned_files[0].action == FileAction.MODIFY
    assert executor.call_args.kwargs["subtask"].planned_files[0].action == FileAction.MODIFY


@pytest.mark.asyncio
async def test_shadow_tool_subtask_stops_the_pass_before_any_later_subtask(tmp_path):
    """SH09 (continue instead of break): nothing after a TOOL subtask runs in shadow mode."""
    tool = Subtask(id="s1", description="run a command", execution_method=ExecutionMethod.TOOL,
                   tool_name="shell", tool_arguments={"command": "true"})
    plan = _plan(tool, _model("s2", depends_on=["s1"]))
    done = SubtaskResult(subtask_id="s2", status=SubtaskStatus.COMPLETED, execution_method="model")
    (_p, results, decisions, report, notes), executor = await _shadow(_shadow_engine(), tmp_path, plan, results=[done])
    executor.assert_not_called()
    assert [(r.subtask_id, r.status) for r in results] == [("s1", SubtaskStatus.NEEDS_REVIEW)]
    assert notes == ["stopped at subtask 's1': TOOL subtasks are never executed in shadow mode"]
    assert report is not None


@pytest.mark.asyncio
async def test_shadow_stops_at_the_first_subtask_that_did_not_complete(tmp_path):
    """SH11 (no break): the dependent subtask never runs after a failed one."""
    plan = _plan(_model("s1"), _model("s2", "b.py", depends_on=["s1"]))
    failed = SubtaskResult(subtask_id="s1", status=SubtaskStatus.FAILED, execution_method="model", error="boom")
    done = SubtaskResult(subtask_id="s2", status=SubtaskStatus.COMPLETED, execution_method="model")
    (_p, results, _d, _r, notes), executor = await _shadow(_shadow_engine(), tmp_path, plan, results=[failed, done])
    assert executor.await_count == 1
    assert [r.subtask_id for r in results] == ["s1"]
    assert notes == ["stopped at subtask 's1': status=failed"]


@pytest.mark.asyncio
async def test_shadow_runs_each_model_subtask_with_the_engine_kernel_and_records_its_context(tmp_path):
    """SH12 (kernel not passed) / SH14 (the per-subtask context package not recorded)."""
    kernel = SimpleNamespace(name="kernel-sentinel")   # no registry: the tool listing fails and is only logged
    plan = _plan(_model("s1"))
    done = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")
    (_p, results, decisions, _r, notes), executor = await _shadow(_shadow_engine(kernel), tmp_path, plan, results=[done])
    assert executor.call_args.kwargs["kernel"] is kernel
    assert [d.type for d in decisions] == ["plan_created", "subtask_context_package", "subtask_attempt"]
    assert notes == [] and [r.status for r in results] == [SubtaskStatus.COMPLETED]


# ============================================================================ the enforce trace row

def _traced(cfg):
    llm = SimpleNamespace(role_metrics=rm.RoleMetrics(), last_completion=None, model="m")
    return WorkflowController(SimpleNamespace(kernel=strict_kernel(cfg), developer=SimpleNamespace(llm=llm)))


def _row(cfg):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        ((run_id, status, category, events, files),) = db.execute(
            "SELECT run_id, status, failure_category, run_events, files_modified FROM runs").fetchall()
    return run_id, status, category, json.loads(events or "[]"), files


def _kinds(events):
    return [e["kind"] for e in events if e["kind"] != "model.role_metrics"]


def test_an_error_result_writes_a_run_exception_event():
    """TR02: status error -> one run.exception event with the exception type and message."""
    cfg = strict_config()
    assert _traced(cfg)._write_enforce_trace("r1", "goal", {
        "status": "error", "failure_type": "KeyError", "files": [], "error": "'boom'"}, 0.0) is True
    run_id, status, category, events, _ = _row(cfg)
    assert (run_id, status, category) == ("r1.enforce", "error", "KeyError")
    (event,) = [e for e in events if e["kind"] == "run.exception"]
    assert event["details"] == {"exception_type": "KeyError", "message": "'boom'"}
    assert _kinds(events) == ["run.exception"]


def _contract(**extra):
    return {"contract_digest": "c" * 64, "admission": "ADMITTED", "totals": {"requirements": 1},
            "stored_path": "/x/contract.json", "authorities": [], "visibility": {}, "dispositions": None, **extra}


def test_a_baseline_without_no_mutation_flag_writes_no_no_mutation_event():
    """TR06 (the no_mutation_required event written whatever the result says)."""
    cfg = strict_config()
    _traced(cfg)._write_enforce_trace("r2", "goal", {
        "status": "needs_review", "files": [], "verification_contract": _contract(),
        "baseline_authority": {"no_mutation_required": False, "discriminating": True, "authorities_run": []}}, 0.0)
    _, _, _, events, _ = _row(cfg)
    assert _kinds(events) == ["verification_contract.compiled", "verification_contract.baseline",
                              "verification_contract.sealed"]


def test_only_an_unavailable_authority_category_writes_the_refused_event():
    """TR07 (the refused event for every other category)."""
    cfg = strict_config()
    _traced(cfg)._write_enforce_trace("r3", "goal", {
        "status": "success", "files": [], "no_mutation_required": True, "verification_contract": _contract(),
        "baseline_authority": {"no_mutation_required": True, "discriminating": True, "authorities_run": []}}, 0.0)
    _, _, category, events, _ = _row(cfg)
    assert category is None
    assert _kinds(events) == ["verification_contract.compiled", "verification_contract.baseline",
                              "verification_contract.no_mutation_required", "verification_contract.sealed"]


def test_an_unavailable_authority_writes_the_refused_event_with_only_the_unavailable_entries():
    """TR07 (the refused event never written): the typed refusal lists only the unavailable authority runs."""
    cfg = strict_config()
    unavailable = {"kind": "baseline_run", "reason_code": "AUTHORITY_EXECUTION_UNAVAILABLE"}
    _traced(cfg)._write_enforce_trace("r4", "goal", {
        "status": "failure", "failure_type": "VERIFICATION_AUTHORITY_UNAVAILABLE", "files": [],
        "failure_category": "verification_authority_unavailable", "error": "cannot run",
        "verification_contract": _contract(),
        "baseline_authority": {"no_mutation_required": False, "discriminating": False,
                               "authorities_run": [unavailable, {"kind": "x", "reason_code": "OK"}]}}, 0.0)
    _, _, category, events, _ = _row(cfg)
    assert category == "VERIFICATION_AUTHORITY_UNAVAILABLE"
    (refused,) = [e for e in events if e["kind"] == "verification_contract.refused"]
    assert refused["details"] == {"reason_code": "VERIFICATION_AUTHORITY_UNAVAILABLE", "unavailable": [unavailable]}


@pytest.mark.parametrize("reason_code, kind", [
    ("VERIFICATION_AUTHORITY_REQUIRED", "requirement.authority_required"),
    ("GOAL_INSUFFICIENT_FOR_VERIFICATION", "requirement.admission_refused"),
])
def test_an_admission_refusal_writes_its_typed_kind_and_the_authority_requests(reason_code, kind):
    """TR08 (the two refusal kinds swapped) / TR09 (the authority_requested event dropped)."""
    cfg = strict_config()
    requests = [{"requirement_id": "REQ-1"}, {"requirement_id": "REQ-1"}, {"requirement_id": "REQ-2"}]
    _traced(cfg)._write_enforce_trace("r5", "goal", {
        "status": "failure", "failure_type": "VERIFICATION_AUTHORITY", "files": [], "error": "refused",
        "verification_contract": _contract(), "requirements_admission": {"reason_code": reason_code},
        "authority_requests": requests, "authority_requests_path": "/x/requests.json"}, 0.0)
    _, _, _, events, _ = _row(cfg)
    assert _kinds(events) == ["verification_contract.compiled", kind, "verification_contract.authority_requested"]
    (requested,) = [e for e in events if e["kind"] == "verification_contract.authority_requested"]
    assert requested["details"] == {"contract_digest": "c" * 64, "stored_path": "/x/requests.json", "requests": requests}
    assert requested["message"].startswith("3 authority request(s) for 2 requirement(s)")


def test_a_sealed_contract_with_dispositions_writes_the_dispositioned_event():
    """TR11 (the dispositioned audit event dropped)."""
    cfg = strict_config()
    dispositions = {"digest": "d" * 64}
    _traced(cfg)._write_enforce_trace("r6", "goal", {
        "status": "success", "files": ["a.py"], "verification_contract": _contract(
            dispositions=dispositions,
            requirements=[{"id": "REQ-1", "status": "OPEN", "disposition": "WAIVED", "dispositioned_claims": ["c1"]},
                          {"id": "REQ-2", "status": "OPEN"}])}, 0.0)
    _, _, _, events, _ = _row(cfg)
    assert _kinds(events) == ["verification_contract.compiled", "verification_contract.sealed",
                              "verification_contract.dispositioned"]
    (event,) = [e for e in events if e["kind"] == "verification_contract.dispositioned"]
    assert event["details"]["entries"] == [
        {"requirement_id": "REQ-1", "status": "OPEN", "claims": ["c1"], "disposition": "WAIVED"}]


def test_the_trace_row_records_the_result_files():
    """TR15 (the files not recorded)."""
    cfg = strict_config()
    _traced(cfg)._write_enforce_trace("r7", "goal", {"status": "success", "files": ["b.py", "a.py"]}, 0.0)
    *_, files = _row(cfg)
    assert files == "a.py,b.py"


def test_no_trace_database_is_logged_and_never_raised(caplog):
    """TR16 (the trace_db_path failure re-raised)."""
    cfg = strict_config()
    with patch("kriya.core.state_paths.trace_db_path", side_effect=RuntimeError("no state dir")):
        assert _traced(cfg)._write_enforce_trace("r8", "goal", {"status": "success"}, 0.0) is False
    assert "No traces.db for enforce run 'r8': no state dir" in caplog.text


# ============================================================================ the enforce run: harness

_COMPILE = VerificationMethod(type=VerificationMethodType.TOOL, description="compiles", tool_name="compile")
_COMPILED = {"type": "tool", "tool_name": "compile", "description": "compiles", "passed": True}


def _enforce_engine(tmp_path, **autonomy):
    """A strict engine double over a real AppConfig (its kernel is ``strict_kernel``): the enforce run compiles and
    seals its real verification contract, runs the real terminal gates and commit, and calls only the scripted
    Planner and ``run_generation_workflow``."""
    cfg = strict_config(autonomy={"spec_compliance_enabled": False, **autonomy},
                        paths={"memory": str(tmp_path / "memory")})
    we = strict_engine(cfg)
    we.engineering_triage.classify = AsyncMock(return_value=_route())
    we.planner.run = AsyncMock(return_value="plan")
    we.planner.max_output_tokens = None
    return we


def _writer(tmp_path, calls, outcomes=None):
    """run_generation_workflow: records its kwargs and writes the subtask's planned files into its workspace."""
    async def run(**kwargs):
        calls.append(kwargs)
        scripted = (outcomes or {}).get(kwargs["current_subtask_id"], {})
        files = list(kwargs["allowed_write_relpaths"] or [])
        for rel in files:
            path = os.path.join(kwargs["workspace_path"], rel)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"# {rel} by {kwargs['current_subtask_id']}\n")
        evidence = [{"type": vm["type"], "tool_name": vm.get("tool_name"), "description": vm["description"],
                     "passed": True} for vm in kwargs.get("required_verification") or []]
        return {"status": "success", "quality_gates_passed": True, "files": files,
                "verification_results": evidence, **scripted}
    return run


def _unit(sid, path="a.py", depends_on=(), **extra):
    return Subtask(id=sid, description=f"write {path}", execution_method=ExecutionMethod.MODEL,
                   planned_files=[PlannedFile(path=path, action=FileAction.CREATE)] if path else [],
                   depends_on=list(depends_on), verification=[_COMPILE], **extra)


def _plan_of(*subtasks):
    return EngineeringPlan(plan_id="run1", kind=ChangeKind.TASK, subtasks=list(subtasks))


def _planned(plan, validation=None):
    return (patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)),
            patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan),
            patch(f"{C}.validate_plan", new=AsyncMock(return_value=validation or PlanValidationResult(valid=True))))


async def _enforce(we, tmp_path, plan, *, goal="Create a.py", calls=None, outcomes=None, **kwargs):
    calls = [] if calls is None else calls
    we.run_generation_workflow = _writer(tmp_path, calls, outcomes)
    p1, p2, p3 = _planned(plan)
    with p1, p2, p3, patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace):
        result = await WorkflowController(we).execute(goal, str(tmp_path), migration_mode="enforce", **kwargs)
    return result, calls


def _request(we):
    return we.planner.run.await_args_list[0].args[0]


# ============================================================================ enforce: contract admission and planning request

@pytest.mark.asyncio
async def test_a_blocking_requirement_policy_refuses_an_unadmitted_goal_before_planning(tmp_path):
    """EP01 (the admission refusal skipped) / EP02 (the typed authority requests not sealed before the stop)."""
    we = _enforce_engine(tmp_path, requirement_unverified_policy="block")
    result, calls = await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    legacy = result.legacy_result
    assert (legacy["status"], legacy["failure_type"], legacy["reason_codes"]) == (
        "failure", "VERIFICATION_AUTHORITY", ["VERIFICATION_AUTHORITY_REQUIRED"])
    assert we.planner.run.await_count == 0 and calls == []
    assert legacy["authority_requests"] and os.path.isfile(legacy["authority_requests_path"])


@pytest.mark.asyncio
async def test_a_failing_tool_listing_never_stops_the_run(tmp_path):
    """EP08: the registry's tool listing raising is logged; the run goes on."""
    we = _enforce_engine(tmp_path)
    we.kernel.registry.list_components.side_effect = RuntimeError("registry offline")
    result, calls = await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    assert result.legacy_result["status"] == "success" and len(calls) == 1


@pytest.mark.asyncio
async def test_resume_id_alone_requests_the_subtask_resume(tmp_path):
    """EP09 (only ``resume`` honoured): a second run with resume_id reads the prior ControlState and records its
    resume decision."""
    we = _enforce_engine(tmp_path)
    first, _ = await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    assert first.legacy_result["status"] == "success" and "resume_decision" not in first.legacy_result
    second, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), resume_id="prior")
    decision = second.legacy_result["resume_decision"]
    assert decision["source"] == "control_state" and decision["prior_control_state_run_id"] == first.run_id


@pytest.mark.asyncio
async def test_the_planner_output_cap_is_the_planner_agents_own(tmp_path):
    """EP11 (the agent's own max_output_tokens ignored) / EP19 (a request with nothing to fit sent with candidate
    prompts) / EP25 (json_mode)."""
    we = _enforce_engine(tmp_path)
    we.planner.max_output_tokens = 1234
    await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    kwargs = we.planner.run.await_args_list[0].kwargs
    assert kwargs["max_tokens_override"] == 1234
    assert kwargs["json_mode"] is True and kwargs["candidate_prompt"] is None
    assert kwargs["system_prompt_override"] == AUTHORITATIVE_PLANNER_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_unavailable_owner_candidates_never_stop_the_run(tmp_path):
    """EP12: grounded owner candidates raising are logged; planning goes on without them."""
    we = _enforce_engine(tmp_path)
    with patch(f"{C}.grounded_owner_candidates", side_effect=RuntimeError("graph gone")):
        result, calls = await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    assert result.legacy_result["status"] == "success" and len(calls) == 1


@pytest.mark.asyncio
async def test_the_planner_request_carries_the_owner_localization_goal_fact_and_requirement_sections(tmp_path):
    """EP13 / EP16 / EP17 / EP18: each section reaches the Planner request, in this order."""
    we = _enforce_engine(tmp_path)
    with patch(f"{C}.owner_candidates_prompt_block", return_value="OWNER-BLOCK"), \
         patch(f"{C}.render_localization_candidates", return_value="LOCALIZATION-BLOCK"), \
         patch("kriya.workflow.goal_facts.goal_facts_block", return_value="\nGOAL-FACTS-BLOCK"):
        await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    request = _request(we)
    owner = request.index("\n\nOWNER-BLOCK")
    localization = request.index("\nLOCALIZATION-BLOCK")
    facts = request.index("\nGOAL-FACTS-BLOCK")
    requirements = request.index("Set requirement_ids on each subtask to the REQ ids it serves")
    assert owner < localization < facts < requirements
    assert "REQ-1" in request[requirements - 200:]


@pytest.mark.asyncio
async def test_a_localization_deadline_stop_propagates_out_of_the_run(tmp_path):
    """EP14: Kriya's own inference deadline is a typed stop, never swallowed as 'localization unavailable'."""
    we = _enforce_engine(tmp_path)
    stop = InferenceDeadlineError("DEADLINE_EXHAUSTED", {"deadline_source": "run"})
    with patch(f"{C}.localization_candidates", new=AsyncMock(side_effect=stop)), pytest.raises(InferenceDeadlineError):
        await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    assert we.planner.run.await_count == 0


# ============================================================================ enforce: the subtask-spanning resume decision

async def _interrupted(tmp_path, plan, *, failing="s2"):
    """A first enforce run in which ``failing`` does not pass Quality Gates: the ControlState records the completed
    rest (completion scope "workspace": the plan sandbox is the workspace here)."""
    first, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, plan,
                              outcomes={failing: {"quality_gates_passed": False, "status": "failed"}})
    assert first.legacy_result["status"] == "failed"
    return first


def _rewrite_prior_state(tmp_path, **updates):
    from kriya.control.persistence import load_control_state, save_control_state
    state = load_control_state(str(tmp_path))
    save_control_state(str(tmp_path), state.with_updates(**updates))


def _two_units():
    return _plan_of(_unit("s1", "a.py"), _unit("s2", "b.py", depends_on=["s1"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("update, reason", [
    ({"requirement_contract_digest": "another-contract"}, "REQUIREMENT_CONTRACT_CHANGED"),
    ({"acceptance_approval_digest": "another-approval"}, "ACCEPTANCE_APPROVAL_CHANGED"),
    ({"verification_contract_digest": "another-verification-contract"}, "VERIFICATION_CONTRACT_CHANGED"),
    ({"current_plan_hash": "another-plan"}, "PLAN_CHANGED"),
    ({"subtask_completion_scope": "candidate"}, "COMPLETIONS_NOT_IN_WORKSPACE"),
], ids=["requirement_contract", "acceptance_approval", "verification_contract", "plan", "completion_scope"])
async def test_a_resume_under_other_authority_reuses_nothing(tmp_path, update, reason):
    """ER01 / ER02 / ER03 / ER04 / ER05: each binding the recorded subtasks ran under refuses their reuse, in this
    precedence order, and the plan runs fresh from s1."""
    await _interrupted(tmp_path, _two_units())
    _rewrite_prior_state(tmp_path, **update)
    result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _two_units(), resume=True)
    decision = result.legacy_result["resume_decision"]
    assert (decision["reason"], decision["reused_subtasks"]) == (reason, [])
    assert [c["current_subtask_id"] for c in calls] == ["s1", "s2"]


@pytest.mark.asyncio
async def test_a_matching_resume_reuses_only_the_completed_subtasks_and_annotates_the_run(tmp_path):
    """ER11 (failed subtasks listed as reused) / ER12 (the decision not annotated on the run record) / ER19 (the
    reused completion not approved as completed) / ER21 (the approved lifecycle) / ER14 (abandoned-plan quarantine
    although the resume reused work)."""
    await _interrupted(tmp_path, _two_units())
    (tmp_path / "old.py").write_text("residue\n")
    _rewrite_prior_state(tmp_path, subtask_written_files={"s1": ["a.py", "old.py"]})
    saved = []
    from kriya.workflow import workflow_controller as wc
    with patch(f"{C}.annotate_run", wraps=wc.annotate_run) as annotate, \
         patch(f"{C}.save_approved_plan", side_effect=lambda ws, pid, doc: saved.append(doc)):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _two_units(), resume=True)
    decision = result.legacy_result["resume_decision"]
    assert (decision["reason"], decision["reused_subtasks"]) == (None, ["s1"])
    assert [c["current_subtask_id"] for c in calls] == ["s2"]
    assert annotate.call_args_list[0].kwargs == {"resume_decision": decision}
    approved = saved[0]
    assert approved["lifecycle_state"] == "approved"
    assert approved["stage_states"] == {"s1": "completed", "s2": "pending"}
    assert (tmp_path / "old.py").read_text() == "residue\n"


@pytest.mark.asyncio
async def test_a_refused_resume_quarantines_only_the_abandoned_files_and_survives_a_cleanup_failure(tmp_path):
    """ER15 (a cleanup failure stops the run): the abandoned plan's residue cleanup is best effort."""
    await _interrupted(tmp_path, _two_units())
    _rewrite_prior_state(tmp_path, current_plan_hash="another-plan")
    with patch(f"{C}.quarantine_abandoned_plan_files", side_effect=OSError("read-only")):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _two_units(), resume=True)
    assert result.legacy_result["resume_decision"]["reason"] == "PLAN_CHANGED"
    assert result.legacy_result["status"] == "success" and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("record, expected_in_decisions", [
    ("unreadable", "bad record"),
    ("missing", "prior-run"),
])
async def test_a_prior_run_record_that_cannot_be_trusted_refuses_the_resume(tmp_path, record, expected_in_decisions):
    """ER07 (an unreadable record's error dropped) / ER08 (a missing record not reported): the prior state's
    provenance is unknown, so the resume fails validation."""
    from kriya.control.persistence import UnreadableRunRecordError
    await _interrupted(tmp_path, _two_units())
    loader = (MagicMock(side_effect=UnreadableRunRecordError("/x/run.json", "bad record")) if record == "unreadable"
              else MagicMock(return_value=None))
    with patch(f"{C}.load_control_state_run_reference", return_value="prior-run"), patch(f"{C}.load_run_record", loader):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _two_units(), resume=True)
    decision = result.legacy_result["resume_decision"]
    assert decision["reason"] == "RESUME_VALIDATION_FAILED" and decision["reused_subtasks"] == []
    assert expected_in_decisions in json.dumps(decision["decisions"])
    assert [c["current_subtask_id"] for c in calls] == ["s1", "s2"]


@pytest.mark.asyncio
async def test_a_tool_subtask_recorded_completed_is_executed_again_on_resume(tmp_path):
    """ER10 (TOOL subtasks resume-skipped): authority always revalidates."""
    tool = Subtask(id="t1", description="run a command", execution_method=ExecutionMethod.TOOL,
                   tool_name="shell", tool_arguments={"command": "true"})
    plan = _plan_of(tool, _unit("s2", "b.py", depends_on=["t1"]))
    done = SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool")
    executor = AsyncMock(return_value=done)
    with patch(f"{C}.subtask_executor.execute", new=executor):
        await _interrupted(tmp_path, plan)
        assert executor.await_count == 1
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, plan, resume=True)
    assert result.legacy_result["resume_decision"]["reused_subtasks"] == []
    assert executor.await_count == 2 and [c["current_subtask_id"] for c in calls] == ["s2"]


@pytest.mark.asyncio
async def test_the_control_state_binds_the_acceptance_approval_and_starts_unscoped(tmp_path):
    """ER18 (the approval digest not bound) / ER16 (a fresh plan's completions claimed for the workspace before any
    sandbox exists): the first persisted ControlState of a fresh run."""
    from kriya.control.persistence import save_control_state
    persisted = []
    with patch(f"{C}.bound_approval", return_value=SimpleNamespace(digest="approval-1")), \
         patch(f"{C}.save_control_state", side_effect=lambda ws, cs: persisted.append(cs) or save_control_state(ws, cs)):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")))
    assert result.control_state.acceptance_approval_digest == "approval-1"
    assert persisted[0].subtask_completion_scope is None
    assert persisted[0].acceptance_approval_digest == "approval-1"


@pytest.mark.asyncio
async def test_the_approved_plan_records_its_ownership_findings_from_create_actions(tmp_path):
    """ER23 (findings never recorded in the obligation ledger) / ER24 (MODIFY actions judged as creations)."""
    (tmp_path / "b.py").write_text("old\n")
    plan = _plan_of(_unit("s1", "a.py"), Subtask(
        id="s2", description="edit b.py", execution_method=ExecutionMethod.MODEL, depends_on=["s1"],
        planned_files=[PlannedFile(path="b.py", action=FileAction.MODIFY)], verification=[_COMPILE]))
    from kriya.workflow import workflow_controller as wc
    with patch(f"{C}.find_ownership_findings", wraps=wc.find_ownership_findings) as find, \
         patch(f"{C}.record_findings", wraps=wc.record_findings) as record:
        await _enforce(_enforce_engine(tmp_path), tmp_path, plan)
    assert find.call_args.args[0] == [("a.py", "s1", "write a.py")]
    assert find.call_args.kwargs["touched_paths"] == ["b.py"]
    assert record.call_args.kwargs == {"revision": 0, "source": "workflow_controller.approved_plan"}


# ============================================================================ enforce: plan normalization and the bounded PLAN_REPAIR loop

def _decisions(tmp_path, kind):
    from kriya.control.persistence import decision_ledger_path
    with open(decision_ledger_path(str(tmp_path)), encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return [row for row in rows if row["type"] == kind]


async def _enforce_rounds(we, tmp_path, plan, validations, *, goal="Create a.py", extra=(), **kwargs):
    """An enforce run whose validate_plan answers ``validations`` in order (one per Planner round)."""
    calls = []
    we.run_generation_workflow = _writer(tmp_path, calls)
    repair = MagicMock(side_effect=lambda *a, **k: "REPAIR-PROMPT round " + str(a[4]))
    with patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan), \
         patch(f"{C}.validate_plan", new=AsyncMock(side_effect=list(validations))) as validate, \
         patch(f"{C}.build_structured_plan_repair_prompt", repair), \
         patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace):
        for extra_patch in extra:
            extra_patch.start()
        try:
            result = await WorkflowController(we).execute(goal, str(tmp_path), migration_mode="enforce", **kwargs)
        finally:
            for extra_patch in extra:
                extra_patch.stop()
    return result, calls, validate, repair


_INVALID = PlanValidationResult(valid=False, errors=["s1 is wrong"], reason_codes=["PLAN_VALIDATION_FAILED"])
_VALID = PlanValidationResult(valid=True)


def _variant(plan, sid, description):
    copy = plan.model_copy(deep=True)
    copy.subtask_by_id(sid).description = description
    return copy


@pytest.mark.asyncio
async def test_each_normalization_feeds_the_next_and_is_recorded(tmp_path):
    """EV04 / EV05 / EV06 / EV07 (a normalization's plan dropped) / EV08 (the normalized record not written): the
    plan validate_plan judges is the last normalization's output."""
    base = _plan_of(_unit("s1"))
    merged, redirected, derived, scoped = (_variant(base, "s1", f"step {i}") for i in range(4))
    seen = {}

    def step(name, out, record):
        def run(plan, *args, **kwargs):
            seen[name] = plan.subtask_by_id("s1").description
            return out, record
        return run
    patches = [patch(f"{C}.coalesce_same_file_owners", side_effect=step("coalesce", merged, ["merge-1"])),
               patch(f"{C}.redirect_immutable_test_edits", side_effect=step("redirect", redirected, ["redirect-1"])),
               patch(f"{C}.derive_verification_contracts", side_effect=step("derive", derived, ["derived-1"])),
               patch(f"{C}.scope_verification_to_requirements", side_effect=step("scope", scoped, ["scope-1"]))]
    result, calls, validate, _ = await _enforce_rounds(_enforce_engine(tmp_path), tmp_path, base, [_VALID], extra=patches)
    assert seen == {"coalesce": "write a.py", "redirect": "step 0", "derive": "step 1", "scope": "step 2"}
    assert validate.await_args_list[0].args[0].subtask_by_id("s1").description == "step 3"
    (normalized,) = _decisions(tmp_path, "structured_plan_normalized")
    assert (normalized["merges"], normalized["immutable_test_redirects"], normalized["derived_contracts"],
            normalized["verification_scope"], normalized["repair_attempt"]) == (
        ["merge-1"], ["redirect-1"], ["derived-1"], ["scope-1"], 0)
    assert calls[0]["structured_plan"].subtask_by_id("s1").description == "step 3"


@pytest.mark.asyncio
async def test_validate_plan_receives_the_sealed_requirement_ids_and_frozen_tests(tmp_path):
    """EV10 (known requirement ids dropped) / EV11 (the TEST_IMMUTABILITY frozen tests dropped)."""
    goal = "Create a.py. Keep b.py."
    frozen = patch("kriya.workflow.workflow.immutable_test_files", return_value=["tests/test_x.py"])
    _r, _c, validate, _ = await _enforce_rounds(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), [_VALID],
                                                goal=goal, extra=[frozen])
    kwargs = validate.await_args_list[0].kwargs
    assert kwargs["known_requirement_ids"] == ["REQ-1", "REQ-2"]
    assert kwargs["immutable_test_files"] == ["tests/test_x.py"]


@pytest.mark.asyncio
async def test_plan_target_errors_fail_the_round_and_only_inconsistent_targets_reach_the_repair(tmp_path):
    """EV13 (target errors ignored) / EV14 (consistent target evidence forwarded) / EV15 (the targets record not
    written) / EV37 (the evidence not forwarded to the repair prompt)."""
    inconsistent = {"subtask_id": "s1", "verdict": "inconsistent", "path": "a.py"}
    consistent = {"subtask_id": "s1", "verdict": "consistent", "path": "a.py"}
    targets = MagicMock(side_effect=[(["target a.py disagrees"], ["PLAN_TARGET_INCONSISTENT"], [inconsistent, consistent]),
                                     ([], [], [])])
    result, calls, _v, repair = await _enforce_rounds(
        _enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), [_VALID, _VALID],
        extra=[patch(f"{C}.check_plan_targets", targets)])
    assert result.legacy_result["status"] == "success" and len(calls) == 1
    (args, kwargs) = repair.call_args
    assert args[2] == ["target a.py disagrees"] and args[3] == ["PLAN_TARGET_INCONSISTENT"]
    assert kwargs["validation_evidence"] == [inconsistent]
    (record,) = _decisions(tmp_path, "structured_plan_targets")
    assert record["targets"] == [inconsistent, consistent] and record["repair_attempt"] == 0


@pytest.mark.asyncio
async def test_an_unbounded_model_subtask_is_reported_unless_validation_already_said_so(tmp_path):
    """EV17: the MODEL_SUBTASK_MISSING_PLANNED_FILES code is suppressed only by validation's own 'declares no
    planned_files' error, not by any error mentioning 'declares'."""
    unbounded = _plan_of(Subtask(id="s1", description="do it", execution_method=ExecutionMethod.MODEL,
                                 verification=[_COMPILE]))
    first = PlanValidationResult(valid=False, errors=["s9 declares an unknown tool"], reason_codes=["X_CODE"])
    result, *_ = await _enforce_rounds(_enforce_engine(tmp_path), tmp_path, unbounded, [first, first, first])
    legacy = result.legacy_result
    assert "MODEL_SUBTASK_MISSING_PLANNED_FILES" in legacy["reason_codes"]
    assert legacy["invalid_subtask_ids"] == ["s1"]


@pytest.mark.asyncio
async def test_grounded_gaps_and_infeasible_edits_fail_the_round_with_their_codes(tmp_path):
    """EV19 (MISWIRED_GROUNDED_DEPENDENCY_EDGE) / EV22 (PLANNED_EDIT_INFEASIBLE) / EV23 (the infeasible unit not
    named) / EV24 (codes not de-duplicated)."""
    from kriya.workflow.edit_feasibility import EditFeasibility, Feasibility
    gap = {"reason": "not_in_dependency_chain", "test_file": "t.py", "consumer_subtask": "s1",
           "missing_production_artifact": "a.py", "owning_subtask": "s0"}
    infeasible = EditFeasibility("s1", "a.py", Feasibility.INFEASIBLE, "too big",
                                 {"required_capacity_tokens": 9, "available_capacity_tokens": 1})
    dup = PlanValidationResult(valid=False, errors=["e"], reason_codes=["DUP_CODE", "DUP_CODE"])
    result, *_ = await _enforce_rounds(
        _enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), [dup, dup, dup],
        extra=[patch(f"{C}.find_missing_grounded_production_artifacts", return_value=[gap]),
               patch(f"{C}.evaluate_plan_edit_feasibility", new=AsyncMock(return_value=[infeasible]))])
    codes = result.legacy_result["reason_codes"]
    assert codes[:3] == ["DUP_CODE", "MISWIRED_GROUNDED_DEPENDENCY_EDGE", "PLANNED_EDIT_INFEASIBLE"]
    assert codes.count("DUP_CODE") == 1 and result.legacy_result["invalid_subtask_ids"] == ["s1"]
    rounds = _decisions(tmp_path, "structured_plan_validation")
    assert [r["reason_codes"][:3] for r in rounds] == [["DUP_CODE", "MISWIRED_GROUNDED_DEPENDENCY_EDGE",
                                                       "PLANNED_EDIT_INFEASIBLE"]] * 3


async def _regressed_round(tmp_path, regression, code):
    """EV25 / EV26: a later candidate that regressed previously validated facts is never accepted, even when
    validate_plan passes it; the next repair is seeded from the retained round."""
    result, calls, _v, repair = await _enforce_rounds(
        _enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), [_INVALID, _VALID, _VALID], extra=[regression])
    assert result.legacy_result["status"] == "success" and result.legacy_result["plan_repair_attempts"] == 2
    (first, second, third) = _decisions(tmp_path, "structured_plan_validation")
    assert (first["valid"], second["valid"], second["reason_codes"], third["valid"]) == (False, False, [code], True)
    assert [c.args[3] for c in repair.call_args_list] == [["PLAN_VALIDATION_FAILED"], ["PLAN_VALIDATION_FAILED"]]


@pytest.mark.asyncio
async def test_a_silent_semantic_contract_regression_rejects_an_otherwise_valid_plan(tmp_path):
    """EV25 (SEMANTIC_CONTRACT_REGRESSION_REJECTED dropped)."""
    await _regressed_round(tmp_path, patch(f"{C}._semantic_contract_regression_subtasks",
                                           MagicMock(side_effect=[[], ["s1"], []])),
                           "SEMANTIC_CONTRACT_REGRESSION_REJECTED")


@pytest.mark.asyncio
async def test_a_silent_preserved_reference_regression_rejects_an_otherwise_valid_plan(tmp_path):
    """EV26 (PRESERVED_REFERENCE_REGRESSION_REJECTED dropped)."""
    await _regressed_round(tmp_path, patch(f"{C}._preserved_reference_regressions",
                                           MagicMock(side_effect=[[], [("t.py", "a.py")], []])),
                           "PRESERVED_REFERENCE_REGRESSION_REJECTED")


@pytest.mark.asyncio
async def test_the_repair_prompt_must_preserve_every_validated_fact(tmp_path):
    """EV35 / EV36 / EV38: the preserved-reference and semantic-contract lines reach the repair prompt."""
    result, calls, _v, repair = await _enforce_rounds(
        _enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), [_INVALID, _VALID],
        extra=[patch(f"{C}._preserved_reference_must_preserve_lines", return_value=["PRESERVED-LINE"]),
               patch(f"{C}._semantic_contract_must_preserve_lines", return_value=["SEMANTIC-LINE"])])
    assert result.legacy_result["status"] == "success"
    must = repair.call_args.kwargs["must_preserve"]
    assert must[-2:] == ["PRESERVED-LINE", "SEMANTIC-LINE"]


@pytest.mark.asyncio
async def test_exhaustion_after_a_regression_reports_the_retained_baseline(tmp_path):
    """EV40 / EV41: when the last round was rejected as a regression (a strict superset of the retained round's
    codes), the terminal report carries the retained round's codes and invalid units."""
    from kriya.workflow.edit_feasibility import EditFeasibility, Feasibility

    def infeasible(sid):
        return [EditFeasibility(sid, "a.py", Feasibility.INFEASIBLE, "too big",
                                {"required_capacity_tokens": 9, "available_capacity_tokens": 1})]
    round0 = PlanValidationResult(valid=False, errors=["a"], reason_codes=["CODE_A"])
    worse = PlanValidationResult(valid=False, errors=["a", "b"], reason_codes=["CODE_A", "CODE_B"])
    plan = _plan_of(_unit("s1"), _unit("s2", "b.py", depends_on=["s1"]))
    feasibility = AsyncMock(side_effect=[infeasible("s1"), infeasible("s2"), infeasible("s2")])
    result, *_ = await _enforce_rounds(_enforce_engine(tmp_path), tmp_path, plan, [round0, worse, worse],
                                       extra=[patch(f"{C}.evaluate_plan_edit_feasibility", new=feasibility)])
    legacy = result.legacy_result
    assert legacy["invalid_subtask_ids"] == ["s1"]
    assert sorted(legacy["reason_codes"][:2]) == ["CODE_A", "PLANNED_EDIT_INFEASIBLE"]
    assert "CODE_B" not in legacy["reason_codes"] and "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in legacy["reason_codes"]
    assert legacy["plan_repair_attempts"] == 2


@pytest.mark.asyncio
async def test_the_repair_round_sends_the_fitted_repair_prompt(tmp_path):
    """EV46 (the repair request not fitted per candidate) / EP23 (the first request not fitted): with a reference
    to fit, both Planner requests carry the fenced reference after the request text."""
    reference = "\n[Source: docs/x.md]\nREFERENCE-BODY\n"
    we = _enforce_engine(tmp_path)
    result, *_ = await _enforce_rounds(we, tmp_path, _plan_of(_unit("s1")), [_INVALID, _VALID],
                                       reference_context=reference)
    first, second = (call.args[0] for call in we.planner.run.await_args_list)
    assert "REFERENCE-BODY" in first and "REFERENCE-BODY" in second
    assert second.startswith("REPAIR-PROMPT round 1")
    assert we.planner.run.await_args_list[1].kwargs["candidate_prompt"] is not None


# ============================================================================ enforce: the plan sandbox

@pytest.mark.asyncio
async def test_a_worktree_sync_refusal_is_a_typed_stop_not_a_planning_problem(tmp_path):
    """EA01: FILE-INTEGRITY-CONTRACT-001's WorktreeSyncError reaches execute() unchanged."""
    from kriya.workflow.worktree import WorktreeSyncError
    we = _enforce_engine(tmp_path)
    we.run_generation_workflow = _writer(tmp_path, [])
    p1, p2, p3 = _planned(_plan_of(_unit("s1")))
    with p1, p2, p3, patch(f"{C}.create_git_worktree", side_effect=WorktreeSyncError("WORKTREE_SYNC_INCOMPLETE", "x")):
        result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    assert (legacy["failure_type"], legacy["reason_codes"]) == ("WORKTREE_SYNC", ["WORKTREE_SYNC_INCOMPLETE"])


@pytest.mark.asyncio
async def test_a_sandbox_bootstrap_failure_is_a_pre_execution_planning_stop(tmp_path):
    """EA02: any other worktree failure becomes _StructuredPlanUnavailable (needs_review), never a crash."""
    we = _enforce_engine(tmp_path)
    calls = []
    we.run_generation_workflow = _writer(tmp_path, calls)
    p1, p2, p3 = _planned(_plan_of(_unit("s1")))
    with p1, p2, p3, patch(f"{C}.create_git_worktree", side_effect=RuntimeError("git missing")):
        result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    assert (legacy["status"], legacy["reason_codes"]) == ("needs_review", ["STRUCTURED_PLAN_UNAVAILABLE"])
    assert "git missing" in legacy["error"] and calls == []


# ============================================================================ enforce: the bounded subtask invocation

def _runtime_check():
    return VerificationMethod(type=VerificationMethodType.JUDGMENT, description="run the app",
                              verifier_kind=VerifierKind.APPLICATION_RUNTIME, requires_runtime_execution=True)


@pytest.mark.asyncio
async def test_each_bounded_subtask_call_carries_its_scope_and_the_run_wide_state(tmp_path):
    """EB03 / EB04 / EB06 / EB07 / EB08 / EB09 / EB18 / EB19: the run_generation_workflow arguments of two
    successive units."""
    we = _enforce_engine(tmp_path)
    we.kernel.config.process_profiles.enabled = True
    we.kernel.config.process_profiles.enforce_context_depth = True
    plan = _plan_of(_unit("s1", "a.py"),
                    Subtask(id="s2", description="write b.py", execution_method=ExecutionMethod.MODEL,
                            depends_on=["s1"], planned_files=[PlannedFile(path="b.py", action=FileAction.CREATE)],
                            verification=[_runtime_check()]))
    result, calls = await _enforce(we, tmp_path, plan, resume=False, trace_id_override="trace-x")
    first, second = calls
    assert result.legacy_result["status"] == "success"
    assert (first["runtime_verification_required"], second["runtime_verification_required"]) == (False, True)
    assert first["strict_spec_compliance"] is True and second["strict_dependency_index"] is True
    assert first["enforce_run_id"] == f"{result.run_id}.enforce"
    assert first["resume"] is False and "trace_id_override" not in first
    assert (first["completed_subtask_ids"], second["completed_subtask_ids"]) == (frozenset(), frozenset({"s1"}))
    assert first["deterministic_failure_diagnostics"] is not None
    assert first["deterministic_failure_diagnostics"] is second["deterministic_failure_diagnostics"]
    assert first["migration_resolution"] is not None and first["migration_resolution"] is second["migration_resolution"]
    assert first["reopened_owner"] is False and first["execution_scope"] == "subtask=s1 role=planned"


@pytest.mark.asyncio
async def test_a_subtask_sees_only_its_own_ownership_findings(tmp_path):
    """EB11: findings are projected per unit, never every unit's."""
    findings = [SimpleNamespace(subtask_id="s2", status="OPEN", to_dict=lambda: {"subtask_id": "s2"})]
    rendered = patch(f"{C}.findings_prompt_block",
                     side_effect=lambda fs: ("FINDINGS:" + ",".join(f.subtask_id for f in fs)) if fs else "")
    plan = _plan_of(_unit("s1", "a.py"), _unit("s2", "b.py", depends_on=["s1"]))
    with patch(f"{C}.settle_findings", return_value=findings), patch(f"{C}.record_findings"), rendered:
        _result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, plan)
    assert "FINDINGS:" not in calls[0]["supplementary_context"]
    assert "FINDINGS:s2" in calls[1]["supplementary_context"]


# ============================================================================ enforce: per-subtask acceptance

@pytest.mark.asyncio
async def test_a_resumed_subtasks_content_is_established_for_the_next_one(tmp_path):
    """EL02 / EL27 (resumed content not threaded) / EL25 (resumed provenance attributed to another unit)."""
    from kriya.workflow import workflow_controller as wc
    await _interrupted(tmp_path, _two_units())
    records = []

    class Recording(wc.EstablishedProvenance):
        def record(self, path, subtask_id):
            records.append((path, subtask_id))
            super().record(path, subtask_id)
    with patch(f"{C}.EstablishedProvenance", Recording):
        _result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _two_units(), resume=True)
    (only,) = calls
    assert only["established_files"] == ["a.py"] and "# a.py by s1" in only["supplementary_context"]
    assert records[0] == ("a.py", "s1")


def _tool(sid="t1"):
    return Subtask(id=sid, description="run a command", execution_method=ExecutionMethod.TOOL,
                   tool_name="shell", tool_arguments={"command": "true"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status, lifecycle", [(SubtaskStatus.NEEDS_REVIEW, "needs_review"),
                                               (SubtaskStatus.FAILED, "failed")])
async def test_a_tool_subtask_that_did_not_complete_stops_the_run(tmp_path, status, lifecycle):
    """EL05 (needs_review recorded as failed) / EL06 (the run continues) / EL07 (the tool unit's evidence lacks its
    result) / EL08 (the status persisted as completed)."""
    outcome = SubtaskResult(subtask_id="t1", status=status, execution_method="tool", error="denied")
    units = []

    @contextlib.contextmanager
    def tool_unit(config, subtask_id, tool_name):
        unit = {"subtask_id": subtask_id, "tool_name": tool_name}
        units.append(unit)
        yield unit
    saved = []
    plan = _plan_of(_tool(), _unit("s2", "b.py", depends_on=["t1"]))
    with patch(f"{C}.subtask_executor.execute", new=AsyncMock(return_value=outcome)), \
         patch("kriya.core.attempt_evidence.scope.tool_unit", tool_unit), \
         patch(f"{C}.save_approved_plan", side_effect=lambda ws, pid, doc: saved.append(doc["lifecycle_state"])):
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, plan)
    assert calls == []
    assert units == [{"subtask_id": "t1", "tool_name": "shell", "result": outcome}]
    assert saved[:3] == ["approved", "in_progress", lifecycle]
    assert result.control_state.subtask_states["t1"] == status.value
    assert result.legacy_result["status"] == ("needs_review" if status == SubtaskStatus.NEEDS_REVIEW else "failed")


@pytest.mark.asyncio
async def test_a_refused_final_review_is_the_subtasks_reported_cause(tmp_path):
    """EL12 / EL17: the review refusal's detail and reason code, not the generic Quality Gates text."""
    refusal = {"detail": "the review request cannot fit", "reason_code": "FINAL_REVIEW_UNSENDABLE"}
    result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")),
                               outcomes={"s1": {"quality_gates_passed": False, "final_review_refusal": refusal}})
    (s1,) = result.legacy_result["subtask_results"]
    assert s1["status"] == "failed"
    assert s1["error"] == "subtask final review not performed: the review request cannot fit"
    assert s1["reason_codes"] == ["FINAL_REVIEW_UNSENDABLE"]


@pytest.mark.asyncio
async def test_a_scope_conflict_is_the_subtasks_reported_cause(tmp_path):
    """EL13: the conflict report's text, NEEDS_REVIEW, and the aggregated PLAN_SCOPE_REVISION_REQUIRED; EL20 (the
    plan lifecycle failed instead of needs_review)."""
    conflict = {"classification": "PLAN_SCOPE_INSUFFICIENT", "reason_code": "PLAN_TARGET_RELOCALIZATION_REQUIRED",
                "reason": "the target moved", "required_files": []}
    saved = []
    with patch(f"{C}.save_approved_plan", side_effect=lambda ws, pid, doc: saved.append(doc["lifecycle_state"])):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")),
                                   outcomes={"s1": {"quality_gates_passed": False, "plan_scope_conflict": conflict}})
    assert saved == ["approved", "in_progress", "needs_review", "needs_review"]
    legacy = result.legacy_result
    (s1,) = legacy["subtask_results"]
    assert s1["status"] == "needs_review"
    assert s1["error"] == ("subtask repair requires approved-plan revision (PLAN_TARGET_RELOCALIZATION_REQUIRED): "
                           "the target moved")
    assert legacy["failure_type"] == "PLANNING_ERROR"
    assert legacy["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED", "PLAN_TARGET_RELOCALIZATION_REQUIRED"]


@pytest.mark.asyncio
async def test_missing_verification_evidence_is_the_subtasks_reported_cause(tmp_path):
    """EL14: gates passed but the declared verification has no evidence -> NEEDS_REVIEW with that reason."""
    result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")),
                               outcomes={"s1": {"verification_results": []}})
    (s1,) = result.legacy_result["subtask_results"]
    assert (s1["status"], s1["reason_codes"]) == ("needs_review", ["VERIFICATION_EVIDENCE_MISSING"])
    assert s1["error"] == "declared verification requirements have no complete authoritative evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("passed", [True, False])
async def test_verified_no_change_is_recorded_only_for_a_passing_unit(tmp_path, passed):
    """EL18: a failed unit never carries VERIFIED_NO_CHANGE."""
    result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")),
                               outcomes={"s1": {"completion_kind": "VERIFIED_NO_CHANGE", "quality_gates_passed": passed}})
    (s1,) = result.legacy_result["subtask_results"]
    assert s1["reason_codes"] == (["VERIFIED_NO_CHANGE"] if passed else [])


# ============================================================================ enforce: plan revisions from a unit's conflict

def _owner_plan():
    return _plan_of(_unit("s0", "dep.py"), _unit("s1", "a.py", depends_on=["s0"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("revalidates", [True, False])
async def test_a_planned_prerequisite_conflict_revises_the_plan_only_when_it_revalidates(tmp_path, revalidates):
    """ES01 (the prerequisite branch skipped) / ES02 / ES03 (the event) / ES04 (a failed revalidation's reason
    dropped)."""
    conflict = {"reason_code": "PLANNED_PREREQUISITE_OWNER_REQUIRED", "required_owner_subtask_id": "s0",
                "required_capability": "dep_api", "consumer_artifacts": ["a.py"], "reason": "needs dep_api",
                "required_files": ["dep.py"], "failure_type": "COMPILE_ERROR"}
    plan = _owner_plan()
    revised = _variant(plan, "s1", "write a.py after s0 provides dep_api")
    second = PlanValidationResult(valid=True) if revalidates else PlanValidationResult(valid=False, errors=["cycle"])
    we = _enforce_engine(tmp_path)
    calls = []
    we.run_generation_workflow = _writer(tmp_path, calls, {"s1": {"quality_gates_passed": False,
                                                                  "plan_scope_conflict": conflict}})
    with patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan), \
         patch(f"{C}.validate_plan", new=AsyncMock(side_effect=[_VALID, second])), \
         patch(f"{C}.revise_plan_for_planned_prerequisite", return_value=revised) as revise, \
         patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace):
        result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    assert revise.call_args.kwargs == {"consumer_subtask_id": "s1", "owner_subtask_id": "s0",
                                       "capability": "dep_api", "consumer_artifacts": ["a.py"]}
    events = legacy["plan_recovery_events"]
    reopen = [e for e in events if e.get("reopened_owner") == "s0"]
    assert len(reopen) == 1
    if revalidates:
        assert (events[0]["reason_code"], events[0]["prerequisite_owner"], events[0]["revised_plan_hash"]) == (
            "PLANNED_PREREQUISITE_OWNER_REQUIRED", "s0", revised.content_hash())
        assert reopen[0]["reason"] == "needs dep_api"
    else:
        assert "reason_code" not in events[0]
        assert reopen[0]["reason"] == "planned prerequisite wiring failed revalidation: cycle"


@pytest.mark.asyncio
async def test_a_runtime_plan_gap_without_a_single_owner_is_never_guessed(tmp_path):
    """ES07: a resolvable path with no compatible owner records INDETERMINATE and revises nothing."""
    conflict = {"reason_code": "RUNTIME_PLAN_GAP", "missing_logical_artifact": "app.wsgi", "reason": "not planned"}
    with patch(f"{C}.resolve_python_module_to_candidate_artifact_path", return_value="app/wsgi.py"), \
         patch(f"{C}.resolve_runtime_plan_gap_owner", return_value=None), \
         patch(f"{C}.revise_plan_for_runtime_plan_gap") as revise:
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _owner_plan(),
                                       outcomes={"s1": {"quality_gates_passed": False,
                                                        "plan_scope_conflict": conflict}})
    revise.assert_not_called()
    assert [c["current_subtask_id"] for c in calls] == ["s0", "s1"]
    assert "plan_recovery_events" not in result.legacy_result


# ============================================================================ enforce: scope revision and owner recovery

def _scripted(tmp_path, calls, script):
    """run_generation_workflow answering ``script[(subtask, role)]`` (a list, consumed in order); every call writes
    its allowed files (or ``_write``, or nothing with ``_skip_write``) with call-unique content, so a repeated owner
    call is never byte-identical unless it writes nothing."""
    async def run(**kwargs):
        calls.append(kwargs)
        sid, role = kwargs["current_subtask_id"], kwargs["execution_scope"].split("role=")[1]
        queue = script.get((sid, role)) or []
        scripted = dict(queue.pop(0)) if queue else {}
        files = list(kwargs["allowed_write_relpaths"] or [])
        for rel in ([] if scripted.pop("_skip_write", False) else scripted.pop("_write", files)):
            path = os.path.join(kwargs["workspace_path"], rel)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"# {rel} by {sid} call {len(calls)}\n")
        evidence = [{"type": vm["type"], "tool_name": vm.get("tool_name"), "description": vm["description"],
                     "passed": True} for vm in kwargs.get("required_verification") or []]
        answer = {"status": "success", "quality_gates_passed": True, "files": files, "verification_results": evidence}
        answer.update(scripted)
        return answer
    return run


async def _enforce_scripted(we, tmp_path, plan, script, *, validations=None, extra=()):
    calls = []
    we.run_generation_workflow = _scripted(tmp_path, calls, script)
    validate = AsyncMock(side_effect=list(validations)) if validations else AsyncMock(return_value=_VALID)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch(f"{C}.parse_planner_structured_output", return_value=(MagicMock(), None)))
        stack.enter_context(patch(f"{C}.build_engineering_plan_from_planner_output", return_value=plan))
        stack.enter_context(patch(f"{C}.validate_plan", new=validate))
        stack.enter_context(patch(f"{C}.create_git_worktree", side_effect=lambda workspace: workspace))
        for extra_patch in extra:
            stack.enter_context(extra_patch)
        result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    return result, calls


def _defect(path, line=3, tier="architectural_owner"):
    return {"quality_gates_passed": False, "status": "failed", "plan_scope_conflict": {
        "classification": "PLAN_SCOPE_DEFECT", "required_files": [path], "attribution_tier": tier,
        "failure_type": "COMPILE_ERROR", "reason": f"{path} must change",
        "grounded_locations": [{"path": path, "line": line}]}}


@pytest.mark.asyncio
async def test_a_chain_of_grounded_owners_is_revised_at_most_the_bound_times(tmp_path):
    """EM04 (the revision loop bounded at 1) / EM08 (the next grounded owner not re-derived) / EM10 (the revised unit
    not in progress) / EB20 / EM12 (the failure's grounded loci not shown to the re-invoked unit) / EM11 (the scope
    recovery event's basis)."""
    script = {("s1", "planned"): [_defect("x1.py", 1)],
              ("s1", "plan_scope_recovery"): [_defect("x2.py", 2), _defect("x3.py", 3), _defect("x4.py", 4)]}
    saved = []
    for index in range(1, 5):
        (tmp_path / f"x{index}.py").write_text(f"owner {index}\n")
    result, calls = await _enforce_scripted(
        _enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), script,
        extra=[patch(f"{C}.save_approved_plan", side_effect=lambda ws, pid, doc: saved.append(doc))])
    recoveries = [c for c in calls if c["execution_scope"] == "subtask=s1 role=plan_scope_recovery"]
    assert len(recoveries) == 3
    assert [c["grounded_locations"] for c in recoveries] == [
        [{"path": "x1.py", "line": 1}], [{"path": "x2.py", "line": 2}], [{"path": "x3.py", "line": 3}]]
    assert recoveries[-1]["allowed_write_relpaths"] == ["a.py", "x1.py", "x2.py", "x3.py"]
    revised = [d for d in saved if d["lifecycle_state"] == "scope_revised"]
    assert len(revised) == 3 and all(d["stage_states"]["s1"] == "in_progress" for d in revised)
    assert len([e for e in result.legacy_result["plan_recovery_events"]
                if e.get("revalidation_basis") == "deterministic grounded owner plus validate_plan"]) == 3


def _recovery_plan(plan, actions):
    from kriya.workflow.recovery_plan import (
        RecoveryExecutionPlan,
        RecoveryOwnerGroup,
        RecoveryParticipant,
        RecoveryParticipantRole,
    )
    participants = tuple(RecoveryParticipant(artifact=f"dep{i}.py", role=RecoveryParticipantRole.REQUIRED_MUTATION,
                                             effective_owner_subtask_id="s0", owner_resolution_basis="declared",
                                             mutation_reason="named", recovery_action=action)
                         for i, action in enumerate(actions))
    group = RecoveryOwnerGroup(group_id="group.s0", owner_subtask_id="s0", participants=participants,
                               depends_on_group_ids=(), relationship_basis="plan_dependency")
    return RecoveryExecutionPlan(id="recovery.s1.0", originating_subtask_id="s1", scope_conflict={},
                                 participants=participants, groups=(group,), group_order=("group.s0",))


def _once_for_s1(recovery):
    """build_recovery_execution_plan: ``recovery`` for s1's first non-empty conflict, nothing otherwise."""
    served = []

    def build(plan, scope_conflict, subtask, **kwargs):
        if subtask.id == "s1" and scope_conflict and not served:
            served.append(recovery)
            return recovery
        return None
    return build


def _owner_two_files():
    return _plan_of(Subtask(id="s0", description="write deps", execution_method=ExecutionMethod.MODEL,
                            planned_files=[PlannedFile(path="dep0.py", action=FileAction.CREATE),
                                           PlannedFile(path="dep1.py", action=FileAction.CREATE)],
                            verification=[_COMPILE]),
                    _unit("s1", "a.py", depends_on=["s0"]))


def _insufficient():
    return {"quality_gates_passed": False, "status": "failed", "plan_scope_conflict": {
        "classification": "PLAN_SCOPE_INSUFFICIENT", "required_files": ["dep0.py", "dep1.py"],
        "failure_type": "COMPILE_ERROR", "reason": "deps must change"}}


@pytest.mark.asyncio
async def test_a_group_with_any_must_change_participant_reopens_its_owner_and_retries_the_consumer(tmp_path):
    """EO04 (a mixed group treated as VERIFY) / EO22 (the consumer retry's role) / EO25 (the cross-owner obligation
    closed) / EO26 (the closing record re-stamped DETERMINISTIC) / EO20 (the reopened owner's integration obligations
    not evaluated): the owner is reopened, the consumer retried, the obligation SATISFIED with the recovery."""
    from kriya.workflow.obligations import ObligationAuthority, ObligationKind, ObligationLedger, ObligationStatus
    from kriya.workflow.recovery_plan import RecoveryAction
    records = []
    real_record = ObligationLedger.record

    def record(self, rec):
        records.append(rec)
        return real_record(self, rec)
    from kriya.workflow import workflow_controller as wc
    plan = _owner_two_files()
    mixed = _recovery_plan(plan, [RecoveryAction.MUST_CHANGE, RecoveryAction.VERIFY])
    with patch(f"{C}._evaluate_integration_obligations", wraps=wc._evaluate_integration_obligations) as evaluate:
        result, calls = await _enforce_scripted(
            _enforce_engine(tmp_path), tmp_path, plan, {("s1", "planned"): [_insufficient()]},
            extra=[patch(f"{C}.build_recovery_execution_plan", side_effect=_once_for_s1(mixed)),
                   patch.object(ObligationLedger, "record", record)])
    assert [(c.args[2], c.args[4]) for c in evaluate.call_args_list] == [("s0", 1), ("s0", 1), ("s1", 2)]
    roles = [c["execution_scope"] for c in calls]
    assert roles == ["subtask=s0 role=planned", "subtask=s1 role=planned", "subtask=s0 role=owner_recovery",
                     "subtask=s1 role=consumer_retry"]
    satisfied = [r for r in records if r.kind == ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT
                 and r.status == ObligationStatus.SATISFIED]
    assert len(satisfied) == 1 and result.legacy_result["status"] == "success"
    (created,) = [r for r in records if r.kind == ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT
                  and r.status != ObligationStatus.SATISFIED]
    assert satisfied[0].authority == created.authority == ObligationAuthority.JUDGMENT


@pytest.mark.asyncio
async def test_a_failed_consumer_retry_never_closes_the_cross_owner_obligation(tmp_path):
    """EO25 (SATISFIED recorded although the recovery was rejected) / EO08 (the owner's dependents not invalidated)
    / EO28 / EO27 (the rejected owner persisted and staged completed) / EO09 (its dependents not invalidated in the
    ControlState) / EO37 (the plan lifecycle after a rejected recovery)."""
    from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationStatus
    from kriya.workflow.recovery_plan import RecoveryAction
    records = []
    real_record = ObligationLedger.record

    def record(self, rec):
        records.append(rec)
        return real_record(self, rec)
    plan = _owner_two_files()
    script = {("s1", "planned"): [_insufficient()],
              ("s1", "consumer_retry"): [{"quality_gates_passed": False, "status": "failed"}]}
    from kriya.control.persistence import save_control_state as real_save_cs
    saved, persisted, trail = [], [], []
    result, calls = await _enforce_scripted(
        _enforce_engine(tmp_path), tmp_path, plan, script,
        extra=[patch(f"{C}.build_recovery_execution_plan",
                     side_effect=_once_for_s1(_recovery_plan(plan, [RecoveryAction.MUST_CHANGE]))),
               patch.object(ObligationLedger, "record", record),
               patch(f"{C}.save_approved_plan", side_effect=lambda ws, pid, doc: saved.append(doc) or trail.append(doc)),
               patch(f"{C}.save_control_state",
                     side_effect=lambda ws, cs: persisted.append(cs) or trail.append(cs) or real_save_cs(ws, cs))])
    assert [d["lifecycle_state"] for d in saved] == ["approved", "in_progress", "in_progress", "in_progress",
                                                     "recovering", "in_progress", "needs_review", "failed", "failed"]
    recovering = [d for d in saved if d["lifecycle_state"] == "recovering"]
    assert [d["stage_states"] for d in recovering] == [{"s0": "in_progress", "s1": "pending"}]
    assert [d["stage_states"]["s0"] for d in saved if d["lifecycle_state"] == "needs_review"] == ["needs_review"]
    recovering_at = next(i for i, item in enumerate(trail) if isinstance(item, dict)
                         and item["lifecycle_state"] == "recovering")
    assert dict(trail[recovering_at + 1].subtask_states) == {"s0": "in_progress", "s1": "pending"}
    assert result.control_state.subtask_states["s0"] == "needs_review"
    kinds = [(r.kind, r.status) for r in records if r.kind == ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT]
    assert kinds and (ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT, ObligationStatus.SATISFIED) not in kinds
    assert result.legacy_result["status"] == "failed"


@pytest.mark.asyncio
async def test_a_requirement_that_exhausted_its_recovery_attempts_never_reopens_the_owner(tmp_path):
    """EO07 (the bound checked with > instead of >=): a requirement with the bound's number of prior attempts stops."""
    from kriya.workflow.obligations import ObligationLedger
    from kriya.workflow.recovery_plan import RecoveryAction
    real_history = ObligationLedger.history

    def history(self, obligation_id):
        return [object()] * 3 if obligation_id.startswith("recovery.") else real_history(self, obligation_id)
    plan = _owner_two_files()
    result, calls = await _enforce_scripted(
        _enforce_engine(tmp_path), tmp_path, plan, {("s1", "planned"): [_insufficient()]},
        extra=[patch(f"{C}.build_recovery_execution_plan",
                     side_effect=_once_for_s1(_recovery_plan(plan, [RecoveryAction.MUST_CHANGE]))),
               patch.object(ObligationLedger, "history", history)])
    assert [c["execution_scope"] for c in calls] == ["subtask=s0 role=planned", "subtask=s1 role=planned"]
    assert result.legacy_result["status"] == "needs_review"


# ============================================================================ enforce: runtime-evidence plan repair

def _gap(artifact="app.wsgi"):
    return {"quality_gates_passed": False, "status": "failed", "plan_scope_conflict": {
        "reason_code": "RUNTIME_PLAN_GAP", "missing_logical_artifact": artifact, "failure_type": "RUNTIME_ERROR",
        "reason": "imported but never planned"}}


def _recorder():
    from kriya.workflow.obligations import ObligationLedger
    records = []
    real = ObligationLedger.record

    def record(self, rec):
        records.append(rec)
        return real(self, rec)
    return records, patch.object(ObligationLedger, "record", record)


def _gap_records(records):
    from kriya.workflow.obligations import ObligationKind
    return [(r.id, r.status.value, r.owner_subtask_id) for r in records if r.kind == ObligationKind.RUNTIME_PLAN_GAP]


@pytest.mark.asyncio
@pytest.mark.parametrize("revalidates", [True, False])
async def test_a_runtime_plan_gap_adds_the_artifact_to_its_owner_only_when_the_revision_revalidates(tmp_path, revalidates):
    """ES05 (the runtime-gap branch skipped) / ES08 (the obligation status not from the revalidation) / ES09 (a valid
    revision not swapped in) / ES10 (the reshaped conflict's attribution tier): a valid revision is recorded PENDING,
    swapped in, and the reshaped architectural_owner conflict goes to the merge path when no effective owner exists;
    a rejected one is VIOLATED and changes nothing."""
    records, recording = _recorder()
    revision = _VALID if revalidates else PlanValidationResult(valid=False, errors=["dup file"])
    from kriya.workflow import workflow_controller as wc
    with patch(f"{C}.revise_plan_for_grounded_scope_owner", side_effect=lambda plan, *args: plan) as merge:
        result, calls = await _enforce_scripted(
            _enforce_engine(tmp_path), tmp_path, _owner_plan(), {("s1", "planned"): [_gap()]},
            validations=[_VALID, revision, _VALID, _VALID, _VALID],
            extra=[recording,
                   patch(f"{C}.resolve_python_module_to_candidate_artifact_path", return_value="app/wsgi.py"),
                   patch(f"{C}.resolve_runtime_plan_gap_owner", return_value="s0"),
                   patch(f"{C}.resolve_effective_scope_conflict_owners", return_value={}),
                   patch(f"{C}.revise_plan_for_runtime_plan_gap", wraps=wc.revise_plan_for_runtime_plan_gap)])
    gap_id = "runtime_plan_gap.s1.app.wsgi"
    if revalidates:
        assert _gap_records(records) == [(gap_id, "pending", "s0")]
        (event,) = [e for e in result.legacy_result["plan_recovery_events"] if e["classification"] == "RUNTIME_PLAN_GAP"]
        assert (event["new_artifact_path"], event["new_artifact_owner"]) == ("app/wsgi.py", "s0")
        assert merge.call_args.args[0].subtask_by_id("s0").planned_files[-1].path == "app/wsgi.py"
        assert merge.call_args.args[1:3] == ("s1", ["app/wsgi.py"])
    else:
        assert _gap_records(records) == [(gap_id, "violated", "s0")]
        assert "plan_recovery_events" not in result.legacy_result
        merge.assert_not_called()


@pytest.mark.asyncio
async def test_a_runtime_plan_gap_with_no_resolvable_owner_records_indeterminate(tmp_path):
    """ES12: a resolvable path without one compatible owner is recorded INDETERMINATE, owner unset."""
    records, recording = _recorder()
    result, calls = await _enforce_scripted(
        _enforce_engine(tmp_path), tmp_path, _owner_plan(), {("s1", "planned"): [_gap()]},
        extra=[recording, patch(f"{C}.resolve_python_module_to_candidate_artifact_path", return_value="app/wsgi.py"),
               patch(f"{C}.resolve_runtime_plan_gap_owner", return_value=None)])
    assert _gap_records(records) == [("runtime_plan_gap.s1.app.wsgi", "indeterminate", None)]


@pytest.mark.asyncio
async def test_a_runtime_plan_gap_seen_twice_before_is_no_progress_and_never_revised_again(tmp_path):
    """ES06 (the bound raised to 3) / ES11 (the no-progress obligation not VIOLATED)."""
    from kriya.workflow.obligations import ObligationLedger
    records, recording = _recorder()
    real_history = ObligationLedger.history

    def history(self, obligation_id):
        return [object()] * 2 if obligation_id.startswith("runtime_plan_gap.") else real_history(self, obligation_id)
    with patch(f"{C}.revise_plan_for_runtime_plan_gap") as revise:
        await _enforce_scripted(
            _enforce_engine(tmp_path), tmp_path, _owner_plan(), {("s1", "planned"): [_gap()]},
            extra=[recording, patch.object(ObligationLedger, "history", history),
                   patch(f"{C}.resolve_python_module_to_candidate_artifact_path", return_value="app/wsgi.py"),
                   patch(f"{C}.resolve_runtime_plan_gap_owner", return_value="s0")])
    revise.assert_not_called()
    assert _gap_records(records) == [("runtime_plan_gap.s1.app.wsgi", "violated", None)]


@pytest.mark.asyncio
async def test_integration_obligations_are_evaluated_after_every_completed_unit(tmp_path):
    """EL24: each completed unit is the consumer the integration check evaluates, at its position."""
    from kriya.workflow import workflow_controller as wc
    with patch(f"{C}._evaluate_integration_obligations", wraps=wc._evaluate_integration_obligations) as evaluate:
        await _enforce(_enforce_engine(tmp_path), tmp_path, _two_units())
    assert [(c.args[2], c.args[4]) for c in evaluate.call_args_list] == [("s1", 1), ("s2", 2)]
    assert all(c.kwargs["provenance"] is not None for c in evaluate.call_args_list)


# ============================================================================ enforce: the baseline authority run and the contract records

_PRESERVATION_GOAL = "Every existing test must keep passing unchanged."
_PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'


def _with_authority(monkeypatch):
    """The run's real sealed contract, given one bound external authority (admitted: the goal closes by the suite)."""
    import dataclasses

    from kriya.workflow import workflow as wf
    from kriya.workflow.contract_compilation import ExternalAuthority
    real = wf.compile_run_contract
    authority = ExternalAuthority("external_acceptance_command", "b" * 64,
                                  {"REQ-1": {"BEHAVIOR": {"accepted_strength": "GENERAL"}}})

    def compiled(*args, **kwargs):
        return dataclasses.replace(real(*args, **kwargs), authorities=(authority,))
    monkeypatch.setattr(wf, "compile_run_contract", compiled)


def _report(**fields):
    from kriya.workflow.contract_baseline import BaselineAuthorityReport
    return BaselineAuthorityReport(base_revision="r0", **fields)


@pytest.mark.asyncio
async def test_a_bound_authority_runs_at_baseline_and_its_report_is_returned(tmp_path, monkeypatch):
    """EP04 (the baseline never run) / ET50 (its report not returned)."""
    from kriya.workflow import workflow as wf
    _with_authority(monkeypatch)
    report = _report(authorities_run=[{"kind": "external_acceptance_command", "reason_code": "AUTHORITY_FAILED"}],
                     discriminating=True)
    baseline = MagicMock(return_value=report)
    monkeypatch.setattr(wf, "run_contract_baseline", baseline)
    we = _enforce_engine(tmp_path)
    result, calls = await _enforce(we, tmp_path, _plan_of(_unit("s1")), goal=_PRESERVATION_GOAL)
    (args, kwargs) = baseline.call_args
    assert args[0] is we and args[3] == str(tmp_path) and kwargs["config"] is we.kernel.config
    assert result.legacy_result["baseline_authority"] == report.to_dict() and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["raises", "unavailable", "no_mutation"])
async def test_the_baseline_decides_a_stop_before_any_model_call(tmp_path, monkeypatch, case):
    """EP05 (a raising baseline not turned into AUTHORITY_EXECUTION_UNAVAILABLE) / EP06 (an unavailable authority
    not a stop) / EP07 (a satisfied baseline not NO_MUTATION_REQUIRED)."""
    from kriya.workflow import workflow as wf
    _with_authority(monkeypatch)
    if case == "raises":
        baseline = MagicMock(side_effect=RuntimeError("docker gone"))
    elif case == "unavailable":
        baseline = MagicMock(return_value=_report(authorities_run=[
            {"kind": "external_acceptance_command", "reason_code": "AUTHORITY_EXECUTION_UNAVAILABLE", "reason": "x"}]))
    else:
        baseline = MagicMock(return_value=_report(no_mutation_required=True, discriminating=False))
    monkeypatch.setattr(wf, "run_contract_baseline", baseline)
    we = _enforce_engine(tmp_path)
    result, calls = await _enforce(we, tmp_path, _plan_of(_unit("s1")), goal=_PRESERVATION_GOAL)
    legacy = result.legacy_result
    assert we.planner.run.await_count == 0 and calls == []
    if case == "no_mutation":
        assert (legacy["status"], legacy["no_mutation_required"]) == ("success", True)
    else:
        assert (legacy["status"], legacy["failure_type"]) == ("failure", "VERIFICATION_AUTHORITY_UNAVAILABLE")
        (entry,) = legacy["baseline_authority"]["authorities_run"]
        assert entry["reason_code"] == "AUTHORITY_EXECUTION_UNAVAILABLE"
        if case == "raises":
            assert entry["reason"] == "the baseline authority run raised RuntimeError: docker gone"


@pytest.mark.asyncio
async def test_the_contract_non_claims_and_dispositions_are_recorded_once_on_the_run_ledger(tmp_path):
    """EP27 / EP28: both contract records reach the run's obligation ledger, from the controller's source."""
    from kriya.workflow import contract_compilation as cc
    with patch.object(cc, "record_contract_non_claims", wraps=cc.record_contract_non_claims) as non_claims, \
         patch.object(cc, "record_contract_dispositions", wraps=cc.record_contract_dispositions) as dispositions:
        result, calls = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")))
    for spy in (non_claims, dispositions):
        assert spy.call_count == 1
        assert spy.call_args.kwargs == {"source": "workflow_controller.verification_contract"}
    assert non_claims.call_args.args[0] is calls[0]["obligation_ledger"]


@pytest.mark.asyncio
async def test_a_request_trimmed_to_fit_is_recorded_and_one_that_fits_is_not(tmp_path):
    """EP20 (a trimmed structural section not recorded) / EP21 (an untrimmed reference recorded) / EP22 (no fit
    record at all)."""
    small = "\n[Source: docs/x.md]\nshort reference\n"
    await _enforce_rounds(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), [_VALID],
                          reference_context=small)
    from kriya.control.persistence import decision_ledger_path
    assert not os.path.isfile(decision_ledger_path(str(tmp_path))) or _decisions(tmp_path, "context.request_fit") == []
    big = tmp_path / "big"
    big.mkdir()
    huge_reference = "".join(f"\n[Source: docs/{i}.md]\n" + ("reference text " * 400) for i in range(400))
    structural = "\n".join(f"src/m{i}.py -> src/n{i}.py (imports, grounded)" + " x" * 60 for i in range(6000))
    with patch(f"{C}.build_planning_structural_evidence", return_value=(structural, {})):
        await _enforce_rounds(_enforce_engine(big), big, _plan_of(_unit("s1")), [_VALID],
                              reference_context=huge_reference)
    (fit,) = _decisions(big, "context.request_fit")
    assert fit["request"] == "structured_planner"
    assert fit["reference"] is not None and fit["structural_evidence"] is not None
    assert fit["structural_evidence"]["estimated_tokens_after"] < fit["structural_evidence"]["estimated_tokens_before"]


def _verification_plan():
    return _plan_of(_unit("s0", "a.py"),
                    Subtask(id="sv", description="verify a.py", execution_method=ExecutionMethod.MODEL,
                            execution_role=ExecutionRole.VERIFICATION, depends_on=["s0"], verification=[_COMPILE]))


_NO_CHANGE_POSSIBLE = {"quality_gates_passed": False, "status": "failed",
                       "no_progress_reason": "VERIFICATION_RETRY_NO_CHANGE_POSSIBLE"}


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_answer, accepted", [
    ({}, True),
    ({"verification_results": []}, False),
], ids=["verified_owner", "owner_without_verification_evidence"])
async def test_a_failed_verification_unit_reopens_its_owner_and_reruns_only_on_an_accepted_change(
        tmp_path, owner_answer, accepted):
    """EW05 (an owner whose declared verification has no evidence accepted) / EW10 (the rerun's role): the
    verification rerun happens only for
    an owner candidate that passed its gates, its own declared verification, changed bytes and stayed in scope."""
    script = {("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)], ("s0", "verification_owner_recovery"): [owner_answer]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _verification_plan(), script)
    roles = [c["execution_scope"] for c in calls]
    expected = ["subtask=s0 role=planned", "subtask=sv role=planned", "subtask=s0 role=verification_owner_recovery"]
    assert roles == expected + (["subtask=sv role=verification_rerun"] if accepted else [])
    assert calls[2]["reopened_owner"] is True
    assert result.legacy_result["status"] == ("success" if accepted else "failed")


# ============================================================================ enforce: terminal gates, commit and the result on each terminal path

@pytest.mark.asyncio
async def test_terminal_events_carry_the_failed_gates_reason_and_a_failing_event_bus_is_recorded(tmp_path):
    """ET05 (a failed gate's reason dropped from its outcome event) / ET04 / ET46 (an event-bus failure not recorded
    as an observability error)."""
    we = _enforce_engine(tmp_path)
    emitted = []

    async def emit(name, payload):
        emitted.append((name, payload))
    we.kernel.events.emit = emit
    with patch(f"{C}.resolve_migration_resolution", side_effect=RuntimeError("cannot resolve")):
        result, _ = await _enforce(we, tmp_path, _plan_of(_unit("s1")))
    gates = {p["gate"]: p for name, p in emitted if name == "terminal_gate_outcome"}
    assert gates["migration"]["status"] == "failed" and gates["migration"]["reason"]
    assert "reason" not in gates["stack_contract"]
    assert result.legacy_result["status"] == "failed"

    (tmp_path / "x").mkdir()
    failing = _enforce_engine(tmp_path / "x")
    failing.kernel.events.emit = AsyncMock(side_effect=RuntimeError("bus down"))
    result, _ = await _enforce(failing, tmp_path / "x", _plan_of(_unit("s1")))
    errors = result.legacy_result["observability_errors"]
    assert [e["event"] for e in errors][:2] == ["terminal_gates_started", "terminal_gate_outcome"]
    assert errors[0]["error"] == "RuntimeError: bus down"
    assert result.legacy_result["status"] == "success"


def _sandboxed():
    """create_git_worktree: a separate copy of the workspace (the plan sandbox)."""
    import shutil

    def create(workspace):
        sandbox = workspace + ".sandbox"
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya"))
        return sandbox
    return create


def _commit_failure(workspace_state):
    return SimpleNamespace(completed=False, evidence=None, contract_registry=None, record_errors=[], failure={
        "reason_code": "WORKSPACE_COMMIT_FAILED", "workspace_state": workspace_state, "error": "disk full"})


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_state", ["UNCHANGED", "UNCERTAIN"])
async def test_a_failed_workspace_commit_fails_the_run_and_keeps_the_candidate_only_when_uncertain(
        tmp_path, workspace_state):
    """ET11 (a commit failure reported as success) / ET20 (needs_review on a commit failure) / ET55 (the
    static-analysis candidate's in-place flag) / ET16 / ET18 (the candidate retained, and reported, only when the
    workspace state is UNCERTAIN) / ET21 (the final plan lifecycle) / ET33 / ET34 (the failure in the result) / ET42
    (derived artifacts persisted although nothing was committed) / ET14 / ET15 (the discarded sandbox's completions
    left completed and its written files kept)."""
    from kriya.workflow import workflow_controller as wc
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(_PYPROJECT)
    we = _enforce_engine(workspace)
    calls = []
    we.run_generation_workflow = _writer(workspace, calls)
    saved, gates_artifacts = [], []
    p1, p2, p3 = _planned(_plan_of(_unit("s1")))
    with p1, p2, p3, patch(f"{C}.create_git_worktree", side_effect=_sandboxed()), \
         patch(f"{C}.remove_git_worktree") as remove, \
         patch(f"{C}.commit_verified_candidate", side_effect=lambda report, request: gates_artifacts.extend(
             report.candidate_derived_artifacts) or _commit_failure(workspace_state)), \
         patch(f"{C}.save_artifact_registry") as save_registry, \
         patch(f"{C}.TerminalGateRequest", wraps=wc.TerminalGateRequest) as gate_request, \
         patch(f"{C}.save_approved_plan", side_effect=lambda ws, pid, doc: saved.append(doc)):
        result = await WorkflowController(we).execute("Create a.py", str(workspace), migration_mode="enforce")
    legacy = result.legacy_result
    assert (legacy["status"], legacy["quality_gates_passed"]) == ("needs_review", False)
    assert legacy["reason_codes"] == ["WORKSPACE_COMMIT_FAILED"]
    failure = legacy["workspace_commit_failure"]
    assert failure["workspace_state"] == workspace_state
    assert gate_request.call_args.kwargs["static_analysis_candidate"].in_place is False
    assert saved[-1]["lifecycle_state"] == "needs_review"
    assert saved[-1]["stage_states"] == {"s1": "pending"}
    assert result.control_state.subtask_states == {"s1": "pending"} and result.control_state.subtask_written_files == {}
    save_registry.assert_not_called()
    assert "derived_artifacts" not in legacy
    assert gates_artifacts, "the gates derived the candidate's artifacts (the commit then failed)"
    if workspace_state == "UNCERTAIN":
        remove.assert_not_called()
        assert failure["retained_candidate_path"] == str(workspace) + ".sandbox"
    else:
        remove.assert_called_once_with(str(workspace), str(workspace) + ".sandbox")
        assert "retained_candidate_path" not in failure


@pytest.mark.asyncio
async def test_a_completed_commit_persists_the_derived_artifacts_and_reports_a_late_plan_save_failure(tmp_path):
    """ET42's positive half (artifacts persisted after a completed commit) / ET22 (a post-commit plan persistence
    failure recorded, never raised)."""
    from kriya.control.persistence import save_approved_plan as real_save

    def save(ws, pid, doc):
        if doc["lifecycle_state"] == "completed":
            raise OSError("read-only")
        real_save(ws, pid, doc)
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    with patch(f"{C}.save_approved_plan", side_effect=save):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")))
    legacy = result.legacy_result
    assert legacy["status"] == "success" and legacy["derived_artifacts"]
    assert legacy["post_commit_persistence_errors"] == [
        {"operation": "save_approved_plan", "error": "OSError: read-only"}]


@pytest.mark.asyncio
async def test_the_terminal_gates_are_bound_to_the_runs_milestone(tmp_path):
    """ET54: a milestone run's terminal gates (and static-analysis unit) carry its milestone id, not the run id."""
    from kriya.workflow import workflow_controller as wc
    we = _enforce_engine(tmp_path)
    route = EngineeringRoute(
        kind=ChangeKind.MILESTONE, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT)
    we.engineering_triage.classify = AsyncMock(return_value=route)
    with patch(f"{C}.TerminalGateRequest", wraps=wc.TerminalGateRequest) as request:
        await _enforce(we, tmp_path, _plan_of(_unit("s1")), milestone_group_id="grp", milestone_index=2)
    assert request.call_args.kwargs["milestone_id"] == "grp:2"
    assert request.call_args.kwargs["static_analysis_candidate"].unit_id == "grp:2"


@pytest.mark.asyncio
async def test_a_recovery_plan_naming_an_unknown_owner_is_rejected_before_the_consumer(tmp_path, caplog):
    """EO03 (an unknown owner accepted locally) / EO24 (the rejected plan logged as passed=None)."""
    import logging

    from kriya.workflow.recovery_plan import RecoveryAction
    caplog.set_level(logging.INFO, logger=C)
    plan = _owner_two_files()
    ghost = _recovery_plan(plan, [RecoveryAction.MUST_CHANGE])
    object.__setattr__(ghost.groups[0], "owner_subtask_id", "ghost")
    result, calls = await _enforce_scripted(
        _enforce_engine(tmp_path), tmp_path, plan, {("s1", "planned"): [_insufficient()]},
        extra=[patch(f"{C}.build_recovery_execution_plan", side_effect=_once_for_s1(ghost))])
    assert [c["execution_scope"] for c in calls] == ["subtask=s0 role=planned", "subtask=s1 role=planned"]
    assert "RECOVERY_EXECUTION_PLAN_COMPLETED plan_id=recovery.s1.0 passed=False" in caplog.text


@pytest.mark.asyncio
async def test_an_owner_that_did_not_write_every_required_file_is_rejected(tmp_path):
    """EO15: the owner's candidate must write every required file to be accepted locally."""
    from kriya.workflow.recovery_plan import RecoveryAction
    plan = _owner_two_files()
    script = {("s1", "planned"): [_insufficient()], ("s0", "owner_recovery"): [{"_write": ["dep0.py"],
                                                                                 "files": ["dep0.py"]}]}
    result, calls = await _enforce_scripted(
        _enforce_engine(tmp_path), tmp_path, plan, script,
        extra=[patch(f"{C}.build_recovery_execution_plan",
                     side_effect=_once_for_s1(_recovery_plan(plan, [RecoveryAction.MUST_CHANGE] * 2)))])
    assert [c["execution_scope"] for c in calls][-1] == "subtask=s0 role=owner_recovery"
    (s0,) = [r for r in result.legacy_result["subtask_results"] if r["subtask_id"] == "s0"]
    assert (s0["status"], s0["reason_codes"]) == ("needs_review", ["PLAN_RECOVERY_OWNER_FAILED"])


@pytest.mark.asyncio
async def test_a_second_recovery_cycle_gets_the_next_plan_generation_and_obligation_revision(tmp_path):
    """EO32 / EO41 (the per-subtask recovery-plan generation not advanced or not passed) / EO38 (the cross-owner
    obligation's revision not the recovery event count) / EO34 (a new conflict after the consumer retry does not
    continue the cycle)."""
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.obligations import ObligationKind
    records, recording = _recorder()
    plan = _owner_two_files()
    retry_conflict = dict(_insufficient())
    script = {("s1", "planned"): [_insufficient()], ("s1", "consumer_retry"): [retry_conflict]}
    with patch(f"{C}.build_recovery_execution_plan", wraps=wc.build_recovery_execution_plan) as build:
        result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, plan, script, extra=[recording])
    generations = [c.kwargs["plan_generation"] for c in build.call_args_list if c.args[2].id == "s1"]
    assert generations[:2] == [0, 1]
    created = [r.revision for r in records if r.kind == ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT
               and r.status.value != "satisfied"]
    assert created[:2] == [0, 1]
    assert [c["execution_scope"] for c in calls].count("subtask=s1 role=consumer_retry") == 2


@pytest.mark.asyncio
async def test_an_accepted_recovery_ends_the_cycles(tmp_path):
    """EO33 (the cycle loop continues after an accepted recovery): a consumer retry that passes ends recovery even
    when its answer still carries a conflict."""
    from kriya.workflow import workflow_controller as wc
    plan = _owner_two_files()
    passing_with_conflict = {"plan_scope_conflict": _insufficient()["plan_scope_conflict"]}
    script = {("s1", "planned"): [_insufficient()], ("s1", "consumer_retry"): [passing_with_conflict]}
    with patch(f"{C}.build_recovery_execution_plan", wraps=wc.build_recovery_execution_plan) as build:
        await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, plan, script)
    assert [c.kwargs["plan_generation"] for c in build.call_args_list if c.args[2].id == "s1"] == [0]


@pytest.mark.asyncio
async def test_a_scope_revision_that_succeeds_commits_the_newly_owned_file_against_its_original_bytes(tmp_path):
    """EM06 (the newly scoped file's original revision not recorded): the commit applies it."""
    (tmp_path / "x1.py").write_text("owner 1\n")
    script = {("s1", "planned"): [_defect("x1.py")]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), script)
    assert result.legacy_result["status"] == "success"
    assert (tmp_path / "x1.py").read_text().startswith("# x1.py by s1 call 2")


@pytest.mark.asyncio
async def test_a_scope_revision_that_fails_revalidation_is_not_retried(tmp_path):
    """EM09 (a rejected revision retried with the same files)."""
    from kriya.workflow import workflow_controller as wc
    (tmp_path / "x1.py").write_text("owner 1\n")
    with patch(f"{C}.revise_plan_for_grounded_scope_owner", wraps=wc.revise_plan_for_grounded_scope_owner) as revise:
        result, calls = await _enforce_scripted(
            _enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")), {("s1", "planned"): [_defect("x1.py")]},
            validations=[_VALID, PlanValidationResult(valid=False, errors=["no"]), _VALID, _VALID])
    assert revise.call_count == 1
    assert [c["execution_scope"] for c in calls] == ["subtask=s1 role=planned"]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_answer", [
    {"final_review_refusal": {"detail": "unsendable", "reason_code": "FINAL_REVIEW_UNSENDABLE"}},
    {"_skip_write": True, "files": ["a.py"]},
    {"_write": ["a.py", "zzz.py"], "files": ["a.py", "zzz.py"]},
], ids=["review_refused", "unchanged_bytes", "undeclared_file"])
async def test_a_reopened_owner_candidate_must_be_reviewed_changed_and_in_scope(tmp_path, owner_answer):
    """EW04 / EW06 / EW07: each refusal leaves the verification failure standing (no rerun) and is recorded as a
    rejected reopen."""
    events = []
    with patch("kriya.core.attempt_evidence.scope.mirror_event", side_effect=lambda event: events.append(event)):
        result, calls = await _enforce_scripted(
            _enforce_engine(tmp_path), tmp_path, _verification_plan(),
            {("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)], ("s0", "verification_owner_recovery"): [owner_answer]})
    assert [c["execution_scope"] for c in calls][-1] == "subtask=s0 role=verification_owner_recovery"
    (event,) = [e for e in events if getattr(e, "kind", "").startswith("verification.owner_recovery")]
    assert event.kind == "verification.owner_recovery_rejected"
    assert event.details["owner_files"] == ["a.py"]
    assert result.legacy_result["status"] == "failed"


@pytest.mark.asyncio
async def test_an_accepted_reopen_records_the_owner_files_and_its_written_set_before_the_rerun(tmp_path):
    """EW08 (the event kind) / EW12 (a deleted planned file among the owner files) / EW14 (the reopened owner's
    written files not persisted)."""
    (tmp_path / "old.py").write_text("old\n")
    owner = Subtask(id="s0", description="write a", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE),
                                   PlannedFile(path="a2.py", action=FileAction.CREATE),
                                   PlannedFile(path="old.py", action=FileAction.DELETE)], verification=[_COMPILE])
    plan = _plan_of(owner, Subtask(id="sv", description="verify", execution_method=ExecutionMethod.MODEL,
                                   execution_role=ExecutionRole.VERIFICATION, depends_on=["s0"],
                                   verification=[_COMPILE]))
    events = []
    script = {("s0", "planned"): [{"_write": ["a.py"], "files": ["a.py"]}],
              ("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)],
              ("s0", "verification_owner_recovery"): [{"_write": ["a.py", "a2.py"], "files": ["a.py", "a2.py"]}]}
    with patch("kriya.core.attempt_evidence.scope.mirror_event", side_effect=lambda event: events.append(event)):
        result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, plan, script)
    (event,) = [e for e in events if getattr(e, "kind", "").startswith("verification.owner_recovery")]
    assert event.kind == "verification.owner_recovery_accepted"
    assert event.details["owner_files"] == ["a.py", "a2.py"]
    assert result.control_state.subtask_written_files["s0"] == ["a.py", "a2.py"]
    assert [c["execution_scope"] for c in calls][-1] == "subtask=sv role=verification_rerun"


@pytest.mark.asyncio
async def test_a_unit_with_planned_files_never_reopens_an_owner(tmp_path):
    """EW01: only a verification-only unit (no planned files) reopens its upstream owner."""
    plan = _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]))
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, plan,
                                            {("s1", "planned"): [dict(_NO_CHANGE_POSSIBLE)]})
    assert [c["execution_scope"] for c in calls] == ["subtask=s0 role=planned", "subtask=s1 role=planned"]


@pytest.mark.asyncio
async def test_the_terminal_gate_request_carries_the_bound_acceptance_and_its_closure_attempts_reach_the_result(tmp_path):
    """ET56 (the bound acceptance not handed to the terminal gates) / ET29 (the gates' requirement closure attempts
    not in the result)."""
    import dataclasses

    from kriya.workflow.terminal_gate_service import TERMINAL_GATES_NOT_RUN
    acceptance = SimpleNamespace(name="bound-acceptance")
    report = dataclasses.replace(TERMINAL_GATES_NOT_RUN, requirement_closure_attempts=({"requirement_id": "REQ-1"},))
    requests = []

    class Gates:
        def __init__(self, validators):
            self.validators = validators

        async def run(self, request, emit):
            requests.append(request)
            return report
    with patch(f"{C}.bound_acceptance", return_value=acceptance), patch(f"{C}.TerminalGateService", Gates):
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")))
    (request,) = requests
    assert request.acceptance is acceptance
    assert result.legacy_result["requirements"]["closure_attempts"] == [{"requirement_id": "REQ-1"}]


@pytest.mark.asyncio
async def test_the_commit_carries_the_plans_contract_transition(tmp_path):
    """ET53 (no ContractRegistry transition handed to the commit)."""
    import functools

    from kriya.workflow import workflow_controller as wc
    with patch(f"{C}.commit_verified_candidate", wraps=wc.commit_verified_candidate) as commit:
        result, _ = await _enforce(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")))
    request = commit.call_args.args[1]
    transition = request.contract_transition_for([], "tx-1")
    assert isinstance(transition, functools.partial) and transition.keywords["transaction_id"] == "tx-1"
    assert result.legacy_result["status"] == "success"


@pytest.mark.asyncio
async def test_a_verification_unit_reopens_its_nearest_completed_ancestor_not_one_invalidated_by_recovery(tmp_path):
    """EW11 (the candidates not restricted to completed units): after an owner recovery invalidated s1 (it is never
    re-run), the verification unit's nearest COMPLETED mutating ancestor is s0, not s1."""
    plan = _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]), _unit("s2", "c.py", depends_on=["s1"]),
                    Subtask(id="sv", description="verify", execution_method=ExecutionMethod.MODEL,
                            execution_role=ExecutionRole.VERIFICATION, depends_on=["s1"],
                            verification=[_COMPILE]))
    needs_a = {"quality_gates_passed": False, "status": "failed", "plan_scope_conflict": {
        "classification": "PLAN_SCOPE_INSUFFICIENT", "required_files": ["a.py"], "failure_type": "COMPILE_ERROR",
        "reason": "a.py must change"}}
    script = {("s2", "planned"): [needs_a], ("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, plan, script)
    roles = [c["execution_scope"] for c in calls]
    assert "subtask=s0 role=owner_recovery" in roles and "subtask=s2 role=consumer_retry" in roles
    assert [r for r in roles if r.endswith("verification_owner_recovery")] == ["subtask=s0 role=verification_owner_recovery"]
