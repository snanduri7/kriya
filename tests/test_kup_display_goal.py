"""display_goal (PRODUCT-UX-COMPLETION): the run title shown to a person is the ORIGINAL user request, resolved from structured fields only.

Shape MEASURED in the Scenario A state store: the unit row holds the COMPILED "=== Authoritative Goal ===" prompt and names the terminal ``<run>.enforce`` row through
``enforce_run_id``; that terminal row holds the original request. The projection must never extract text from a prompt, and must say "unavailable" rather than guess.
"""
from __future__ import annotations

import sqlite3

import pytest

from kriya.agents.contracts import AUTHORITATIVE_GOAL_SECTION_HEADER
from kriya.core.trace import TraceLogger
from kriya.kup import policy
from kriya.kup.inspect import history_detail, history_list
from kriya.kup.store import Snapshot

ORIGINAL = "Fix a bug in the existing Spring Boot driver service: `DefaultDriverService.delete(Long driverId)` must persist the soft delete."
COMPILED = (f"{AUTHORITATIVE_GOAL_SECTION_HEADER}\nThis is the real, unmediated user request - the source of truth for what is actually required.\n\n"
            f"{ORIGINAL}\n\n=== Planned Implementation ===\nPlanner detail the user never wrote.")


def _snapshot(tmp_path, rows):
    db = str(tmp_path / "traces.db")
    logger = TraceLogger(db)
    for row in rows:
        logger.log_run(run_id=row["id"], goal=row["goal"], duration_sec=1.0, attempts=0, status="success", files_modified=["A.java"],
                       enforce_run_id=row.get("enforce"))
    return Snapshot(snapshot_id="snap-1", directory=str(tmp_path), db_path=db, manifest={})


def _by_id(data):
    return {r["run_id"]: r for r in data["runs"]}


def test_header_constant_matches_the_agents_contract():
    assert policy.COMPILED_GOAL_HEADER == AUTHORITATIVE_GOAL_SECTION_HEADER


def test_a_unit_row_shows_the_original_request_of_its_enforce_row(tmp_path):
    snap = _snapshot(tmp_path, [{"id": "unit1", "goal": COMPILED, "enforce": "run-1.enforce"}, {"id": "run-1.enforce", "goal": ORIGINAL}])
    rows = _by_id(history_list(snap, None, None))
    assert rows["unit1"]["display_goal"] == ORIGINAL
    assert rows["unit1"]["display_goal_source"] == policy.DISPLAY_GOAL_ENFORCE_ROW
    assert rows["unit1"]["goal"] == COMPILED  # the stored column is verbatim; only the projection differs
    assert rows["run-1.enforce"]["display_goal"] == ORIGINAL and rows["run-1.enforce"]["display_goal_source"] == policy.DISPLAY_GOAL_OWN


def test_detail_carries_the_same_projection(tmp_path):
    snap = _snapshot(tmp_path, [{"id": "unit1", "goal": COMPILED, "enforce": "run-1.enforce"}, {"id": "run-1.enforce", "goal": ORIGINAL}])
    run = history_detail(snap, "unit1")["run"]
    assert (run["display_goal"], run["display_goal_source"]) == (ORIGINAL, policy.DISPLAY_GOAL_ENFORCE_ROW)


def test_a_plain_run_shows_its_own_goal(tmp_path):
    snap = _snapshot(tmp_path, [{"id": "plain", "goal": "Add a clamp function with tests."}])
    row = _by_id(history_list(snap, None, None))["plain"]
    assert (row["display_goal"], row["display_goal_source"]) == ("Add a clamp function with tests.", policy.DISPLAY_GOAL_OWN)


OTHER_WRAPPERS = ["=== Retry Package ===\nprevious failure ...\n" + ORIGINAL, "  === Milestone 2 of 3 ===\n" + ORIGINAL, "=== Anything At All ===\n" + ORIGINAL]


def _fresh(tmp_path, name, rows):
    folder = tmp_path / name
    folder.mkdir()
    return _snapshot(folder, rows)


@pytest.mark.parametrize("wrapped", OTHER_WRAPPERS)
def test_any_section_header_wrapper_is_never_a_title_for_a_plain_row_or_a_linked_row(tmp_path, wrapped):
    plain = _by_id(history_list(_fresh(tmp_path, "plain", [{"id": "p", "goal": wrapped}]), None, None))["p"]
    assert plain["display_goal"] is None and plain["display_goal_source"] == policy.DISPLAY_GOAL_UNAVAILABLE
    linked = _by_id(history_list(_fresh(tmp_path, "linked", [{"id": "u", "goal": COMPILED, "enforce": "e.enforce"}, {"id": "e.enforce", "goal": wrapped}]), None, None))["u"]
    assert linked["display_goal"] is None


@pytest.mark.parametrize("goal", ["Make the === operator strict in the parser", "Fix a === b comparison\nin two places", "=== improve logging in the parser"])
def test_a_real_request_that_merely_mentions_equals_signs_is_still_a_title(tmp_path, goal):
    row = _by_id(history_list(_fresh(tmp_path, "real", [{"id": "r", "goal": goal}]), None, None))["r"]
    assert row["display_goal"] == goal and row["display_goal_source"] == policy.DISPLAY_GOAL_OWN


@pytest.mark.parametrize("rows", [
    [{"id": "unit1", "goal": COMPILED, "enforce": "gone.enforce"}],                       # the link points at a row that is not there
    [{"id": "unit1", "goal": COMPILED}],                                                  # a compiled unit goal with no link at all
    [{"id": "unit1", "goal": COMPILED, "enforce": "e.enforce"}, {"id": "e.enforce", "goal": COMPILED}],  # the linked row is itself compiled
    [{"id": "unit1", "goal": COMPILED, "enforce": "e.enforce"}, {"id": "e.enforce", "goal": "   "}],     # the linked row has no text
])
def test_no_authoritative_request_is_unavailable_never_extracted_from_the_prompt(tmp_path, rows):
    row = _by_id(history_list(_snapshot(tmp_path, rows), None, None))["unit1"]
    assert row["display_goal"] is None and row["display_goal_source"] == policy.DISPLAY_GOAL_UNAVAILABLE
    # the original sentence is INSIDE the compiled prompt; a heuristic parser would find it. It must not.
    assert ORIGINAL not in str(row["display_goal"])


def test_a_store_without_the_enforce_column_still_projects_own_goals(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, timestamp TEXT, goal TEXT, duration_sec REAL, attempts INTEGER, status TEXT, files_modified TEXT, "
                 "failure_category TEXT, milestone_group_id TEXT, milestone_index INTEGER, milestone_total INTEGER)")
    conn.execute("INSERT INTO runs VALUES ('old1','2026-01-01 00:00:00','An older goal',1.0,1,'success','a.py',NULL,NULL,NULL,NULL)")
    conn.execute("INSERT INTO runs VALUES ('old2','2026-01-01 00:00:01',?,1.0,1,'success','a.py',NULL,NULL,NULL,NULL)", (COMPILED,))
    conn.commit()
    conn.close()
    rows = _by_id(history_list(Snapshot(snapshot_id="s", directory=str(tmp_path), db_path=db, manifest={}), None, None))
    assert rows["old1"]["display_goal"] == "An older goal"
    assert rows["old2"]["display_goal"] is None and rows["old2"]["display_goal_source"] == policy.DISPLAY_GOAL_UNAVAILABLE
