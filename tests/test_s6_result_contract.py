"""S6 (C-02, R-02; owner rulings 2026-11-15 and 2026-11-16): ONE result contract, on the RunRecord.

MEASURED before the fix (A3 probe P8 at 63c04ba, ~/kriya-evidence/s6-63c04ba/p8_pre_fix_63c04ba.txt): a direct run under the default
``record`` policy applied ``app.py``, left REQ-1 unverified (``OPEN_REQUIREMENTS``) and reported ``quality_gates_passed = True``.
First wrong owners (TRACED): ``RunState.final_workflow_quality_passed`` (no requirement term, the final review folded in), the enforce
aggregate (status from ``all_completed`` alone) and the coordinator (the payload's claim plus commit and work-unit proofs only).

The contract proved here:

- the record refuses SUCCESS without a requirement truth that permits it (resolved, or the one explicitly requirements-free shape);
- the coordinator decides the terminal word from the record (``evaluate_result_contract``); a payload can lose a success, never make one;
- committed with an unresolved truth is FAILURE / APPLIED_REQUIREMENTS_OPEN / COMMITTED, ``applied_requirements_open`` on the result,
  ``quality_gates_passed`` False, the dedicated exit code, the open requirement ids kept, nothing rolled back;
- direct and enforce end with the same words for the same facts; the history row follows the reconciled result;
- B3 (``human_bound_acceptance``) is the only human modality that closes a requirement, counted as accepted and never verified;
- an OPTIONAL graded runtime failure is advisory; a REQUIRED one still fails; the final review is not a term of the verdict;
- `kriya fix` has its one repair requirement (INT-05), closed only by the gate that reproduced the failure.
"""
import asyncio
import inspect
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runtime_verification as trv  # noqa: E402
from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_config, chaos_engine, run_direct  # noqa: E402
from _t6_harness import shop_enforce  # noqa: E402
from test_enforce_terminal_requirement_truth_001 import _enforce_row, _history  # noqa: E402
from test_enforce_terminal_requirement_truth_001 import _run as _enforce_run  # noqa: E402
from test_project_continuity_two_run import GOAL_2, WrongFileScripted, _git_project  # noqa: E402
from test_s5_semantic_contract_authority import GENERAL, RUNTIME, _contract, _ledger  # noqa: E402

from kriya import cli_output  # noqa: E402
from kriya.application import history as history_module  # noqa: E402
from kriya.cli_output import applied_requirements_open_lines, result_exit_code  # noqa: E402
from kriya.control import run_coordinator as coordinator  # noqa: E402
from kriya.control import run_record as rr  # noqa: E402
from kriya.control.persistence import list_run_records  # noqa: E402
from kriya.control.run_coordinator import (  # noqa: E402
    begin_run_commit,
    coordinated_mutation,
    evaluate_result_contract,
    settle_run_commit,
)
from kriya.control.run_record import IllegalRunTransitionError, RunLifecycle, RunRecord  # noqa: E402
from kriya.control.trace import TraceLogger  # noqa: E402
from kriya.core.state_paths import trace_db_path  # noqa: E402
from kriya.workflow import requirements as rq  # noqa: E402
from kriya.workflow import runtime_verification as rv  # noqa: E402
from kriya.workflow.requirements import (  # noqa: E402
    HUMAN_ACCEPTANCE_METHOD,
    RequirementOutcome,
    record_requirement_closure,
    repair_requirement_truth,
    requirement_obligation_id,
    requirement_outcomes,
    requirement_truth,
)
from kriya.workflow.state import GenerationState  # noqa: E402

OPEN = {"status": "OPEN_REQUIREMENTS", "verified": 0, "accepted": 0, "total": 1,
        "unresolved": [{"id": "REQ-1", "text": "the behaviour", "outcome": "unverified"}]}
VERIFIED = {"status": "ALL_VERIFIED", "verified": 1, "accepted": 0, "total": 1, "unresolved": []}
ACCEPTED = {"status": "RESOLVED_WITH_OPERATOR_ACCEPTANCE", "verified": 0, "accepted": 1, "total": 1, "unresolved": []}
CONTRADICTED = {"status": "CONTRADICTED", "verified": 0, "accepted": 0, "total": 1,
                "unresolved": [{"id": "REQ-1", "text": "the behaviour", "outcome": "violated"}]}
TOOL = dict(rr.NO_REQUIREMENT_SET_TRUTH_OF_TOOL_EXECUTION)


# ------------------------------------------------------------------ 1. the record: SUCCESS implies requirement truth resolved

def _running(**fields):
    return RunRecord.new("run-1", "ws", None, None).transition(RunLifecycle.RUNNING, **fields)


def _committed(record):
    return record.begin_commit("tx-1", intent="APPLY_VERIFIED_CANDIDATE", candidate_hash="c1").settle_commit(rr.COMMIT_COMMITTED)


@pytest.mark.parametrize("truth", [VERIFIED, ACCEPTED, TOOL], ids=["all_verified", "operator_acceptance", "requirements_free_shape"])
def test_the_record_ends_success_only_with_a_truth_that_permits_it(truth):
    record = _committed(_running(requirement_truth=rr.normalized_requirement_truth(truth))).transition(RunLifecycle.SUCCESS)
    assert record.lifecycle_state is RunLifecycle.SUCCESS and record.terminal_status == "SUCCESS"


@pytest.mark.parametrize("truth", [
    None, OPEN, CONTRADICTED,
    {"status": "ALL_VERIFIED"},                                                            # no counts: malformed, never guessed
    {"status": "ALL_VERIFIED", "verified": 0, "accepted": 0, "total": 0, "unresolved": []},   # "resolved" with nothing mandatory
    {"status": "ALL_VERIFIED", "verified": 1, "accepted": 0, "total": 2, "unresolved": []},   # its own counts contradict it
    {"status": "NO_REQUIREMENT_SET"},                                                      # no shape
    {"status": "NO_REQUIREMENT_SET", "shape": "fix"},                                      # `kriya fix` is not requirements-free
    {"status": "NO_REQUIREMENT_SET", "shape": "milestone"},
    {"status": "VERIFIED", "verified": 1, "accepted": 0, "total": 1},                      # a word outside the closed vocabulary
], ids=["missing", "open", "contradicted", "no_counts", "zero_mandatory", "inconsistent_counts", "no_shape", "fix_shape", "milestone_shape", "unknown_word"])
def test_the_record_refuses_success_for_every_other_truth(truth):
    assert rr.requirement_truth_gap(truth) is not None
    with pytest.raises(IllegalRunTransitionError):
        _committed(_running()).transition(RunLifecycle.SUCCESS, requirement_truth=truth)


def test_the_requirements_free_shapes_are_one_closed_table_and_fix_is_not_in_it():
    assert rr.REQUIREMENTS_FREE_SHAPES == frozenset({"direct_tool_execution"})
    cli_source = inspect.getsource(sys.modules["kriya.cli"]) if "kriya.cli" in sys.modules else Path(rr.__file__).parents[1].joinpath("cli.py").read_text()
    assert cli_source.count("NO_REQUIREMENT_SET_TRUTH_OF_TOOL_EXECUTION") == 2, "one import and one use: the tool lifecycle's SUCCESS transition"
    workflow_sources = "".join(path.read_text() for path in Path(rr.__file__).parents[1].joinpath("workflow").glob("*.py"))
    assert "NO_REQUIREMENT_SET" not in workflow_sources, "no workflow shape may declare itself requirements-free"


def test_an_annotation_must_be_a_normalized_truth_and_applied_open_needs_a_committed_cycle():
    with pytest.raises(IllegalRunTransitionError):
        _running().annotate(requirement_truth={"status": "ALL_VERIFIED"})
    with pytest.raises(IllegalRunTransitionError):
        _running().transition(RunLifecycle.FAILURE, terminal_status=rr.TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN)
    record = _committed(_running()).transition(RunLifecycle.FAILURE, terminal_status=rr.TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN)
    assert (record.lifecycle_state, record.terminal_status, record.commit_result) == (RunLifecycle.FAILURE, "APPLIED_REQUIREMENTS_OPEN", "COMMITTED")
    assert record.terminal and RunLifecycle.FAILURE in rr.TERMINAL_LIFECYCLES and len(RunLifecycle) == 11, "no new lifecycle state"


def test_the_record_keeps_ids_and_outcomes_of_open_requirements_and_never_their_text():
    normal = rr.normalized_requirement_truth({**OPEN, "extra": "dropped"})
    assert normal == {"status": "OPEN_REQUIREMENTS", "verified": 0, "accepted": 0, "total": 1, "unresolved": [{"id": "REQ-1", "outcome": "unverified"}]}


def test_the_control_vocabulary_is_the_requirement_owners():
    assert (rr.REQUIREMENT_TRUTH_ALL_VERIFIED, rr.REQUIREMENT_TRUTH_RESOLVED_WITH_ACCEPTANCE, rr.REQUIREMENT_TRUTH_OPEN, rr.REQUIREMENT_TRUTH_CONTRADICTED) == (
        rq.REQUIREMENT_TRUTH_ALL_VERIFIED, rq.REQUIREMENT_TRUTH_RESOLVED_WITH_ACCEPTANCE, rq.REQUIREMENT_TRUTH_OPEN, rq.REQUIREMENT_TRUTH_CONTRADICTED)
    assert set(history_module._TRUTH_STATUSES) == set(rr.REQUIREMENT_TRUTH_STATUSES) - {rr.REQUIREMENT_TRUTH_NO_REQUIREMENT_SET}
    assert cli_output.RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN == coordinator.RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN == "applied_requirements_open"
    assert "requirement_truth" in rr._ANNOTATABLE_FIELDS and "model_admission" in rr._ANNOTATABLE_FIELDS


# ------------------------------------------------------------------ 2. the coordinator: one decision, from the record

SUCCESS_CLAIM = {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}


def _coordinated(tmp_path, payload, *, commit=True, units=None, inside=None):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)

    @coordinated_mutation
    async def entry(workspace_path):
        if units is not None:
            assert coordinator.annotate_run(workspace_path, work_unit_states=units) is None
        if commit:
            assert settle_run_commit(begin_run_commit(workspace_path, "tx-1", candidate_hash="c1"), rr.COMMIT_COMMITTED) is None
        if inside is not None:
            await inside(workspace_path)
        return dict(payload)

    result = asyncio.run(entry(workspace_path=str(workspace)))
    (record,) = list_run_records(str(workspace))
    return result, record


@pytest.mark.parametrize("truth", [VERIFIED, ACCEPTED], ids=["all_verified", "operator_acceptance"])
def test_result_control_2_every_requirement_closed_and_gates_passed_is_success(tmp_path, truth):
    result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, "requirement_truth": truth})
    assert (record.lifecycle_state, record.terminal_status, record.commit_result) == (RunLifecycle.SUCCESS, "SUCCESS", "COMMITTED")
    assert record.requirement_truth == rr.normalized_requirement_truth(truth)
    assert result == {**SUCCESS_CLAIM, "requirement_truth": truth} and result_exit_code(result) == 0


@pytest.mark.parametrize("truth, open_ids", [
    (OPEN, ["REQ-1"]), (CONTRADICTED, ["REQ-1"]), (None, []), ({"status": "ALL_VERIFIED"}, []), (TOOL, []),
], ids=["open", "contradicted", "missing", "malformed", "a_payload_cannot_declare_itself_requirements_free"])
def test_result_control_1_applied_with_requirements_open_is_a_typed_non_success(tmp_path, truth, open_ids):
    payload = {**SUCCESS_CLAIM, **({"requirement_truth": truth} if truth is not None else {})}
    result, record = _coordinated(tmp_path, payload)
    assert (record.lifecycle_state, record.terminal_status, record.commit_result) == (RunLifecycle.FAILURE, "APPLIED_REQUIREMENTS_OPEN", "COMMITTED")
    assert result["status"] == "applied_requirements_open" and result["quality_gates_passed"] is False
    assert result["technical_gates_passed"] is True and result["commit_result"] == "COMMITTED" and result["files"] == ["app.py"]
    assert result["open_requirements"] == open_ids and result["reason_codes"] == ["APPLIED_REQUIREMENTS_OPEN"]
    assert result["reported_status"] == "success" and result["run_record_state"] == "FAILURE"
    assert "applied" in result["error"] and "not successful" in result["error"] and "nothing was rolled back" in result["error"]
    assert result_exit_code(result) == cli_output.EXIT_APPLIED_REQUIREMENTS_OPEN == 5
    if truth is OPEN:
        assert result["requirement_truth"] == OPEN, "the same truth as the record's: the owner's full form (with texts) is kept"
    if truth == {"status": "ALL_VERIFIED"}:
        assert "requirement_truth" not in result or result["requirement_truth"] == truth, "a malformed truth is never replaced by a guess"


def test_open_requirements_with_nothing_committed_is_a_plain_failure_never_applied(tmp_path):
    result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, "requirement_truth": OPEN}, commit=False)
    assert (record.lifecycle_state, record.terminal_status, record.commit_result) == (RunLifecycle.FAILURE, "FAILURE", "NOT_COMMITTED")
    assert result["status"] == "failed" and result["quality_gates_passed"] is False
    assert result["reason_codes"] == ["REQUIREMENT_TRUTH_UNRESOLVED"] and result_exit_code(result) == 1


def test_result_control_4_a_failed_mandatory_gate_still_fails_whatever_the_requirement_truth(tmp_path):
    payload = {"status": "failed", "quality_gates_passed": False, "requirement_truth": VERIFIED}
    result, record = _coordinated(tmp_path, payload, commit=False)
    assert record.lifecycle_state is RunLifecycle.FAILURE and record.terminal_status == "FAILURE"
    assert result == payload and result_exit_code(result) == 1


def test_the_payload_is_not_result_authority_an_unverified_work_unit_fails_closed(tmp_path):
    result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, "requirement_truth": VERIFIED}, units={"u1": {"status": "RUNNING"}})
    assert record.lifecycle_state is RunLifecycle.FAILURE and record.terminal_status == "FAILURE"
    assert result["status"] == "failed" and result["reason_codes"] == ["RESULT_CONTRADICTS_RUN_RECORD"]


def test_the_decision_reads_the_record_and_never_the_payloads_truth():
    record = _committed(_running())           # the record holds no truth; the payload's own copy of one decides nothing
    decision = evaluate_result_contract(record, {**SUCCESS_CLAIM, "requirement_truth": VERIFIED})
    assert decision.outcome == coordinator.RESULT_APPLIED_REQUIREMENTS_OPEN and "no valid requirement truth" in decision.reason
    with_truth = _committed(_running(requirement_truth=rr.normalized_requirement_truth(VERIFIED)))
    assert evaluate_result_contract(with_truth, dict(SUCCESS_CLAIM)).outcome == coordinator.RESULT_SUCCESS
    assert evaluate_result_contract(with_truth, {"status": "failed", "quality_gates_passed": False}).outcome == coordinator.RESULT_FAILURE
    assert evaluate_result_contract(with_truth, {"note": "says nothing"}).outcome == coordinator.RESULT_UNDECIDED


def test_the_coordinator_is_the_only_caller_of_the_result_contract_and_of_the_success_transition():
    package = Path(rr.__file__).parents[1]
    callers = sorted(str(path.relative_to(package)) for path in package.rglob("*.py") if "evaluate_result_contract(" in path.read_text())
    assert callers == ["control/run_coordinator.py"]
    success = sorted(str(path.relative_to(package)) for path in package.rglob("*.py")
                     if "RunLifecycle.SUCCESS" in path.read_text() and "transition_mutating_run" in path.read_text())
    assert success == ["cli.py", "control/run_coordinator.py"], "the tool lifecycle (cli.py, requirements-free by table) and the coordinator"


# ------------------------------------------------------------------ 3. surfaces project the reconciled result

def test_the_exit_table_is_one_closed_table_used_by_every_run_exit_site():
    assert (cli_output.EXIT_SUCCESS, cli_output.EXIT_FAILED, cli_output.EXIT_KNOWLEDGE_GAP, cli_output.EXIT_APPLIED_REQUIREMENTS_OPEN) == (0, 1, 3, 5)
    assert result_exit_code({"status": "success", "quality_gates_passed": True}) == 0
    assert result_exit_code({"status": "success"}) == 0, "a milestone result carries no quality_gates_passed"
    assert result_exit_code({"status": "applied_requirements_open", "quality_gates_passed": False}) == 5
    assert result_exit_code({"status": "applied_requirements_open", "quality_gates_passed": True}) == 5, "the status word wins over a stale flag"
    for failed in ({"status": "failed", "quality_gates_passed": False}, {"status": "needs_review", "quality_gates_passed": False},
                   {"status": "success", "quality_gates_passed": False}, {"status": "milestone_failed"}, {}, None):
        assert result_exit_code(failed) == 1, failed
    cli_source = Path(rr.__file__).parents[1].joinpath("cli.py").read_text()
    assert cli_source.count("sys.exit(result_exit_code(") == 4, "generate, the milestone sequence, proposal execution, fix"
    assert 'get("quality_gates_passed") else 1)' not in cli_source and '== "success" else 1)' not in cli_source, "no exit site recomputes success"


def test_the_applied_open_message_names_what_was_applied_what_is_open_and_what_can_close_it():
    lines = applied_requirements_open_lines({
        "status": "applied_requirements_open", "files": ["app.py"], "open_requirements": ["REQ-1"],
        "requirement_truth": OPEN, "open_requirement_authorities": {"REQ-1": ["acceptance_approval (B3: an operator-approved acceptance suite)"]}})
    text = "\n".join(lines)
    assert "APPLIED, REQUIREMENTS OPEN (not successful)" in lines[0] and "nothing was rolled back" in lines[0]
    assert "app.py" in text and "OPEN_REQUIREMENTS (0 verified, 0 accepted, 1 mandatory)" in text
    assert "open: REQ-1 (unverified): the behaviour" in text and "can be closed by: acceptance_approval (B3" in text


def test_the_contract_names_the_authorities_that_can_close_an_open_requirement():
    _reqs, contract = _contract(GENERAL)
    view = contract.closing_authorities(["REQ-1"])
    assert set(view) == {"REQ-1"} and any("acceptance_approval" in name for name in view["REQ-1"]), view
    assert rq.open_requirement_authorities(VERIFIED, contract) == {} and rq.open_requirement_authorities(OPEN, None) == {}
    assert rq.open_requirement_authorities(OPEN, contract) == view


def _row(db, run_id):
    with sqlite3.connect(db) as conn:
        status, events = conn.execute("SELECT status, run_events FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return status, json.loads(events)


STALE_EVENT = {"kind": "requirement.truth", "attempt": 1, "source": "workflow.requirement_closure", "authority": "authoritative", "details": dict(VERIFIED)}


def test_the_history_row_follows_the_reconciled_result_and_readers_show_the_records_truth(tmp_path):
    """INT-04 and C-02 'persisted identity': the row said ``success`` and its LAST per-attempt truth event said ALL_VERIFIED; the applied
    candidate's truth (the record's) is OPEN. After reconciliation the row and every reader of it say what the record says."""
    db = str(tmp_path / "traces.db")

    @coordinated_mutation
    async def unit(workspace_path):
        TraceLogger(db).log_run(run_id="unit-row", goal="g", duration_sec=0.0, attempts=1, status="success", files_modified=[], run_events=[STALE_EVENT],
                                enforce_run_id="parent.enforce")
        TraceLogger(db).log_run(run_id="milestone-row", goal="g", duration_sec=0.0, attempts=1, status="success", files_modified=[], run_events=[STALE_EVENT],
                                milestone_group_id="group-1")
        return {"status": "success", "quality_gates_passed": True}

    async def inside(workspace_path):
        await unit(workspace_path=workspace_path)                                    # a nested unit's row is that unit's own truth
        TraceLogger(db).log_run(run_id="run-row", goal="g", duration_sec=0.0, attempts=1, status="success", files_modified=["app.py"], run_events=[STALE_EVENT])

    _result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, "run_id": "run-row", "requirement_truth": OPEN}, inside=inside)
    status, events = _row(db, "run-row")
    assert status == "applied_requirements_open"
    (reconciled,) = [event for event in events if event["kind"] == "result.reconciled"]
    assert reconciled["authority"] == "authoritative" and reconciled["details"] == {
        "run_record_id": record.run_id, "run_record_revision": record.revision, "lifecycle_state": "FAILURE",
        "terminal_status": "APPLIED_REQUIREMENTS_OPEN", "commit_result": "COMMITTED",
        "requirement_truth": record.requirement_truth, "row_status_corrected": True}
    assert history_module._truth_of_events(events) == {"status": "OPEN_REQUIREMENTS", "verified": 0, "accepted": 0, "total": 1}
    assert history_module._truth_of_events([STALE_EVENT])["status"] == "ALL_VERIFIED", "a row from before reconciliation keeps its event"
    for unit_row in ("unit-row", "milestone-row"):                                   # a unit's row is that unit's own truth
        unit_status, unit_events = _row(db, unit_row)
        assert unit_status == "success" and not [event for event in unit_events if event["kind"] == "result.reconciled"], unit_row


def test_a_consistent_success_row_is_stamped_with_the_record_and_keeps_its_status(tmp_path):
    db = str(tmp_path / "traces.db")

    async def inside(workspace_path):
        TraceLogger(db).log_run(run_id="run-row", goal="g", duration_sec=0.0, attempts=1, status="success", files_modified=[], run_events=[])

    _result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, "run_id": "run-row", "requirement_truth": VERIFIED}, inside=inside)
    status, events = _row(db, "run-row")
    assert status == "success" and events[-1]["details"]["lifecycle_state"] == "SUCCESS" and events[-1]["details"]["row_status_corrected"] is False
    assert events[-1]["details"]["run_record_revision"] == record.revision


# ------------------------------------------------------------------ 4. A3 probe P8 inverted, direct and enforce (result controls 1, 5, 6)

def _direct_p8(tmp_path, **kwargs):
    workspace = _git_project(tmp_path, {"main.py": 'print("Hello World")\n'})
    runtime = ChaosRuntime(WrongFileScripted())
    cfg = chaos_config(spec_compliance_enabled=True)
    cfg.llm.context_window = 32768
    runtime.served_window = 32768
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), GOAL_2, workspace, **kwargs)
    (record,) = list_run_records(str(workspace))
    return workspace, cfg, result, record


def test_p8_inverted_direct_applied_with_an_open_requirement_is_not_success(tmp_path):
    workspace, cfg, result, record = _direct_p8(tmp_path)
    assert (workspace / "app.py").exists(), "the application fact stays: nothing is rolled back"
    assert result["status"] == "applied_requirements_open" and result["quality_gates_passed"] is False
    assert result["candidate_gates_passed"] is True and result["terminal_regression_passed"] is True and result["technical_gates_passed"] is True
    assert result["files"] == ["app.py"] and result["open_requirements"] == ["REQ-1"]
    assert result["requirement_truth"]["status"] == "OPEN_REQUIREMENTS" and result["requirement_truth"]["unresolved"][0]["id"] == "REQ-1"
    assert result["open_requirement_authorities"]["REQ-1"], "the result says what the sealed contract accepts to close it"
    assert (record.lifecycle_state, record.terminal_status, record.commit_result) == (RunLifecycle.FAILURE, "APPLIED_REQUIREMENTS_OPEN", "COMMITTED")
    assert record.requirement_truth["status"] == "OPEN_REQUIREMENTS"
    assert result_exit_code(result) == 5
    with sqlite3.connect(trace_db_path(cfg)) as conn:
        rows = conn.execute("SELECT status, run_events FROM runs").fetchall()
    assert [status for status, _events in rows] == ["applied_requirements_open"]
    assert history_module._truth_of_events(json.loads(rows[0][1]))["status"] == "OPEN_REQUIREMENTS"


def test_p8_inverted_enforce_a_real_plan_commit_with_an_open_requirement_ends_with_the_direct_words(tmp_path, monkeypatch):
    """Result control 5: the real enforce controller (real plan sandbox, terminal gates and workspace commit; only the model runtime
    and the plan parse are scripted) on a plain-wording goal. Same facts as the direct P8 - committed, a requirement open - same words."""
    observed = shop_enforce(tmp_path, monkeypatch)
    result = observed.result.legacy_result
    (record,) = list_run_records(str(observed.workspace))
    assert result["requirement_truth"]["status"] == "OPEN_REQUIREMENTS" and result["commit_evidence"], "the scenario: committed, a requirement open"
    assert result["status"] == "applied_requirements_open" and result["quality_gates_passed"] is False and result["technical_gates_passed"] is True
    assert result["reported_status"] == "success" and result["open_requirements"] and result["open_requirement_authorities"]
    assert (record.lifecycle_state, record.terminal_status, record.commit_result) == (RunLifecycle.FAILURE, "APPLIED_REQUIREMENTS_OPEN", "COMMITTED")
    assert result_exit_code(result) == 5


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, status, record_words, exit_code", [
    (None, "failed", ("FAILURE", "FAILURE", "NOT_COMMITTED"), 1),
    (RequirementOutcome.CLOSED_BY_EVIDENCE, "success", ("SUCCESS", "SUCCESS", "NO_COMMIT"), 0),
    (RequirementOutcome.HUMAN_ACCEPTED, "success", ("SUCCESS", "SUCCESS", "NO_COMMIT"), 0),
], ids=["open", "all_verified", "operator_acceptance"])
async def test_enforce_without_a_commit_cycle_the_truth_alone_decides_success_and_the_row_and_history_follow(tmp_path, outcome, status, record_words, exit_code):
    """The characterization harness (its sandbox IS the workspace, so the record holds no commit cycle): nothing recorded as committed
    plus an open requirement is a plain failure, never "applied"; the closed truths succeed. The `.enforce` row and both history
    readers show the reconciled status and the record's truth."""
    cfg, result = await _enforce_run(tmp_path, outcome)
    (record,) = list_run_records(str(tmp_path))
    assert result["status"] == status and result["quality_gates_passed"] is (status == "success")
    assert (record.lifecycle_state.value, record.terminal_status, record.commit_result) == record_words
    assert result_exit_code(result) == exit_code
    enforce_id, row_status, events = _enforce_row(cfg)
    assert row_status == status
    assert history_module._truth_of_events(events)["status"] == record.requirement_truth["status"]
    _unscoped, scoped, detail = _history(cfg, tmp_path, enforce_id)
    assert scoped["runs"][0]["status"] == row_status and detail["run"]["requirement_truth"]["status"] == record.requirement_truth["status"]
    if outcome is None:
        assert result["open_requirements"] == ["REQ-1"] and result["reason_codes"] == ["REQUIREMENT_TRUTH_UNRESOLVED"]


# ------------------------------------------------------------------ 5. person acceptance: B3 only (owner ruling 1, 2026-11-16)

def _close(ledger, reqs, requirement_id, method, **kwargs):
    evidence_id = ledger.current(requirement_obligation_id(requirement_id)).evidence["evidence_id"]
    record_requirement_closure(ledger, reqs, requirement_id, evidence_id=evidence_id, method=method, detail={}, source="test", revision=1, **kwargs)


def test_person_controls_1_and_2_a_b3_acceptance_closes_as_accepted_and_is_never_verified(tmp_path):
    """The sealed B3 modality (``human_bound_acceptance`` on the BEHAVIOR claim, text-bound: tests/test_b3_human_acceptance.py proves the
    closer) yields HUMAN_ACCEPTED. Here: what that outcome means for the run - resolved, counted as accepted, never as verified - and
    that the result contract accepts it."""
    reqs, _contract_unused = _contract(RUNTIME)
    outcomes = {"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE, "REQ-2": RequirementOutcome.HUMAN_ACCEPTED}
    truth = requirement_truth(outcomes, reqs)
    assert (truth["status"], truth["verified"], truth["accepted"], truth["total"]) == ("RESOLVED_WITH_OPERATOR_ACCEPTANCE", 1, 1, 2)
    assert truth["status"] != "ALL_VERIFIED" and rr.requirement_truth_gap(truth) is None
    result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, "requirement_truth": truth})
    assert record.lifecycle_state is RunLifecycle.SUCCESS and record.requirement_truth["accepted"] == 1 and record.requirement_truth["verified"] == 1
    assert rq.OPERATOR_SUFFICIENCY_METHODS == frozenset({HUMAN_ACCEPTANCE_METHOD, "external_acceptance_command"})
    assert "ACCEPTED_BY_PERSON" not in Path(rq.__file__).read_text(), "no second human modality: the existing canonical state is reused"


def test_a_whole_requirement_record_under_the_b3_method_closes_nothing_only_a_bound_claim_does():
    """Operator sufficiency closes a CLAIM the contract bound it to (re-checked against the requirement's text at read time); a record
    that merely names the method for a whole requirement is not that."""
    reqs, contract = _contract(RUNTIME)
    ledger = _ledger(reqs)
    _close(ledger, reqs, "REQ-2", HUMAN_ACCEPTANCE_METHOD)
    assert requirement_outcomes(ledger, reqs, contract=contract)["REQ-2"] is RequirementOutcome.UNVERIFIED
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("method", [HUMAN_ACCEPTANCE_METHOD, "verifier_semantic_expectation_valid", "final_review_passed", "change_approved"],
                         ids=["b3_method_on_an_unbound_requirement", "generic_verifier_decision", "final_review", "mutation_approval"])
def test_person_controls_3_4_5_no_human_decision_closes_a_requirement_the_contract_binds_no_person_to(method):
    """REQ-1 of the general goal has NO bound authority (GENERAL_UNSPECIFIED): a record that claims a person accepted it - whatever it is
    called - manufactures no authority, and the run's truth stays open."""
    reqs, contract = _contract(GENERAL)
    ledger = _ledger(reqs)
    _close(ledger, reqs, "REQ-1", method)
    _close(ledger, reqs, "REQ-2", "test_addition_record")
    outcomes = requirement_outcomes(ledger, reqs, contract=contract)
    assert outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED
    truth = requirement_truth(outcomes, reqs)
    assert truth["status"] == "OPEN_REQUIREMENTS" and rr.requirement_truth_gap(truth) == "requirement truth is OPEN_REQUIREMENTS"


def test_person_control_4_an_approval_is_not_a_requirement_closer():
    """Requirement acceptance != candidate / mutation approval != final review: the approval and review owners write no closure."""
    package = Path(rr.__file__).parents[1]
    writers = sorted(str(path.relative_to(package)) for path in package.rglob("*.py") if "record_requirement_closure(" in path.read_text())
    assert writers == ["workflow/requirements.py", "workflow/spec_compliance.py"], writers
    claim_writers = sorted(str(path.relative_to(package)) for path in package.rglob("*.py") if "record_requirement_claim(" in path.read_text())
    assert claim_writers == ["workflow/acceptance_oracle.py", "workflow/api_preservation.py", "workflow/authority_bundle.py",
                             "workflow/contract_closers.py", "workflow/requirements.py"], claim_writers
    for owner in ("policy/approval.py", "workflow/stage_apply.py", "workflow/stage_review.py", "workflow/verifier_semantic.py"):
        source = package.joinpath(owner).read_text()
        assert "HUMAN_ACCEPTED" not in source and HUMAN_ACCEPTANCE_METHOD not in source, owner


def test_person_control_6_an_acceptance_never_erases_an_authoritative_contradiction():
    reqs, contract = _contract(RUNTIME)
    ledger = _ledger(reqs)
    _close(ledger, reqs, "REQ-2", "runtime_stdout", violated=True)
    assert requirement_outcomes(ledger, reqs, contract=contract)["REQ-2"] is RequirementOutcome.VIOLATED
    truth = requirement_truth({"REQ-1": RequirementOutcome.HUMAN_ACCEPTED, "REQ-2": RequirementOutcome.VIOLATED}, reqs)
    assert truth["status"] == "CONTRADICTED" and truth["accepted"] == 1 and rr.requirement_truth_gap(truth) is not None
    assert [row["id"] for row in truth["unresolved"]] == ["REQ-2"], "the contradiction stays visible beside the acceptance"


# ------------------------------------------------------------------ 6. optional graded runtime check (ruling 17), final review (ruling 2)

FAILED_GRADE = ({"passed": False, "verdict": "FAIL", "reasoning": "NameError", "likely_files": ["calc.py"]},)


def _graded(tmp_path, *, required):
    run = trv._AttemptRun(tmp_path, required=required, scripted={"app": (trv._app, [trv._crash()])}, grades=FAILED_GRADE)
    run.call(trv._approve_and_execute)
    return run


def test_result_control_3_an_optional_check_that_ran_and_was_graded_failed_cannot_fail_the_attempt(tmp_path):
    run = _graded(tmp_path, required=False)
    assert run.outcome == "returned", run.failure
    assert not [outcome for outcome in run.state.gate_outcomes if outcome.get("type") == "run_verification"], "advisory: neither a pass nor a failure"
    (event,) = [event for event in run.state.run_events if event.kind == "runtime_verification.advisory_failure"]
    assert event.authority.value == "advisory" and event.details["required"] is False and event.details["commands"]


def test_result_control_4_a_required_check_that_was_graded_failed_still_fails(tmp_path):
    run = _graded(tmp_path, required=True)
    assert run.outcome == "raised" and run.failure.type == "run_verification"
    assert [outcome for outcome in run.state.gate_outcomes if outcome.get("type") == "run_verification" and outcome.get("success") is False]
    assert not [event for event in run.state.run_events if event.kind == "runtime_verification.advisory_failure"]


def test_every_graded_runtime_failure_leaves_through_the_one_exit_keyed_on_the_runs_own_flag():
    source = inspect.getsource(rv)
    assert source.count("_fail_graded_runtime_check(deps, state, ctx, failure, failure_outcome)") == 3, "managed service probe, verification-only unit, graded run"
    exit_source = inspect.getsource(rv._fail_graded_runtime_check)
    assert "ctx.runtime_verification_required" in exit_source and "raise QualityGateFailure(failure)" in exit_source


def _quality(**overrides):
    facts = dict(candidate_gates_succeeded=True, terminal_regression_succeeded=True, overall_attempt_succeeded=True, quality_gates_succeeded=True,
                 api_contract_recovery=None, final_review_refusal=None)
    return GenerationState.final_workflow_quality_passed(SimpleNamespace(**{**facts, **overrides}))


def test_the_final_review_is_advisory_it_is_not_a_term_of_the_terminal_verdict():
    assert _quality() is True
    assert _quality(final_review_refusal={"reason_code": "REVIEW_REQUEST_REFUSED"}) is True, "a refused review is recorded and shown, never a failure"
    for gate in ("candidate_gates_succeeded", "terminal_regression_succeeded", "overall_attempt_succeeded", "quality_gates_succeeded"):
        assert _quality(**{gate: False}) is False, gate
    assert _quality(api_contract_recovery=object()) is False


# ------------------------------------------------------------------ 7. INT-05: the repair requirement of `kriya fix`

def _baseline(*, status="captured", execution_status="completed", success=False):
    return SimpleNamespace(status=status, outcome=SimpleNamespace(execution_status=execution_status, success=success, failure_fingerprint="fp-1"))


def test_int05_a_reproduced_failure_that_no_longer_reproduces_is_the_one_verified_requirement(tmp_path):
    repair = repair_requirement_truth(_baseline(), terminal_regression_passed=True, gates_passed=True)
    assert repair["requirements"]["outcomes"] == {"REPAIR-1": "closed_by_evidence"}
    assert repair["requirement_truth"] == {"status": "ALL_VERIFIED", "verified": 1, "accepted": 0, "total": 1, "unresolved": []}
    assert repair["requirements"]["evidence"]["REPAIR-1"]["baseline_failure_fingerprint"] == "fp-1" and "open_requirement_authorities" not in repair
    result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, **repair})
    assert record.lifecycle_state is RunLifecycle.SUCCESS and result_exit_code(result) == 0


@pytest.mark.parametrize("baseline, regression, gates, verdict", [
    (None, True, True, "not_reproduced"),                                              # no baseline: nothing reproduced the failure
    (_baseline(success=True), True, True, "not_reproduced"),                           # Kriya's own gate passed on the untouched workspace
    (_baseline(status="baseline_indeterminate"), True, True, "not_reproduced"),        # an indeterminate baseline is never "assume failing"
    (_baseline(execution_status="environment_failure"), True, True, "not_reproduced"),  # the gate itself could not run
    (SimpleNamespace(status="captured", outcome=None), True, True, "not_reproduced"),
    (_baseline(), False, True, "still_failing"),
    (_baseline(), None, True, "still_failing"),
    (_baseline(), True, False, "still_failing"),
], ids=["no_baseline", "baseline_passed", "indeterminate", "environment_failure", "no_outcome", "regression_failed", "regression_unknown", "gates_failed"])
def test_int05_negative_controls_nothing_else_closes_the_repair_requirement(tmp_path, baseline, regression, gates, verdict):
    repair = repair_requirement_truth(baseline, terminal_regression_passed=regression, gates_passed=gates)
    assert repair["requirements"]["outcomes"] == {"REPAIR-1": verdict}
    assert repair["requirement_truth"]["status"] == "OPEN_REQUIREMENTS" and repair["requirement_truth"]["unresolved"][0]["outcome"] == verdict
    assert repair["open_requirement_authorities"]["REPAIR-1"]
    result, record = _coordinated(tmp_path, {**SUCCESS_CLAIM, **repair})
    assert record.terminal_status == "APPLIED_REQUIREMENTS_OPEN" and result["open_requirements"] == ["REPAIR-1"] and result_exit_code(result) == 5


def test_int05_only_the_repair_entry_shape_gets_the_repair_requirement():
    source = Path(rq.__file__).parent.joinpath("workflow.py").read_text()
    assert source.count("repair_requirement_truth(") == 1
    assert "if not requirements_from_goal and requirement_set is None else None" in source
    cli_source = Path(rr.__file__).parents[1].joinpath("cli.py").read_text()
    assert cli_source.count("requirements_from_goal=False") == 1, "`kriya fix` is the one repair entry"


# ------------------------------------------------------------------ 9. the desktop words the canonical status (display only)

def test_the_desktop_words_the_canonical_applied_open_status_and_never_calls_it_not_recorded():
    """Source-level guard (the UI toolchain is not installed here; ui/shared/src/cockpit/core/presentation.test.ts holds the executable
    case and runs in the UI certification): the two words the backend owns - the history row status and the RunRecord terminal status -
    are the exact literals the desktop maps, on every surface that words a run status, and the mapping is display only."""
    ui = Path(rr.__file__).parents[2] / "ui" / "shared" / "src"
    presentation = (ui / "cockpit/core/presentation.ts").read_text()
    assert f"APPLIED_REQUIREMENTS_OPEN_STATUS = '{coordinator.RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN}'" in presentation
    assert f"APPLIED_REQUIREMENTS_OPEN_TERMINAL_STATUS = '{rr.TERMINAL_STATUS_APPLIED_REQUIREMENTS_OPEN}'" in presentation
    label = presentation.split("export function recordedStatusLabel", 1)[1].split("\n}", 1)[0]
    assert "status === APPLIED_REQUIREMENTS_OPEN_STATUS ? APPLIED_REQUIREMENTS_OPEN_LABEL : 'Status not recorded'" in label
    card = presentation.split("export function runCard", 1)[1].split("\n}", 1)[0]
    assert "run.status === APPLIED_REQUIREMENTS_OPEN_STATUS ? 'applied_open'" in card and "recordedStatusLabel(run.status)" in card
    assert "run.status === 'success' ? 'success'" in card, "only the literal success status is a success card"
    # the two failure words the row writers record (the enforce controller's own row says "failed"): both read "Failed", never "not recorded"
    assert coordinator._ROW_STATUS_BY_RESULT["failed"] == "failure" and 'row_status = "failed" if reconciled_status == "failure"' in Path(coordinator.__file__).read_text()
    assert "return status === 'failure' || status === 'failed';" in presentation and "isRecordedFailure(status) ? 'Failed'" in label
    assert "isRecordedFailure(run.status) ? 'failure'" in card
    # the open run's header, the outcome line of a finished launch, and the runs column
    assert "recordedStatusLabel(ctx.status)" in (ui / "cockpit/shell/MainWorkspace.tsx").read_text()
    execution = (ui / "cockpit/core/execution.tsx").read_text()
    assert "record.terminal_status === APPLIED_REQUIREMENTS_OPEN_TERMINAL_STATUS ? 'Change applied, requirements still open'" in execution
    assert "record.terminal_status === 'SUCCESS' ? 'success'" in execution, "only the record's SUCCESS is a success outcome"
    assert f"{coordinator.RESULT_STATUS_APPLIED_REQUIREMENTS_OPEN}: 'st-review'" in (ui / "panels/RunsColumn.tsx").read_text()
