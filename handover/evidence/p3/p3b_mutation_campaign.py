"""P3-B mutation campaign (P1/P4/P5 method: one exact single
replacement per mutant, the FS-1 test set, KILLED iff it fails). Each mutant
runs in its own fresh clone of the commit under test (never the working
tree, which a full-suite run may be using - rule 19).

usage: python p3b_mutation_campaign.py <commit>    -> p3b_mutation_results.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/p3b-impl")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p3b_mutation_results.json")
TESTS = ["tests/test_p3b_stitched_anchor_loci.py", "tests/test_p3_developer_protocol_reproducers.py",
         "tests/test_context_edit_protocol_001.py"]
EC = "kriya/workflow/edit_capability.py"
M = [
    ("gap-loci-dropped", EC, "            return list(range(stitched[0][0] + 1, stitched[-1][1] + 1))\n",
     "            return [line + 1 for start, end in stitched for line in range(start, end)]\n"),
    ("stitch-detection-skipped", EC, "        stitched = _stitched_runs(normalized_lines, block)\n",
     "        stitched = []\n"),
    ("ambiguous-part-accepted", EC, "    return starts[0] if len(starts) == 1 else None\n",
     "    return starts[0] if starts else None\n"),
    ("out-of-order-parts-accepted", EC,
     "        if found is None or (parts and found[0] < parts[-1][1]):\n", "        if found is None:\n"),
    ("shortest-part-not-longest", EC, "        for end in range(len(block), index, -1):\n",
     "        for end in range(index + 1, len(block) + 1):\n"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="p3b-mut-")
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
