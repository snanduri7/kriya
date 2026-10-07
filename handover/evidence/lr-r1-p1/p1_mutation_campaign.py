"""LR-R1-P1 mutation campaign at the P1 implementation commit.

Each mutant replaces one exact source text (must occur), runs the P1 test
set, restores the file and checks the tree is clean again. Killed = the
test set exits non-zero. Results -> p1_mutation_results.json.
"""
import json
import os
import subprocess
import sys
import time

REPO = os.path.expanduser("~/kriya-wt/lr-r1-m1")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p1_mutation_results.json")
TESTS = ["tests/test_lr_r1_p1_capability_routing.py", "tests/test_lr_r1_p1_fallback_incompatible_reproducer.py",
         "tests/test_prd017_fallback_transition.py", "tests/test_model_capabilities.py",
         "tests/test_prd014_model_qualification.py", "tests/test_retry_policy.py", "tests/test_best_of_n.py",
         "tests/test_prd031_coordinators.py", "tests/test_context_edit_protocol_001.py",
         "tests/test_lr_r1_m1_recovery.py", "tests/test_lr_r1_m1_t6_paths.py", "tests/state_machine"]

MC = "kriya/core/model_capabilities.py"
MT = "kriya/workflow/model_transition.py"
RC = "kriya/workflow/recovery_coordinator.py"
RS = "kriya/workflow/retry_strategy.py"
A = "kriya/workflow/attempt.py"

M = [
    # --- derivation ---
    ("derive", "qualified-implies-all-capabilities", MC,
     "        return all(statuses.get(case) == mq.PASS for case in CAPABILITY_PROOF_CASES[name])",
     "        return True"),
    ("derive", "unavailable-counts-as-pass", MC,
     "        return all(statuses.get(case) == mq.PASS for case in CAPABILITY_PROOF_CASES[name])",
     "        return all(statuses.get(case) in (mq.PASS, mq.UNAVAILABLE) for case in CAPABILITY_PROOF_CASES[name])"),
    ("derive", "ignore-stale-record", MC,
     "        if not current:\n            return None\n        statuses",
     "        if record is None:\n            return None\n        statuses"),
    ("derive", "ignore-runtime-identity", MC,
     "        record = mq.load_record(fingerprint.digest, settings)\n",
     "        record = mq.load_record(fingerprint.digest, settings) or next((r for r in mq._stored_records() if r.get('alias') == model or r.get('model') == model), None)\n        fingerprint = type('F', (), {'digest': (record or {}).get('fingerprint_digest'), 'exact': True})() if record else fingerprint\n"),
    ("derive", "ignore-inference-identity", MC,
     "        current, _reasons = mq.record_is_current(record, fingerprint, settings, policy_digest=policy_digest)",
     "        record = record or next((r for r in mq._stored_records() if r.get('fingerprint_digest') == fingerprint.digest), None)\n        current = record is not None"),
    ("derive", "patch-implies-native-tools", MC,
     '    tools = proven("native_tool_calls")\n',
     '    tools = proven("native_tool_calls") or proven("patch_edit")\n'),
    ("derive", "inherit-base-tag-capabilities", MC,
     "    known = KNOWN_MODEL_PROFILES.get(_normalize_model_identity(model))\n    if known is not None:\n        return ResolvedCapabilityProfile(model=model, capabilities=known, source=\"known_production_profile\")",
     "    known = KNOWN_MODEL_PROFILES.get(_normalize_model_identity(model).split('-kriya-')[0])\n    if known is not None:\n        return ResolvedCapabilityProfile(model=model, capabilities=known, source=\"known_production_profile\")"),
    ("derive", "skip-edit-consumer-request-profile", MT,
     "    from kriya.core.model_capabilities import resolve_model_capability_profile\n    from kriya.core.model_qualification import assess",
     "    from kriya.core.model_capabilities import declared_capability_profile as resolve_model_capability_profile\n    from kriya.core.model_qualification import assess"),
    ("derive", "explicit-loses-to-derived", MC,
     '    if declared.source.startswith("explicit_"):\n        return declared\n',
     ""),
    ("derive", "fingerprint-reads-derived-profile", "kriya/core/model_runtime.py",
     "    profile = declared_capability_profile(config, model)",
     "    from kriya.core.model_capabilities import resolve_model_capability_profile\n    profile = resolve_model_capability_profile(config, model)"),
    # --- routing ---
    ("route", "configured-equals-compatible", MT,
     "        return any(c.status != CANDIDATE_PATCH_INCOMPATIBLE for c in self.candidates)",
     "        return bool(self.candidates)"),
    ("route", "ordinary-fallback-targeted-uses-bool-chain", RC,
     "        has_fallback_model=routing.available,\n    )\n    if routing.patch_excluded:",
     "        has_fallback_model=bool(ctx.chain),\n    )\n    if routing.patch_excluded:"),
    ("route", "only-force-strategy-transition-fixed", RC,
     "    routing = fallback_routing_for_context(state, ctx)\n    retry_decision",
     "    routing = fallback_routing_for_context(state, SimpleNamespace(chain=[]))\n    from types import SimpleNamespace  # noqa\n    retry_decision"),
    ("route", "transition-reverts-to-bool-chain", RS,
     "has_fallback_model=(routing := fallback_routing_for_context(state, ctx)).available,",
     "has_fallback_model=bool((routing := fallback_routing_for_context(state, ctx)) or True) and bool(ctx.chain),"),
    ("route", "ignore-patch-only-targets", MT,
     "    sizes: Dict[str, int] = {}\n    if not worktree_path:",
     "    sizes: Dict[str, int] = {}\n    if True:"),
    ("route", "remove-call-time-backstop", A,
     "        if reasons:\n            kwargs, profile = _substitute_for_required_patch(state, ctx, kwargs, profile, reasons)",
     "        if False:\n            kwargs, profile = _substitute_for_required_patch(state, ctx, kwargs, profile, reasons)"),
    ("route", "reorder-fallback-chain", MT,
     "    remaining = chain[min(retry_count - 1, len(chain) - 1):]\n",
     "    remaining = list(reversed(chain[min(retry_count - 1, len(chain) - 1):]))\n"),
    ("route", "full-set-escalation-invokes-incompatible", A,
     "        if not patch_reasons:\n            break\n",
     "        break\n"),
    ("route", "first-incompatible-stops-the-scan", MT,
     "            file_allocation_tokens=room))\n    return FallbackCompatibility(PATCH_ONLY_PROVEN",
     "            file_allocation_tokens=room))\n        if status == CANDIDATE_PATCH_INCOMPATIBLE:\n            break\n    return FallbackCompatibility(PATCH_ONLY_PROVEN"),
    ("route", "unknown-protocol-treated-as-incompatible", MT,
     "                        if whole_file_minimum_tokens(size, ceiling) > room)), room",
     "                        if True)), room"),
    ("route", "primary-full-set-path-skipped", RC,
     "    terminal = False\n",
     "    terminal = True\n"),
    ("route", "terminal-while-primary-route-remains", RC,
     "    if not decision.should_continue and decision.action is RetryAction.STOP_EXHAUSTED:",
     "    if True:"),
    ("route", "boundary-equal-is-proof", MT,
     "if whole_file_minimum_tokens(size, ceiling) > room)), room",
     "if whole_file_minimum_tokens(size, ceiling) >= room)), room"),
    ("route", "nominal-window-not-allocation", MT,
     "    room = request_capacity(config, binding).tokens\n",
     "    room = int(binding.context_window or 0)\n"),
    ("route", "run-wide-rejection-becomes-primary-route", A,
     '    stays_on_primary = selected is None and allow_primary and all(r["model"] in patch_rejected for r in rejected)',
     "    stays_on_primary = selected is None and allow_primary"),
    ("route", "full-set-escalation-without-targets", A,
     "                                              targets=ctx.architect_files, allow_primary=True)",
     "                                              targets=(), allow_primary=True)"),
    ("evidence", "q8-drops-routing-fields", "kriya/core/attempt_evidence/explain.py",
     '"newly_rejected", "patch_rejected", "requirement", "candidates",\n                                          "decision_point", "other_route_remained", "resulting_strategy")',
     '"newly_rejected")'),
    ("evidence", "routing-decision-unrecorded", RC,
     '    attempt_evidence_scope.record_fallback_decision({"phase": "routing", **details})\n', ""),
]


def run_tests():
    env = dict(os.environ, PYTHONPATH=REPO)
    proc = subprocess.run(f"ulimit -n 256; {PYTEST} -q -x -p no:cacheprovider -n 8 " + " ".join(TESTS),
                          shell=True, cwd=REPO, env=env, capture_output=True, text=True)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main():
    assert subprocess.run(["git", "diff", "--quiet"], cwd=REPO).returncode == 0, "tree not clean"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    code, tail = run_tests()
    results = {"head": head, "baseline": {"exit": code, "summary": tail}, "tests": TESTS, "mutants": []}
    assert code == 0, f"baseline not green: {tail}"
    for group, label, rel, old, new in M:
        path = os.path.join(REPO, rel)
        with open(path) as handle:
            original = handle.read()
        if original.count(old) != 1:
            results["mutants"].append({"group": group, "label": label, "file": rel,
                                       "status": f"PATTERN_COUNT_{original.count(old)}"})
            print(group, label, results["mutants"][-1]["status"], flush=True)
            continue
        started = time.time()
        try:
            with open(path, "w") as handle:
                handle.write(original.replace(old, new, 1))
            code, tail = run_tests()
        finally:
            with open(path, "w") as handle:
                handle.write(original)
        assert subprocess.run(["git", "diff", "--quiet"], cwd=REPO).returncode == 0, f"not restored: {rel}"
        results["mutants"].append({"group": group, "label": label, "file": rel,
                                   "status": "KILLED" if code != 0 else "SURVIVED", "summary": tail,
                                   "seconds": round(time.time() - started, 1)})
        print(group, label, results["mutants"][-1]["status"], tail, flush=True)
    with open(OUT, "w") as handle:
        json.dump(results, handle, indent=1)
    statuses = [m["status"] for m in results["mutants"]]
    print("TOTAL", len(statuses), {s: statuses.count(s) for s in set(statuses)})


if __name__ == "__main__":
    sys.exit(main())
