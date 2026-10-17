"""The run-opening early exits of ``run_generation_workflow`` and their traces.db rows (full modularization W4, E4;
OPEN-ITEMS OI-32), written BEFORE their trace writes moved to ``run_lifecycle.persist_early_exit_trace``.

Three exits happen after the run's trace identity exists and before any model call, each writing one SHORT row
(``attempts=0``, no files - stored as an empty string, MEASURED -, no gate outcomes/model hops/evidence): the VERIFICATION-CONTRACT-003 admission refusal
(``verification_authority_required``), the baseline authority that cannot execute
(``verification_authority_unavailable``) and NO_MUTATION_REQUIRED (a success without a model; the only row with no
failure_category). Each test pins the result dict's keys and the row: every column the short form writes and every
column it leaves at the store's default, with ``log_run``'s keyword arguments in the exact order the inline call
passed them.
"""
import sqlite3
from unittest.mock import patch

import pytest
from test_prd020_requirement_lineage import _engine, _gates_pass, _verdict_json, _verdicts_json
from test_verification_contract_003_baseline import CALC_OK, EXACT_GOAL
from test_verification_contract_003_baseline import _workspace as _calc_workspace

from kriya.core.state_paths import trace_db_path

OPENING_EVENTS = ["egress.authority", "evidence.attempt_store", "model.runtime", "model.role_independence", "requirement.derived",
                 "verification_contract.compiled"]
# the row drains the PRD-018 role metrics last (run_lifecycle.trace_run_events)
SHORT_ROW_KWARGS = ["run_id", "goal", "duration_sec", "attempts", "status", "files_modified", "failure_category",
                    "milestone_group_id", "milestone_index", "milestone_total", "enforce_run_id", "run_events"]


def _rows(cfg):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY rowid")]


def _row_shape(row):
    """The row without its clock (timestamp, duration) and with its run events as their kinds."""
    import json

    shape = {k: v for k, v in row.items() if k not in ("timestamp", "duration_sec", "run_events")}
    shape["run_event_kinds"] = [e["kind"] for e in json.loads(row["run_events"] or "[]")]
    return shape


def _spy_log_run():
    """Records each ``log_run`` call's keyword names (in order) and still writes the row."""
    from kriya.control.trace import TraceLogger

    real, calls = TraceLogger.log_run, []

    def spy(self, *args, **kwargs):
        calls.append(list(kwargs))
        return real(self, *args, **kwargs)
    return patch.object(TraceLogger, "log_run", spy), calls


def _common(row, trace_id, goal, status, category):
    assert row["run_id"] == trace_id and row["goal"] == goal and row["status"] == status
    assert row["attempts"] == 0 and row["files_modified"] == "" and row["failure_category"] == category


@pytest.mark.asyncio
async def test_the_admission_refusal_writes_the_short_failure_row(tmp_path):
    from test_prd020_requirement_lineage import GOAL

    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    spy, kwargs = _spy_log_run()
    p1, p2 = _gates_pass()
    with p1, p2, spy:
        res = await engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace))
    assert sorted(res) == ["authority_requests", "authority_requests_path", "environment_failure", "error",
                           "failure_category", "files", "goal", "quality_gates_passed", "reason_codes", "requirements_admission",
                           "run_id", "status", "verification_contract", "workspace_path"]
    assert (res["status"], res["failure_category"], res["files"], res["quality_gates_passed"]) == (
        "failure", "verification_authority_required", [], False)
    [row] = _rows(cfg)
    _common(row, res["run_id"], GOAL, "failure", "verification_authority_required")
    assert kwargs == [SHORT_ROW_KWARGS]
    shape = _row_shape(row)
    assert shape["gate_outcomes"] in (None, "[]") and shape["model_hops"] in (None, "[]")
    assert shape["run_event_kinds"] == OPENING_EVENTS + ["requirement.authority_required",
                                                         "verification_contract.authority_requested", "model.role_metrics"]
    assert engine.developer.run_generation.await_count == 0 and calls["planner"] == []


@pytest.mark.asyncio
async def test_an_unexecutable_baseline_authority_writes_the_short_failure_row(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = _calc_workspace(tmp_path, CALC_OK)
    spy, kwargs = _spy_log_run()
    # the baseline run itself raises (review VC3-R8 / D2): an environment outcome, a typed stop before any model call
    with spy, patch("kriya.workflow.contract_baseline.run_baseline_authorities", side_effect=OSError("no scratch space")):
        res = await engine.run_generation_workflow(goal=EXACT_GOAL, workspace_path=str(workspace))
    assert (res["status"], res["failure_category"], res["quality_gates_passed"]) == (
        "failure", "verification_authority_unavailable", False)
    assert {"goal", "workspace_path", "run_id"} <= set(res)
    [row] = _rows(cfg)
    _common(row, res["run_id"], EXACT_GOAL, "failure", "verification_authority_unavailable")
    assert kwargs == [SHORT_ROW_KWARGS]
    assert _row_shape(row)["run_event_kinds"] == OPENING_EVENTS + [
        "verification_contract.sealed", "verification_contract.baseline", "verification_contract.refused", "model.role_metrics"]
    assert engine.developer.run_generation.await_count == 0 and calls["planner"] == []


@pytest.mark.asyncio
async def test_no_mutation_required_writes_the_short_success_row_without_a_failure_category(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = _calc_workspace(tmp_path, CALC_OK)
    spy, kwargs = _spy_log_run()
    with spy, patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}):
        res = await engine.run_generation_workflow(goal=EXACT_GOAL, workspace_path=str(workspace))
    assert res["status"] == "success" and res["no_mutation_required"] is True
    [row] = _rows(cfg)
    _common(row, res["run_id"], EXACT_GOAL, "success", None)
    assert kwargs == [[k for k in SHORT_ROW_KWARGS if k != "failure_category"]]
    assert _row_shape(row)["run_event_kinds"] == OPENING_EVENTS + [
        "verification_contract.sealed", "verification_contract.baseline", "verification_contract.no_mutation_required",
        "model.role_metrics"]
    assert engine.developer.run_generation.await_count == 0 and calls["planner"] == []
