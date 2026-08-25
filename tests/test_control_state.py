"""MA5.1: ControlState (kriya/control/state.py) - immutability, the
with_updates() invariant guard, and the to_dict/from_dict/content_hash
round-trip contract (including the deliberate engineering_route/
process_profile asymmetry)."""

import dataclasses

import pytest

from kriya.control.state import CURRENT_SCHEMA_VERSION, ControlState
from kriya.workflow.process_profile import HEAVY_PROFILE
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass


def _route():
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.STANDARD,
    )


def test_new_sets_schema_version_and_timestamps():
    state = ControlState.new(run_id="run-1")
    assert state.schema_version == CURRENT_SCHEMA_VERSION
    assert state.run_id == "run-1"
    assert state.created_at == state.updated_at


def test_control_state_is_frozen():
    state = ControlState.new(run_id="run-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.current_milestone_id = "M1"


def test_with_updates_returns_a_new_object_and_refreshes_updated_at():
    state = ControlState.new(run_id="run-1")
    updated = state.with_updates(current_milestone_id="M1")
    assert updated is not state
    assert updated.current_milestone_id == "M1"
    assert state.current_milestone_id is None
    assert updated.created_at == state.created_at  # created_at never changes


def test_with_updates_rejects_changing_immutable_fields():
    state = ControlState.new(run_id="run-1")
    for field_name, value in (("run_id", "other"), ("schema_version", 99), ("created_at", "x")):
        with pytest.raises(ValueError):
            state.with_updates(**{field_name: value})


def test_to_dict_serializes_engineering_route_and_process_profile_as_summaries():
    state = ControlState.new(run_id="run-1", engineering_route=_route(), process_profile=HEAVY_PROFILE)
    as_dict = state.to_dict()
    assert as_dict["engineering_route"]["kind"] == "task"
    assert as_dict["process_profile"]["execution_weight"] == "heavy"


def test_to_dict_handles_none_route_and_profile():
    state = ControlState.new(run_id="run-1")
    as_dict = state.to_dict()
    assert as_dict["engineering_route"] is None
    assert as_dict["process_profile"] is None


def test_from_dict_round_trips_control_metadata_fields():
    state = ControlState.new(run_id="run-1", milestone_group_id="grp-1").with_updates(
        current_milestone_id="M2",
        milestone_states={"M1": "done", "M2": "in_progress"},
        base_commit="abc123",
        tree_hash="def456",
        current_contract_hash="contracts-hash",
        current_artifact_registry_hash="artifacts-hash",
    )
    reconstructed = ControlState.from_dict(state.to_dict())
    assert reconstructed.run_id == state.run_id
    assert reconstructed.current_milestone_id == "M2"
    assert reconstructed.milestone_states == {"M1": "done", "M2": "in_progress"}
    assert reconstructed.base_commit == "abc123"
    assert reconstructed.tree_hash == "def456"
    assert reconstructed.current_contract_hash == "contracts-hash"
    assert reconstructed.current_artifact_registry_hash == "artifacts-hash"


def test_from_dict_defaults_artifact_registry_hash_for_legacy_state():
    payload = ControlState.new(run_id="run-1").to_dict()
    del payload["current_artifact_registry_hash"]
    assert ControlState.from_dict(payload).current_artifact_registry_hash is None


def test_subtask_states_defaults_empty_and_round_trips():
    """subtask_states (added 2026-08-24) is the MA6/WorkflowController
    analog of milestone_states, added to make MA5.9's resume-validation
    machinery real - see this field's own docstring in kriya/control/state.py."""
    fresh = ControlState.new(run_id="run-1")
    assert fresh.subtask_states == {}

    state = fresh.with_updates(subtask_states={"s1": "completed", "s2": "failed"})
    reconstructed = ControlState.from_dict(state.to_dict())
    assert reconstructed.subtask_states == {"s1": "completed", "s2": "failed"}


def test_subtask_states_defaults_empty_when_loading_a_pre_existing_persisted_dict():
    """A ControlState persisted before this field existed must load cleanly
    with subtask_states={}, not KeyError."""
    old_style_dict = ControlState.new(run_id="run-1").to_dict()
    del old_style_dict["subtask_states"]
    reconstructed = ControlState.from_dict(old_style_dict)
    assert reconstructed.subtask_states == {}


def test_subtask_written_files_defaults_empty_and_round_trips():
    """subtask_written_files (added 2026-08-25) records the real, applied
    file paths each completed subtask wrote - see this field's own docstring
    for the abandoned-plan-residue bug it exists to let WorkflowController
    detect and clean up."""
    fresh = ControlState.new(run_id="run-1")
    assert fresh.subtask_written_files == {}

    state = fresh.with_updates(subtask_written_files={"s1": ["Protocol.java"], "s2": ["ProtocolMain.java"]})
    reconstructed = ControlState.from_dict(state.to_dict())
    assert reconstructed.subtask_written_files == {"s1": ["Protocol.java"], "s2": ["ProtocolMain.java"]}


def test_subtask_written_files_defaults_empty_when_loading_a_pre_existing_persisted_dict():
    """A ControlState persisted before this field existed must load cleanly
    with subtask_written_files={}, not KeyError."""
    old_style_dict = ControlState.new(run_id="run-1").to_dict()
    del old_style_dict["subtask_written_files"]
    reconstructed = ControlState.from_dict(old_style_dict)
    assert reconstructed.subtask_written_files == {}


def test_from_dict_always_drops_live_route_and_profile_objects():
    """The documented asymmetry - a loaded ControlState never resurrects a
    live EngineeringRoute/ProcessProfile from its own to_dict() summary."""
    state = ControlState.new(run_id="run-1", engineering_route=_route(), process_profile=HEAVY_PROFILE)
    reconstructed = ControlState.from_dict(state.to_dict())
    assert reconstructed.engineering_route is None
    assert reconstructed.process_profile is None


def test_content_hash_is_stable_and_ignores_timestamps():
    state = ControlState.new(run_id="run-1", milestone_group_id="grp-1")
    hash_a = state.content_hash()
    # A pure timestamp bump (with_updates always refreshes updated_at) must
    # not change the hash - resume validation cares about CONTENT drift,
    # not "was this object touched."
    import time
    time.sleep(0.01)
    hash_b = state.with_updates(current_milestone_id=None).content_hash()
    assert hash_a == hash_b


def test_content_hash_changes_when_real_content_changes():
    state = ControlState.new(run_id="run-1")
    hash_a = state.content_hash()
    hash_b = state.with_updates(current_milestone_id="M1").content_hash()
    assert hash_a != hash_b
