"""D1 (live Greenfield failure, run 20261015T152407-5f87716a): an unresolved SEMANTIC validator finding must survive a
response-shape/protocol failure.

Live sequence (MEASURED, handover/DEMO-V2/LIVE-GREENFIELD-FAILURE-ROOT-CAUSE.md): attempt 1's AppTest.java failed
`static_rule_violation [vacuous_test_assertion]`; attempt 2's reply failed `operation_contract` (CONFLICTING_DEVELOPER_RESPONSE);
attempts 3-5 were then told ONLY about the protocol error, so the model reasoned "the previous error was a malformed response"
and the still-open test defect never reached it again.

The tests drive the real recording path (`handle_attempt_failure`) and the real retry-package builder, then read what the
next repair request would carry (`RetryPackage.render_error()` / `prior_error_text()`).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import developer_double

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import AttemptContext, _retry_package_for_attempt
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

TEST_FILE = "src/test/java/hellokriya/AppTest.java"
POM = "pom.xml"
MAIN_FILE = "src/main/java/hellokriya/App.java"
VACUOUS = (
    "[vacuous_test_assertion] src/test/java/hellokriya/AppTest.java contains a constant-truth assertion ('assertTrue(true)') "
    "that passes unconditionally regardless of the actual production behavior being tested."
)
PROTOCOL = "malformed repair response: CONFLICTING_DEVELOPER_RESPONSE: more than one outcome block"
WINDOW = 32768


def _ctx(tmp_path) -> AttemptContext:
    return AttemptContext(
        goal="goal", plan="plan", design="design",
        workspace_path=str(tmp_path), worktree_path=str(tmp_path),
        architect_files=[POM, MAIN_FILE, TEST_FILE], resume_state=None, run_id="run",
        skills_prompt="", learned_rag_context="", matched_files=[], related_files=[],
        ecosystem_invariant_block="", resource_lifecycle_block="",
        verification_contract_block="", recovery_contract_block="",
        required_files_prompt_block="", required_dependencies_prompt_block="",
        expected_files_upfront=[POM, MAIN_FILE, TEST_FILE],
        architect_basename_to_path={"pom.xml": POM, "App.java": MAIN_FILE, "AppTest.java": TEST_FILE},
        chain=[], targeted_max_retries=3, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={},
        developer=developer_double(), run_verifier=AsyncMock(), spec_compliance=AsyncMock(),
        skill_engine=MagicMock(), kernel=Kernel(config=AppConfig()), max_retries=4,
        web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False),
        established_files=[],
    )


def _write_project(tmp_path):
    for path, text in ((POM, "<project/>\n"), (MAIN_FILE, "package hellokriya;\npublic class App {}\n"),
                       (TEST_FILE, "package hellokriya;\npublic class AppTest { void t() { assertTrue(true); } }\n")):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def _semantic(type_="static_rule_violation", raw=VACUOUS, files=(TEST_FILE,), **extra):
    return QualityGateFailure(Failure(
        type=type_, message=f"STATIC RULE VIOLATION: {raw}", raw_output=raw, likely_files=list(files), **extra))


def _protocol(files=(TEST_FILE,), type_="operation_contract", raw=PROTOCOL, reason="CONFLICTING_DEVELOPER_RESPONSE"):
    return QualityGateFailure(Failure(
        type=type_, message=f"OPERATION CONTRACT FAILURE in {files[0]}: {raw}", raw_output=raw, likely_files=list(files),
        diagnostics={"reason_code": reason}))


async def _record(state, ctx, *failures):
    state.last_attempt_mode = "full_set"
    with patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None):
        for exc in failures:
            state.attempt_number += 1
            await handle_attempt_failure(state, ctx, exc)


def _next_request(state, ctx, targets=(TEST_FILE,)):
    package = _retry_package_for_attempt(state, ctx, target_files=list(targets), prompt_window=WINDOW)
    assert package is not None
    return package


@pytest.mark.asyncio
async def test_live_sequence_vacuity_then_protocol_error_the_next_request_still_carries_the_vacuity(tmp_path):
    """ORIGINAL SYMPTOM: attempt 3's request carried only the protocol error. Fails on 5a42377."""
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _semantic(), _protocol())

    rendered = _next_request(state, ctx).render_error()

    assert "CONFLICTING_DEVELOPER_RESPONSE" in rendered                      # the protocol problem is still reported
    assert "vacuous_test_assertion" in rendered and TEST_FILE in rendered    # and the unresolved defect is NOT lost


@pytest.mark.asyncio
async def test_the_open_finding_is_typed_state_not_prose(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _semantic(), _protocol())

    [finding] = state.open_findings
    assert finding.failure_type == "static_rule_violation"
    assert finding.files == (TEST_FILE,)
    assert finding.source == "quality_gate"
    assert "vacuous_test_assertion" in finding.evidence
    assert state.last_failure.type == "operation_contract"                   # the attempt-local delivery error is separate


@pytest.mark.asyncio
async def test_finding_survives_several_consecutive_protocol_errors_without_growing(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _semantic(), _protocol(), _protocol(), _protocol())

    assert len(state.open_findings) == 1
    rendered = _next_request(state, ctx).render_error()
    assert rendered.count("vacuous_test_assertion") == 1                     # not repeated per protocol failure
    assert rendered.count("CONFLICTING_DEVELOPER_RESPONSE") == 1             # no history of delivery errors either


@pytest.mark.asyncio
async def test_a_different_delivery_failure_type_also_keeps_the_finding(tmp_path):
    """anchored_edit / diagnosis_mismatch (live attempts 6-8) are delivery failures too."""
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    anchored = QualityGateFailure(Failure(
        type="anchored_edit", message="ANCHORED EDIT FAILURE in pom.xml", raw_output="ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT",
        likely_files=[POM], diagnostics={"reason_code": "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"}))
    mismatch = QualityGateFailure(Failure(
        type="diagnosis_mismatch", message="DIAGNOSIS MISMATCH in pom.xml", raw_output="your analysis said ...", likely_files=[POM]))
    await _record(state, ctx, _semantic(), anchored, mismatch)

    rendered = _next_request(state, ctx, targets=(POM,)).render_error()
    assert "vacuous_test_assertion" in rendered and TEST_FILE in rendered


@pytest.mark.asyncio
async def test_a_corrected_candidate_that_fails_a_different_validator_supersedes_the_old_finding(tmp_path):
    """Clearing: the next candidate reached a later gate, so the earlier check no longer fires; the current finding replaces it."""
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    compile_failure = QualityGateFailure(Failure(
        type="compile", message="COMPILATION FAILURE", raw_output="AppTest.java:3: cannot find symbol", likely_files=[TEST_FILE]))
    await _record(state, ctx, _semantic(), compile_failure, _protocol())

    [finding] = state.open_findings
    assert finding.failure_type == "compile"
    rendered = _next_request(state, ctx).render_error()
    assert "cannot find symbol" in rendered and "vacuous_test_assertion" not in rendered


@pytest.mark.asyncio
async def test_two_independent_open_findings_both_survive_a_protocol_error(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _semantic())
    from kriya.workflow.open_findings import OpenFinding
    state.open_findings = [*state.open_findings, OpenFinding(
        failure_type="pom_semantic_validation", reason_code=None, files=(POM,), source="quality_gate",
        evidence="mvn validate: invalid POM", first_attempt=1)]
    await _record(state, ctx, _protocol())

    assert len(state.open_findings) == 2
    rendered = _next_request(state, ctx).render_error()
    assert "vacuous_test_assertion" in rendered and "invalid POM" in rendered


@pytest.mark.asyncio
async def test_a_pure_protocol_error_adds_no_phantom_finding(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _protocol())

    assert state.open_findings == []
    rendered = _next_request(state, ctx).render_error()
    assert "unresolved" not in rendered.lower()
    assert rendered.startswith("Failure type: operation_contract")


@pytest.mark.asyncio
async def test_a_new_semantic_finding_after_the_protocol_correction_is_the_current_open_set(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    run_failure = QualityGateFailure(Failure(
        type="run_verification", message="RUN FAILED", raw_output="Exception in thread main", likely_files=[MAIN_FILE]))
    await _record(state, ctx, _semantic(), _protocol(), run_failure)

    assert [f.failure_type for f in state.open_findings] == ["run_verification"]
    assert state.open_findings[0].files == (MAIN_FILE,)


@pytest.mark.asyncio
async def test_non_candidate_failures_neither_set_nor_clear_findings(tmp_path):
    """A transient/internal failure says nothing about the candidate: it must not erase the open finding."""
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _semantic(), QualityGateFailure(Failure(type="general_error", message="timeout", raw_output="timeout")))

    assert [f.failure_type for f in state.open_findings] == ["static_rule_violation"]


@pytest.mark.asyncio
async def test_advisory_failures_do_not_change_the_open_set(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    advisory = QualityGateFailure(Failure(
        type="compile", message="advisory lint", raw_output="warning only", authority="advisory", likely_files=[MAIN_FILE]))
    await _record(state, ctx, _semantic(), advisory)

    assert [f.failure_type for f in state.open_findings] == ["static_rule_violation"]


@pytest.mark.asyncio
async def test_the_finding_keeps_its_validator_owned_subject_even_when_model_text_names_another_file(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    protocol_naming_pom = _protocol(files=(POM,))
    await _record(state, ctx, _semantic(), protocol_naming_pom)

    [finding] = state.open_findings
    assert finding.files == (TEST_FILE,)


@pytest.mark.asyncio
async def test_the_prior_error_text_given_to_the_developer_agent_also_carries_the_open_finding(tmp_path):
    """`prior_error_context` (attempt._prepare_retry_context) is the second place a repair request is rendered."""
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _semantic(), _protocol())

    prior = _next_request(state, ctx).prior_error_text()
    assert "CONFLICTING_DEVELOPER_RESPONSE" in prior and "vacuous_test_assertion" in prior


@pytest.mark.asyncio
async def test_the_open_finding_text_is_bounded(tmp_path):
    _write_project(tmp_path)
    ctx, state = _ctx(tmp_path), GenerationState()
    await _record(state, ctx, _semantic(raw="[x] " + "very long evidence " * 5000), _protocol())

    assert len(state.open_findings[0].evidence) <= 2000
    assert len(_next_request(state, ctx).render_error()) < 12000


def test_best_of_n_reset_forgets_open_findings():
    from kriya.workflow.best_of_n import reset_state_for_independent_candidate
    state = GenerationState()
    from kriya.workflow.open_findings import OpenFinding
    state.open_findings = [OpenFinding("static_rule_violation", None, (TEST_FILE,), "quality_gate", "x", 1)]
    reset_state_for_independent_candidate(state)
    assert state.open_findings == []

