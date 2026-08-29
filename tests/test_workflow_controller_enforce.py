"""MA7.8: WorkflowController migration_mode="enforce" (kriya/workflow/
workflow_controller.py::_run_structured_enforce) - the first mode where
WorkflowController actually owns the real outcome. Reuses
run_generation_workflow() once per subtask (the same real pattern
kriya/workflow/milestones.py::run_milestones() already uses per milestone)
rather than reimplementing edit-application/verification/approval."""

import json
import os
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.control.persistence import load_approved_plan, load_control_state
from kriya.workflow.plan_schema import (
    AcceptanceCriterion,
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    GlobalInvariant,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
)
from kriya.workflow.plan_validation import PlanValidationResult, validate_plan
from kriya.workflow.planning_diagnostics import (
    normalized_ownership_validation_records,
    persist_planning_attempt_diagnostic,
    planning_diagnostics_path,
)
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.workflow_controller import (
    AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
    _StructuredPlanUnavailable,
    WorkflowController,
    _authoritative_planner_extension_candidates,
    _build_owner_recovery_context,
    _cross_owner_obligation_id,
    _get_or_create_cross_owner_obligation,
    _transitive_upstream_ids,
    build_authoritative_planner_request,
    build_subtask_constraint_context,
    build_subtask_goal_text,
    build_subtask_semantic_context,
    build_structured_plan_repair_prompt,
    resolve_scope_conflict_owners,
    revise_plan_for_grounded_scope_owner,
)
from kriya.workflow.workflow_types import SubtaskStatus


@pytest.fixture(autouse=True)
def _keep_mocked_controller_tests_on_their_explicit_workspace(monkeypatch):
    """Most tests mock the entire subtask workflow and isolate orchestration.

    Transactional-sandbox tests below override this with a distinct copy and
    therefore exercise the real terminal apply/discard boundary directly.
    """
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", lambda workspace: workspace,
    )


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


def test_scope_conflict_owner_resolution_accepts_unique_transitive_upstream_owner():
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="owner", description="implement behavior",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="src/Owner.py", action=FileAction.MODIFY)],
            ),
            Subtask(
                id="adapter", description="adapt integration",
                execution_method=ExecutionMethod.MODEL, depends_on=["owner"],
                planned_files=[PlannedFile(path="src/Adapter.py", action=FileAction.MODIFY)],
            ),
            Subtask(
                id="consumer", description="verify behavior",
                execution_method=ExecutionMethod.MODEL, depends_on=["adapter"],
                planned_files=[PlannedFile(path="tests/test_owner.py", action=FileAction.MODIFY)],
            ),
        ],
    )

    assert resolve_scope_conflict_owners(
        plan, ["src/Owner.py"], plan.subtask_by_id("consumer"),
    ) == {"owner": ["src/Owner.py"]}


@pytest.mark.asyncio
async def test_grounded_controller_revises_and_revalidates_service_only_scope(tmp_path):
    service = "src/main/java/example/CustomerService.java"
    controller = "src/main/java/example/CustomerController.java"
    test_file = "src/test/java/example/CustomerControllerTest.java"
    for path in (service, controller, test_file):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("class Placeholder {}\n")

    plan = EngineeringPlan(
        plan_id="scope-recovery", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="Preserve the existing endpoint architecture.")],
        subtasks=[
            Subtask(
                id="s2", description="update service behavior",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=service, action=FileAction.MODIFY)],
                provides=["customer behavior"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s3", description="compose endpoint response",
                execution_method=ExecutionMethod.MODEL, depends_on=["s2"],
                planned_files=[PlannedFile(path=controller, action=FileAction.MODIFY)],
                requires=["customer behavior"], provides=["endpoint response"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s4", description="extend response coverage",
                execution_method=ExecutionMethod.MODEL, depends_on=["s3"],
                planned_files=[PlannedFile(path=test_file, action=FileAction.MODIFY)],
                requires=["endpoint response"],
                relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )

    revised = revise_plan_for_grounded_scope_owner(
        plan, "s2", [controller], str(tmp_path),
    )
    validation = await validate_plan(
        revised,
        workspace_path=str(tmp_path),
        route=_route(ChangeKind.TASK),
        require_model_planned_files=True,
        require_semantic_contracts=True,
    )

    assert validation.valid, validation.errors
    assert [pf.path for pf in revised.subtask_by_id("s2").planned_files] == [
        service, controller,
    ]
    assert revised.subtask_by_id("s3") is None
    assert revised.subtask_by_id("s4").depends_on == ["s2"]
    assert revised.file_owner(controller).id == "s2"
    assert revised.file_owner(controller).planned_files[1].reason == (
        "deterministically grounded architectural owner"
    )


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


def test_subtask_goal_preserves_overall_constraints_and_mapped_acceptance_without_expanding_scope():
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="Create the build manifest", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            acceptance_criteria_ids=["ac1"],
        )],
        acceptance_criteria=[AcceptanceCriterion(
            id="ac1", description="All libraries required by the requested application are resolved",
        )],
    )

    rendered = build_subtask_goal_text(
        plan.subtasks[0], 1, 1,
        plan=plan,
    )
    constraints = build_subtask_constraint_context(
        "Build an application using framework X and integration Y.",
    )

    assert "All libraries required" in rendered
    assert "pom.xml (create)" in rendered
    assert "framework X and integration Y" in constraints
    assert "only its approved files" in constraints
    assert "not this stage's completion scope" in constraints


def test_subtask_semantic_context_projects_invariants_upstream_and_downstream_contracts():
    invariant = "runtime configuration remains external"
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement=invariant)],
        subtasks=[
            Subtask(
                id="config", description="write config", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="config.xml", action=FileAction.CREATE)],
                provides=["config.ready"], relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="app", description="consume config", execution_method=ExecutionMethod.MODEL,
                depends_on=["config"],
                planned_files=[PlannedFile(path="App.java", action=FileAction.CREATE)],
                requires=["config.ready"], relevant_global_invariant_ids=["gi1"],
                verification=[VerificationMethod(
                    type=VerificationMethodType.JUDGMENT, description="application consumes config",
                    requires_runtime_execution=True,
                )],
            ),
        ],
    )

    provider_context = build_subtask_semantic_context(plan, plan.subtasks[0])
    consumer_context = build_subtask_semantic_context(plan, plan.subtasks[1])
    assert '"consumer": "app"' in provider_context
    assert '"capability": "config.ready"' in provider_context
    assert '"provider": "config"' in consumer_context
    assert invariant in consumer_context
    assert "application consumes config" in consumer_context
    assert '"runtime_execution_required": true' in consumer_context


def test_plan_repair_prompt_is_json_only_and_gives_exact_unscoped_check_correction():
    prompt = build_structured_plan_repair_prompt(
        "build the requested application",
        "previous response",
        ["MODEL subtask(s) ['verify'] declare no planned_files"],
        ["MODEL_SUBTASK_MISSING_PLANNED_FILES"],
        2,
    )
    assert "Return only one complete JSON object" in prompt
    assert "Do not use Markdown or code fences" in prompt
    # PRV-05 (2026-08-28): a non-editing check subtask is now corrected by
    # setting execution_role=verification and keeping it as its own subtask
    # (see ExecutionRole's own docstring, kriya/workflow/plan_schema.py) -
    # not by removing it, which the Planner's own repeated identical plan
    # proved doesn't reflect what a real regression-verification step needs.
    assert "set its execution_role to verification" in prompt
    assert "do NOT remove it" in prompt
    assert "Never invent a fake file" in prompt


def test_plan_repair_prompt_gives_exact_schema_contract_and_extension_evidence():
    prompt = build_structured_plan_repair_prompt(
        "extend the application",
        "previous response",
        ["schema invalid", "extension point required"],
        [
            "STRUCTURED_PLAN_SCHEMA_INVALID",
            "SUBTASK_REQUIREMENT_UNPROVIDED",
            "EXTENSION_POINT_REQUIRED",
        ],
        1,
        route_kind=ChangeKind.MILESTONE,
        extension_candidates=["Existing.java"],
    )
    assert "verification item must be an object" in prompt
    assert "character-for-character" in prompt
    assert '["Existing.java"]' in prompt
    assert "Do not invent a path" in prompt


def test_plan_repair_prompt_resolves_duplicate_file_ownership_without_weakening_scope():
    prompt = build_structured_plan_repair_prompt(
        "Fix the existing formatter and add regression coverage",
        "previous response",
        ["planned file ownership must be unique: {'formatter.py': ['implementation', 'verify']}"],
        ["AMBIGUOUS_PLANNED_FILE_OWNERSHIP"],
        1,
        repository_candidates=[
            "src/CustomerDisplayNameFormatter.java",
            "tests/CustomerDisplayNameFormatterTest.java",
        ],
    )

    assert "owned by exactly one MODEL subtask" in prompt
    assert "retain it only on the subtask that actually performs" in prompt
    assert "verification or acceptance criteria" in prompt
    assert "do not rename, replace, or invent files" in prompt
    assert "'formatter.py': ['implementation', 'verify']" in prompt
    assert "REMOVE any separate MODEL" in prompt
    assert "sole purpose is to analyze, inspect, research, or explain" in prompt
    assert "src/CustomerDisplayNameFormatter.java" in prompt
    assert "For modify/delete, use an exact relevant existing path" in prompt


def test_plan_repair_prompt_directs_unknown_global_invariant_to_declared_ids(tmp_path):
    """Regression test for PRV-06 (2026-08-28): UNKNOWN_GLOBAL_INVARIANT
    used to have NO targeted correction block at all (every other reason
    code in this function does) - the model was shown the error but never
    told the fix mechanism, so it non-convergently repeated the same
    mismatch across two full bounded repair rounds live. This asserts the
    new targeted block actually reaches the model."""
    prompt = build_structured_plan_repair_prompt(
        "build the requested application",
        "previous response",
        ["subtask 's3' references unknown global invariant id(s): ['gi_ghost']; "
         "declared ids are ['gi1', 'gi2']"],
        ["UNKNOWN_GLOBAL_INVARIANT"],
        1,
    )
    assert "replace that entry with one of the declared ids" in prompt
    assert "Do not invent a new id" in prompt
    assert "do not restate the invariant's statement text as the id" in prompt
    assert "Existing global invariant ids from the previous draft must be preserved" in prompt
    assert "never restate the statement text as the id" in prompt


def test_normalized_ownership_diagnostic_exposes_duplicate_claims_without_inventing_symbols(tmp_path):
    (tmp_path / "formatter.py").write_text("def format_name(value): return value\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="implementation", description="fix formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(
                    path="formatter.py", action=FileAction.MODIFY, reason="existing implementation",
                )],
            ),
            Subtask(
                id="verification", description="verify formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(
                    path="formatter.py", action=FileAction.MODIFY, reason="run checks",
                )],
            ),
        ],
    )

    records = normalized_ownership_validation_records(plan, workspace_path=str(tmp_path))

    assert records == [{
        "planned_path": "formatter.py",
        "planned_symbol": None,
        "declared_owner": None,
        "declared_owners": ["implementation", "verification"],
        "candidate_existing_owners": ["formatter.py"],
        "repository_evidence": {
            "exact_path_exists": True,
            "ownership_discovery_performed": False,
            "claims": [
                {"subtask_id": "implementation", "action": "modify", "reason": "existing implementation"},
                {"subtask_id": "verification", "action": "modify", "reason": "run checks"},
            ],
        },
        "validator_rule": "each planned file path must be owned by exactly one subtask",
        "decision": "rejected",
        "reason": "planned path is claimed by 2 subtasks",
    }]


def test_planning_diagnostics_append_bounded_local_attempt_records(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="implementation", description="write app",
            execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="app.py", action=FileAction.CREATE)],
        )],
    )
    for attempt in range(3):
        persist_planning_attempt_diagnostic(
            str(tmp_path), "run/unsafe:id",
            attempt=attempt,
            planner_request="request",
            planner_system_prompt="system",
            raw_plan_response=f"response {attempt}",
            plan=plan,
            validation_errors=["validation error"],
            reason_codes=["REASON"],
            repository_evidence=[{"path": "app.py", "exists": False}],
            repair_prompt="repair" if attempt < 2 else None,
        )

    path = planning_diagnostics_path(str(tmp_path), "run/unsafe:id")
    records = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert [record["attempt"] for record in records] == [0, 1, 2]
    assert records[0]["raw_plan_response"] == "response 0"
    assert records[1]["repair_prompt"] == "repair"
    assert records[2]["repair_prompt"] is None
    assert records[0]["repository_evidence"] == [{"path": "app.py", "exists": False}]
    assert path.endswith("/.kriya/control/planning-diagnostics/run_unsafe_id.jsonl")


def test_authoritative_planner_request_forbids_unsupported_tool_stages_without_changing_goal():
    request = build_authoritative_planner_request("Build one runnable application.")
    assert "Original product request:\nBuild one runnable application." in request
    assert "Do not emit execution_method=tool subtasks" in request
    # PRV-05 (2026-08-28): a non-editing check now gets its own
    # execution_role=verification subtask (see ExecutionRole's own
    # docstring, kriya/workflow/plan_schema.py) rather than being folded
    # into an implementation subtask's verification/acceptance_criteria.
    assert "execution_role: implementation or verification" in request
    assert "not product requirements" in request
    assert "Return only one complete JSON object" in request
    assert "Emit no prose" in request
    assert "observable application behavior" in request
    assert "actually runs the application" in request
    assert "Never emit verification strings" in request
    assert "exactly match one provides string" in request
    assert "tool_name=compile" in request
    assert "runnable entrypoint stage" in request
    assert "Each planned_files path must be owned by exactly one implementation MODEL subtask" in request
    assert "solely to analyze, inspect, research, or explain code" in request


def test_authoritative_planner_request_carries_testability_and_tooling_dag_guidance():
    """PRV-06 (2026-08-28): two generic, cross-language planning-time
    nudges added after a real greenfield failure - (1) a stage needing
    build/test tooling from another stage's manifest must declare that as
    a real requires/depends_on edge, not rely on incidental subtask-id
    ordering (topological_subtask_order's own tie-break is "sorted id...
    not a meaningful priority" - confirmed this run's s2/s3 ordering only
    held by alphabetical luck); (2) a process-terminating entrypoint and
    the tests exercising it in-process must stay structurally separable."""
    request = build_authoritative_planner_request("Build one runnable application.")
    assert "must declare that stage's provides value in its own requires and depends_on" in request
    assert "do not rely on planned_files or list position to imply execution order" in request
    assert "keep the process-terminating call separate from the" in request
    assert "System.exit" not in request  # generic across languages, not Java-specific
    assert "run()" not in request  # no required method name/shape


def test_authoritative_planner_system_prompt_carries_testability_and_tooling_dag_guidance():
    assert "the terminating call sits in a thin wrapper" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "any process-termination mechanism" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "does not require a specific method name" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "own requires and depends_on" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT
    assert "execution order is not" in AUTHORITATIVE_PLANNER_SYSTEM_PROMPT


def test_task_planner_request_supplies_existing_paths_for_brownfield_owner_selection():
    request = build_authoritative_planner_request(
        "Fix the existing display-name formatter and update its tests",
        route_kind=ChangeKind.TASK,
        repository_candidates=[
            "src/CustomerDisplayNameFormatter.java",
            "tests/CustomerDisplayNameFormatterTest.java",
        ],
    )

    assert "Existing local workspace paths available as repository evidence" in request
    assert "src/CustomerDisplayNameFormatter.java" in request
    assert "tests/CustomerDisplayNameFormatterTest.java" in request
    assert "For modify/delete actions, select the exact relevant existing path" in request
    assert "Use action=create only" in request


def test_authoritative_planner_request_supplies_route_extension_candidates(tmp_path):
    (tmp_path / "Existing.java").write_text("class Existing {}")
    (tmp_path / "goal.md").write_text("private goal text")
    (tmp_path / "kriya.yaml").write_text("autonomy: {}")
    candidates = _authoritative_planner_extension_candidates(str(tmp_path))
    request = build_authoritative_planner_request(
        "Extend the application",
        route_kind=ChangeKind.MILESTONE,
        extension_candidates=candidates,
    )
    assert candidates == ["Existing.java"]
    assert "extension_points must name" in request
    assert '["Existing.java"]' in request
    assert "private goal text" not in request


def test_planner_repository_evidence_keeps_goal_relevant_owner_inside_bound(tmp_path):
    for index in range(110):
        (tmp_path / f"AUnrelated{index:03d}.java").write_text("class Unrelated {}")
    relevant = tmp_path / "ZCustomerDisplayNameFormatter.java"
    relevant.write_text("class ZCustomerDisplayNameFormatter {}")

    candidates = _authoritative_planner_extension_candidates(
        str(tmp_path), max_files=10,
        goal="Fix the existing customer display name formatter",
    )

    assert "ZCustomerDisplayNameFormatter.java" in candidates
    assert len(candidates) == 10


@pytest.mark.asyncio
async def test_enforce_planner_calls_use_json_only_system_contract(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py"],
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    planner_kwargs = we.planner.run.await_args.kwargs
    assert planner_kwargs["json_mode"] is True
    assert "Return exactly one valid JSON object" in planner_kwargs["system_prompt_override"]
    assert "no Markdown" in planner_kwargs["system_prompt_override"]


@pytest.mark.asyncio
async def test_enforce_classifies_schema_invalid_plan_separately_from_json_parse_failure(tmp_path):
    we = _workflow_engine()
    with patch(
        "kriya.workflow.workflow_controller.parse_planner_structured_output",
        return_value=(None, "structured plan JSON block failed schema validation: invalid verification"),
    ):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert "STRUCTURED_PLAN_SCHEMA_INVALID" in result.legacy_result["reason_codes"]
    assert "STRUCTURED_PLAN_PARSE_FAILED" not in result.legacy_result["reason_codes"]
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]


@pytest.mark.asyncio
async def test_enforce_knowledge_guard_stops_before_structured_planning(tmp_path):
    from kriya.config import AppConfig
    from kriya.tools.knowledge import GapReport

    report = GapReport()
    report.add_gap("org.example:new-lib", "9.0.0", None, "high", "after cutoff")
    we = _workflow_engine()
    config = AppConfig()
    config.paths.memory = str(tmp_path / "memory")
    config.paths.skills = str(tmp_path / "skills")
    we.kernel = MagicMock(config=config)

    with patch(
        "kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=report,
    ):
        result = await WorkflowController(we).execute(
            "Use new-lib 9.0.0", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "knowledge_gap"
    we.planner.run.assert_not_awaited()


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
async def test_enforce_canonicalizes_wrong_action_instead_of_repairing(tmp_path):
    """Regression test (PRV-05 run #10, 2026-08-28): the Planner declared
    action=modify for a file that does not exist yet - previously this
    always cost a PLANNED_FILE_ACTION_MISMATCH repair round, and the live
    incident showed the model can fail to correct it even across two full
    rounds. canonicalize_planned_file_actions() now fixes this before
    validate_plan() ever sees it, so the plan must validate on the very
    first Planner call - zero repair rounds, zero extra planner.run() calls."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a new file", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.MODIFY)],  # wrong: a.py doesn't exist yet
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(
        return_value={"status": "success", "quality_gates_passed": True, "files": []},
    )

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "success"
    assert we.planner.run.await_count == 1, "a real defect must not have required any repair round"
    # canonicalize_planned_file_actions() never mutates the Planner's own
    # object - only the corrected copy used for validation/execution.
    assert plan.subtasks[0].planned_files[0].action == FileAction.MODIFY


@pytest.mark.asyncio
async def test_enforce_reuses_one_skill_registry_and_projects_resolved_knowledge(tmp_path):
    from kriya.config import AppConfig
    from kriya.tools.knowledge import GapReport

    plan = _two_subtask_plan()
    we = _workflow_engine()
    config = AppConfig()
    config.paths.memory = str(tmp_path / "memory")
    config.paths.skills = str(tmp_path / "skills")
    we.kernel = MagicMock(config=config)
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run
    shared_engine = MagicMock()
    p1, p2, p3 = _patched(plan)
    with patch(
        "kriya.tools.knowledge.KnowledgeGuard.check_goal", return_value=GapReport(),
    ), patch(
        "kriya.skills.skill.SkillEngine.from_config", return_value=shared_engine,
    ), p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    shared_engine.discover_and_load.assert_called_once_with()
    assert calls[0]["skill_engine_override"] is shared_engine
    assert calls[1]["skill_engine_override"] is shared_engine
    assert calls[0]["resolved_knowledge_coordinates"] == []
    assert calls[1]["resolved_knowledge_coordinates"] == []


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


# --- knowledge_gap surfacing (2026-08-25, real live finding: ignite_qpid_protocol) ---

@pytest.mark.asyncio
async def test_enforce_surfaces_knowledge_gap_from_the_first_subtask_to_the_top_level(tmp_path):
    """Regression test for a real live bug, 2026-08-25 (ignite_qpid_protocol):
    subtask s1's own internal run_generation_workflow() call hit
    KnowledgeGuard's stage-0 gap check and returned status='knowledge_gap' -
    previously flattened into a generic 'did not pass Quality Gates' failure
    with the real gap_report silently discarded, so the CLI's own already-
    built confirmation UX (kriya/cli.py's `res.get('status') ==
    'knowledge_gap'` check) could never fire for enforce mode at all. The run
    produced zero files with zero visible explanation - the actual real-world
    symptom. Scoped to the SAME safety boundary _StructuredPlanUnavailable
    already uses (only when literally nothing has been established yet)."""
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run(**kwargs):
        return {
            "status": "knowledge_gap",
            "gap_report": {"gaps": [{"library": "org.apache.ignite:ignite-core", "version": "2.18.0"}]},
            "run_id": "gap-run-1",
        }

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "knowledge_gap"
    assert result.legacy_result["gap_report"] == {
        "gaps": [{"library": "org.apache.ignite:ignite-core", "version": "2.18.0"}]
    }
    assert result.legacy_result["run_id"] == "gap-run-1"
    # The real per-subtask diagnostic detail must still be there too (used by
    # the CLI's "No files written" fallback branch for any OTHER failure kind).
    assert result.legacy_result["subtask_results"][0]["error"]


@pytest.mark.asyncio
async def test_enforce_does_not_surface_knowledge_gap_from_a_later_subtask(tmp_path):
    """A knowledge gap on a LATER subtask, after real prior work already
    exists, must NOT promote to the top-level knowledge_gap status - that
    would invite the CLI's retry flow to silently re-run already-completed
    subtasks from scratch. Stays the ordinary generic failure instead."""
    plan = _two_subtask_plan()
    we = _workflow_engine()

    async def fake_run(**kwargs):
        if "'s2'" in kwargs["goal"]:
            return {"status": "knowledge_gap", "gap_report": {"gaps": []}, "run_id": "gap-run-2"}
        (tmp_path / "a.py").write_text("# a.py")
        return {"status": "success", "quality_gates_passed": True, "files": ["a.py"]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "failed"
    assert "gap_report" not in result.legacy_result


@pytest.mark.asyncio
async def test_enforce_never_forwards_trace_id_override_to_a_subtask_call(tmp_path):
    """trace_id_override (kriya/cli.py) is meant to overwrite the ONE
    transient knowledge_gap trace row a single whole-goal retry produces -
    forwarding it unfiltered to every subtask call in this loop would give
    them all the SAME trace id, and since traces.db does INSERT OR REPLACE
    keyed by run_id, each subtask's own real trace row would silently
    overwrite the previous subtask's. Every OTHER legacy kwarg must still
    pass through untouched."""
    plan = _two_subtask_plan()
    we = _workflow_engine()
    captured = []

    async def fake_run(**kwargs):
        captured.append(kwargs)
        path = "a.py" if len(captured) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute(
            "goal", str(tmp_path), migration_mode="enforce",
            trace_id_override="should-never-appear", knowledge_risk_confirmed=True,
        )

    assert len(captured) == 2
    for kwargs in captured:
        assert "trace_id_override" not in kwargs
        assert kwargs["knowledge_risk_confirmed"] is True


# --- bounded authoritative plan repair (before any implementation) ---

@pytest.mark.asyncio
async def test_enforce_repairs_late_unscoped_model_subtask_before_execution(tmp_path):
    bad_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s6", description="run tests", execution_method=ExecutionMethod.MODEL)],
    )
    good_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s6", description="write report", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="report.txt", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.planner.run = AsyncMock(side_effect=["initial plan", "corrected plan"])
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["report.txt"],
    })
    validations = [
        PlanValidationResult(
            valid=False,
            errors=["subtask 's6' uses execution_method=model but declares no planned_files"],
            reason_codes=["MODEL_SUBTASK_MISSING_PLANNED_FILES"],
        ),
        PlanValidationResult(valid=True),
    ]
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), patch(
        "kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
        side_effect=[bad_plan, good_plan],
    ), patch(
        "kriya.workflow.workflow_controller.validate_plan",
        new=AsyncMock(side_effect=validations),
    ):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert we.planner.run.await_count == 2
    repair_prompt = we.planner.run.await_args_list[1].args[0]
    assert "PLAN_REPAIR" in repair_prompt
    assert "MODEL_SUBTASK_MISSING_PLANNED_FILES" in repair_prompt
    assert "s6" in repair_prompt
    we.run_generation_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_repairs_unknown_global_invariant_reference_by_id(tmp_path):
    """Regression test for PRV-06 (2026-08-28): the exact live failure shape
    - a subtask references a global invariant id that isn't declared yet.
    Unlike the pre-fix text-matching contract, this converges in exactly one
    repair round once the model swaps in a declared id, because the
    validator and the repair-prompt guidance both operate on ids, not
    free-text reproduction."""
    compound = GlobalInvariant(
        id="gi_retrieve_print",
        statement="The application must retrieve the value from that service and print it.",
    )
    bad_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK, global_invariants=[compound],
        subtasks=[Subtask(
            id="s2", description="service", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="Service.java", action=FileAction.CREATE)],
            relevant_global_invariant_ids=["gi_ghost"],
        )],
    )
    good_plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK, global_invariants=[compound],
        subtasks=[Subtask(
            id="s2", description="service", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="Service.java", action=FileAction.CREATE)],
            relevant_global_invariant_ids=["gi_retrieve_print"],
        )],
    )
    we = _workflow_engine()
    we.planner.run = AsyncMock(side_effect=["initial plan", "corrected plan"])
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["Service.java"],
    })
    validations = [
        PlanValidationResult(
            valid=False,
            errors=["subtask 's2' references unknown global invariant id(s): ['gi_ghost']; "
                    "declared ids are ['gi_retrieve_print']"],
            reason_codes=["UNKNOWN_GLOBAL_INVARIANT"],
        ),
        PlanValidationResult(valid=True),
    ]
    with patch("kriya.workflow.workflow_controller.parse_planner_structured_output", return_value=(MagicMock(), None)), patch(
        "kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
        side_effect=[bad_plan, good_plan],
    ), patch(
        "kriya.workflow.workflow_controller.validate_plan",
        new=AsyncMock(side_effect=validations),
    ):
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert we.planner.run.await_count == 2
    repair_prompt = we.planner.run.await_args_list[1].args[0]
    assert "gi_ghost" in repair_prompt
    assert "declared ids are" in repair_prompt
    assert "replace that entry with one of the declared ids" in repair_prompt


@pytest.mark.asyncio
async def test_enforce_structured_plan_unavailable_never_degrades_to_legacy(tmp_path):
    we = _workflow_engine()
    controller = WorkflowController(we)
    controller._run_structured_enforce = AsyncMock(
        side_effect=_StructuredPlanUnavailable("planner transport unavailable")
    )
    controller._run_legacy_generation = AsyncMock(
        side_effect=AssertionError("authoritative mode must not invoke legacy generation")
    )
    result = await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["reason_codes"] == ["STRUCTURED_PLAN_UNAVAILABLE"]
    controller._run_legacy_generation.assert_not_called()


@pytest.mark.asyncio
async def test_enforce_plan_repair_exhaustion_fails_closed_without_legacy_execution(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(id="s1", description="do something", execution_method=ExecutionMethod.MODEL)],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock()
    p1, p2, p3 = _patched(
        plan,
        validation_result=PlanValidationResult(
            valid=False, errors=["missing scope"],
            reason_codes=["MODEL_SUBTASK_MISSING_PLANNED_FILES"],
        ),
    )
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["failure_type"] == "PLANNING_ERROR"
    assert result.legacy_result["recovery"] == "PLAN_REPAIR"
    assert result.legacy_result["plan_repair_attempts"] == 2
    assert "STRUCTURED_PLAN_REPAIR_EXHAUSTED" in result.legacy_result["reason_codes"]
    assert "MODEL_SUBTASK_MISSING_PLANNED_FILES" in result.legacy_result["reason_codes"]
    assert we.planner.run.await_count == 3
    we.run_generation_workflow.assert_not_awaited()
    diagnostics = [
        json.loads(line)
        for line in open(
            planning_diagnostics_path(str(tmp_path), result.run_id), encoding="utf-8",
        )
    ]
    assert [record["attempt"] for record in diagnostics] == [0, 1, 2]
    assert diagnostics[0]["repair_prompt"] is not None
    assert diagnostics[1]["repair_prompt"] is not None
    assert diagnostics[2]["repair_prompt"] is None


@pytest.mark.asyncio
async def test_enforce_surfaces_grounded_out_of_scope_repair_as_plan_revision(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="implement application", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "failed", "quality_gates_passed": False, "files": [],
        "plan_scope_conflict": {
            "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
            "failure_type": "misdirected_edit",
            "required_files": ["pom.xml"],
            "allowed_files": ["src/App.java"],
        },
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["failure_type"] == "PLANNING_ERROR"
    assert result.legacy_result["recovery"] == "PLAN_REPAIR"
    assert result.legacy_result["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"]
    assert result.subtask_results[0].status == SubtaskStatus.NEEDS_REVIEW
    assert result.subtask_results[0].reason_codes == ("PLAN_SCOPE_REVISION_REQUIRED",)


@pytest.mark.asyncio
async def test_enforce_reopens_unique_upstream_owner_and_reruns_consumer(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="framework dependencies must be resolved")],
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
                provides=["build.dependencies.ready"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s3", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
                requires=["build.dependencies.ready"],
                relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "misdirected_edit",
                    "required_files": ["pom.xml"],
                    "allowed_files": ["src/App.java"],
                },
            }
        if len(calls) == 3:
            (tmp_path / "pom.xml").write_text("<project><dependencies/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "App.java").write_text("class App {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/App.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["quality_gates_passed"] is True
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)
    assert result.legacy_result["last_subtask_result"]["quality_gates_passed"] is True
    assert len(calls) == 4
    assert calls[1]["allowed_write_relpaths"] == ["src/App.java"]
    assert calls[2]["allowed_write_relpaths"] == ["pom.xml"]
    assert calls[3]["allowed_write_relpaths"] == ["src/App.java"]
    assert result.legacy_result["plan_recovery_events"] == [{
        "failed_subtask": "s3",
        "reopened_owner": "s1",
        "required_repair_files": ["pom.xml"],
        "classification": "PLAN_SCOPE_INSUFFICIENT",
        "reason": None,
        "invalidated_subtasks": ["s3"],
        "ownership_revalidated": True,
        "revalidation_basis": "unique approved upstream file owner",
        "owner_recovery_passed": True,
        # MA8.1 completion (2026-08-29 v2): requirement-scoped identity,
        # not just the owner id - see _cross_owner_obligation_id.
        "requirement_id": "recovery.s3.s1.pom.xml.misdirected_edit.0",
        "generation": 0,
    }]


@pytest.mark.asyncio
async def test_enforce_reopens_completed_predecessor_for_authoritative_deterministic_scope_conflict_instead_of_merging(tmp_path):
    """MA8 (spec §30, 'owner is a completed predecessor'): a DETERMINISTIC-
    authority scope conflict (attribution_tier="authoritative_deterministic"
    - currently only ever produced by the migration gate's
    failure.authoritative_files, see attribution.py) naming a file a real
    UPSTREAM subtask already owns must REOPEN that predecessor, not merge/
    steal its ownership into the failing subtask - even though
    classification == PLAN_SCOPE_DEFECT would otherwise route into the
    merge path (compare test_enforce_revises_service_scope_to_grounded_
    controller_and_continues just below, whose attribution_tier=
    "architectural_owner" case is deliberately left routing through the
    unchanged merge path)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="remove old dependency", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.MODIFY)],
            ),
            Subtask(
                id="s2", description="migrate production usage", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/JsonService.java", action=FileAction.MODIFY)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "pom.xml").write_text("<project><dependencies><gson/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "classification": "PLAN_SCOPE_DEFECT",
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "migration_incomplete",
                    "required_files": ["pom.xml"],
                    "grounded_owner_files": [],
                    "attribution_tier": "authoritative_deterministic",
                    "allowed_files": ["src/JsonService.java"],
                },
            }
        if len(calls) == 3:
            (tmp_path / "pom.xml").write_text("<project><dependencies/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "JsonService.java").write_text("class JsonService {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/JsonService.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 4
    # s2 (the failing consumer) never got pom.xml merged into its own scope -
    # s1 was REOPENED and re-ran pom.xml itself.
    assert calls[1]["allowed_write_relpaths"] == ["src/JsonService.java"]
    assert calls[2]["allowed_write_relpaths"] == ["pom.xml"]
    assert calls[3]["allowed_write_relpaths"] == ["src/JsonService.java"]
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_subtasks = approved["plan"]["subtasks"]
    # Ownership must remain exactly as originally planned - pom.xml still
    # belongs to s1, NOT stripped from it and merged into s2.
    assert any(
        item["id"] == "s1" and "pom.xml" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )
    assert not any(
        item["id"] == "s2" and "pom.xml" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )


@pytest.mark.asyncio
async def test_enforce_surfaces_unrelated_owner_scope_conflict_as_plan_revision_required_instead_of_merging(tmp_path):
    """MA8 (spec §30, 'owner is unrelated/parallel'): a DETERMINISTIC-
    authority scope conflict naming a file owned by a subtask that is
    NEITHER an upstream predecessor NOR unowned (s3 here has no
    dependency relationship with the failing s2 at all) must not be
    silently merged into the failing subtask (the architecture-discovery
    path) OR silently reopened (the completed-predecessor path only ever
    looks upstream) - it must surface as an unresolved PLAN_SCOPE_
    REVISION_REQUIRED result, per the spec's own 'treat as plan
    inconsistency... do not cross-edit silently' instruction. Compare
    test_enforce_reopens_completed_predecessor_for_authoritative_
    deterministic_scope_conflict_instead_of_merging just above (the
    PAST_ORDERED case, which DOES auto-resolve) and test_enforce_revises_
    service_scope_to_grounded_controller_and_continues just below (the
    architectural_owner-tier case, deliberately unaffected by this)."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="unrelated setup", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="src/A.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="consumer", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/B.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="unrelated parallel owner", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="src/C.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "A.java").write_text("class A {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["src/A.java"]}
        return {
            "status": "failed", "quality_gates_passed": False, "files": [],
            "plan_scope_conflict": {
                "classification": "PLAN_SCOPE_DEFECT",
                "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                "failure_type": "migration_incomplete",
                "required_files": ["src/C.java"],
                "grounded_owner_files": [],
                "attribution_tier": "authoritative_deterministic",
                "allowed_files": ["src/B.java"],
            },
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    # Only s1 and s2's own (failed) call happened - no merge re-invoke, no
    # reopen re-invoke, and s3 (topologically free to run) never had to.
    assert len(calls) == 2
    assert result.legacy_result["status"] != "success"
    assert result.legacy_result.get("reason_codes") == ["PLAN_SCOPE_REVISION_REQUIRED"]
    assert result.legacy_result["plan_scope_conflict"]["required_files"] == ["src/C.java"]
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_subtasks = approved["plan"]["subtasks"]
    # Ownership must remain exactly as originally planned - src/C.java
    # still belongs to s3, never merged into s2.
    assert any(
        item["id"] == "s3" and "src/C.java" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )
    assert not any(
        item["id"] == "s2" and "src/C.java" in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )


@pytest.mark.asyncio
async def test_enforce_revises_service_scope_to_grounded_controller_and_continues(tmp_path):
    service = "src/CustomerService.java"
    controller = "src/CustomerController.java"
    test_file = "tests/CustomerControllerTest.java"
    for path in (service, controller, test_file):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("baseline\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="existing response ownership is preserved")],
        subtasks=[
            Subtask(
                id="s2", description="update service", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=service, action=FileAction.MODIFY)],
                provides=["customer behavior"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s3", description="compose response", execution_method=ExecutionMethod.MODEL,
                depends_on=["s2"],
                planned_files=[PlannedFile(path=controller, action=FileAction.MODIFY)],
                requires=["customer behavior"], provides=["response composed"],
                relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="s4", description="verify response", execution_method=ExecutionMethod.MODEL,
                depends_on=["s3"],
                planned_files=[PlannedFile(path=test_file, action=FileAction.MODIFY)],
                requires=["response composed"],
                relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "classification": "PLAN_SCOPE_DEFECT",
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "goal_spec_compliance",
                    "required_files": [controller],
                    "grounded_owner_files": [controller],
                    "attribution_tier": "architectural_owner",
                    "allowed_files": [service],
                },
            }
        for path in kwargs["allowed_write_relpaths"]:
            (tmp_path / path).write_text("modified\n")
        return {
            "status": "success", "quality_gates_passed": True,
            "files": kwargs["allowed_write_relpaths"],
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "add displayName to the existing endpoint response",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert calls[0]["allowed_write_relpaths"] == [service]
    assert calls[1]["allowed_write_relpaths"] == [service, controller]
    assert calls[1]["execution_scope"] == "subtask=s2 role=plan_scope_recovery"
    assert calls[2]["allowed_write_relpaths"] == [test_file]
    assert (tmp_path / controller).read_text() == "modified\n"
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_subtasks = approved["plan"]["subtasks"]
    assert any(
        item["id"] == "s2"
        and controller in [pf["path"] for pf in item["planned_files"]]
        for item in approved_subtasks
    )
    assert not any(item["id"] == "s3" for item in approved_subtasks)
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)


@pytest.mark.asyncio
async def test_enforce_reopens_owner_with_grounded_diagnosis_and_commits_plan_atomically(
    tmp_path, monkeypatch,
):
    source_path = "src/Formatter.java"
    test_path = "tests/FormatterTest.java"
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / source_path).write_text("incomplete\n")
    (tmp_path / test_path).write_text("test\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id="gi1", statement="output is normalized")],
        subtasks=[
            Subtask(
                id="implementation", description="fix formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=source_path, action=FileAction.MODIFY)],
                provides=["formatter.fixed"], relevant_global_invariant_ids=["gi1"],
            ),
            Subtask(
                id="tests", description="update formatter tests",
                execution_method=ExecutionMethod.MODEL, depends_on=["implementation"],
                planned_files=[PlannedFile(path=test_path, action=FileAction.MODIFY)],
                requires=["formatter.fixed"], relevant_global_invariant_ids=["gi1"],
            ),
        ],
    )
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox,
    )
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.remove_git_worktree",
        lambda workspace, candidate: shutil.rmtree(candidate),
    )
    calls = []
    diagnosis = "Whitespace-only middle names still produce duplicate spaces"

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (sandbox / source_path).write_text("still incomplete\n")
            return {"status": "success", "quality_gates_passed": True, "files": [source_path]}
        if len(calls) == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "targeted_test", "reason": diagnosis,
                    "required_files": [source_path], "allowed_files": [test_path],
                },
            }
        if len(calls) == 3:
            assert diagnosis in kwargs["supplementary_context"]
            (sandbox / source_path).write_text("complete\n")
            return {"status": "success", "quality_gates_passed": True, "files": [source_path]}
        return {"status": "success", "quality_gates_passed": True, "files": [test_path]}

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "fix formatter", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert (tmp_path / source_path).read_text() == "complete\n"
    assert not sandbox.exists()
    assert result.legacy_result["plan_recovery_events"][0]["owner_recovery_passed"] is True


@pytest.mark.asyncio
async def test_enforce_discards_successful_earlier_subtask_when_plan_fails(
    tmp_path, monkeypatch,
):
    source_path = "src/Formatter.java"
    test_path = "tests/FormatterTest.java"
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / source_path).write_text("original\n")
    (tmp_path / test_path).write_text("test\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="implementation", description="fix formatter",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=source_path, action=FileAction.MODIFY)],
            ),
            Subtask(
                id="tests", description="update tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["implementation"],
                planned_files=[PlannedFile(path=test_path, action=FileAction.MODIFY)],
            ),
        ],
    )
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox,
    )
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.remove_git_worktree",
        lambda workspace, candidate: shutil.rmtree(candidate),
    )
    calls = 0

    async def fake_run(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            (sandbox / source_path).write_text("staged change\n")
            return {"status": "success", "quality_gates_passed": True, "files": [source_path]}
        return {"status": "failed", "quality_gates_passed": False, "files": []}

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "fix formatter", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["quality_gates_passed"] is False
    assert (tmp_path / source_path).read_text() == "original\n"
    assert not sandbox.exists()
    persisted = load_control_state(str(tmp_path))
    assert persisted.subtask_states["implementation"] == "pending"
    assert persisted.subtask_written_files == {}


@pytest.mark.asyncio
async def test_enforce_terminal_commit_resolves_dependency_ordered_same_path_ownership(
    tmp_path, monkeypatch,
):
    """Regression test (2026-08-28): plan_validation.py's own
    AMBIGUOUS_PLANNED_FILE_OWNERSHIP check legitimately allows the same
    path to be owned by a dependency-ordered chain of subtasks (e.g. an
    "identify usages" stage and a later "migrate" stage both touching
    pom.xml). The terminal-commit assembly used to build one StagedFileWrite
    per (subtask, planned_file) pair, so a plan shaped exactly like this fed
    commit_revision_grounded_batch's own duplicate-target-path guard a
    genuine duplicate and crashed with BatchCommitError instead of
    completing. The fix resolves to exactly one write per unique path,
    sourced from the last owner in real dependency/execution order."""
    same_path = "pom.xml"
    (tmp_path / same_path).write_text("<project>original</project>\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="identify usages", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=same_path, action=FileAction.MODIFY)],
            ),
            Subtask(
                id="s2", description="migrate dependency", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path=same_path, action=FileAction.MODIFY)],
            ),
        ],
    )
    sandbox = tmp_path / "plan-sandbox"

    def create_plan_sandbox(workspace):
        shutil.copytree(workspace, sandbox, ignore=shutil.ignore_patterns(".kriya", "plan-sandbox"))
        return str(sandbox)

    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.create_git_worktree", create_plan_sandbox,
    )
    monkeypatch.setattr(
        "kriya.workflow.workflow_controller.remove_git_worktree",
        lambda workspace, candidate: shutil.rmtree(candidate),
    )
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            (sandbox / same_path).write_text("<project>s1 content</project>\n")
        else:
            (sandbox / same_path).write_text("<project>s2 final content</project>\n")
        return {"status": "success", "quality_gates_passed": True, "files": [same_path]}

    we = _workflow_engine()
    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "migrate dependency", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["quality_gates_passed"] is True
    assert (tmp_path / same_path).read_text() == "<project>s2 final content</project>\n"
    assert not sandbox.exists()


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
async def test_enforce_global_final_state_check_downgrades_success_when_migration_incomplete(tmp_path):
    """Regression test for PRV-05 (2026-08-28, run 5): every subtask passing
    its own LOCAL Quality Gates is not sufficient for a migration - the
    goal explicitly authorized replacing Gson with Jackson, but the final
    applied workspace still has Gson declared/used. Even though every
    per-subtask run_generation_workflow() call here is mocked to report
    unconditional success (proving this is a NEW, additional check, not a
    side effect of the per-subtask one), the global final-state check must
    still downgrade the overall result."""
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies>\n"
        "<dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId>"
        "<version>2.11.0</version></dependency>\n"
        "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId>"
        "<version>2.17.2</version></dependency>\n"
        "</dependencies></project>\n"
    )
    owner = tmp_path / "src/main/java/com/example/JsonService.java"
    owner.parent.mkdir(parents=True)
    owner.write_text(
        "package com.example;\n"
        "import com.google.gson.Gson;\n"
        "public class JsonService {\n"
        " private final Gson gson=new Gson();\n"
        " public String serialize(Object c){ return gson.toJson(c); }\n"
        "}\n"
    )
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="migrate JsonService.java to the new JSON library",
            execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="src/main/java/com/example/JsonService.java", action=FileAction.MODIFY)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        controller = WorkflowController(we)
        result = await controller.execute(
            "Replace the existing JSON serialization library with the JSON library already "
            "approved for this repository.",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert result.legacy_result["quality_gates_passed"] is False
    assert "global_migration_gap" in result.legacy_result
    assert "SOURCE_DEPENDENCY_REMAINS" in result.legacy_result["global_migration_gap"]


@pytest.mark.asyncio
async def test_enforce_global_final_state_check_fails_closed_on_its_own_internal_error(tmp_path):
    """The global final-state check is authoritative and deterministic - an
    internal bug in IT must not silently fall back to trusting the
    per-subtask gates (the same silent-degrade shape as the false-PASS bug
    this check exists to close). Must downgrade to failure, not success,
    when resolve_migration_resolution itself raises."""
    (tmp_path / "pom.xml").write_text("<project></project>\n")
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="migrate the library", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="pom.xml", action=FileAction.MODIFY)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={"status": "success", "quality_gates_passed": True, "files": []})

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, patch(
        "kriya.workflow.workflow_controller.resolve_migration_resolution",
        side_effect=RuntimeError("simulated validator bug"),
    ):
        controller = WorkflowController(we)
        result = await controller.execute(
            "Replace the existing JSON serialization library with the JSON library already "
            "approved for this repository.",
            str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] != "success"
    assert result.legacy_result["quality_gates_passed"] is False
    assert "INDETERMINATE" in result.legacy_result["global_migration_gap"]


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
async def test_enforce_artifact_derivation_failure_fails_closed(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1",
            description="write a.py",
            execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
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

    # ArtifactRegistry is authoritative in enforce mode: repository success
    # without durable physical-linkage evidence requires review.
    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["quality_gates_passed"] is False
    assert result.legacy_result["artifact_error"] == "derivation exploded"
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
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    assert approved is not None
    assert approved["plan_hash"] == plan.content_hash()
    assert approved["approval_status"] == "approved"
    assert approved["lifecycle_state"] == "completed"
    assert approved["stage_order"] == ["s1", "s2"]
    assert approved["stage_states"] == {"s1": "completed", "s2": "completed"}
    assert approved["plan"]["subtasks"][0]["planned_files"][0]["path"] == "a.py"


@pytest.mark.asyncio
async def test_enforce_persists_in_progress_stage_before_generation_call(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(side_effect=RuntimeError("process interrupted"))

    p1, p2, p3 = _patched(plan)
    with p1, p2, p3, pytest.raises(RuntimeError, match="process interrupted"):
        await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    assert approved is not None
    assert approved["lifecycle_state"] == "in_progress"
    assert approved["stage_states"] == {"s1": "in_progress"}


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
async def test_enforce_resume_refused_quarantines_abandoned_plan_files(tmp_path):
    """Regression test for a real live bug, 2026-08-25 (protocol_encoder_java):
    an abandoned plan's own completed subtask had already applied a real
    file to the workspace; a later re-plan whose resume gets refused (a
    different plan hash) no longer referenced that file at all, but nothing
    ever cleaned it up - it just sat there, orphaned, alongside the new
    plan's own output. Confirms the abandoned file is moved into
    .kriya/abandoned_plan_files/ (never deleted, never left in the active
    tree) while a file BOTH plans still reference is left untouched."""
    _init_git_repo(tmp_path)
    plan_a = _two_subtask_plan()  # s1 -> a.py, s2 -> b.py
    we = _workflow_engine()
    calls = []

    async def fake_run_a(**kwargs):
        calls.append(kwargs["goal"])
        path = "a.py" if len(calls) == 1 else "b.py"
        (tmp_path / path).write_text(f"# {path}")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run_a

    p1, p2, p3 = _patched(plan_a)
    with p1, p2, p3:
        controller = WorkflowController(we)
        await controller.execute("goal", str(tmp_path), migration_mode="enforce")
    assert (tmp_path / "b.py").exists()

    # A re-plan whose s2 writes c.py instead of b.py - a genuinely different
    # plan shape (content_hash() differs), not just an edited description.
    plan_b = EngineeringPlan(
        plan_id="run2", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="write c.py instead", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"], planned_files=[PlannedFile(path="c.py", action=FileAction.CREATE)],
            ),
        ],
    )
    assert plan_b.content_hash() != plan_a.content_hash()

    async def fake_run_b(**kwargs):
        calls.append(kwargs["goal"])
        path = "c.py" if "c.py" in kwargs["goal"] else "a.py"
        (tmp_path / path).write_text(f"# {path} v2")
        return {"status": "success", "quality_gates_passed": True, "files": [path]}

    we.run_generation_workflow = fake_run_b

    p1, p2, p3 = _patched(plan_b)
    with p1, p2, p3:
        await controller.execute("goal", str(tmp_path), migration_mode="enforce", resume=True)

    assert (tmp_path / "a.py").exists(), "still referenced by the new plan - must stay in place"
    assert (tmp_path / "c.py").exists()
    assert not (tmp_path / "b.py").exists(), "abandoned - must no longer sit in the active tree"
    quarantined = list((tmp_path / ".kriya" / "abandoned_plan_files").rglob("b.py"))
    assert len(quarantined) == 1


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
    assert calls[0]["allowed_write_relpaths"] == ["a.py"]
    assert calls[1]["predetermined_architect_files"] == ["b.py"]
    assert calls[1]["allowed_write_relpaths"] == ["b.py"]


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
async def test_enforce_requires_authoritative_subtask_verification_evidence(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            verification=[VerificationMethod(
                type=VerificationMethodType.TOOL,
                tool_name="quality_gates",
                description="compile, tests, and regression pass",
            )],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py"],
        "verification_results": [{
            "type": "tool", "tool_name": "quality_gates",
            "description": "compile, tests, and regression pass",
            "passed": True, "source": "existing_quality_gates",
        }],
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )
    assert result.legacy_result["quality_gates_passed"] is True
    assert we.run_generation_workflow.await_args.kwargs["required_verification"][0]["tool_name"] == "quality_gates"


@pytest.mark.asyncio
async def test_enforce_marks_unresolved_subtask_verification_needs_review(tmp_path):
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="write a.py", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE)],
            verification=[VerificationMethod(
                type=VerificationMethodType.JUDGMENT,
                description="human confirms the behavior",
            )],
        )],
    )
    we = _workflow_engine()
    we.run_generation_workflow = AsyncMock(return_value={
        "status": "success", "quality_gates_passed": True, "files": ["a.py"],
        "verification_results": [{
            "type": "judgment", "tool_name": None,
            "description": "human confirms the behavior",
            "passed": None, "source": "unresolved",
        }],
    })
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )
    assert result.legacy_result["quality_gates_passed"] is False
    assert result.subtask_results[0].status == SubtaskStatus.NEEDS_REVIEW
    assert result.subtask_results[0].reason_codes == ("VERIFICATION_UNRESOLVED",)


@pytest.mark.asyncio
async def test_enforce_rejects_model_subtask_without_planned_files(tmp_path):
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

    assert result.legacy_result["status"] == "needs_review"
    assert result.legacy_result["quality_gates_passed"] is False
    assert result.legacy_result["reason_codes"] == [
        "MODEL_SUBTASK_MISSING_PLANNED_FILES",
        "STRUCTURED_PLAN_REPAIR_EXHAUSTED",
        # MA8: the planner is mocked to return the identical broken plan on
        # every repair attempt, so the same obligation stays VIOLATED across
        # all 3 attempts (never oscillates through SATISFIED) - the
        # exhaustion branch correctly reports non-convergence, not
        # oscillation. Pre-existing test gap (this assertion predates MA8
        # v1's oscillation/non-convergence diagnostics), fixed while
        # verifying Batch 1 - not caused by Batch 1 itself.
        "PLAN_REPAIR_NON_CONVERGENCE",
    ]
    assert result.legacy_result["plan_repair_attempts"] == 2
    assert result.legacy_result["invalid_subtask_ids"] == ["s1"]
    assert we.planner.run.await_count == 3
    we.run_generation_workflow.assert_not_awaited()


# --- MA8.1 (PRV-06, 2026-08-29): Cross-Owner Requirement-Preserving Recovery
# - a live run proved the grounded reason for reopening an upstream owner
# (e.g. "pom.xml is missing the JUnit 5 dependency s4's tests need") was not
# surviving the handoff: the owner got reopened with a generic "preserve
# brownfield identity" framing, regenerated an equally incomplete file, and
# the same downstream failure recurred. A separate, real bug in the SAME
# incident: the "preferred" grounded-owner plan-revision path produced a
# cyclic subtask dependency graph when reopening an owner that had OTHER,
# earlier consumers upstream of the failing stage. ---

def _prv06_shaped_plan():
    """The exact shape of the live PRV-06 Hardened failure: a build
    manifest (s1) owned upstream of two parallel production subtasks (s2,
    s3), which a test-writing subtask (s4) depends on. s4 proves s1 is
    missing something it needs; s1 must be reopened WITHOUT inverting the
    s1 -> s2/s3 -> s4 dependency direction."""
    return EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="create App", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/main/java/App.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create InMemoryService", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/main/java/InMemoryService.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s4", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s2", "s3"],
                planned_files=[
                    PlannedFile(path="src/test/java/AppTest.java", action=FileAction.CREATE),
                    PlannedFile(path="src/test/java/InMemoryServiceTest.java", action=FileAction.CREATE),
                ],
            ),
        ],
    )


def test_transitive_upstream_ids_finds_everything_a_subtask_depends_on():
    plan = _prv06_shaped_plan()
    assert _transitive_upstream_ids(plan, "s4") == {"s1", "s2", "s3"}
    assert _transitive_upstream_ids(plan, "s2") == {"s1"}
    assert _transitive_upstream_ids(plan, "s1") == set()


def test_revise_plan_for_grounded_scope_owner_does_not_invert_dependency_direction(tmp_path):
    """The exact live cyclic-DAG incident, reproduced directly: reopening
    s1 (pom.xml) for the failing s4 must not redirect s1's OTHER consumers
    (s2, s3 - both upstream of s4) to depend on s4 instead - that would
    invert s1 -> s2/s3 -> s4 into a cycle. Confirmed via
    _transitive_upstream_ids: no subtask may end up (transitively)
    depending on itself."""
    plan = _prv06_shaped_plan()
    (tmp_path / "pom.xml").write_text("<project/>")

    revised = revise_plan_for_grounded_scope_owner(plan, "s4", ["pom.xml"], str(tmp_path))

    # s1 is gone (merged into s4); s2 and s3 must NOT depend on s4.
    assert revised.subtask_by_id("s1") is None
    s2 = revised.subtask_by_id("s2")
    s3 = revised.subtask_by_id("s3")
    assert "s4" not in s2.depends_on
    assert "s4" not in s3.depends_on
    # No subtask transitively depends on itself - the direct cycle check.
    for subtask in revised.subtasks:
        assert subtask.id not in _transitive_upstream_ids(revised, subtask.id)
    # Must still validate cleanly as a real plan (acyclic, well-formed).
    EngineeringPlan.model_validate(revised.model_dump(mode="json"))


def test_cross_owner_obligation_id_stable_and_scoped():
    id1 = _cross_owner_obligation_id("s4", "s1", ["pom.xml"], "compile", 0)
    id2 = _cross_owner_obligation_id("s4", "s1", ["pom.xml"], "compile", 0)
    id3 = _cross_owner_obligation_id("s4", "s2", ["pom.xml"], "compile", 0)
    assert id1 == id2
    assert id1 != id3


def test_cross_owner_obligation_id_distinguishes_failure_family_and_generation():
    """Test C-equivalent: same origin/owner/files, different failure_family
    or generation, must NOT collide - this is what lets a genuinely new
    requirement coexist with (rather than silently overwrite) an earlier
    one on the same owner+artifact."""
    base = _cross_owner_obligation_id("s4", "s3", ["MainApplication.java"], "diagnosis_mismatch", 0)
    different_family = _cross_owner_obligation_id("s4", "s3", ["MainApplication.java"], "structural_corruption", 0)
    different_generation = _cross_owner_obligation_id("s4", "s3", ["MainApplication.java"], "diagnosis_mismatch", 1)
    assert base != different_family
    assert base != different_generation
    assert different_family != different_generation


def _scope_conflict_fixture(reason="grounded reason", raw_evidence="javac error", required_files=("pom.xml",)):
    required_files = list(required_files)
    return {
        "classification": "PLAN_SCOPE_DEFECT",
        "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
        "failure_type": "compile",
        "reason": reason,
        "required_files": required_files,
        "allowed_files": [],
        "attribution_tier": "architectural_owner",
        "grounded_owner_files": required_files,
        "raw_evidence": raw_evidence,
    }


def test_get_or_create_cross_owner_obligation_creates_with_real_evidence():
    ledger = ObligationLedger()
    scope_conflict = _scope_conflict_fixture(
        reason="pom.xml is missing the JUnit 5 dependency",
        raw_evidence="package org.junit.jupiter.api does not exist",
    )

    record = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=scope_conflict, generation=0, revision=1,
    )

    assert record is not None
    assert record.kind == ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT
    assert record.status == ObligationStatus.VIOLATED
    assert record.authority == ObligationAuthority.DETERMINISTIC
    assert record.owner_subtask_id == "s1"
    assert record.repair_scope == ("pom.xml",)
    assert "pom.xml is missing the JUnit 5 dependency" in record.evidence["must_fix"][0]
    assert record.evidence["raw_evidence"] == "package org.junit.jupiter.api does not exist"
    assert record.evidence["originating_subtask_id"] == "s4"
    assert ledger.current(record.id) is record


def test_get_or_create_cross_owner_obligation_is_sticky_across_a_failed_attempt():
    """A second call for the SAME requirement (same originating subtask,
    owner, required files, failure_family, AND generation) while it is
    still VIOLATED must reuse the exact same MUST_FIX/evidence CONTENT -
    even when the current scope_conflict's own evidence is weaker/
    different (e.g. a later static-rule violation instead of the original
    compiler error) - never silently regressing the text an owner-recovery
    prompt depends on. A fresh history entry IS still recorded each time
    (Test B-equivalent: recurrence must remain countable, not invisible -
    see the function's own docstring for why)."""
    ledger = ObligationLedger()
    strong_evidence = _scope_conflict_fixture(
        reason="pom.xml is missing the JUnit 5 dependency",
        raw_evidence="package org.junit.jupiter.api does not exist",
    )
    first = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=strong_evidence, generation=0, revision=1,
    )

    weaker_evidence = _scope_conflict_fixture(
        reason="unrelated static rule violation", raw_evidence="",
    )
    second = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=weaker_evidence, generation=0, revision=2,
    )

    assert second.id == first.id
    assert second.evidence["raw_evidence"] == "package org.junit.jupiter.api does not exist"
    assert len(ledger.history(first.id)) == 2


def test_get_or_create_cross_owner_obligation_no_ledger_returns_none():
    assert _get_or_create_cross_owner_obligation(
        None, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=_scope_conflict_fixture(), generation=0, revision=1,
    ) is None


def test_build_owner_recovery_context_surfaces_obligation_not_generic_framing():
    """§28.7-equivalent for MA8.1: the reopened owner's prompt must show
    the exact grounded MUST_FIX/MUST_PRESERVE/EVIDENCE/ACCEPTANCE, never
    fall back to the old generic 'preserve brownfield owner identity'
    framing, whenever an obligation is available."""
    obligation = ObligationRecord(
        id="recovery.s4.s1.pom.xml", kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
        status=ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC,
        description="x", source="test", revision=1,
        evidence={
            "must_fix": ["provide the JUnit 5 dependency required by AppTest.java"],
            "must_preserve": ["existing project identity"],
            "raw_evidence": "package org.junit.jupiter.api does not exist",
            "acceptance_conditions": ["subtask s4 passes its own Quality Gates"],
        },
        owner_subtask_id="s1", repair_scope=("pom.xml",),
    )

    context = _build_owner_recovery_context(
        owner_id="s1", failed_subtask_id="s4", required_owner_files=["pom.xml"],
        cross_owner_obligation=obligation, scope_conflict=_scope_conflict_fixture(),
    )

    assert "MUST FIX" in context
    assert "provide the JUnit 5 dependency required by AppTest.java" in context
    assert "MUST PRESERVE" in context
    assert "existing project identity" in context
    assert "EVIDENCE" in context
    assert "package org.junit.jupiter.api does not exist" in context
    assert "ACCEPTANCE" in context
    assert "subtask s4 passes its own Quality Gates" in context


def test_build_owner_recovery_context_falls_back_without_ledger():
    context = _build_owner_recovery_context(
        owner_id="s1", failed_subtask_id="s4", required_owner_files=["pom.xml"],
        cross_owner_obligation=None,
        scope_conflict=_scope_conflict_fixture(reason="some diagnosis"),
    )
    assert "authoritative plan recovery" in context
    assert "some diagnosis" in context


@pytest.mark.asyncio
async def test_enforce_reopened_owner_receives_grounded_requirement_not_generic_framing(tmp_path):
    """End-to-end reproduction of the live PRV-06 shape (§28.2/28.7-
    equivalent): s4's compile failure grounds a requirement on s1
    (pom.xml); the reopened owner's own Developer call must receive the
    exact MUST_FIX/evidence text, not a generic framing - and the
    obligation is SATISFIED only once s4's own retry actually passes."""
    plan = _prv06_shaped_plan()
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n <= 3:
            # s1, s2, s3 all succeed cleanly on the first pass.
            for path in kwargs["allowed_write_relpaths"]:
                full = tmp_path / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text("baseline\n")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        if n == 4:
            # s4 fails - grounded evidence points at pom.xml.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _scope_conflict_fixture(
                    reason="pom.xml is missing the JUnit 5 dependency required by the generated tests",
                    raw_evidence="package org.junit.jupiter.api does not exist",
                ),
            }
        if n == 5:
            # s1 owner_recovery - this time actually fixes it.
            for path in kwargs["allowed_write_relpaths"]:
                (tmp_path / path).write_text("<project><dependencies><junit/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        # s4 consumer_retry - now passes.
        for path in ["src/test/java/AppTest.java", "src/test/java/InMemoryServiceTest.java"]:
            full = tmp_path / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("class Test {}\n")
        return {"status": "success", "quality_gates_passed": True, "files": [
            "src/test/java/AppTest.java", "src/test/java/InMemoryServiceTest.java",
        ]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6
    # The reopened owner (call index 4, s1's owner_recovery) must have
    # received the exact grounded requirement, not generic framing.
    owner_recovery_context = calls[4]["supplementary_context"]
    assert "MUST FIX" in owner_recovery_context
    assert "pom.xml is missing the JUnit 5 dependency" in owner_recovery_context
    assert "EVIDENCE" in owner_recovery_context
    assert "package org.junit.jupiter.api does not exist" in owner_recovery_context
    assert "ACTIVE CROSS-OWNER RECOVERY REQUIREMENT" in owner_recovery_context
    # And the ORIGINAL DAG must remain intact - no cyclic-DAG plan
    # revision was ever needed for this shape.
    approved = load_approved_plan(str(tmp_path), plan.plan_id)
    approved_ids = {item["id"] for item in approved["plan"]["subtasks"]}
    assert approved_ids == {"s1", "s2", "s3", "s4"}


@pytest.mark.asyncio
async def test_enforce_cross_owner_recovery_generic_non_maven_shape(tmp_path):
    """Genericity proof (mirrors MA9's own heterogeneous-artifact tests):
    the identical mechanism, with Python-flavored paths - pyproject.toml
    reopened because a downstream test subtask proves a dependency is
    missing. No Java/Maven-specific code path is exercised anywhere in
    workflow_controller.py's own logic; only the evidence/paths differ."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pyproject.toml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="create service", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="service.py", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s2"],
                planned_files=[PlannedFile(path="test_service.py", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n <= 2:
            for path in kwargs["allowed_write_relpaths"]:
                full = tmp_path / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text("baseline\n")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        if n == 3:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _scope_conflict_fixture(
                    reason="pyproject.toml is missing the pytest dependency required by the generated tests",
                    raw_evidence="ModuleNotFoundError: No module named 'pytest'",
                    required_files=["pyproject.toml"],
                ),
            }
        if n == 4:
            for path in kwargs["allowed_write_relpaths"]:
                (tmp_path / path).write_text("[tool.poetry.dependencies]\npytest = \"*\"\n")
            return {"status": "success", "quality_gates_passed": True, "files": kwargs["allowed_write_relpaths"]}
        (tmp_path / "test_service.py").write_text("def test_x(): pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["test_service.py"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 5
    owner_recovery_context = calls[3]["supplementary_context"]
    assert "MUST FIX" in owner_recovery_context
    assert "pytest dependency" in owner_recovery_context
    assert "ModuleNotFoundError" in owner_recovery_context


@pytest.mark.asyncio
async def test_enforce_permits_a_second_distinct_generation_recovery_on_the_same_owner(tmp_path):
    """MA8.1 completion, Test A: the exact live PRV-06 shape - s3's first
    recovery of s1 (pom.xml) succeeds, but s3's OWN retry then surfaces a
    SECOND, genuinely different requirement on the SAME owner+file (same
    required_files, but a different failure_type: "misdirected_edit" then
    "diagnosis_mismatch" - mirroring the real incident where both
    occurrences happened to share the same failure_type yet were still
    different requirements). A bare owner-id one-shot guard would silently
    block this second recovery; the requirement-scoped identity must
    permit it. Expected: two owner-recovery cycles run, both requirement
    ids differ (R1.id != R2.id) even though owner/originating-subtask/
    required_files are identical, and the run still finishes as an overall
    success once the second recovery actually resolves the requirement."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    def _conflict(failure_type):
        return {
            "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
            "failure_type": failure_type,
            "required_files": ["pom.xml"],
            "allowed_files": ["src/App.java"],
        }

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            # s3 attempt 1 - first requirement.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("misdirected_edit"),
            }
        if n == 3:
            # s1 owner_recovery, generation 0.
            (tmp_path / "pom.xml").write_text("<project><dependencies/></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 4:
            # s3 consumer_retry, generation 0 - passes its own gate for
            # requirement 1 but surfaces a SECOND, distinct requirement.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("diagnosis_mismatch"),
            }
        if n == 5:
            # s1 owner_recovery, generation 1 - the second, real fix.
            (tmp_path / "pom.xml").write_text("<project><dependencies><junit/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # s3 consumer_retry, generation 1 - now genuinely passes.
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "App.java").write_text("class App {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/App.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert result.legacy_result["quality_gates_passed"] is True
    assert len(calls) == 6
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    assert events[0]["generation"] == 0
    assert events[1]["generation"] == 1
    assert events[0]["requirement_id"] == "recovery.s3.s1.pom.xml.misdirected_edit.0"
    assert events[1]["requirement_id"] == "recovery.s3.s1.pom.xml.diagnosis_mismatch.1"
    # The defining claim of Test A: two genuinely different requirements
    # on the same owner get two genuinely different requirement ids.
    assert events[0]["requirement_id"] != events[1]["requirement_id"]
    assert events[0]["reopened_owner"] == events[1]["reopened_owner"] == "s1"


@pytest.mark.asyncio
async def test_enforce_bounds_recovery_attempts_per_downstream_owner_pair(tmp_path):
    """MA8.1 completion, Tests B/G: even though every completed recovery
    cycle advances the generation counter (so the requirement id keeps
    changing and never itself hits its own per-id cap), the SURROUNDING
    loop is still bounded by the existing, unchanged
    _MAX_PLAN_SCOPE_REVISION_ATTEMPTS=3 - the global recovery budget this
    module already enforced before MA8.1 ever existed. A requirement that
    keeps recurring (never actually gets fixed) must not reopen its owner
    forever."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []
    conflict = {
        "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
        "failure_type": "compile",
        "required_files": ["pom.xml"],
        "allowed_files": ["src/App.java"],
    }

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n >= 3 and n % 2 == 1:
            # every owner_recovery call (n=3,5,7) "succeeds" its own narrow
            # gate but never actually resolves the downstream requirement.
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # every s3 attempt - the initial one (n=2) and every consumer_retry
        # (n=4,6,8) - hits the same unresolved requirement, forever.
        return {
            "status": "failed", "quality_gates_passed": False, "files": [],
            "plan_scope_conflict": dict(conflict),
        }

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    # 1 (s1 initial) + 1 (s3's own first attempt) + 3 * (owner_recovery,
    # consumer_retry) = 8.
    assert len(calls) == 8
    assert result.legacy_result["status"] != "success"
    events = result.legacy_result["plan_recovery_events"]
    assert [event["generation"] for event in events] == [0, 1, 2]
    requirement_ids = {event["requirement_id"] for event in events}
    # Three distinct requirement ids (generation keeps advancing) - the
    # bound comes from the surrounding loop, not from any one id's own
    # history ever reaching the cap.
    assert len(requirement_ids) == 3


@pytest.mark.asyncio
async def test_enforce_second_owner_recovery_preserves_earlier_requirements_must_fix_text(tmp_path):
    """MA8.1 completion, Tests D/F: two DIFFERENT downstream subtasks (s3,
    s4) independently discover two DIFFERENT requirements on the SAME
    upstream owner (s1, pom.xml). Both requirements must be able to exist
    (Test D - simultaneously active, distinct ids scoped by originating
    subtask as well as failure_family/generation), and the SECOND owner
    recovery's own prompt must carry forward the FIRST requirement's
    MUST_FIX text as a MUST_PRESERVE entry (Test F / invariant 6 - "a
    later recovery of an owner must preserve corrections established by
    earlier active recovery requirements")."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s3", description="create App", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="src/App.java", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s4", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1", "s3"],
                planned_files=[PlannedFile(path="src/AppTest.java", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            # s1 initial.
            (tmp_path / "pom.xml").write_text("<project/>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            # s3 attempt 1 - first requirement, grounded on JUnit.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "reason": "pom.xml is missing the JUnit 5 dependency required by App.java",
                    "raw_evidence": "package org.junit.jupiter.api does not exist",
                    "required_files": ["pom.xml"],
                    "allowed_files": ["src/App.java"],
                },
            }
        if n == 3:
            # s1 owner_recovery for s3's requirement - fixes it for real.
            (tmp_path / "pom.xml").write_text("<project><dependencies><junit/></dependencies></project>")
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 4:
            # s3 consumer_retry - passes.
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "App.java").write_text("class App {}")
            return {"status": "success", "quality_gates_passed": True, "files": ["src/App.java"]}
        if n == 5:
            # s4 attempt 1 - a DIFFERENT requirement, grounded on Jackson.
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": {
                    "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                    "failure_type": "compile",
                    "reason": "pom.xml is missing the Jackson dependency required by AppTest.java",
                    "raw_evidence": "package com.fasterxml.jackson.databind does not exist",
                    "required_files": ["pom.xml"],
                    "allowed_files": ["src/AppTest.java"],
                },
            }
        if n == 6:
            # s1 owner_recovery for s4's requirement.
            (tmp_path / "pom.xml").write_text(
                "<project><dependencies><junit/><jackson/></dependencies></project>",
            )
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        # s4 consumer_retry - passes.
        (tmp_path / "src" / "AppTest.java").write_text("class AppTest {}")
        return {"status": "success", "quality_gates_passed": True, "files": ["src/AppTest.java"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 7
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    # Test D: two simultaneously-active requirements on the same owner,
    # from two different origins, get two distinct ids.
    assert events[0]["requirement_id"] == "recovery.s3.s1.pom.xml.compile.0"
    assert events[1]["requirement_id"] == "recovery.s4.s1.pom.xml.compile.0"
    assert events[0]["requirement_id"] != events[1]["requirement_id"]
    # Test F: the SECOND owner-recovery prompt (call index 5) must carry
    # forward the FIRST requirement's own MUST_FIX text as something to
    # preserve, not just its own new one.
    second_owner_recovery_context = calls[5]["supplementary_context"]
    assert "MUST FIX" in second_owner_recovery_context
    assert "Jackson dependency" in second_owner_recovery_context
    assert "MUST PRESERVE" in second_owner_recovery_context
    assert "JUnit 5 dependency required by App.java" in second_owner_recovery_context


def test_get_or_create_cross_owner_obligation_satisfying_one_generation_leaves_another_untouched():
    """MA8.1 completion, Test E: marking one requirement's obligation
    SATISFIED (the real acceptance signal is always the ORIGINATING
    subtask's own consumer_retry passing - see workflow_controller.py's
    own comment where this exact ObligationRecord construction lives)
    must not incidentally satisfy a DIFFERENT requirement that happens to
    share the same owner+artifact - satisfaction is per requirement id,
    never per owner."""
    ledger = ObligationLedger()
    first = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s3", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=_scope_conflict_fixture(reason="needs junit"),
        generation=0, revision=1,
    )
    second = _get_or_create_cross_owner_obligation(
        ledger, originating_subtask_id="s4", owner_subtask_id="s1",
        required_files=["pom.xml"], scope_conflict=_scope_conflict_fixture(reason="needs jackson"),
        generation=0, revision=1,
    )
    assert first.id != second.id

    # s3's own retry passes - only s3's requirement is satisfied.
    ledger.record(ObligationRecord(
        id=first.id, kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
        status=ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC,
        description=first.description, source="test", revision=2,
        evidence=first.evidence, owner_subtask_id=first.owner_subtask_id,
        terminal_required=False, repair_scope=first.repair_scope,
    ))

    assert ledger.current(first.id).status == ObligationStatus.SATISFIED
    assert ledger.current(second.id).status == ObligationStatus.VIOLATED


@pytest.mark.asyncio
async def test_enforce_generic_non_maven_shape_permits_a_second_distinct_recovery(tmp_path):
    """MA8.1 completion, Test H: the genericity proof for the two-
    requirement shape (mirrors test_enforce_cross_owner_recovery_generic_
    non_maven_shape, extended to a second, distinct recovery cycle) -
    nothing about requirement-scoped identity or the bounded loop is
    Java/Maven-specific."""
    plan = EngineeringPlan(
        plan_id="run1", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pyproject.toml", action=FileAction.CREATE)],
            ),
            Subtask(
                id="s2", description="create tests", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"],
                planned_files=[PlannedFile(path="test_service.py", action=FileAction.CREATE)],
            ),
        ],
    )
    we = _workflow_engine()
    calls = []

    def _conflict(failure_type):
        return {
            "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
            "failure_type": failure_type,
            "required_files": ["pyproject.toml"],
            "allowed_files": ["test_service.py"],
        }

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            (tmp_path / "pyproject.toml").write_text("[project]\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        if n == 2:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("missing_dependency"),
            }
        if n == 3:
            (tmp_path / "pyproject.toml").write_text("[project]\ndependencies = ['pytest']\n")
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        if n == 4:
            return {
                "status": "failed", "quality_gates_passed": False, "files": [],
                "plan_scope_conflict": _conflict("import_error"),
            }
        if n == 5:
            (tmp_path / "pyproject.toml").write_text(
                "[project]\ndependencies = ['pytest', 'requests']\n",
            )
            return {"status": "success", "quality_gates_passed": True, "files": ["pyproject.toml"]}
        (tmp_path / "test_service.py").write_text("def test_x(): pass\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["test_service.py"]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        )

    assert result.legacy_result["status"] == "success"
    assert len(calls) == 6
    events = result.legacy_result["plan_recovery_events"]
    assert len(events) == 2
    assert events[0]["requirement_id"] != events[1]["requirement_id"]
    assert events[0]["generation"] == 0
    assert events[1]["generation"] == 1
