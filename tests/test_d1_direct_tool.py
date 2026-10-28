"""D1 slice 4: `kriya tools execute` - the direct, operator-authored tool execution - follows the same lifecycle: durable STARTED before the effect, durable COMPLETED before any
success is reported, an unresolved record blocks the next run and survives retention, and a crash in any window is needs_review (never retried)."""
import glob
import json
import os
import subprocess
import sys

import pytest
from _d1_direct_tool_support import RESULT_TEXT, TOOL, execute_tool, lines, make_workspace
from test_d1_tool_lifecycle import Faults

from kriya.control import commit_state
from kriya.control.persistence import list_run_records
from kriya.control.retention import prune_run_state
from kriya.repository import atomic_write

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_d1_direct_tool_child.py")


def _record_files(workspace):
    return sorted(glob.glob(os.path.join(str(workspace), ".kriya", "control", "runs", "*.json")))


def _tool_executions(workspace):
    return [json.load(open(path)).get("tool_execution") for path in _record_files(workspace)]


def test_a_direct_tool_is_durably_started_before_the_effect_and_durably_completed_before_the_result_is_reported(tmp_path):
    workspace, plugins, effects = make_workspace(tmp_path)
    result = execute_tool(workspace, plugins, effects)
    assert result.exit_code == 0 and RESULT_TEXT in result.output
    log = lines(effects)
    assert json.loads(log[0].removeprefix("ON_DISK_AT_EFFECT=")) == [{"tool": TOOL, "state": "started"}], "STARTED was not on disk when the effect ran"
    assert log[1:] == ["EFFECT_BEGIN", "EFFECT_DONE"]
    assert _tool_executions(workspace) == [{"tool": TOOL, "state": "completed"}]


@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
def test_a_started_persistence_failure_at_any_step_means_the_tool_never_runs(tmp_path, monkeypatch, step):
    workspace, plugins, effects = make_workspace(tmp_path)
    Faults(monkeypatch, write=1, step=step)
    result = execute_tool(workspace, plugins, effects)
    assert result.exit_code == 1 and "[TOOL NOT STARTED]" in result.output and RESULT_TEXT not in result.output
    assert lines(effects) == [], "the side effect ran although STARTED was not durable"


@pytest.mark.parametrize("step", atomic_write.DURABLE_WRITE_STEPS)
def test_a_completed_persistence_failure_reports_no_success_and_blocks_a_retry(tmp_path, monkeypatch, step):
    workspace, plugins, effects = make_workspace(tmp_path)
    Faults(monkeypatch, write=2, step=step)
    result = execute_tool(workspace, plugins, effects)
    assert result.exit_code == 1 and "[NEEDS REVIEW]" in result.output
    assert RESULT_TEXT not in result.output, "a result was reported before the completion was durable"
    assert [entry for entry in lines(effects) if entry in ("EFFECT_BEGIN", "EFFECT_DONE")] == ["EFFECT_BEGIN", "EFFECT_DONE"]
    monkeypatch.undo()
    if step == "directory_fsync":
        # The replace happened, so the record that is visible says "completed" - which is TRUE (the effect did complete) - and only its durability is unknown. Nothing was reported as
        # success and nothing ran again. The fault is undone here, so a later reader's durability confirmation succeeds and the visible record is authoritative; while the outage lasts
        # that reader stays blocking (tests/test_d1_durability_consumption.py).
        assert lines(effects).count("EFFECT_BEGIN") == 1
        return
    assert any(state and state.get("state") == "started" for state in _tool_executions(workspace)), "the record must stay unresolved"
    again = execute_tool(workspace, plugins, effects)                  # the operator tries again: refused, nothing runs
    assert again.exit_code == 1 and "Recovery Required" in again.output and "unresolved TOOL execution" in again.output
    assert lines(effects).count("EFFECT_BEGIN") == 1, "the unresolved tool execution was retried"


@pytest.mark.parametrize("window", ["before_effect", "during_effect", "after_effect_before_completed"])
def test_a_crash_in_any_window_is_needs_review_and_the_next_run_never_runs_the_tool(tmp_path, window):
    workspace, plugins, effects = make_workspace(tmp_path)
    env = {**os.environ, "D1_EFFECT_LOG": str(effects)}
    done = subprocess.run([sys.executable, CHILD, str(tmp_path), window], capture_output=True, text=True, timeout=240, check=False, env=env)
    assert done.returncode == 137, f"the child did not die at the window: rc={done.returncode}\n{done.stderr[-1500:]}"
    assert _tool_executions(workspace) == [{"tool": TOOL, "state": "started"}], "the unresolved STARTED record must be what survived the crash"
    before = [open(path, "rb").read() for path in _record_files(workspace)]
    effects_before = lines(effects)
    again = execute_tool(workspace, plugins, effects)                  # a fresh process state: only the disk remembers
    assert again.exit_code == 1 and "unresolved TOOL execution" in again.output and RESULT_TEXT not in again.output
    assert lines(effects) == effects_before, "the tool ran again after a crash"
    assert [open(path, "rb").read() for path in _record_files(workspace)] == before, "the unresolved record was altered by the refused run"
    assessment = commit_state.assess_workspace_commit_state(str(workspace))
    assert commit_state.REASON_UNRESOLVED_TOOL_EXECUTION in assessment.reason_codes


def test_retention_never_erases_an_unresolved_direct_tool_execution_even_when_the_record_is_terminal(tmp_path, monkeypatch):
    """A crash leaves the record non-terminal (retention already protects those). The case that needs the D1 rule is a TERMINAL record that still says "started": the COMPLETED write
    fails, the run exits, and the coordinator terminates the record as FAILURE - the unresolved marker must still keep it from the most aggressive prune."""
    workspace, plugins, effects = make_workspace(tmp_path)
    Faults(monkeypatch, write=2, step="replace")
    result = execute_tool(workspace, plugins, effects)
    monkeypatch.undo()
    assert result.exit_code == 1 and "[NEEDS REVIEW]" in result.output
    [record] = list_run_records(str(workspace))
    assert record.terminal and record.unresolved_tool_execution == TOOL, "precondition: a terminal record that still says started"
    prune_run_state(str(workspace), keep_terminal_runs=0, keep_unreferenced_evidence=0)
    assert [r.run_id for r in list_run_records(str(workspace))] == [record.run_id], "cleanup deleted the unresolved record"
    assert not commit_state.assess_workspace_commit_state(str(workspace)).safe, "after cleanup the unresolved execution must still block"


def test_a_tool_that_FAILED_is_a_known_outcome_not_an_unresolved_execution_negative_control(tmp_path):
    from kriya.control.run_coordinator import begin_mutating_run, transition_mutating_run
    from kriya.control.run_record import RunLifecycle
    workspace, _, _ = make_workspace(tmp_path)
    with begin_mutating_run(str(workspace)) as context:
        transition_mutating_run(context, RunLifecycle.RUNNING, durable=True, tool_execution={"tool": TOOL, "state": "started"})
        transition_mutating_run(context, RunLifecycle.FAILURE, commit_result="DIRECT_TOOL_EXECUTION", tool_execution={"tool": TOOL, "state": "failed"})
    assert commit_state.assess_workspace_commit_state(str(workspace)).safe, "a recorded failure must not block the operator's next run"
