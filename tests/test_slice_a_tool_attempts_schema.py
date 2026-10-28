"""Wave 2 Slice A, stage 2: ``ControlState.tool_attempts`` - the CURRENT attempt counter of a plan TOOL subtask - and its serialization contract.

Omitted from to_dict() (and so from content_hash(), which every checkpoint stores as control_state_hash) while empty: an explicit compatibility contract, not a convenience. The
reference digest below was computed from the pre-field kriya/control/state.py at bc88f44, so it is independent of this change."""
import json

import pytest

from kriya.control.persistence import (
    ControlStateUnreadableError,
    control_state_path,
    load_control_state,
    save_control_state,
)
from kriya.control.state import SUBTASK_STATE_TOOL_FAILED, SUBTASK_STATE_TOOL_STARTED, ControlState

REFERENCE_DIGEST_WITHOUT_THE_FIELD = "34ac77835fb71e4477b84205c43601ddccc29756943bcd4a88fdfa9f66dfa2c0"


def _state(**overrides):
    base = dict(schema_version=1, run_id="r1", subtask_states={"t1": SUBTASK_STATE_TOOL_STARTED, "m1": "completed"},
                subtask_written_files={"m1": ["a.py"]}, current_plan_hash="ph", created_at="t", updated_at="t")
    base.update(overrides)
    return ControlState(**base)


def test_an_empty_field_is_omitted_and_the_content_hash_is_what_it_was_before_the_field_existed():
    state = _state()
    assert "tool_attempts" not in state.to_dict()
    assert state.content_hash() == REFERENCE_DIGEST_WITHOUT_THE_FIELD


def test_a_non_empty_field_serializes_deterministically_and_changes_the_content_hash():
    first, second = _state(tool_attempts={"t2": 1, "t1": 3}), _state(tool_attempts={"t1": 3, "t2": 1})
    assert list(first.to_dict()["tool_attempts"].items()) == [("t1", 3), ("t2", 1)]       # sorted keys, whatever the insertion order
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)
    assert first.content_hash() == second.content_hash() != REFERENCE_DIGEST_WITHOUT_THE_FIELD
    assert _state(tool_attempts={"t1": 4}).content_hash() != _state(tool_attempts={"t1": 3}).content_hash()   # the counter value is part of the identity


def test_an_old_state_with_no_field_loads_as_legacy_and_a_new_state_round_trips_exactly():
    old = _state().to_dict()
    assert "tool_attempts" not in old
    assert ControlState.from_dict(old).tool_attempts == {}                  # absent = legacy/unversioned, NOT {"t1": 1}
    new = _state(tool_attempts={"t1": 2})
    assert ControlState.from_dict(new.to_dict()) == new and ControlState.from_dict(new.to_dict()).content_hash() == new.content_hash()


@pytest.mark.parametrize("bad", [{"t1": 0}, {"t1": -1}, {"t1": True}, {"t1": "2"}, {"t1": 1.5}, {"": 1}, ["t1"], "t1", None])
def test_a_malformed_attempt_counter_is_refused_not_read_as_no_attempts(bad, tmp_path):
    payload = _state().to_dict()
    payload["tool_attempts"] = bad
    with pytest.raises(ValueError):
        ControlState.from_dict(payload)
    save_control_state(str(tmp_path), _state(tool_attempts={"t1": 1}))
    path = control_state_path(str(tmp_path))
    on_disk = json.load(open(path, encoding="utf-8"))
    on_disk["tool_attempts"] = bad
    open(path, "w", encoding="utf-8").write(json.dumps(on_disk))
    with pytest.raises(ControlStateUnreadableError):                         # the typed fail-closed refusal of an unreadable state (D1 slice 2)
        load_control_state(str(tmp_path))


def test_undisposed_ids_are_started_plus_terminal_failed_and_nothing_else():
    state = _state(subtask_states={"a": SUBTASK_STATE_TOOL_STARTED, "b": SUBTASK_STATE_TOOL_FAILED, "c": "failed", "d": "needs_review", "e": "completed", "f": "pending"})
    assert state.unresolved_tool_subtask_ids == ("a",)                       # D1 semantics unchanged
    assert state.terminal_failed_tool_subtask_ids == ("b",)
    assert state.undisposed_tool_subtask_ids == ("a", "b")


def test_the_state_persists_and_reloads_the_counter_through_the_real_store(tmp_path):
    save_control_state(str(tmp_path), _state(tool_attempts={"t1": 2}), durable=True)
    assert load_control_state(str(tmp_path)).tool_attempts == {"t1": 2}
