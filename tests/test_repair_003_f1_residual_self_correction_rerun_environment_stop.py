"""RUNTIME-VERIFICATION-ENV-STOP-001 residual (focused F1/F3 review of 6d97558..6169745, finding F1; owner decision
2026-10-10: REPRODUCE FIRST, through the actual self-correction re-run path). Written BEFORE any production change.

Traced (attempt.py, the mutating path's inline runtime block): the plain-nonzero-exit branch may run the bounded
self-correction micro-loop; when the loop reports ``resolved`` the block re-runs ``validator.run_app_sequence`` once
and grades THAT result directly - deterministic kind: ``bool(run_res["success"])``; otherwise the contract classifier
and ``_resolve_runtime_verification_grade`` - without passing it through the shared runtime consumer
``_raise_runtime_verification_infrastructure_failure`` (its only callers are the verification-only path and the first
run of this block). A re-run that returns the typed VERIFICATION-UNIT-ENV-FALLBACK-001 result
(``environment_reason_code == PYTHON_ENVIRONMENT_UNAVAILABLE``, nothing launched) is therefore graded as the
candidate's runtime behaviour and routed as a ``run_verification`` failure, exactly the class 3d5f6db closed on the
first run.

Reachability (the trajectory this module scripts): the first run is dependency-free (no declaration, Kriya's own
interpreter, admitted) and the application exits nonzero; the micro-loop's repair declares a dependency
(``requirements.txt`` is in its writable scope - the ordinary model response to a ModuleNotFoundError); the re-run now
REQUIRES the project environment, ``python -m venv`` fails, and the resolver returns the typed result. Only
``python -m venv`` is stubbed; the loop itself is replaced by a scripted repair (rule 4: a live trajectory becomes a
deterministic scripted equivalent); every other step is the real production code.

Required invariant (owner, 2026-10-10): the re-run's structured environment result stops runtime verification through
the EXISTING shared raiser (``verification_infrastructure_failure``, ``diagnostics.reason_code``, STOP_ENVIRONMENT)
before grading, before Developer retry and before owner recovery - no new taxonomy, no retry-policy change, no
output-text matching. A re-run that genuinely executed keeps its grading (application) and its process-exit verdict
(deterministic sequence) exactly as before.
"""
import sys
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double
from test_repair_003_f1_runtime_environment_stop import (
    INFRA,
    PYTHON_ENVIRONMENT_UNAVAILABLE,
    _app_launched,
    _assert_stopped_before_retry,
    _VenvCreationFails,
)
from test_workflow import _minimal_attempt_ctx

from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.attempt import run_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.self_correction import SelfCorrectionResult
from kriya.workflow.state import GenerationState

APP_CRASH = "import sys\nprint('APP_RAN')\nsys.exit(3)\n"
APP_OK = "print('APP_RAN')\n"
TEST_FAILS = "def test_x():\n    assert 1 == 2\n"
# Longer than TEST_FAILS on purpose: pytest keys its assertion-rewrite cache on size+mtime, and the repair
# rewrites the file within the same second (pre-fix measurement: a same-size rewrite re-ran the failing bytecode).
TEST_PASSES = "def test_x():\n    assert 1 == 1  # repaired by the micro-loop\n"
RUN_APP = [["python", "app.py"]]
RUN_PYTEST = [["python", "-m", "pytest", "-q", "tests"]]
GRADE_FAILED = {"passed": False, "reasoning": "APP_RAN printed but the process exited 3", "likely_files": []}


def _mutating_ctx(tmp_path, run_commands, *, grades):
    """The mutating path: the Developer writes app.py, the real compile and test gates run, the judge asks for
    ``run_commands``; the grader answers ``grades`` in order (one entry per grade() call the test expects)."""
    developer = developer_double()
    developer.run_generation = AsyncMock(return_value=[{"filepath": "app.py", "content": APP_CRASH}])
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True, "run_commands": run_commands, "command_source": "inferred",
        "success_criteria": "prints APP_RAN and exits 0",
    })
    run_verifier.grade = AsyncMock(side_effect=list(grades))
    ctx = _minimal_attempt_ctx(
        tmp_path, developer=developer, run_verifier=run_verifier, goal="Run the app and print APP_RAN",
        architect_files=["app.py"], expected_files_upfront=["app.py"],
        allowed_write_relpaths=["app.py", "requirements.txt", "tests/test_x.py"], max_retries=4,
    )
    ctx.kernel.config.autonomy.self_correction_loop_enabled = True
    return ctx, developer, run_verifier


class _ScriptedRepair:
    """Stands in for ``run_self_correction_loop`` with the one thing the block consumes: a resolved result after a
    repair the loop was authorized to make (``edit`` writes only files the loop received as writable)."""

    def __init__(self, edit):
        self.edit = edit
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["failure_type"] == "run_verification"
        written = self.edit(kwargs["worktree_path"])
        assert set(written) <= set(kwargs["writable_files"]), (written, kwargs["writable_files"])
        return SelfCorrectionResult(resolved=True, turns_used=1, final_compile_output="py_compile: ok",
                                    modified_files={path: "" for path in written})


def _declare_dependency(worktree):
    with open(f"{worktree}/requirements.txt", "w", encoding="utf-8") as handle:
        handle.write("packaging>=20\n")  # a real declaration: from here on the project requires its own environment
    return ["requirements.txt"]


def _venv_attempted(commands):
    return [c for c in commands if len(c) >= 3 and c[1:3] == ["-m", "venv"]]


# ------------------------------------------------------------------- the residual, through the real self-correction re-run
@pytest.mark.asyncio
async def test_f1_residual_a_rerun_whose_environment_cannot_be_prepared_stops_typed_before_grading(tmp_path):
    """First run: dependency-free, launched, exit 3, graded failed (one grade() call), self-correction repairs by
    declaring a dependency. Re-run: the required environment cannot be created, nothing launches, the typed result
    comes back. Invariant: the typed environment stop, raised by the existing shared consumer - the grader is never
    asked a second time, the failure is not the candidate's, and the retry strategy takes STOP_ENVIRONMENT."""
    ctx, developer, run_verifier = _mutating_ctx(tmp_path, RUN_APP, grades=[GRADE_FAILED, GRADE_FAILED])
    repair = _ScriptedRepair(_declare_dependency)
    state = GenerationState()
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method), \
            patch("kriya.workflow.self_correction.run_self_correction_loop", new=repair):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    # The trajectory really happened: one Developer write, one launched run, one repair, one venv attempt after it.
    assert developer.run_generation.await_count == 1 and len(repair.calls) == 1
    assert (tmp_path / "requirements.txt").exists()
    launched = _app_launched(stub.commands)
    assert len(launched) == 1 and launched[0][0] == sys.executable, stub.commands
    assert len(_venv_attempted(stub.commands)) == 1 and stub.commands.index(_venv_attempted(stub.commands)[0]) > stub.commands.index(launched[0])
    # The invariant.
    failure = raised.value.failure
    assert failure.type == INFRA, (failure.type, failure.message[:300])
    assert failure.diagnostics["reason_code"] == PYTHON_ENVIRONMENT_UNAVAILABLE
    assert "ensurepip" in failure.message
    assert run_verifier.grade.await_count == 1  # the first run only; the re-run observed nothing to grade
    assert [o["type"] for o in state.gate_outcomes][-1:] == [INFRA]
    await _assert_stopped_before_retry(state, ctx, raised.value)


# ------------------------------------------------------------------- negative controls: a re-run that executed is unchanged
@pytest.mark.asyncio
async def test_f1_residual_control_a_rerun_that_launched_and_still_fails_is_graded_as_the_candidates(tmp_path):
    """Resolved repair, no environment involved (nothing declared): the re-run launches the application again, it
    still exits 3, and the SECOND grade() verdict decides - a run_verification failure carrying the self-correction
    attempt, never an environment stop."""
    ctx, _developer, run_verifier = _mutating_ctx(tmp_path, RUN_APP, grades=[GRADE_FAILED, GRADE_FAILED])
    repair = _ScriptedRepair(lambda _worktree: [])  # resolved, changed nothing that matters
    state = GenerationState()
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method), \
            patch("kriya.workflow.self_correction.run_self_correction_loop", new=repair):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    failure = raised.value.failure
    assert failure.type == "run_verification" and "APP_RAN" in failure.raw_output
    assert not (failure.diagnostics or {}).get("reason_code")
    assert failure.self_correction_attempt["turns_used"] == 1
    assert run_verifier.grade.await_count == 2 and len(repair.calls) == 1
    assert len(_app_launched(stub.commands)) == 2 and _venv_attempted(stub.commands) == []
    assert state.gate_outcomes[-1]["type"] == "run_verification" and state.gate_outcomes[-1]["graded_by"] == "llm"
    assert state.environment_failure is None


@pytest.mark.asyncio
async def test_f1_residual_control_a_deterministic_rerun_that_executed_keeps_its_process_exit_verdict(tmp_path):
    """Deterministic sequence (pytest): the first run fails (exit 1 on a failing test), the repair fixes the test in
    scope, the re-run executes and exits 0 - process-exit authority passes the gate without any grade() call and
    without the environment stop, exactly as before. (The test gate itself is not under test here and is held green
    so the runtime block is reached with a failing test on disk.)"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(TEST_FAILS)
    ctx, _developer, run_verifier = _mutating_ctx(tmp_path, RUN_PYTEST, grades=[])

    def _fix_the_test(worktree):
        with open(f"{worktree}/tests/test_x.py", "w", encoding="utf-8") as handle:
            handle.write(TEST_PASSES)
        return ["tests/test_x.py"]

    repair = _ScriptedRepair(_fix_the_test)
    state = GenerationState()
    stub = _VenvCreationFails()
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=stub.method), \
            patch.object(PolymorphicValidator, "run_tests", return_value={"success": True, "output": ""}), \
            patch("kriya.workflow.self_correction.run_self_correction_loop", new=repair):
        await run_attempt(state, ctx)  # must not raise
    assert len(repair.calls) == 1 and run_verifier.grade.await_count == 0
    assert len(_app_launched(stub.commands)) == 2 and _venv_attempted(stub.commands) == []
    outcome = state.gate_outcomes[-1]
    assert outcome["type"] == "test" and outcome["success"] is True and outcome["graded_by"] == "process_exit"
    assert state.environment_failure is None
