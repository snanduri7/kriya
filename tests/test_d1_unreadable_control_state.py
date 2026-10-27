"""D1 slice 2: an unreadable persisted ControlState is never "absent". It is never resumed from, never started fresh over, and its bytes are never overwritten."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from kriya.control import commit_state
from kriya.control.persistence import (
    ControlStateUnreadableError,
    control_state_path,
    control_state_unreadable,
    load_control_state,
    save_control_state,
)
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.state import ControlState


@pytest.fixture
def workspace(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return str(tmp_path)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _saved(workspace, **fields):
    state = ControlState(schema_version=1, run_id="r1", **fields)
    save_control_state(workspace, state)
    return state


@pytest.mark.parametrize("garbage", [b'{"truncated": ', b"", b"\xff\xfe not utf8", b"[1, 2]"],
                         ids=["torn-json", "empty", "not-utf8", "wrong-shape"])
def test_an_existing_but_unreadable_state_is_never_absent(workspace, garbage):
    _saved(workspace)
    path = control_state_path(workspace)
    with open(path, "wb") as handle:
        handle.write(garbage)
    with pytest.raises(ControlStateUnreadableError) as caught:
        load_control_state(workspace)
    assert caught.value.path == path and "left untouched" in str(caught.value)
    assert control_state_unreadable(workspace)[0] == path


def test_a_never_saved_state_is_absent_and_a_readable_one_loads_negative_controls(workspace):
    assert load_control_state(workspace) is None and control_state_unreadable(workspace) is None
    _saved(workspace)
    assert load_control_state(workspace).run_id == "r1" and control_state_unreadable(workspace) is None


def test_saving_never_overwrites_an_unreadable_state_and_its_bytes_are_preserved_exactly(workspace):
    _saved(workspace)
    path = control_state_path(workspace)
    with open(path, "wb") as handle:
        handle.write(b'{"in_progress_tool_subtask": "s7", "truncated": ')
    before, listing = _sha(path), sorted(os.listdir(os.path.dirname(path)))
    for durable in (False, True):
        with pytest.raises(ControlStateUnreadableError):
            save_control_state(workspace, ControlState(schema_version=1, run_id="fresh"), durable=durable)
        assert _sha(path) == before, "the unreadable evidence was overwritten"
        assert sorted(os.listdir(os.path.dirname(path))) == listing, "a temp file or sibling appeared"


def test_the_run_gate_refuses_a_mutating_run_over_an_unreadable_state_with_a_typed_needs_review(workspace):
    _saved(workspace)
    path = control_state_path(workspace)
    with open(path, "wb") as handle:
        handle.write(b'{"torn": ')
    before = _sha(path)
    assessment = commit_state.assess_workspace_commit_state(workspace)
    assert not assessment.safe and commit_state.REASON_CONTROL_STATE_UNREADABLE in assessment.reason_codes
    payload = assessment.to_payload("run-x")
    assert payload["status"] == "needs_review" and payload["quality_gates_passed"] is False
    assert payload["unreadable_control_state"]["path"] == path and payload["recovery_command"] is None
    assert "neither resume from it nor start fresh over it" in assessment.describe()
    with pytest.raises(commit_state.UncertainWorkspaceStateError) as caught:
        with begin_mutating_run(workspace):
            pytest.fail("a mutating run started over an unreadable control state")
    assert commit_state.REASON_CONTROL_STATE_UNREADABLE in caught.value.assessment.reason_codes
    assert _sha(path) == before, "the refused run touched the unreadable state"


def test_a_readable_state_leaves_the_assessment_and_its_payload_shape_unchanged_negative_control(workspace):
    _saved(workspace)
    assessment = commit_state.assess_workspace_commit_state(workspace)
    assert assessment.safe and assessment.unreadable_control_state is None
    assert "unreadable_control_state" not in assessment.to_payload()


def test_the_classification_survives_a_genuine_process_boundary(workspace):
    """A different process (the restart) finds the unreadable state and fails closed; the bytes are untouched by it."""
    state = _saved(workspace)
    path = control_state_path(workspace)
    document = json.loads(open(path).read())
    torn = json.dumps(document)[: len(json.dumps(document)) // 2]
    with open(path, "w") as handle:
        handle.write(torn)
    before = _sha(path)
    program = ("import sys\nfrom kriya.control.persistence import load_control_state, ControlStateUnreadableError\n"
               "try:\n    load_control_state(sys.argv[1]); print('LOADED')\nexcept ControlStateUnreadableError as e:\n    print('UNREADABLE')\n")
    done = subprocess.run([sys.executable, "-c", program, workspace], capture_output=True, text=True, check=True, timeout=60)
    assert done.stdout.strip() == "UNREADABLE" and _sha(path) == before and state.run_id == "r1"


def test_valid_json_without_an_ownership_stamp_is_refused_by_the_existing_ownership_gate_never_read_as_absent(workspace):
    """Not this slice's typed error: the pre-existing ownership gate already refuses an unstamped document (fail closed). Pinned so it can never degrade to None."""
    from kriya.context.workspace_identity import WorkspaceOwnershipError
    _saved(workspace)
    path = control_state_path(workspace)
    with open(path, "w") as handle:
        handle.write('{"valid": "json but not a ControlState"}')
    with pytest.raises(WorkspaceOwnershipError):
        load_control_state(workspace)
    assert open(path).read() == '{"valid": "json but not a ControlState"}', "the document was altered"
