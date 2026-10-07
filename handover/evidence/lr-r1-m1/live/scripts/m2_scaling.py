"""LR-R1-M1 M-2 offline scaling measurement (harness, not Kriya code).

Copies the live runs' real stores N times into a scratch state dir and times
Kriya's own ``prune_evidence`` (the function ``prune_after_run`` calls at run
close) at the packaged defaults (keep_runs=200, max_bytes=5 GiB), dry run so
nothing is removed and every N measures the same work. Reported separately
from the in-run close timing: this is an extrapolation input, not a live run.

usage: venv/bin/python scripts/m2_scaling.py <scratch dir> <run id>...
"""
import json
import os
import shutil
import statistics
import sys
import time

from kriya.core.attempt_evidence.retention import prune_evidence
from kriya.core.attempt_evidence.writer import run_directory, store_root

D = os.path.expanduser("~/kriya-m1-live")


def main(scratch, run_ids):
    sources = [run_directory(os.path.join(D, "state"), r) for r in run_ids]
    state = os.path.join(scratch, "state")
    shutil.rmtree(state, ignore_errors=True)
    os.makedirs(store_root(state))
    files_per_store = [sum(len(f) for _p, _d, f in os.walk(s)) for s in sources]
    results = []
    count = 0
    for target in (1, 10, 50, 100, 200, 400):
        while count < target:
            src = sources[count % len(sources)]
            shutil.copytree(src, run_directory(state, f"copy{count:04d}-{os.path.basename(src)}"))
            count += 1
        samples = []
        for _ in range(5):
            started = time.perf_counter()
            report = prune_evidence(state, keep_runs=200, max_bytes=5 * 1024 ** 3, protect_run_ids=(), dry_run=True)
            samples.append(time.perf_counter() - started)
        results.append({"stores": count, "median_seconds": round(statistics.median(samples), 5),
                        "max_seconds": round(max(samples), 5), "retained_bytes": report.retained_bytes,
                        "would_prune": len(report.pruned)})
        print(json.dumps(results[-1]))
    out = {"source_runs": run_ids, "files_per_source_store": files_per_store, "measurements": results,
           "note": "dry_run; page cache warm after the first sample; packaged retention defaults"}
    with open(os.path.join(D, "evidence", "m2_scaling.json"), "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)
    shutil.rmtree(state, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
