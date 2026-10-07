"""B2-COV mutation campaign (FS-1C1 method: one exact single
replacement per mutant, KILLED iff the test set fails). Each mutant runs in its
own fresh clone of the commit under test (never the working tree - rule 19).

usage: python b2a_mutation_campaign.py <commit>    -> b2cov_mutation_results.json (overwritten per run; the run log is kept)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/b2a")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "b2cov_mutation_results.json")
TESTS = ["tests/test_b2cov_claim_strength.py", "tests/test_b2a_acceptance_oracle.py",
         "tests/test_fs1c1_requirement_claims.py", "tests/test_prd020_requirement_lineage.py",
         "tests/test_prd020_milestone_requirements.py", "tests/test_fs1_false_success_reproducer.py"]
AO = "kriya/workflow/acceptance_oracle.py"
RQ = "kriya/workflow/requirements.py"
M = [
    # owner targets
    ("finite-examples-close-universal-claim", AO,
     "            if judgment.passed and strength != BEHAVIOR_EXACT:", "            if False:"),
    ("one-passed-example-closes-entire-behavior", RQ, "    behavior = claims[BEHAVIOR]\n",
     "    behavior = claims[BEHAVIOR] = claims[BEHAVIOR] or requirement_claim(\n"
     "        ledger, requirement.id, BEHAVIOR_EXAMPLES, evidence_id)\n"),
    ("model-claim-upgrades-universal-evidence", RQ,
     "        elif outcome is RequirementOutcome.SATISFIED:\n            # FS-1B",
     "        elif False:\n            # FS-1B"),
    ("c0-evidence-upgrades-universal-behavior", RQ, "            elif BEHAVIOR not in claims:", "            elif True:"),
    ("candidate-test-upgrades-universal-behavior", RQ, "               BEHAVIOR_EXAMPLES: BEHAVIOR_CLOSURE_METHODS}",
     "               BEHAVIOR_EXAMPLES: BEHAVIOR_CLOSURE_METHODS | NAMED_TEST_CLOSURE_METHODS}"),
    ("ambiguous-claim-treated-as-exact", RQ, "    if not examples:\n", "    if False:\n"),
    ("resume-restores-old-broad-b2-claim", RQ,
     "            and not _finite_evidence_may_close(requirement, required)):", "            and False):"),
    ("resume-restores-old-broad-b2-whole-closure", RQ,
     '            and not _finite_evidence_may_close(requirement, tuple(closure.get("required_claims") or ()))):',
     "            and False):"),
    ("failed-counterexample-leaves-universal-claim-closed", AO,
     "                      else ObligationStatus.VIOLATED if judgment.violated else ObligationStatus.INDETERMINATE)",
     "                      else ObligationStatus.INDETERMINATE if judgment.violated else ObligationStatus.INDETERMINATE)"),
    # the classifier's own decision points
    ("universal-words-ignored", RQ, "        if cues:\n", "        if False:\n"),
    ("formula-over-parameter-ignored", RQ, "        if formula:\n", "        if False:\n"),
    ("placeholder-ignored", RQ, "    if _PLACEHOLDER.search(raw):\n", "    if False:\n"),
    ("uncovered-preservation-ignored", RQ, "            if not regression_covered:\n", "            if False:\n"),
    ("regression-coverage-assumed", RQ, "    covered = REGRESSION_PRESERVATION in required\n",
     "    covered = True\n"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="b2cov-mut-")
    subprocess.run(["git", "clone", "-q", "--no-checkout", SRC, root], check=True)
    subprocess.run(["git", "checkout", "-q", commit], cwd=root, check=True)
    return root


def run_tests(repo):
    proc = subprocess.run(f"ulimit -n 256; {PYTEST} -q -x -p no:cacheprovider -n 8 " + " ".join(TESTS), shell=True,
                          cwd=repo, env=dict(os.environ, PYTHONPATH=repo), capture_output=True, text=True)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main(commit):
    results = {"commit": commit, "tests": TESTS, "mutants": []}
    base = clone(commit)
    try:
        code, tail = run_tests(base)
        print("baseline", tail, flush=True)
        if code != 0:
            raise SystemExit(f"baseline not green: {tail}")
        results["baseline"] = tail
    finally:
        shutil.rmtree(base, ignore_errors=True)
    for name, path, old, new in M:
        repo = clone(commit)
        try:
            target = os.path.join(repo, path)
            source = open(target).read()
            if source.count(old) != 1:
                results["mutants"].append({"name": name, "status": "NOT_APPLIED", "count": source.count(old)})
                print(name, "NOT_APPLIED", source.count(old), flush=True)
                continue
            open(target, "w").write(source.replace(old, new))
            started = time.time()
            code, tail = run_tests(repo)
            status = "KILLED" if code != 0 else "SURVIVED"
            results["mutants"].append({"name": name, "file": path, "status": status, "tail": tail,
                                       "seconds": round(time.time() - started, 1)})
            print(name, status, tail, flush=True)
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    counts = {}
    for mutant in results["mutants"]:
        counts[mutant["status"]] = counts.get(mutant["status"], 0) + 1
    results["counts"] = counts
    json.dump(results, open(OUT, "w"), indent=2)
    print("TOTAL", len(M), counts, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
