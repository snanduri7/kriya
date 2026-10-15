"""D2 (live Greenfield failure, run 20261015T152407-5f87716a): model narrative must not override a validator-owned subject.

Live (MEASURED, seq 167): attempt 5's `static_rule_violation [vacuous_test_assertion]` names src/test/java/hellokriya/AppTest.java.
The attempt's own FIX ANALYSIS, written about a different problem, mentioned "the pom.xml excerpt", and the self-diagnosis tier
(attribution.py tier 0) re-targeted the repair to pom.xml (`attribution_tier: self_diagnosis`, `likely_files: [pom.xml]`).
Attempt 6 then repaired the wrong file.

The existing authority model (attribution.py): `Failure.authoritative_files` - deterministic evidence - outranks the model's
self-diagnosis, which outranks locator/judge. The static-rule raise site never set it. It now does, from the exact repository
paths the check itself printed in its violation message.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import developer_double

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import AttemptContext
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.failure_grounding import build_static_rule_failure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

TEST_FILE = "src/test/java/hellokriya/AppTest.java"
POM = "pom.xml"
MAIN_FILE = "src/main/java/hellokriya/App.java"
KNOWN = [MAIN_FILE, POM, TEST_FILE]
VACUOUS = (
    "[vacuous_test_assertion] src/test/java/hellokriya/AppTest.java contains a constant-truth assertion ('assertTrue(true)') "
    "that passes unconditionally regardless of the actual production behavior being tested."
)


def _ctx(tmp_path) -> AttemptContext:
    return AttemptContext(
        goal="goal", plan="plan", design="design",
        workspace_path=str(tmp_path), worktree_path=str(tmp_path),
        architect_files=KNOWN, resume_state=None, run_id="run",
        skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="",
        verification_contract_block="", recovery_contract_block="",
        required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=KNOWN, architect_basename_to_path={"pom.xml": POM, "App.java": MAIN_FILE, "AppTest.java": TEST_FILE},
        chain=[], targeted_max_retries=3, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={},
        developer=developer_double(), run_verifier=AsyncMock(), spec_compliance=AsyncMock(),
        skill_engine=MagicMock(), kernel=Kernel(config=AppConfig()), max_retries=4,
        web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False),
        established_files=[],
    )


def _write_project(tmp_path):
    for path in KNOWN:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// " + path + "\n")


def _static_failure(tmp_path, violation=VACUOUS, known=KNOWN):
    return build_static_rule_failure(violation, str(tmp_path), known, 1)


async def _repeat_with_self_diagnosis(state, ctx, make_failure, diagnosed):
    """The failure recurs; the model's own analysis from the preceding attempt (state.last_self_diagnosis) names ``diagnosed``."""
    state.last_attempt_mode = "full_set"
    with patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None):
        state.attempt_number = 1
        await handle_attempt_failure(state, ctx, QualityGateFailure(make_failure()))
        state.attempt_number = 2
        state.last_self_diagnosis = (state.budgets.last_failure_signature, list(diagnosed), state.attempt_number)
        await handle_attempt_failure(state, ctx, QualityGateFailure(make_failure()))
    return state.last_implicated_files


@pytest.mark.asyncio
async def test_live_sequence_model_text_naming_pom_does_not_redirect_a_finding_about_apptest(tmp_path):
    """ORIGINAL SYMPTOM (MEASURED seq 167): target became pom.xml. Fails on d8c2986 (static failure carries no authority)."""
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()

    targets = await _repeat_with_self_diagnosis(state, ctx, lambda: _static_failure(tmp_path), [POM])

    assert targets == [TEST_FILE]


@pytest.mark.asyncio
async def test_validator_naming_pom_is_not_redirected_to_main_by_model_text(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    violation = "[some_rule] pom.xml declares a dependency the project does not use."

    targets = await _repeat_with_self_diagnosis(state, ctx, lambda: _static_failure(tmp_path, violation), [MAIN_FILE])

    assert targets == [POM]


@pytest.mark.asyncio
async def test_a_cross_file_subject_named_by_the_validator_is_preserved_whole(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    violation = "[some_rule] src/main/java/hellokriya/App.java uses a type declared by neither pom.xml nor src/test/java/hellokriya/AppTest.java."

    targets = await _repeat_with_self_diagnosis(state, ctx, lambda: _static_failure(tmp_path, violation), [POM])

    assert sorted(targets) == sorted(KNOWN)


@pytest.mark.asyncio
async def test_model_text_naming_the_same_file_or_nothing_changes_nothing(tmp_path):
    _write_project(tmp_path)
    for diagnosed in ([TEST_FILE], []):
        ctx, state = _ctx(tmp_path), GenerationState()
        targets = await _repeat_with_self_diagnosis(state, ctx, lambda: _static_failure(tmp_path), diagnosed)
        assert targets == [TEST_FILE]


@pytest.mark.asyncio
async def test_model_text_naming_several_files_cannot_widen_or_move_the_subject(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()

    targets = await _repeat_with_self_diagnosis(state, ctx, lambda: _static_failure(tmp_path), [POM, MAIN_FILE])

    assert targets == [TEST_FILE]


@pytest.mark.asyncio
async def test_without_validator_attribution_the_existing_self_diagnosis_behaviour_is_unchanged(tmp_path):
    """A failure that names no known file has no authoritative subject: the self-diagnosis tier still applies (not widened)."""
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()

    def failure():
        return Failure(type="run_verification", message="RUN FAILED", raw_output="the program printed nothing")

    targets = await _repeat_with_self_diagnosis(state, ctx, failure, [POM])

    assert targets == [POM]


@pytest.mark.asyncio
async def test_a_later_deterministic_finding_with_its_own_authority_may_move_the_target(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    state.last_attempt_mode = "full_set"
    with patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None):
        state.attempt_number = 1
        await handle_attempt_failure(state, ctx, QualityGateFailure(_static_failure(tmp_path)))
        assert state.last_implicated_files == [TEST_FILE]
        state.attempt_number = 2
        await handle_attempt_failure(state, ctx, QualityGateFailure(Failure(
            type="migration_incomplete", message="MIGRATION INCOMPLETE", raw_output="dependency still declared",
            likely_files=[POM], authoritative_files=[POM])))

    assert state.last_implicated_files == [POM]


def test_the_static_failure_gets_authority_only_from_exact_known_paths_it_printed(tmp_path):
    _write_project(tmp_path)

    named = _static_failure(tmp_path)
    assert named.authoritative_files == [TEST_FILE]
    assert named.likely_files == [TEST_FILE]

    unknown_file = _static_failure(tmp_path, "[some_rule] other/Missing.java is wrong.")
    assert unknown_file.authoritative_files == []                                  # an unknown path is not evidence

    prose_only = _static_failure(tmp_path, "[some_rule] the build file mentions a pom elsewhere.")
    assert prose_only.authoritative_files == []                                     # a basename-like word is not a path match

    nested = _static_failure(tmp_path, "[some_rule] module/pom.xml is wrong.", known=[POM, "module/pom.xml"])
    assert nested.authoritative_files == ["module/pom.xml"]                         # 'pom.xml' inside 'module/pom.xml' is not the root pom


def test_model_text_can_never_manufacture_authority_for_a_failure_type_with_no_exact_path(tmp_path):
    _write_project(tmp_path)
    failure = Failure(type="run_verification", message="m", raw_output="analysis says pom.xml is the cause", likely_files=[POM])
    assert failure.authoritative_files == []


@pytest.mark.asyncio
async def test_run_attempt_production_path_records_the_vacuous_test_file_as_the_validator_subject(tmp_path):
    """The real raise site (attempt.run_attempt), not the builder alone: Demo 00's attempt-1 candidate."""
    from test_workflow import _minimal_attempt_ctx  # noqa: PLC0415 - heavy module, only this test needs it

    from kriya.workflow.attempt import run_attempt  # noqa: PLC0415

    developer = developer_double()
    developer.run_generation = AsyncMock(return_value=[
        {"filepath": POM, "content": "<project></project>\n"},
        {"filepath": MAIN_FILE, "content": 'package hellokriya;\npublic class App { public static void main(String[] a) { System.out.println("Hello from Kriya"); } }\n'},
        {"filepath": TEST_FILE, "content": "package hellokriya;\npublic class AppTest { public void testApp() { assertTrue(true); } }\n"},
    ])
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, architect_files=[POM, MAIN_FILE, TEST_FILE], expected_files_upfront=[POM, MAIN_FILE, TEST_FILE],
        architect_basename_to_path={"pom.xml": POM, "App.java": MAIN_FILE, "AppTest.java": TEST_FILE},
    )
    state = GenerationState()

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check"), \
         patch("kriya.tools.validate.PolymorphicValidator.run_pom_validate", return_value={"success": True, "output": ""}):
        with pytest.raises(QualityGateFailure) as exc_info:
            await run_attempt(state, ctx)

    failure = exc_info.value.failure
    assert failure.type == "static_rule_violation" and "vacuous_test_assertion" in failure.message
    assert failure.authoritative_files == [TEST_FILE]
    assert state.gate_outcomes[-1]["authoritative_files"] == [TEST_FILE]


# --- review of D2: a finding spanning an in-scope and an out-of-scope file must not abort the unit -----------------------------------

@pytest.mark.asyncio
async def test_a_static_finding_spanning_an_in_scope_and_an_out_of_scope_file_retries_the_in_scope_file(tmp_path):
    from kriya.policy.filesystem import WriteScopeMode  # noqa: PLC0415

    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    ctx.write_scope_mode, ctx.allowed_write_relpaths = WriteScopeMode.ALLOWLIST, [TEST_FILE]
    violation = f"[some_rule] {TEST_FILE} and {POM} are inconsistent."
    failure = build_static_rule_failure(violation, str(tmp_path), KNOWN, 1, writable_paths=[TEST_FILE])
    assert failure.authoritative_files == [TEST_FILE]

    state.last_attempt_mode, state.attempt_number = "full_set", 1
    with patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None):
        stopped = await handle_attempt_failure(state, ctx, QualityGateFailure(failure))

    assert stopped is False and not state.plan_scope_conflict and state.last_implicated_files == [TEST_FILE]


def test_when_no_printed_path_is_writable_all_are_kept_and_the_existing_scope_handling_decides(tmp_path):
    violation = f"[some_rule] {POM} is wrong."
    assert build_static_rule_failure(violation, str(tmp_path), KNOWN, 1, writable_paths=[TEST_FILE]).authoritative_files == [POM]
