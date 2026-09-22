"""PRD-007 canonical RunRecord schema, persistence, and coordinator wiring."""

import asyncio
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from kriya.control.persistence import (
    StaleRunRecordError,
    control_state_path,
    load_run_record,
    save_control_state,
    save_run_record,
)
from kriya.control.run_coordinator import (
    begin_mutating_run,
    coordinated_mutation,
    current_run_context,
    transition_mutating_run,
)
from kriya.control.run_record import (
    IllegalRunTransitionError,
    RunLifecycle,
    RunRecord,
)
from kriya.control.state import ControlState
from kriya.workflow.checkpoint import checkpoint_path, save_checkpoint

MP = multiprocessing.get_context("fork")


@coordinated_mutation
async def _successful_run(workspace_path):
    return {"status": "success", "quality_gates_passed": True}


@coordinated_mutation
async def _failed_run(workspace_path):
    raise RuntimeError("controlled failure")


class UncertainCommitError(RuntimeError):
    pass


@coordinated_mutation
async def _uncertain_run(workspace_path):
    raise UncertainCommitError("commit outcome unknown")


def _crash_with_running_record(workspace_path):
    with begin_mutating_run(workspace_path, run_id="crashed-run") as context:
        transition_mutating_run(context, RunLifecycle.RUNNING)
        os._exit(17)


def test_schema_round_trip_contains_required_lifecycle_fields(tmp_path):
    record = RunRecord.new("run-1", "workspace-1", "commit-1", "tree-1")
    record = record.transition(
        RunLifecycle.RUNNING,
        effective_config_fingerprint="config-1",
        model_runtime_fingerprint_ids=["model-1"],
        goal_hash="goal-1",
        approved_plan_hash="plan-1",
        obligation_ledger_revision=3,
        obligation_ledger_hash="ledger-1",
        candidate_revision="candidate-rev",
        candidate_hash="candidate-hash",
        verification_evidence_ids=["evidence-1"],
        retry_state_reference="checkpoint-1",
        retry_counters={"developer": 2},
        commit_intent="commit-candidate",
        commit_result="pending",
        store_revisions={"control_state": 4},
    )
    save_run_record(str(tmp_path), RunRecord.new("run-1", "workspace-1", "commit-1", "tree-1"), expected_revision=None)
    save_run_record(str(tmp_path), record, expected_revision=1)
    assert load_run_record(str(tmp_path), "run-1") == record


def test_illegal_and_post_terminal_transitions_fail_closed():
    record = RunRecord.new("run-1", "workspace-1", None, None)
    with pytest.raises(IllegalRunTransitionError):
        record.transition(RunLifecycle.COMMITTED)
    terminal = record.transition(RunLifecycle.FAILURE)
    with pytest.raises(IllegalRunTransitionError):
        terminal.transition(RunLifecycle.RUNNING)


def test_stale_writer_cannot_overwrite_newer_record(tmp_path):
    initial = RunRecord.new("run-1", "workspace-1", None, None)
    save_run_record(str(tmp_path), initial, expected_revision=None)
    running = initial.transition(RunLifecycle.RUNNING)
    save_run_record(str(tmp_path), running, expected_revision=1)
    with pytest.raises(StaleRunRecordError):
        save_run_record(
            str(tmp_path), initial.transition(RunLifecycle.PLANNING), expected_revision=1,
        )
    assert load_run_record(str(tmp_path), "run-1") == running


def test_successful_public_mutation_persists_terminal_success(tmp_path):
    asyncio.run(_successful_run(str(tmp_path)))
    records = list((tmp_path / ".kriya" / "control" / "runs").glob("*.json"))
    assert len(records) == 1
    run_id = records[0].stem
    record = load_run_record(str(tmp_path), run_id)
    assert record is not None
    assert record.lifecycle_state == RunLifecycle.SUCCESS
    assert record.terminal_status == "SUCCESS"
    assert record.commit_intent == "APPLY_VERIFIED_CANDIDATE"
    assert record.commit_result == "NO_CHANGES"


def test_controlled_exception_persists_terminal_failure(tmp_path):
    with pytest.raises(RuntimeError, match="controlled failure"):
        asyncio.run(_failed_run(str(tmp_path)))
    run_id = next((tmp_path / ".kriya" / "control" / "runs").glob("*.json")).stem
    record = load_run_record(str(tmp_path), run_id)
    assert record is not None
    assert record.lifecycle_state == RunLifecycle.FAILURE
    assert record.commit_result == "NOT_COMMITTED"


def test_uncertain_commit_exception_persists_uncertain_terminal_state(tmp_path):
    with pytest.raises(UncertainCommitError):
        asyncio.run(_uncertain_run(str(tmp_path)))
    run_id = next((tmp_path / ".kriya" / "control" / "runs").glob("*.json")).stem
    record = load_run_record(str(tmp_path), run_id)
    assert record is not None
    assert record.lifecycle_state == RunLifecycle.UNCERTAIN
    assert record.commit_result == "UNCERTAIN"


def test_durable_state_is_reloadable_while_run_is_in_progress(tmp_path):
    with begin_mutating_run(str(tmp_path), run_id="reloadable") as context:
        transition_mutating_run(context, RunLifecycle.RUNNING)
        reloaded = load_run_record(str(tmp_path), context.run_id)
        assert reloaded is not None
        assert reloaded.lifecycle_state == RunLifecycle.RUNNING
        assert current_run_context() is context
    terminal = load_run_record(str(tmp_path), "reloadable")
    assert terminal is not None
    assert terminal.lifecycle_state == RunLifecycle.FAILURE


def test_process_crash_preserves_last_durable_lifecycle_state(tmp_path):
    process = MP.Process(target=_crash_with_running_record, args=(str(tmp_path),))
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 17
    record = load_run_record(str(tmp_path), "crashed-run")
    assert record is not None
    assert record.lifecycle_state == RunLifecycle.RUNNING
    # Kernel lock released on process death; recovery can reacquire ownership.
    with begin_mutating_run(str(tmp_path), run_id="recovery-run"):
        pass


def test_specialized_stores_reference_the_run_record_revision(tmp_path):
    workspace = str(tmp_path)
    with begin_mutating_run(workspace, run_id="derived-stores") as context:
        transition_mutating_run(context, RunLifecycle.RUNNING)
        expected_revision = context.record_revision
        save_control_state(workspace, ControlState.new(run_id=context.run_id))
        save_checkpoint(workspace, context.run_id, {"stage": "planning"})

        control_payload = json.loads(Path(control_state_path(workspace)).read_text())
        checkpoint_payload = json.loads(Path(checkpoint_path(workspace, context.run_id)).read_text())
        expected = {
            "classification": "derived",
            "run_id": context.run_id,
            "revision": expected_revision,
        }
        assert control_payload["_run_record"] == expected
        assert checkpoint_payload["_run_record"] == expected
