"""The requirement truth of each listed run (owner ruling 2026-11-08, option A): the Runs list, the run header and the stored-run Overview word a completed run by the
SAME persisted authority - the run's own LAST ``requirement.truth`` event - projected at read time. There is no second stored copy, no per-run query, and a run with no
(or an unusable) event carries no field, so a host words it neutrally and never "verified".

Rows are written by the real ``TraceLogger`` (the producer of ``runs.run_events``) and read back through the real snapshot reader.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from kriya.application.history import _TRUTH_STATUSES, HistoryService, attach_requirement_truth
from kriya.control.history import Snapshot, SnapshotTrace, history_list
from kriya.core.trace import TraceLogger
from kriya.workflow import requirements as workflow_requirements


def _truth_event(status, verified, accepted=0, total=None, unresolved=None, attempt=1):
    return {"kind": "requirement.truth", "attempt": attempt, "source": "workflow.requirement_closure", "details": {
        "requirement_set_digest": "d" * 8, "status": status, "verified": verified, "accepted": accepted,
        "total": verified + accepted if total is None else total, "unresolved": unresolved or []}}


def _snapshot(tmp_path, rows):
    db = str(tmp_path / "traces.db")
    logger = TraceLogger(db)
    for run_id, events in rows.items():
        logger.log_run(run_id=run_id, goal=f"goal {run_id}", duration_sec=1.0, attempts=1, status="success", files_modified=["a.py"], run_events=events)
    return Snapshot(snapshot_id="snap-1", directory=str(tmp_path), db_path=db, manifest={})


def _page(snap):
    trace = SnapshotTrace(snap)
    page = history_list(snap, None, None)
    attach_requirement_truth(trace, page["runs"])
    return trace, {r["run_id"]: r for r in page["runs"]}


def test_the_closed_status_vocabulary_equals_the_workflows():
    assert _TRUTH_STATUSES == (workflow_requirements.REQUIREMENT_TRUTH_ALL_VERIFIED, workflow_requirements.REQUIREMENT_TRUTH_RESOLVED_WITH_ACCEPTANCE,
                               workflow_requirements.REQUIREMENT_TRUTH_OPEN, workflow_requirements.REQUIREMENT_TRUTH_CONTRADICTED)


def test_each_listed_run_carries_the_truth_its_own_event_recorded(tmp_path):
    snap = _snapshot(tmp_path, {
        "run-verified": [_truth_event("ALL_VERIFIED", 3)],
        "run-open": [_truth_event("OPEN_REQUIREMENTS", 1, total=3, unresolved=[{"id": "REQ-1", "text": "t", "outcome": "unverified"}])],
        "run-contradicted": [_truth_event("CONTRADICTED", 1, total=2)],
        "run-historical": [{"kind": "attempt.passed", "details": {}}],
        "run-resolved": [_truth_event("RESOLVED_WITH_OPERATOR_ACCEPTANCE", 1, accepted=2)],
    })
    _trace, runs = _page(snap)
    assert runs["run-verified"]["requirement_truth"] == {"status": "ALL_VERIFIED", "verified": 3, "accepted": 0, "total": 3}
    assert runs["run-open"]["requirement_truth"] == {"status": "OPEN_REQUIREMENTS", "verified": 1, "accepted": 0, "total": 3}  # bounded: no requirement text on the list
    assert runs["run-contradicted"]["requirement_truth"]["status"] == "CONTRADICTED"
    assert runs["run-resolved"]["requirement_truth"] == {"status": "RESOLVED_WITH_OPERATOR_ACCEPTANCE", "verified": 1, "accepted": 2, "total": 3}  # carried, never folded into ALL_VERIFIED
    assert "requirement_truth" not in runs["run-historical"]  # no event: no field, never "verified"


def test_the_last_truth_event_of_a_run_is_final(tmp_path):
    snap = _snapshot(tmp_path, {"run-1": [_truth_event("OPEN_REQUIREMENTS", 0, total=2, attempt=1), _truth_event("ALL_VERIFIED", 2, attempt=2)],
                                "run-2": [_truth_event("ALL_VERIFIED", 2, attempt=1), _truth_event("OPEN_REQUIREMENTS", 1, total=2, attempt=2)]})
    _trace, runs = _page(snap)
    assert runs["run-1"]["requirement_truth"]["status"] == "ALL_VERIFIED"
    assert runs["run-2"]["requirement_truth"]["status"] == "OPEN_REQUIREMENTS"  # an earlier verified closure never outvotes the final one


def test_an_unusable_event_or_unparseable_events_text_yields_no_truth(tmp_path):
    bad = {
        "run-status": [_truth_event("WONDERFUL", 3)],
        "run-bool": [_truth_event("ALL_VERIFIED", 3)],
        "run-negative": [_truth_event("ALL_VERIFIED", 3)],
        "run-no-details": [{"kind": "requirement.truth"}],
        "run-garbled": [],
    }
    snap = _snapshot(tmp_path, bad)
    with sqlite3.connect(snap.db_path) as conn:
        conn.execute("UPDATE runs SET run_events = ? WHERE run_id = 'run-garbled'", ("{not json",))
        conn.execute("UPDATE runs SET run_events = replace(run_events, '\"verified\": 3', '\"verified\": true') WHERE run_id = 'run-bool'")
        conn.execute("UPDATE runs SET run_events = replace(run_events, '\"verified\": 3', '\"verified\": -1') WHERE run_id = 'run-negative'")
    _trace, runs = _page(snap)
    for run_id in bad:
        assert "requirement_truth" not in runs[run_id], run_id
    assert runs["run-bool"]["status"] == "success"  # the row itself is still listed, only the projection is withheld


def test_one_batched_read_serves_the_whole_page(tmp_path):
    snap = _snapshot(tmp_path, {f"run-{i}": [_truth_event("ALL_VERIFIED", 1)] for i in range(7)})
    real = SnapshotTrace(snap)
    calls = []

    class Counting:
        snapshot_id = real.snapshot_id

        @contextmanager
        def events_reader(self):
            with real.events_reader() as reader:
                def counted(run_ids):
                    calls.append(list(run_ids))
                    return reader(run_ids)
                yield counted

    page = history_list(snap, None, None)
    attach_requirement_truth(Counting(), page["runs"])
    assert len(calls) == 1 and sorted(calls[0]) == sorted(r["run_id"] for r in page["runs"])  # no query per run
    assert all(r["requirement_truth"]["status"] == "ALL_VERIFIED" for r in page["runs"])
    calls.clear()
    attach_requirement_truth(Counting(), [])
    assert calls == []  # an empty page reads nothing


class _Store:
    def __init__(self, snap):
        self.snap = snap

    def trace(self, snapshot):
        return SnapshotTrace(snapshot)


def test_the_service_projects_the_same_truth_on_list_and_detail(tmp_path):
    snap = _snapshot(tmp_path, {"run-open": [_truth_event("OPEN_REQUIREMENTS", 1, total=3)], "run-old": []})
    service = HistoryService(_Store(snap))
    listed = {r["run_id"]: r for r in service.list(None, None, snap, limit=None, cursor=None)["runs"]}
    for run_id in ("run-open", "run-old"):
        detail_run = service.detail(None, None, snap, run_id)["run"]
        assert detail_run.get("requirement_truth") == listed[run_id].get("requirement_truth")
    assert listed["run-open"]["requirement_truth"] == {"status": "OPEN_REQUIREMENTS", "verified": 1, "accepted": 0, "total": 3}
    assert "requirement_truth" not in listed["run-old"]
