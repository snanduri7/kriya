"""FS-1C1 mutation campaign (P1/P4/P5 method: one exact single
replacement per mutant, the FS-1 test set, KILLED iff it fails). Each mutant
runs in its own fresh clone of the commit under test (never the working
tree, which a full-suite run may be using - rule 19).

usage: python fs1c1_mutation_campaign.py <commit>    -> fs1c1_mutation_results.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/fs1c1")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fs1c1_mutation_results.json")
TESTS = ["tests/test_fs1c1_requirement_claims.py", "tests/test_prd020_requirement_lineage.py",
         "tests/test_fs1_false_success_reproducer.py", "tests/test_fs1a_test_execution_evidence.py",
         "tests/test_d8_terminal_toolchain_authority.py", "tests/test_fs1c0_named_test_oracle.py",
         "tests/test_model_evidence_hardening_001.py", "tests/test_prd020_mutation_scope.py",
         "tests/test_prd020_milestone_requirements.py"]
RQ = "kriya/workflow/requirements.py"
M = [
    ("c0-closes-entire-compound-requirement", RQ, "            elif BEHAVIOR not in claims:\n", "            elif True:\n"),
    ("textual-test-mention-closes-behavioral-claim", RQ,
     "    return (((BEHAVIOR,) if leftover or not named else ())\n", "    return (((BEHAVIOR,) if not named else ())\n"),
    ("regression-pass-copied-to-behavior-obligation", RQ, "    if all(claims.values()):\n", "    if any(claims.values()):\n"),
    ("model-claim-fills-missing-behavior-evidence-write", RQ,
     "        if outcome is RequirementOutcome.SATISFIED:\n            outcome = RequirementOutcome.UNVERIFIED\n",
     "        if False:\n            outcome = RequirementOutcome.UNVERIFIED\n"),
    ("model-claim-fills-missing-behavior-evidence-read", RQ,
     "        elif outcome is RequirementOutcome.SATISFIED:\n            # FS-1B",
     "        elif False:\n            # FS-1B"),
    ("candidate-or-regression-evidence-closes-behavior", RQ,
     "    if method not in allowed.get(claim, frozenset()):\n", "    if False:\n"),
    ("resume-restores-old-broad-c0-closure", RQ,
     "            and BEHAVIOR in requirement_claims(requirement.text, closure.get(\"tests\") or ())):\n",
     "            and False):\n"),
    ("preservation-vocabulary-ignored", RQ,
     "        if any(ch.isalnum() for ch in token) and token not in references and token not in _PRESERVATION_WORDS\n",
     "        if any(ch.isalnum() for ch in token) and token not in references\n"),
    ("claim-not-bound-to-candidate", RQ,
     "        if record.status is ObligationStatus.SATISFIED and evidence.get(\"evidence_id\") == evidence_id:\n            return evidence\n    return None\n\n\ndef _effective_closure(",
     "        if record.status is ObligationStatus.SATISFIED:\n            return evidence\n    return None\n\n\ndef _effective_closure("),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="fs1c1-mut-")
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
