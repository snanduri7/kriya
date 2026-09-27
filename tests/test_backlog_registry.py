"""The canonical backlog registry (handover/BACKLOG_REGISTRY.csv, 2026-09-27).

Every open or deferred item carries the fields a batch needs to decide
whether it can close: priority, status, where it was found, its target
scope, what it blocks, and the evidence that closes it. A batch cannot
close with an item that lacks a target scope. The registry is the one place
an open item's status lives: the task tracker never duplicates it.
"""
import csv
from pathlib import Path

HANDOVER = Path(__file__).resolve().parent.parent / "handover"
REQUIRED = ("id", "priority", "status", "discovered_in", "target_scope", "blocking", "closure_evidence")
STATUSES = {"OPEN", "DEFERRED", "CLOSED"}
PRIORITIES = {"P0", "P1", "P2", "P3"}


def _rows(name):
    with open(HANDOVER / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_every_item_carries_the_required_fields():
    rows = _rows("BACKLOG_REGISTRY.csv")
    assert rows
    missing = [(row.get("id"), field) for row in rows for field in REQUIRED if not (row.get(field) or "").strip()]
    assert missing == []
    assert all(row["status"] in STATUSES and row["priority"] in PRIORITIES for row in rows), rows


def test_ids_are_unique():
    ids = [row["id"] for row in _rows("BACKLOG_REGISTRY.csv")]
    assert len(ids) == len(set(ids))


def test_an_open_p0_or_p1_is_never_parked_in_the_backlog():
    """Open P0/P1 defects block closure and are worked, not backlogged."""
    parked = [row["id"] for row in _rows("BACKLOG_REGISTRY.csv")
              if row["status"] != "CLOSED" and row["priority"] in {"P0", "P1"}]
    assert parked == []


def test_the_tracker_never_duplicates_a_registry_item():
    registry = {row["id"] for row in _rows("BACKLOG_REGISTRY.csv")}
    tracker = {row["task_id"] for row in _rows("TASK_STATUS_TRACKER.csv")}
    assert registry & tracker == set()


def test_the_tracker_holds_no_open_finding():
    """A newly found defect or finding goes to the registry: the tracker
    keeps tasks, and defects only once they have been worked."""
    open_findings = [row["task_id"] for row in _rows("TASK_STATUS_TRACKER.csv")
                     if row["wave"] in {"Defect", "Finding"} and row["status"] == "OPEN"]
    assert open_findings == []
