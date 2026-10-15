"""REOPENED-PROJECT-RUN-HISTORY-001: a project's Recent runs come from the history store of the state directory the app reads (a published snapshot of its trace store), scoped to the
project by the evidence manifest's workspace identity - and they survive a restart.

Every read here is a FRESH PROCESS (`kriya traces --json`, exactly what the desktop host spawns), so no in-memory state can carry a run over; the writer is a different process or an earlier call.
The owner's defect (a completed Demo 00 run absent from the reopened project) was a state-directory mismatch: the app instance read an empty scenario state, not the one that holds the run.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from _kup_evidence_fixtures import seed_trace_run, state_dir

from kriya.config import AppConfig
from kriya.control.workspace_identity import workspace_identity
from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.core.state_paths import trace_db_path

KRIYA = str(Path(sys.executable).parent / "kriya")


def _record_run(workspace: Path, trace_id: str, *, timestamp: str | None = None, status: str = "success") -> None:
    """A finished run of `workspace` as the product records it: a sealed evidence store naming the workspace + the trace row that links to it."""
    store_id = f"ev-{trace_id}"
    writer = AttemptEvidenceWriter(state_dir(), store_id, capture=model.CAPTURE_FULL,
                                   manifest={"workspace_identity": workspace_identity(str(workspace)), "workspace_path": str(workspace)})
    writer.append("run.opened", {"goal_digest": "sha256:" + "1" * 64})
    writer.seal("run_closed")
    seed_trace_run(trace_id, {"run_id": store_id, "status": "OPEN", "capture": "full"})
    with sqlite3.connect(trace_db_path(AppConfig())) as db:
        db.execute("UPDATE runs SET status = ?, timestamp = COALESCE(?, timestamp) WHERE run_id = ?", (status, timestamp, trace_id))


def _kriya(*args: str, state: str | None = None) -> dict:
    env = {**os.environ, "KRIYA_STATE_DIR": state if state is not None else state_dir()}
    done = subprocess.run([KRIYA, "traces", "--json", *args], capture_output=True, text=True, env=env, check=False, timeout=120)
    return json.loads(done.stdout)


def _recent(workspace: Path, *, limit: int = 3, state: str | None = None) -> list:
    """What the desktop does on open: acquire a snapshot for the workspace, then list that workspace's runs from it - each a new process."""
    acquired = _kriya("--snapshot", "--workspace", str(workspace), state=state)
    assert acquired["error"] is None, acquired
    listed = _kriya("--snapshot-id", acquired["data"]["snapshot_id"], "--workspace", str(workspace), "-n", str(limit), state=state)
    assert listed["error"] is None, listed
    return listed["data"]["runs"]


def test_a_completed_run_is_listed_after_a_restart_and_after_every_reopen(tmp_path):
    project = tmp_path / "HelloWorld"
    project.mkdir()
    _record_run(project, "t-done")
    for _reopen in range(2):                                          # two separate processes: nothing but the recorded state is shared
        runs = _recent(project)
        assert [r["run_id"] for r in runs] == ["t-done"]
        assert runs[0]["status"] == "success"                         # a closed, successful run is not filtered out


def test_histories_are_isolated_by_project(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _record_run(a, "t-a")
    assert [r["run_id"] for r in _recent(a)] == ["t-a"]
    assert _recent(b) == []                                           # project A's run never appears in B
    _record_run(b, "t-b")
    assert [r["run_id"] for r in _recent(b)] == ["t-b"] and [r["run_id"] for r in _recent(a)] == ["t-a"]


def test_the_latest_three_follow_the_recorded_time_not_the_order_they_were_written(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    stamps = {"t-1": "2026-10-01T10:00:00", "t-5": "2026-10-05T10:00:00", "t-2": "2026-10-02T10:00:00", "t-4": "2026-10-04T10:00:00", "t-3": "2026-10-03T10:00:00"}
    for run_id, stamp in stamps.items():                              # written out of chronological order
        _record_run(project, run_id, timestamp=stamp)
    assert [r["run_id"] for r in _recent(project, limit=3)] == ["t-5", "t-4", "t-3"]
    assert [r["run_id"] for r in _recent(project, limit=10)] == ["t-5", "t-4", "t-3", "t-2", "t-1"]


def test_successful_failed_and_unfinished_runs_are_all_retained(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    for index, status in enumerate(("success", "failure", "in_progress")):
        _record_run(project, f"t-{status}", timestamp=f"2026-10-0{index + 1}T10:00:00", status=status)
    assert {r["run_id"]: r["status"] for r in _recent(project)} == {"t-success": "success", "t-failure": "failure", "t-in_progress": "in_progress"}


def test_a_malformed_record_never_suppresses_its_valid_siblings(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    _record_run(project, "t-good-old", timestamp="2026-10-01T10:00:00")
    seed_trace_run("t-garbage-events", None, with_event=False)
    with sqlite3.connect(trace_db_path(AppConfig())) as db:
        db.execute("UPDATE runs SET run_events = ?, timestamp = ? WHERE run_id = ?", ("{not json", "2026-10-02T10:00:00", "t-garbage-events"))
    seed_trace_run("t-missing-store", {"run_id": "ev-gone", "status": "OPEN", "capture": "full"})
    _record_run(project, "t-good-new", timestamp="2026-10-03T10:00:00")
    assert [r["run_id"] for r in _recent(project)] == ["t-good-new", "t-good-old"]


def test_an_app_reading_another_state_directory_has_no_history_and_the_projects_own_records_are_not_a_substitute(tmp_path):
    """The owner's case: the run lives in one state directory; an app instance started with another (an empty per-scenario one) reads nothing - and says so with a typed error, never an invented list."""
    project = tmp_path / "p"
    project.mkdir()
    _record_run(project, "t-done")
    control = project / ".kriya" / "control" / "runs"                  # the project's own control record of a run: NOT a history source
    control.mkdir(parents=True)
    (control / "20261015T000000-aaaaaaaa.json").write_text(json.dumps({"run_id": "20261015T000000-aaaaaaaa", "terminal_status": "SUCCESS"}))
    other = tmp_path / "other-state"
    other.mkdir()
    acquired = _kriya("--snapshot", "--workspace", str(project), state=str(other))
    assert acquired["error"]["code"] == "READ_ONLY_UNAVAILABLE" and acquired["error"]["database_state"] == "missing"
    assert [r["run_id"] for r in _recent(project)] == ["t-done"]      # the state directory that holds the run still shows it
