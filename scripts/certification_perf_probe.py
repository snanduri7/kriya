#!/usr/bin/env python3
"""PRD-034: the bounded performance probe that must pass before a canonical certification run (scripts/certify.sh stage ``perf_probe``).

usage: certification_perf_probe.py OUT_DIR

CERT-TMP-GROWTH-001: the first canonical run (c3-run1, 2026-11-02) fell from normal throughput to ~4 tests/min with ~83 % of its CPU in the kernel.
MEASURED cause: ``kriya.platform.filesystem_semantics.canonical_spelling`` lists every ancestor directory of a path in full, and the directories it
listed were the system temp root (23k entries of earlier runs' leftovers) and pytest's basetemp (5 entries per test, never emptied: 47k by test 9,700).

The probe runs a small, fixed, representative population in the SAME environment the canonical run uses (the caller's TMPDIR), and judges MECHANISM
INVARIANTS, not a wall-clock budget (the suite's legitimate nodes range from milliseconds to ~9 minutes):

  1. every probe test passed;
  2. the temp root it runs in started essentially empty (a polluted ambient temp area is exactly what crawled) and the tests leave (almost)
     nothing in it: a test that leaks a scratch directory per run re-creates that pollution (tests/_milestone_proof_harness.py did, ~2 per run);
  3. pytest's basetemp does not GROW while tests pass (entries after the last test <= entries after the first few + SLACK);
  4. the time spent listing directories is a small fraction of the wall time.

A violation means the crawl can recur: certification stays blocked. It also records tests, elapsed, tests/s, user/system CPU, the slowest nodes and
the measured per-class rates, from which only an ORDER-OF-MAGNITUDE bound on a full run is stated (never a forecast).
"""
import json
import os
import resource
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# engine class: real-engine milestone runs, the path that listed ancestors ~345 times per test; ordinary class: many fast unit tests.
PROBE_FILES = {"engine": ["tests/test_prd008_s4c_milestone_resume.py"], "ordinary": ["tests/test_agents.py"]}
TMP_ROOT_MAX_ENTRIES = 50  # the temp root a certification runs in must start (nearly) empty
TMP_ROOT_GROWTH_MAX = 10  # entries a probe class may leave behind in the temp root
BASETEMP_GROWTH_SLACK = 25  # entries
LISTING_FRACTION_MAX = 0.15


def evaluate(metrics: Mapping[str, Any], first_class: bool = True) -> Tuple[bool, List[str]]:
    """The probe verdict over measured ``metrics`` (pure, so it is unit-tested with synthetic input)."""
    problems: List[str] = []
    if metrics["failed"] or metrics["exit_status"] != 0:
        problems.append(f"probe tests did not all pass (exit {metrics['exit_status']}, {metrics['failed']} failed)")
    growth = metrics["tmp_root_entries_after"] - metrics["tmp_root_entries_before"]
    if growth > TMP_ROOT_GROWTH_MAX:
        problems.append(f"the tests left {growth} entries in the temp root (> {TMP_ROOT_GROWTH_MAX}): a per-test scratch directory is never removed")
    if first_class and metrics["tmp_root_entries_before"] > TMP_ROOT_MAX_ENTRIES:
        problems.append(f"the temp root already held {metrics['tmp_root_entries_before']} entries before the probe (> {TMP_ROOT_MAX_ENTRIES}): "
                        "a polluted temp area is what made every directory listing O(thousands)")
    samples = metrics["basetemp_samples"]
    if len(samples) >= 20:
        early = max(entries for _, entries in samples[:10])
        last = samples[-1][1]
        if last > early + BASETEMP_GROWTH_SLACK:
            problems.append(f"pytest's basetemp grew from {early} to {last} entries over {samples[-1][0]} passing tests: per-test directories are not being released")
    if metrics["wall_s"] > 0 and metrics["listing_s"] / metrics["wall_s"] > LISTING_FRACTION_MAX:
        problems.append(f"{100 * metrics['listing_s'] / metrics['wall_s']:.0f}% of the wall time is directory listing (> {100 * LISTING_FRACTION_MAX:.0f}%), "
                        f"worst: {metrics['listing_worst_path']} with {metrics['listing_max_entries']} entries")
    return not problems, problems


def _entries(path: str) -> int:
    try:
        return len(os.listdir(path))
    except OSError:
        return 0


def _run_class(name: str, files: List[str], out: str) -> Dict[str, Any]:
    tmp_root = os.environ.get("TMPDIR") or "/tmp"
    probe_out = os.path.join(out, f"{name}-listing.json")
    junit = os.path.join(out, f"{name}-junit.xml")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PERF_PROBE_OUT=probe_out,
               PYTHONPATH=os.pathsep.join(filter(None, [os.path.join(ROOT, "tests"), os.environ.get("PYTHONPATH")])))
    command = [sys.executable, "-m", "pytest", "-o", "addopts=", "-m", "not live_model", "-p", "no:cacheprovider", "-p", "_perf_probe_plugin", "-q",
               "--durations=5", f"--junitxml={junit}", *files]
    before_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    entries_before = _entries(tmp_root)
    started = time.time()
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    wall = time.time() - started
    after_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    with open(os.path.join(out, f"{name}-pytest.txt"), "w", encoding="utf-8") as handle:
        handle.write(result.stdout + result.stderr)
    measured: Dict[str, Any] = {"listing": {"seconds": 0.0, "max_entries": 0, "worst_path": ""}, "basetemp_samples": []}
    try:
        with open(probe_out, "r", encoding="utf-8") as handle:
            measured = json.load(handle)
    except (OSError, ValueError):
        pass
    summary = [line for line in result.stdout.splitlines() if " passed" in line or " failed" in line or " error" in line][-1:]
    tests = len(measured["basetemp_samples"])
    failed = 0
    for word in (summary[0].split(",") if summary else []):
        if "failed" in word or "error" in word:
            failed += int(word.split()[0])
    user = after_children.ru_utime - before_children.ru_utime
    system = after_children.ru_stime - before_children.ru_stime
    return {
        "class": name, "files": files, "tests": tests, "wall_s": round(wall, 1), "tests_per_s": round(tests / wall, 3) if wall else None,
        "user_s": round(user, 1), "system_s": round(system, 1), "system_fraction": round(system / (user + system), 3) if user + system else None,
        "exit_status": result.returncode, "failed": failed, "summary": summary[0] if summary else "",
        "tmp_root": tmp_root, "tmp_root_entries_before": entries_before, "tmp_root_entries_after": _entries(tmp_root),
        "basetemp_samples": measured["basetemp_samples"], "listing_s": round(measured["listing"]["seconds"], 2),
        "listing_max_entries": measured["listing"]["max_entries"], "listing_worst_path": measured["listing"]["worst_path"],
    }


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = argv[1]
    os.makedirs(out, exist_ok=True)
    classes, problems = [], []
    for index, (name, files) in enumerate(PROBE_FILES.items()):
        metrics = _run_class(name, files, out)
        ok, class_problems = evaluate(metrics, first_class=index == 0)
        metrics["ok"] = ok
        classes.append(metrics)
        problems += [f"{name}: {p}" for p in class_problems]
    rates = {c["class"]: c["tests_per_s"] for c in classes if c["tests_per_s"]}
    total = None
    census = os.path.join(out, "..", "collection", "selected.json")
    try:
        with open(census, "r", encoding="utf-8") as handle:
            total = json.load(handle)["count"]
    except (OSError, ValueError, KeyError):
        pass
    bound = None
    if total and rates:
        bound = {"tests": total, "if_all_at_the_fastest_probe_class_hours": round(total / max(rates.values()) / 3600, 2),
                 "if_all_at_the_slowest_probe_class_hours": round(total / min(rates.values()) / 3600, 1),
                 "note": "an order-of-magnitude BOUND between the measured classes, not a forecast; the watchdog (scripts/certification_watch.py) judges the run against its own throughput"}
    report = {"ok": not problems, "problems": problems, "classes": classes, "full_run_bound": bound, "invariants": {
        "tmp_root_max_entries": TMP_ROOT_MAX_ENTRIES, "tmp_root_growth_max": TMP_ROOT_GROWTH_MAX, "basetemp_growth_slack": BASETEMP_GROWTH_SLACK, "listing_fraction_max": LISTING_FRACTION_MAX}}
    with open(os.path.join(out, "perf-probe.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    for c in classes:
        print(f"[perf-probe] {c['class']}: {c['tests']} tests in {c['wall_s']}s ({c['tests_per_s']} tests/s), sys {c['system_fraction']}, "
              f"listing {c['listing_s']}s (max {c['listing_max_entries']} entries), temp root {c['tmp_root_entries_before']}->{c['tmp_root_entries_after']} entries")
    print(f"[perf-probe] {'PASS' if report['ok'] else 'FAIL'}" + "".join(f"\n  - {p}" for p in problems))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
