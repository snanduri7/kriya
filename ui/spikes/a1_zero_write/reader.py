#!/usr/bin/env python
"""A1 read candidate (06 §A1, P-29). Runs INSIDE ``sandbox-exec`` with
``(deny file-write*)``. It never imports ``kriya`` (kriya/core/db.py:20
monkey-patches sqlite3.connect with a WAL pragma, which this spike must not
inherit) and it runs exactly the query shapes the Phase C adapter will use
(P-25): capabilities, history.list (ORDER BY/LIMIT, cursor page),
history.detail by run_id and history.prompt.

Allowed connection settings: ``file:<path>?mode=ro`` with ``uri=True``, a
bounded busy timeout, and ``PRAGMA temp_store=MEMORY`` (connection-local;
keeps SQLite's own scratch storage out of the filesystem so a sandbox denial
can only ever be the store, its -wal/-shm/-journal sidecars, or a harness
bug). No journal_mode pragma, no WAL pragma, no immutable=1.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from urllib.request import pathname2url

LIST_COLUMNS = (
    "run_id, timestamp, goal, duration_sec, attempts, status, files_modified, "
    "failure_category, failure_report, milestone_group_id, milestone_index, milestone_total"
)
LIST_SQL = f"SELECT {LIST_COLUMNS} FROM runs ORDER BY timestamp DESC, run_id DESC LIMIT ?"
LIST_CURSOR_SQL = (
    f"SELECT {LIST_COLUMNS} FROM runs WHERE (timestamp < ? OR (timestamp = ? AND run_id < ?)) "
    "ORDER BY timestamp DESC, run_id DESC LIMIT ?"
)
DETAIL_SQL = (
    "SELECT run_id, timestamp, goal, duration_sec, attempts, status, files_modified, "
    "retrieved_chunks, active_skills, gate_outcomes, model_hops, failure_category, "
    "milestone_group_id, milestone_index, milestone_total, run_events, evidence_records, "
    "generation_metrics, failure_report FROM runs WHERE run_id = ?"
)
PROMPT_SQL = "SELECT prompt_rendered FROM runs WHERE run_id = ?"
CAPS_SQL = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
COUNT_SQL = "SELECT COUNT(*) FROM runs"


def read_only_uri(path: str) -> str:
    return "file:" + pathname2url(os.path.abspath(path)) + "?mode=ro"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--timeout", type=float, default=2.0, help="busy timeout, seconds (bounded)")
    parser.add_argument("--pause-after-first-row", type=float, default=0.0,
                        help="print a marker after the first list row, then sleep (the harness "
                             "deletes the store during this pause for the vanish case)")
    parser.add_argument("--page-size", type=int, default=50)
    args = parser.parse_args()

    report = {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "uri": read_only_uri(args.db),
        "steps": [],
    }

    def run_step(name, fn):
        started = time.monotonic()
        entry = {"step": name}
        try:
            entry["result"] = fn()
            entry["ok"] = True
        except sqlite3.Error as error:
            entry["ok"] = False
            entry["error"] = str(error)
            entry["sqlite_errorname"] = getattr(error, "sqlite_errorname", None)
            entry["sqlite_errorcode"] = getattr(error, "sqlite_errorcode", None)
        except OSError as error:
            entry["ok"] = False
            entry["error"] = f"{type(error).__name__}: {error}"
        entry["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        report["steps"].append(entry)
        return entry

    conn_box = {}

    def connect():
        conn_box["conn"] = sqlite3.connect(report["uri"], uri=True, timeout=args.timeout)
        return "connected"

    if not run_step("connect", connect)["ok"]:
        print(json.dumps(report))
        return 0
    conn = conn_box["conn"]

    run_step("pragma_temp_store_memory", lambda: conn.execute("PRAGMA temp_store=MEMORY").fetchall())
    run_step("pragma_journal_mode_read", lambda: conn.execute("PRAGMA journal_mode").fetchone()[0])
    run_step("capabilities_tables", lambda: [r[0] for r in conn.execute(CAPS_SQL).fetchall()])
    run_step("count", lambda: conn.execute(COUNT_SQL).fetchone()[0])

    first_page = {}

    def list_first_page():
        cursor = conn.execute(LIST_SQL, (args.page_size,))
        first = cursor.fetchone()
        if first is not None and args.pause_after_first_row > 0:
            print(json.dumps({"marker": "first_row_fetched", "pid": os.getpid()}), flush=True)
            time.sleep(args.pause_after_first_row)
        rest = cursor.fetchall()
        rows = ([first] if first is not None else []) + rest
        first_page["rows"] = rows
        return {"rows": len(rows), "first_run_id": first[0] if first else None}

    run_step("history_list_page1", list_first_page)

    def list_second_page():
        rows = first_page.get("rows") or []
        if not rows:
            return {"rows": 0, "note": "no first page"}
        last = rows[-1]
        page = conn.execute(LIST_CURSOR_SQL, (last[1], last[1], last[0], args.page_size)).fetchall()
        return {"rows": len(page)}

    run_step("history_list_page2_cursor", list_second_page)

    def detail():
        rows = first_page.get("rows") or []
        run_id = rows[0][0] if rows else "missing-run"
        row = conn.execute(DETAIL_SQL, (run_id,)).fetchone()
        return {"found": row is not None, "run_events_bytes": len(row[15] or "") if row else 0}

    run_step("history_detail", detail)

    def prompt():
        rows = first_page.get("rows") or []
        run_id = rows[0][0] if rows else "missing-run"
        row = conn.execute(PROMPT_SQL, (run_id,)).fetchone()
        return {"found": row is not None, "prompt_bytes": len(row[0] or "") if row else 0}

    run_step("history_prompt", prompt)
    run_step("close", lambda: (conn.close(), "closed")[1])
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
