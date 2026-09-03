"""PRV-17 Stage Contract Architecture Hardening (2026-09-03) - one
production-path integration test proving CURRENT/FUTURE stage ownership is
now AUTHORITATIVE across Spec Compliance, verification routing, and
recovery admission - not just present as data (planned_files/acceptance_
criteria_ids/relevant_global_invariant_ids/requires-provides) that each gate
could freely ignore, the gap "Run 5" exposed live.

Uses the real production functions this session's fixes touched:
  - kriya/workflow/attempt.py::_stage_scoped_spec_compliance_goal()
    (Spec Compliance stage projection, now cross-checked against pending
    peer subtasks, not just the current subtask's own claim)
  - kriya/workflow/attempt.py::_spec_requirements_naming_planner_only_
    identifiers() / _extract_requirement_identifier_tokens() (Planner-
    strategy authority isolation, now covering dotted manifest filenames
    and version specifiers, not just code-shaped identifiers)
  - kriya/workflow/attempt.py's run-verification advisory-only suppression
    (ctx.runtime_verification_required gates real execution, not just the
    "required but declined" failure path)
  - kriya/workflow/retry_strategy.py::handle_attempt_failure()'s new
    NO_AUTHORIZED_REPAIR_TARGET admission gate (a grounded, ALLOWLIST-scope
    -exhausted failure stops before another Developer call)
  - kriya/workflow/static_checks.py::derive_stack_contract() (negation-aware
    family detection, fixed earlier this same session)

Zero LLM calls - every judgment (SpecComplianceAgent.check, RunVerifierAgent
.judge/.grade, DeveloperAgent.run_generation) is a deterministic AsyncMock.
Deliberately NOT a rerun of the PRV harness (scenarios/PRV-17/ under
kriya-live-validation) - see tests/test_prv17_preflight.py's own docstring
for why a fixed-input production-path test is a different, repeatable,
zero-cost question from "did a live local model converge."

Do not redesign: MA8 authority hierarchy, MA9 recovery model, FUTURE_OWNER
semantics, StackContract, write authorization, global retry budgets. Every
fix this test exercises reuses an existing mechanism/field; none of them
were touched here."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER, PLANNED_IMPLEMENTATION_SECTION_HEADER
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.attempt import (
    AttemptContext,
    _spec_requirements_naming_planner_only_identifiers,
    _stage_scoped_spec_compliance_goal,
    run_attempt,
)
from kriya.workflow.attribution import AttributionResult
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    GlobalInvariant,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
)
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState
from kriya.workflow.static_checks import derive_stack_contract
from kriya.workflow.triage import ChangeKind

# The real scenarios/PRV-17/goal.md text, verbatim - not a paraphrase.
_ACTUAL_PRV17_GOAL = (
    "Create a Python 3.12 Django application.\n\n"
    "Requirements:\n"
    "- Use Django.\n"
    "- Use Python packaging conventions.\n"
    "- Provide one Django project and one application named `customers`.\n"
    "- Expose a `/customers/health` HTTP endpoint returning JSON:\n"
    '  {"status": "ok"}\n'
    "- Add an automated test for the endpoint.\n"
    "- Do not use Java, Spring, Maven, Gradle, Node.js, or another web framework.\n"
)

_HEALTH_INVARIANT_ID = "gi_health_endpoint"
_HEALTH_INVARIANT_TEXT = "The application exposes GET /customers/health returning {\"status\": \"ok\"}"


def _stage_contract_plan() -> EngineeringPlan:
    """s1: manage.py + dependency manifest -> s2: Django project package/
    settings/urls -> s3: customers application -> s4: endpoint/tests/
    integration. gi_health_endpoint is correctly owned by s4 alone - s1
    does NOT (mistakenly) also claim it, matching the "Run 5" incident
    shape where an earlier subtask's own relevant_global_invariant_ids
    listed a final-application invariant it could never satisfy yet."""
    return EngineeringPlan(
        plan_id="prv17-stage-contract", kind=ChangeKind.TASK,
        global_invariants=[GlobalInvariant(id=_HEALTH_INVARIANT_ID, statement=_HEALTH_INVARIANT_TEXT)],
        subtasks=[
            Subtask(
                id="s1",
                description=(
                    "Scaffold manage.py and use Python packaging conventions: declare "
                    "dependencies in a requirements.txt file, pinning python>=3.12."
                ),
                execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="manage.py", action=FileAction.CREATE),
                    PlannedFile(path="requirements.txt", action=FileAction.CREATE),
                ],
                provides=["project_scaffold"],
            ),
            Subtask(
                id="s2", description="create the Django project package (settings/urls)",
                execution_method=ExecutionMethod.MODEL, depends_on=["s1"],
                planned_files=[
                    PlannedFile(path="customers_project/settings.py", action=FileAction.CREATE),
                    PlannedFile(path="customers_project/urls.py", action=FileAction.CREATE),
                ],
                requires=["project_scaffold"], provides=["django_project_package"],
            ),
            Subtask(
                id="s3", description="create the customers application skeleton",
                execution_method=ExecutionMethod.MODEL, depends_on=["s2"],
                planned_files=[PlannedFile(path="customers/apps.py", action=FileAction.CREATE)],
                requires=["django_project_package"], provides=["customers_app"],
            ),
            Subtask(
                id="s4", description="implement and test the /customers/health endpoint",
                execution_method=ExecutionMethod.MODEL, depends_on=["s3"],
                planned_files=[
                    PlannedFile(path="customers/views.py", action=FileAction.CREATE),
                    PlannedFile(path="customers/tests.py", action=FileAction.CREATE),
                ],
                requires=["customers_app"], relevant_global_invariant_ids=[_HEALTH_INVARIANT_ID],
            ),
        ],
    )


def _default_run_verifier() -> AsyncMock:
    """A cleanly no-op run-verifier: should_run=False, so run_attempt()'s
    run-verification gate no-ops and moves on - matches tests/test_workflow
    .py's own _minimal_attempt_ctx default. Callers that actually want to
    exercise run-verification pass their own configured run_verifier."""
    verifier = AsyncMock()
    verifier.judge = AsyncMock(return_value={
        "should_run": False, "run_commands": [], "command_source": "inferred", "success_criteria": "",
    })
    verifier.grade = AsyncMock(return_value={
        "passed": False, "reasoning": "Runtime verification was not requested by this test.", "likely_files": [],
    })
    return verifier


def _kernel_with_spec_compliance_enabled() -> Kernel:
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    return Kernel(config=cfg)


def _ctx(tmp_path, plan, subtask_id, *, developer, spec_compliance=None, run_verifier=None, **overrides):
    subtask = plan.subtask_by_id(subtask_id)
    files = [pf.path for pf in subtask.planned_files]
    defaults = dict(
        goal=(
            f"{AUTHORITATIVE_GOAL_SECTION_HEADER}\n"
            "Create a Python 3.12 Django application. Use Django. Use Python packaging "
            "conventions. Provide one Django project and one application named customers. "
            "Expose a /customers/health HTTP endpoint returning JSON {\"status\": \"ok\"}. "
            "Add an automated test for the endpoint. Do not use Java, Spring, Maven, Gradle, "
            "Node.js, or another web framework.\n\n"
            f"{PLANNED_IMPLEMENTATION_SECTION_HEADER}\n"
            f"{subtask.description}"
        ),
        plan="Step 1: build the plan above", design=f"Design: {subtask.description}",
        workspace_path=str(tmp_path), worktree_path=str(tmp_path),
        architect_files=files, resume_state=None, run_id="prv17-stage-contract-run",
        skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="",
        verification_contract_block="", recovery_contract_block="",
        required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=files,
        architect_basename_to_path={f.split("/")[-1]: f for f in files},
        chain=[], targeted_max_retries=3, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={},
        developer=developer,
        run_verifier=run_verifier or _default_run_verifier(),
        spec_compliance=spec_compliance or AsyncMock(check=AsyncMock(return_value={
            "compliant": True, "status": "ok", "reasoning": "no concrete requirement due yet",
            "missing_requirements": [], "likely_files": [],
        })),
        skill_engine=MagicMock(), kernel=_kernel_with_spec_compliance_enabled(),
        max_retries=4, web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False),
        allowed_write_relpaths=files, write_scope_mode=WriteScopeMode.ALLOWLIST,
        structured_plan=plan, current_subtask_id=subtask_id,
        runtime_verification_required=False,
    )
    defaults.update(overrides)
    return AttemptContext(**defaults)


@pytest.mark.asyncio
async def test_prv17_stage_contract_architecture(tmp_path):
    plan = _stage_contract_plan()

    # --- 1. s1 Spec Compliance does not require future project/app files,
    # and (3) FUTURE obligations remain PENDING during s1 - the health
    # endpoint invariant (owned by s4) never reaches SpecComplianceAgent's
    # own prompt for s1, and is reported as pending, not violated. ---
    scoped_s1, pending_s1 = _stage_scoped_spec_compliance_goal(
        _ctx(tmp_path, plan, "s1", developer=AsyncMock())
    )
    assert "/customers/health" not in scoped_s1
    assert "status" not in scoped_s1
    assert pending_s1 == [_HEALTH_INVARIANT_ID]

    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
    (tmp_path / "requirements.txt").write_text("Django>=5.0\n")
    s1_developer = AsyncMock()
    s1_developer.run_generation = AsyncMock(return_value=[
        {"filepath": "manage.py", "content": "#!/usr/bin/env python\n"},
        {"filepath": "requirements.txt", "content": "Django>=5.0\n"},
    ])
    s1_spec_compliance = AsyncMock()
    s1_spec_compliance.check = AsyncMock(return_value={
        "compliant": True, "status": "ok",
        "reasoning": "no concrete requirement due at this stage", "missing_requirements": [], "likely_files": [],
    })
    s1_state = GenerationState()
    s1_state.attempt_number = 0
    s1_state.all_files_written = set()
    s1_ctx = _ctx(tmp_path, plan, "s1", developer=s1_developer, spec_compliance=s1_spec_compliance)
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""},
    ):
        await run_attempt(s1_state, s1_ctx)  # must NOT raise
    s1_spec_compliance.check.assert_awaited()
    assert "/customers/health" not in s1_spec_compliance.check.await_args.kwargs["goal"]
    assert s1_state.candidate_gates_succeeded is True

    # --- 2. Python 3.12 does not become "python>=3.12" in pip dependency
    # requirements through acceptance logic - a Planner-authored
    # missing_requirement in exactly that shape is suppressed as
    # planner-only, never enforced against the authoritative goal. ---
    kept, planner_only = _spec_requirements_naming_planner_only_identifiers(
        ["requirements.txt must contain python>=3.12"], s1_ctx.goal,
    )
    assert kept == []
    assert planner_only == ["requirements.txt must contain python>=3.12"]

    # --- 4. A failure requiring a future/out-of-scope artifact (settings.py,
    # owned by s2) causes ZERO Developer recovery calls for s1 - the
    # recovery-admission gate stops before another Developer call rather
    # than paying for an unwinnable FULL_SET retry. ---
    recovery_state = GenerationState()
    recovery_state.attempt_number = 1
    recovery_state.last_attempt_mode = "full_set"
    recovery_state.all_files_written = {"manage.py", "requirements.txt"}
    recovery_developer = AsyncMock()
    recovery_developer.run_generation = AsyncMock(
        side_effect=AssertionError("no Developer call is legal once every implicated file is future-owned"),
    )
    recovery_ctx = _ctx(tmp_path, plan, "s1", developer=recovery_developer)
    out_of_scope_failure = QualityGateFailure(Failure(
        type="test",
        message="ImproperlyConfigured: ROOT_URLCONF is not defined",
        likely_files=["customers_project/settings.py", "customers_project/urls.py"],
    ))
    grounded_future_owned = AttributionResult(
        tier="judge", files=["customers_project/settings.py", "customers_project/urls.py"],
        confidence="medium", reasoning="both files plausibly relate to the settings misconfiguration",
    )
    with patch(
        "kriya.workflow.retry_strategy.attribute_failure", AsyncMock(return_value=grounded_future_owned),
    ):
        should_break = await handle_attempt_failure(recovery_state, recovery_ctx, out_of_scope_failure)
    assert should_break is True
    assert recovery_state.environment_failure is not None
    assert "NO_AUTHORIZED_REPAIR_TARGET" in recovery_state.environment_failure
    assert not recovery_developer.run_generation.called

    # --- Regression: a genuine CURRENT semantic violation (a real syntax
    # defect in s1's OWN authorized manage.py) must still invoke recovery
    # normally - the admission gate must never suppress a real, fixable
    # failure. ---
    current_violation_state = GenerationState()
    current_violation_state.attempt_number = 1
    current_violation_state.last_attempt_mode = "full_set"
    current_violation_state.all_files_written = {"manage.py", "requirements.txt"}
    current_violation_ctx = _ctx(tmp_path, plan, "s1", developer=AsyncMock())
    current_violation_failure = QualityGateFailure(Failure(
        type="test", message="SyntaxError: invalid syntax in manage.py", likely_files=["manage.py"],
    ))
    grounded_current = AttributionResult(
        tier="locator", files=["manage.py"], confidence="high", reasoning="the traceback names this file directly",
    )
    with patch(
        "kriya.workflow.retry_strategy.attribute_failure", AsyncMock(return_value=grounded_current),
    ):
        should_break_current = await handle_attempt_failure(
            current_violation_state, current_violation_ctx, current_violation_failure,
        )
    assert should_break_current is False
    assert current_violation_state.environment_failure is None
    assert current_violation_state.last_implicated_files == ["manage.py"]

    # --- 5. A later owner (s4) CAN satisfy the obligation once it actually
    # runs - the health-endpoint invariant IS due for s4, and a compliant
    # verdict passes candidate gates normally. ---
    scoped_s4, pending_s4 = _stage_scoped_spec_compliance_goal(
        _ctx(tmp_path, plan, "s4", developer=AsyncMock(), completed_subtask_ids=frozenset(["s1", "s2", "s3"]))
    )
    assert "/customers/health" in scoped_s4
    assert pending_s4 == []

    (tmp_path / "customers").mkdir(exist_ok=True)
    (tmp_path / "customers/views.py").write_text(
        "from django.http import JsonResponse\n"
        "def health(request):\n"
        "    return JsonResponse({\"status\": \"ok\"})\n"
    )
    (tmp_path / "customers/tests.py").write_text(
        "from django.test import TestCase, Client\n"
        "class HealthTests(TestCase):\n"
        "    def test_health(self):\n"
        "        self.assertEqual(Client().get('/customers/health').json(), {\"status\": \"ok\"})\n"
    )
    s4_developer = AsyncMock()
    s4_developer.run_generation = AsyncMock(return_value=[
        {"filepath": "customers/views.py", "content": (tmp_path / "customers/views.py").read_text()},
        {"filepath": "customers/tests.py", "content": (tmp_path / "customers/tests.py").read_text()},
    ])
    s4_spec_compliance = AsyncMock()
    s4_spec_compliance.check = AsyncMock(return_value={
        "compliant": True, "status": "ok",
        "reasoning": "the health endpoint returns the required JSON shape",
        "missing_requirements": [], "likely_files": [],
    })
    s4_state = GenerationState()
    s4_state.attempt_number = 0
    s4_state.all_files_written = set()
    s4_ctx = _ctx(
        tmp_path, plan, "s4", developer=s4_developer, spec_compliance=s4_spec_compliance,
        completed_subtask_ids=frozenset(["s1", "s2", "s3"]),
    )
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""},
    ), patch(
        "kriya.tools.validate.PolymorphicValidator.run_tests", return_value={"success": True, "output": ""},
    ):
        await run_attempt(s4_state, s4_ctx)  # must NOT raise
    assert s4_state.candidate_gates_succeeded is True

    # --- 6. Terminal verification still requires the complete application:
    # s4's own plan DOES declare the runtime obligation
    # (runtime_verification_required=True) - the advisory-only suppression
    # added for intermediate subtasks (s1-s3) never applies here, and the
    # inferred runtime check is executed and graded for real. ---
    s6_run_verifier = AsyncMock()
    s6_run_verifier.judge = AsyncMock(return_value={
        "should_run": True, "run_commands": [["python", "manage.py", "runserver"]],
        "command_source": "inferred", "success_criteria": "GET /customers/health returns status ok",
    })
    s6_run_verifier.grade = AsyncMock(return_value={
        "passed": True, "reasoning": "returned {\"status\": \"ok\"}", "likely_files": [],
    })
    s6_state = GenerationState()
    s6_state.attempt_number = 0
    s6_state.all_files_written = {"customers/views.py", "customers/tests.py"}
    s6_ctx = _ctx(
        tmp_path, plan, "s4", developer=AsyncMock(), run_verifier=s6_run_verifier,
        completed_subtask_ids=frozenset(["s1", "s2", "s3"]), runtime_verification_required=True,
    )
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_app_sequence",
        return_value={"success": True, "timed_out": False, "returncode": 0, "output": "ok", "steps": []},
    ):
        await run_attempt(s6_state, s6_ctx)  # must not raise
    s6_run_verifier.judge.assert_awaited_once()

    # --- 7. The actual PRV-17 goal text still resolves to a Python/Django
    # StackContract - untouched by any fix in this round (negation-aware
    # family detection was fixed and verified separately, tests/
    # test_stack_drift.py). ---
    contract = derive_stack_contract(
        "Create a Python 3.12 Django application with a customers app and a "
        "/customers/health endpoint."
    )
    assert contract is not None
    assert contract.languages == ("python",)
    assert contract.frameworks == ("django",)


def _health_test_verification() -> VerificationMethod:
    return VerificationMethod(
        type=VerificationMethodType.TOOL, description="run customers.tests", tool_name="test",
    )


@pytest.mark.asyncio
async def test_prv17_verification_prerequisite_closure(tmp_path):
    """PRV-17 Verification Prerequisite Closure (2026-09-03): Run 6's exact
    shape - a validated plan scheduled `python manage.py test
    customers.tests` (a TEST-kind verification) with no current/past-
    ordered subtask planning a Python dependency manifest capable of
    provisioning Django, and the plan passed validate_plan() anyway. Uses
    the REAL scenarios/PRV-17/goal.md text (_ACTUAL_PRV17_GOAL, not a
    paraphrase) to derive the StackContract validate_plan() is actually
    called with in production (workflow_controller.py passes derive_stack_
    contract(goal) at every real call site)."""
    contract = derive_stack_contract(_ACTUAL_PRV17_GOAL)
    assert contract is not None
    assert contract.languages == ("python",)
    assert contract.frameworks == ("django",)

    # --- Negative plan: source files -> Django-dependent verification ->
    # no dependency-manifest/provider anywhere - must be rejected before
    # Developer execution ever starts. ---
    negative_scaffold = Subtask(
        id="s1", description="scaffold the Django project", execution_method=ExecutionMethod.MODEL,
        planned_files=[
            PlannedFile(path="manage.py", action=FileAction.CREATE),
            PlannedFile(path="customers/views.py", action=FileAction.CREATE),
        ],
    )
    negative_tests = Subtask(
        id="s2", description="test the customers app", execution_method=ExecutionMethod.MODEL,
        depends_on=["s1"],
        planned_files=[PlannedFile(path="customers/tests.py", action=FileAction.CREATE)],
        verification=[_health_test_verification()],
    )
    negative_plan = EngineeringPlan(
        plan_id="prv17-verification-negative", kind=ChangeKind.TASK,
        subtasks=[negative_scaffold, negative_tests],
    )
    negative_result = await validate_plan(
        negative_plan, workspace_path=str(tmp_path), stack_contract=contract,
    )
    assert negative_result.valid is False
    assert "VERIFICATION_PREREQUISITE_MANIFEST_MISSING" in negative_result.reason_codes

    # --- Positive plan: dependency manifest/provider -> source files ->
    # Django-dependent verification - the manifest-planning subtask is
    # ordered ahead of the verification consumer. Must pass. ---
    positive_scaffold = Subtask(
        id="s1", description="scaffold and declare dependencies", execution_method=ExecutionMethod.MODEL,
        planned_files=[
            PlannedFile(path="manage.py", action=FileAction.CREATE),
            PlannedFile(path="requirements.txt", action=FileAction.CREATE),
        ],
        provides=["project_scaffold"],
    )
    positive_app = Subtask(
        id="s2", description="implement the customers app", execution_method=ExecutionMethod.MODEL,
        depends_on=["s1"], requires=["project_scaffold"],
        planned_files=[PlannedFile(path="customers/views.py", action=FileAction.CREATE)],
    )
    positive_tests = Subtask(
        id="s3", description="test the customers app", execution_method=ExecutionMethod.MODEL,
        depends_on=["s2"],
        planned_files=[PlannedFile(path="customers/tests.py", action=FileAction.CREATE)],
        verification=[_health_test_verification()],
    )
    positive_plan = EngineeringPlan(
        plan_id="prv17-verification-positive", kind=ChangeKind.TASK,
        subtasks=[positive_scaffold, positive_app, positive_tests],
    )
    positive_result = await validate_plan(
        positive_plan, workspace_path=str(tmp_path), stack_contract=contract,
    )
    assert positive_result.valid is True
    assert "VERIFICATION_PREREQUISITE_MANIFEST_MISSING" not in positive_result.reason_codes

    # --- An already-established environment dependency (a real
    # requirements.txt already present in the workspace) satisfies the
    # invariant without any NEW planned manifest at all. ---
    established_workspace = tmp_path / "established"
    established_workspace.mkdir()
    (established_workspace / "requirements.txt").write_text("Django>=5.0\n")
    established_plan = EngineeringPlan(
        plan_id="prv17-verification-established", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="test the customers app", execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="customers/views.py", action=FileAction.CREATE),
                    PlannedFile(path="customers/tests.py", action=FileAction.CREATE),
                ],
                verification=[_health_test_verification()],
            ),
        ],
    )
    established_result = await validate_plan(
        established_plan, workspace_path=str(established_workspace), stack_contract=contract,
    )
    assert established_result.valid is True

    # --- Ordinary verification with no external dependency (the goal names
    # no framework) remains completely unaffected. ---
    plain_contract = derive_stack_contract("Write a Python calculator module with unit tests.")
    assert plain_contract is not None and plain_contract.frameworks == ()
    plain_plan = EngineeringPlan(
        plan_id="prv17-verification-plain", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="write and test a calculator", execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="calculator.py", action=FileAction.CREATE),
                    PlannedFile(path="test_calculator.py", action=FileAction.CREATE),
                ],
                verification=[_health_test_verification()],
            ),
        ],
    )
    plain_result = await validate_plan(
        plain_plan, workspace_path=str(tmp_path), stack_contract=plain_contract,
    )
    assert plain_result.valid is True

    # --- Existing requires -> provides ordering behavior remains green:
    # the positive plan above already exercises a real requires/provides
    # edge (s2 requires "project_scaffold", provided by s1, in depends_on) -
    # confirm it was actually checked, not merely absent from errors. ---
    assert "project_scaffold" in positive_app.requires
    assert "s1" in positive_app.depends_on
