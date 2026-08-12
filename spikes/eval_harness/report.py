"""Reads a batch's traces.db (written by run_harness.py, via the same
TraceLogger every `kriya generate`/`fix` run uses) and reports pass rate plus
a failure_category breakdown - the actual prioritization signal this harness
exists to produce. Unlike spikes/version_b_routing/run_spike.py, there's no
fixed go/no-go bar here: this is ongoing measurement, not a one-time
feasibility check, so the useful output is the distribution itself, not a
pass/fail verdict on the harness.

Usage:
    .venv/bin/python spikes/eval_harness/report.py --logs-dir spikes/eval_harness/runs/<batch>/logs
    # or let it find the most recent batch automatically:
    .venv/bin/python spikes/eval_harness/report.py
"""
import argparse
import glob
import os
import sqlite3
from collections import Counter

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_latest_traces_db():
    candidates = sorted(glob.glob(os.path.join(HARNESS_DIR, "runs", "*", "logs", "traces.db")))
    return candidates[-1] if candidates else None


def _load_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runs ORDER BY timestamp ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs-dir", default=None, help="Directory containing this batch's traces.db.")
    parser.add_argument("--db-path", default=None, help="Direct path to a traces.db, overrides --logs-dir.")
    args = parser.parse_args()

    if args.db_path:
        db_path = args.db_path
    elif args.logs_dir:
        db_path = os.path.join(args.logs_dir, "traces.db")
    else:
        db_path = _find_latest_traces_db()

    if not db_path or not os.path.exists(db_path):
        print(f"No traces.db found (looked at: {db_path}). Run run_harness.py first.")
        return

    rows = _load_rows(db_path)
    if not rows:
        print(f"{db_path} exists but has no rows yet.")
        return

    print(f"Eval harness report: {db_path}")
    print(f"Total rows: {len(rows)}  (a single goal can produce more than one row - e.g. a "
          "knowledge_gap gate followed by an auto-confirmed retry - see goals.py)\n")

    status_counts = Counter(r["status"] for r in rows)
    total = len(rows)
    passed = status_counts.get("success", 0)
    print(f"Pass rate: {passed}/{total} ({passed / total:.0%})\n")

    print("By status:")
    for status, count in status_counts.most_common():
        print(f"  {status:<16} {count}")
    if status_counts.get("in_progress"):
        print(
            "  NOTE: 'in_progress' means a knowledge-gap retry genuinely started running "
            "(the real generation attempt, not just the gate check) but never reached its "
            "own final result - almost always killed by this harness's own --timeout-per-goal "
            "before it could finish, not an instant/benign halt. Check that goal's own "
            "*.stdout.log for what actually happened before treating it as a fast failure."
        )

    category_counts = Counter(r["failure_category"] for r in rows if r["failure_category"])
    if category_counts:
        print("\nBy failure_category (non-success rows):")
        for category, count in category_counts.most_common():
            print(f"  {category:<28} {count}")

    print("\nPer-run detail:")
    print(f"  {'TIMESTAMP':<20} | {'STATUS':<16} | {'CATEGORY':<24} | {'ATTEMPTS':<8} | {'DURATION':<9} | GOAL")
    for r in rows:
        category = r["failure_category"] or ""
        duration = f"{r['duration_sec']:.1f}s" if r["duration_sec"] is not None else "?"
        goal_preview = (r["goal"] or "")[:60].replace("\n", " ")
        print(f"  {r['timestamp']:<20} | {r['status']:<16} | {category:<24} | {r['attempts']:<8} | {duration:<9} | {goal_preview}")


if __name__ == "__main__":
    main()
