"""D2 EXEC-LAYER rebuild S6: NO EXECUTION != EMPTY SUCCESS for the milestone replay.

MEASURED before the change (2026-10-25, scratchpad s6/c1.py, the REAL validator): a replay that never ran collapsed to an empty failure list and the driver
treated it as a pass. Under ``contained_execution_required=True`` + ``containment_backend="none"`` (a typed ExternalExecutionRefused), with a failing
backend, with a binary that does not exist and with a persisted empty command, ``replay_prior_milestone_verifications`` returned ``[]`` and
``_MilestonePlanDriver.run_phase`` returned ``None``: no regression, the integration unit ran. (A deterministic command in the same refused state was reported
as a REGRESSION - a product failure - for something that never executed.) The first owner that lost the distinction is the replay itself: the validator's
result already says nothing ran (a step with no exit code that did not time out, ``returncode`` None) - the contract the runtime-verification gate uses
(``runtime_verification_infrastructure_reason``) - and the replay ignored it, then skipped any result without a marker.

Now the replay returns a report with one OUTCOME per milestone (passed / regressed / not_evaluable / not_executed); a replay that did not run is its own
terminal state (``milestone_replay_not_executed``), never an empty list, a pass or a regression, and the evidence the final result carries says which.
The critical negative control: a replay that really ran with zero regressions still succeeds and is distinguishable from one that never ran."""
from __future__ import annotations

import asyncio
import sys

import pytest
from _mcp_containment_standin import ContainmentStandIn
from _strict_doubles import strict_config
from test_milestones import mkv2

from kriya.tools import containment
from kriya.workflow.execution_plan import TerminalPhase
from kriya.workflow.milestones import (
    MILESTONE_REPLAY_NOT_EXECUTED,
    REPLAY_EVIDENCE_UNAVAILABLE,
    REPLAY_INPUT_INVALID,
    MilestoneReplayNotExecuted,
    MilestoneReplayReport,
    MilestoneRunState,
    _MilestonePlanDriver,
    replay_prior_milestone_report,
    replay_prior_milestone_verifications,
)

PLAN = {"group_id": "g1", "original_goal": "x", "milestones": [{"id": "M1", "goal": "g", "depends_on": []}, {"id": "M2", "goal": "g2", "depends_on": []}]}
PASS = [sys.executable, "-c", "print('[VERIFICATION] PASS')"]
FAIL = [sys.executable, "-c", "print('[VERIFICATION] FAIL: it broke')"]
NO_MARKER_CRASH = [sys.executable, "-c", "import sys; print('no marker here'); sys.exit(3)"]
PYTEST_RUN = [sys.executable, "-m", "pytest", "-q"]          # a deterministic kind ("test")


def _autonomy(**fields):
    return strict_config(autonomy=fields).autonomy


REFUSED = dict(contained_execution_required=True, containment_backend="none")       # the owner's typed ExternalExecutionRefused
HOST = dict(contained_execution_required=False)


@pytest.fixture
def failing_backend(monkeypatch):
    monkeypatch.setitem(containment._PRODUCTION_BACKEND_REGISTRY, "s6-failing-backend", lambda: ContainmentStandIn(should_fail=True))
    return dict(contained_execution_required=True, containment_backend="s6-failing-backend")


def _state(**commands):
    state = MilestoneRunState.from_dict(PLAN)
    state.verification_commands.update(commands)
    return state


def _replay(tmp_path, state, **autonomy):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return replay_prior_milestone_report(str(workspace), state, autonomy_cfg=_autonomy(**autonomy)), workspace


def _driver(workspace, state, **autonomy):
    driver = object.__new__(_MilestonePlanDriver)
    driver.workspace_path = str(workspace)
    driver.run_state = state
    driver.total = 2
    driver.engine_config = strict_config(autonomy=autonomy)
    driver._replay_evidence = None
    return driver


def _phase(driver):
    return asyncio.run(driver.run_phase(TerminalPhase.REPLAY_PRIOR_VERIFICATIONS))


def _outcomes(report):
    return {entry["milestone_id"]: entry["outcome"] for entry in report.entries}


# ----------------------------------------------------------------- a replay that never ran is never an empty success

@pytest.mark.parametrize("command", [PASS, PYTEST_RUN], ids=["marker-command", "deterministic-test-command"])
def test_a_refused_replay_is_not_executed_not_an_empty_list_and_not_a_regression(tmp_path, command):
    sentinel = tmp_path / "RAN"
    refused_command = [sys.executable, "-c", f"open({str(sentinel)!r}, 'w').write('x')"] if command is PASS else command
    report, workspace = _replay(tmp_path, _state(M1=[refused_command]), **REFUSED)
    assert _outcomes(report) == {"M1": "not_executed"} and report.state == "not_executed"
    assert report.regressions == [], "a replay that never ran is not a regression either"
    entry = report.entries[0]
    assert "could not be executed" in entry["reason"] and "CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION" in entry["cause"], entry
    assert not sentinel.exists()
    with pytest.raises(MilestoneReplayNotExecuted):
        replay_prior_milestone_verifications(str(workspace), _state(M1=[refused_command]), autonomy_cfg=_autonomy(**REFUSED))


def test_the_driver_turns_a_refused_replay_into_its_own_terminal_status_and_the_integration_never_starts(tmp_path):
    state = _state(M1=[PASS])
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = _phase(_driver(workspace, state, **REFUSED))
    assert result is not None, "the old driver returned None here: 'replay passed'"
    assert result["status"] == "milestone_replay_not_executed" and result["quality_gates_passed"] is False
    assert result["reason_codes"] == [MILESTONE_REPLAY_NOT_EXECUTED] and result["failures"] == []
    assert result["replay"]["state"] == "not_executed" and result["replay"]["not_executed"] == ["M1"]


def test_an_unavailable_backend_is_not_executed(tmp_path, failing_backend):
    report, _workspace = _replay(tmp_path, _state(M1=[PASS]), **failing_backend)
    assert report.state == "not_executed" and _outcomes(report) == {"M1": "not_executed"} and report.regressions == []


def test_a_process_that_never_started_is_not_executed(tmp_path):
    report, _workspace = _replay(tmp_path, _state(M1=[["kriya-no-such-binary-xyz", "--check"]]), **HOST)
    assert _outcomes(report) == {"M1": "not_executed"}
    assert "No such file" in report.entries[0]["cause"], report.entries[0]


@pytest.mark.parametrize("corrupted", [[[]], [["python3", 3]], "python3 -m x", [[""]], [[], PASS]], ids=["empty-argv", "non-string", "not-a-list", "empty-token", "empty-first"])
def test_corrupted_persisted_commands_are_a_typed_invalid_input_and_nothing_runs(tmp_path, corrupted):
    report, _workspace = _replay(tmp_path, _state(M1=corrupted), **HOST)
    assert _outcomes(report) == {"M1": "not_executed"} and report.entries[0]["reason"].startswith(REPLAY_INPUT_INVALID)


def test_a_validators_early_return_shapes_are_not_executed_too(tmp_path, monkeypatch):
    """The validator returns early (interpreter/environment not prepared, a failed prerequisite) with `returncode` None and no steps: nothing ran."""
    from kriya.tools.validate import PolymorphicValidator

    shapes = [
        {"success": False, "timed_out": False, "returncode": None, "output": "python environment could not be prepared"},
        {"success": False, "timed_out": False, "returncode": None, "prerequisite_failed": True, "output": "RUNTIME_PREREQUISITE_FAILED: the build failed"},
    ]
    for shape in shapes:
        monkeypatch.setattr(PolymorphicValidator, "run_app_sequence", lambda self, commands, shape=shape: dict(shape))
        report, _workspace = _replay(tmp_path, _state(M1=[PASS]), **HOST)
        assert _outcomes(report) == {"M1": "not_executed"}, shape


# ----------------------------------------------------------------- lost evidence is typed too

def test_a_completed_milestone_whose_verification_could_not_be_captured_is_not_silently_skipped(tmp_path):
    state = _state(M2=[PASS])
    state.replay_evidence_unavailable["M1"] = "RuntimeError: the run-verification judge was unavailable"
    report, _workspace = _replay(tmp_path, state, **HOST)
    assert _outcomes(report) == {"M1": "not_executed", "M2": "passed"} and report.state == "not_executed"
    assert report.entries[0]["reason"].startswith(REPLAY_EVIDENCE_UNAVAILABLE)


def test_the_unavailable_evidence_survives_the_sidecar_and_an_old_sidecar_still_loads():
    state = _state(M2=[PASS])
    state.replay_evidence_unavailable["M1"] = "boom"
    again = MilestoneRunState.from_dict(state.to_dict())
    assert again.replay_evidence_unavailable == {"M1": "boom"}
    legacy = state.to_dict()
    legacy.pop("replay_evidence_unavailable")
    assert MilestoneRunState.from_dict(legacy).replay_evidence_unavailable == {}


def test_resuming_a_sequence_keeps_the_evidence_that_was_lost_before_the_interruption(tmp_path):
    from kriya.workflow.milestones import load_or_resume_milestone_run_state, save_milestone_run_state

    first = MilestoneRunState.from_dict(PLAN)
    first.replay_evidence_unavailable["M1"] = "RuntimeError: the judge was unavailable"
    save_milestone_run_state(str(tmp_path), first)
    resumed = load_or_resume_milestone_run_state(str(tmp_path), PLAN)
    assert resumed.replay_evidence_unavailable == {"M1": "RuntimeError: the judge was unavailable"}


class _Judge:
    def __init__(self, answer=None, raises=None):
        self.answer, self.raises = answer, raises

    async def judge(self, **_kwargs):
        if self.raises:
            raise self.raises
        return self.answer


def _capture(tmp_path, judge, **autonomy):
    driver = object.__new__(_MilestonePlanDriver)
    driver.engine_config = strict_config(autonomy={"run_verification_enabled": True, **autonomy})
    driver.workspace_path = str(tmp_path)
    driver.run_state = MilestoneRunState.from_dict(PLAN)
    driver.we = type("We", (), {"run_verifier": judge})()
    driver._goal = {"M1": "goal"}
    asyncio.run(driver._capture_verification_commands(mkv2("M1"), {"files": ["a.txt"], "design": "d"}))
    return driver.run_state


def test_capturing_records_what_it_could_not_capture_and_not_what_was_simply_not_applicable(tmp_path):
    lost = _capture(tmp_path, _Judge(raises=RuntimeError("judge exploded")))
    assert "judge exploded" in lost.replay_evidence_unavailable["M1"] and "M1" not in lost.verification_commands
    down = _capture(tmp_path, _Judge({"should_run": False, "run_commands": None, "infrastructure_error": "model unreachable"}))
    assert "model unreachable" in down.replay_evidence_unavailable["M1"]
    nothing = _capture(tmp_path, _Judge({"should_run": False, "run_commands": None}))
    assert nothing.replay_evidence_unavailable == {} and nothing.verification_commands == {}, "nothing applicable is not lost evidence"
    captured = _capture(tmp_path, _Judge({"should_run": True, "run_commands": [PASS], "command_source": "inferred"}))
    assert captured.verification_commands == {"M1": [PASS]} and captured.replay_evidence_unavailable == {}


def test_with_runtime_verification_turned_off_a_judge_that_cannot_answer_loses_nothing_the_operator_asked_for(tmp_path):
    for judge in (_Judge(raises=RuntimeError("boom")), _Judge({"should_run": False, "run_commands": None, "infrastructure_error": "model unreachable"})):
        state = _capture(tmp_path, judge, run_verification_enabled=False)
        assert state.replay_evidence_unavailable == {}


def test_a_later_successful_capture_clears_the_loss(tmp_path):
    driver_state = _capture(tmp_path, _Judge(raises=RuntimeError("first attempt")))
    assert "M1" in driver_state.replay_evidence_unavailable
    driver = object.__new__(_MilestonePlanDriver)
    driver.engine_config = strict_config(autonomy={"run_verification_enabled": True})
    driver.workspace_path, driver.run_state, driver._goal = str(tmp_path), driver_state, {"M1": "goal"}
    driver.we = type("We", (), {"run_verifier": _Judge({"should_run": True, "run_commands": [PASS], "command_source": "inferred"})})()
    asyncio.run(driver._capture_verification_commands(mkv2("M1"), {"files": ["a.txt"], "design": "d"}))
    assert driver_state.replay_evidence_unavailable == {}


# ----------------------------------------------------------------- the critical negative control: a replay that RAN with zero regressions still succeeds

def test_a_replay_that_really_ran_with_zero_regressions_succeeds_and_is_distinguishable_from_one_that_never_ran(tmp_path):
    ran, workspace = _replay(tmp_path, _state(M1=[PASS], M2=[PASS]), **HOST)
    never, _ = _replay(tmp_path, _state(M1=[PASS], M2=[PASS]), **REFUSED)
    assert ran.state == "executed" and _outcomes(ran) == {"M1": "passed", "M2": "passed"}
    assert never.state == "not_executed" and _outcomes(never) == {"M1": "not_executed", "M2": "not_executed"}
    assert ran.to_dict() != never.to_dict()
    assert replay_prior_milestone_verifications(str(workspace), _state(M1=[PASS], M2=[PASS]), autonomy_cfg=_autonomy(**HOST)) == []
    driver = _driver(workspace, _state(M1=[PASS], M2=[PASS]), **HOST)
    assert _phase(driver) is None, "a replay that ran clean lets the plan proceed"
    assert driver._replay_evidence["state"] == "executed" and driver._replay_evidence["passed"] == ["M1", "M2"]


def test_a_genuine_regression_is_reported_with_the_evidence(tmp_path):
    report, workspace = _replay(tmp_path, _state(M1=[PASS], M2=[FAIL]), **HOST)
    assert report.state == "regressions_found" and _outcomes(report) == {"M1": "passed", "M2": "regressed"}
    result = _phase(_driver(workspace, _state(M1=[PASS], M2=[FAIL]), **HOST))
    assert result["status"] == "milestone_replay_failed" and [f["milestone_id"] for f in result["failures"]] == ["M2"]
    assert result["replay"]["state"] == "regressions_found" and result["replay"]["regressed"] == ["M2"]


def test_a_regression_beside_a_milestone_that_did_not_run_is_still_a_replay_that_did_not_complete(tmp_path):
    state = _state(M1=[FAIL])
    state.replay_evidence_unavailable["M2"] = "capture failed"
    result = _phase(_driver(tmp_path, state, **HOST))
    assert result["status"] == "milestone_replay_not_executed", "an incomplete replay cannot claim anything but incomplete"
    assert [f["milestone_id"] for f in result["failures"]] == ["M1"] and [e["milestone_id"] for e in result["not_executed"]] == ["M2"]


def test_an_executed_command_with_no_verdict_is_recorded_as_not_evaluable_never_as_passed(tmp_path):
    """The documented policy (a missing marker is no failure) stays, but it is no longer indistinguishable from a pass."""
    report, _workspace = _replay(tmp_path, _state(M1=[NO_MARKER_CRASH]), **HOST)
    assert _outcomes(report) == {"M1": "not_evaluable"} and report.entries[0]["returncode"] == 3
    assert report.state == "executed" and report.regressions == [] and report.to_dict()["passed"] == []


def test_nothing_to_replay_says_so(tmp_path):
    report, workspace = _replay(tmp_path, _state(), **HOST)
    assert report.state == "nothing_to_replay" and report.entries == []
    driver = _driver(workspace, _state(), **HOST)
    assert _phase(driver) is None and driver._replay_evidence["state"] == "nothing_to_replay"


# ----------------------------------------------------------------- the persisted evidence and the final result agree

@pytest.mark.asyncio
async def test_run_milestones_reports_the_same_truth_in_the_final_result_and_the_persisted_plan_states():
    import tempfile
    from unittest.mock import AsyncMock, MagicMock, patch

    from _strict_doubles import strict_engine
    from test_milestones import _committing

    from kriya.workflow.milestones import run_milestones

    def run(report):
        async def go():
            with tempfile.TemporaryDirectory() as tmp:
                state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=[mkv2("M1", goal="g1", success_criterion="c1")])
                we = strict_engine()
                we.run_generation_workflow = AsyncMock(side_effect=_committing({"quality_gates_passed": True, "design": "d", "files": ["a.py"]}))
                we.run_verifier = MagicMock()
                we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
                with patch("kriya.workflow.milestones.replay_prior_milestone_report", return_value=report):
                    result = await run_milestones(we, state, tmp)
                return result, we.run_generation_workflow.await_count
        return go()

    never, calls_never = await run(MilestoneReplayReport(entries=[
        {"milestone_id": "M1", "outcome": "not_executed", "reason": "runtime verification command could not be executed"}]))
    assert never["status"] == "milestone_replay_not_executed" and never["replay"]["state"] == "not_executed"
    assert calls_never == 1, "the integration call never fired"
    states = never["work_unit_states"]
    integration = [state for state in states.values() if state["status"] == "BLOCKED"]
    assert len(integration) == 1 and ("TERMINAL_PHASE_FAILED" in str(integration[0]) or "replay" in str(integration[0]).lower()), states
    assert all(state["status"] in ("VERIFIED", "BLOCKED") for state in states.values()), states
    ran, calls_ran = await run(MilestoneReplayReport(entries=[
        {"milestone_id": "M1", "outcome": "passed", "reason": "the verification marker reported PASS on replay"}]))
    assert ran["status"] == "success" and ran["replay"]["state"] == "executed" and ran["replay"]["passed"] == ["M1"]
    assert calls_ran == 2, "a replay that ran clean lets the integration call proceed"


def test_the_cli_treats_every_status_but_success_as_a_failed_run():
    import inspect

    from kriya import cli
    assert 'sys.exit(0 if milestone_result.get("status") == "success" else 1)' in inspect.getsource(cli)
