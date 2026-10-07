"""REG-R1 mutation suite: each mutant is one exact source substitution in the worktree; the focused tests must fail.

usage (cwd = worktree): python reg_r1_mutants.py <python> <out.json>
Every mutated file is restored byte-for-byte after its run (and verified).
"""
import hashlib
import json
import pathlib
import subprocess
import sys

VB = "kriya/workflow/validation_baseline.py"
PS = "kriya/workflow/pytest_stability.py"
TE = "kriya/tools/test_execution.py"
TESTS = ["tests/test_reg_r1_pytest_regression_authority.py", "tests/test_reg_r1_workflow_reproducer.py",
         "tests/test_validation_baseline.py"]

MUTANTS = [
    ("restore aggregate-output authority", VB,
     '    reasons = [f"level2:{test_id}:{classification.value}" for test_id, classification in per_test.level2.items()\n'
     '               if classification in TERMINAL_BLOCKING_CLASSIFICATIONS]\n',
     '    reasons = [f"level2:{test_id}:{classification.value}" for test_id, classification in per_test.level2.items()\n'
     '               if classification in TERMINAL_BLOCKING_CLASSIFICATIONS]\n'
     '    if level1.classification in TERMINAL_BLOCKING_CLASSIFICATIONS:\n'
     '        reasons.append(f"level1:{level1.classification.value}")\n'),
    ("treat volatile baseline field as stable", VB,
     "        if any(status == FIELD_STABLE for status in statuses.values()):",
     "        if any(status in (FIELD_STABLE, FIELD_VOLATILE) for status in statuses.values()):"),
    ("treat stable changed field as volatile (measurement)", PS,
     "            statuses = {name: FIELD_STABLE if all(o.field_digest(name) == case.field_digest(name) for o in seen)\n"
     "                        else FIELD_VOLATILE for name in STABILITY_FIELDS}",
     "            statuses = {name: FIELD_VOLATILE for name in STABILITY_FIELDS}"),
    ("treat stable changed field as volatile (decision)", VB,
     "        if any(status == FIELD_STABLE for status in statuses.values()):\n"
     "            level2[shown] = DeltaClassification.CHANGED_FAILURE",
     "        if False:\n            level2[shown] = DeltaClassification.CHANGED_FAILURE"),
    ("ignore exception-type transition", VB,
     "        if before.outcome != after.outcome or before.failure_type != after.failure_type:",
     "        if before.outcome != after.outcome:"),
    ("ignore FAIL<->ERROR outcome transition", VB,
     "        if before.outcome != after.outcome or before.failure_type != after.failure_type:",
     "        if before.failure_type != after.failure_type:"),
    ("ignore PASS->FAIL", VB,
     "        if not before.failing:\n            if after.failing:\n"
     "                level2[shown] = DeltaClassification.NEW_FAILURE",
     "        if not before.failing:\n            if False:\n"
     "                level2[shown] = DeltaClassification.NEW_FAILURE"),
    ("ignore a new failing test", VB,
     "        if before is None:\n            if after.failing:",
     "        if before is None:\n            if False:"),
    ("ignore missing test", VB,
     "        if after is None:\n            level2[shown] = DeltaClassification.NEWLY_SKIPPED_OR_NOT_EXECUTED\n"
     "            continue",
     "        if after is None:\n            continue"),
    ("accept incomplete report", VB,
     "                   if evidence is None or not evidence.complete]",
     "                   if evidence is None]"),
    ("aggregate match passes incomplete evidence", VB,
     "        if post.success:\n            return replace(legacy, pytest_evidence_status=status)",
     "        if post.success or not legacy.blocking:\n            return replace(legacy, pytest_evidence_status=status)"),
    ("learn volatility from the candidate (unmeasured difference excused)", VB,
     "        if measured is None:\n            required[key] = differing\n"
     "            level2[shown] = DeltaClassification.STABILITY_UNRESOLVED\n            continue",
     "        if measured is None:\n            level2[shown] = DeltaClassification.PRE_EXISTING_FAILURE\n            continue"),
    ("indeterminate stability excused as volatile", VB,
     "        elif all(status == FIELD_VOLATILE for status in statuses.values()):",
     "        elif all(status in (FIELD_VOLATILE, FIELD_INDETERMINATE) for status in statuses.values()):"),
    ("reuse cache across base revisions", PS,
     '        "workspace_revision": baseline.workspace_revision,\n', ""),
    ("reuse cache across environments", PS,
     '        "environment_fingerprint": baseline.invocation.environment_fingerprint,\n', ""),
    ("reuse cache across test commands", PS,
     '        "command_identity": baseline.invocation.command_identity,\n', ""),
    ("reuse cache across baseline results", PS,
     '        "test_key": case.key, "baseline_result": case.identity(),\n', '        "test_key": case.key,\n'),
    ("cache an indeterminate measurement", PS,
     "        if failure is not None:\n            statuses, reason = _indeterminate(failure)\n",
     "        if failure is not None:\n            statuses, reason = _indeterminate(failure)\n"
     "            cache[binding] = {\"binding\": binding, \"fields\": statuses}\n"),
    ("skip the untouched-baseline revision check", PS,
     "    if current_revision() != baseline.workspace_revision:\n        failure = \"BASELINE_REVISION_CHANGED\"\n"
     "    else:",
     "    if False:\n        failure = \"BASELINE_REVISION_CHANGED\"\n    else:"),
    ("allow old checkpoint without new evidence", VB,
     '    return isinstance(outcome, dict) and "pytest_evidence" not in outcome',
     "    return False"),
    ("skip JUnit integrity check", VB,
     '    elif not isinstance(raw_cases, dict) or integrity.get("ok") is not True:',
     "    elif not isinstance(raw_cases, dict):"),
    ("raw-evidence integrity ignored (duplicates/counts)", TE,
     "    reason = None\n    if suites == 0 or undeclared:",
     "    reason = None\n    if False:"),
    ("failure message omitted from signature", VB,
     '        return {"message": self.message_digest, "body": self.body_digest}[name]',
     '        return {"message": None, "body": self.body_digest}[name]'),
    ("failure body omitted from signature", VB,
     '        return {"message": self.message_digest, "body": self.body_digest}[name]',
     '        return {"message": self.message_digest, "body": None}[name]'),
    ("normalization too aggressive (digits stripped)", VB,
     "    return content_revision(normalize_failure_text(text or \"\"))",
     "    return content_revision(re.sub(r\"\\d\", \"\", normalize_failure_text(text or \"\")))"),
    ("replay touches more than the disputed tests", PS,
     "        node_ids = [case.node_id for case, _ in pending.values()]",
     "        node_ids = [case.node_id for case, _ in pending.values()] + [\"tests\"]"),
    ("single baseline replay", PS, "BASELINE_REPLAYS = 2", "BASELINE_REPLAYS = 1"),
]


def run_tests(python):
    proc = subprocess.run([python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                          capture_output=True, text=True, check=False, env={**__import__("os").environ})
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main(python, out):
    baseline_code, baseline_line = run_tests(python)
    assert baseline_code == 0, f"unmutated tests must pass first: {baseline_line}"
    results = []
    for name, path, old, new in MUTANTS:
        source = pathlib.Path(path)
        original = source.read_bytes()
        text = original.decode()
        if text.count(old) != 1:
            results.append({"mutant": name, "status": "NOT_APPLIED", "occurrences": text.count(old)})
            continue
        source.write_text(text.replace(old, new))
        try:
            code, line = run_tests(python)
        finally:
            source.write_bytes(original)
            assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(original).digest()
        results.append({"mutant": name, "file": path, "status": "KILLED" if code != 0 else "SURVIVED", "tests": line})
        print(f"{results[-1]['status']:9} {name}  [{line}]", flush=True)
    summary = {"killed": sum(r["status"] == "KILLED" for r in results), "total": len(results), "results": results}
    pathlib.Path(out).write_text(json.dumps(summary, indent=1))
    print(f"{summary['killed']}/{summary['total']} killed")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
