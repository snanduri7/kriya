"""The live smoke contract (tests/_live_smoke_contract.py), deterministically:
it passes only SUCCESS or an allowlisted typed capability failure, and only
with a real model request, a recorded runtime, bounded retries, terminal and
settled RunRecords, an untouched workspace (unless verified SUCCESS) and no
safety signal.
"""
import dataclasses
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from _chaos_harness import attempt_ceiling
from _live_smoke_contract import (
    SMOKE_CAPABILITY_FAILURES,
    SUCCESS,
    assert_smoke_contract,
    smoke_outcome,
    workspace_snapshot,
)

from kriya.control.persistence import save_run_record
from kriya.control.run_record import COMMIT_COMMITTED, RunLifecycle, RunRecord

MODEL = "qwen2.5-coder:1.5b"
DIGEST = "a" * 64


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    (ws / "README.md").write_text("scratch\n")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=ws, check=True)
    (ws / "kriya.yaml").write_text("llm: {}\n")
    return ws


def _traces(state, roles):
    """A traces.db holding one run whose model.role_metrics event reports ``roles``."""
    with sqlite3.connect(str(state / "traces.db")) as connection:
        connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, run_events TEXT)")
        event = {"kind": "model.role_metrics", "details": {"rows": [
            {"role": role, "model": MODEL, "calls": calls} for role, calls in roles.items()]}}
        connection.execute("INSERT INTO runs VALUES (?, ?)", ("run-1", json.dumps([event])))


@pytest.fixture
def state_dir(tmp_path):
    runtimes = tmp_path / "state" / "model_runtimes"
    runtimes.mkdir(parents=True)
    (runtimes / f"{DIGEST}.json").write_text(json.dumps({"model": MODEL, "digest": DIGEST}))
    _traces(tmp_path / "state", {"planner": 1, "architect": 1})
    return str(tmp_path / "state")


def _record(workspace, *, lifecycle=RunLifecycle.FAILURE, fingerprints=(DIGEST,), retries=1, commits=()):
    record = dataclasses.replace(
        RunRecord.new("run-1", "w", None, None), lifecycle_state=lifecycle,
        model_runtime_fingerprint_ids=list(fingerprints), retry_counters={"retry_count": retries},
        commits=list(commits))
    save_run_record(str(workspace), record, expected_revision=None)


FAILED = {"quality_gates_passed": False, "failure_category": "quality_gates_exhausted", "files": []}


def _check(workspace, state_dir, payload=FAILED, *, before=None, stderr="=== Generation Workflow Completed ==="):
    return assert_smoke_contract(workspace, payload, before=before or workspace_snapshot(workspace),
                                 stderr=stderr, state_dir=state_dir, model=MODEL)


def test_an_allowlisted_capability_failure_with_every_invariant_held_passes(workspace, state_dir):
    _record(workspace)
    evidence = _check(workspace, state_dir)
    assert evidence["outcome"] == "quality_gates_exhausted" and evidence["runtime_fingerprints"] == [DIGEST]
    assert evidence["changed_files"] == [] and evidence["reason"] == SMOKE_CAPABILITY_FAILURES["quality_gates_exhausted"]
    assert evidence["roles_called"] == {"planner": 1, "architect": 1}


def test_a_run_without_a_real_planner_call_fails(workspace, tmp_path):
    state = tmp_path / "other-state"
    (state / "model_runtimes").mkdir(parents=True)
    (state / "model_runtimes" / f"{DIGEST}.json").write_text(json.dumps({"model": MODEL}))
    _traces(state, {"reviewer": 1})
    _record(workspace)
    with pytest.raises(AssertionError, match="no real Planner call"):
        _check(workspace, str(state))
    assert Path(state, "traces.db").exists()


def test_verified_success_may_change_exactly_its_reported_files(workspace, state_dir):
    before = workspace_snapshot(workspace)
    (workspace / "add.py").write_text("def add(a, b):\n    return a + b\n")
    _record(workspace, lifecycle=RunLifecycle.SUCCESS, commits=[{"result": COMMIT_COMMITTED}])
    payload = {"quality_gates_passed": True, "files": ["add.py"]}
    assert _check(workspace, state_dir, payload, before=before)["changed_files"] == ["add.py"]
    (workspace / "README.md").write_text("changed too\n")
    with pytest.raises(AssertionError, match="outside the reported files"):
        _check(workspace, state_dir, payload, before=before)


def test_an_untyped_outcome_is_named_as_untyped(workspace, state_dir):
    _record(workspace)
    with pytest.raises(AssertionError, match="untyped outcome"):
        _check(workspace, state_dir, {"quality_gates_passed": False})


@pytest.mark.parametrize("payload", [
    {"quality_gates_passed": False, "failure_category": "environment_failure"},
    {"quality_gates_passed": False, "failure_category": "workspace_commit_failed"},
    {"quality_gates_passed": False, "failure_category": "containment_setup_failed"},
    {"quality_gates_passed": False, "failure_category": "unauthorized_generation_target"},
    {"status": "planner_output_unauthorized_path"},
])
def test_an_unlisted_outcome_never_passes(workspace, state_dir, payload):
    _record(workspace)
    with pytest.raises(AssertionError, match="not a smoke outcome"):
        _check(workspace, state_dir, payload)


def test_the_typed_outcome_reads_the_result_the_run_actually_produced():
    assert smoke_outcome({"quality_gates_passed": True, "failure_category": None}) == SUCCESS
    assert smoke_outcome({"status": "planner_output_incomplete"}) == "planner_output_incomplete"
    assert smoke_outcome({"status": "knowledge_gap"}) is None


@pytest.mark.parametrize("fingerprints,match", [((), "no real model request"), (("b" * 64,), "no recorded runtime")])
def test_a_real_model_request_on_the_smoke_model_is_required(workspace, state_dir, fingerprints, match):
    _record(workspace, fingerprints=fingerprints)
    with pytest.raises(AssertionError, match=match):
        _check(workspace, state_dir)


def test_a_workspace_changed_without_success_fails(workspace, state_dir):
    before = workspace_snapshot(workspace)
    (workspace / "add.py").write_text("partial\n")
    _record(workspace)
    with pytest.raises(AssertionError, match="changed without SUCCESS"):
        _check(workspace, state_dir, before=before)


@pytest.mark.parametrize("stderr", ["Traceback (most recent call last):\n  boom", "EgressViolationError: public host"])
def test_a_crash_or_an_egress_violation_fails(workspace, state_dir, stderr):
    _record(workspace)
    with pytest.raises(AssertionError):
        _check(workspace, state_dir, stderr=stderr)


@pytest.mark.parametrize("kwargs", [
    {"lifecycle": RunLifecycle.UNCERTAIN},
    {"lifecycle": RunLifecycle.RUNNING},
    {"commits": [{"result": COMMIT_COMMITTED}]},  # a commit without SUCCESS
    {"commits": [{"result": None}]},              # an unsettled cycle
    {"retries": attempt_ceiling() + 1},
])
def test_unsafe_or_unbounded_evidence_fails(workspace, state_dir, kwargs):
    _record(workspace, **kwargs)
    with pytest.raises(AssertionError):
        _check(workspace, state_dir)


def test_no_run_record_or_an_unreadable_one_fails(workspace, state_dir):
    with pytest.raises(AssertionError, match="no RunRecord"):
        _check(workspace, state_dir)
    _record(workspace)
    runs = workspace / ".kriya" / "control" / "runs"
    (runs / "run-2.json").write_text("{corrupt")
    with pytest.raises(AssertionError):
        _check(workspace, state_dir)
