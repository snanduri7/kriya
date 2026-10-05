"""P3-C mutation campaign (P1/P4/P5 method: one exact single
replacement per mutant, the FS-1 test set, KILLED iff it fails). Each mutant
runs in its own fresh clone of the commit under test (never the working
tree, which a full-suite run may be using - rule 19).

usage: python p3c_mutation_campaign.py <commit>    -> p3c_mutation_results.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/p3c-impl")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p3c_mutation_results.json")
TESTS = ['tests/test_p3c_prose_candidate_delta.py', 'tests/test_p3_developer_protocol_reproducers.py', 'tests/test_workflow.py']
FR = "kriya/workflow/file_resolution.py"
AT = "kriya/workflow/attempt.py"
M = [
    ("baseline-subtraction-removed", FR, "        if unchanged[line] > 0:\n", "        if False:\n"),
    ("subtract-by-identity-not-count", FR, "            unchanged[line] -= 1\n", "            pass\n"),
    ("check-skipped-entirely", AT, "    if not contamination:\n        return\n    failure = Failure(\n        type=\"prose_contamination\",",
     "    if True:\n        return\n    failure = Failure(\n        type=\"prose_contamination\","),
    ("edit-path-baseline-is-the-candidate", AT,
     "            _reject_explanatory_prose(state, filepath, new_content, orig_text)\n",
     "            _reject_explanatory_prose(state, filepath, new_content, new_content)\n"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="p3c-mut-")
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
