"""D1 slice 3d: an unresolved TOOL execution is visible to the one run gate, survives retention, and only the persisted TOOL marker (never a MODEL in_progress, never a completed
record) raises it."""
import hashlib
import os
import subprocess
import sys

import pytest

from kriya.control import commit_state
from kriya.control.persistence import control_state_path, list_run_records, save_control_state
from kriya.control.retention import prune_run_state
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.state import SUBTASK_STATE_TOOL_STARTED, ControlState

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_d1_tool_crash_child.py")


@pytest.fixture
def workspace(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return str(tmp_path)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _save(workspace, run_id="r1", **states):
    save_control_state(workspace, ControlState(schema_version=1, run_id=run_id, subtask_states=dict(states)))


def test_only_a_durably_started_tool_marker_raises_the_gate(workspace):
    _save(workspace, t1=SUBTASK_STATE_TOOL_STARTED, s2="pending")
    assessment = commit_state.assess_workspace_commit_state(workspace)
    assert not assessment.safe and commit_state.REASON_UNRESOLVED_TOOL_EXECUTION in assessment.reason_codes
    assert assessment.unresolved_tool_executions == (("r1", ("t1",)),) and not assessment.recoverable
    payload = assessment.to_payload("x")
    assert payload["status"] == "needs_review" and payload["unresolved_tool_executions"] == [{"run_id": "r1", "subtask_ids": ["t1"]}]
    assert "will not retry it and will not start over it" in assessment.describe()


@pytest.mark.parametrize("states", [{"t1": "completed"}, {"t1": "failed"}, {"s1": "in_progress"}, {"t1": "needs_review"}, {}],
                         ids=["completed", "failed", "model-in-progress", "pre-effect-needs-review", "empty"])
def test_nothing_else_raises_the_gate_negative_controls(workspace, states):
    _save(workspace, **states)
    assessment = commit_state.assess_workspace_commit_state(workspace)
    assert assessment.safe and assessment.unresolved_tool_executions == () and "unresolved_tool_executions" not in assessment.to_payload()


def test_a_run_never_blocks_itself_through_its_own_in_flight_marker(workspace):
    _save(workspace, run_id="mine", t1=SUBTASK_STATE_TOOL_STARTED)
    assert commit_state.assess_workspace_commit_state(workspace, exclude_run_ids=["mine"]).safe
    assert not commit_state.assess_workspace_commit_state(workspace, exclude_run_ids=["someone-else"]).safe


def test_a_mutating_run_is_refused_with_the_typed_reason_and_the_record_is_untouched(workspace):
    _save(workspace, t1=SUBTASK_STATE_TOOL_STARTED)
    before = _sha(control_state_path(workspace))
    with pytest.raises(commit_state.UncertainWorkspaceStateError) as caught:
        with begin_mutating_run(workspace):
            pytest.fail("a mutating run started over an unresolved TOOL execution")
    assert commit_state.REASON_UNRESOLVED_TOOL_EXECUTION in caught.value.assessment.reason_codes
    assert _sha(control_state_path(workspace)) == before


def test_retention_never_erases_an_unresolved_tool_execution(tmp_path):
    """RETENTION: after a genuine crash the unresolved STARTED record (and the run record it belongs to) survives the most aggressive cleanup."""
    effects = tmp_path.parent / f"{tmp_path.name}-effects.log"
    done = subprocess.run([sys.executable, CHILD, str(tmp_path), str(effects), "during_effect"], capture_output=True, text=True, timeout=240, check=False)
    assert done.returncode == 137, done.stderr[-800:]
    path = control_state_path(str(tmp_path))
    before, runs_before = _sha(path), sorted(r.run_id for r in list_run_records(str(tmp_path)))
    prune_run_state(str(tmp_path), keep_terminal_runs=0, keep_unreferenced_evidence=0)
    assert _sha(path) == before, "cleanup altered the unresolved record"
    assert sorted(r.run_id for r in list_run_records(str(tmp_path))) == runs_before and runs_before, "cleanup deleted the run that owns the unresolved record"
    assert not commit_state.assess_workspace_commit_state(str(tmp_path)).safe, "after cleanup the unresolved execution must still block"
