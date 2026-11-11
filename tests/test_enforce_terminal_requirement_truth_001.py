"""ENFORCE-TERMINAL-REQUIREMENT-TRUTH-001 (audit root R1, owner ruling 2026-11-11).

MEASURED before the fix (sealed reproducer, verify-r1-r6-ce1efb7-2026-11-11/repro/test_r1_enforce_requirement_truth.py):
an enforce run ended ``success`` with its requirement PENDING and carried no ``requirement_truth`` on the result, the
launch record, the ``.enforce`` trace row or the history readers, and the row belonged to no workspace.

The contract proved here: the enforce aggregate computes ``requirement_truth`` ONCE from the outcomes it reports; the
SAME value reaches the launch record, the ``.enforce`` row's ``requirement.truth`` event and ``history.list`` /
``history.detail``; no reader computes it again; the row carries the run's ``evidence.attempt_store`` pointer, so the
workspace-scoped run list finds it. Lifecycle status is NOT changed by the truth (requirement policy decides that).

The harness is tests/test_e12_enforce_characterization.py's: the real WorkflowController enforce run, real requirement
derivation, real terminal gates, real commit and real trace writer; only the Planner and the unit are scripted. The
requirement outcomes of the three non-default states are set at the one outcome owner
(``kriya.workflow.requirements.requirement_outcomes``), which the terminal gate's ``blocking_requirements`` reads too.
"""
import json
import sqlite3
import time
from unittest.mock import patch

import pytest
from _kup_evidence_fixtures import acquire_snapshot_id
from _strict_doubles import strict_config
from test_e12_enforce_characterization import C, _enforce, _enforce_engine, _plan_of, _traced, _unit

from kriya.control.launch import _requirement_truth_field
from kriya.control.run_coordinator import begin_mutating_run
from kriya.core.state_paths import trace_db_path
from kriya.kup.cli_ops import run_kup_operation
from kriya.workflow import requirements as requirements_module
from kriya.workflow.requirements import RequirementOutcome, requirement_truth

GOAL = "Create a.py"


def _enforce_row(cfg):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_id, status, run_events FROM runs").fetchall()
    ((run_id, status, events),) = [row for row in rows if row[0].endswith(".enforce")]
    return run_id, status, json.loads(events)


def _events(events, kind):
    return [event for event in events if event["kind"] == kind]


def _outcome_patches(outcome):
    """The one requirement outcome of the run, at the outcome owner and at the controller's imported name."""
    if outcome is None:
        return ()

    def outcomes(ledger, requirement_set):
        return {requirement.id: outcome for requirement in requirement_set.requirements}
    return (patch.object(requirements_module, "requirement_outcomes", outcomes), patch(f"{C}.requirement_outcomes", outcomes))


async def _run(tmp_path, outcome=None):
    we = _enforce_engine(tmp_path)
    patches = _outcome_patches(outcome)
    for item in patches:
        item.start()
    try:
        with begin_mutating_run(str(tmp_path)):
            result, _calls = await _enforce(we, tmp_path, _plan_of(_unit("s1")), goal=GOAL)
    finally:
        for item in patches:
            item.stop()
    return we.kernel.config, result.legacy_result


def _history(cfg, tmp_path, enforce_id):
    snapshot = acquire_snapshot_id(cfg)
    unscoped = run_kup_operation(cfg, {"operation": "history.list", "snapshot_id": snapshot})["data"]
    scoped = run_kup_operation(cfg, {"operation": "history.list", "snapshot_id": snapshot, "workspace": str(tmp_path)})["data"]
    detail = run_kup_operation(cfg, {"operation": "history.detail", "snapshot_id": snapshot, "run_id": enforce_id})["data"]
    return unscoped, scoped, detail


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, truth_status, run_status, counts", [
    (None, "OPEN_REQUIREMENTS", "success", (0, 0, 1)),                                   # the sealed R1 scenario: REQ-1 pending
    (RequirementOutcome.CLOSED_BY_EVIDENCE, "ALL_VERIFIED", "success", (1, 0, 1)),
    (RequirementOutcome.HUMAN_ACCEPTED, "RESOLVED_WITH_OPERATOR_ACCEPTANCE", "success", (0, 1, 1)),
    (RequirementOutcome.VIOLATED, "CONTRADICTED", "failed", (0, 0, 1)),                  # blocking_requirements: VIOLATED always blocks
], ids=["open", "all_verified", "operator_acceptance", "contradicted"])
async def test_the_enforce_truth_is_one_value_on_the_result_the_launch_record_the_trace_row_and_history(
        tmp_path, outcome, truth_status, run_status, counts):
    cfg, legacy = await _run(tmp_path, outcome)
    verified, accepted, total = counts

    # the lifecycle status is what requirement policy decides (unchanged), and the truth says what was verified
    assert legacy["status"] == run_status
    assert legacy["quality_gates_passed"] is (run_status == "success")
    truth = legacy["requirement_truth"]
    assert (truth["status"], truth["verified"], truth["accepted"], truth["total"]) == (truth_status, verified, accepted, total)
    # ... and it is the canonical function's value for exactly the outcomes this result reports
    reported = {rid: RequirementOutcome(value) for rid, value in legacy["requirements"]["outcomes"].items()}
    from kriya.workflow.requirements import Requirement, RequirementSet
    requirement_set = RequirementSet(
        goal_digest=legacy["requirements"].get("goal_digest", ""), version=legacy["requirements"].get("version", 1),
        requirements=tuple(Requirement(**row) for row in legacy["requirements"]["requirements"]))
    assert truth == requirement_truth(reported, requirement_set)

    # the launch record's projection (the function LaunchSession.finish calls)
    assert _requirement_truth_field(legacy)["requirement_truth"]["status"] == truth_status

    # the persisted `.enforce` row: its status, and ONE requirement.truth event that is the result's own field
    enforce_id, row_status, events = _enforce_row(cfg)
    assert row_status == run_status
    (event,) = _events(events, "requirement.truth")
    assert event["authority"] == "authoritative"
    assert event["details"] == {"requirement_set_digest": legacy["requirements"]["digest"], **truth}

    # history.list / history.detail read that persisted event; the workspace-scoped list finds the run
    expected = {"status": truth_status, "verified": verified, "accepted": accepted, "total": total}
    unscoped, scoped, detail = _history(cfg, tmp_path, enforce_id)
    assert {run["run_id"]: run for run in unscoped["runs"]}[enforce_id]["requirement_truth"] == expected
    assert detail["run"]["requirement_truth"] == expected
    assert [run["run_id"] for run in scoped["runs"]] == [enforce_id]
    assert scoped["runs"][0]["requirement_truth"] == expected
    assert scoped["workspace_scope"]["unattributed"] == 0


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_open_requirement_never_reads_as_a_verified_or_truthless_success(tmp_path):
    """The sealed R1 symptom, inverted: status ``success`` (default ``record`` policy) AND the truth OPEN_REQUIREMENTS
    with the unresolved requirement named - on the result and on every reader, never absent (absent is worded as a
    plain "Completed" by the desktop, ui/shared/src/cockpit/core/presentation.ts::requirementTruthWords)."""
    cfg, legacy = await _run(tmp_path)
    assert legacy["status"] == "success" and legacy["requirements"]["outcomes"] == {"REQ-1": "pending"}
    assert legacy["requirement_truth"]["status"] == "OPEN_REQUIREMENTS"
    assert [row["id"] for row in legacy["requirement_truth"]["unresolved"]] == ["REQ-1"]
    assert _requirement_truth_field(legacy)["requirement_truth"]["unresolved"][0]["outcome"] == "pending"
    enforce_id, _status, _events_list = _enforce_row(cfg)
    unscoped, _scoped, detail = _history(cfg, tmp_path, enforce_id)
    for projected in ({run["run_id"]: run for run in unscoped["runs"]}[enforce_id], detail["run"]):
        assert projected["status"] == "success" and projected["requirement_truth"]["status"] == "OPEN_REQUIREMENTS"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_truth_is_computed_once_and_no_reader_computes_it_again(tmp_path):
    """One call of the canonical function per enforce run (the aggregate's); the trace writer, the launch projection
    and the history readers then work with the function unavailable."""
    calls = []

    def counting(outcomes, requirement_set):
        calls.append(dict(outcomes))
        return requirement_truth(outcomes, requirement_set)

    we = _enforce_engine(tmp_path)
    with patch(f"{C}.requirement_truth", counting), begin_mutating_run(str(tmp_path)):
        result, _ = await _enforce(we, tmp_path, _plan_of(_unit("s1")), goal=GOAL)
    legacy, cfg = result.legacy_result, we.kernel.config
    assert len(calls) == 1 and calls[0] == {"REQ-1": RequirementOutcome.PENDING}

    def unavailable(*_args, **_kwargs):
        raise AssertionError("a reader computed the requirement truth again")
    with patch.object(requirements_module, "requirement_truth", unavailable), patch(f"{C}.requirement_truth", unavailable):
        enforce_id, _status, events = _enforce_row(cfg)
        unscoped, scoped, detail = _history(cfg, tmp_path, enforce_id)
        assert _requirement_truth_field(legacy)["requirement_truth"]["status"] == "OPEN_REQUIREMENTS"
    assert detail["run"]["requirement_truth"]["status"] == "OPEN_REQUIREMENTS"
    assert scoped["runs"][0]["requirement_truth"] == {run["run_id"]: run for run in unscoped["runs"]}[enforce_id]["requirement_truth"]
    assert _events(events, "requirement.truth")[0]["details"]["status"] == "OPEN_REQUIREMENTS"


def _row_events(cfg):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        ((events,),) = db.execute("SELECT run_events FROM runs").fetchall()
    return json.loads(events)


def test_the_trace_row_copies_the_results_truth_and_never_derives_one_from_the_outcomes():
    """A result whose truth field disagrees with its outcomes (it cannot happen in a run; it discriminates a copy from
    a second computation): the row records the FIELD."""
    cfg = strict_config()
    field = {"status": "ALL_VERIFIED", "verified": 1, "accepted": 0, "total": 1, "unresolved": []}
    _traced(cfg)._write_enforce_trace("r1", "goal", {
        "status": "success", "requirement_truth": field,
        "requirements": {"digest": "d1", "outcomes": {"REQ-1": "pending"}, "verdicts": {}},
    }, time.time())
    (event,) = _events(_row_events(cfg), "requirement.truth")
    assert event["details"] == {"requirement_set_digest": "d1", **field}


@pytest.mark.parametrize("result", [
    {"status": "success"},                                                                    # no requirement set reported
    {"status": "needs_review", "failure_type": "PLANNING_ERROR", "reason_codes": ["STRUCTURED_PLAN_UNAVAILABLE"]},
    {"status": "error", "failure_type": "RuntimeError", "error": "boom"},
    {"status": "success", "requirement_truth": "ALL_VERIFIED"},                               # malformed: never guessed
], ids=["no_truth", "planning_failed", "error", "malformed"])
def test_a_result_without_a_truth_records_none_and_still_records_the_store_pointer(result):
    """Every terminal shape of the enforce row: no truth on the result means no truth event (a missing truth is never
    turned into one), and the row always says where its attempt evidence is (outside a run: NO_RUN_SCOPE)."""
    cfg = strict_config()
    assert _traced(cfg)._write_enforce_trace("r2", "goal", dict(result), time.time()) is True
    events = _row_events(cfg)
    assert _events(events, "requirement.truth") == []
    (pointer,) = _events(events, "evidence.attempt_store")
    assert pointer["details"] == {"status": "NO_RUN_SCOPE"} and pointer["authority"] == "auxiliary"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_enforce_row_names_the_runs_own_evidence_store(tmp_path):
    """Run attribution: the row's pointer is the run's open attempt-evidence store (the existing owner's pointer, the
    one every unit row records), whose manifest records this workspace."""
    cfg, legacy = await _run(tmp_path)
    _enforce_id, _status, events = _enforce_row(cfg)
    (pointer,) = _events(events, "evidence.attempt_store")
    assert pointer["details"]["run_id"] == legacy["run_id"]
    assert pointer["details"]["status"] == "OPEN" and pointer["details"]["path"]
