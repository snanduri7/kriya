"""RDCG B-005 (owner decision 2026-10-28): a persisted proposal whose bytes are not UTF-8 is the existing typed ``PROPOSAL_PARSE_INVALID`` refusal, exactly like bytes that are not JSON.

MEASURED before the fix (WAVE2_MALFORMED_STATE_CHARACTERIZATION_2026-10-28): ``load_proposal`` caught ``(OSError, json.JSONDecodeError)``, so the ``UnicodeDecodeError`` escaped it and
every caller (``proposal show|approve|reject|execute``, which catch ``ProposalStoreError`` only) ended in a raw traceback. It was fail-closed (the error is raised before any digest, approval or
write) but untyped. Absent stays ``PROPOSAL_NOT_FOUND``; malformed JSON stays ``PROPOSAL_PARSE_INVALID``."""
import os

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.workflow.proposal_store import (
    PROPOSALS_DIRNAME,
    REASON_PROPOSAL_NOT_FOUND,
    REASON_PROPOSAL_PARSE_INVALID,
    ProposalStoreError,
    approve_proposal,
    load_proposal,
    reject_proposal,
)

PID = "p1"
ASCII_GARBAGE = b"{{ not json"
INVALID_UTF8 = {
    "lone_invalid_start_byte": b'{"schema_version": 1}\xff\xfe',
    "truncated_multibyte_sequence": b'{"proposal_id": "p1", "note": "\xe2\x82',
    "utf16_with_bom": b'\xff\xfe{\x00}\x00',
}
UNREADABLE = {"ascii_garbage_control": ASCII_GARBAGE, "empty_control": b"", **INVALID_UTF8}


def _store(root, data):
    directory = os.path.join(str(root), PROPOSALS_DIRNAME)
    os.makedirs(directory)
    path = os.path.join(directory, f"{PID}.json")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


# `proposal execute` is a coordinated mutating command: it records its own (failed) RunRecord under the run lock even for a proposal that was refused; that is the existing
# behaviour of the typed-refusal control too, so it is excluded from "nothing was proposed, approved or generated"
RUN_COORDINATION = (os.path.join(".kriya", "control"), os.path.join(".kriya", "run.lock"))


def _tree(root):
    found = {}
    for directory, _dirs, files in os.walk(str(root)):
        for name in files:
            full = os.path.join(directory, name)
            relative = os.path.relpath(full, str(root))
            if relative.startswith(RUN_COORDINATION):
                continue
            with open(full, "rb") as handle:
                found[relative] = handle.read()
    return found


def _run_records(root):
    directory = os.path.join(str(root), ".kriya", "control", "runs")
    return sorted(os.listdir(directory)) if os.path.isdir(directory) else []


def _cli(root, *args):
    old = os.getcwd()
    os.chdir(str(root))
    try:
        return CliRunner().invoke(main, ["proposal", *args])
    finally:
        os.chdir(old)


def test_an_absent_proposal_is_still_not_found(tmp_path):
    with pytest.raises(ProposalStoreError) as exc_info:
        load_proposal(PID, str(tmp_path))
    assert exc_info.value.reason_code == REASON_PROPOSAL_NOT_FOUND


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
@pytest.mark.parametrize("operation", [load_proposal, approve_proposal, reject_proposal])
def test_unreadable_proposal_bytes_are_parse_invalid_never_not_found_and_nothing_is_written(tmp_path, data, operation):
    path = _store(tmp_path, data)
    before = _tree(tmp_path)
    with pytest.raises(ProposalStoreError) as exc_info:
        operation(PID, str(tmp_path))
    assert exc_info.value.reason_code == REASON_PROPOSAL_PARSE_INVALID
    assert exc_info.value.reason_code != REASON_PROPOSAL_NOT_FOUND
    assert _tree(tmp_path) == before and before[os.path.relpath(path, str(tmp_path))] == data


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_proposal_file_that_cannot_be_opened_is_parse_invalid_too(tmp_path):
    path = _store(tmp_path, b"{}")
    os.chmod(path, 0)
    try:
        with pytest.raises(ProposalStoreError) as exc_info:
            load_proposal(PID, str(tmp_path))
    finally:
        os.chmod(path, 0o600)
    assert exc_info.value.reason_code == REASON_PROPOSAL_PARSE_INVALID
    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.parametrize("data", INVALID_UTF8.values(), ids=list(INVALID_UTF8))
def test_the_encoding_cause_is_kept_in_the_detail_not_in_a_new_code(tmp_path, data):
    _store(tmp_path, data)
    with pytest.raises(ProposalStoreError) as exc_info:
        load_proposal(PID, str(tmp_path))
    assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)
    assert "utf-8" in exc_info.value.message


@pytest.mark.parametrize("data", UNREADABLE.values(), ids=list(UNREADABLE))
@pytest.mark.parametrize("subcommand", ["show", "approve", "reject", "execute"])
def test_the_proposal_commands_refuse_typed_and_proceed_with_nothing(tmp_path, data, subcommand):
    _store(tmp_path, data)
    before = _tree(tmp_path)
    result = _cli(tmp_path, subcommand, PID)
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.exit_code == 1
    assert REASON_PROPOSAL_PARSE_INVALID in result.output
    assert REASON_PROPOSAL_NOT_FOUND not in result.output
    assert _tree(tmp_path) == before
    if subcommand != "execute":
        assert _run_records(tmp_path) == []
