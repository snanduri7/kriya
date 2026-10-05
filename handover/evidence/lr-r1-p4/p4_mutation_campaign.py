"""LR-R1-P4 mutation campaign (same method as the P1 campaign: one exact
single replacement, run the P4 test set, restore, clean-tree check).
Results -> p4_mutation_results.json."""
import json
import os
import subprocess
import sys
import time

REPO = os.path.expanduser("~/kriya-wt/lr-r1-m1")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p4_mutation_results.json")
TESTS = ["tests/test_lr_r1_p4_verification_only_retry_reproducer.py",
         "tests/test_lr_r1_p4_verification_retry_admission.py", "tests/test_prd026_retry_progress.py",
         "tests/test_retry_policy.py", "tests/test_prd031_coordinators.py", "tests/test_workflow_controller_enforce.py",
         "tests/test_lr_r1_m1_recovery.py", "tests/state_machine"]
RS = "kriya/workflow/retry_strategy.py"
VC = "kriya/workflow/verification_coordinator.py"
M = [
    ("retry-whenever-budget-remains", RS,
     "    retryable = not next_attempt_verification_only or workspace_changed\n", "    retryable = True\n"),
    ("verification-only-flag-ignored", RS,
     "    next_attempt_verification_only = is_verification_only_unit(ctx.write_scope_mode, ctx.required_verification)\n",
     "    next_attempt_verification_only = False\n"),
    ("verification-only-predicate-ignores-executable-verifiers", VC,
     "    return write_scope_mode == WriteScopeMode.DENY_ALL and bool(",
     "    return write_scope_mode == WriteScopeMode.DENY_ALL or bool("),
    ("workspace-change-check-ignored", RS,
     "    workspace_changed = now != state.verification_only_inputs\n", "    workspace_changed = False\n"),
    ("new-recovery-input-check-ignored", RS,
     "    if state.environment_failure or state.plan_scope_conflict is not None or state.no_progress_terminated:",
     "    if state.environment_failure or state.no_progress_terminated:"),
    ("mutable-recovery-suppressed", RS,
     "    retryable = not next_attempt_verification_only or workspace_changed\n", "    retryable = workspace_changed\n"),
    ("transient-infrastructure-stop-overridden", RS,
     "    if state.environment_failure or state.plan_scope_conflict is not None or state.no_progress_terminated:",
     "    if state.plan_scope_conflict is not None or state.no_progress_terminated:"),
    ("attempt-number-is-new-information", RS,
     '    canonical = json.dumps({"workspace": workspace, "required_verification": ctx.required_verification},',
     '    canonical = json.dumps({"workspace": workspace, "required_verification": ctx.required_verification, "attempt": state.attempt_number},'),
    ("resume-is-a-state-change", RS,
     '    canonical = json.dumps({"workspace": workspace, "required_verification": ctx.required_verification},',
     '    canonical = json.dumps({"workspace": workspace, "required_verification": ctx.required_verification, "process": id(state)},'),
    ("typed-failure-converted-to-success", RS,
     "    state.no_progress_terminated = True\n    state.no_progress_reason = VERIFICATION_RETRY_NO_CHANGE_POSSIBLE\n",
     "    state.no_progress_terminated = True\n    state.no_progress_reason = VERIFICATION_RETRY_NO_CHANGE_POSSIBLE\n"
     "    state.candidate_gates_succeeded = state.overall_attempt_succeeded = state.terminal_regression_succeeded = True\n"
     "    state.last_failure = None\n"),
    ("stop-without-typed-reason", RS,
     "    state.no_progress_reason = VERIFICATION_RETRY_NO_CHANGE_POSSIBLE\n", ""),
    ("non-verification-attempt-guard-removed", RS,
     "    if state.verification_only_inputs is None or state.verification_only_inputs_attempt != state.attempt_number:\n",
     "    if state.verification_only_inputs is None:\n"),
    ("admission-never-called", RS, "    _admit_verification_only_retry(state, ctx, failure)\n", ""),
    ("admission-evidence-unrecorded", RS, '        kind="retry.verification_admission",', '        kind="retry.unrecorded",'),
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
