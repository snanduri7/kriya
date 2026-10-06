"""GR-R0 mutation campaign (P3-D method: one exact replacement per mutant; KILLED iff the test set fails). Each
mutant runs in its own fresh clone of the commit under test (never the working tree - rule 19).

usage: python gr0_mutation_campaign.py <commit>    -> gr0_mutation_results.json (overwritten per run; keep the log)
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
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gr0_mutation_results.json")
TESTS = ["tests/test_gr0_retry_no_information_gain.py", "tests/test_gr0_negative_model_authority.py",
         "tests/test_context_edit_protocol_001.py", "tests/test_lr_r1_p1_fallback_incompatible_reproducer.py",
         "tests/test_prd020_requirement_lineage.py", "tests/test_b2a_acceptance_oracle.py",
         "tests/test_fs1c1_requirement_claims.py"]
RS = "kriya/workflow/retry_strategy.py"
RP = "kriya/workflow/retry_policy.py"
AT = "kriya/workflow/attempt.py"
RQ = "kriya/workflow/requirements.py"
M = [
    # Part 1: RETRY-NO-INFORMATION-GAIN
    ("refusal-does-not-transition-at-once", RS, "        immediate=capability_unchanged,", "        immediate=False,"),
    ("every-failure-transitions-at-once", RS, "        immediate=capability_unchanged,", "        immediate=True,"),
    ("transition-rule-ignores-immediate", RP,
     "    if not immediate and consecutive_no_progress < STRATEGY_TRANSITION_AFTER_NO_PROGRESS:",
     "    if consecutive_no_progress < STRATEGY_TRANSITION_AFTER_NO_PROGRESS:"),
    ("repeat-flag-not-forwarded", RS,
     "        refusal_repeated=capability_unchanged and bool(failure.diagnostics.get(\"refusal_repeated\")),",
     "        refusal_repeated=False,"),
    ("repeat-never-detected", AT, "            repeated = refusal in state.edit_refused_capabilities",
     "            repeated = False"),
    ("first-refusal-treated-as-repeat", AT, "            repeated = refusal in state.edit_refused_capabilities",
     "            repeated = True"),
    ("repeat-does-not-reach-the-limit", RS,
     "        state.consecutive_no_progress_attempts = max(state.consecutive_no_progress_attempts, limit)",
     "        state.consecutive_no_progress_attempts += 0"),
    ("refusal-key-ignores-model-and-operation", AT,
     "            refusal = (path, capability.digest, (model, requested[path]))",
     "            refusal = (path, capability.digest)"),
    # Part 2: NEGATIVE-MODEL-AUTHORITY
    ("model-negative-recorded-violated", RQ,
     "        if outcome in (RequirementOutcome.SATISFIED, RequirementOutcome.VIOLATED):",
     "        if outcome is RequirementOutcome.SATISFIED:"),
    ("legacy-violated-record-is-a-veto", RQ,
     "        if outcome is RequirementOutcome.VIOLATED:\n            # GR-R0",
     "        if False:\n            # GR-R0"),
    ("model-negative-alone-does-not-block", RQ,
     "                    and (unverified_policy == \"block\" or _model_reported_missing(ledger, requirement.id)))):",
     "                    and unverified_policy == \"block\")):"),
    ("every-unverified-treated-as-model-negative", RQ,
     "    return RequirementOutcome.VIOLATED.value in (evidence.get(\"model_outcome\"), evidence.get(\"outcome\"))",
     "    return True"),
    ("deterministic-counter-evidence-ignored", RQ,
     "        if (requirement_counter_evidence(ledger, requirement.id, evidence_id) is not None\n",
     "        if (False\n"),
    ("acceptance-counter-evidence-ignored", RQ,
     "                or _claim_counter_evidence(ledger, requirement.id, evidence_id) is not None):",
     "                or False):"),
    ("model-negative-outranks-trusted-closure", RQ,
     "        elif closure is not None and outcome in (RequirementOutcome.UNVERIFIED, RequirementOutcome.SATISFIED):",
     "        elif closure is not None and outcome is RequirementOutcome.SATISFIED:"),
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
