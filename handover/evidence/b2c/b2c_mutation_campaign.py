"""FS-1C2 B2-c mutation campaign (FS-1C1 method: one exact single
replacement per mutant, KILLED iff the test set fails). Each mutant runs in its
own fresh clone of the commit under test (never the working tree - rule 19).

usage: python b2a_mutation_campaign.py <commit>    -> b2c_mutation_results.json (overwritten per run; the run log is kept)
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
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "b2c_mutation_results.json")
TESTS = ["tests/test_b2c_jvm_acceptance.py", "tests/test_b2cov_claim_strength.py", "tests/test_b2a_acceptance_oracle.py",
         "tests/test_fs1c1_requirement_claims.py"]
JV = "kriya/workflow/acceptance_jvm.py"
AO = "kriya/workflow/acceptance_oracle.py"
RQ = "kriya/workflow/requirements.py"
SURFACE = "        changed = changed_surface(base_digests, candidate_digests) + surface.output_root_writes(candidate_paths)"
M = [
    # owner targets
    ("candidate-pom-config-trusted", JV, "        if changed:\n", "        if False:\n"),
    ("candidate-tests-treated-as-acceptance", JV, SURFACE,
     "        changed = [p for p in changed_surface(base_digests, candidate_digests) if \"/test/\" not in \"/\" + p]"
     " + surface.output_root_writes(candidate_paths)"),
    ("model-claim-closes-behavior", RQ, "        elif outcome is RequirementOutcome.SATISFIED:\n            # FS-1B",
     "        elif False:\n            # FS-1B"),
    ("c0-closes-behavior", RQ, "            elif BEHAVIOR not in claims:", "            elif True:"),
    ("finite-jvm-examples-close-general-claim", AO,
     "            if judgment.passed and strength != BEHAVIOR_EXACT:", "            if False:"),
    ("counterexample-fails-to-violate", JV,
     "        if entry.get(\"status\") == test_execution.FAILED and kind in _ASSERTION_TYPES and references_candidate:",
     "        if False:"),
    ("missing-junit-identity-treated-as-pass", JV,
     "            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_IDENTITY_NOT_EXECUTED,",
     "            judgments[rid] = AcceptanceJudgment(ACCEPTANCE_PASSED,"),
    ("stale-surefire-report-accepted", JV,
     "    if report is None or not report.complete or report.runner != JVM_RUNNER:", "    if report is None:"),
    ("wrong-candidate-digest-accepted", RQ,
     '        if (record.evidence or {}).get("evidence_id") == evidence_id:\n            return record',
     "        if True:\n            return record"),
    ("wrong-acceptance-digest-accepted", JV, "        if _file_digest(target) != artifact.digest:", "        if False:"),
    ("acceptance-collision-silently-overwritten", JV,
     "        if injection in base.paths or os.path.lexists(os.path.join(candidate_root, injection)):",
     "        if False:"),
    ("trust-surface-output-root-ignored", JV, SURFACE,
     "        changed = changed_surface(base_digests, candidate_digests)"),
    ("harness-compile-error-treated-as-violation", JV,
     "            return every(ACCEPTANCE_HARNESS_COMPILE_FAILED,", "            return every(ACCEPTANCE_VIOLATED,"),
    # B2-c's own decision points
    ("candidate-compile-failure-treated-as-violation", JV,
     "            run.refusal = AcceptanceError(ACCEPTANCE_CANDIDATE_COMPILE_FAILED,",
     "            run.refusal = AcceptanceError(\"ACCEPTANCE_VIOLATED\","),
    ("harness-exception-treated-as-contradiction", JV, "            if frames & candidate_classes:", "            if True:"),
    ("assertion-without-candidate-reference-violates", JV,
     "        if entry.get(\"status\") == test_execution.FAILED and kind in _ASSERTION_TYPES and references_candidate:",
     "        if entry.get(\"status\") == test_execution.FAILED and kind in _ASSERTION_TYPES:"),
    ("report-files-not-reverified", JV, "    if details is None:\n", "    if False:\n"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="b2c-mut-")
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
