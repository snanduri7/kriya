"""Wave-2 item 1(b) (MILESTONE-REPLAY-UNAPPROVED-COMMANDS-001, containment half; owner decision 2026-10-22: reproduced -> T0): the milestone replay executes the persisted verification argv
under the RUN'S autonomy configuration, never a default one.

MEASURED before the fix: `replay_prior_milestone_verifications` built `PolymorphicValidator(workspace, original_workspace_path=workspace)` - `AutonomyConfig()` defaults, so
`build_containment_profile_and_backend()` was `(None, None)` and a run configured `contained_execution_required=True` executed the replayed argv on the host: a sentinel file OUTSIDE the
workspace was written. Every other `PolymorphicValidator(` construction in kriya/workflow passes the run's autonomy config.

These tests run the REAL validator (the existing replay tests patch `PolymorphicValidator` and so bypass the defect path) with a real command that writes a sentinel outside the workspace."""
import asyncio
import inspect
import os
import sys

import pytest
from _strict_doubles import strict_config

from kriya.workflow import milestones
from kriya.workflow.execution_plan import TerminalPhase
from kriya.workflow.milestones import MilestoneRunState, _MilestonePlanDriver, replay_prior_milestone_verifications

PLAN = {"group_id": "g1", "original_goal": "x", "milestones": [{"id": "M1", "goal": "g", "depends_on": []}]}


def _approve(files, reason):  # W2-04: the run's approval channel approves the replay commands (human-in-the-loop is the default mode)
    return True


def _scenario(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sentinel = tmp_path / "OUTSIDE_WORKSPACE_SENTINEL"
    state = MilestoneRunState.from_dict(PLAN)
    state.verification_commands["M1"] = [[sys.executable, "-c", f"open({str(sentinel)!r}, 'w').write('executed')"]]
    return str(workspace), sentinel, state


def _autonomy(**fields):
    return strict_config(autonomy=fields).autonomy


def test_a_run_that_requires_containment_never_replays_on_the_host(tmp_path):
    workspace, sentinel, state = _scenario(tmp_path)
    try:
        asyncio.run(replay_prior_milestone_verifications(workspace, state, autonomy_cfg=_autonomy(contained_execution_required=True), approval_callback=_approve))
    except Exception as refusal:  # a platform with no containment backend refuses; either way nothing ran on the host
        assert "contain" in str(refusal).lower() or "sandbox" in str(refusal).lower() or "backend" in str(refusal).lower(), refusal
    assert not sentinel.exists(), "the replayed command wrote outside the workspace: it ran uncontained"


def test_the_replay_has_no_default_autonomy_so_a_caller_cannot_forget_the_runs_config():
    parameter = inspect.signature(replay_prior_milestone_verifications).parameters["autonomy_cfg"]
    assert parameter.default is inspect.Parameter.empty and parameter.kind is inspect.Parameter.KEYWORD_ONLY


def _driver(workspace, state, engine_config):
    driver = object.__new__(_MilestonePlanDriver)
    driver.workspace_path = workspace
    driver.run_state = state
    driver.total = 1
    driver.engine_config = engine_config
    driver.generation_kwargs = {"approval_callback": _approve}
    return driver


def test_the_milestone_driver_passes_the_engines_autonomy_into_the_replay(tmp_path):
    """The path the run really takes: `_MilestonePlanDriver.run_phase(REPLAY_PRIOR_VERIFICATIONS)` replays under `engine_config.autonomy`."""
    workspace, sentinel, state = _scenario(tmp_path)
    contained = _driver(workspace, state, strict_config(autonomy={"contained_execution_required": True}))
    try:
        asyncio.run(contained.run_phase(TerminalPhase.REPLAY_PRIOR_VERIFICATIONS))
    except Exception:  # no containment backend on this platform: refusing is the contract; the sentinel below is the proof
        pass
    assert not sentinel.exists()


def test_the_driver_without_an_engine_config_does_not_fall_back_to_a_default_autonomy(tmp_path):
    workspace, sentinel, state = _scenario(tmp_path)
    driver = _driver(workspace, state, None)
    with pytest.raises(Exception):
        asyncio.run(driver.run_phase(TerminalPhase.REPLAY_PRIOR_VERIFICATIONS))
    assert not sentinel.exists()


def test_no_other_caller_of_the_replay_exists_outside_the_driver():
    """Structural: the single production caller is the driver's run_phase, so threading the config there covers every replay."""
    source = inspect.getsource(milestones)
    assert source.count("replay_prior_milestone_verifications(") == 1  # D2 S6: the definition only - a compatibility wrapper with no production caller
    assert source.count("replay_prior_milestone_report(") == 3  # the definition, that wrapper's call and the driver's call
    assert "autonomy_cfg=" in inspect.getsource(_MilestonePlanDriver.run_phase)
    assert os.path.basename(milestones.__file__) == "milestones.py"
