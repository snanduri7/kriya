"""ENFORCE-UNIT-ADVISORY-REVIEW-ACCEPTANCE-001 (audit root R2, owner ruling 2026-11-11).

MEASURED before the fix (sealed reproducer, verify-r1-r6-ce1efb7-2026-11-11/repro/test_r2_enforce_review_refusal.py): a
Reviewer answer cut off (``finish_reason=length``) while unit s2 of an enforce plan ran failed s2, failed the plan,
reset the accepted unit s1 to ``pending`` and committed nothing, although s2's own deterministic gates had passed and
its candidate was applied. The enforce loop read one field, ``quality_gates_passed``, which folds in "the final review
was performed".

The contract proved here: in an enforce plan a unit's final review is advisory. A unit is accepted on its deterministic
gates (the loop's one predicate, ``enforce_loop.enforce_unit_gates_accepted``) at every acceptance site; a review that
was not performed is reported as not performed and never as a review; every deterministic failure still fails; the
direct path's final review stays authoritative and fail-closed; ``quality_gates_passed`` itself is unchanged.
"""
import re
from pathlib import Path

import _enforce_unit_harness as harness
import pytest
import test_recovery_may_change_001 as may_change
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    benign_roles,
    chaos_config,
    chaos_engine,
    git_workspace,
    plausible_message_tokens,
    run_direct,
)
from test_e12_enforce_characterization import (
    _COMPILE,
    _NO_CHANGE_POSSIBLE,
    _enforce_engine,
    _enforce_scripted,
    _plan_of,
    _unit,
)

from kriya.agents.agent import REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS
from kriya.core.inference_runtime import ChatResponse
from kriya.workflow import enforce_loop
from kriya.workflow.enforce_loop import (
    ADVISORY_REVIEW_NOT_PERFORMED,
    advisory_review_refusal,
    enforce_unit_gates_accepted,
)
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, ExecutionRole, Subtask

# A unit result exactly as run_generation_workflow reports an applied candidate whose final review was not performed
# (kriya/workflow/outcome.py::completed_result + final_review_refusal_payload; MEASURED shape, sealed r2.txt).
REFUSED = {
    "status": "failure", "quality_gates_passed": False, "candidate_gates_passed": True, "terminal_regression_passed": True,
    "overall_attempt_passed": True, "failure_category": "final_review_refused", "plan_scope_conflict": None,
    "workspace_commit_failure": None,
    "final_review_refusal": {"reason_code": "OUTPUT_TRUNCATED", "detail": "the model output is not a complete answer",
                             "batch": 1, "batches": 1, "candidate_applied": True, "committed_work_units": None,
                             "rolled_back": False},
}
# One deterministic term of the unit's own verdict missing at a time: the refusal then excuses nothing.
DETERMINISTIC_FAILURES = {
    "candidate_gates_failed": {"candidate_gates_passed": False},
    "terminal_regression_failed": {"terminal_regression_passed": False},
    "attempt_failed": {"overall_attempt_passed": False},
    "candidate_not_applied": {"final_review_refusal": {**REFUSED["final_review_refusal"], "candidate_applied": False}},
    "scope_conflict": {"plan_scope_conflict": {"classification": "PLAN_SCOPE_DEFECT", "required_files": ["zz.py"],
                                               "failure_type": "COMPILE_ERROR", "reason": "zz.py must change"}},
    "commit_failed": {"workspace_commit_failure": {"reason_code": "WORKSPACE_COMMIT_FAILED", "workspace_state": "UNCHANGED",
                                                   "error": "x"}},
}


# ------------------------------------------------------------------ the predicate

def test_a_unit_that_passed_outright_is_accepted_and_has_no_advisory_refusal():
    passed = {"status": "success", "quality_gates_passed": True}
    assert enforce_unit_gates_accepted(passed) is True and advisory_review_refusal(passed) is None


def test_only_the_missing_review_is_excused():
    assert enforce_unit_gates_accepted(REFUSED) is True
    assert advisory_review_refusal(REFUSED) == REFUSED["final_review_refusal"]


@pytest.mark.parametrize("missing", sorted(DETERMINISTIC_FAILURES))
def test_a_refused_review_never_excuses_a_deterministic_failure(missing):
    result = {**REFUSED, **DETERMINISTIC_FAILURES[missing]}
    assert enforce_unit_gates_accepted(result) is False and advisory_review_refusal(result) is None


@pytest.mark.parametrize("absent", ["candidate_gates_passed", "terminal_regression_passed", "overall_attempt_passed"])
def test_an_unreported_deterministic_term_is_not_assumed_true(absent):
    result = {key: value for key, value in REFUSED.items() if key != absent}
    assert enforce_unit_gates_accepted(result) is False


def test_a_plain_gate_failure_is_not_accepted():
    assert enforce_unit_gates_accepted({"status": "failed", "quality_gates_passed": False}) is False
    assert enforce_unit_gates_accepted({"status": "failed", "quality_gates_passed": False, "candidate_gates_passed": True,
                                        "terminal_regression_passed": True, "overall_attempt_passed": True}) is False


# ------------------------------------------------------------------ the sealed scenario: real units, real workflow

SERVICE, OTHER = "shop/service.py", "shop/other.py"
BASE_SOURCE = "def page_size():\n    return 5\n"
PRODUCED_SOURCE = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n"
UNITS = {"s1": (SERVICE, PRODUCED_SOURCE), "s2": (OTHER, "OTHER = 1\n")}
LOOP = "### Code Review\n\n#### Summary of Findings\n\n* The tests cover standard cases.\n\n" * 400


def _real_plan():
    def subtask(sid, path, depends=()):
        return {"id": sid, "description": f"edit {path}", "execution_method": "model",
                "planned_files": [{"path": path, "action": "create" if path == OTHER else "modify"}],
                "depends_on": list(depends), "provides": [f"p_{sid}"], "relevant_global_invariant_ids": ["gi1"],
                "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [subtask("s1", SERVICE), subtask("s2", OTHER, ("s1",))]})


class _TransportTap:
    """The harness's ``patch`` name, with the transport it installs wrapped: every Reviewer answer given while
    ``cut_off_unit`` runs is the provider's ``finish_reason=length`` cut-off. Nothing else differs from the control."""

    def __init__(self, real_patch, cut_off_unit, log):
        self._real, self._unit, self._log = real_patch, cut_off_unit, log

    def __call__(self, *args, **kwargs):
        return self._real(*args, **kwargs)

    def object(self, target, name, **kwargs):
        if name == "_request_once":
            inner, state = kwargs["new"], {"developer_path": None}

            async def transport(llm, client, model, system_prompt, user_prompt, *args, **kw):
                answer = await inner(llm, client, model, system_prompt, user_prompt, *args, **kw)
                first = (system_prompt or "").splitlines()[0] if system_prompt else ""
                if "Developer Agent" in first:
                    state["developer_path"] = next((p for _sid, (p, _src) in UNITS.items() if p in answer["content"]), None)
                unit = next((sid for sid, (p, _src) in UNITS.items() if p == state["developer_path"]), None)
                if answer["content"] == "Review: Approved":
                    cut = unit == self._unit
                    self._log.append((unit, "length" if cut else "stop"))
                    if cut:
                        answer = {**answer, "content": LOOP, "completion_tokens": REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS,
                                  "finish_reason": "length"}
                return answer
            kwargs["new"] = transport
        return self._real.object(target, name, **kwargs)


def _real_run(tmp_path, monkeypatch, cut_off_unit, mode="guardrails"):
    log = []
    monkeypatch.setattr(harness, "patch", _TransportTap(harness.patch, cut_off_unit, log))
    run = harness.run_enforce(tmp_path, plan=_real_plan(), units=UNITS, mode=mode,
                              base_files={SERVICE: BASE_SOURCE, "shop/__init__.py": ""})
    return run, log


@pytest.mark.parametrize("mode", ["human-in-the-loop", "guardrails"])
def test_a_cut_off_review_in_unit_two_keeps_both_units_work_and_is_reported_as_not_performed(tmp_path, monkeypatch, mode):
    """The sealed R2 scenario, inverted: the plan succeeds, both files are committed, the upstream unit stays
    accepted, and the review that did not happen is named on the unit and on the run - never implied."""
    run, log = _real_run(tmp_path, monkeypatch, cut_off_unit="s2", mode=mode)
    legacy = run.result.legacy_result
    assert ("s2", "length") in log, "the Reviewer answer of unit s2 was never cut off: the scenario does not test R2"
    last = legacy["last_subtask_result"]
    # the unit's own verdict is what it always was: the fold is NOT redefined
    assert last["quality_gates_passed"] is False and last["candidate_gates_passed"] is True
    assert last["final_review_refusal"]["reason_code"] == "OUTPUT_TRUNCATED"
    assert last["final_review_refusal"]["candidate_applied"] is True
    # the enforce plan's decision
    assert legacy["status"] == "success" and legacy["quality_gates_passed"] is True
    by_id = {s["subtask_id"]: s for s in legacy["subtask_results"]}
    assert by_id["s1"]["status"] == "completed" and by_id["s1"]["reason_codes"] == []
    assert by_id["s2"]["status"] == "completed" and by_id["s2"]["error"] is None
    assert by_id["s2"]["reason_codes"] == [ADVISORY_REVIEW_NOT_PERFORMED, "OUTPUT_TRUNCATED"]
    (record,) = legacy["advisory_reviews_not_performed"]
    assert (record["subtask_id"], record["reason_code"], record["review_performed"]) == ("s2", "OUTPUT_TRUNCATED", False)
    assert "OUTPUT_TRUNCATED" in record["detail"]
    assert "review" not in legacy                      # the plan result claims no review of its own
    # the committed workspace and the durable state
    assert (run.workspace / SERVICE).read_text() == PRODUCED_SOURCE and (run.workspace / OTHER).read_text() == "OTHER = 1\n"
    states = dict(run.result.control_state.subtask_states)
    assert states == {"s1": "completed", "s2": "completed"}
    assert dict(run.result.control_state.subtask_written_files) == {"s1": [SERVICE], "s2": [OTHER]}
    assert run.result.control_state.subtask_completion_scope == "workspace"


def test_complete_reviews_report_no_unperformed_review(tmp_path, monkeypatch):
    run, log = _real_run(tmp_path, monkeypatch, cut_off_unit=None)
    legacy = run.result.legacy_result
    assert legacy["status"] == "success" and "advisory_reviews_not_performed" not in legacy
    assert [s["reason_codes"] for s in legacy["subtask_results"]] == [[], []]
    assert {finish for _unit_id, finish in log} == {"stop"}


def test_the_direct_path_final_review_stays_authoritative_and_fail_closed(tmp_path):
    """The same cut-off on the direct path: not a success, the refusal is the run's typed outcome, the applied
    candidate is kept (its existing contract, unchanged by R2)."""
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})

    def responder(role, request):
        if role == "developer":
            return CALC_WITH_SUB
        if role == "reviewer":
            return ChatResponse(content=LOOP, prompt_tokens=plausible_message_tokens(request.messages, request.tools),
                                completion_tokens=REVIEWER_DEFAULT_MAX_OUTPUT_TOKENS, finish_reason="length")
        return benign_roles(role, request)

    with RuntimeRegistration(ChaosRuntime(responder)):
        result = run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    assert result["quality_gates_passed"] is False and result["candidate_gates_passed"] is True
    assert result["failure_category"] == "final_review_refused"
    assert result["final_review_refusal"]["reason_code"] == "OUTPUT_TRUNCATED"
    assert result["final_review_refusal"]["candidate_applied"] is True
    assert "advisory_reviews_not_performed" not in result
    assert (workspace / "calc.py").read_text() != CALC


# ------------------------------------------------------------------ every acceptance site of the loop (scripted units)

def _roles(calls):
    return [call["execution_scope"] for call in calls]


def _by_id(legacy):
    return {s["subtask_id"]: s for s in legacy["subtask_results"]}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_planned_unit_whose_review_was_not_performed_is_accepted(tmp_path):
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")),
                                            {("s1", "planned"): [dict(REFUSED)]})
    legacy = result.legacy_result
    assert legacy["status"] == "success" and _roles(calls) == ["subtask=s1 role=planned"]
    assert _by_id(legacy)["s1"]["reason_codes"] == [ADVISORY_REVIEW_NOT_PERFORMED, "OUTPUT_TRUNCATED"]
    assert legacy["advisory_reviews_not_performed"] == [{
        "subtask_id": "s1", "execution_role": "unit", "reason_code": "OUTPUT_TRUNCATED",
        "detail": "the model output is not a complete answer", "review_performed": False}]
    assert dict(result.control_state.subtask_written_files) == {"s1": ["a.py"]}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("missing", sorted(set(DETERMINISTIC_FAILURES) - {"scope_conflict"}))
async def test_a_deterministic_failure_still_fails_the_unit_and_the_plan(tmp_path, missing):
    """Candidate-gate failure, regression failure, a failed attempt, a candidate that was not applied and a commit
    failure: each still stops the plan, with or without a refused review beside it. (A scope conflict takes the
    loop's recovery path, which its own tests drive; the predicate's refusal of it is pinned above.)"""
    result, _calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")),
                                             {("s1", "planned"): [{**REFUSED, **DETERMINISTIC_FAILURES[missing]}] * 6})
    legacy = result.legacy_result
    assert legacy["status"] != "success" and legacy["quality_gates_passed"] is False
    assert _by_id(legacy)["s1"]["status"] != "completed"
    assert "advisory_reviews_not_performed" not in legacy
    assert "commit_evidence" not in legacy


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("evidence, status, code", [
    ([{"passed": False}], "failed", "VERIFICATION_FAILED"),
    ([], "needs_review", "VERIFICATION_EVIDENCE_MISSING"),
], ids=["verification_failed", "verification_evidence_missing"])
async def test_the_units_declared_verification_still_decides_after_an_unperformed_review(tmp_path, evidence, status, code):
    verification = [{"type": _COMPILE.type.value, "tool_name": _COMPILE.tool_name, "description": _COMPILE.description, **row}
                    for row in evidence]
    result, _calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s1")),
                                             {("s1", "planned"): [{**REFUSED, "verification_results": verification}]})
    legacy = result.legacy_result
    assert legacy["status"] == status
    unit = _by_id(legacy)["s1"]
    assert unit["status"] == status and code in unit["reason_codes"] and ADVISORY_REVIEW_NOT_PERFORMED in unit["reason_codes"]


_NEEDS_A = {"quality_gates_passed": False, "status": "failed", "plan_scope_conflict": {
    "classification": "PLAN_SCOPE_INSUFFICIENT", "required_files": ["a.py"], "failure_type": "COMPILE_ERROR",
    "reason": "a.py must change"}}


def _chain():
    return _plan_of(_unit("s0", "a.py"), _unit("s1", "b.py", depends_on=["s0"]), _unit("s2", "c.py", depends_on=["s1"]))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_reopened_owner_whose_review_was_not_performed_is_accepted_and_the_consumer_is_retried(tmp_path):
    script = {("s2", "planned"): [dict(_NEEDS_A)], ("s0", "owner_recovery"): [dict(REFUSED)]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _chain(), script)
    legacy = result.legacy_result
    assert "subtask=s2 role=consumer_retry" in _roles(calls)
    (event,) = legacy["plan_recovery_events"]
    assert event["reopened_owner"] == "s0" and event["owner_recovery_passed"] is True
    assert {"subtask_id": "s0", "execution_role": "owner_recovery", "reason_code": "OUTPUT_TRUNCATED",
            "detail": "the model output is not a complete answer", "review_performed": False} in legacy["advisory_reviews_not_performed"]
    assert dict(result.control_state.subtask_written_files)["s0"] == ["a.py"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_reopened_owner_with_a_deterministic_failure_is_still_rejected(tmp_path):
    script = {("s2", "planned"): [dict(_NEEDS_A)],
              ("s0", "owner_recovery"): [{**REFUSED, **DETERMINISTIC_FAILURES["candidate_gates_failed"]}] * 6}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _chain(), script)
    assert "subtask=s2 role=consumer_retry" not in _roles(calls)
    assert result.legacy_result["status"] != "success"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_consumer_retry_whose_review_was_not_performed_is_accepted(tmp_path):
    script = {("s2", "planned"): [dict(_NEEDS_A)], ("s2", "consumer_retry"): [dict(REFUSED)]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _chain(), script)
    legacy = result.legacy_result
    assert _roles(calls)[-1] == "subtask=s2 role=consumer_retry"
    (event,) = legacy["plan_recovery_events"]
    assert event["owner_recovery_passed"] is True
    unit = _by_id(legacy)["s2"]
    assert unit["status"] == "completed" and unit["reason_codes"] == [ADVISORY_REVIEW_NOT_PERFORMED, "OUTPUT_TRUNCATED"]
    assert [r["subtask_id"] for r in legacy["advisory_reviews_not_performed"]] == ["s2"]      # recorded once


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_consumer_retry_with_a_deterministic_failure_still_fails_the_recovery(tmp_path):
    script = {("s2", "planned"): [dict(_NEEDS_A)],
              ("s2", "consumer_retry"): [{**REFUSED, **DETERMINISTIC_FAILURES["terminal_regression_failed"]}] * 6}
    result, _calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _chain(), script)
    legacy = result.legacy_result
    assert legacy["status"] != "success"
    assert all(event["owner_recovery_passed"] is False for event in legacy["plan_recovery_events"])


def _verification_plan():
    return _plan_of(_unit("s0", "a.py"),
                    Subtask(id="sv", description="verify", execution_method=ExecutionMethod.MODEL,
                            execution_role=ExecutionRole.VERIFICATION, depends_on=["s0"], verification=[_COMPILE]))


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_verification_owner_reopen_whose_review_was_not_performed_is_accepted(tmp_path):
    """The reopen used to require "its final review was performed" (an explicit earlier term); in an enforce plan
    that review is advisory, so the changed, gate-passing owner is accepted and the verification runs again."""
    script = {("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)], ("s0", "verification_owner_recovery"): [dict(REFUSED)]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _verification_plan(), script)
    assert _roles(calls)[-1] == "subtask=sv role=verification_rerun"
    legacy = result.legacy_result
    assert legacy["status"] == "success"
    assert [(r["subtask_id"], r["execution_role"]) for r in legacy["advisory_reviews_not_performed"]] == [
        ("s0", "verification_owner_recovery")]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_verification_owner_reopen_with_a_deterministic_failure_is_still_rejected(tmp_path):
    script = {("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)],
              ("s0", "verification_owner_recovery"): [{**REFUSED, **DETERMINISTIC_FAILURES["candidate_not_applied"]}]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _verification_plan(), script)
    assert "subtask=sv role=verification_rerun" not in _roles(calls)
    assert result.legacy_result["status"] != "success"


class _RefusingReviews:
    """The MAY_CHANGE harness's engine, with every gate-passing MAY_CHANGE owner answer turned into the refused-review
    shape (optionally with one deterministic term missing)."""

    def __init__(self, inner, extra):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_extra", extra)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        if name == "run_generation_workflow":
            unit_run, extra = value, self._extra

            async def run(**kwargs):
                answer = await unit_run(**kwargs)
                if kwargs["execution_scope"].endswith(f"role={may_change.MAY}") and answer.get("quality_gates_passed"):
                    answer = {**answer, **REFUSED, **extra}
                return answer
            value = run
        setattr(self._inner, name, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra, accepted", [({}, True), (DETERMINISTIC_FAILURES["candidate_gates_failed"], False)],
                         ids=["review_not_performed", "candidate_gates_failed"])
async def test_a_may_change_owner_is_judged_by_the_same_predicate(tmp_path, monkeypatch, extra, accepted):
    real_engine = may_change._engine
    # the MAY_CHANGE harness's own module fixture: its scripted units write into the workspace itself
    monkeypatch.setattr("kriya.workflow.workflow_controller.create_git_worktree", lambda workspace: workspace)
    monkeypatch.setattr(may_change, "_engine", lambda kernel=None: _RefusingReviews(real_engine(kernel), extra))
    result, calls = await may_change._execute(tmp_path, may_change._plan(), conflicts={"v": [["cons.py"]]},
                                              recovery={"p": ["changed"], "c": ["unchanged"]})
    legacy = result.legacy_result
    events = may_change._events(result)
    if accepted:
        assert legacy["status"] == "success"
        assert events["p"]["may_change_outcome"] == "changed"
        assert ("v", "consumer_retry") in may_change._reopens(calls)
        assert [(r["subtask_id"], r["execution_role"]) for r in legacy["advisory_reviews_not_performed"]] == [
            ("p", "may_change_owner_recovery")]
    else:
        assert legacy["status"] != "success"
        assert events["p"]["may_change_outcome"] == "failed"
        assert ("v", "consumer_retry") not in may_change._reopens(calls)


# ------------------------------------------------------------------ one predicate, no second reading in the loop

def test_the_loop_reads_the_raw_field_only_to_report_it():
    """Every decision in the enforce loop asks ``enforce_unit_gates_accepted``. The raw ``quality_gates_passed`` of a
    unit result is still read in the loop body, but only on lines that log or report it; a new raw read is a new
    acceptance site and fails here until it is routed through the predicate or classified as reporting."""
    source = Path(enforce_loop.__file__).read_text(encoding="utf-8")
    body = source.split("        async def _invoke_bounded_subtask(", 1)[1].split("\nADVISORY_REVIEW_NOT_PERFORMED = ", 1)[0]
    raw_reads = [line.strip() for line in body.splitlines() if re.search(r'\.get\("quality_gates_passed"\)', line)]
    assert raw_reads == [
        'bool(owner_result.get("quality_gates_passed")), bytes_retained, owner_undeclared,',            # RECOVERY_MAY_CHANGE_* log
        'bool(owner_result.get("quality_gates_passed")), owner_undeclared,',                            # VERIFICATION_OWNER_RECOVERY_* log
        '"owner_quality_gates_passed": bool(owner_result.get("quality_gates_passed")),',                # the reopen's evidence event
        '"quality_gates_passed": bool(cr.get("quality_gates_passed")),',                                # subtask_retry_locality
    ]
    assert 'get("final_review_refusal")' not in body.replace('review_refusal = call_result.get("final_review_refusal")', "")
