"""RDCG ``_load_json_document`` caller 2 (owner decision 2026-10-28): run-state retention never prunes while the persisted ControlState cannot be read.

MEASURED before the fix (WAVE2_MALFORMED_STATE_CHARACTERIZATION_2026-10-28/out_load_json_document.txt): ``load_control_state_run_reference`` reads any parse failure as "no reference" (``_load_json_document``
logs "treating as absent"), so with a malformed or non-UTF-8 ControlState ``workspace_run_references`` was empty and ``prune_run_state`` listed the run record that control state references for deletion -
exactly what an ABSENT control state does. The ControlState is a documented reference source (enforce resume, retention), and retention already refuses while any run record is unreadable because "its
references are unknown"; an unreadable control state gets the same treatment. The control state itself is never touched."""
import os

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.context.workspace_identity import workspace_identity
from kriya.control.persistence import control_state_path, run_record_path, save_run_record
from kriya.control.retention import prune_run_state, workspace_run_references
from kriya.control.run_record import RunLifecycle, RunRecord

RUN_ID = "20250101T000000-aaaaaaaa"
UNREADABLE = {
    "ascii_garbage": b"{{ not json",
    "empty": b"",
    "invalid_utf8": b'{"schema_version": 1}\xff\xfe',
    "truncated_multibyte_sequence": b'{"note": "\xe2\x82',
    "json_array": b"[]",
}


def _workspace_with_a_terminal_run(tmp_path) -> str:
    workspace = str(tmp_path)
    record = RunRecord.new(RUN_ID, workspace_identity(workspace), None, None)
    save_run_record(workspace, record, expected_revision=None)
    save_run_record(workspace, record.transition(RunLifecycle.FAILURE, terminal_status="FAILURE", commit_result="NOT_COMMITTED"), expected_revision=1)
    return workspace


def _bytes_of(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _put_control_state(workspace: str, data: bytes) -> str:
    path = control_state_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def test_control_with_no_control_state_a_terminal_record_beyond_the_keep_count_is_pruned(tmp_path):
    """The fixture is not vacuous: an ABSENT control state references nothing, so retention may prune the record."""
    workspace = _workspace_with_a_terminal_run(tmp_path)
    report = prune_run_state(workspace, keep_terminal_runs=0, dry_run=True)
    assert list(report.pruned_run_ids) == [RUN_ID] and report.skipped_reason is None


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
def test_an_unreadable_control_state_blocks_pruning_and_nothing_is_deleted_or_rewritten(tmp_path, data):
    workspace = _workspace_with_a_terminal_run(tmp_path)
    state_path = _put_control_state(workspace, data)
    record_bytes = _bytes_of(run_record_path(workspace, RUN_ID))
    for dry_run in (True, False):
        report = prune_run_state(workspace, keep_terminal_runs=0, dry_run=dry_run)
        assert list(report.pruned_run_ids) == [] and list(report.pruned_evidence_ids) == []
        assert report.skipped_reason is not None and "control state is unreadable" in report.skipped_reason and state_path in report.skipped_reason
    assert _bytes_of(run_record_path(workspace, RUN_ID)) == record_bytes, "the referenced run record was deleted or rewritten"
    assert _bytes_of(state_path) == data, "the control state is evidence and is never touched"


@pytest.mark.parametrize("data", [UNREADABLE["ascii_garbage"], UNREADABLE["invalid_utf8"]], ids=["ascii_garbage", "invalid_utf8"])
def test_kriya_runs_prune_refuses_with_the_action_required_exit_and_deletes_nothing(tmp_path, data):
    workspace = _workspace_with_a_terminal_run(tmp_path)
    _put_control_state(workspace, data)
    result = CliRunner().invoke(main, ["runs", "prune", "--workspace", workspace, "--keep", "0"])
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.exit_code != 0 and "Skipped: the persisted control state is unreadable" in result.output
    assert os.path.exists(run_record_path(workspace, RUN_ID))


def test_the_reference_reader_itself_is_unchanged_and_documented_as_not_telling_absent_from_unreadable(tmp_path):
    """The destructive decision is retention's; the reader keeps answering None for both, which is why retention checks readability first."""
    workspace = _workspace_with_a_terminal_run(tmp_path)
    _put_control_state(workspace, UNREADABLE["invalid_utf8"])
    assert workspace_run_references(workspace) == set()
