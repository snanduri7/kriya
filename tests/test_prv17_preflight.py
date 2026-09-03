"""PRV-17 ("Fresh Repository Stack Drift") deterministic preflight - proves
the goal.md scenario's real shape (a fresh Python 3.12 Django project,
`customers` app, `/customers/health` endpoint, "never Java/Spring/Maven/
Gradle/Node") through Kriya's own already-existing, unmodified production
components: plan schema/validation, planned-artifact normalization, subtask
ownership/order, allowed write scope, candidate authorization,
verification-kind resolution, CURRENT/FUTURE obligation handling, and
StackContract validation - end to end, with ZERO LLM calls (the Developer
call in section 7 is a deterministic AsyncMock, not a live model) and no
external Codex CLI. Deliberately NOT a rerun of the PRV harness itself (see
scenarios/PRV-17/ under kriya-live-validation) - this proves the pipeline
handles the scenario's shape correctly using fixed, known inputs, which is
a different (and repeatable, zero-cost) question from "did a live local
model's own output happen to converge."

One test function, seven numbered sections - each proves exactly one of
the seven behaviors this preflight was commissioned to check (2026-09-03,
following the two live runs that surfaced the path-canonicalization,
directory-artifact, and unrecoverable-scope-denial defects this same
session fixed in kriya/policy/filesystem.py, kriya/workflow/plan_schema.py,
and kriya/workflow/retry_strategy.py)."""
import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.policy.filesystem import WriteScopeMode
from kriya.policy.errors import PolicyDeniedError
from kriya.workflow.attempt import AttemptContext, run_attempt
from kriya.workflow.attribution import resolve_future_owner_verification_deferral
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
)
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState as _GenerationState
from kriya.workflow.static_checks import derive_stack_contract, validate_stack_contract_artifacts
from kriya.workflow.triage import ChangeKind
from kriya.workflow.verification_authority import deterministic_verification_kind


def _minimal_ctx(tmp_path, **overrides) -> AttemptContext:
    """A deliberately self-contained AttemptContext builder (no
    WorkflowEngine, no Planner/Architect/Graph RAG, no worktree) - mirrors
    tests/test_workflow.py's own `_minimal_attempt_ctx` fixture (the one
    PRV-18's real production-path tests already use) at a smaller scope,
    so this preflight file has no import-time dependency on that other
    (large, unrelated) test module."""
    defaults = dict(
        goal="Implement the customers health endpoint",
        plan="Step 1: write it", design="Design: one file",
        workspace_path=str(tmp_path), worktree_path=str(tmp_path),
        architect_files=["customers/views.py", "customers/urls.py"],
        resume_state=None, run_id="prv17-preflight-run",
        skills_prompt="", learned_rag_context="",
        matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="",
        verification_contract_block="", recovery_contract_block="",
        required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=["customers/views.py", "customers/urls.py"],
        architect_basename_to_path={"views.py": "customers/views.py", "urls.py": "customers/urls.py"},
        chain=[], targeted_max_retries=3,
        stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={},
        developer=AsyncMock(), run_verifier=AsyncMock(), spec_compliance=AsyncMock(),
        skill_engine=MagicMock(), kernel=Kernel(config=AppConfig()),
        max_retries=4, web_lookup_query_callback=None,
        approve_web_lookup=AsyncMock(return_value=False),
    )
    defaults.update(overrides)
    return AttemptContext(**defaults)

_PRV17_GOAL = (
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

# derive_stack_contract's own _goal_declared_family (kriya/workflow/
# static_checks.py) is a plain keyword match with no negation-awareness -
# confirmed by direct call: _PRV17_GOAL's own "Do not use Java, Spring,
# Maven, Gradle, Node.js" sentence matches the "java" and "node" families
# just as strongly as "Use Django" matches "python", so THREE families
# match and derive_stack_contract(_PRV17_GOAL) returns None (its own
# documented behavior for an ambiguous goal - "not this check's business
# to referee"). This is a real, pre-existing StackContract limitation this
# preflight surfaces, not something this fix touches (explicitly out of
# scope: "do not modify StackContract design") - sections 2/5/6 below
# instead derive the contract from this positive-only paraphrase of the
# SAME goal's affirmative requirement, through the identical, unmodified
# derive_stack_contract() function.
_PRV17_GOAL_POSITIVE_ONLY = (
    "Create a Python 3.12 Django application with a customers app and a "
    "/customers/health endpoint."
)

_DJANGO_PLANNED_FILES = [
    "manage.py",
    "customers_project/__init__.py",
    "customers_project/settings.py",
    "customers_project/urls.py",
    "customers_project/wsgi.py",
    "customers/__init__.py",
    "customers/apps.py",
    "customers/views.py",
    "customers/urls.py",
    "customers/tests.py",
]


def _django_scaffold_plan() -> EngineeringPlan:
    """The PRV-17 goal's real shape: scaffold -> health endpoint -> test,
    matching the plan the live run actually produced (customers_project/ as
    a bare planned_files entry alongside the real files nested under it -
    see section 1/2 below for why that specific shape is now rejected)."""
    return EngineeringPlan(
        plan_id="prv17-preflight", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="scaffold the Django project",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="manage.py", action=FileAction.CREATE),
                    PlannedFile(path="customers_project/__init__.py", action=FileAction.CREATE),
                    PlannedFile(path="customers_project/settings.py", action=FileAction.CREATE),
                    PlannedFile(path="customers_project/urls.py", action=FileAction.CREATE),
                    PlannedFile(path="customers_project/wsgi.py", action=FileAction.CREATE),
                ],
                provides=["customers_project_scaffold"],
            ),
            Subtask(
                id="s2", description="implement the customers health endpoint",
                execution_method=ExecutionMethod.MODEL, depends_on=["s1"],
                planned_files=[
                    PlannedFile(path="customers/__init__.py", action=FileAction.CREATE),
                    PlannedFile(path="customers/apps.py", action=FileAction.CREATE),
                    PlannedFile(path="customers/views.py", action=FileAction.CREATE),
                    PlannedFile(path="customers/urls.py", action=FileAction.CREATE),
                ],
                requires=["customers_project_scaffold"],
                provides=["customers_health_endpoint"],
            ),
            Subtask(
                id="s3", description="test the customers health endpoint",
                execution_method=ExecutionMethod.MODEL, depends_on=["s2"],
                planned_files=[PlannedFile(path="customers/tests.py", action=FileAction.CREATE)],
                requires=["customers_health_endpoint"],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_prv17_preflight_pipeline(tmp_path):
    django_contract = derive_stack_contract(_PRV17_GOAL_POSITIVE_ONLY)
    assert django_contract is not None
    assert django_contract.languages == ("python",)
    assert django_contract.frameworks == ("django",)
    # The full, real goal text (with its own negative constraint sentence)
    # is documented as unable to derive a contract at all - see
    # _PRV17_GOAL_POSITIVE_ONLY's own comment above.
    assert derive_stack_contract(_PRV17_GOAL) is None

    # --- 1. A directory-shaped planned file ("customers_project/", the
    # live run's own literal Planner output) is rejected - at the earliest
    # possible point, plan-schema construction, before a plan naming it can
    # even be built. See test_plan_schema.py for the focused unit coverage;
    # this proves it's still the first thing this pipeline does. ---
    with pytest.raises(ValidationError):
        PlannedFile(path="customers_project/", action=FileAction.CREATE)

    # --- 2. The real, valid plan (nested files only, no directory entries)
    # normalizes and validates cleanly - planned artifact normalization,
    # subtask ownership/order (s1 -> s2 -> s3, a real dependency chain), and
    # StackContract validation (item 5: a Python/Django candidate satisfies
    # a Python/Django-requesting goal) all pass through the SAME validate_
    # plan() call production uses. ---
    plan = _django_scaffold_plan()
    assert sorted(pf.path for st in plan.subtasks for pf in st.planned_files) == sorted(_DJANGO_PLANNED_FILES)
    result = await validate_plan(plan, workspace_path=str(tmp_path), stack_contract=django_contract)
    assert result.valid is True, result.errors
    assert result.errors == []
    assert validate_stack_contract_artifacts(django_contract, _DJANGO_PLANNED_FILES) is None

    # --- 3. A failure discovered while an EARLIER subtask (s1) is running,
    # whose evidence locates to a file (customers/tests.py) owned by a
    # LATER subtask (s3) whose own unmet requirement is satisfied by
    # ANOTHER not-yet-run subtask (s2, also later than s1) - the CURRENT/
    # FUTURE obligation handling this preflight is meant to prove does NOT
    # fail s1 for something only a future subtask can resolve. Uses the
    # SAME "Syntax error in X.py line N:" locator shape PolymorphicValidator
    # itself emits for a real Python syntax failure (kriya/workflow/
    # failure_grounding.py's extract_error_source_locations). ---
    deferral = resolve_future_owner_verification_deferral(
        plan, current_subtask_id="s1",
        raw_failure_text="Syntax error in customers/tests.py line 8: invalid syntax",
        completed_subtask_ids=frozenset(),
    )
    assert deferral is not None
    assert deferral.verification_subtask_id == "s3"
    assert deferral.evidence_path == "customers/tests.py"
    assert deferral.future_owner_id == "s2"
    assert deferral.required_capability == "customers_health_endpoint"

    # --- 4. Verification-kind resolution: the goal's own test command
    # (`python -m django test customers.tests`) resolves deterministically
    # as TEST - a process-status-authoritative gate, not an
    # APPLICATION_RUNTIME candidate that would need semantic/judge grading
    # for something as ordinary as a passing test run. ---
    assert deterministic_verification_kind(
        ["python", "-m", "django", "test", "customers.tests"]
    ) == "test"
    # The real discriminator is the module subcommand, not the framework
    # name - runserver stays unclassified (application execution, not a
    # deterministic test gate), matching this scenario's own "the app must
    # still be a real runnable Django service, not just a test-only stub".
    assert deterministic_verification_kind(["python", "-m", "django", "runserver"]) is None

    # --- 6. A Java/Spring implementation substitution of the SAME
    # Django-requesting goal is rejected by the identical StackContract the
    # real plan validated against above (item 5's positive control). ---
    java_substitution_plan = EngineeringPlan(
        plan_id="prv17-preflight-java-substitution", kind=ChangeKind.TASK,
        subtasks=[
            Subtask(
                id="s1", description="scaffold a Spring Boot project instead",
                execution_method=ExecutionMethod.MODEL,
                planned_files=[
                    PlannedFile(path="pom.xml", action=FileAction.CREATE),
                    PlannedFile(path="src/main/java/com/example/customers/CustomersApplication.java", action=FileAction.CREATE),
                    PlannedFile(path="src/main/java/com/example/customers/CustomersController.java", action=FileAction.CREATE),
                ],
            ),
        ],
    )
    java_result = await validate_plan(
        java_substitution_plan, workspace_path=str(tmp_path), stack_contract=django_contract,
    )
    assert java_result.valid is False
    assert "AUTHORITATIVE_STACK_SUBSTITUTION" in java_result.reason_codes
    assert validate_stack_contract_artifacts(
        django_contract, ["pom.xml", "src/main/java/com/example/customers/CustomersApplication.java"],
    ) is not None

    # --- 7. Candidate authorization: a Developer-generated write outside
    # the validated subtask's allowed write scope, naming a target that
    # doesn't exist on disk (no real owner to hand recovery off to via
    # ownership/recovery semantics), stops the retry loop before another
    # Developer/LLM invocation. Same production entry points (run_attempt/
    # handle_attempt_failure) and PRV-18's own "real candidate staged
    # through the real attempt-processing path" style as test_workflow.py's
    # test_run_attempt_rejects_mixed_batch_with_unauthorized_target_under_
    # allowlist - proven here for s2 of the SAME plan validated above,
    # authorized only for its own customers/views.py + customers/urls.py. ---
    s2_scope = ["customers/views.py", "customers/urls.py"]
    developer = AsyncMock()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": "customers/tests.py", "content": "# should never be authorized here\n"},
    ])
    ctx = _minimal_ctx(
        tmp_path, developer=developer,
        allowed_write_relpaths=s2_scope, write_scope_mode=WriteScopeMode.ALLOWLIST,
    )
    state = _GenerationState()
    state.attempt_number = 0
    state.all_files_written = set()

    with pytest.raises(PolicyDeniedError) as exc_info:
        await run_attempt(state, ctx)
    assert exc_info.value.result.reason_code == "FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"
    assert not (tmp_path / "customers" / "tests.py").exists()

    # The retry loop's own decision, given that exact denial as the failure
    # that just ended the attempt: stop now (should_break True), record
    # exactly one unrecoverable-scope-denial (not a repeat), and never
    # reclassify it as an environment/toolchain problem - so no further
    # Developer/LLM call is ever made for this subtask.
    should_break = await handle_attempt_failure(state, ctx, exc_info.value)
    assert should_break is True
    assert state.unrecoverable_scope_denial_count == 1
    assert state.environment_failure is not None
    assert "UNAUTHORIZED_GENERATION_TARGET" in state.environment_failure
    assert state.plan_scope_conflict is None
    assert developer.run_generation.await_count == 1
