import json

import pytest

from kriya.control.persistence import load_control_state, save_control_state
from kriya.control.state import ControlState
from kriya.control.workspace_identity import WorkspaceOwnershipError, workspace_identity


def _state():
    return ControlState.new(run_id="run")


def test_control_state_is_owned_by_saving_workspace(tmp_path):
    save_control_state(str(tmp_path), _state())
    payload = json.loads((tmp_path / ".kriya/control/state.json").read_text())
    assert payload["_workspace"]["workspace_id"] == workspace_identity(str(tmp_path))


def test_control_state_ownership_mismatch_fails_closed(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    save_control_state(str(first), _state())
    target = second / ".kriya/control"
    target.mkdir(parents=True)
    target.joinpath("state.json").write_bytes(first.joinpath(".kriya/control/state.json").read_bytes())
    with pytest.raises(WorkspaceOwnershipError):
        load_control_state(str(second))
