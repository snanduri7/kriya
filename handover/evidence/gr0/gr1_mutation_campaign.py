"""GR-R1 (A: explicit requirement contract, B: attempt spec-gate model authority) mutation campaign (P3-D method: one exact replacement per mutant; KILLED iff the test set fails). Each
mutant runs in its own fresh clone of the commit under test (never the working tree - rule 19).

usage: python gr1_mutation_campaign.py <commit>    -> gr1_mutation_results.json (overwritten per run; keep the log)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/p3d")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gr1_mutation_results.json")
TESTS = ["tests/test_gr1a_requirement_contract.py", "tests/test_gr1b_spec_gate_model_authority.py",
         "tests/test_gr0_negative_model_authority.py", "tests/test_prd020_requirement_lineage.py",
         "tests/test_b3_human_acceptance.py", "tests/test_b2a_acceptance_oracle.py",
         "tests/test_prd008_resume_fingerprints.py", "tests/test_workflow.py"]
RC = "kriya/workflow/requirement_contract.py"
RQ = "kriya/workflow/requirements.py"
RF = "kriya/workflow/resume_fingerprints.py"
WC = "kriya/workflow/workflow_controller.py"
AP = "kriya/workflow/acceptance_approval.py"
AO = "kriya/workflow/acceptance_oracle.py"
AT = "kriya/workflow/attempt.py"
M = [
    # GR-R1A
    ("raw-narrative-used-despite-contract", RC, "    if contract is None:\n        return derive_requirements(goal)",
     "    if True:\n        return derive_requirements(goal)"),
    ("contract-inside-workspace-accepted", RC,
     "    if path_relation(os.path.realpath(workspace), real) is not PathRelation.OUTSIDE:", "    if False:"),
    ("goal-digest-ignored", RC, "    if contract.requirement_set.goal_digest != goal_identity(goal):", "    if False:"),
    ("requirement-set-digest-ignores-contract", RQ,
     '             **({"contract_digest": self.contract_digest} if self.contract_digest is not None else {})},\n'
     "            sort_keys=True,", "},\n            sort_keys=True,"),
    ("resume-fingerprint-ignores-contract", RF,
     '            **({"requirement_contract_digest": requirement_contract_digest}\n'
     "               if requirement_contract_digest is not None else {}),", ""),
    ("enforce-resume-accepts-changed-contract", WC,
     "            if prior_control_state.requirement_contract_digest != current_contract_digest:", "            if False:"),
    ("b3-ignores-requirement-set-identity", AP,
     "        (entry.requirement_set_sha256 == requirements.digest if entry.requirement_set_sha256 is not None\n"
     '         else getattr(requirements, "contract_digest", None) is None,',
     "        (True,"),
    ("b3-v1-accepted-for-contract", AP,
     '    if getattr(requirements, "contract_digest", None) is not None and fields is not _FIELDS_V2:',
     "    if False:"),
    ("unknown-acceptance-requirement-accepted", AO, "    if unknown:\n        raise AcceptanceError(ACCEPTANCE_UNKNOWN_REQUIREMENT,",
     "    if False:\n        raise AcceptanceError(ACCEPTANCE_UNKNOWN_REQUIREMENT,"),
    ("duplicate-requirement-id-accepted", RC, "        if rid in seen:", "        if False:"),
    ("unknown-kind-accepted", RC, "        if kind not in REQUIREMENT_KINDS:", "        if False:"),
    ("empty-set-accepted", RC, '    if not document["requirements"]:', "    if False:"),
    ("model-generated-requirement-accepted", RQ, "        if rid not in known:", "        if False:"),
    # GR-R1B
    ("model-missing-fails-the-attempt", AT, "            model_missing_requirements = list(kept_requirements)\n",
     "            raise QualityGateFailure(Failure(type=\"goal_spec_compliance\", message=\"m\", raw_output=\"m\"))\n"),
    ("model-indeterminate-fails-the-attempt", AT,
     '                    "status": SPEC_MODEL_INDETERMINATE, "reason_code": SPEC_COMPLIANCE_MODEL_ADVISORY,\n'
     "                }",
     '                    "status": SPEC_MODEL_INDETERMINATE, "reason_code": SPEC_COMPLIANCE_MODEL_ADVISORY,\n'
     "                }\n                raise QualityGateFailure(Failure(type=\"spec_compliance_indeterminate\", "
     "message=\"m\", raw_output=\"m\"))"),
    ("model-advisory-not-recorded", AT, "        if model_advisory:\n            logger.warning(",
     "        if False:\n            logger.warning("),
    ("model-advisory-settles-the-obligation", AT,
     "                status=ObligationStatus.INDETERMINATE if model_advisory else ObligationStatus.SATISFIED,",
     "                status=ObligationStatus.SATISFIED,"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="gr0-mut-")
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
