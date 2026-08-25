"""MA5.1: ControlState persistence (kriya/control/persistence.py) - the
save/load round trip, fail-closed behavior on a missing/corrupt file, and
that saving really does go through AuthorizedFileWriter (MA4.16), not a
raw write bypass."""

import json
import os
import tempfile

import pytest

from kriya.control.persistence import (
    approved_plan_path,
    control_state_path,
    load_approved_plan,
    load_control_state,
    save_approved_plan,
    save_control_state,
)
from kriya.control.state import ControlState
from kriya.policy.errors import PolicyDeniedError


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_load_control_state_is_none_when_never_saved(workspace):
    assert load_control_state(workspace) is None


def test_save_and_load_round_trips(workspace):
    state = ControlState.new(run_id="run-1", milestone_group_id="grp-1").with_updates(
        current_milestone_id="M1", base_commit="abc123",
    )
    save_control_state(workspace, state)
    loaded = load_control_state(workspace)
    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert loaded.current_milestone_id == "M1"
    assert loaded.base_commit == "abc123"


def test_save_creates_the_control_directory(workspace):
    state = ControlState.new(run_id="run-1")
    save_control_state(workspace, state)
    path = control_state_path(workspace)
    assert os.path.isfile(path)
    assert os.path.dirname(path) == os.path.join(workspace, ".kriya", "control")


def test_save_overwrites_a_prior_state(workspace):
    save_control_state(workspace, ControlState.new(run_id="run-1"))
    save_control_state(workspace, ControlState.new(run_id="run-1").with_updates(current_milestone_id="M2"))
    loaded = load_control_state(workspace)
    assert loaded.current_milestone_id == "M2"


def test_load_control_state_fails_closed_on_corrupt_json(workspace):
    path = control_state_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{not valid json")
    assert load_control_state(workspace) is None


def test_save_goes_through_authorized_file_writer_and_denies_outside_workspace(workspace, monkeypatch):
    """Confirms this module doesn't bypass AuthorizedFileWriter - forcing
    is_within_scope to False (as if control_state_path somehow resolved
    outside the workspace) must raise PolicyDeniedError, not silently
    write anyway."""
    import kriya.policy.filesystem as fs_mod

    monkeypatch.setattr(fs_mod, "is_within_scope", lambda scope, target: False)
    with pytest.raises(PolicyDeniedError):
        save_control_state(workspace, ControlState.new(run_id="run-1"))
    monkeypatch.undo()


def test_persisted_state_is_stable_json(workspace):
    state = ControlState.new(run_id="run-1")
    save_control_state(workspace, state)
    with open(control_state_path(workspace)) as f:
        raw = json.load(f)
    assert raw["run_id"] == "run-1"
    assert raw["schema_version"] == state.schema_version


def test_approved_plan_and_stage_states_round_trip_with_workspace_ownership(workspace):
    payload = {
        "schema_version": 1,
        "plan_id": "run-1",
        "approval_status": "approved",
        "lifecycle_state": "in_progress",
        "stage_order": ["s1", "s2"],
        "stage_states": {"s1": "completed", "s2": "in_progress"},
        "plan": {"plan_id": "run-1", "subtasks": []},
    }
    save_approved_plan(workspace, "run-1", payload)

    loaded = load_approved_plan(workspace, "run-1")
    assert loaded is not None
    assert loaded["approval_status"] == "approved"
    assert loaded["stage_states"] == {"s1": "completed", "s2": "in_progress"}
    assert loaded["_workspace"]["workspace_id"]
    assert os.path.isfile(approved_plan_path(workspace, "run-1"))


def test_approved_plan_path_rejects_unsafe_plan_id(workspace):
    with pytest.raises(ValueError):
        approved_plan_path(workspace, "../outside")
