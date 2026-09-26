"""PROMPT-BUDGET-FIT-001C: a final Reviewer request that PRD-016 refuses
before inference ends the run as a typed non-success, never as a raw
exception and never as SUCCESS. What already happened (gates passed, the
candidate applied and committed) is reported as it is: nothing is rolled back
and nothing is retried.

Direct and milestone runs go through the real CLI, WorkflowEngine, milestone
driver and LLMClient (transport and runtime probe are stand-ins); the refusal
is a genuine ContextBudgetUnsatisfiableError from token_budget.plan_dispatch.
"""
import glob
import json
import os
import sqlite3
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from test_prd020_milestone_requirements import Transport, _probe

from kriya.cli import main
from kriya.config import AppConfig
from kriya.control.run_record import RunRecord
from kriya.core import model_runtime
from kriya.core.llm import LLMClient
from kriya.core.role_metrics import current_model_role
from kriya.core.state_paths import trace_db_path
from kriya.core.token_budget import CONTEXT_BUDGET_UNSATISFIABLE, plan_dispatch
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine


class RefusingReviewer:
    """LLMClient._dispatch_budget stand-in: a Reviewer request whose prompt
    contains ``marker`` (every Reviewer request when None) is refused by the
    real plan_dispatch against an undersized window; every other request is
    planned as usual."""

    def __init__(self, marker=None):
        self.marker = marker
        self.refused = 0
        self.original = LLMClient._dispatch_budget

    def __call__(self, client, **kwargs):
        text = "".join(str(message.get("content") or "") for message in kwargs["messages"])
        if current_model_role() == "reviewer" and (self.marker is None or self.marker in text):
            self.refused += 1
            return plan_dispatch(messages=kwargs["messages"], requested_max_tokens=1024,
                                 context_window=512, window_source="served_num_ctx")
        return self.original(client, **kwargs)


def _workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=workspace, check=True)
    return workspace


def _config(tmp_path):
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    return cfg


def _invoke(tmp_path, monkeypatch, args, refusing):
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    monkeypatch.chdir(tmp_path / "ws")
    cfg = _config(tmp_path)
    results = []
    real_run = WorkflowEngine.run_generation_workflow

    async def capturing(self, *a, **kw):
        result = await real_run(self, *a, **kw)
        results.append(result)
        return result

    with patch("kriya.cli.load_config", return_value=cfg), \
         patch.object(LLMClient, "_request_once", new=Transport()), \
         patch.object(LLMClient, "_dispatch_budget", new=lambda client, **kw: refusing(client, **kw)), \
         patch.object(WorkflowEngine, "run_generation_workflow", new=capturing):
        cli = CliRunner().invoke(main, args)
    return cfg, cli, results


def _run_record(workspace):
    [path] = glob.glob(os.path.join(str(workspace), ".kriya", "control", "runs", "*.json"))
    with open(path, encoding="utf-8") as handle:
        return RunRecord.from_dict(json.load(handle))


def _trace_rows(cfg):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        return db.execute("SELECT status, failure_category, run_events FROM runs ORDER BY rowid").fetchall()


def test_a_refused_final_review_after_an_applied_commit_is_a_typed_non_success(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    refusing = RefusingReviewer()
    cfg, cli, results = _invoke(tmp_path, monkeypatch, ["generate", "build M1: create m1.py", "-y"], refusing)

    assert cli.exception is None or isinstance(cli.exception, SystemExit), cli.output  # nothing raw escapes
    assert cli.exit_code != 0 and refusing.refused == 1  # not retried
    # The direct goal runs as a one-unit plan: the unit's result is the run's.
    result = results[-1]
    # Never SUCCESS, with the refusal as the typed terminal reason.
    assert result["quality_gates_passed"] is False
    assert result["failure_category"] == "final_review_refused"
    assert result["environment_failure"] is None
    # What already happened is reported as it is.
    assert result["candidate_gates_passed"] and result["terminal_regression_passed"]
    assert result["overall_attempt_passed"] is True
    refusal = result["final_review_refusal"]
    assert refusal["reason_code"] == CONTEXT_BUDGET_UNSATISFIABLE and "Nothing was sent" in refusal["detail"]
    assert refusal["candidate_applied"] is True and refusal["rolled_back"] is False
    record = _run_record(workspace)
    assert refusal["committed_work_units"] == record.committed_work_units() == ["direct"]
    assert record.terminal_status != "SUCCESS"
    assert (workspace / "m1.py").read_text().strip() == "VALUE = 'm1.py'"  # really applied, not rolled back
    # The trace and the CLI say the same, never "NOT applied".
    status, category, events = _trace_rows(cfg)[-1]
    assert (status, category) == ("failure", "final_review_refused")
    refused = [e["details"] for e in json.loads(events) if e["kind"] == "review.refused"]
    assert refused and refused[0]["reason_code"] == CONTEXT_BUDGET_UNSATISFIABLE
    assert "Files applied to workspace and committed (direct), not rolled back: m1.py" in cli.output
    assert "[FINAL REVIEW REFUSED]" in cli.output and "NOT applied" not in cli.output
    # Nothing left for a resume to redo.
    assert not glob.glob(os.path.join(str(workspace), ".kriya", "checkpoints", "*"))


def test_the_same_run_with_its_final_review_is_successful(tmp_path, monkeypatch):
    """Control: the refusal alone makes the difference."""
    workspace = _workspace(tmp_path)
    refusing = RefusingReviewer(marker="never present in any prompt")
    _, cli, results = _invoke(tmp_path, monkeypatch, ["generate", "build M1: create m1.py", "-y"], refusing)
    result = results[-1]

    assert cli.exit_code == 0, cli.output
    assert result["quality_gates_passed"] is True and result["failure_category"] is None
    assert "final_review_refusal" not in result and result["review"]
    assert "Final review not performed" not in result["review"]
    assert _run_record(workspace).terminal_status == "SUCCESS"


def test_a_milestone_whose_final_review_is_refused_keeps_earlier_commits_and_fails(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"group_id": "fit001c", "original_goal": "Build m1.py and m2.py.", "milestones": [
        {"id": "M1", "goal": "build M1: create m1.py", "success_criterion": "m1.py exists", "depends_on": []},
        {"id": "M2", "goal": "build M2: create m2.py", "success_criterion": "m2.py exists", "depends_on": ["M1"]},
    ]}))
    refusing = RefusingReviewer(marker="build M2")
    _, cli, results = _invoke(tmp_path, monkeypatch, ["generate", "--from-milestones", str(plan), "-y"], refusing)

    assert cli.exception is None or isinstance(cli.exception, SystemExit), cli.output
    assert cli.exit_code != 0 and refusing.refused == 1
    m2 = [r for r in results if r.get("final_review_refusal")]
    assert m2 and all(r is m2[0] or r == m2[0] for r in m2) and m2[0]["failure_category"] == "final_review_refused"
    assert m2[0]["final_review_refusal"]["candidate_applied"] is True
    record = _run_record(workspace)
    assert record.terminal_status != "SUCCESS"
    committed = record.committed_work_units()
    assert committed[0] == "M1" and m2[0]["final_review_refusal"]["committed_work_units"] == committed
    assert (workspace / "m1.py").exists() and (workspace / "m2.py").exists()  # nothing rolled back
    payload = json.loads(cli.output[cli.output.index("{", cli.output.index("=== Milestone sequence")):])
    assert payload["work_unit_states"]["M2"]["status"] != "VERIFIED"
    assert payload["committed_work_units"] == committed and payload["committed_changes_retained"] is True


@pytest.mark.asyncio
async def test_an_enforce_subtask_whose_final_review_is_refused_fails_typed_and_commits_nothing(tmp_path):
    """Enforce subtasks run in the plan workspace; the live workspace changes
    only at the terminal commit, which a failed subtask never reaches."""
    from test_auth_goal_contamination_001 import _plan_copying
    from test_workflow_controller import _workflow_engine

    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.workflow_controller import WorkflowController

    plan = _plan_copying("create a.py")
    engine = _workflow_engine()
    engine.planner.run = AsyncMock(return_value="structured plan")
    refusal = {"reason_code": CONTEXT_BUDGET_UNSATISFIABLE, "detail": "CONTEXT_BUDGET_UNSATISFIABLE: refused",
               "batch": 1, "batches": 1, "candidate_applied": True, "committed_work_units": None,
               "rolled_back": False}

    async def generation(**kwargs):
        with open(os.path.join(kwargs["workspace_path"], "a.py"), "w", encoding="utf-8") as handle:
            handle.write("# generated\n")
        return {"status": "failure", "quality_gates_passed": False, "candidate_gates_passed": True,
                "terminal_regression_passed": True, "overall_attempt_passed": True,
                "failure_category": "final_review_refused", "final_review_refusal": refusal, "files": ["a.py"]}

    engine.run_generation_workflow = AsyncMock(side_effect=generation)
    workspace = tmp_path / "live"
    workspace.mkdir()
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(object(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan",
               new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        outcome = await WorkflowController(engine).execute("create a.py", str(workspace), migration_mode="enforce")

    legacy = outcome.legacy_result or {}
    assert legacy.get("quality_gates_passed") is False and legacy.get("status") != "success"
    assert engine.run_generation_workflow.await_count == 1  # not retried
    [subtask] = [r for r in legacy["subtask_results"] if r["subtask_id"] == "s1"]
    assert subtask["status"] == "failed" and CONTEXT_BUDGET_UNSATISFIABLE in subtask["reason_codes"]
    assert "final review not performed" in subtask["error"]
    assert not (workspace / "a.py").exists()  # nothing reached the live workspace


def test_terminal_quality_never_passes_with_a_refused_final_review():
    state = GenerationState.__new__(GenerationState)
    state.candidate_gates_succeeded = state.terminal_regression_succeeded = True
    state.overall_attempt_succeeded = state.quality_gates_succeeded = True
    state.api_contract_recovery = None
    state.final_review_refusal = None
    assert state.final_workflow_quality_passed() is True
    state.final_review_refusal = {"reason_code": CONTEXT_BUDGET_UNSATISFIABLE}
    assert state.final_workflow_quality_passed() is False


def test_kriya_fix_reports_a_refused_final_review_truthfully(tmp_path):
    """The fix command's own output branch: never "still fail", never unapplied."""
    from test_cli_smoke import _FAKE_GENERATE_RESULT, _mock_kernel, _mock_workflow_engine

    refused = dict(
        _FAKE_GENERATE_RESULT, quality_gates_passed=False, failure_category="final_review_refused",
        files=["m1.py"], final_review_refusal={
            "reason_code": CONTEXT_BUDGET_UNSATISFIABLE, "detail": "CONTEXT_BUDGET_UNSATISFIABLE: refused",
            "candidate_applied": True, "committed_work_units": ["direct"], "rolled_back": False},
    )
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        with patch("kriya.cli.WorkflowEngine", return_value=_mock_workflow_engine(refused)), \
             patch("kriya.cli.Kernel", side_effect=_mock_kernel), \
             patch("kriya.cli.LLMClient"):
            result = runner.invoke(main, ["fix", "--error", "some compile error", "-y"])

    assert "Files applied to workspace and committed (direct), not rolled back: m1.py" in result.output
    assert "[FINAL REVIEW REFUSED]" in result.output
    assert "still fail" not in result.output and "[SUCCESS]" not in result.output
