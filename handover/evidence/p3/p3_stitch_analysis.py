"""P3 stitch analysis: for every ANCHOR_NOT_IN_FILE SEARCH block, split it into
maximal runs of consecutive block lines that occur as a contiguous run in the
target file (whitespace-normalized per line, the anchor matcher's own
tolerance). A block whose lines are all real but that needs >= 2 runs was
stitched from non-adjacent file regions; the lines skipped between runs are
reported. Lines found in no run are altered/invented. Read-only.

usage: python p3_stitch_analysis.py > p3_stitch_analysis.json
"""
import json
import sys

from p3_anchor_analysis import RUNS, STORES, analyse


def norm(line):
    return " ".join(line.split())


def segments(block, source):
    file_lines = [norm(line) for line in source.split("\n")]
    block_lines = [norm(line) for line in block.split("\n")]
    while block_lines and not block_lines[-1]:
        block_lines.pop()
    runs, i, unmatched = [], 0, []
    while i < len(block_lines):
        best = None
        for start in range(len(file_lines)):
            length = 0
            while (i + length < len(block_lines) and start + length < len(file_lines)
                   and file_lines[start + length] == block_lines[i + length]):
                length += 1
            if length and (best is None or length > best[1]):
                best = (start, length)
        if best is None:
            unmatched.append(block_lines[i])
            i += 1
            continue
        runs.append({"block_from": i, "file_from": best[0] + 1, "file_to": best[0] + best[1], "lines": best[1]})
        i += best[1]
    gaps = [runs[k + 1]["file_from"] - runs[k]["file_to"] - 1 for k in range(len(runs) - 1)]
    return runs, gaps, unmatched


def main():
    import os

    from p3_anchor_analysis import load

    rows = []
    for label, run_id in RUNS.items():
        store = os.path.join(STORES, run_id)
        records, blob = load(store)
        for row in analyse(label, run_id):
            if row["reason_code"] != "ANCHOR_NOT_IN_FILE":
                continue
            recs = [r for r in records if r.get("unit_id") == row["unit"] and r.get("attempt_number") == row["attempt"]]
            outcome = json.loads(blob(next(r for r in recs if r["kind"] == "mirror.gate_outcome"
                                           and (r.get("blobs") or {}).get("outcome"))["blobs"]["outcome"]))
            source = outcome["failed_content"][row["target"]]
            for edit in outcome.get("attempted_edits") or []:
                if norm(edit["search"]) and " ".join(edit["search"].split()) in " ".join(source.split()):
                    continue  # this block matched; another block of the edit failed
                runs, gaps, unmatched = segments(edit["search"], source)
                rows.append({"run": label, "unit": row["unit"], "attempt": row["attempt"], "model": row["model"],
                             "runs": runs, "gaps_skipped_lines": gaps, "unmatched_lines": unmatched[:6],
                             "verdict": ("stitched" if len(runs) >= 2 and not unmatched else
                                         "altered" if unmatched else "single_run_other")})
    json.dump(rows, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
