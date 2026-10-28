"""Wave 2 Slice A, stage 5: `kriya runs tool-inspect|abandon|retry` is a THIN adapter. It prints the owner's evidence, passes the operator's explicit decision, executes nothing,
and never edits state itself."""
import json
import os

from click.testing import CliRunner

from kriya.cli_commands.runs import runs_group
from kriya.control.persistence import control_state_path, load_control_state, save_control_state
from kriya.control.run_ownership import acquire_run_lock
from kriya.control.state import SUBTASK_STATE_TOOL_FAILED, SUBTASK_STATE_TOOL_STARTED, ControlState


def _seed(tmp_path, states, attempts):
    save_control_state(str(tmp_path), ControlState(schema_version=1, run_id="r1", subtask_states=dict(states), tool_attempts=dict(attempts)))


def _run(tmp_path, *args):
    return CliRunner().invoke(runs_group, [f"tool-{args[0]}", *args[1:], "--workspace", str(tmp_path)], catch_exceptions=False)


def test_inspect_prints_the_evidence_and_exits_action_required_while_something_awaits_an_operator(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED, "old": SUBTASK_STATE_TOOL_STARTED}, {"t1": 2})
    result = _run(tmp_path, "inspect", "--json")
    assert result.exit_code == 1
    entries = {e["subtask_id"]: e for e in json.loads(result.output)["entries"]}
    assert entries["t1"]["attempt"] == 2 and entries["t1"]["allowed_actions"] == ["abandon", "retry"]
    assert entries["old"]["legacy_unversioned"] is True and entries["old"]["allowed_actions"] == ["abandon"]
    assert _run(tmp_path / ".", "inspect").exit_code == 1
    clean = tmp_path / "clean"
    clean.mkdir()
    assert _run(clean, "inspect").exit_code == 0


def test_without_an_explicit_confirmation_nothing_changes_and_the_evidence_and_duplicate_warning_are_shown(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    before = open(control_state_path(str(tmp_path)), "rb").read()
    result = _run(tmp_path, "retry", "t1", "--attempt", "1")
    assert result.exit_code == 1 and "DUPLICATE" in result.output and "DISPOSITION_CONFIRMATION_REQUIRED" in result.output
    assert open(control_state_path(str(tmp_path)), "rb").read() == before


def test_retry_with_confirmation_rearms_and_executes_nothing(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    result = _run(tmp_path, "retry", "t1", "--attempt", "1", "--confirm", "--operator", "someone")
    state = load_control_state(str(tmp_path))
    assert result.exit_code == 0 and "nothing was executed" in result.output
    assert state.subtask_states["t1"] == "pending" and state.tool_attempts == {"t1": 2}
    assert _run(tmp_path, "retry", "t1", "--attempt", "1", "--confirm").exit_code == 0          # idempotent re-run
    assert load_control_state(str(tmp_path)).tool_attempts == {"t1": 2}


def test_abandon_a_legacy_record_needs_the_legacy_flag_and_attempt_and_legacy_are_exclusive(tmp_path):
    _seed(tmp_path, {"old": SUBTASK_STATE_TOOL_STARTED}, {})
    assert _run(tmp_path, "abandon", "old", "--confirm").exit_code == 2                               # neither --attempt nor --legacy
    assert _run(tmp_path, "abandon", "old", "--attempt", "1", "--legacy", "--confirm").exit_code == 2
    assert load_control_state(str(tmp_path)).subtask_states["old"] == SUBTASK_STATE_TOOL_STARTED
    assert _run(tmp_path, "abandon", "old", "--legacy", "--confirm").exit_code == 0
    assert load_control_state(str(tmp_path)).subtask_states["old"] == "tool_abandoned"


def test_a_live_run_holding_the_workspace_exits_run_active_and_changes_nothing(tmp_path):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    from kriya.control.tool_disposition import _canonical
    with acquire_run_lock(_canonical(str(tmp_path)), run_id="live"):
        result = CliRunner().invoke(runs_group, ["tool-abandon", "t1", "--attempt", "1", "--confirm", "--workspace", str(tmp_path)])
    assert result.exit_code == 3 and load_control_state(str(tmp_path)).subtask_states["t1"] == SUBTASK_STATE_TOOL_FAILED
    assert os.path.isfile(control_state_path(str(tmp_path)))
