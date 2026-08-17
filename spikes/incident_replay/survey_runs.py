"""Survey every eval-harness run for Quality Gate failures, categorized by
type - the first step toward "grep every failure across every run and turn
it into a regression test set" (the actual test-writing stays a separate,
deliberate step - see this module's own docstring below for why).

Does NOT auto-generate tests. A failure line in a log proves a Quality Gate
fired; it does NOT by itself prove the underlying cause is still live in
current code, or that the "obvious" fix is the real one - both of those
need the same verify-before-trusting discipline every fix in docs/design.md
§§7.23-7.27 used (direct reproduction, checked against current code, a
regression test written by hand after confirming the real mechanism). This
script's job is triage: which failure shapes are common enough or novel
enough to be worth that treatment, not to skip the treatment.

Usage:
    .venv/bin/python spikes/incident_replay/survey_runs.py \\
        --runs-dir spikes/eval_harness/runs

Prints a failure-type frequency table across every run found, then a
per-run breakdown. Read-only - never touches the run directories.
"""
import argparse
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from replay_attempt import _FAIL_TYPE_PREFIXES, extract_raw_completions

_QUALITY_GATES_FAILED_RE = re.compile(r"Quality Gates FAILED \(Attempt (\d+), (\w+),")
_CLI_OUTCOME_RE = re.compile(r"\[(\S+)\] (TIMED OUT after \d+s|CLI exited \d+)")


def _classify(line: str) -> str:
    for prefix, fail_type in _FAIL_TYPE_PREFIXES:
        if prefix in line:
            return fail_type
    return "unclassified"


def find_run_logs(runs_dir: str) -> List[Tuple[str, str, str]]:
    """Returns (run_id, goal_id, log_path) for every workspaces/*/logs/
    kriya.log under runs_dir - deliberately NOT .kriya/worktree/logs/
    kriya.log (a redundant per-worktree copy, not the canonical one every
    incident this session was root-caused from)."""
    results = []
    runs_path = Path(runs_dir)
    for run_dir in sorted(runs_path.iterdir()):
        if not run_dir.is_dir():
            continue
        workspaces = run_dir / "workspaces"
        if not workspaces.is_dir():
            continue
        for goal_dir in sorted(workspaces.iterdir()):
            log_path = goal_dir / "logs" / "kriya.log"
            if log_path.exists():
                results.append((run_dir.name, goal_dir.name, str(log_path)))
    return results


def survey_one(log_path: str) -> Dict[str, int]:
    counts: Counter = Counter()
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "Quality Gates FAILED" in line:
                    counts[_classify(line)] += 1
    except OSError:
        pass
    return dict(counts)


def find_outcome(run_dir: str, goal_id: str) -> str:
    summary_path = os.path.join(run_dir, "summary.txt")
    if not os.path.exists(summary_path):
        return "unknown (no summary.txt)"
    with open(summary_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _CLI_OUTCOME_RE.search(line)
            if m and m.group(1) == goal_id:
                return m.group(2)
    return "unknown (goal not in summary.txt)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs-dir", required=True)
    args = parser.parse_args()

    entries = find_run_logs(args.runs_dir)
    print(f"Found {len(entries)} (run, goal) log files under {args.runs_dir}\n")

    total_counts: Counter = Counter()
    per_run_rows = []
    debug_capture_runs = []

    for run_id, goal_id, log_path in entries:
        run_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(log_path))))
        counts = survey_one(log_path)
        for k, v in counts.items():
            total_counts[k] += v
        outcome = find_outcome(run_dir, goal_id)
        per_run_rows.append((run_id, goal_id, outcome, counts))
        has_debug = False
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "Developer raw completion for" in line:
                        has_debug = True
                        break
        except OSError:
            pass
        if has_debug:
            debug_capture_runs.append((run_id, goal_id, log_path))

    print("=== Failure-type frequency across ALL runs ===")
    for fail_type, count in total_counts.most_common():
        print(f"  {fail_type:30s} {count}")
    print()

    print("=== Outcome frequency ===")
    outcome_counts: Counter = Counter(row[2] for row in per_run_rows)
    for outcome, count in outcome_counts.most_common():
        print(f"  {outcome:30s} {count}")
    print()

    print(f"=== Runs with DEBUG-level raw-completion capture (replayable): {len(debug_capture_runs)} ===")
    for run_id, goal_id, _ in debug_capture_runs:
        print(f"  {run_id} / {goal_id}")
    print()

    print("=== Per-run breakdown (only runs with at least one failure) ===")
    for run_id, goal_id, outcome, counts in per_run_rows:
        if not counts:
            continue
        counts_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {run_id:45s} {goal_id:25s} [{outcome}] {counts_str}")


if __name__ == "__main__":
    main()
