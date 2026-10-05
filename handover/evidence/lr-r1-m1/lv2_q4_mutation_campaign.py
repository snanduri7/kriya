"""LR-R1-M1 LV-2: Q4 no-model-call mutants over every M1 test file.
Same method as m1_mutation_campaign_v2.py (exact single replacement, restore,
clean-tree check). Results -> lv2_q4_mutation_results.json."""
import glob
import json
import os
import subprocess
import sys

REPO = os.path.expanduser("~/kriya-wt/lr-r1-m1")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lv2_q4_mutation_results.json")
E = "kriya/core/attempt_evidence/explain.py"
M = [
    ("branch-removed", "    elif not developer_requests and not parses and _no_candidate_possible(records):",
     "    elif False:"),
    ("any-call-counts-as-developer", "    elif not developer_requests and not parses and _no_candidate_possible(records):",
     "    elif not _of(records, \"model.request\") and not parses and _no_candidate_possible(records):"),
    ("developer-call-ignored", "    elif not developer_requests and not parses and _no_candidate_possible(records):",
     "    elif not parses and _no_candidate_possible(records):"),
    ("gate-consumed-candidate-ignored", "    return not _of(records, \"gate.result\", stage=\"validator\")",
     "    return True"),
    ("verification-only-not-recognised",
     "    if opened and (opened[0].get(\"payload\") or {}).get(\"mode\") == \"verification_only\":",
     "    if False:"),
    ("typed-as-not-recorded", "answers[\"Q4\"] = _absent(NOT_APPLICABLE, \"no_model_call: no Developer request in this attempt\")",
     "answers[\"Q4\"] = _absent(NOT_RECORDED, \"no_model_call: no Developer request in this attempt\")"),
]


def run_tests():
    files = sorted(glob.glob(os.path.join(REPO, "tests/test_lr_r1_m1_*.py")))
    proc = subprocess.run(f"ulimit -n 256; {PYTEST} -q -p no:cacheprovider -n 8 " + " ".join(files), shell=True,
                          cwd=REPO, env=dict(os.environ, PYTHONPATH=REPO), capture_output=True, text=True)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main():
    assert subprocess.run(["git", "diff", "--quiet"], cwd=REPO).returncode == 0, "tree not clean"
    code, tail = run_tests()
    assert code == 0, tail
    results = {"baseline": tail, "mutants": []}
    path = os.path.join(REPO, E)
    for label, old, new in M:
        original = open(path).read()
        assert original.count(old) == 1, label
        try:
            open(path, "w").write(original.replace(old, new, 1))
            code, tail = run_tests()
        finally:
            open(path, "w").write(original)
        assert subprocess.run(["git", "diff", "--quiet"], cwd=REPO).returncode == 0
        results["mutants"].append({"label": label, "status": "KILLED" if code else "SURVIVED", "summary": tail})
        print(label, results["mutants"][-1]["status"], tail, flush=True)
    json.dump(results, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
