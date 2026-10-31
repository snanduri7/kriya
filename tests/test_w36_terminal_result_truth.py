"""W3-6 terminal result truth (signed 2026-10-30; handover/WAVE3_LEDGER_2026-10-30.md section 3): the visible result derives from the authoritative terminal
transaction state at its first wrong owner. Registry row KERNEL-RESULT-NO-CATEGORY-MORE-PATHS-001 (+ the facets W3-5 routed here).

Slice A - an enforce run whose terminal COMMIT FAILED. MEASURED (W3-0, worker C1, re-run by the coordinator): the workspace holds none of the generated files, yet the CLI
printed ``Files written to workspace: a.py`` (``_echo_needs_review`` printed ``res["files"]`` unconditionally) and the commit-failure advice was never printed (it keyed
on a ``failure_category`` that enforce results never carried, and ``generate``'s needs_review branch never reaches it).
"""
import os
import sys
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(__file__))
import test_e12_enforce_characterization as e12  # noqa: E402

from kriya import cli  # noqa: E402
from kriya.workflow.workflow_controller import WorkflowController  # noqa: E402

C = e12.C


async def _enforce_with_failed_commit(tmp_path, workspace_state):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(e12._PYPROJECT)
    we = e12._enforce_engine(workspace)
    calls = []
    we.run_generation_workflow = e12._writer(workspace, calls)
    p1, p2, p3 = e12._planned(e12._plan_of(e12._unit("s1")))
    with p1, p2, p3, patch(f"{C}.create_git_worktree", side_effect=e12._sandboxed()), patch(f"{C}.remove_git_worktree"), \
         patch(f"{C}.commit_verified_candidate", side_effect=lambda report, request: e12._commit_failure(workspace_state)), \
         patch(f"{C}.save_artifact_registry"), patch(f"{C}.save_approved_plan"):
        result = await WorkflowController(we).execute("Create a.py", str(workspace), migration_mode="enforce")
    return result.legacy_result, workspace, calls


def _cli_view(legacy):
    """What ``kriya generate`` prints for this result: its needs_review branch (the branch an enforce commit failure takes)."""
    @click.command()
    def show():
        if legacy.get("status") == "needs_review":
            cli._echo_needs_review(legacy)

    return CliRunner().invoke(show).output


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_state", ["UNCHANGED", "UNCERTAIN"])
async def test_an_enforce_commit_failure_never_claims_the_files_were_written(tmp_path, workspace_state):
    legacy, workspace, calls = await _enforce_with_failed_commit(tmp_path, workspace_state)
    assert calls, "the Developer ran: the candidate exists, only its commit failed"
    assert (legacy["status"], legacy["quality_gates_passed"]) == ("needs_review", False)
    # the existing category the direct path uses for the same terminal state, and the message it carries
    assert legacy["failure_category"] == "workspace_commit_failed"
    assert legacy["environment_failure"].startswith(f"WORKSPACE_COMMIT_NOT_COMPLETED: WORKSPACE_COMMIT_FAILED (workspace {workspace_state}): disk full")
    # the fact: the UNCHANGED workspace holds none of the generated files
    if workspace_state == "UNCHANGED":
        assert not (workspace / "a.py").exists()
    view = _cli_view(legacy)
    assert "Files written to workspace" not in view
    assert "[WORKSPACE COMMIT NOT COMPLETED]" in view and "disk full" in view
    if workspace_state == "UNCERTAIN":
        assert "The workspace state is UNCERTAIN: run `kriya runs recover`" in view
    else:
        assert "The workspace is unchanged." in view


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_committed_enforce_run_with_a_record_problem_still_reports_its_files_as_written(tmp_path):
    """Negative control (RDCG-B-001, the case ``_echo_needs_review`` was written for): the commit is CONFIRMED and only the run record could not be saved, so the files
    ARE in the workspace and the line stays."""
    view = _cli_view({"status": "needs_review", "quality_gates_passed": False, "files": ["a.py"],
                      "post_commit_persistence_errors": [{"operation": "save_approved_plan", "error": "OSError: disk full"}]})
    assert "Files written to workspace: a.py" in view and "Run record problem (save_approved_plan)" in view
    assert "[WORKSPACE COMMIT NOT COMPLETED]" not in view


# ============================================================================================================================== Slice B - the category of a terminal result
# KERNEL-RESULT-NO-CATEGORY-MORE-PATHS-001 / KERNEL-STAGE-STOP-NO-CATEGORY-001 (producer facet) / OI-06 / OI-40 / OI-69. MEASURED (E14 PATH_TABLE): the design-stage stops, the
# stage-4 baseline_indeterminate stop and every enforce-path result carried no ``failure_category`` while the SAME run's traces.db row did (= the status for the stage stops; the
# enforce trace rule for enforce). One consumer copies it (control/launch.py finish), so the launch record and the trace row disagreed. The invariant: a non-success terminal
# result carries the category its own trace row records.

from test_e14_pre_characterization import _trace_rows, kg  # noqa: E402

_STOP_SCENARIOS = {
    "legacy_knowledge_gap": "knowledge_gap",
    "legacy_planner_output_incomplete": "planner_output_incomplete",
    "legacy_planner_unauthorized_path": "planner_output_unauthorized_path",
    "legacy_planner_schema_invalid": "planner_output_schema_invalid",
    "legacy_architect_file_list_rejected": "architect_file_list_rejected",
    "legacy_baseline_indeterminate": "baseline_indeterminate",
}


@pytest.mark.parametrize("name", sorted(_STOP_SCENARIOS))
def test_a_stage_stop_result_carries_the_category_its_trace_row_records(name, tmp_path):
    outcome = kg.SCENARIOS[name](tmp_path)
    legacy = outcome.result.legacy_result if hasattr(outcome.result, "legacy_result") else outcome.result
    rows = _trace_rows(outcome.cfg)
    assert [(row[1], row[2]) for row in rows] == [(_STOP_SCENARIOS[name], _STOP_SCENARIOS[name])]
    assert legacy["status"] == _STOP_SCENARIOS[name] and legacy["failure_category"] == rows[0][2]
    # the stage-stop contract of W3-5 is untouched: the planner/knowledge/architect stops still carry no quality_gates_passed, so ``kriya fix`` reports them as stage stops
    if name != "legacy_baseline_indeterminate":
        assert "quality_gates_passed" not in legacy or name == "legacy_architect_file_list_rejected"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_enforce_plan_repair_result_carries_the_category_its_trace_row_records(tmp_path):
    from test_e14_pre_characterization import _enforce_engine as planner_engine
    from test_e14_pre_characterization import _planner_text

    we, cfg = planner_engine(tmp_path, _planner_text("unique_ids"))
    result = await WorkflowController(we).execute("Create a.py", str(tmp_path), migration_mode="enforce")
    legacy = result.legacy_result
    rows = _trace_rows(cfg)
    assert legacy["status"] == "needs_review" and legacy["failure_type"] == "PLANNING_ERROR"
    assert [row[2] for row in rows] == ["PLANNING_ERROR"] and legacy["failure_category"] == "PLANNING_ERROR"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_enforce_gate_failure_result_carries_the_category_its_trace_row_records(tmp_path):
    (tmp_path / "pyproject.toml").write_text(e12._PYPROJECT)
    we = e12._enforce_engine(tmp_path)
    result, _calls = await e12._enforce(we, tmp_path, e12._plan_of(e12._unit("s1")),
                                        outcomes={"s1": {"quality_gates_passed": False, "status": "failed"}})
    legacy = result.legacy_result
    assert legacy["status"] == "failed"
    rows = [r for r in _trace_rows(we.kernel.config) if r[0].endswith(".enforce")]
    assert len(rows) == 1 and legacy["failure_category"] == rows[0][2] and rows[0][2] == "failed"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_successful_enforce_run_carries_no_failure_category(tmp_path):
    """Negative control: success has no category (its trace row's category is NULL), and a result that already carries a more specific category keeps it."""
    (tmp_path / "pyproject.toml").write_text(e12._PYPROJECT)
    we = e12._enforce_engine(tmp_path)
    result, _calls = await e12._enforce(we, tmp_path, e12._plan_of(e12._unit("s1")))
    assert result.legacy_result["status"] == "success" and "failure_category" not in result.legacy_result
    legacy, _workspace, _calls = await _enforce_with_failed_commit(tmp_path / "second", "UNCHANGED") if (tmp_path / "second").mkdir() is None else (None, None, None)
    assert legacy["failure_category"] == "workspace_commit_failed"        # not overwritten by the trace-rule default (needs_review)


@pytest.mark.parametrize("name,category", [("legacy_approval_refused", "human_rejected"), ("legacy_approval_required_no_callback", "approval_required")])
def test_an_approval_abort_result_carries_the_category_its_trace_row_records(name, category, tmp_path):
    """KERNEL-HUMAN-REJECTED-NO-CATEGORY-001 (the sibling of the stage stops): the abort after the Developer loop (a human refusal, or an approval nobody could give)."""
    outcome = kg.SCENARIOS[name](tmp_path)
    legacy = outcome.result.legacy_result if hasattr(outcome.result, "legacy_result") else outcome.result
    rows = _trace_rows(outcome.cfg)
    assert [(row[1], row[2]) for row in rows] == [(category, category)]
    assert legacy["failure_category"] == category == rows[0][2] and legacy["quality_gates_passed"] is False and legacy["files"] == []


# ============================================================================================================================== Slice C - the exit status of `kriya fix`
# KRIYA-FIX-EXIT-CODE-ZERO-ON-FAILURE-001 (MEASURED in W3-5): `kriya fix` exited 0 when the repair FAILED, so a script could not tell a failed repair from a success.
# `generate` and the proposal executor end with ``sys.exit(0 if result.get("quality_gates_passed") else 1)``; `fix` was the outlier.

from test_cli_smoke import _FAKE_GENERATE_RESULT  # noqa: E402
from test_w35_typed_boundaries import _fix_with_result  # noqa: E402


@pytest.mark.parametrize("fields,expected_exit", [
    ({"status": "success", "quality_gates_passed": True}, 0),
    ({"status": "failure", "quality_gates_passed": False}, 1),
    ({"status": "failed", "quality_gates_passed": False}, 1),
    ({"status": "needs_review", "quality_gates_passed": False}, 1),
])
def test_kriya_fix_exits_by_the_terminal_result_like_generate(tmp_path, fields, expected_exit):
    result = _fix_with_result(tmp_path, dict(_FAKE_GENERATE_RESULT, **fields))
    assert result.exit_code == expected_exit, result.output[-400:]
    assert ("[SUCCESS]" in result.output) == (expected_exit == 0)


def test_kriya_fix_exits_non_zero_for_a_result_with_neither_a_status_nor_the_key(tmp_path):
    """A malformed result is never an exit-0 success."""
    malformed = {k: v for k, v in _FAKE_GENERATE_RESULT.items() if k not in ("status", "quality_gates_passed")}
    assert _fix_with_result(tmp_path, malformed).exit_code == 1
