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
import json
import sqlite3
import sys
from pathlib import Path
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


# ----------------------------------------------------------------------------- E5 (OI-39): the stage-4 prelude stops
# and the E3 design-stage exits, written from aad9ab1 BEFORE the prelude moved out of run_generation_workflow and before
# their inline short rows were routed through run_lifecycle.persist_early_exit_trace (OI-32). Each pins the result
# dict, the row's every column (the clock and the run id aside) and log_run's keyword order. The prelude stops and the
# KnowledgeGuard exit also run with a milestone/enforce identity (a direct engine call), so a dropped or swapped
# identity column is visible; the kernel goldens legacy_baseline_indeterminate / legacy_worktree_sync_refused hold the
# same runs end to end (result, RunRecord, evidence, events).

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

# the columns a short row leaves at the store's defaults
STORE_DEFAULTS = {"retrieved_chunks": "[]", "active_skills": "", "prompt_rendered": "", "gate_outcomes": "[]",
                  "model_hops": "[]", "evidence_records": "[]", "generation_metrics": "{}", "failure_report": "[]"}
IDENTITY = {"milestone_group_id": "mg-e5", "milestone_index": 2, "milestone_total": 3, "enforce_run_id": "run-e5.enforce"}
NO_IDENTITY = {key: None for key in IDENTITY}
# what every run records before its first stage (the goal derives requirements; the contract is sealed)
SEALED = OPENING_EVENTS + ["verification_contract.sealed"]


@pytest.fixture
def _git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


def _short_row(row):
    shape = {k: v for k, v in row.items() if k not in ("timestamp", "duration_sec", "run_id", "run_events")}
    shape["run_event_kinds"] = [e["kind"] for e in json.loads(row["run_events"] or "[]")]
    return shape


def _expected_row(goal, status, category, identity, kinds):
    return {"goal": goal, "attempts": 0, "status": status, "files_modified": "", "failure_category": category,
            **STORE_DEFAULTS, **identity, "run_event_kinds": kinds}


def _required_baseline(cfg):
    cfg.autonomy.brownfield_full_regression_baseline_policy = "required"


def _crashed_runner(self, target_test=None, *args, **kwargs):
    raise RuntimeError("test runner crashed")


def _uncommitted(workspace):
    (workspace / "notes.txt").write_text("uncommitted work\n")


def _baseline_stop(tmp_path, spy):
    return kg._apply_run(tmp_path, configure=_required_baseline, controller=False, run_kwargs=dict(IDENTITY),
                         patches=[spy, patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=_crashed_runner)])


def _sync_stop(tmp_path, spy):
    from kriya.workflow import worktree as worktree_module

    return kg._apply_run(tmp_path, prepare=_uncommitted, controller=False, run_kwargs=dict(IDENTITY),
                         patches=[spy, patch.object(worktree_module.shutil, "copy2", side_effect=OSError("disk full"))])


def test_the_baseline_indeterminate_stop_writes_the_short_row(tmp_path, _git_identity):
    spy, kwargs = _spy_log_run()
    outcome = _baseline_stop(tmp_path, spy)
    res = outcome.result
    workspace = outcome.workspace
    assert res == {
        "status": "baseline_indeterminate",
        "reason": ("brownfield_full_regression_baseline_policy is 'required' but a trustworthy PRE-mutation baseline "
                   "could not be established: validator raised: test runner crashed"),
        "plan": "Step 1: add mul", "design": '{"files": ["calc.py"]}', "goal": kg.GOAL, "workspace_path": workspace,
        "run_id": res["run_id"], "quality_gates_passed": False}
    [row] = _rows(outcome.cfg)
    assert row["run_id"] == res["run_id"] and kwargs == [SHORT_ROW_KWARGS]
    assert _short_row(row) == _expected_row(kg.GOAL, "baseline_indeterminate", "baseline_indeterminate", IDENTITY, SEALED + [
        "requirement.lineage", "requirement.lineage", "validation_baseline.policy",
        "validation_baseline.full_regression_source", "model.role_metrics"])
    assert outcome.extra["model_calls"]["developer"] == 0 and outcome.extra["approvals"] == []
    assert [c["stage"] for c in outcome.extra["checkpoints"]] == ["design"]


def test_the_worktree_sync_refusal_writes_the_short_row(tmp_path, _git_identity):
    spy, kwargs = _spy_log_run()
    outcome = _sync_stop(tmp_path, spy)
    res = outcome.result
    assert res == {
        "status": "failure", "failure_category": "worktree_sync_failed", "reason_codes": ["WORKTREE_SYNC_FAILED"],
        "error": "WORKTREE_SYNC_FAILED: copying uncommitted work failed: disk full", "plan": "Step 1: add mul",
        "design": '{"files": ["calc.py"]}', "goal": kg.GOAL, "workspace_path": outcome.workspace, "run_id": res["run_id"],
        "files": [], "quality_gates_passed": False}
    [row] = _rows(outcome.cfg)
    assert row["run_id"] == res["run_id"] and kwargs == [SHORT_ROW_KWARGS]
    assert _short_row(row) == _expected_row(kg.GOAL, "failure", "worktree_sync_failed", IDENTITY, SEALED + [
        "requirement.lineage", "requirement.lineage", "validation_baseline.policy", "model.role_metrics"])
    assert outcome.extra["model_calls"]["developer"] == 0 and outcome.extra["approvals"] == []
    assert [c["stage"] for c in outcome.extra["checkpoints"]] == ["design"]
    assert outcome.extra["workspace_files"] == kg._tree_digests(Path(outcome.workspace))   # nothing applied
    assert set(outcome.extra["workspace_files"]) == {"calc.py", "test_calc.py", "notes.txt"}


def test_the_knowledge_gap_stop_writes_the_short_row_with_the_run_identity(tmp_path, _git_identity):
    spy, kwargs = _spy_log_run()
    outcome = kg._apply_run(tmp_path, controller=False, patches=[spy],
                            run_kwargs={"goal": kg.STAGE_LIBRARY_GOAL, **IDENTITY})
    res = outcome.result
    assert sorted(res) == ["gap_report", "goal", "run_id", "status", "workspace_path"] and res["status"] == "knowledge_gap"
    [row] = _rows(outcome.cfg)
    assert row["run_id"] == res["run_id"] and kwargs == [SHORT_ROW_KWARGS]
    assert _short_row(row) == _expected_row(kg.STAGE_LIBRARY_GOAL, "knowledge_gap", "knowledge_gap", IDENTITY,
                                            SEALED + ["model.role_metrics"])


DESIGN_STAGE_EXITS = {
    "knowledge_gap": (dict(goal=kg.STAGE_LIBRARY_GOAL), SEALED + ["model.role_metrics"]),
    "planner_output_incomplete": (dict(planner=lambda n: ""), SEALED + ["model.role_metrics"]),
    "planner_output_unauthorized_path": (dict(planner=lambda n: kg._escaping_plan()), SEALED + ["model.role_metrics"]),
    "planner_output_schema_invalid": (dict(planner=lambda n: kg._schema_invalid_plan()), SEALED + [
        "structured_plan_validation", "structured_plan_repair_requested", "structured_plan_repair_result",
        "structured_plan_validation", "structured_plan_repair_requested", "structured_plan_repair_result",
        "model.role_metrics"]),
    "architect_file_list_rejected": (dict(architect=lambda n: json.dumps({"files": ["../outside/evil.py"]})), SEALED + [
        "requirement.lineage", "requirement.lineage", "plan.file_list_entries_rejected", "model.role_metrics"]),
}


@pytest.mark.parametrize("status", sorted(DESIGN_STAGE_EXITS))
def test_a_design_stage_exit_writes_the_short_row(status, tmp_path, _git_identity):
    run, kinds = DESIGN_STAGE_EXITS[status]
    spy, kwargs = _spy_log_run()
    outcome = kg._stage_run(tmp_path, patches=[spy], **run)
    legacy = outcome.result.legacy_result
    assert legacy["status"] == status and "failure_category" not in legacy   # OI-40: only the row carries it
    [row] = _rows(outcome.cfg)
    assert row["run_id"] == legacy["run_id"] and kwargs == [SHORT_ROW_KWARGS]
    assert _short_row(row) == _expected_row(run.get("goal", kg.GOAL), status, status, NO_IDENTITY, kinds)
