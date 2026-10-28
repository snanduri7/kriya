"""DURABLE-DISPOSITION-DIRECTORY-FSYNC-VISIBLE-BEFORE-DURABLE-001 (owner decision 2026-10-28, P2): a D1 state that CLEARS an execution-safety gate is authoritative only after its parent
directory's durability has been confirmed by the reader that is about to consume it.

The writer (kriya/repository/atomic_write.py) reports a failure at ``directory_fsync`` AFTER the atomic replace, so the new bytes are visible and not known to be durable. Before this
contract every independent reader (the mutating-run gate, recovery, the resume classification, the operator commands) read those visible bytes as resolved. Now the two strict readers
(kriya/control/persistence.py ``_read_run_record`` / ``_read_control_state_strict``) confirm durability of a document that carries a gate-clearing D1 TOOL state; a failed
confirmation is a typed DURABILITY_UNCONFIRMED refusal on the readers' existing fail-closed channel, never a pass. The confirmation is idempotent: the first time it succeeds the visible
state is the durable one, and nothing is rewritten. A BLOCKING state (STARTED / FAILED) is never confirmed - an operator must be able to inspect it while storage misbehaves.

The outage is injected at the real OS call (``os.fsync`` on a directory descriptor), exactly like the writer-side Faults of tests/test_d1_tool_lifecycle.py."""
import errno
import json
import os
import stat

import pytest
from _d1_direct_tool_support import TOOL, execute_tool, lines, make_workspace
from test_d1_tool_lifecycle import Faults
from test_slice_a_tool_disposition import _apply, _seed
from test_w202_direct_tool_disposition import _abandon, _bytes, _direct_audit, _gate_blocks, _runs, _synthetic_record

from kriya.control import commit_state, recovery
from kriya.control import tool_disposition as td
from kriya.control.persistence import (
    ControlStateUnreadableError,
    UnreadableRunRecordError,
    control_state_path,
    inspect_control_state,
    load_control_state,
    load_run_record,
    save_control_state,
    save_run_record,
    scan_run_records,
)
from kriya.control.run_record import RunLifecycle
from kriya.control.state import SUBTASK_STATE_TOOL_FAILED, SUBTASK_STATE_TOOL_STARTED, ControlState
from kriya.repository import atomic_write

UNCONFIRMED = "DURABILITY_UNCONFIRMED"
RUN_ID = "20250101T000000-aaaaaaaa"


class DirectoryFsyncOutage:
    """A persistent parent-directory fsync outage, switched by ``down`` (and ``files_down`` for a file fsync); counts every directory fsync attempted while it is installed."""

    def __init__(self, monkeypatch):
        self.down = False
        self.files_down = False
        self.directory_attempts = 0
        real_fsync = os.fsync

        def fsync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                self.directory_attempts += 1
                if self.down:
                    raise OSError(errno.EIO, "injected persistent directory fsync outage")
            elif self.files_down:
                raise OSError(errno.EIO, "injected persistent file fsync outage")
            return real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fsync)


def _started_direct(tmp_path):
    workspace, plugins, effects = make_workspace(tmp_path)
    _synthetic_record(workspace, RUN_ID, tool_execution={"tool": TOOL, "state": "started"})
    return workspace, plugins, effects


def _unconfirmed_records(workspace):
    return [path for path, reason in commit_state.assess_workspace_commit_state(str(workspace)).unreadable_run_records if UNCONFIRMED in reason]


# ------------------------------------------------------------------ 1-3 DIRECT: the writer's directory_fsync failure, then the independent reader
def test_replace_succeeds_directory_fsync_fails_the_writer_reports_and_an_independent_reader_cannot_clear_the_gate(tmp_path, monkeypatch):
    workspace, plugins, effects = _started_direct(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")                 # durable write 1 = the audit decision, 2 = the RunRecord disposition
    with pytest.raises(atomic_write.DurableWriteError) as raised:
        _abandon(workspace, RUN_ID)
    assert raised.value.step == "directory_fsync", "the writer must still report the failure"
    assert json.loads(_bytes(workspace, RUN_ID))["tool_execution"]["state"] == "abandoned", "precondition: the replace happened, the bytes are visible"
    outage.down = True
    assessment = commit_state.assess_workspace_commit_state(str(workspace))
    assert not assessment.safe, "a visible-but-unconfirmed `abandoned` cleared the gate"
    assert _unconfirmed_records(workspace), assessment.describe()
    assert "move it" not in assessment.describe(), "a transient durability failure must never advise moving the evidence away"
    assert recovery.assess_recovery(str(workspace)).status != recovery.STATUS_CLEAN
    result = execute_tool(workspace, plugins, effects)                  # nothing may run on the strength of that state
    assert result.exit_code == 1 and lines(effects) == [], "a tool ran although the gate-clearing state was not confirmed durable"


def test_the_same_visible_state_becomes_authoritative_when_a_later_confirmation_succeeds_and_nothing_is_rewritten_or_duplicated(tmp_path, monkeypatch):
    workspace, plugins, effects = _started_direct(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")
    with pytest.raises(atomic_write.DurableWriteError):
        _abandon(workspace, RUN_ID)
    outage.down = True
    assert _gate_blocks(workspace)
    visible = _bytes(workspace, RUN_ID)
    outage.down = False                                                  # the directory can be synced now
    assert not _gate_blocks(workspace), "a confirmed visible `abandoned` must clear the gate"
    assert _bytes(workspace, RUN_ID) == visible, "confirming must not rewrite the record"
    assert len(_direct_audit(workspace)) == 1 and lines(effects) == []
    rerun = _abandon(workspace, RUN_ID)                                   # the idempotent rerun converges with no second audit record and no second write
    assert rerun.status == td.STATUS_ALREADY_APPLIED and len(_direct_audit(workspace)) == 1 and _bytes(workspace, RUN_ID) == visible


def test_repeated_confirmation_failure_keeps_blocking_and_changes_no_byte(tmp_path, monkeypatch):
    workspace, _, _ = _started_direct(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")
    with pytest.raises(atomic_write.DurableWriteError):
        _abandon(workspace, RUN_ID)
    visible = _bytes(workspace, RUN_ID)
    outage.down = True
    for _ in range(3):
        assert _gate_blocks(workspace) and _unconfirmed_records(workspace)
        assert _bytes(workspace, RUN_ID) == visible
    with pytest.raises(atomic_write.DurableWriteError) as raised:         # the operator rerun is a durable-write failure (exit 4 in the CLI), never a pass and never a refusal to "move it aside"
        _abandon(workspace, RUN_ID)
    assert raised.value.step == "directory_fsync"
    assert _gate_blocks(workspace) and _bytes(workspace, RUN_ID) == visible


# ------------------------------------------------------------------ 4-5 RELOAD, ALREADY-DURABLE, BLOCKING STATES
@pytest.mark.parametrize("state", ["completed", "failed", "abandoned"])
def test_every_resolving_direct_state_is_confirmed_before_it_is_consumed_and_a_durable_one_is_normally_usable(tmp_path, monkeypatch, state):
    workspace, _, _ = make_workspace(tmp_path)
    _synthetic_record(workspace, RUN_ID, tool_execution={"tool": TOOL, "state": state})
    outage = DirectoryFsyncOutage(monkeypatch)
    assert load_run_record(str(workspace), RUN_ID).tool_execution["state"] == state and not _gate_blocks(workspace), "an already-durable terminal state must stay usable"
    assert outage.directory_attempts >= 1, "a resolving state was consumed without confirming its parent directory"
    outage.down = True
    with pytest.raises(UnreadableRunRecordError) as raised:
        load_run_record(str(workspace), RUN_ID)
    assert UNCONFIRMED in raised.value.reason and isinstance(raised.value.durable_write_error, atomic_write.DurableWriteError)
    assert _gate_blocks(workspace) and _unconfirmed_records(workspace)
    assert scan_run_records(str(workspace)).records == [], "an unconfirmed record must not appear among the readable ones"


def test_a_failed_file_fsync_during_confirmation_is_also_unconfirmed_not_swallowed(tmp_path, monkeypatch):
    workspace, _, _ = make_workspace(tmp_path)
    _synthetic_record(workspace, RUN_ID, tool_execution={"tool": TOOL, "state": "abandoned"})
    outage = DirectoryFsyncOutage(monkeypatch)
    outage.files_down = True
    with pytest.raises(UnreadableRunRecordError) as raised:
        load_run_record(str(workspace), RUN_ID)
    assert UNCONFIRMED in raised.value.reason and raised.value.durable_write_error.step == "file_fsync" and _gate_blocks(workspace)
    outage.files_down = False
    assert not _gate_blocks(workspace)


def test_a_blocking_started_state_is_never_confirmed_so_the_operator_can_still_inspect_it_while_storage_misbehaves(tmp_path, monkeypatch):
    workspace, _, _ = _started_direct(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    outage.down = True
    report = td.inspect_direct_run(str(workspace), RUN_ID)
    assert report.entry is not None and report.entry.execution_state == "started" and report.entry.unresolved and report.reason_code is None
    assert _gate_blocks(workspace) and not _unconfirmed_records(workspace)
    assert outage.directory_attempts == 0, "reading a blocking state must not depend on the directory fsync"


def test_a_record_without_a_tool_execution_is_never_confirmed(tmp_path, monkeypatch):
    workspace, _, _ = make_workspace(tmp_path)
    _synthetic_record(workspace, RUN_ID, tool_execution=None)
    outage = DirectoryFsyncOutage(monkeypatch)
    outage.down = True
    assert load_run_record(str(workspace), RUN_ID) is not None and outage.directory_attempts == 0


def test_a_direct_completed_whose_directory_fsync_failed_does_not_clear_the_gate_until_confirmed_and_never_runs_the_tool_again(tmp_path, monkeypatch):
    workspace, plugins, effects = make_workspace(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")                 # write 1 = STARTED, 2 = COMPLETED
    first = execute_tool(workspace, plugins, effects)
    assert first.exit_code == 1 and "[NEEDS REVIEW]" in first.output
    outage.down = True
    assert _gate_blocks(workspace)
    again = execute_tool(workspace, plugins, effects)
    assert again.exit_code == 1 and lines(effects).count("EFFECT_BEGIN") == 1, "the tool ran again on the strength of an unconfirmed `completed`"
    outage.down = False
    assert not _gate_blocks(workspace) and lines(effects).count("EFFECT_BEGIN") == 1


# ------------------------------------------------------------------ 6 PLAN TOOL: the control state
@pytest.mark.parametrize("action", ["abandon", "retry"])
def test_plan_tool_disposition_whose_directory_fsync_failed_cannot_clear_the_gate_until_confirmed(tmp_path, monkeypatch, action):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")                 # write 1 = the ledger record, 2 = the control state
    with pytest.raises(atomic_write.DurableWriteError) as raised:
        _apply(tmp_path, action)
    assert raised.value.step == "directory_fsync"
    visible = open(control_state_path(str(tmp_path)), "rb").read()
    assert SUBTASK_STATE_TOOL_FAILED not in load_control_state(str(tmp_path)).subtask_states.values(), "precondition: the disposition is visible"
    outage.down = True
    with pytest.raises(ControlStateUnreadableError) as unreadable:
        load_control_state(str(tmp_path))
    assert UNCONFIRMED in unreadable.value.reason and "move it aside" not in str(unreadable.value)
    assert isinstance(unreadable.value.durable_write_error, atomic_write.DurableWriteError)
    assessment = commit_state.assess_workspace_commit_state(str(tmp_path))
    assert not assessment.safe and assessment.unreadable_control_state is not None and UNCONFIRMED in assessment.unreadable_control_state[1]
    assert "move it aside" not in assessment.describe()
    for _ in range(2):                                                    # repeated failure stays blocking, nothing is rewritten
        assert _gate_blocks(tmp_path) and open(control_state_path(str(tmp_path)), "rb").read() == visible
    with pytest.raises(atomic_write.DurableWriteError) as rerun:          # the operator rerun is the durable-write failure it is, not a "corrupt state" refusal
        _apply(tmp_path, action)
    assert rerun.value.step == "directory_fsync"
    cli = _runs(tmp_path, "tool-retry" if action == "retry" else "tool-abandon", "t1", "--attempt", "1", "--confirm")
    assert cli.exit_code == 4 and "DISPOSITION_DURABLE_WRITE_FAILED" in cli.output and "Traceback" not in cli.output
    assert open(control_state_path(str(tmp_path)), "rb").read() == visible
    outage.down = False
    assert not _gate_blocks(tmp_path), "the confirmed visible disposition must clear the gate"
    assert open(control_state_path(str(tmp_path)), "rb").read() == visible
    assert _apply(tmp_path, action).status == td.STATUS_ALREADY_APPLIED


def test_plan_tool_completed_is_confirmed_and_a_legacy_or_blocking_state_is_not(tmp_path, monkeypatch):
    outage = DirectoryFsyncOutage(monkeypatch)
    _seed(tmp_path, {"t1": "completed"}, {"t1": 1})
    assert load_control_state(str(tmp_path)) is not None and outage.directory_attempts >= 1
    outage.down = True
    with pytest.raises(ControlStateUnreadableError):
        load_control_state(str(tmp_path))
    state, unreadable = inspect_control_state(str(tmp_path))
    assert state is None and unreadable is not None and UNCONFIRMED in unreadable[1]
    for states, attempts in (({"t1": SUBTASK_STATE_TOOL_STARTED}, {"t1": 1}), ({"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 2}), ({"t1": "completed"}, {})):
        outage.down = False
        _seed(tmp_path, states, attempts)                                  # the legacy `completed` carries no attempt identity: nothing newly authoritative
        outage.down = True
        loaded = load_control_state(str(tmp_path))                         # must not raise: a blocking or legacy state is never confirmed
        assert loaded is not None and loaded.subtask_states == states


# ------------------------------------------------------------------ the operator commands: a durable-write failure (exit 4), not a refusal that tells the operator to move evidence away
def test_the_operator_rerun_under_a_persistent_outage_is_a_typed_durable_write_failure(tmp_path, monkeypatch):
    workspace, _, _ = _started_direct(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")
    first = _runs(workspace, "tool-abandon", "--run", RUN_ID, "--confirm")
    assert first.exit_code == 4 and "DISPOSITION_DURABLE_WRITE_FAILED" in first.output
    outage.down = True
    again = _runs(workspace, "tool-abandon", "--run", RUN_ID, "--confirm")
    assert again.exit_code == 4 and "DISPOSITION_DURABLE_WRITE_FAILED" in again.output and "directory_fsync" in again.output and "Traceback" not in again.output
    outage.down = False
    done = _runs(workspace, "tool-abandon", "--run", RUN_ID, "--confirm", "--json")
    assert done.exit_code == 0 and json.loads(done.output)["status"] == "already_applied" and not _gate_blocks(workspace)


def test_the_operator_output_for_an_unconfirmed_record_names_a_storage_condition_and_never_advises_moving_the_evidence(tmp_path, monkeypatch):
    workspace, _, _ = _started_direct(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")
    with pytest.raises(atomic_write.DurableWriteError):
        _abandon(workspace, RUN_ID)
    outage.down = True
    visible = _bytes(workspace, RUN_ID)
    for command in ("status", "recover"):
        result = _runs(workspace, command)
        assert result.exit_code != 0, f"`runs {command}` reported success while the gate-clearing state was unconfirmed:\n{result.output}"
        assert UNCONFIRMED in result.output and "storage condition" in result.output and "move it out" not in result.output, result.output
    assert _bytes(workspace, RUN_ID) == visible, "`runs recover` altered an unconfirmed record"


def test_a_writer_never_depends_on_confirming_the_resolving_state_it_replaces(tmp_path, monkeypatch):
    """Confirmation is a READER's precondition for relying on a state. A writer replaces the bytes (and its own write fsyncs the directory), so an outage must not be able to make a
    plain save fail merely because the previous resolving state could not be confirmed."""
    outage = DirectoryFsyncOutage(monkeypatch)
    _seed(tmp_path, {"t1": "completed"}, {"t1": 1})
    outage.down = True
    save_control_state(str(tmp_path), ControlState(schema_version=1, run_id="r1", subtask_states={"t1": "completed", "t2": "pending"}, tool_attempts={"t1": 1}, current_plan_hash="ph"))
    outage.down = False
    assert load_control_state(str(tmp_path)).subtask_states == {"t1": "completed", "t2": "pending"}
    (tmp_path / "direct").mkdir()
    workspace, _, _ = make_workspace(tmp_path / "direct")
    _synthetic_record(workspace, RUN_ID, tool_execution={"tool": TOOL, "state": "completed"})
    record = load_run_record(str(workspace), RUN_ID)
    outage.down = True
    save_run_record(str(workspace), record.transition(RunLifecycle.PLANNING), expected_revision=record.revision)
    outage.down = False
    assert load_run_record(str(workspace), RUN_ID).lifecycle_state == RunLifecycle.PLANNING
