"""LR-R1-P5 mutation campaign (same method as P1/P4: one exact single
replacement, run the P5 test set, restore, clean-tree check).
Results -> p5_mutation_results.json."""
import json
import os
import subprocess
import sys
import time

REPO = os.path.expanduser("~/kriya-wt/lr-r1-m1")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p5_mutation_results.json")
TESTS = ["tests/test_lr_r1_p5_integration_obligation_reproducer.py", "tests/test_lr_r1_p5_integration_evidence.py",
         "tests/test_workflow_controller_enforce.py", "tests/test_lr_r1_m1_t6_fixes.py"]
WC = "kriya/workflow/workflow_controller.py"
EX = "kriya/core/attempt_evidence/explain.py"
M = [
    ("consumer-writes-only", WC,
     "        current = provenance.current_content(path)\n", "        current = None\n"),
    ("all-subtask-writes-blindly-unioned", WC,
     '        consumer_content = "\\n".join(contents)\n',
     '        consumer_content = "\\n".join(list(contents) + list(established_file_context.values()))\n'),
    ("provider-identity-ignored / unrelated-sibling-accepted", WC,
     "        if writer not in set(producers):\n            return \"established_by_non_provider\", writer\n", ""),
    ("wrong-provider-accepted (any subtask's planned files count)", WC,
     "            for pid in rel.producer_subtask_ids\n            for pf in (plan.subtask_by_id(pid).planned_files",
     "            for pid in [st.id for st in plan.subtasks if st.id not in rel.consumer_subtask_ids]\n            for pf in (plan.subtask_by_id(pid).planned_files"),
    ("provider-local-pass-implies-global-pass", WC,
     "            if not re.search(rf\"\\b{re.escape(token)}\\b\", consumer_content):\n                missing.append(path)\n    else:",
     "            pass\n    else:"),
    ("final-workspace-state-ignored", WC,
     "        if current is None or current != self.digest.get(path):\n            return \"invalidated\", writer\n", ""),
    ("no-change-consumer-treated-as-missing", WC,
     "        if current is not None:\n            contents.append(current)\n", ""),
    ("verification-only-consumer-treated-as-missing", WC,
     '        evidence["evaluation"] = "verification_only_consumer"\n',
     '        evidence["evaluation"] = "verification_only_consumer"\n        missing = sorted(set(missing) | set(producer_paths))\n'),
    ("verification-only-without-verification-passes", WC,
     "        if not verification:\n", "        if False:\n"),
    ("missing-artifact-converted-to-success", WC,
     "        satisfied = not missing_producers\n", "        satisfied = True\n"),
    ("production-call-passes-no-provenance", WC,
     "                plan, obligation_ledger, subtask_id, established_file_context, position,\n                provenance=established_provenance,\n",
     "                plan, obligation_ledger, subtask_id, established_file_context, position,\n"),
    ("resume-provenance-unrecorded", WC,
     "                    established_provenance.record(planned_file.path, subtask_id)\n", ""),
    ("decision-not-mirrored-to-m1", WC,
     "            attempt_evidence_scope.mirror_event(RunEvent(", "            (lambda *a, **k: None)(RunEvent("),
    ("q9-drops-integration-decisions", EX,
     "        integration_obligations=_integration_obligations(records, run),\n", ""),
]


def run_tests():
    proc = subprocess.run(f"ulimit -n 256; {PYTEST} -q -x -p no:cacheprovider -n 8 " + " ".join(TESTS), shell=True,
                          cwd=REPO, env=dict(os.environ, PYTHONPATH=REPO), capture_output=True, text=True)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main():
    assert subprocess.run(["git", "diff", "--quiet"], cwd=REPO).returncode == 0, "tree not clean"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    code, tail = run_tests()
    assert code == 0, tail
    results = {"head": head, "baseline": tail, "tests": TESTS, "mutants": []}
    for label, rel, old, new in M:
        path = os.path.join(REPO, rel)
        original = open(path).read()
        if original.count(old) != 1:
            results["mutants"].append({"label": label, "status": f"PATTERN_COUNT_{original.count(old)}"})
            print(label, results["mutants"][-1]["status"], flush=True)
            continue
        started = time.time()
        try:
            open(path, "w").write(original.replace(old, new, 1))
            code, tail = run_tests()
        finally:
            open(path, "w").write(original)
        assert subprocess.run(["git", "diff", "--quiet"], cwd=REPO).returncode == 0, f"not restored: {rel}"
        results["mutants"].append({"label": label, "file": rel, "status": "KILLED" if code else "SURVIVED",
                                   "summary": tail, "seconds": round(time.time() - started, 1)})
        print(label, results["mutants"][-1]["status"], tail, flush=True)
    json.dump(results, open(OUT, "w"), indent=1)
    statuses = [m["status"] for m in results["mutants"]]
    print("TOTAL", len(statuses), {s: statuses.count(s) for s in set(statuses)})


if __name__ == "__main__":
    sys.exit(main())
