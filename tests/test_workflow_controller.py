"""MA6.8/6.9/6.10/6.13/6.14/MA7.2: WorkflowController (kriya/workflow/workflow_controller.py) -
first real pytest coverage for this module (previously only ad hoc-verified,
never as a permanent regression test).

Every test uses `tmp_path` (a real, isolated directory) rather than a bare
string like "/tmp/proj" - execute() now really writes to
<workspace_path>/.kriya/control/state.json (MA7.2's save_control_state),
so a fake, nonexistent workspace_path would leave real, uncleaned files
under a shared /tmp/proj on the machine running these tests."""

import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.control.persistence import load_control_state
from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
)
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import (
    WorkflowController, WorkflowControllerConfigurationError,
    compute_abandoned_plan_files, quarantine_abandoned_plan_files,
)
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus


def _route(kind=ChangeKind.TASK):
    """A real EngineeringRoute, not a MagicMock - MA7.2's save_control_state
    really JSON-serializes ControlState.engineering_route.to_dict(), which a
    MagicMock can't do (json.dumps fails on the mock object it returns).
    Using a real, minimal route here also exercises real
    process_profile_for()/execute()'s downstream code paths instead of
    special-casing mocked attribute lookups."""
    return EngineeringRoute(
        kind=kind, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    )


def _workflow_engine(route=None, legacy_result=None):
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=route or _route())
    we.run_generation_workflow = AsyncMock(return_value=legacy_result or {"status": "success", "run_id": "legacy-run"})
    return we


# --- abandoned-plan-file cleanup (2026-08-25 live finding) ---

def test_compute_abandoned_plan_files_flags_a_file_from_a_completed_subtask_the_new_plan_no_longer_declares():
    abandoned = compute_abandoned_plan_files(
        prior_subtask_states={"s1": "completed", "s2": "completed"},
        prior_subtask_written_files={"s1": ["a.py"], "s2": ["b.py"]},
        new_plan_files={"a.py", "c.py"},
    )
    assert abandoned == ["b.py"]


def test_compute_abandoned_plan_files_leaves_a_file_the_new_plan_still_declares():
    abandoned = compute_abandoned_plan_files(
        prior_subtask_states={"s1": "completed"},
        prior_subtask_written_files={"s1": ["a.py"]},
        new_plan_files={"a.py"},
    )
    assert abandoned == []


def test_compute_abandoned_plan_files_ignores_a_subtask_that_never_completed():
    """A failed/incomplete subtask's own written files are ordinary
    in-progress work, not abandoned residue - resume already refuses to
    reuse them for an unrelated reason (plan-hash/drift mismatch)."""
    abandoned = compute_abandoned_plan_files(
        prior_subtask_states={"s1": "failed"},
        prior_subtask_written_files={"s1": ["a.py"]},
        new_plan_files=set(),
    )
    assert abandoned == []


def test_quarantine_abandoned_plan_files_moves_the_file_and_leaves_no_original(tmp_path):
    (tmp_path / "b.py").write_text("# b")
    moved = quarantine_abandoned_plan_files(str(tmp_path), ["b.py"], "run-1")
    assert moved == ["b.py"]
    assert not (tmp_path / "b.py").exists()
    quarantined = tmp_path / ".kriya" / "abandoned_plan_files" / "run-1" / "b.py"
    assert quarantined.exists()
    assert quarantined.read_text() == "# b"


def test_quarantine_abandoned_plan_files_preserves_subdirectory_structure(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("# b")
    moved = quarantine_abandoned_plan_files(str(tmp_path), ["sub/b.py"], "run-1")
    assert moved == ["sub/b.py"]
    quarantined = tmp_path / ".kriya" / "abandoned_plan_files" / "run-1" / "sub" / "b.py"
    assert quarantined.exists()


def test_quarantine_abandoned_plan_files_skips_a_file_that_no_longer_exists():
    """Never guessed at, never raised - a file already gone (a previous
    quarantine, a manual delete) is simply not in the returned list."""
    moved = quarantine_abandoned_plan_files("/nonexistent/workspace", ["gone.py"], "run-1")
    assert moved == []


# --- migration_mode validation ---

@pytest.mark.asyncio
async def test_enforce_migration_mode_is_now_accepted_not_rejected(tmp_path):
    """MA7.8 (2026-08-24): 'enforce' is real, explicitly-authorized code
    now - the prior rejection (WorkflowControllerConfigurationError,
    'not safe yet') no longer applies. Full enforce-mode behavior coverage
    lives in tests/test_workflow_controller_enforce.py; this just confirms
    the mode string itself is no longer refused at the top of execute()."""
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="valid structured plan")
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": [],
    })
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    with patch(
        "kriya.workflow.workflow_controller.parse_planner_structured_output",
        return_value=(MagicMock(), None),
    ), patch(
        "kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
        return_value=plan,
    ), patch(
        "kriya.workflow.workflow_controller.validate_plan",
        new=AsyncMock(return_value=PlanValidationResult(valid=True)),
    ):
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    # Reaches real authoritative subtask execution, rather than relying on
    # the obsolete prose-only legacy fallback that plan repair now rejects.
    assert result.legacy_result["status"] == "success"
    we.run_generation_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_migration_mode_is_rejected(tmp_path):
    controller = WorkflowController(_workflow_engine())
    with pytest.raises(WorkflowControllerConfigurationError):
        await controller.execute("goal", str(tmp_path), migration_mode="bogus")


# --- legacy mode: zero-overhead passthrough, shadow fields stay empty ---

@pytest.mark.asyncio
async def test_legacy_mode_never_touches_the_shadow_path(tmp_path):
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "r1"})
    controller = WorkflowController(we)

    result = await controller.execute("goal", str(tmp_path), migration_mode="legacy")

    assert result.legacy_result == {"status": "success", "run_id": "r1"}
    assert result.subtask_results == ()
    assert result.decisions == ()
    assert result.verification_report is None
    we.planner.run.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_mode_forwards_legacy_kwargs_unchanged(tmp_path):
    we = _workflow_engine()
    controller = WorkflowController(we)
    approval_cb = MagicMock()

    await controller.execute(
        "goal", str(tmp_path), migration_mode="legacy", approval_callback=approval_cb, resume=True,
    )

    we.run_generation_workflow.assert_awaited_once_with(
        "goal", str(tmp_path),
        milestone_group_id=None, milestone_index=None, milestone_total=None,
        approval_callback=approval_cb, resume=True,
    )


# --- MA7.2: ControlState is now persisted ---

@pytest.mark.asyncio
async def test_execute_persists_control_state_to_disk(tmp_path):
    we = _workflow_engine()
    controller = WorkflowController(we)

    result = await controller.execute("goal", str(tmp_path), migration_mode="legacy")

    state_file = tmp_path / ".kriya" / "control" / "state.json"
    assert state_file.is_file()
    import json
    saved = json.loads(state_file.read_text())
    assert saved["run_id"] == result.run_id


@pytest.mark.asyncio
async def test_control_state_persist_failure_is_non_fatal(tmp_path, monkeypatch):
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "r1"})
    controller = WorkflowController(we)

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("kriya.workflow.workflow_controller.save_control_state", boom)

    result = await controller.execute("goal", str(tmp_path), migration_mode="legacy")

    assert result.legacy_result == {"status": "success", "run_id": "r1"}


# --- per-kind control-plane bookkeeping (MA6.9) ---

@pytest.mark.asyncio
async def test_milestone_kind_attaches_milestone_metadata(tmp_path):
    we = _workflow_engine(route=_route(kind=ChangeKind.MILESTONE))
    controller = WorkflowController(we)

    result = await controller.execute(
        "goal", str(tmp_path), migration_mode="legacy", milestone_group_id="grp1", milestone_index=2,
    )

    assert result.control_state.milestone_group_id == "grp1"
    assert result.control_state.current_milestone_id == "grp1:2"


@pytest.mark.asyncio
async def test_milestone_kind_with_no_group_id_leaves_state_unchanged(tmp_path):
    we = _workflow_engine(route=_route(kind=ChangeKind.MILESTONE))
    controller = WorkflowController(we)

    result = await controller.execute("goal", str(tmp_path), migration_mode="legacy")

    assert result.control_state.milestone_group_id is None


@pytest.mark.asyncio
async def test_refactor_kind_attaches_real_git_baseline():
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=d, capture_output=True)
        with open(f"{d}/a.py", "w") as f:
            f.write("x = 1")
        subprocess.run(["git", "add", "a.py"], cwd=d, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True)

        we = _workflow_engine(route=_route(kind=ChangeKind.REFACTOR))
        controller = WorkflowController(we)

        result = await controller.execute("goal", d, migration_mode="legacy")

        assert result.control_state.base_commit is not None
        assert result.control_state.tree_hash is not None


@pytest.mark.asyncio
async def test_task_kind_gets_no_extra_bookkeeping(tmp_path):
    we = _workflow_engine(route=_route(kind=ChangeKind.TASK))
    controller = WorkflowController(we)

    result = await controller.execute("goal", str(tmp_path), migration_mode="legacy")

    assert result.control_state.milestone_group_id is None
    assert result.control_state.base_commit is None


# --- shadow mode: never blocks or alters the real outcome ---

def _shadow_plan():
    return EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL)],
        acceptance_criteria=[AcceptanceCriterion(id="ac1", description="thing works")],
    )


@pytest.mark.asyncio
async def test_shadow_mode_still_returns_the_real_legacy_result_unchanged(tmp_path):
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(return_value=fake_result)):

        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    # the real outcome is exactly what the legacy call produced, regardless
    # of what the shadow run did
    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}
    we.run_generation_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_shadow_mode_populates_subtask_results_decisions_and_verification_report(tmp_path):
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(return_value=fake_result)):

        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    assert len(result.subtask_results) == 1
    assert result.subtask_results[0].subtask_id == "s1"
    decision_types = [d.type for d in result.decisions]
    assert "plan_created" in decision_types
    assert "subtask_attempt" in decision_types
    assert result.verification_report is not None
    assert result.control_state.current_plan_hash == _shadow_plan().content_hash()


@pytest.mark.asyncio
async def test_shadow_mode_records_undeclared_file_touch_decision(tmp_path):
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(
        subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model", undeclared_files=("surprise.py",),
    )

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(return_value=fake_result)):

        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    assert "subtask_undeclared_file_touch" in [d.type for d in result.decisions]


@pytest.mark.asyncio
async def test_shadow_mode_no_structured_plan_leaves_shadow_fields_empty_but_legacy_still_runs(tmp_path):
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(return_value="prose plan, no JSON block")

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(None, "no fenced JSON block found")):
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    assert result.subtask_results == ()
    assert result.decisions == ()
    assert result.verification_report is None
    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}


@pytest.mark.asyncio
async def test_shadow_mode_plan_validation_failure_still_lets_legacy_run(tmp_path):
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=_shadow_plan()), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=False, errors=["bad plan"]))):

        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    assert result.subtask_results == ()
    assert result.verification_report is None
    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}


@pytest.mark.asyncio
async def test_shadow_mode_never_really_executes_a_tool_tagged_subtask(tmp_path):
    """The real safety fix this test guards: a TOOL-tagged subtask (e.g.
    tool_name="shell") has real side effects the instant it runs -
    shadow's own contract is non-mutating/observational, so it must never
    reach subtask_executor.execute() for real, regardless of tool_name."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="run a shell command", execution_method=ExecutionMethod.TOOL,
            tool_name="shell", tool_arguments={"command": "sudo rm -rf /"},
        )],
    )
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None

    real_execute = AsyncMock()
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=real_execute):

        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    # the real tool dispatch was never called - this is the actual guarantee
    real_execute.assert_not_called()
    assert result.subtask_results[0].status == SubtaskStatus.NEEDS_REVIEW
    assert "TOOL subtasks" in result.subtask_results[0].error
    # still real telemetry - the attempt is recorded, not silently dropped
    assert "subtask_attempt" in [d.type for d in result.decisions]
    # and the real outcome (legacy) is completely unaffected
    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}


@pytest.mark.asyncio
async def test_shadow_mode_exception_is_swallowed_and_never_fails_the_real_run(tmp_path):
    we = _workflow_engine(legacy_result={"status": "success", "run_id": "legacy-run"})
    we.planner.run = AsyncMock(side_effect=RuntimeError("planner exploded"))

    controller = WorkflowController(we)
    result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    assert result.legacy_result == {"status": "success", "run_id": "legacy-run"}
    assert result.subtask_results == ()
    assert result.decisions == ()
    assert result.verification_report is None


# --- MA7.2: ContextOrchestrator + ContractRegistry/ArtifactRegistry wiring ---

@pytest.mark.asyncio
async def test_shadow_context_includes_real_on_disk_planned_file_content(tmp_path):
    (tmp_path / "a.py").write_text("print('hello')")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="edit a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[{"path": "a.py", "action": "modify"}],
        )],
    )
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    we.developer.run_generation = AsyncMock(return_value=[])

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):

        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    # real DeveloperAgent.run_generation was called (not the fully-mocked
    # subtask_executor.execute this time) - confirms _build_context handed
    # it real design_context containing a.py's actual on-disk content
    _, kwargs = we.developer.run_generation.call_args
    assert "print('hello')" in kwargs["design_context"]


@pytest.mark.asyncio
async def test_shadow_context_surfaces_persisted_contract_and_artifact_entries(tmp_path):
    from kriya.control.contracts import ContractRegistry
    from kriya.control.artifacts import ArtifactRecord, ArtifactRegistry
    from kriya.control.persistence import save_artifact_registry, save_contract_registry

    contracts = ContractRegistry()
    contracts.register(
        contract_id="c1", name="Widget", provider_milestone_id="m1",
        shape={"kind": "interface", "name": "Widget"},
    )
    save_contract_registry(str(tmp_path), contracts)

    artifacts = ArtifactRegistry()
    artifacts.record(ArtifactRecord(
        milestone_id="m1", ecosystem="python", kind="library",
        coordinates={"name": "mypkg", "version": "1.0"},
    ))
    save_artifact_registry(str(tmp_path), artifacts)

    plan = _shadow_plan()
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")

    captured_context = {}

    async def spy_execute(*, subtask, plan, context, **kwargs):
        captured_context["package"] = context
        return fake_result

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(side_effect=spy_execute)):

        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    package = captured_context["package"]
    assert len(package.contract_entries) == 1
    assert package.contract_entries[0]["id"] == "c1"
    assert len(package.artifact_entries) == 1
    assert package.artifact_entries[0]["coordinates"] == {"name": "mypkg", "version": "1.0"}


@pytest.mark.asyncio
async def test_shadow_context_empty_registries_are_honest_not_an_error(tmp_path):
    """No .kriya/control/ store exists at all for this workspace - real,
    common case (nothing registered yet), not a failure."""
    plan = _shadow_plan()
    we = _workflow_engine()
    we.planner.run = AsyncMock(return_value="fake plan text")
    we.kernel = None
    we.developer = MagicMock()
    fake_result = SubtaskResult(subtask_id="s1", status=SubtaskStatus.COMPLETED, execution_method="model")

    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output", return_value=plan), \
         patch("kriya.workflow.workflow_controller.validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))), \
         patch("kriya.workflow.workflow_controller.subtask_executor.execute", new=AsyncMock(return_value=fake_result)):

        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="shadow")

    # ran cleanly through to completion despite empty registries
    assert result.subtask_results[0].status == SubtaskStatus.COMPLETED


# --- execute_milestones (MA7-C4, 2026-08-25 external review) ---

def _milestone_run_state(group_id="grp-1", original_goal="build a thing"):
    return MagicMock(group_id=group_id, original_goal=original_goal)


@pytest.mark.asyncio
async def test_execute_milestones_calls_run_milestones_with_the_real_run_state_and_kwargs(tmp_path):
    we = _workflow_engine()
    run_state = _milestone_run_state()
    fake_run_milestones = AsyncMock(return_value={"status": "success"})

    with patch("kriya.workflow.milestones.run_milestones", fake_run_milestones):
        controller = WorkflowController(we)
        result = await controller.execute_milestones(
            run_state, str(tmp_path), knowledge_risk_confirmed=True, resume=False,
        )

    fake_run_milestones.assert_awaited_once_with(
        we, run_state, str(tmp_path), authoritative=True,
        knowledge_risk_confirmed=True, resume=False,
    )
    assert result.legacy_result == {"status": "success"}


@pytest.mark.asyncio
async def test_execute_milestones_persists_control_state_keyed_by_group_id(tmp_path):
    we = _workflow_engine()
    run_state = _milestone_run_state(group_id="grp-real")
    fake_run_milestones = AsyncMock(return_value={"status": "success"})

    with patch("kriya.workflow.milestones.run_milestones", fake_run_milestones):
        controller = WorkflowController(we)
        result = await controller.execute_milestones(run_state, str(tmp_path))

    assert result.run_id == "grp-real"
    assert result.control_state.run_id == "grp-real"
    assert result.control_state.milestone_group_id == "grp-real"

    from kriya.control.persistence import load_control_state
    persisted = load_control_state(str(tmp_path))
    assert persisted is not None
    assert persisted.milestone_group_id == "grp-real"


@pytest.mark.asyncio
async def test_execute_milestones_initial_persist_failure_fails_closed_before_execution(tmp_path):
    we = _workflow_engine()
    run_state = _milestone_run_state()
    fake_run_milestones = AsyncMock(return_value={"status": "success"})

    with patch("kriya.workflow.milestones.run_milestones", fake_run_milestones), \
         patch("kriya.workflow.workflow_controller.save_control_state", side_effect=RuntimeError("disk full")):
        controller = WorkflowController(we)
        result = await controller.execute_milestones(run_state, str(tmp_path))

    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["reason_codes"] == ["CONTROL_STATE_PERSISTENCE_FAILED"]
    fake_run_milestones.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_milestones_final_persist_failure_overrides_success(tmp_path):
    we = _workflow_engine()
    run_state = _milestone_run_state()
    fake_run_milestones = AsyncMock(return_value={"status": "success"})
    from kriya.control.persistence import save_control_state as real_save_control_state
    save_count = 0

    def fail_second_save(workspace_path, state):
        nonlocal save_count
        save_count += 1
        if save_count == 2:
            raise RuntimeError("disk full after run")
        return real_save_control_state(workspace_path, state)

    with patch("kriya.workflow.milestones.run_milestones", fake_run_milestones), \
         patch(
             "kriya.workflow.workflow_controller.save_control_state",
             side_effect=fail_second_save,
         ):
        result = await WorkflowController(we).execute_milestones(run_state, str(tmp_path))

    fake_run_milestones.assert_awaited_once()
    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["reason_codes"] == ["CONTROL_STATE_PERSISTENCE_FAILED"]


@pytest.mark.asyncio
async def test_execute_milestones_persists_initial_state_before_running_m1(tmp_path):
    we = _workflow_engine()
    milestone = MagicMock(id="M1")
    milestone.model_dump.return_value = {"id": "M1"}
    run_state = MagicMock(
        group_id="grp", original_goal="goal", milestones=[milestone],
        stale_milestone_ids=[], completed_milestone_ids=[],
    )

    async def assert_initial_state(*args, **kwargs):
        persisted = load_control_state(str(tmp_path))
        assert persisted.milestone_group_id == "grp"
        assert persisted.milestone_states == {"M1": "pending"}
        assert persisted.current_milestone_id == "M1"
        assert persisted.current_plan_hash
        return {"status": "success"}

    with patch(
        "kriya.workflow.milestones.run_milestones",
        new=AsyncMock(side_effect=assert_initial_state),
    ):
        result = await WorkflowController(we).execute_milestones(run_state, str(tmp_path))

    assert result.legacy_result["status"] == "success"


@pytest.mark.asyncio
async def test_execute_milestones_propagates_a_real_run_milestones_failure_result(tmp_path):
    """Not swallowed/reshaped - run_milestones()'s own rich failure shape
    (status=milestone_failed, etc.) passes through as-is via legacy_result,
    same as _run_legacy_generation's own passthrough for ordinary goals."""
    we = _workflow_engine()
    run_state = _milestone_run_state()
    fake_run_milestones = AsyncMock(return_value={
        "status": "milestone_failed", "milestone_id": "M2", "milestone_index": 2,
    })

    with patch("kriya.workflow.milestones.run_milestones", fake_run_milestones):
        controller = WorkflowController(we)
        result = await controller.execute_milestones(run_state, str(tmp_path))

    assert result.legacy_result["status"] == "milestone_failed"
    assert result.legacy_result["milestone_id"] == "M2"
