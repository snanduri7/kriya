"""FS-1C2 B2-a mutation campaign (FS-1C1 method: one exact single
replacement per mutant, KILLED iff the test set fails). Each mutant runs in its
own fresh clone of the commit under test (never the working tree - rule 19).

usage: python b2a_mutation_campaign.py <commit>    -> b2a_mutation_results.json (overwritten per run; the run log is kept)
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
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "b2a_mutation_results.json")
TESTS = ["tests/test_b2a_acceptance_oracle.py", "tests/test_fs1c1_requirement_claims.py",
         "tests/test_prd020_requirement_lineage.py", "tests/test_fs1_false_success_reproducer.py",
         "tests/test_prd020_milestone_requirements.py"]
AO = "kriya/workflow/acceptance_oracle.py"
RQ = "kriya/workflow/requirements.py"
WF = "kriya/workflow/workflow.py"
VA = "kriya/tools/validate.py"
M = [
    # owner targets
    ("candidate-config-used-during-acceptance", AO,
     'arguments = ["-c", os.path.join(stage_rel, _INI_FILE), "--rootdir", stage_rel, "--noconftest",',
     'arguments = ["--rootdir", stage_rel, "--noconftest",'),
    ("candidate-conftest-trusted", AO, '"--rootdir", stage_rel, "--noconftest",', '"--rootdir", stage_rel,'),
    ("acceptance-file-writable-by-candidate", AO, 'STAGING_DIR = os.path.join(".kriya", "acceptance-runs")',
     'STAGING_DIR = "acceptance-runs"'),
    ("staged-artifact-change-ignored", AO,
     "            if _file_digest(os.path.join(stage, name)) != hashlib.sha256(data).hexdigest():",
     "            if False:"),
    ("model-claim-closes-behavior", RQ,
     "        elif outcome is RequirementOutcome.SATISFIED:\n            # FS-1B",
     "        elif False:\n            # FS-1B"),
    ("candidate-tests-close-behavior", RQ,
     'BEHAVIOR_CLOSURE_METHODS = frozenset({"acceptance_oracle", "human_bound_tests"})',
     'BEHAVIOR_CLOSURE_METHODS = frozenset({"acceptance_oracle", "human_bound_tests", "named_test_oracle"})'),
    ("failed-acceptance-treated-as-pass", AO,
     '            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_VIOLATED, "acceptance case(s) observed the behaviour "',
     '            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_PASSED, "acceptance case(s) observed the behaviour "'),
    ("failed-acceptance-left-closed", RQ,
     '        if (record.evidence or {}).get("evidence_id") == evidence_id:\n            return record',
     '        if (record.evidence or {}).get("evidence_id") == evidence_id and record.status is ObligationStatus.SATISFIED:\n            return record'),
    ("missing-execution-treated-as-pass", AO,
     "            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_IDENTITY_NOT_EXECUTED,",
     "            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_PASSED,"),
    ("stale-or-incomplete-report-accepted", AO,
     "    if report is None or not report.complete or report.runner != ACCEPTANCE_RUNNER:",
     "    if report is None:"),
    ("missing-observations-accepted", AO, "    if observed is None:\n", "    if False:\n"),
    ("wrong-candidate-digest-accepted", RQ,
     '        if (record.evidence or {}).get("evidence_id") == evidence_id:\n            return record',
     '        if True:\n            return record'),
    ("candidate-changed-during-run-accepted", AO,
     "        if run.integrity_problem is None and _tree_digest(candidate_root, tracked) != run.candidate_digest:",
     "        if False:"),
    ("wrong-acceptance-digest-accepted", AO,
     'and (prior.evidence or {}).get("method") == ACCEPTANCE_METHOD and prior_digest != current_digest):',
     'and (prior.evidence or {}).get("method") == ACCEPTANCE_METHOD and False):'),
    ("other-requirement-set-accepted", AO,
     "        if acceptance.requirement_set_digest != requirements.digest:", "        if False:"),
    ("unknown-requirement-accepted", AO, "    if unknown:\n", "    if False:\n"),
    ("unsupported-layout-falls-back-to-normal-tests", AO,
     "    except AcceptanceError as refusal:\n        run.refusal = refusal\n        return run",
     "    except AcceptanceError:\n        run.result = validator_factory().run_tests() or {}\n"
     "        run.report = test_execution.report_from_result(run.result)\n        return run"),
    # B2-a's own decision points
    ("plugin-autoload-left-on", AO, 'os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"\n', ""),
    ("foreign-plugin-ignored", AO, "    if foreign:\n", "    if False:\n"),
    ("bytecode-written-into-candidate", VA, 'cmd = [interpreter, "-I", "-B", runner_script]',
     'cmd = [interpreter, "-I", runner_script]'),
    ("assertion-violates-without-candidate-code", AO,
     "    return exercised and exception in _ASSERTION_TYPES", "    return exception in _ASSERTION_TYPES"),
    ("environment-error-in-candidate-violates", AO,
     "    if not exception or exception in _ENVIRONMENT_TYPES:", "    if not exception:"),
    ("claims-typed-on-candidate-files-only", WF,
     "        test_files=None if reference is None else sorted(set(test_files) | set(reference)),",
     "        test_files=test_files,"),
    ("required-claims-ignored", RQ,
     "    required = tuple((behavior or {}).get(\"required_claims\") or (BEHAVIOR, REGRESSION_PRESERVATION))",
     "    required = (BEHAVIOR,)"),
    ("raising-run-leaves-earlier-pass", AO,
     "            for requirement, evidence_id, _, entry in pending:\n                record(requirement, evidence_id, ObligationStatus.INDETERMINATE,",
     "            for requirement, evidence_id, _, entry in []:\n                record(requirement, evidence_id, ObligationStatus.INDETERMINATE,"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="b2a-mut-")
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
