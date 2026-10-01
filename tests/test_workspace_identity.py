import json

import pytest

from kriya.control.decisions import load_decision_ledger
from kriya.control.persistence import load_control_state, save_control_state
from kriya.control.state import ControlState
from kriya.control.workspace_identity import WorkspaceOwnershipError, workspace_identity
from kriya.workflow.workflow_controller import _migrate_legacy_controller_ownership


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


def test_authoritative_migration_stamps_ownerless_state_and_records_decision(tmp_path):
    control_dir = tmp_path / ".kriya" / "control"
    control_dir.mkdir(parents=True)
    state_path = control_dir / "state.json"
    state_path.write_text(json.dumps(_state().to_dict()))

    migrated = _migrate_legacy_controller_ownership(str(tmp_path), "new-run")

    assert migrated == ("control_state",)
    payload = json.loads(state_path.read_text())
    assert payload["_workspace"]["workspace_id"] == workspace_identity(str(tmp_path))
    decisions = load_decision_ledger(str(tmp_path)).all()
    assert decisions[-1].type == "legacy_workspace_ownership_migrated"
    assert decisions[-1].fields["stores"] == ["control_state"]
