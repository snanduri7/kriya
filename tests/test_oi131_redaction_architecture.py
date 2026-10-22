"""OI-131 permanent control: the history display never shows a stored secret, whatever the row looks like.

The CURRENT architecture has two layers, and this file pins both so neither can silently erode (N5, reconciliation 2026-10-22):

1. WRITE TIME (S20 / OI-134): the one trace writer redacts every column before the row is stored. After S20 moved redaction here,
   the S2b read-side pins started to exercise already-redacted rows, so a mutant that made ``HistoryService.detail`` return the
   stored row unredacted survived 835 tests.
2. DISPLAY PROJECTION (S2b / OI-131): ``HistoryService`` redacts what the GUI receives, through the same one owner. It is what
   protects a row stored RAW - one written before S20 by an older Kriya, or by anything that bypassed the writer. Stored rows are
   never rewritten by a read.

The raw row below is planted the way such a row exists on disk: the writer creates the run, then the secret-bearing columns are
overwritten with unredacted bytes in SQLite."""
import json
import sqlite3

import pytest
from test_s2b_redaction_completeness import GATES, R, S, _kup

from kriya.config import AppConfig
from kriya.core.state_paths import trace_db_path
from kriya.foundation.redaction import redact_value

RAW_GOAL = f"deploy with api_key = 'x {S}4'"
RAW_PROMPT = f"use DB_PASSWORD={S}3 and plan the change"
RUN = "r-legacy"


def _stored(db, columns="gate_outcomes, goal, prompt_rendered"):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT {columns} FROM runs WHERE run_id = ?", (RUN,)).fetchone()          # noqa: S608 - fixed column list
    finally:
        conn.close()


@pytest.fixture
def legacy_db():
    from kriya.core.trace import TraceLogger

    db = trace_db_path(AppConfig())
    TraceLogger(db).log_run(run_id=RUN, goal="g", duration_sec=1.0, attempts=1, status="SUCCESS", files_modified=[],
                            gate_outcomes=[{"attempt": 1, "type": "compile", "success": True}], prompt_rendered="p")
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE runs SET gate_outcomes = ?, goal = ?, prompt_rendered = ? WHERE run_id = ?",
                     (json.dumps(GATES), RAW_GOAL, RAW_PROMPT, RUN))
        conn.commit()
    finally:
        conn.close()
    gates, goal, prompt = _stored(db)
    assert S in gates and S in goal and S in prompt                 # the plant is real: the stored row is RAW (not vacuous)
    return db


def _snapshot():
    return _kup("snapshot.acquire")[0]["data"]["snapshot_id"]


def test_a_raw_stored_row_is_hidden_in_the_history_detail_the_gui_receives(legacy_db):
    envelope, wire = _kup("history.detail", snapshot_id=_snapshot(), run_id=RUN)
    assert S not in wire, wire
    data = envelope["data"]
    assert data["gate_outcomes"]["data"][1]["steps"][0]["command"] == ["run.sh", "--token", R, f"--password={R}"]
    assert json.loads(data["fields"]["gate_outcomes"]["data"]) == data["gate_outcomes"]["data"]      # blob and section agree
    assert data["run"]["goal"] == f"deploy with api_key = '{R}'"
    assert redact_value(data) == data                                                                # a fixed point


def test_a_raw_stored_prompt_is_hidden_in_the_history_prompt_the_gui_receives(legacy_db):
    envelope, wire = _kup("history.prompt", snapshot_id=_snapshot(), run_id=RUN)
    assert S not in wire and envelope["data"]["prompt_rendered"] == f"use DB_PASSWORD={R} and plan the change"


def test_a_raw_stored_goal_is_hidden_in_the_history_list_the_gui_receives(legacy_db):
    _envelope, wire = _kup("history.list", snapshot_id=_snapshot())
    assert "deploy with api_key" in wire and S not in wire


def test_reading_the_history_never_rewrites_the_stored_row(legacy_db):
    before = _stored(legacy_db)
    snapshot = _snapshot()
    for operation in ("history.detail", "history.prompt"):
        _kup(operation, snapshot_id=snapshot, run_id=RUN)
    _kup("history.list", snapshot_id=snapshot)
    assert _stored(legacy_db) == before                              # evidence stays as stored; only the projection is redacted


def test_the_trace_writer_stores_no_secret_so_a_new_row_needs_no_projection(tmp_path):
    """The write-time half: the same inputs, written through the real writer, leave no secret in any stored byte."""
    from kriya.core.trace import TraceLogger

    db = str(tmp_path / "traces.db")
    TraceLogger(db).log_run(run_id=RUN, goal=RAW_GOAL, duration_sec=1.0, attempts=1, status="SUCCESS", files_modified=[],
                            gate_outcomes=GATES, prompt_rendered=RAW_PROMPT)
    gates, goal, prompt = _stored(db)
    assert S not in gates and S not in goal and S not in prompt
    assert json.loads(gates) == redact_value(GATES) and goal == f"deploy with api_key = '{R}'"
