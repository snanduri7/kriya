"""FS-1C0 mutation campaign (P1/P4/P5 method: one exact single
replacement per mutant, the FS-1 test set, KILLED iff it fails). Each mutant
runs in its own fresh clone of the commit under test (never the working
tree, which a full-suite run may be using - rule 19).

usage: python c0_mutation_campaign.py <commit>    -> c0_mutation_results.json
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
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "c0_mutation_results.json")
TESTS = ["tests/test_fs1c0_named_test_oracle.py", "tests/test_fs1c_named_test_oracle_dependency.py",
         "tests/test_prd020_requirement_lineage.py", "tests/test_d8_terminal_toolchain_authority.py",
         "tests/test_fs1a_test_execution_evidence.py", "tests/test_fs1_false_success_reproducer.py"]
NO = "kriya/workflow/named_test_oracle.py"
WF = "kriya/workflow/workflow.py"
RQ = "kriya/workflow/requirements.py"
M = [
    # ---- required (owner list)
    ("ignore-conftest-and-package-files", NO,
     "            for name in _PYTEST_DIR_FILES:\n", "            for name in ():\n"),
    ("ignore-support-module-closure", NO,
     "                        if _is_test_side(target) and target not in seen:\n",
     "                        if False:\n"),
    ("ignore-runner-ini-files", NO,
     '_PYTEST_DIR_FILES = ("conftest.py", "__init__.py", "pytest.ini", ".pytest.ini")',
     '_PYTEST_DIR_FILES = ("conftest.py", "__init__.py")'),
    ("ignore-pyproject-pytest-config", NO,
     '            entries[_join(directory, "pyproject.toml")] = _pyproject(root=directory == "")\n', ""),
    ("ignore-setup-cfg-pytest-config", NO,
     '            entries[_join(directory, "setup.cfg")] = _ini_section("tool:pytest")\n', ""),
    ("ignore-declared-dependencies", NO,
     '        pending, visited = ["requirements.txt"], set()\n', "        pending, visited = [], set()\n"),
    ("candidate-report-trusted-blindly", NO,
     "    if report is None or not report.complete or report.runner != runner:\n        reason =",
     "    if report is None:\n        reason ="),
    ("candidate-exit-code-trusted-blindly", NO,
     "    report = test_execution.report_from_result(result)\n    if report is None or not report.complete or report.runner != runner:\n        reason =",
     "    if isinstance(result, dict) and result.get(\"success\"):\n        return ORACLE_PASSED, \"\"\n"
     "    report = test_execution.report_from_result(result)\n    if report is None or not report.complete or report.runner != runner:\n        reason ="),
    ("runner-failure-ignored", NO,
     "    if not (isinstance(result, dict) and result.get(\"success\")):\n", "    if False:\n"),
    ("missing-baseline-identity-accepted", NO, "    if missing:\n", "    if False:\n"),
    ("skipped-or-deselected-baseline-identity-accepted", NO,
     "any(status != test_execution.PASSED for status in statuses[key])",
     "any(status not in (test_execution.PASSED, test_execution.SKIPPED) for status in statuses[key])"),
    ("stale-inventory-from-candidate-tree", NO,
     "            base_check = base_validator(export)\n", "            base_check = candidate_validator()\n"),
    ("stale-inventory-head-not-run-base", WF,
     "        return base or git_read_lines(candidate_root, \"rev-parse\", \"HEAD\")[0]\n",
     "        return git_read_lines(candidate_root, \"rev-parse\", \"HEAD\")[0]\n"),
    ("incomplete-baseline-inventory-accepted", NO,
     "    if report is None or not report.complete or report.runner != runner or not report.cases:\n",
     "    if report is None or not report.cases:\n"),
    ("digest-mismatch-ignored", NO,
     "    changed = changed_surface(base_digests, candidate_digests)\n", "    changed = []\n"),
    ("change-during-run-ignored", NO, "    if after != candidate_digests:\n", "    if False:\n"),
    # ---- additional decision points
    ("parameter-cases-collapsed", NO,
     '    return f"{case.classname}::{case.name}"\n', '    return f"{case.classname}::{case.name.split(\'[\')[0]}"\n'),
    ("output-root-writes-ignored", NO,
     "    changed += surface.output_root_writes(modified)\n", "    changed += []\n"),
    ("unsupported-runner-accepted", NO, "    if runner not in SUPPORTED_RUNNERS:\n", "    if False:\n"),
    ("jvm-test-classpath-ignored", NO, "                    or (jvm and _non_main_source_set(parts))):\n",
     "                    or False):\n"),
    ("jvm-source-set-rule-applied-to-python", NO,
     '        jvm = any(path.rsplit("/", 1)[-1] in _JVM_MODULE_MARKERS for path in universe)\n',
     "        jvm = True\n"),
    ("test-resources-ignored", NO,
     '                if path.startswith(prefix) and not path.endswith((".py", ".pyc")):\n',
     "                if False:\n"),
    ("closure-binding-dropped", RQ, "                    detail=dict(judgment.evidence), source=source,",
     "                    detail={}, source=source,"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="c0-mut-")
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
