"""S5 - Incremental Change probe.

Analyzes/indexes a fixture repo once, then re-runs the identical deterministic
prep path three times: (a) immediately again with nothing changed (b) after
modifying exactly one file's content (c) after touching (mtime-only, same
content) that same file - to separate the mtime fast-path from the
content-hash fast-path. Measures file opens, elapsed time, and DB row deltas
for BOTH of Kriya's two independent repository walks: RepositoryAnalyzer.
analyze() (no caching at all, feeds Planner/Architect's RepositoryModel facts)
and RepositoryAnalyzer.index_repository() (mtime+hash cached, feeds Graph RAG).
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402
import probes  # noqa: E402

BAND_FILLER_COUNT = 45  # "small" S1 band


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="ctx001_s5_")
    root = os.path.join(tmp, "repo")
    mem = os.path.join(tmp, "memory")
    skl = os.path.join(tmp, "skills")
    fixtures.build_s1_fixture(root, band_filler_count=BAND_FILLER_COUNT)

    results = {"fixture": {"filler_files": BAND_FILLER_COUNT}, "runs": []}

    # --- analyze() has no caching at all: run it 3x with nothing changed,
    # to establish the "always full cost" baseline it pays every single time.
    analyze_runs = []
    for label in ("cold", "warm_no_change_1", "warm_no_change_2"):
        r = await probes.run_analyze(root)
        analyze_runs.append({"label": label, **r})
    results["analyze_no_caching_baseline"] = analyze_runs

    # --- index_repository(): cold, then warm/no-change, then one-file-changed,
    # then mtime-only-touch (same content, new mtime).
    target_rel = "core/billing/invoice_impl.py"
    target_abs = os.path.join(root, target_rel)

    r_cold = await probes.run_index_repository(root, mem, skills_dir=skl)
    results["runs"].append({"label": "index_cold", **r_cold})

    r_warm = await probes.run_index_repository(root, mem, skills_dir=skl)
    results["runs"].append({"label": "index_warm_no_change", **r_warm})

    # Modify exactly one file's real content (append a comment - changes hash).
    with open(target_abs, "r", encoding="utf-8") as fh:
        original_content = fh.read()
    time.sleep(1.05)  # ensure a distinguishable mtime (some filesystems have 1s resolution)
    with open(target_abs, "a", encoding="utf-8") as fh:
        fh.write("\n# CTX-001 S5 probe: single-file content mutation marker\n")

    r_one_change = await probes.run_index_repository(root, mem, skills_dir=skl)
    results["runs"].append({"label": "index_one_file_content_changed", "changed_file": target_rel, **r_one_change})

    # Touch same file (mtime bump, no content change) - isolates the mtime
    # fast path from the hash fast path.
    time.sleep(1.05)
    os.utime(target_abs, None)
    r_touch = await probes.run_index_repository(root, mem, skills_dir=skl)
    results["runs"].append({"label": "index_mtime_touch_same_content", "changed_file": target_rel, **r_touch})

    # Restore original content, confirm a real revert is also detected as a change.
    time.sleep(1.05)
    with open(target_abs, "w", encoding="utf-8") as fh:
        fh.write(original_content)
    r_revert = await probes.run_index_repository(root, mem, skills_dir=skl)
    results["runs"].append({"label": "index_reverted_to_original", "changed_file": target_rel, **r_revert})

    total_files = r_cold["db_counts"]["files_graph"]
    results["summary"] = {
        "total_files_indexed": total_files,
        "opens_cold": r_cold["opens"],
        "opens_warm_no_change": r_warm["opens"],
        "opens_one_file_changed": r_one_change["opens"],
        "opens_mtime_touch_same_content": r_touch["opens"],
        "opens_reverted": r_revert["opens"],
        "pct_of_repo_reprocessed_on_one_file_change": (
            round(100.0 * (r_one_change["opens"] - r_warm["opens"]) / max(1, total_files), 2)
        ),
        "analyze_opens_are_constant_regardless_of_change": len({a["opens"] for a in analyze_runs}) == 1,
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "s5_incremental_change.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    print(json.dumps(results["summary"], indent=2))
    print(f"\nFull results written to {out_path}")

    shutil.rmtree(tmp)


if __name__ == "__main__":
    asyncio.run(main())
