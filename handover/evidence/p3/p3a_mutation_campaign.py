"""P3-A mutation campaign (P1/P4/P5 method: one exact single
replacement per mutant, the FS-1 test set, KILLED iff it fails). Each mutant
runs in its own fresh clone of the commit under test (never the working
tree, which a full-suite run may be using - rule 19).

usage: python p3a_mutation_campaign.py <commit>    -> p3a_mutation_results.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/p3-impl")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p3a_mutation_results.json")
TESTS = ["tests/test_p3a_sent_request_anchor_authority.py", "tests/test_p3_developer_protocol_reproducers.py",
         "tests/test_context_edit_protocol_001.py", "tests/test_prd016_adaptive_budget.py"]
AT = "kriya/workflow/attempt.py"
CB = "kriya/workflow/context_budget.py"
M = [
    ("pre-fit-prompt-recorded-as-sent", CB, "        self.fitted.append(fitted)\n", "        self.fitted.append(prompt)\n"),
    ("any-anchor-in-the-file-accepted", AT,
     "    if not any(norm_search in normalize_whitespace(piece) for piece in pieces):\n        return False\n",
     "    if False:\n        return False\n"),
    ("revision-of-sent-text-ignored", AT,
     "    if not sent or revision != capability.revision or not current.strip():\n",
     "    if not sent or not current.strip():\n"),
    ("stale-member-unit-accepted", AT,
     "        if member.is_exact and member.content and (not member.revision or member.revision == revision)\n",
     "        if member.is_exact and member.content\n"),
    ("member-unit-need-not-be-sent", AT,
     "        and any(member.content in prompt for prompt in sent))\n", "        )\n"),
    ("whole-file-need-not-be-sent", AT,
     "    pieces = [current] if any(current in prompt for prompt in sent) else []\n", "    pieces = [current]\n"),
    ("not-in-file-anchor-accepted-from-sent-text", AT,
     "        if status == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT and _sent_exact_source_covers(\n",
     "        if _sent_exact_source_covers(\n"),
    ("with-section-copy-not-recorded", CB,
     "        return DeveloperRequestFit(self.config, self.binding, self.sections + (section,), self.fitted)\n",
     "        return DeveloperRequestFit(self.config, self.binding, self.sections + (section,))\n"),
    ("sent-requests-never-kept", AT,
     "            state.edit_capability_sent[path] = tuple(request_fit.fitted)\n",
     "            state.edit_capability_sent[path] = ()\n"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="p3a-mut-")
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
