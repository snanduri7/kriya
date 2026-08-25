"""MA7.8: WorkflowController migration_mode="enforce" (kriya/workflow/
workflow_controller.py::_run_structured_enforce) - the first mode where
WorkflowController actually owns the real outcome. Reuses
run_generation_workflow() once per subtask (the same real pattern
kriya/workflow/milestones.py::run_milestones() already uses per milestone)
rather than reimplementing edit-application/verification/approval."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import WorkflowController
from kriya.workflow.workflow_types import SubtaskStatus


def _route(kind=ChangeKind.TASK):
    return EngineeringRoute(
        kind=kind, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    )


def _workflow_engine(route=None):
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=route or _route())
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    return we


def _two_subtask_plan():
    return EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="write b.py", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"], planned_files=[PlannedFile(path="b.py", action=FileAction.CREATE)],
            ),
        ],
    )


def _patched(plan, validation_result=None):
    return (
        patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)),
        patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan),
        patch(
            "kriya.workflow.workflow_controller.validate_plan",
            new=AsyncMock(return_value=validation_result or PlanValidationResult(valid=True)),
        ),
    )


# --- core per-subtask orchestration ---

@pytest.mark.asyncio
async def test_enforce_calls_run_generation_workflow_once_per_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 2
    assert result.legacy_result["status"] == "success"
    assert [r.subtask_id for r in result.subtask_results] == ["s1", "s2"]
    assert all(r.status == SubtaskStatus.COMPLETED for r in result.subtask_results)


@pytest.mark.asyncio
async def test_enforce_logs_a_subtask_progress_banner_before_each_subtask(tmp_path, caplog):
    """Real live-validation gap, 2026-08-24: a 3-subtask enforce run's log
    showed 3 back-to-back PLANNING/ARCHITECTURE/DEVELOPMENT/REVIEW cycles
    with no indication of which subtask number was running or how many
    subtasks the plan had in total - unlike run_milestones(), which prints
    a real "MILESTONE 'id' (2/3): ..." banner per milestone. This mirrors
    that exact convention for subtasks."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with caplog.at_level("INFO", logger="kriya.workflow.workflow"):
        with p1, p2, p3:
            controller = WorkflowController(we)
            await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    banner_text = "\n".join(r.message for r in caplog.records)
    assert "SUBTASK 'S1' (1/2)" in banner_text.upper()
    assert "SUBTASK 'S2' (2/2)" in banner_text.upper()


@pytest.mark.asyncio
async def test_enforce_threads_established_files_forward_to_the_next_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "a.py").write_text("print('a')")
            return {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert calls[0]["established_files"] == []
    assert calls[1]["established_files"] == ["a.py"]
    assert "print('a')" in calls[1]["supplementary_context"]


@pytest.mark.asyncio
async def test_enforce_subtask_goal_text_names_the_real_subtask_and_dependency(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    # s1 has no depends_on - no header, matching build_milestone_goal_text's
    # own precedent (a DAG root gets no "depends on" preamble either)
    assert calls[0]["goal"] == "write a.py\n\nFiles this subtask should touch:\n- a.py (create)"
    # s2 depends on s1 - real header naming both the subtask id/position and the dependency
    assert "'s2' (2 of 2)" in calls[1]["goal"]
    assert "depending on: s1" in calls[1]["goal"]
    assert "write b.py" in calls[1]["goal"]


# --- stop-on-failure: real per-subtask retry locality ---

@pytest.mark.asyncio
async def test_enforce_stops_at_first_failing_subtask_never_calls_the_dependent(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "failed", "quality_gates_passed": False}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 1  # s2's own call never happened
    assert result.legacy_result["status"] == "failed"
    assert [r.subtask_id for r in result.subtask_results] == ["s1"]
    assert result.subtask_results[0].status == SubtaskStatus.FAILED


# --- honest, explicit refusal cases (clean failure, not a crash) ---

# --- MA7.8 fix (2026-08-24, real live-validation finding, protocol_encoder_java):
# a PRE-EXECUTION structural problem (no plan, zero subtasks, a TOOL
# subtask, failed validation) must fall back to the legacy whole-goal
# path, not produce zero files. Real live run: a single malformed subtask
# in Stage A's JSON block (execution_method=tool with no tool_name, a
# pydantic validation error) made the very first enforce-mode run produce
# nothing, even though the same goal would almost certainly have
# succeeded via the ordinary prose-based path - see
# _StructuredPlanUnavailable's own docstring for the full story.

@pytest.mark.asyncio
async def test_enforce_falls_back_to_legacy_when_plan_contains_a_tool_subtask(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="lint", execution_method=ExecutionMethod.TOOL, tool_name="lint")],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": ["a.py"]})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    we.run_generation_workflow.assert_awaited_once()
    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["files"] == ["a.py"]
    # no structured subtask machinery ran - the fallback is the ordinary
    # whole-goal legacy call, not a partial structured attempt
    assert result.subtask_results == ()


@pytest.mark.asyncio
async def test_enforce_falls_back_to_legacy_reproducing_the_real_live_validation_finding(tmp_path):
    """Reproduces the actual real-world failure verbatim (protocol_encoder_java,
    2026-08-24): parse_planner_structured_output returns a pydantic
    validation error, not a clean 'missing JSON block' message."""
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["Protocol.java", "App.java"],
    })

    real_error = (
        "structured plan JSON block failed schema validation: 1 validation error for "
        "PlannerStructuredOutput\nsubtasks.2\n  Value error, subtask 's3' has "
        "execution_method=tool but no tool_name"
    )
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(None, real_error)):
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    we.run_generation_workflow.assert_awaited_once()
    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["files"] == ["Protocol.java", "App.java"]


@pytest.mark.asyncio
async def test_enforce_falls_back_to_legacy_when_no_structured_plan(tmp_path):
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="prose only, no JSON block")
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(None, "no fenced JSON block found")):
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    we.run_generation_workflow.assert_awaited_once()
    assert result.legacy_result["status"] == "success"


@pytest.mark.asyncio
async def test_enforce_falls_back_to_legacy_when_plan_validation_fails(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan, validation_result=PlanValidationResult(valid=False, errors=["bad plan"]))
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    we.run_generation_workflow.assert_awaited_once()
    assert result.legacy_result["status"] == "success"


@pytest.mark.asyncio
async def test_enforce_legacy_fallback_forwards_legacy_kwargs(tmp_path):
    """The fallback call must be a real, correctly-parameterized legacy
    call - not a stub - so approval callbacks etc. still work."""
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})
    approval_cb = MagicMock()

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(None, "no json")):
        controller = WorkflowController(we)
        await controller.execute(
            "goal", str(tmp_path), migration_mode="enforce", approval_callback=approval_cb,
        )

    _, kwargs = we.run_generation_workflow.call_args
    assert kwargs["approval_callback"] is approval_cb


# --- enforce mode fully replaces legacy, never runs it too ---

@pytest.mark.asyncio
async def test_enforce_never_calls_run_generation_workflow_a_bonus_legacy_time(tmp_path):
    """Confirms enforce mode does NOT also invoke a separate 'legacy' pass
    the way shadow mode invokes shadow alongside legacy - enforce fully
    REPLACES the legacy call, it doesn't run alongside it."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "quality_gates_passed": True, "files": []}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    # exactly 2 calls (one per subtask) - not 3 (which would mean a bonus
    # whole-goal legacy call also happened)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_enforce_persists_control_state(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    state_file = tmp_path / ".kriya" / "control" / "state.json"
    assert state_file.is_file()
    assert result.control_state.current_plan_hash == plan.content_hash()


# --- MA7.9: real subtask retry-locality data ---

@pytest.mark.asyncio
async def test_enforce_aggregated_result_includes_real_subtask_retry_locality_data(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        return {
            "status": "success", "quality_gates_passed": True, "files": [],
            "generation_metrics": {"calls": n},  # subtask 1 -> 1 call, subtask 2 -> 2 calls
        }

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    locality = result.legacy_result["subtask_retry_locality"]
    assert locality == [
        {"subtask_id": "s1", "generation_calls": 1, "quality_gates_passed": True},
        {"subtask_id": "s2", "generation_calls": 2, "quality_gates_passed": True},
    ]


@pytest.mark.asyncio
async def test_enforce_retry_locality_data_present_even_on_a_failed_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "failed", "quality_gates_passed": False,
        "generation_metrics": {"calls": 4},
    })

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    locality = result.legacy_result["subtask_retry_locality"]
    assert len(locality) == 1  # only s1 ran before the stop
    assert locality[0]["subtask_id"] == "s1"
    assert locality[0]["generation_calls"] == 4
    assert locality[0]["quality_gates_passed"] is False


# --- MA7.10: real ArtifactRegistry derivation through an actual enforce run ---

@pytest.mark.asyncio
async def test_enforce_derives_and_persists_real_artifacts_on_full_success(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myproj"\nversion = "1.0.0"\n'
    )
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "success"
    derived = result.legacy_result.get("derived_artifacts")
    assert derived and derived[0]["ecosystem"] == "python"

    registry_file = tmp_path / ".kriya" / "control" / "artifacts.json"
    assert registry_file.is_file()


@pytest.mark.asyncio
async def test_enforce_does_not_derive_artifacts_when_a_subtask_fails(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myproj"\n')
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "failed", "quality_gates_passed": False})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert "derived_artifacts" not in result.legacy_result
    assert not (tmp_path / ".kriya" / "control" / "artifacts.json").exists()


@pytest.mark.asyncio
async def test_enforce_artifact_derivation_failure_is_non_fatal(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL)],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.ArtifactRegistry.derive_from_workspace",
        side_effect=RuntimeError("derivation exploded"),
    ):
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    # the real run's own success is unaffected by a broken derivation step
    assert result.legacy_result["status"] == "success"
    assert "derived_artifacts" not in result.legacy_result


@pytest.mark.asyncio
async def test_enforce_aggregated_status_is_failed_if_only_some_subtasks_ran(tmp_path):
    """Belt-and-suspenders on the aggregation logic itself: if somehow
    fewer subtask_results exist than the plan's real subtask count, the
    aggregated status must never read as success."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "failed", "quality_gates_passed": False})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["quality_gates_passed"] is False
    assert len(result.legacy_result["subtask_results"]) == 1


# --- subtask-spanning resume (MA5.9 finally wired to something, 2026-08-24) ---

import subprocess

from kriya.control.persistence import load_control_state


def _init_git_repo(path):
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, capture_output=True)
    (path / "README.md").write_text("seed")
    subprocess.run(["git", "add", "README.md"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=path, capture_output=True)


@pytest.mark.asyncio
async def test_enforce_persists_subtask_states_after_each_subtask(tmp_path):
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    persisted = load_control_state(str(tmp_path))
    assert persisted is not None
    assert persisted.subtask_states == {"s1": "completed", "s2": "completed"}
    assert persisted.current_plan_hash == plan.content_hash()


@pytest.mark.asyncio
async def test_enforce_without_resume_flag_never_skips_even_with_prior_state(tmp_path):
    """Resume is opt-in only, mirroring run_generation_workflow()'s own
    convention - a persisted ControlState from an earlier run must NOT be
    consulted at all unless resume/resume_id is explicitly passed."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        # "'s2'" only ever appears in s2's own goal text (its depends_on
        # header names it) - robust across however many execute() calls
        # happen in this test, unlike a bare len(calls) counter.
        path = "b.py" if "'s2'" in kwargs["goal"] else "a.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        # A second call, same workspace, NO resume flag - both subtasks must run again.
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 4


@pytest.mark.asyncio
async def test_enforce_resume_skips_every_subtask_when_the_whole_plan_already_completed(tmp_path):
    """Both subtasks genuinely completed on the first run - a resume must
    make ZERO new real calls, restoring everything from disk."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        first = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert len(calls) == 2

        second = await controller.execute(
            "goal", str(tmp_path), migration_mode="enforce", resume=True,
        )

    assert len(calls) == 2, "nothing new should have run - both subtasks were already completed"
    assert second.legacy_result["quality_gates_passed"] is True
    assert [r["subtask_id"] for r in second.legacy_result["subtask_results"]] == ["s1", "s2"]
    assert second.legacy_result["subtask_results"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_enforce_resume_skips_only_the_subtasks_already_completed(tmp_path):
    """Subtask 2 failed on the first attempt (never got to run) - a resume
    must skip s1 (real) and still execute s2 for real."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run_first_attempt(**kwargs):
        (tmp_path / "a.py").write_text("# a.py")
        return {"status": "failed", "quality_gates_passed": False, "files": ["a.py"]} \
            if "'s2'" in kwargs["goal"] else {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    async def fake_run_second_attempt(**kwargs):
        (tmp_path / "b.py").write_text("# b.py")
        return {"status": "success", "quality_gates_passed": True, "files": ["b.py"]}

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        we.run_generation_workflow = fake_run_first_attempt
        first = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert first.legacy_result["quality_gates_passed"] is False

        we.run_generation_workflow = AsyncMock(side_effect=fake_run_second_attempt)
        second = await controller.execute(
            "goal", str(tmp_path), migration_mode="enforce", resume=True,
        )

    assert second.legacy_result["quality_gates_passed"] is True
    we.run_generation_workflow.assert_awaited_once()
    awaited_goal = we.run_generation_workflow.await_args.kwargs["goal"]
    assert "'s2'" in awaited_goal, "the one real call on resume must be for s2, not a re-run of s1"


@pytest.mark.asyncio
async def test_enforce_resume_refused_when_plan_hash_differs(tmp_path):
    _init_git_repo(tmp_path)
    plan_a = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) in (1, 3) else "b.py"
        (tmp_path / path).write_text(f"# {path} {len(calls)}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan_a)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    assert len(calls) == 2

    plan_b = EngineeringPlan(
        plan_id="run2", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="write a DIFFERENT a.py", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="write b.py", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"], planned_files=[PlannedFile(path="b.py", action=FileAction.CREATE)],
            ),
        ],
    )
    assert plan_b.content_hash() != plan_a.content_hash()

    p1, p2, p3 = _patched(plan_b)
    with p1, p2, p3:
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    # A different plan -> resume refused -> both subtasks run again for real.
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_enforce_resume_refused_when_workspace_has_drifted(tmp_path):
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) in (1, 3) else "b.py"
        (tmp_path / path).write_text(f"# {path} {len(calls)}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert len(calls) == 2

        # Real drift: an unrelated commit lands in the workspace between runs.
        (tmp_path / "unrelated.txt").write_text("someone else's change")
        subprocess.run(["git", "add", "unrelated.txt"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "drift"], cwd=str(tmp_path), capture_output=True)

        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert len(calls) == 4


@pytest.mark.asyncio
async def test_enforce_resume_with_no_prior_state_behaves_like_a_fresh_run(tmp_path):
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert result.legacy_result["quality_gates_passed"] is True
    assert len(calls) == 2


# --- extension_points exemption during a real resume (2026-08-24 live-validation finding) ---

@pytest.mark.asyncio
async def test_enforce_resume_passes_own_progress_flag_to_validate_plan_when_a_subtask_already_completed(tmp_path):
    """Real live-validation bug, protocol_encoder_java: subtask s1
    genuinely completed (real file on disk), s2 exhausted its retries. On
    resume, the freshly re-planned goal correctly triggered extension_points
    validation (workspace is no longer empty) but the Planner wasn't
    prompted about continuation and didn't supply one - sending the
    resumed run down the legacy whole-goal fallback, which regenerated and
    broke s1's already-working file. WorkflowController must tell
    validate_plan this is its own established progress, not foreign
    existing work, whenever a real resume is in progress."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run_first(**kwargs):
        (tmp_path / "a.py").write_text("# a.py")
        return {"status": "failed", "quality_gates_passed": False, "files": ["a.py"]} \
            if "'s2'" in kwargs["goal"] else {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3 as mock_validate_plan:
        controller = WorkflowController(we)
        we.run_generation_workflow = fake_run_first
        first = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
        assert first.legacy_result["quality_gates_passed"] is False
        assert mock_validate_plan.await_args.kwargs["resuming_own_established_progress"] is False, (
            "the first, non-resumed call must never claim established progress"
        )

        we.run_generation_workflow = AsyncMock(
            return_value={"status": "success", "quality_gates_passed": True, "files": ["b.py"]},
        )
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert mock_validate_plan.await_args.kwargs["resuming_own_established_progress"] is True, (
        "a resume with a real completed subtask must tell validate_plan so"
    )


@pytest.mark.asyncio
async def test_enforce_resume_does_not_claim_own_progress_when_nothing_completed_yet(tmp_path):
    """A resume request with no prior completed subtask (e.g. a
    misconfigured --resume on a workspace with real, genuinely foreign
    existing content) must NOT exempt extension_points - the exemption is
    earned by real completed progress, not merely by passing --resume."""
    _init_git_repo(tmp_path)
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(
        return_value={"status": "success", "quality_gates_passed": True, "files": ["a.py"]},
    )

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3 as mock_validate_plan:
        controller = WorkflowController(we)
        # No prior ControlState exists for this workspace at all.
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert mock_validate_plan.await_args.kwargs["resuming_own_established_progress"] is False


# --- MA7-C1 (2026-08-25 external review): bounded subtask execution ---

@pytest.mark.asyncio
async def test_enforce_passes_predetermined_plan_design_and_files_from_the_subtask(tmp_path):
    """The core MA7-C1 wiring: each subtask's own run_generation_workflow()
    call must receive predetermined_plan/predetermined_design/
    predetermined_architect_files derived from the ALREADY-VALIDATED
    Subtask, not leave run_generation_workflow() to re-plan from scratch."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert len(calls) == 2
    assert calls[0]["predetermined_plan"] == "Implement: write a.py"
    assert calls[0]["predetermined_architect_files"] == ["a.py"]
    assert calls[0]["predetermined_design"]  # non-empty, real content
    assert calls[1]["predetermined_architect_files"] == ["b.py"]


@pytest.mark.asyncio
async def test_enforce_rejects_a_subtask_that_writes_an_undeclared_file(tmp_path):
    """MA6 invariant 4, finally enforced: a subtask reporting
    quality_gates_passed=True must still be rejected if it wrote a file
    outside its own declared planned_files - a Quality-Gates pass cannot
    silently broaden the validated subtask's scope."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py", "unexpected.py"],
    })

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["quality_gates_passed"] is False
    s1_result = result.subtask_results[0]
    assert s1_result.status == SubtaskStatus.FAILED
    assert s1_result.undeclared_files == ("unexpected.py",)
    assert "unexpected.py" in s1_result.error
    # A rejected s1 must stop the plan - s2 (which depends on s1) never runs.
    we.run_generation_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_accepts_a_subtask_that_stays_within_planned_files(tmp_path):
    """Sanity check: scope enforcement must not false-positive on the
    ordinary, correct case."""
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run(**kwargs):
        path = "a.py" if kwargs["predetermined_architect_files"] == ["a.py"] else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["quality_gates_passed"] is True
    assert all(r.undeclared_files == () for r in result.subtask_results)


@pytest.mark.asyncio
async def test_enforce_scope_check_skipped_when_subtask_declares_no_planned_files(tmp_path):
    """A subtask with an empty planned_files list has nothing to enforce
    against - must not guarantee-fail on the first file it ever writes."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="do something", execution_method=ExecutionMethod.MODEL)],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["whatever.py"],
    })

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["quality_gates_passed"] is True
    assert result.subtask_results[0].undeclared_files == ()
