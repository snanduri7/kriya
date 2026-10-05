"""FS-1A/FS-1B mutation campaign (P1/P4/P5 method: one exact single
replacement per mutant, the FS-1 test set, KILLED iff it fails). Each mutant
runs in its own fresh clone of the commit under test (never the working
tree, which a full-suite run may be using - rule 19).

usage: python fs1_mutation_campaign.py <commit>    -> fs1_mutation_results.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/lr-r1-m1")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fs1_mutation_results.json")
TESTS = ["tests/test_fs1_false_success_reproducer.py", "tests/test_fs1a_test_execution_evidence.py",
         "tests/test_prd020_requirement_lineage.py", "tests/test_prd020_mutation_scope.py",
         "tests/test_prd020_milestone_requirements.py", "tests/test_d8_terminal_toolchain_authority.py",
         "tests/test_model_evidence_hardening_001.py", "tests/test_lr_r1_m1_t6_paths.py"]
RQ = "kriya/workflow/requirements.py"
TD = "kriya/workflow/test_delta.py"
TE = "kriya/tools/test_execution.py"
AT = "kriya/workflow/attempt.py"
PIP = "kriya/capabilities/pip.py"
M = [
    # ---- required (owner list)
    ("model-SATISFIED-writes-SATISFIED", RQ,
     "        if outcome is RequirementOutcome.SATISFIED:\n            outcome = RequirementOutcome.UNVERIFIED\n",
     "        if False:\n            outcome = RequirementOutcome.UNVERIFIED\n"),
    ("UNVERIFIED-does-not-block", RQ,
     "                or (outcome is RequirementOutcome.UNVERIFIED and unverified_policy == \"block\")):",
     "                or False):"),
    ("candidate-tests-alone-close-original-requirement", RQ,
     "        touched = sorted(set(named) & changed)\n", "        touched = []\n"),
    ("missing-report-treated-as-PASS", TD,
     "    if report is None or not report.complete:\n",
     "    if report is None or not report.complete:\n        return TestDeltaVerdict(TEST_DELTA_EXECUTED)\n"),
    ("stale-report-accepted", TE,
     "                    os.remove(stale)\n", "                    pass\n"),
    ("JUnit-missing-test-ignored", TD,
     "        elif reported or item.declared_test:\n", "        elif reported:\n"),
    ("pytest-missing-test-ignored", TD,
     "    elif not verdict.executed:\n", "    elif False:\n"),
    ("test-count-increase-treated-as-sufficient", TD,
     "    elif not verdict.executed:\n", "    elif not verdict.executed and not report.cases:\n"),
    ("helper-method-treated-as-required-executable-test", TD,
     "        elif reported or item.declared_test:\n", "        elif True:\n"),
    ("model-claim-outranks-deterministic-failure", RQ,
     "        if requirement_counter_evidence(ledger, requirement.id, evidence_id) is not None:\n",
     "        if outcome is not RequirementOutcome.SATISFIED and requirement_counter_evidence(\n"
     "                ledger, requirement.id, evidence_id) is not None:\n"),
    # ---- additional decision points
    ("legacy-SATISFIED-record-not-demoted", RQ,
     "        elif outcome is RequirementOutcome.SATISFIED:\n            # FS-1B",
     "        elif False:\n            # FS-1B"),
    ("rule-E-never-called", AT,
     "            _raise_unexecuted_test_delta(state, ctx, validator, accepted_test_result, target_test)\n",
     "            pass\n"),
    ("indeterminate-evidence-not-a-stop", AT,
     "    if verdict.satisfied:\n        return\n", "    if verdict.satisfied or verdict.reason_code == TEST_EXECUTION_EVIDENCE_INDETERMINATE:\n        return\n"),
    ("skipped-counts-as-passed", TE,
     "        return bool(statuses) and all(status == PASSED for status in statuses)\n",
     "        return bool(statuses) and all(status in (PASSED, SKIPPED) for status in statuses)\n"),
    ("incomplete-pytest-session-accepted", TE,
     "        if binding.exit_code not in _PYTEST_COMPLETE_EXIT_CODES and report.reason is None:\n",
     "        if False:\n"),
    ("report-summary-line-not-silenced", PIP,
     "            + (_SILENT_REPORT_SUMMARY if report_argument else \"\")\n", "            + \"\"\n"),
    ("covering-run-skipped", AT,
     "    if not covered or test_execution.report_from_result(result) is None:\n", "    if False:\n"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="fs1-mut-")
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
