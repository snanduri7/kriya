#!/usr/bin/env python
"""Synthetic trace-store fixtures for the A1 zero-write measurement (06 §A1).

Runs OUTSIDE the sandbox. Builds a ``traces.db`` with the baseline ``runs``
schema (kriya/core/trace.py:32-74, copied here on purpose: this spike never
imports kriya) and realistic JSON payloads, in the journal mode and
sidecar/crash state a case asks for. ``active-writer`` keeps committing rows
until it is terminated (the "active fixture writer process" case, D-4).
Synthetic content only; no path under ~/.kriya is ever touched.
"""
import argparse
import json
import os
import signal
import sqlite3
import sys
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT,
    goal TEXT,
    duration_sec REAL,
    attempts INTEGER,
    status TEXT,
    files_modified TEXT,
    retrieved_chunks TEXT,
    active_skills TEXT,
    prompt_rendered TEXT,
    gate_outcomes TEXT,
    model_hops TEXT,
    failure_category TEXT,
    milestone_group_id TEXT,
    milestone_index INTEGER,
    milestone_total INTEGER,
    run_events TEXT,
    evidence_records TEXT,
    generation_metrics TEXT,
    failure_report TEXT
);
CREATE TABLE IF NOT EXISTS milestone_plans (
    group_id TEXT PRIMARY KEY,
    timestamp TEXT,
    status TEXT,
    schema_version INTEGER,
    milestone_count INTEGER,
    dependency_edges INTEGER,
    extension_count INTEGER,
    composition_count INTEGER,
    validation_attempts INTEGER,
    validation_failures TEXT,
    repository_topology TEXT
);
"""

STATUSES = ["SUCCESS", "FAILED", "FAILED", "SUCCESS", "NEEDS_REVIEW"]


def synthetic_row(i: int, events: int = 40):
    # Equal timestamps on purpose for some rows (P-25 pagination with equal timestamps).
    ts = f"2026-09-{(i % 28) + 1:02d} 1{(i // 28) % 10}:00:00"
    run_events = [
        {
            "event": ["retrieval.expansion_seeds", "context.known_target_package",
                      "developer.prompt_composition", "model.transition"][k % 4],
            "attempt": k // 10 + 1,
            "source": "workflow",
            "authority": "deterministic",
            "at": f"{ts}.{k:03d}",
            "payload": {"k": k, "text": "quoted \"json\" \\ and unicode é — " * 8},
        }
        for k in range(events)
    ]
    return (
        f"run-{i:05d}",
        ts,
        f"Synthetic goal {i}: add a feature flag to module {i % 7}",
        12.5 + i,
        (i % 4) + 1,
        STATUSES[i % len(STATUSES)],
        f"src/mod{i % 7}/a.py,src/mod{i % 7}/b.py",
        json.dumps([{"file": f"src/mod{i % 7}/a.py", "score": 0.9}]),
        "skill-a,skill-b",
        "PLANNING PROMPT (fixture) " * 50,
        json.dumps([{"attempt": 1, "gate": "compile", "passed": i % 2 == 0}]),
        json.dumps([{"from": "model-a", "to": "model-b", "attempt": 2}]) if i % 3 == 0 else "[]",
        None if i % 5 == 0 else "quality_gate_failed",
        f"group-{i // 5}" if i % 2 else None,
        i % 5 if i % 2 else None,
        5 if i % 2 else None,
        json.dumps(run_events),
        json.dumps([{"evidence_id": f"ev-{i}", "revision": "sha256:" + "ab" * 32}]),
        json.dumps({"prompt_tokens_estimated": 1200 + i, "prompt_tokens_provider": 1180 + i}),
        json.dumps([{"failure_type": "compile", "category": "quality_gate_failed",
                     "attribution_tier": "locator"}]),
    )


def create(db: str, journal: str, rows: int, leave_sidecars: bool, hot_journal: bool) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
    conn = sqlite3.connect(db)
    mode = conn.execute(f"PRAGMA journal_mode={journal}").fetchone()[0]
    assert mode.lower() == journal.lower(), (mode, journal)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT OR REPLACE INTO runs VALUES (" + ",".join("?" * 20) + ")",
                     [synthetic_row(i) for i in range(rows)])
    conn.commit()
    if hot_journal:
        # Crash mid-transaction with the page cache spilled: SQLite has then
        # synced the rollback journal (real header, nRec > 0) and already
        # written modified pages into the main file, so the journal is HOT -
        # the next opener must roll it back (a write) to see consistent data.
        # (A journal left behind before any spill has a zeroed header, which
        # SQLite treats as not hot; measured 2026-10-04, first harness run.)
        conn.execute("PRAGMA cache_size=10")
        conn.executemany("INSERT INTO runs VALUES (" + ",".join("?" * 20) + ")",
                         [synthetic_row(50000 + i) for i in range(300)])
        os._exit(0)  # no rollback, no commit, no close
    if leave_sidecars:
        # Crash after commit: -wal and -shm stay behind, un-checkpointed.
        conn.execute("INSERT INTO runs (run_id, timestamp, goal) VALUES ('run-wal-tail', '2026-09-30 00:00:00', 'committed to WAL')")
        conn.commit()
        os._exit(0)
    conn.close()


def active_writer(db: str, interval: float) -> None:
    conn = sqlite3.connect(db, timeout=5.0)
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    n = 0
    while not stop["flag"]:
        n += 1
        conn.execute("INSERT OR REPLACE INTO runs VALUES (" + ",".join("?" * 20) + ")",
                     synthetic_row(100000 + n, events=5))
        conn.commit()
        if n == 1:
            print(json.dumps({"marker": "writer_ready", "pid": os.getpid()}), flush=True)
        time.sleep(interval)
    conn.close()
    print(json.dumps({"marker": "writer_stopped", "commits": n}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("db")
    c.add_argument("--journal", choices=["delete", "wal"], default="delete")
    c.add_argument("--rows", type=int, default=120)
    c.add_argument("--leave-sidecars", action="store_true")
    c.add_argument("--hot-journal", action="store_true")
    w = sub.add_parser("active-writer")
    w.add_argument("db")
    w.add_argument("--interval", type=float, default=0.02)
    args = parser.parse_args()
    if args.cmd == "create":
        create(args.db, args.journal, args.rows, args.leave_sidecars, args.hot_journal)
    else:
        active_writer(args.db, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
