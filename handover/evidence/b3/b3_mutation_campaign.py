"""FS-1C2 B3 mutation campaign (FS-1C1 method: one exact single
replacement per mutant, KILLED iff the test set fails). Each mutant runs in its
own fresh clone of the commit under test (never the working tree - rule 19).

usage: python b2a_mutation_campaign.py <commit>    -> b3_mutation_results.json (overwritten per run; the run log is kept)
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
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "b3_mutation_results.json")
TESTS = ["tests/test_b3_human_acceptance.py", "tests/test_b2cov_claim_strength.py", "tests/test_b2a_acceptance_oracle.py",
         "tests/test_fs1c1_requirement_claims.py", "tests/test_prd020_requirement_lineage.py",
         "tests/test_prd008_resume_fingerprints.py"]
AO = "kriya/workflow/acceptance_oracle.py"
AP = "kriya/workflow/acceptance_approval.py"
RQ = "kriya/workflow/requirements.py"
RF = "kriya/workflow/resume_fingerprints.py"
WC = "kriya/workflow/workflow_controller.py"
M = [
    # owner targets
    ("general-b2-pass-closes-without-approval", AO,
     "                problem = approval_problem(approval, requirement, requirements, acceptance, base_revision)",
     "                problem = None"),
    ("wrong-acceptance-digest-in-approval-accepted", AP,
     "        (entry.acceptance_sha256 == acceptance.digest, \"the acceptance artifact differs\"),",
     "        (True, \"the acceptance artifact differs\"),"),
    ("approval-for-req-a-closes-req-b", AP, "    entry = approval.entries.get(requirement.id)",
     "    entry = next(iter(approval.entries.values()))"),
    ("approval-survives-changed-goal", AP, "        (entry.goal_sha256 == requirements.goal_digest, \"the goal differs\"),",
     "        (True, \"the goal differs\"),"),
    ("approval-survives-changed-requirement-text", AP,
     "        (entry.requirement_text_sha256 == text_sha256(requirement.text), \"the requirement text differs\"),",
     "        (True, \"the requirement text differs\"),"),
    ("candidate-can-create-approval", AP,
     "    if path_relation(os.path.realpath(workspace), real) is not PathRelation.OUTSIDE:", "    if False:"),
    ("model-can-create-approval", RQ,
     "        if outcome is RequirementOutcome.SATISFIED:\n            outcome = RequirementOutcome.UNVERIFIED\n",
     "        if False:\n            outcome = RequirementOutcome.UNVERIFIED\n"),
    ("candidate-test-becomes-approved-oracle", AP,
     "        (list(entry.expected_cases) == sorted(acceptance.identities_for(entry.requirement_id)),",
     "        (True,"),
    ("human-approval-overrides-failing-acceptance", AO,
     "            if judgment.passed and strength != BEHAVIOR_EXACT:",
     "            if (judgment.passed or judgment.violated) and strength != BEHAVIOR_EXACT:"),
    ("missing-identity-still-human-accepted", AO,
     "            if judgment.passed and strength != BEHAVIOR_EXACT:",
     "            if (judgment.passed or judgment.code == ACCEPTANCE_IDENTITY_NOT_EXECUTED) and strength != BEHAVIOR_EXACT:"),
    ("stale-report-accepted", AO,
     "    if report is None or not report.complete or report.runner != ACCEPTANCE_RUNNER:", "    if report is None:"),
    ("resume-restores-stale-human-closure", RQ,
     "            and not _human_acceptance_binds(requirement, behavior)):", "            and False):"),
    ("resume-restores-whole-human-closure", RQ,
     '    if closure is not None and closure.get("method") == HUMAN_ACCEPTANCE_METHOD:', "    if False:"),
    ("approval-not-in-resume-identity", RF,
     '            **({"acceptance_approval_digest": approval_digest} if approval_digest is not None else {}),', ""),
    ("enforce-resume-ignores-approval-change", WC,
     "            if prior_control_state.acceptance_approval_digest != current_approval_digest:", "            if False:"),
    ("wildcard-extra-field-accepted", AP, "        if not isinstance(raw, dict) or set(raw) != set(_FIELDS):",
     "        if not isinstance(raw, dict) or not set(_FIELDS) <= set(raw):"),
    ("duplicate-approval-accepted", AP, "        if rid in entries:", "        if False:"),
    # B3's own decision points
    ("unapproved-template-accepted", AP, '        if raw["accept_suite_as_sufficient"] is not True:', "        if False:"),
    ("base-revision-not-bound", AP,
     "        (bool(base_revision) and entry.base_revision == base_revision, \"the base revision differs\"),",
     "        (True, \"the base revision differs\"),"),
    ("runner-contract-not-bound", AP,
     "        (entry.runner_contract_sha256 == runner, \"the acceptance runner contract differs\"),",
     "        (True, \"the acceptance runner contract differs\"),"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="b3-mut-")
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
