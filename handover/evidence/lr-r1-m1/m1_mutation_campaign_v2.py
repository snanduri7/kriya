"""LR-R1-M1 consolidated mutation campaign v2 (after T6 A-H): the v1 mutants plus
the T6 fix mutants, at the final code revision.

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
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m1_mutation_results_v2.json")

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

M += [
    # T6-A no-progress terminal evidence
    ("T6-A", "classification-dropped", RC, '            "progress_classification": state.last_progress_classification,', '            "progress_classification": None,'),
    ("T6-A", "reason-constant", RC, '            "no_progress_reason": state.no_progress_reason,', '            "no_progress_reason": "RETRY_NO_PROGRESS_EXHAUSTED",'),
    ("T6-A", "q9-drops-progress", E, '                                      "progress_classification", "no_progress_reason",', ''),
    # T6-B refused answers / NO_CHANGE
    ("T6-B", "no-parsed-refusal", S, '    if not proposed and not edits:\n        for proposal in parsed:', '    if False:\n        for proposal in parsed:'),
    ("T6-B", "no-change-refusable", S, '_REFUSABLE_ANSWERS = frozenset({"file", "edits", "invalid"})', '_REFUSABLE_ANSWERS = frozenset({"file", "edits", "invalid", "no_change"})'),
    ("T6-B", "invented-null-bytes", S, '"diff": "NOT_APPLICABLE", "after_digest": "NOT_APPLICABLE"}', '"diff": None, "after_digest": None}'),
    ("T6-B", "no-change-unexplained", E, '    elif parses and parse_kinds == {"no_change"}:', '    elif False:'),
    ("T6-B", "any-parse-is-no-change", E, '    elif parses and parse_kinds == {"no_change"}:', '    elif parses:'),
    # T6-C terminal cause from units
    ("T6-C", "first-unit", E, '    payload = units[-1].get("payload") or {}', '    payload = units[0].get("payload") or {}'),
    ("T6-C", "exception-hidden", E, '        if payload.get("outcome") == "EXCEPTION":', '        if False:'),
    # T6-D TOOL unit
    ("T6-D", "no-tool-record", S, '            _record_tool_execution(tool_name, holder.get("result"), closing)', '            pass'),
    ("T6-D", "no-tool-status", S, '        closing["tool_status"] = status', '        pass'),
    ("T6-D", "wrong-unit-kind", S, '    with unit_scope(cfg, unit_id, "tool", {"tool_name": tool_name}) as closing:', '    with unit_scope(cfg, unit_id, "model", {"tool_name": tool_name}) as closing:'),
    ("T6-D", "result-not-handed", "kriya/workflow/workflow_controller.py", '                    evidence["result"] = result', '                    pass'),
    ("T6-D", "explain-generic", E, '    if _of(records, "tool.execution") and not requests:', '    if False:'),
    # T6-E pre-dispatch / no model call
    ("T6-E", "undispatched-as-missing", E, '    elif not unanswered:', '    elif False:'),
    ("T6-E", "missing-as-undispatched", E, '    elif not unanswered:', '    elif True:'),
    ("T6-E", "hide-undispatched", E, '        if undispatched:\n            extra["not_dispatched"]', '        if False:\n            extra["not_dispatched"]'),
    ("T6-E", "no-call-as-missing", E, '        NOT_APPLICABLE, "no_model_call: no model request was made in this attempt")', '        NOT_RECORDED, "no_model_call: no model request was made in this attempt")'),
    # T6-F per-call authority
    ("T6-F", "no-transition-call", "kriya/workflow/attempt.py", '            attempt_evidence_scope.record_authority_transition(', '            (lambda *a, **k: None)('),
    ("T6-F", "unchanged-counted", S, '            if new is not None and new != target.get("requested_operation"):', '            if new is not None:'),
    ("T6-F", "payload-not-updated", S, '                target["requested_operation"] = changed[target["path"]] = new', '                changed[target["path"]] = new'),
    ("T6-F", "binds-first-snapshot", E, '    snapshot = prior[-1]', '    snapshot = prior[0]'),
    # T6-G1..G3
    ("T6-G1", "q6-conflict-suppressed", E, '"no_progress_reason", "stop_reason_code", "plan_scope_conflict")', '"no_progress_reason", "stop_reason_code")'),
    ("T6-G1", "q9-conflict-suppressed", E, '                              for r in decisions if (r.get("payload") or {}).get("plan_scope_conflict")],', '                              for r in decisions if False],'),
    ("T6-G2", "scope-dropped", E, '"write_scope_mode", "authorized_write_scope", "targets", "transition")', '"write_scope_mode", "targets", "transition")'),
    ("T6-G3", "missing-as-no-answer", E, '        if response is None:\n            return None', '        if response is None:\n            reasons.add("missing"); continue'),
    ("T6-G3", "answer-as-no-answer", E, '        else:\n            return None\n    return sorted(reasons)', '        else:\n            continue\n    return sorted(reasons)'),
    ("T6-G3", "refusal-excluded", E, '            reasons.add(payload.get("refusal_type") or "refused_before_dispatch")\n            continue', '            return None'),
    ("T6-G3", "cancel-unlabelled", E, '        if response.get("cancelled"):\n            reasons.add("CANCELLED")', '        if False:\n            reasons.add("CANCELLED")'),
    # T6-H terminal cause precedence
    ("T6-H", "always-last-unit", E, '    terminal_cause = _run_terminal_cause(records, units, closed[-1], run)', '    terminal_cause = _terminal_cause(units)'),
    ("T6-H", "ignore-reason-codes", E, '            cause.update({key: details[key] for key in ("reason_codes", "exception_type", "error")\n                          if key in details})', '            pass'),
    ("T6-H", "any-controller-event-terminal", E, '                  and (r.get("payload") or {}).get("kind") in _CONTROLLER_TERMINAL_KINDS]', ']'),
    ("T6-H", "earliest-controller-record", E, '        terminal = controller[-1]', '        terminal = controller[0]'),
    ("T6-H", "ignore-terminal-gates", E, '    if failed_gates:', '    if False:'),
    ("T6-H", "no-guard", E, '    if succeeded and terminal_status is not None and terminal_status != "SUCCESS":', '    if False:'),
    ("T6-H", "deciding-any-status", E, '        if decision and decision.get("subtask_id") and decision.get("status") not in (None, "completed"):', '        if decision and decision.get("subtask_id"):'),
    # T6 path behaviour seen through explain
    ("T6-paths", "investigation-wire-collapse", S, '    call.wires += 1\n    token = _WIRE.set(call.wires)', '    token = _WIRE.set(1)'),
    ("T6-paths", "deadline-error-unbound", "kriya/core/llm.py", '                attempt_evidence_scope.record_wire_error(error)', '                pass'),
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
