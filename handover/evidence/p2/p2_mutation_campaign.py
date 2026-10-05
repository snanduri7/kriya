"""P2 mutation campaign (P1/P4/P5 method: one exact single
replacement per mutant, the FS-1 test set, KILLED iff it fails). Each mutant
runs in its own fresh clone of the commit under test (never the working
tree, which a full-suite run may be using - rule 19).

usage: python p2_mutation_campaign.py <commit>    -> p2_mutation_results.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/p2-impl")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p2_mutation_results.json")
TESTS = ["tests/test_p2_compile_regression_attribution.py", "tests/test_p2_regression_attribution_reproducer.py",
         "tests/test_validation_baseline.py", "tests/test_lr_r1_m1_recovery.py"]
CR = "kriya/workflow/compile_regression.py"
WF = "kriya/workflow/workflow.py"
RS = "kriya/workflow/retry_strategy.py"
WC = "kriya/workflow/workflow_controller.py"
M = [
    # ---- Track B list
    ("level2-available-guard-dropped", CR, "    if delta is None or delta.level2_available:\n", "    if delta is None:\n"),
    ("pre-success-guard-dropped", CR,
     "    if delta.level1.classification is not DeltaClassification.NEW_FAILURE:\n", "    if False:\n"),
    ("locator-files-omitted-from-known-files", WF,
     "                            _regression_known_files = sorted(\n"
     "                                set(state.all_files_written) | set(_compile_regression.files))\n",
     "                            _regression_known_files = list(state.all_files_written)\n"),
    ("no-loci-carried-by-controller", WC,
     "                        grounded_locations=scope_conflict.get(\"grounded_locations\") or (),\n",
     "                        grounded_locations=(),\n"),
    ("no-loci-recorded-on-conflict", RS,
     "                    \"grounded_locations\": _grounded_locations(ctx.worktree_path, failure, outside_scope),\n",
     "                    \"grounded_locations\": [],\n"),
    ("loci-carried-without-revision-check", WF,
     "        if current is None or current != location.get(\"revision\"):\n", "        if current is None:\n"),
    ("ambiguous-resolution-accepted", CR,
     "    return matches[0] if len(matches) == 1 else None\n", "    return matches[0] if matches else None\n"),
    ("attribution-recorded-before-it-is-known", RS,
     "    state.record_failure(failure, operation=attempt_mode, diagnosis=False)\n",
     "    state.record_failure(failure, operation=attempt_mode, diagnosis=True)\n"),
    # ---- additional decision points
    ("warnings-counted-as-errors", CR, '_MAVEN_ERROR = re.compile(r"^\\[ERROR\\]\\s+"',
     '_MAVEN_ERROR = re.compile(r"^\\[(?:ERROR|WARNING)\\]\\s+"'),
    ("unresolved-diagnostic-skipped", CR, "        if resolved is None:\n            return None\n",
     "        if resolved is None:\n            continue\n"),
    ("seed-path-escape-accepted", WF,
     "        if (normalized and not os.path.isabs(normalized) and not normalized.startswith(\"..\")\n",
     "        if (normalized\n"),
    ("unattributed-stop-never-set", WF,
     "                        if _compile_regression is None:\n                            _full_regression_unattributed = True\n",
     "                        if _compile_regression is None:\n                            pass\n"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="p2-mut-")
    subprocess.run(["git", "clone", "-q", "--no-checkout", SRC, root], check=True)
    subprocess.run(["git", "checkout", "-q", commit], cwd=root, check=True)
    return root


def run_tests(repo):
    proc = subprocess.run(f"ulimit -n 256; {PYTEST} -q -x -p no:cacheprovider -n 8 " + " ".join(TESTS), shell=True,
                          cwd=repo, env=dict(os.environ, PYTHONPATH=repo), capture_output=True, text=True)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main(commit):
    results = {"commit": commit, "tests": TESTS, "mutants": []}
    baseline = clone(commit)
    try:
        code, tail = run_tests(baseline)
    finally:
        shutil.rmtree(baseline, ignore_errors=True)
    assert code == 0, tail
    results["baseline"] = tail
    print("baseline", tail, flush=True)
    for label, rel, old, new in M:
        repo = clone(commit)
        try:
            path = os.path.join(repo, rel)
            original = open(path).read()
            if original.count(old) != 1:
                entry = {"label": label, "file": rel, "status": f"PATTERN_COUNT_{original.count(old)}"}
            else:
                with open(path, "w") as handle:
                    handle.write(original.replace(old, new, 1))
                started = time.time()
                code, tail = run_tests(repo)
                entry = {"label": label, "file": rel, "status": "KILLED" if code else "SURVIVED",
                         "summary": tail, "seconds": round(time.time() - started, 1)}
        finally:
            shutil.rmtree(repo, ignore_errors=True)
        results["mutants"].append(entry)
        print(label, entry["status"], entry.get("summary", ""), flush=True)
    with open(OUT, "w") as handle:
        json.dump(results, handle, indent=1)
    statuses = [m["status"] for m in results["mutants"]]
    print("TOTAL", len(statuses), {s: statuses.count(s) for s in set(statuses)})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
