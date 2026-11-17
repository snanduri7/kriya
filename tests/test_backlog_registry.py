"""The canonical backlog registry (handover/BACKLOG_REGISTRY.csv, 2026-09-27).

Every open or deferred item carries the fields a batch needs to decide
whether it can close: priority, status, where it was found, its target
scope, what it blocks, and the evidence that closes it. A batch cannot
close with an item that lacks a target scope. The registry is the one place
an open item's status lives: the task tracker never duplicates it.

BACKLOG-REGISTRY-CANNOT-REPRESENT-OPEN-P1-001 (owner ruling 2026-11-13): a P0/P1 that is not closed is represented truthfully, as
P0/P1 with status ACTIVE (it is being worked now). The earlier rule refused every non-closed P0/P1 row, so an active P1 was written
down as P2 with wording; severity is never lowered to satisfy this file.
"""
import csv
from pathlib import Path

HANDOVER = Path(__file__).resolve().parent.parent / "handover"
REQUIRED = ("id", "priority", "status", "discovered_in", "target_scope", "blocking", "closure_evidence")
STATUSES = {"OPEN", "ACTIVE", "DEFERRED", "CLOSED", "TRACKED_IN_OPEN_ITEMS"}
PRIORITIES = {"P0", "P1", "P2", "P3"}
BLOCKING_PRIORITIES = {"P0", "P1"}
# What a non-closed row says when it claims P0/P1 severity in its own text. "candidate" wording (severity not yet decided) is not a claim.
SEVERITY_CLAIMS = ("ACTIVE P0", "ACTIVE P1", "CONFIRMED P0", "CONFIRMED P1")
# The old workaround's wording: a lower priority recorded because of this test. Never again on a row that is not closed.
WEAKENED_WORDING = ("recorded P2 only because", "recorded P3 only because")


def _rows(name):
    with open(HANDOVER / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parked_blockers(rows):
    """Non-closed P0/P1 rows that are not ACTIVE: a blocker is worked now or it is closed; it is never OPEN, DEFERRED or tracked elsewhere."""
    return [row["id"] for row in rows
            if row["priority"] in BLOCKING_PRIORITIES and row["status"] not in {"CLOSED", "ACTIVE"}]


def misused_active(rows):
    """ACTIVE is the status of a P0/P1 being worked, nothing else."""
    return [row["id"] for row in rows if row["status"] == "ACTIVE" and row["priority"] not in BLOCKING_PRIORITIES]


def weakened_severity(rows):
    """Non-closed rows whose own text claims P0/P1 severity, or admits a lowered one, while the priority field says less."""
    found = []
    for row in rows:
        if row["status"] == "CLOSED":
            continue
        title = row.get("title") or ""
        claimed = [claim[-2:] for claim in SEVERITY_CLAIMS if claim in title]
        if any(claim != row["priority"] for claim in claimed) or any(wording in title for wording in WEAKENED_WORDING):
            found.append(row["id"])
    return found


def ragged_records(text):
    """(id, width) of every record of CSV ``text`` whose cell count is not the header's. A by-name reader silently shifts or drops the
    fields of such a record (an unquoted comma inside a field, or a missing last cell): REGISTRY-CSV-ROW-WIDTH-001."""
    records = list(csv.reader(text.splitlines(keepends=True)))
    return [(record[0] if record else "", len(record)) for record in records[1:] if len(record) != len(records[0])]


def test_every_record_has_exactly_the_headers_columns():
    with open(HANDOVER / "BACKLOG_REGISTRY.csv", newline="", encoding="utf-8") as handle:
        text = handle.read()
    assert text.startswith("id,") and ragged_records(text) == []
    # the check itself: an unquoted comma in a field and a missing last cell are both found, a quoted comma is not
    header = "id,title,priority\n"
    assert ragged_records(header + 'A,"one, two",P3\n') == []
    assert ragged_records(header + "A,one, two,P3\n") == [("A", 4)]
    assert ragged_records(header + "A,one\n") == [("A", 2)]


def test_every_item_carries_the_required_fields():
    rows = _rows("BACKLOG_REGISTRY.csv")
    assert rows
    missing = [(row.get("id"), field) for row in rows for field in REQUIRED if not (row.get(field) or "").strip()]
    assert missing == []
    assert all(row["status"] in STATUSES and row["priority"] in PRIORITIES for row in rows), rows


def test_ids_are_unique():
    ids = [row["id"] for row in _rows("BACKLOG_REGISTRY.csv")]
    assert len(ids) == len(set(ids))


def test_a_p0_or_p1_that_is_not_closed_is_active_never_parked():
    """Open P0/P1 defects block closure and are worked, not backlogged: the only non-closed status they may carry is ACTIVE."""
    rows = _rows("BACKLOG_REGISTRY.csv")
    assert parked_blockers(rows) == []
    assert misused_active(rows) == []


def test_a_confirmed_blocker_is_never_written_down_as_a_lower_priority():
    assert weakened_severity(_rows("BACKLOG_REGISTRY.csv")) == []


def test_the_representation_rule_itself():
    """Negative controls on the three checks (a registry that passes them all proves nothing about the checks)."""
    def row(priority, status, title="x"):
        return {"id": f"{priority}-{status}", "priority": priority, "status": status, "title": title}

    assert parked_blockers([row("P1", "ACTIVE"), row("P0", "ACTIVE"), row("P1", "CLOSED"), row("P2", "OPEN")]) == []
    assert parked_blockers([row("P1", "OPEN"), row("P0", "DEFERRED"), row("P1", "TRACKED_IN_OPEN_ITEMS")]) == [
        "P1-OPEN", "P0-DEFERRED", "P1-TRACKED_IN_OPEN_ITEMS"]
    assert misused_active([row("P2", "ACTIVE"), row("P3", "ACTIVE"), row("P1", "ACTIVE")]) == ["P2-ACTIVE", "P3-ACTIVE"]
    assert weakened_severity([row("P2", "OPEN", "ACTIVE P1 for the next run"), row("P2", "OPEN", "CONFIRMED P1 defect"),
                              row("P2", "OPEN", "recorded P2 only because a test forbids an open P1")]) == ["P2-OPEN"] * 3
    assert weakened_severity([row("P1", "ACTIVE", "ACTIVE P1 for the next run"), row("P2", "OPEN", "P1 CANDIDATE, not yet characterized"),
                              row("P2", "CLOSED", "ACTIVE P1 at the time; recorded P2 only because of the old rule")]) == []


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
