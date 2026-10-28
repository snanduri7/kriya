"""DURABILITY-UNCONFIRMED-REASON-CODE-001 (owner decision 2026-10-28): a D1 TOOL disposition that is VISIBLE but whose durability could not be confirmed has its own machine-readable reason code,
``DURABILITY_UNCONFIRMED``; the literal-unreadable codes (``RUN_RECORD_UNREADABLE`` / ``CONTROL_STATE_UNREADABLE``) mean corruption and are not also emitted for it.

MEASURED/TRACED before the change (WAVE2_MALFORMED_STATE_CHARACTERIZATION_2026-10-28): the condition travelled on the readers' unreadable channel and was told apart from corruption ONLY by a message
prefix - ``commit_state.describe()`` and ``kriya runs status|recover`` keyed their operator advice on ``reason.startswith("DURABILITY_UNCONFIRMED")`` - while the machine-readable ``reason_codes`` said
``RUN_RECORD_UNREADABLE`` / ``CONTROL_STATE_UNREADABLE``. The structured fact already existed (``durable_write_error`` on both typed errors); the two policy sites now key on it, and the code is additive in
the existing closed ``commit_state`` table and ``reason_codes`` payload transport (no KUP/UI surface keys on these codes; ``needs_review`` stays the coarse status)."""
import json
import os
import re
from pathlib import Path

import pytest
from _d1_direct_tool_support import make_workspace
from test_d1_durability_consumption import RUN_ID, DirectoryFsyncOutage, _started_direct
from test_d1_tool_lifecycle import Faults
from test_slice_a_tool_disposition import _apply, _seed
from test_w202_direct_tool_disposition import _abandon, _runs, _synthetic_record

from kriya.control import commit_state, recovery
from kriya.control.commit_state import (
    REASON_CONTROL_STATE_UNREADABLE,
    REASON_DURABILITY_UNCONFIRMED,
    REASON_RUN_RECORD_UNREADABLE,
    WorkspaceCommitAssessment,
)
from kriya.control.persistence import (
    ControlStateUnreadableError,
    UnreadableRunRecordError,
    control_state_path,
    inspect_control_state_error,
    run_record_path,
    scan_run_records,
)
from kriya.control.state import SUBTASK_STATE_TOOL_FAILED
from kriya.repository import atomic_write

CORRUPT = b"{{ not json"
KRIYA_SOURCE = Path(__file__).resolve().parents[1] / "kriya"


def _sub(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    return folder


def _durable_run_record(tmp_path, monkeypatch):
    """A direct-tool RunRecord whose `abandoned` disposition is visible but whose directory fsync failed and keeps failing."""
    workspace, _, _ = _started_direct(tmp_path)
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")
    with pytest.raises(atomic_write.DurableWriteError):
        _abandon(workspace, RUN_ID)
    outage.down = True
    return workspace, outage


def _durable_control_state(tmp_path, monkeypatch):
    _seed(tmp_path, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    outage = DirectoryFsyncOutage(monkeypatch)
    Faults(monkeypatch, write=2, step="directory_fsync")
    with pytest.raises(atomic_write.DurableWriteError):
        _apply(tmp_path, "abandon")
    outage.down = True
    return str(tmp_path), outage


def _corrupt_run_record(tmp_path):
    workspace, _, _ = make_workspace(tmp_path)
    _synthetic_record(workspace, RUN_ID, tool_execution=None)
    with open(run_record_path(str(workspace), RUN_ID), "wb") as handle:
        handle.write(CORRUPT)
    return workspace


def _corrupt_control_state(workspace):
    _seed(workspace, {"t1": SUBTASK_STATE_TOOL_FAILED}, {"t1": 1})
    with open(control_state_path(str(workspace)), "wb") as handle:
        handle.write(CORRUPT)


# ------------------------------------------------------------------ the typed errors carry the structured fact
def test_the_readers_typed_errors_carry_the_structured_durability_fact_and_corruption_does_not(tmp_path, monkeypatch):
    workspace, _ = _durable_run_record(_sub(tmp_path, "dur"), monkeypatch)
    durable_scan = scan_run_records(str(workspace)).unreadable
    assert [item.durability_unconfirmed for item in durable_scan] == [True]
    corrupt = _corrupt_run_record(_sub(tmp_path, "bad"))
    assert [item.durability_unconfirmed for item in scan_run_records(str(corrupt)).unreadable] == [False]


# ------------------------------------------------------------------ durability only -> the new code, NOT the unreadable codes
def test_a_durability_unconfirmed_run_record_emits_the_new_code_alone_and_keeps_path_and_reason_detail(tmp_path, monkeypatch):
    workspace, _ = _durable_run_record(tmp_path, monkeypatch)
    assessment = commit_state.assess_workspace_commit_state(str(workspace))
    assert assessment.reason_codes == [REASON_DURABILITY_UNCONFIRMED] == ["DURABILITY_UNCONFIRMED"]
    assert REASON_RUN_RECORD_UNREADABLE not in assessment.reason_codes and not assessment.safe
    payload = assessment.to_payload(run_id="r")
    assert payload["status"] == "needs_review" and payload["quality_gates_passed"] is False
    assert payload["reason_codes"] == ["DURABILITY_UNCONFIRMED"] and payload["recovery_command"] is None
    (entry,) = payload["unreadable_run_records"]
    assert entry["path"].endswith(f"{RUN_ID}.json") and "durability could not be confirmed" in entry["reason"] and entry["durability_unconfirmed"] is True
    json.dumps(payload)                                                   # serialises
    text = assessment.describe()
    assert "durability could not be confirmed" in text and "retry once the storage recovers" in text
    assert "move it" not in text and "kriya runs recover" not in text, "durability uncertainty is not settleable by recovery and moving the file would be wrong"


def test_a_durability_unconfirmed_control_state_emits_the_new_code_alone_and_keeps_path_and_reason_detail(tmp_path, monkeypatch):
    workspace, _ = _durable_control_state(tmp_path, monkeypatch)
    assessment = commit_state.assess_workspace_commit_state(workspace)
    assert assessment.reason_codes == ["DURABILITY_UNCONFIRMED"] and REASON_CONTROL_STATE_UNREADABLE not in assessment.reason_codes and not assessment.safe
    payload = assessment.to_payload()
    assert payload["status"] == "needs_review" and payload["reason_codes"] == ["DURABILITY_UNCONFIRMED"] and payload["recovery_command"] is None
    assert payload["unreadable_control_state"]["path"] == control_state_path(workspace)
    assert "failed at directory_fsync" in payload["unreadable_control_state"]["reason"] and payload["unreadable_control_state"]["durability_unconfirmed"] is True
    text = assessment.describe()
    assert "durability could not be confirmed" in text and "retry once the storage recovers" in text and "move it" not in text
    _state, error = inspect_control_state_error(workspace)
    assert isinstance(error, ControlStateUnreadableError) and error.durability_unconfirmed


# ------------------------------------------------------------------ real corruption -> the existing unreadable codes, NOT the durability code
def test_a_corrupt_run_record_keeps_the_unreadable_code_and_its_remediation(tmp_path):
    workspace = _corrupt_run_record(tmp_path)
    assessment = commit_state.assess_workspace_commit_state(str(workspace))
    assert assessment.reason_codes == [REASON_RUN_RECORD_UNREADABLE] == ["RUN_RECORD_UNREADABLE"]
    assert REASON_DURABILITY_UNCONFIRMED not in assessment.reason_codes
    payload = assessment.to_payload()
    assert payload["recovery_command"] == commit_state.RECOVERY_COMMAND and payload["unreadable_run_records"][0]["durability_unconfirmed"] is False
    assert "unreadable run records" in assessment.describe() and "durability" not in assessment.describe()


def test_a_corrupt_control_state_keeps_the_unreadable_code_and_its_remediation(tmp_path):
    _corrupt_control_state(tmp_path)
    assessment = commit_state.assess_workspace_commit_state(str(tmp_path))
    assert assessment.reason_codes == [REASON_CONTROL_STATE_UNREADABLE] == ["CONTROL_STATE_UNREADABLE"]
    assert REASON_DURABILITY_UNCONFIRMED not in assessment.reason_codes
    assert assessment.to_payload()["unreadable_control_state"]["durability_unconfirmed"] is False
    assert "cannot be read" in assessment.describe() and "move it aside yourself" in assessment.describe() and "durability" not in assessment.describe()


def test_corruption_and_a_durability_condition_together_report_both_codes_and_the_corruption_advice(tmp_path, monkeypatch):
    workspace, _ = _durable_run_record(tmp_path, monkeypatch)
    os.makedirs(os.path.dirname(control_state_path(str(workspace))), exist_ok=True)
    with open(control_state_path(str(workspace)), "wb") as handle:
        handle.write(CORRUPT)
    assessment = commit_state.assess_workspace_commit_state(str(workspace))
    assert REASON_CONTROL_STATE_UNREADABLE in assessment.reason_codes and REASON_DURABILITY_UNCONFIRMED in assessment.reason_codes
    assert REASON_RUN_RECORD_UNREADABLE not in assessment.reason_codes
    assert "move it aside yourself" in assessment.describe()


# ------------------------------------------------------------------ the classification is the structured fact, never the message text
def test_the_reason_text_alone_never_makes_something_a_durability_condition():
    faked = "DURABILITY_UNCONFIRMED: this is only text in a corrupt file's error"
    run_record = WorkspaceCommitAssessment(unreadable_run_records=(("/w/r.json", faked),))
    assert run_record.reason_codes == ["RUN_RECORD_UNREADABLE"] and run_record.recoverable
    control_state = WorkspaceCommitAssessment(unreadable_control_state=("/w/c.json", faked))
    assert control_state.reason_codes == ["CONTROL_STATE_UNREADABLE"] and "move it aside yourself" in control_state.describe()
    assert not control_state.to_payload()["unreadable_control_state"]["durability_unconfirmed"]


def test_the_structured_fact_alone_makes_a_durability_condition_whatever_the_detail_text_says():
    run_record = WorkspaceCommitAssessment(unreadable_run_records=(("/w/r.json", "any wording"),), durability_unconfirmed_run_records=("/w/r.json",))
    assert run_record.reason_codes == ["DURABILITY_UNCONFIRMED"] and not run_record.recoverable
    control_state = WorkspaceCommitAssessment(unreadable_control_state=("/w/c.json", "any wording"), control_state_durability_unconfirmed=True)
    assert control_state.reason_codes == ["DURABILITY_UNCONFIRMED"] and "retry once the storage recovers" in control_state.describe()


def test_the_durability_advice_needs_the_durability_fact_it_is_not_the_fallback_for_a_state_with_no_other_explanation():
    """Mutation M08 (run 1 survivor): the durability remediation must not be what an assessment says merely because nothing else matched."""
    text = WorkspaceCommitAssessment().describe()
    assert "durability" not in text and "retry once the storage recovers" not in text


def test_no_policy_in_the_source_parses_the_durability_prefix_out_of_message_text():
    offenders = []
    for path in KRIYA_SOURCE.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "DURABILITY_UNCONFIRMED" in line and re.search(r"startswith\(|\.find\(|\.index\(|re\.(search|match)\(|\bin\s+\w*(reason|message|text)\b", line):
                offenders.append(f"{path.relative_to(KRIYA_SOURCE.parent)}:{number}: {line.strip()}")
    assert offenders == []


# ------------------------------------------------------------------ `kriya runs status|recover`: the same structured fact, the right remediation for each
def test_runs_status_json_and_text_tell_a_storage_condition_from_corruption(tmp_path, monkeypatch):
    workspace, _ = _durable_run_record(_sub(tmp_path, "dur"), monkeypatch)
    assessed = recovery.assess_recovery(str(workspace))
    assert [item["durability_unconfirmed"] for item in assessed.to_dict()["unreadable_records"]] == [True]
    assert assessed.status == recovery.STATUS_MANUAL_ACTION_REQUIRED, "the coarse blocking status is unchanged"
    shown = json.loads(_runs(workspace, "status", "--json").output)
    assert shown["status"] == recovery.STATUS_MANUAL_ACTION_REQUIRED and shown["unreadable_records"][0]["durability_unconfirmed"] is True
    text = _runs(workspace, "status").output
    assert "storage condition, not corruption" in text and "move it out" not in text
    corrupt = _corrupt_run_record(_sub(tmp_path, "bad"))
    bad = json.loads(_runs(corrupt, "status", "--json").output)
    assert bad["status"] == recovery.STATUS_MANUAL_ACTION_REQUIRED and bad["unreadable_records"][0]["durability_unconfirmed"] is False
    bad_text = _runs(corrupt, "status").output
    assert "move it out of .kriya/control/runs/" in bad_text and "storage condition" not in bad_text


def test_runs_status_does_not_take_a_corrupt_records_faked_prefix_for_a_durability_condition(tmp_path):
    """The old advice site decided from reason.startswith(prefix); a RecoveryAssessment whose reason text merely starts with it is still corruption advice."""
    workspace = str(tmp_path)
    faked = recovery.RecoveryAssessment(workspace_path=workspace, unreadable_records=((os.path.join(workspace, "r.json"), "DURABILITY_UNCONFIRMED: text only"),))
    from click.testing import CliRunner

    from kriya.cli_commands import runs as runs_module
    result = CliRunner().invoke(_print_command(runs_module, faked), [])
    assert "move it out of .kriya/control/runs/" in result.output and "storage condition" not in result.output


def _print_command(runs_module, assessment):
    import click

    @click.command()
    def show():
        runs_module._print_recovery_assessment(assessment)  # pylint: disable=protected-access
    return show


def test_the_run_record_error_class_still_exposes_reason_detail():
    error = UnreadableRunRecordError("/w/r.json", "DURABILITY_UNCONFIRMED: detail", durable_write_error=atomic_write.DurableWriteError("x", "directory_fsync", OSError("io")))
    assert error.durability_unconfirmed and "detail" in error.reason
    assert not UnreadableRunRecordError("/w/r.json", "DURABILITY_UNCONFIRMED: only text").durability_unconfirmed
