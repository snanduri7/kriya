"""NON-CANDIDATE-FAILURE-SCOPED-RESET-001 (Scenario-A run 20261113T235143-0762989b, unit s6; owner ruling 2026-11-14).

MEASURED in run 10 and reproduced here without a model: attempt 5 failed on the same assertion as attempt 2 with the targeted
budget closed and the one fallback-targeted repair used. Attempt 6 was refused before inference (``context_budget_unsatisfiable``,
a RESOURCE failure that says nothing about the candidate). It left the assertion finding open, yet it was counted as a genuinely
new failure family: the targeted count went 4 -> 0 and ``fallback_targeted_attempted`` True -> False, so the same blocker was
given a fresh scoped allowance (attempts 8 and 9 ran targeted again).

Root owner: retry_strategy._record_attempt_failure inherited the active family only for delivery types and advisory failures,
a narrower rule than the one open_findings applies to keep a finding open. Both now read open_findings.replaces_open_finding.

The tests drive the REAL policy (decide_for_state / decide_attempt_mode) and the REAL recording path (handle_attempt_failure);
only the attempt outcomes are scripted, in run 10's order. The gate texts are run 10's own decisive lines.

NOT claimed here (RETRY-BLOCKER-IDENTITY-AUTHORITY-001, open): that two authoritative candidate failures in the same gate are
or are not the same blocker. The failure family is text-derived and is not blocker authority.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import developer_double

import kriya.workflow.retry_strategy as retry_strategy
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import AttemptContext
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.open_findings import replaces_open_finding
from kriya.workflow.retry_policy import RetryAction, decide_attempt_mode, decide_for_state
from kriya.workflow.state import GenerationState

TEST_FILE = "src/test/java/com/example/ProtocolParserTest.java"
MAX_RETRIES, TARGETED_MAX = 4, 3   # run 10: one fallback in the chain -> max(4, 1 + 1); development_loop TARGETED_MAX_RETRIES

_UNREPORTED = "unreported exception java.io.UnsupportedEncodingException; must be caught or declared to be thrown"
COMPILE = "\n".join([
    "[INFO] --- compiler:3.11.0:testCompile (default-testCompile) @ ignite-protocol-app ---",
    "[ERROR] COMPILATION ERROR : ",
    f"[ERROR] /kriya/workspace/{TEST_FILE}:[12,40] {_UNREPORTED}",
    f"[ERROR] /kriya/workspace/{TEST_FILE}:[59,40] {_UNREPORTED}",
    f"[ERROR] /kriya/workspace/{TEST_FILE}:[117,38] {_UNREPORTED}",
    "[INFO] 3 errors ",
    "[INFO] BUILD FAILURE",
    "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.11.0:testCompile (default-testCompile) "
    "on project ignite-protocol-app: Compilation failure: Compilation failure: ",
    "[ERROR] -> [Help 1]",
])


def _assertion(line: int) -> str:
    return "\n".join([
        "[INFO] Running com.example.ProtocolParserTest",
        "[ERROR] Tests run: 6, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.070 s <<< FAILURE! -- in com.example.ProtocolParserTest",
        "[ERROR] com.example.ProtocolParserTest.testEncodeDecodeWithLongBody -- Time elapsed: 0.003 s <<< FAILURE!",
        "org.opentest4j.AssertionFailedError: expected: <9999999999> but was: <1410065407>",
        "\tat org.junit.jupiter.api.AssertEquals.assertEquals(AssertEquals.java:161)",
        "\tat org.junit.jupiter.api.Assertions.assertEquals(Assertions.java:629)",
        f"\tat com.example.ProtocolParserTest.testEncodeDecodeWithLongBody(ProtocolParserTest.java:{line})",
        "",
        "[ERROR] Failures: ",
        f"[ERROR]   ProtocolParserTest.testEncodeDecodeWithLongBody:{line} expected: <9999999999> but was: <1410065407>",
        "[ERROR] Tests run: 6, Failures: 1, Errors: 0, Skipped: 0",
        "[INFO] BUILD FAILURE",
    ])


def _gate(raw, lines):
    return QualityGateFailure(Failure(
        type="targeted_test", message=raw[-300:], raw_output=raw, likely_files=[TEST_FILE],
        file_locations=[FileLocation(TEST_FILE, line) for line in lines]))


def _delivery(type_="operation_contract", reason="INVALID_EDIT_PROTOCOL"):
    raw = f"malformed repair response: {reason}"
    return QualityGateFailure(Failure(
        type=type_, message=raw, raw_output=raw, likely_files=[TEST_FILE], file_locations=[FileLocation(TEST_FILE)],
        diagnostics={"reason_code": reason}))


def _orchestrator(type_="context_budget_unsatisfiable", reason="CONTEXT_BUDGET_UNSATISFIABLE"):
    """The fields recovery_coordinator.classify_attempt_exception gives a refusal before inference."""
    raw = f"{reason}: estimated 32327 prompt tokens cannot fit the 32768 window with the minimum output"
    return QualityGateFailure(Failure(
        type=type_, message=raw, raw_output=raw, source="orchestrator", diagnostics={"reason_code": reason}))


def _advisory():
    """An advisory statement with its own, never-seen text: only its authority keeps it from being a new family."""
    raw = "java.lang.IllegalStateException: the parser was closed before decode"
    return QualityGateFailure(Failure(
        type="targeted_test", message=raw, raw_output=raw, authority="advisory", likely_files=[TEST_FILE]))


# (failure, the bytes the attempt left in the unit's file, or None when nothing was applied) - run 10, s6, attempts 1-5.
RUN10_TO_ATTEMPT_5 = [
    (lambda: _gate(COMPILE, (12, 59, 117)), "v1"),
    (lambda: _gate(_assertion(75), (75,)), "v2 throws"),
    (lambda: _delivery("operation_contract", "INVALID_EDIT_PROTOCOL"), None),
    (lambda: _delivery("anchored_edit", "ANCHOR_NOT_IN_FILE"), None),
    (lambda: _gate(_assertion(75), (75,)), "v3 charsets"),
]
RUN10_ATTEMPTS_6_TO_8 = [
    (_orchestrator, None),
    (lambda: _delivery("operation_contract", "OUTPUT_TRUNCATED"), None),
    (lambda: _gate(_assertion(76), (76,)), "v4 import"),
]


def _ctx(tmp_path) -> AttemptContext:
    return AttemptContext(
        goal="goal", plan="plan", design="design", workspace_path=str(tmp_path), worktree_path=str(tmp_path),
        architect_files=[TEST_FILE], resume_state=None, run_id="run", skills_prompt="", learned_rag_context="",
        matched_files=[], related_files=[], ecosystem_invariant_block="", resource_lifecycle_block="",
        verification_contract_block="", recovery_contract_block="", required_files_prompt_block="",
        required_dependencies_prompt_block="", expected_files_upfront=[TEST_FILE],
        architect_basename_to_path={"ProtocolParserTest.java": TEST_FILE},
        chain=[], targeted_max_retries=TARGETED_MAX, stream_callback=None, approval_callback=None,
        active_skills=[], active_skill_rules_snapshot={}, developer=developer_double(), run_verifier=AsyncMock(),
        spec_compliance=AsyncMock(), skill_engine=MagicMock(), kernel=Kernel(config=AppConfig()), max_retries=MAX_RETRIES,
        web_lookup_query_callback=None, approve_web_lookup=AsyncMock(return_value=False), established_files=[],
    )


def _policy():
    return dict(max_retries=MAX_RETRIES, targeted_max_retries=TARGETED_MAX, has_fallback_model=True)


class _Run:
    """One unit's retry loop over the real policy and the real failure recording."""

    def __init__(self, tmp_path):
        self.target = tmp_path / TEST_FILE
        self.target.parent.mkdir(parents=True)
        self.ctx, self.state = _ctx(tmp_path), GenerationState()
        self.state.all_files_written = {TEST_FILE}
        self.resets, self.rows = [], []

    async def play(self, script):
        real_reset = retry_strategy.reset_scoped_budgets_for_new_family

        def spy_reset(budgets):
            self.resets.append(self.state.attempt_number)
            real_reset(budgets)

        routing = SimpleNamespace(available=True, patch_excluded=False, to_dict=lambda: {})   # run 10 had a usable fallback
        with patch.object(retry_strategy, "classify_environment_failure", return_value=None), \
                patch.object(retry_strategy, "fallback_routing_for_context", return_value=routing), \
                patch.object(retry_strategy, "reset_scoped_budgets_for_new_family", spy_reset):
            for make_failure, content in script:
                state = self.state
                assert decide_for_state(state, **_policy()).should_continue
                state.attempt_number += 1
                admitted = decide_attempt_mode(state, **_policy())
                mode = admitted.action.value if admitted.action in (
                    RetryAction.TARGETED, RetryAction.MISSING_FILES, RetryAction.FALLBACK_TARGETED,
                    RetryAction.API_CONTRACT_RECOVERY) else RetryAction.FULL_SET.value
                state.last_attempt_mode = mode
                if mode == RetryAction.FALLBACK_TARGETED.value:   # attempt.py, the fallback-targeted branch
                    state.budgets.fallback_targeted_attempted = True
                    state.budgets.fallback_targeted_requested = False
                    state.budgets.fallback_attempts_used += 1
                elif mode == RetryAction.FULL_SET.value and state.budgets.retry_count >= 1:   # the escalated full-set attempt
                    state.budgets.fallback_attempts_used += 1
                if content is not None:
                    self.target.write_text(content)
                await retry_strategy.handle_attempt_failure(state, self.ctx, make_failure())
                self.rows.append(SimpleNamespace(
                    attempt=state.attempt_number, mode=mode, classification=state.last_progress_classification,
                    no_progress=state.consecutive_no_progress_attempts, full_set=state.budgets.retry_count,
                    targeted=state.budgets.targeted_retry_count, fallback_used=state.budgets.fallback_targeted_attempted,
                    family=state.budgets.last_failure_signature, findings=list(state.open_findings),
                    next_action=decide_for_state(state, **_policy()).action.value,
                ))
        return self


async def _after_attempt_5(tmp_path) -> _Run:
    """Run 10 up to attempt 5: the assertion finding is open, the targeted budget is closed, the fallback-targeted repair used."""
    run = await _Run(tmp_path).play(RUN10_TO_ATTEMPT_5)
    last = run.rows[-1]
    assert last.targeted >= TARGETED_MAX and last.fallback_used is True and last.next_action == "full_set"
    assert [finding.locations for finding in last.findings] == [((TEST_FILE, 75),)]
    return run


@pytest.mark.asyncio
async def test_run10_attempts_1_to_8_the_budget_refusal_at_attempt_6_replenishes_nothing(tmp_path):
    """ORIGINAL SYMPTOM. Before the fix the resets fired at attempts [2, 6] and attempts 8/9 were targeted again."""
    run = await _Run(tmp_path).play(RUN10_TO_ATTEMPT_5 + RUN10_ATTEMPTS_6_TO_8)
    rows = run.rows

    # Attempts 1-5 are exactly what run 10 recorded (modes, progress classification, no-progress counter).
    assert [row.mode for row in rows[:5]] == ["full_set", "targeted", "targeted", "targeted", "fallback_targeted"]
    assert [row.classification for row in rows[:5]] == ["PROGRESS", "PROGRESS", "NO_PROGRESS", "NO_PROGRESS", "PROGRESS"]
    assert [row.no_progress for row in rows[:5]] == [0, 0, 1, 2, 0]

    assert run.resets == [2]                                   # the genuine testCompile -> assertion transition, and only it
    five, six = rows[4], rows[5]
    assert six.family == five.family                           # the refusal inherits the assertion's family
    assert (six.targeted, six.fallback_used) == (five.targeted, True)
    assert six.findings == five.findings                       # the open finding is untouched
    assert six.full_set == five.full_set + 1                   # and the attempt is charged to the mode it ran in
    # The rest follows the existing policy to exhaustion: no targeted attempt ever comes back for this blocker.
    assert [row.mode for row in rows[5:]] == ["full_set", "full_set", "full_set"]
    assert rows[-1].next_action == RetryAction.STOP_EXHAUSTED.value
    assert rows[-1].family == rows[1].family                   # attempt 8 is still attempt 2's assertion


NOT_ABOUT_THE_CANDIDATE = {
    "context_budget_unsatisfiable": _orchestrator,
    "output_budget_unsatisfiable": lambda: _orchestrator("output_budget_unsatisfiable", "OUTPUT_BUDGET_UNSATISFIABLE"),
    "general_error": lambda: _orchestrator("general_error", "UNCLASSIFIED"),
    "delivery_operation_contract": lambda: _delivery("operation_contract", "OUTPUT_TRUNCATED"),
    "delivery_anchored_edit": lambda: _delivery("anchored_edit", "ANCHOR_NOT_IN_FILE"),
    "advisory": _advisory,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(NOT_ABOUT_THE_CANDIDATE))
async def test_a_failure_that_does_not_replace_the_open_finding_replenishes_nothing(tmp_path, kind):
    make_failure = NOT_ABOUT_THE_CANDIDATE[kind]
    assert replaces_open_finding(make_failure().failure) is False
    run = await _after_attempt_5(tmp_path)
    before = run.rows[-1]

    await run.play([(make_failure, None)])

    after = run.rows[-1]
    assert run.resets == [2]
    assert after.family == before.family
    assert after.targeted == before.targeted and after.targeted >= TARGETED_MAX   # the targeted count is not zeroed
    assert after.fallback_used is True                                            # the one fallback-targeted repair stays used
    assert after.findings == before.findings                                      # the open finding is preserved
    assert after.full_set == before.full_set + 1                                  # charged by the existing policy (a full-set attempt)


@pytest.mark.asyncio
async def test_a_genuine_candidate_transition_still_opens_a_new_family_with_a_fresh_scoped_allowance(tmp_path):
    """Run 10 attempt 1 -> 2: the testCompile error is gone and the assertion is a new authoritative finding."""
    run = await _Run(tmp_path).play(RUN10_TO_ATTEMPT_5[:3])
    one, two, three = run.rows

    assert replaces_open_finding(_gate(_assertion(75), (75,)).failure) is True
    assert run.resets == [2]
    assert two.family != one.family
    assert (two.mode, two.targeted) == ("targeted", 0)        # the attempt that exposed the new family is not charged
    assert [finding.locations for finding in two.findings] == [((TEST_FILE, 75),)]
    assert (three.mode, three.targeted) == ("targeted", 1)    # the next failure in that family is


@pytest.mark.asyncio
async def test_a_changed_workspace_resets_only_the_no_progress_counter(tmp_path):
    """Attempt 5: other bytes, the same assertion. PROGRESS stays diagnostic; it never touches the scoped allowance."""
    run = await _Run(tmp_path).play(RUN10_TO_ATTEMPT_5)
    four, five = run.rows[3], run.rows[4]

    assert (four.no_progress, five.no_progress, five.classification) == (2, 0, "PROGRESS")
    assert run.resets == [2]
    assert five.family == four.family
    assert five.targeted == four.targeted and five.fallback_used is True


# ----------------------------------------------------------------------------- S8 (C-07, GAP-A1-07), added; the tests above are unchanged.

def _no_progress_retry():
    """The orchestrator's own refusal of an identical candidate. Unlike the refusals above it is NOT excluded by the memory predicate's deny-lists: it replaces
    the open finding - and, before S8, the same predicate let it open a new failure family."""
    raw = "NO_PROGRESS: the repair produced the same candidate as the previous attempt"
    return QualityGateFailure(Failure(
        type="no_progress_retry", message=raw, raw_output=raw, source="orchestrator", likely_files=[TEST_FILE], file_locations=[FileLocation(TEST_FILE)]))


@pytest.mark.asyncio
async def test_s8_a_no_progress_retry_replenishes_nothing_on_the_real_failure_recorder(tmp_path):
    assert replaces_open_finding(_no_progress_retry().failure) is True     # the memory predicate is unchanged
    run = await _after_attempt_5(tmp_path)
    before = run.rows[-1]

    await run.play([(_no_progress_retry, None)])

    after = run.rows[-1]
    assert run.resets == [2], "pre-fix (MEASURED on the pure owners): a second reset here - the scoped budgets were replenished"
    assert after.family == before.family                                           # it inherited the family the repair was addressing
    assert after.targeted == before.targeted and after.targeted >= TARGETED_MAX
    assert after.fallback_used is True
