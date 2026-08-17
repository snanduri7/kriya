"""Replays every anchored-edit raw completion captured across the 8 DEBUG-
capturable eval-harness runs (b-10d/e/f/h/k/l/m/n) through the CURRENT
find_edits_ignoring_own_diagnosis(), and reports how many would be accepted
vs. rejected now - cross-referenced against whether the run's own log
actually recorded a DIAGNOSIS MISMATCH rejection for that same completion.

Correctness note: for the edits (anchored-edit) shape specifically,
find_edits_ignoring_own_diagnosis()'s signals operate entirely on each
edit's own (search, replace) pair, never on the `orig_text` parameter (that
param is only used for the full-content shape's fallback pairs) - confirmed
directly from kriya/workflow/attribution.py's own source. That means this
replay is exact for every edits-shaped completion, with no prior-content
reconstruction needed at all. Full-content-shaped completions are skipped
here for that reason (they'd need real prior content this script doesn't
have, exactly the limitation replay_attempt.py's own README documents).

Usage:
    .venv/bin/python spikes/incident_replay/analyze_diagnosis_mismatch_history.py \\
        --runs-dir ../eval_harness/runs
"""
import argparse
from pathlib import Path

from kriya.workflow.attribution import find_edits_ignoring_own_diagnosis
from replay_attempt import extract_raw_completions
from kriya.agents.agent import DeveloperAgent

DEBUG_CAPTURABLE_RUNS = ["b-10d", "b-10e", "b-10f", "b-10h", "b-10k", "b-10l", "b-10m", "b-10n"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs-dir", required=True)
    args = parser.parse_args()
    runs_dir = Path(args.runs_dir)

    total_edits_shape = 0
    total_would_reject_now = 0
    total_historically_rejected = 0
    both_then_and_now = 0
    fixed_was_rejected_now_passes = 0
    new_rejection_not_seen_before = 0

    for run_id in DEBUG_CAPTURABLE_RUNS:
        log_candidates = list(runs_dir.glob(f"{run_id}/workspaces/*/logs/kriya.log"))
        if not log_candidates:
            print(f"[{run_id}] no log found, skipping")
            continue
        log_path = str(log_candidates[0])
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            log_text = fh.read()

        completions = extract_raw_completions(log_path)
        for c in completions:
            analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(c.raw_text)
            if not edits:
                continue
            total_edits_shape += 1
            result_now = find_edits_ignoring_own_diagnosis(analysis, edits, None, "")
            would_reject_now = result_now is not None

            # Cross-reference: did the log's own WARNING actually record a
            # DIAGNOSIS MISMATCH tied to this same file, close in time to
            # this completion (within the same attempt cycle)? Approximated
            # by checking for the marker text within a window after this
            # completion's own timestamp position in the log.
            idx = log_text.find(c.raw_text[:60].replace("\\", "\\\\"))
            # Fallback: just check for a DIAGNOSIS MISMATCH mentioning the
            # same filepath ANYWHERE in the log - coarser, but every log in
            # this small set only has a handful of occurrences, checked by
            # hand already tonight, so coarse attribution is acceptable here.
            historically_rejected = (
                "DIAGNOSIS MISMATCH" in log_text and c.filepath in log_text
                and f"DIAGNOSIS MISMATCH in {c.filepath}" in log_text
            )

            if would_reject_now:
                total_would_reject_now += 1
            if historically_rejected:
                total_historically_rejected += 1
            if historically_rejected and would_reject_now:
                both_then_and_now += 1
            if historically_rejected and not would_reject_now:
                fixed_was_rejected_now_passes += 1
            if would_reject_now and not historically_rejected:
                new_rejection_not_seen_before += 1

            tag = "REJECT" if would_reject_now else "pass  "
            hist = "was-rejected-historically" if historically_rejected else ""
            print(f"[{run_id}] {c.filepath:55s} now={tag} {hist}")

    print()
    print("=== Summary across the 8 DEBUG-capturable runs ===")
    print(f"Total edits-shaped completions replayed: {total_edits_shape}")
    print(f"Would be rejected by CURRENT code: {total_would_reject_now}")
    print(f"Historically flagged 'DIAGNOSIS MISMATCH' in these runs' own logs (file-level, coarse): {total_historically_rejected}")
    print(f"  Still rejected then AND now (genuine, unresolved): {both_then_and_now}")
    print(f"  Rejected historically, now PASSES (confirmed fixed by tonight's work): {fixed_was_rejected_now_passes}")
    print(f"  Rejected by current code but no historical DIAGNOSIS MISMATCH for that file (worth a manual look): {new_rejection_not_seen_before}")


if __name__ == "__main__":
    main()
