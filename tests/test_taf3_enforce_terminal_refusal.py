"""TAF-3 end-to-end control: a content-filter refusal raised by a REAL terminal requirement closer stays the
authoritative result of an enforce run.

The route is the real one: ``WorkflowController.execute(..., migration_mode="enforce")`` -> ``EnforceLoop.run`` ->
``TerminalGateService.run`` -> ``_requirement_gap`` -> the real ``close_requirements_by_mutation_scope`` ->
``mutation_scope_evidence`` -> ``refuse_repository_content_filters``. Only the planner parse, the plan build and the plan
validation are patched (the shape of ``test_worktree_canonical_root.py::_enforce``) and the subtask engine is a
coordinated stand-in that writes the candidate.

The cause is real, not planted: a filter defined in the repository's own ``.git/config`` is DORMANT at run entry, and the
candidate writes a ``.gitattributes`` that activates it, so the guarded diff the closer is about to run would execute the
driver. Before the fix the terminal gate's broad handler turned the typed refusal into a ``REQUIREMENTS_UNRESOLVED`` prose
gap (the security code survived only inside the text); the invariant pinned here is that the final result stays
machine-identifiably the refusal.
"""
import sqlite3
import subprocess
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config, strict_kernel

from kriya.control.run_coordinator import coordinated_mutation
from kriya.core.state_paths import trace_db_path
from kriya.repository.git import GIT_CONTENT_FILTER_REPOSITORY_DEFINED, RepositoryContentFilterRefused
from kriya.workflow import requirement_closers
from kriya.workflow import worktree as wt
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import WorkflowController

GOAL = "Update app.py. Do not modify any other file."


def _git(repo, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def _repo(tmp_path, marker):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "app.py").write_text("original\n")
    (repo / ".gitignore").write_text("target/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    script = tmp_path / "clean.sh"
    script.write_text(f'#!/bin/sh\necho ran >> "{marker}"\ncat\n')
    script.chmod(0o755)
    _git(repo, "config", "filter.kx.clean", str(script))      # repository-defined, DORMANT: nothing binds it yet
    return repo


def _engine(config, *, bind_filter):
    engine = MagicMock()
    engine.kernel = strict_kernel(config)
    engine.spec_compliance = MagicMock()
    engine.spec_compliance.check = AsyncMock(return_value={"verdict": "PASS", "requirements": []})
    engine.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT))
    engine.planner.run = AsyncMock(return_value="fake plan text")

    @coordinated_mutation
    async def run_generation_workflow(goal=None, workspace_path=None, **kwargs):
        own = wt.create_git_worktree(workspace_path)
        (Path(own) / "app.py").write_text("verified candidate\n")
        (Path(workspace_path) / "app.py").write_text((Path(own) / "app.py").read_text())   # promotion
        if bind_filter:
            (Path(workspace_path) / ".gitattributes").write_text("*.py filter=kx\n")       # what an app can write
        wt.remove_git_worktree(workspace_path, own)
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    engine.run_generation_workflow = run_generation_workflow
    return engine


async def _enforce(repo, engine):
    plan = EngineeringPlan(plan_id="taf3-e2e", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="update app", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)])])
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
            patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
            patch("kriya.workflow.workflow_controller.validate_plan",
                  new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        return await WorkflowController(engine).execute(GOAL, str(repo), migration_mode="enforce")


def _enforce_row(config):
    conn = sqlite3.connect(trace_db_path(config))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM runs WHERE run_id LIKE '%.enforce'")]
    finally:
        conn.close()
    assert len(rows) == 1, rows
    return rows[0]


def _counting_real_closer(monkeypatch):
    real = requirement_closers.close_requirements_by_mutation_scope
    calls = []

    def closer(*args, **kwargs):
        calls.append(args[3] if len(args) > 3 else kwargs.get("candidate_root"))
        return real(*args, **kwargs)
    monkeypatch.setattr(requirement_closers, "close_requirements_by_mutation_scope", closer)
    return calls


@pytest.mark.asyncio
async def test_control_a_dormant_filter_run_reaches_the_terminal_closer_and_succeeds(tmp_path, monkeypatch):
    """Negative control: the same run without the binding commits, and the real closer really ran in the terminal gate,
    so the refusal test below cannot pass vacuously by never reaching the closer."""
    marker = tmp_path / "FILTER_RAN"
    repo = _repo(tmp_path, marker)
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    calls = _counting_real_closer(monkeypatch)
    result = await _enforce(repo, _engine(config, bind_filter=False))
    assert result.legacy_result["status"] == "success", result.legacy_result
    assert len(calls) == 1, "the terminal mutation-scope closer never ran"
    assert (repo / "app.py").read_text() == "verified candidate\n"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_a_closers_real_refusal_ends_the_enforce_run_as_the_security_refusal(tmp_path, monkeypatch):
    marker = tmp_path / "FILTER_RAN"
    repo = _repo(tmp_path, marker)
    config = strict_config(autonomy={"spec_compliance_enabled": True})
    calls = _counting_real_closer(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        await _enforce(repo, _engine(config, bind_filter=True))
    frames = [frame.name for frame in traceback.extract_tb(raised.value.__traceback__)]
    assert raised.value.reason_code == GIT_CONTENT_FILTER_REPOSITORY_DEFINED and raised.value.driver == "kx"
    assert len(calls) == 1, calls                                  # the real closer ran exactly once
    assert "_requirement_gap" in frames and "mutation_scope_evidence" in frames, frames   # the real terminal route
    row = _enforce_row(config)
    assert (row["status"], row["failure_category"]) == ("error", "RepositoryContentFilterRefused")
    assert (repo / "app.py").read_text() == "original\n"            # nothing committed
    assert not marker.exists(), "the repository's filter ran"
