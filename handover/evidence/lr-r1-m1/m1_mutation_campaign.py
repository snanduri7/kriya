"""LR-R1-M1 consolidated mutation campaign at the final code revision.

Each mutant replaces one exact source text (must occur), runs every M1 test
file, restores the file, and checks the tree is clean again. Killed = the
suite exits non-zero. Results -> m1_mutation_results.json.
"""
import glob
import json
import os
import subprocess
import sys
import time

REPO = os.path.expanduser("~/kriya-wt/lr-r1-m1")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m1_mutation_results.json")

S = "kriya/core/attempt_evidence/scope.py"
W = "kriya/core/attempt_evidence/writer.py"
R = "kriya/core/attempt_evidence/reader.py"
RET = "kriya/core/attempt_evidence/retention.py"
E = "kriya/core/attempt_evidence/explain.py"
A = "kriya/workflow/attempt.py"
RS = "kriya/workflow/retry_strategy.py"
RC = "kriya/workflow/recovery_coordinator.py"
MR = "kriya/core/model_runtime.py"
IL = "benchmarks/reliability/import_legacy.py"

M = [
    # hash chain / seal / store
    ("chain", "prev-not-advanced", W, "            self._prev = model.digest(line)\n", "            pass\n"),
    ("chain", "reader-skips-seq-prev", R, 'if record.get("seq") != count + 1 or record.get("prev") != prev:', "if False:"),
    ("chain", "reader-skips-blob-rehash", R, "if model.digest(data) != ref:", "if False:"),
    ("chain", "reader-skips-seal-check", R, 'if seal.get("final_seq") != count or seal.get("head_digest") != prev:', "if False:"),
    ("chain", "not-append-only", W, "os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL", "os.O_WRONLY | os.O_CREAT | os.O_EXCL"),
    ("capture", "blobs-in-digest-only", W, "self._blobs_enabled = capture in (model.CAPTURE_FULL, model.CAPTURE_FULL_WITH_REASONING)", "self._blobs_enabled = True"),
    ("security", "store-not-private", W, "_FILE_MODE = 0o600", "_FILE_MODE = 0o644"),
    # raw response / reasoning (D3)
    ("raw-response", "stripped-content-recorded", S, 'content = {"content": raw if raw is not None else response.content,', 'content = {"content": response.content,'),
    ("raw-response", "reasoning-always-kept", S, '"reasoning": reasoning if capture_reasoning() else None}', '"reasoning": reasoning}'),
    # identity
    ("identity", "wire-seq-not-advanced", S, "    call.wires += 1\n    token = _WIRE.set(call.wires)", "    token = _WIRE.set(call.wires)"),
    ("identity", "iteration-identity-missing", "kriya/workflow/workflow.py", "attempt_evidence_token = attempt_evidence_scope.enter_attempt_iteration(lambda: state.attempt_number)", "attempt_evidence_token = None"),
    # prompt / request parity
    ("prompt", "developer-sections-unrecorded", "kriya/workflow/context_budget.py", '    _record_developer_sections("".join(fitted), segments,', '    (lambda *a, **k: None)("".join(fitted), segments,'),
    ("prompt", "recorder-wrong-stream", S, "        body = runtime.wire_payload(request, stream=stream)", "        body = runtime.wire_payload(request, stream=not stream)"),
    ("prompt", "native-posted-body-diverges", MR, '        async with http.stream("POST", self._url(client), json=self.wire_payload(request, stream=True),', '        async with http.stream("POST", self._url(client), json={**self.wire_payload(request, stream=True), "x": 1},'),
    ("prompt", "native-unknown-key", MR, '        payload["truncate"] = False\n', '        payload["truncate"] = False\n        payload["extra_unknown"] = 1\n'),
    ("prompt", "resend-unlabelled", "kriya/core/llm.py", 'attempt_evidence_scope.next_wire_reason("response_format_dropped")', "pass"),
    # authority / parse
    ("authority", "authority-unrecorded", A, "    _record_authority_snapshot(state, ctx, kwargs, capabilities, requested, model)", "    pass"),
    ("parse", "parse-unrecorded", "kriya/agents/agent.py", "            attempt_evidence_scope.record_developer_parse(parsed, filepath, selected_protocol=protocol,", "            (lambda *a, **k: None)(parsed, filepath, selected_protocol=protocol,"),
    # candidate diff
    ("candidate", "digests-swapped", A, '"before_digest": raw_digest(before) if before is not None else None,\n                       "after_digest": after_digest', '"before_digest": after_digest,\n                       "after_digest": raw_digest(before) if before is not None else None'),
    ("candidate", "no-newline-marker", A, 'line if line.endswith("\\n") else line + "\\n\\\\ No newline at end of file\\n"', "line"),
    ("candidate", "refused-even-if-staged", S, '        if closed is None or closed.get("attempt") != failure.attempt or closed.get("staged"):', '        if closed is None or closed.get("attempt") != failure.attempt:'),
    # gate attribution
    ("gates", "gate-outcome-unmirrored", "kriya/workflow/state.py", "        attempt_evidence_scope.mirror_gate_outcome(len(self.gate_outcomes) - 1, outcome)", "        pass"),
    ("gates", "validator-pass-unrecorded", "kriya/tools/validate.py", "            _record_gate_result(name, result, started)\n", ""),
    ("gates", "terminal-unrecorded", "kriya/workflow/terminal_gate_service.py", "        _record_terminal_report(report)\n", ""),
    # retry delta
    ("retry-delta", "first-guard-removed", S, '    if current["first"] is None:\n        current["first"] = summary\n        if inputs.previous is not None:', '    current["first"] = summary\n    if True:\n        if inputs.previous is not None:'),
    ("retry-delta", "always-present", S, 'information_gain="PRESENT" if changed else "NONE"', 'information_gain="PRESENT"'),
    ("retry-delta", "failure-dimension-dropped", S, '        "failure_signature": (previous.get("trigger_failure"), current.get("trigger_failure")),\n', ""),
    # fallback
    ("fallback", "escalation-selected-wrong", A, '"selected": selected.model if selected is not None else None,\n        "newly_rejected"', '"selected": requested.model,\n        "newly_rejected"'),
    ("fallback", "call-selected-wrong", A, '"selected": profile.model, "profile_digest": profile.digest})', '"selected": requested_model, "profile_digest": requested_digest})'),
    ("fallback", "refused-call-unrecorded", A, "    except BaseException:\n        attempt_evidence_scope.record_fallback_decision({", "    except ZeroDivisionError:\n        attempt_evidence_scope.record_fallback_decision({"),
    # recovery decision
    ("recovery", "decision-unrecorded", RC, "        _record_recovery_decision(state, classified, decision)\n", ""),
    ("recovery", "retry-flag-wrong", RC, '"retry": bool(not decision.stop_loop and retry is not None and retry.should_continue),', '"retry": not decision.stop_loop,'),
    # recorder failure non-interference (D5)
    ("non-interference", "emit-raises", S, '        logger.warning("Attempt evidence: %s not recorded (%s: %s)", kind, type(error).__name__, error)\n', '        raise\n'),
    ("non-interference", "unavailable-store-raises", S, '            run.status, run.reason = STATUS_UNAVAILABLE, f"{type(error).__name__}: {error}"', "            raise"),
    # I-2 planted effects
    ("I-2", "planted-prompt-change", "kriya/core/llm.py", "                try:\n                    return await method(self, *args, **kwargs)", "                try:\n                    if attempt_evidence_scope.capture_mode() is not None and args and isinstance(args[0], str):\n                        args = (args[0] + \" \",) + tuple(args[1:])\n                    return await method(self, *args, **kwargs)"),
    ("I-2", "planted-decision-change", RC, "    if attempt_evidence_scope.capture_mode() is None:\n        return\n    try:\n        retry = decision.retry_decision", "    if attempt_evidence_scope.capture_mode() is None:\n        return\n    state.budgets.retry_count += 1\n    try:\n        retry = decision.retry_decision"),
    # retention
    ("retention", "named-unprotected", RET, "        if store.run_id in named:", "        if False:"),
    ("retention", "unsealed-young-unprotected", RET, "        elif not store.sealed and now - store.stamp < UNSEALED_GRACE_SECONDS:", "        elif False:"),
    ("retention", "size-bound-ignored", RET, "    while kept and total > max_bytes:", "    while False:"),
    ("retention", "dry-run-writes", RET, "    if not dry_run:\n        for store in pruned:", "    if True:\n        for store in pruned:"),
    ("retention", "run-close-prune-missing", "kriya/control/run_coordinator.py", "                    attempt_evidence_scope.prune_after_run(context, lambda: workspace_run_references(canonical))\n", ""),
    # I-1
    ("I-1", "reader-lists-staging", R, 'if not name.startswith(".") and os.path.isfile', "if os.path.isfile"),
    ("I-1", "consumer-names-layout", "scripts/pinned_images.py", "import ", 'LEAK = "attempt-evidence seal.json"\nimport '),
    # I-3
    ("I-3", "final-diff-to-attempt", IL, '        out.put("candidate.change", {"decision": "RUN_FINAL_DIFF", "scope": "run",', '        out.put("candidate.change", {"decision": "STAGED", "path": "x"}, content={"diff": final_diff}, run_id=run_id, attempt_number=1)\n        out.put("candidate.change", {"decision": "RUN_FINAL_DIFF", "scope": "run",'),
    ("I-3", "absence-dropped", IL, '    ("retry.delta", ("Q7",), "retry information delta did not exist before M1"),\n', ""),
    ("I-3", "explain-overrides-evidence", E, '            if label in answers and answers[label].get("status") != RECORDED:', "            if label in answers:"),
    ("I-3", "output-inside-evidence", IL, "    if state_real == evidence_real or state_real.startswith(evidence_real + os.sep):", "    if False:"),
    # D7
    ("D7", "planted-decision-read", "kriya/workflow/retry_policy.py", "def decide_for_state(state, *, max_retries: int, targeted_max_retries: int, has_fallback_model: bool) -> RetryDecision:", "def decide_for_state(state, *, max_retries: int, targeted_max_retries: int, has_fallback_model: bool) -> RetryDecision:\n    _ = getattr(state, \"x\", None) and state.stop_reason_evidence"),
    ("D7", "stale-code-reported", RC, " or typed.message != state.environment_failure:", ":"),
    ("D7", "code-on-any-stop", RS, '    if failure.type == "regression_unattributed" and state.environment_failure is not None:', "    if state.environment_failure is not None:"),
    # explain
    ("explain", "q6-always-not-applicable", E, "    elif not diagnoses:", "    elif True:"),
    ("explain", "q7-crosses-invocations", E, "        same_invocation = nxt is not None and nxt[:2] == key[:2]", "        same_invocation = nxt is not None"),
]


def run_tests():
    files = sorted(glob.glob(os.path.join(REPO, "tests/test_lr_r1_m1_*.py")))
    env = dict(os.environ, PYTHONPATH=REPO)
    proc = subprocess.run(f"ulimit -n 256; {PYTEST} -q -p no:cacheprovider -n 8 " + " ".join(files),
                          shell=True, cwd=REPO, env=env, capture_output=True, text=True)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main():
    assert subprocess.run(["git", "diff", "--quiet"], cwd=REPO).returncode == 0, "tree not clean"
    code, tail = run_tests()
    results = {"baseline": {"exit": code, "summary": tail}, "mutants": []}
    assert code == 0, f"baseline not green: {tail}"
    for group, label, rel, old, new in M:
        path = os.path.join(REPO, rel)
        with open(path) as handle:
            original = handle.read()
        if original.count(old) < 1:
            results["mutants"].append({"group": group, "label": label, "file": rel, "status": "PATTERN_MISSING"})
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
