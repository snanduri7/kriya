"""REG-R1 context-stability mutation suite: each mutant is one exact source substitution in the worktree; the
focused tests must fail. Every mutated file is restored byte-for-byte after its run (and verified).

usage (cwd = worktree): python reg_r1_context_mutants.py <python> <out.json>
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

TYPE_STATES = ('        states = {"outcome": FIELD_STABLE if len({o.outcome for o in observed}) == 1 '
               'else BASELINE_OUTCOME_UNSTABLE,\n'
               '                  "failure_type": FIELD_STABLE if len({o.failure_type for o in observed}) == 1\n'
               '                  else BASELINE_TYPE_UNSTABLE}')
PRE_CHECK = ('    if current_revision() != baseline.workspace_revision:\n        return None, "BASELINE_REVISION_CHANGED"\n'
             '    replays: List[PytestSuiteEvidence] = []')
POST_CHECK = ('    if current_revision() != baseline.workspace_revision:\n        return None, "BASELINE_REVISION_CHANGED"\n'
              '    return {"context_id"')
LEVEL2_REASONS = ('    reasons = [f"level2:{test_id}:{classification.value}" for test_id, classification in '
                  'per_test.level2.items()\n'
                  '               if classification in TERMINAL_BLOCKING_CLASSIFICATIONS]\n')

MUTANTS = [
    # ---- the owner's context-stability list
    ("reuse isolated-test stability for the suite context (replay only the failing tests)", PS,
     "            result = replay(baseline.invocation.target_test)",
     "            result = replay(tuple(c.node_id for c in evidence.cases if c.failing))"),
    ("treat an unstable baseline outcome as harmless volatility", PS,
     "        reason = next((state for state in states.values() if state != FIELD_STABLE), None)",
     "        reason = None"),
    ("treat an unstable baseline type as harmless volatility", PS, TYPE_STATES,
     TYPE_STATES.replace('FIELD_STABLE if len({o.failure_type for o in observed}) == 1\n'
                         '                  else BASELINE_TYPE_UNSTABLE}', 'FIELD_STABLE}')),
    ("learn volatility from the candidate (unmeasured difference excused)", VB,
     "        if measured is None:\n            required[key] = differing\n"
     "            level2[shown] = DeltaClassification.STABILITY_UNRESOLVED\n            continue",
     "        if measured is None:\n            level2[shown] = DeltaClassification.PRE_EXISTING_FAILURE\n            continue"),
    ("ignore context binding: working directory", PS,
     '        "workspace_revision": baseline.workspace_revision, "working_directory": working_directory,\n',
     '        "workspace_revision": baseline.workspace_revision,\n'),
    ("ignore context binding: pytest session facts", PS,
     '        "session": [list(fact) for fact in evidence.session] if evidence is not None else None,\n', ""),
    # Removing only selection_identity is EQUIVALENT (target_test stays bound and production always derives
    # selection_identity from it - run 1 survivor); the selection binding is both lines:
    ("ignore context binding: selection", PS,
     '        "selection_identity": baseline.invocation.selection_identity,\n'
     '        "target_test": list(baseline.invocation.target_test) if baseline.invocation.target_test is not None '
     'else None,\n', ""),
    ("ignore context binding: environment", PS,
     '        "environment_fingerprint": baseline.invocation.environment_fingerprint,\n', ""),
    ("ignore context binding: command", PS,
     '        "command_identity": baseline.invocation.command_identity,\n', ""),
    ("ignore context binding: baseline run/report", PS,
     '        "baseline": {"run_id": baseline.run_id, "report": evidence.report_digest if evidence is not None '
     'else None},\n', ""),
    ("ignore the replay session check", PS,
     '        if replayed.session != evidence.session:\n            return None, "REPLAY_CONTEXT_MISMATCH"\n', ""),
    ("reuse cache across base revisions", PS,
     '        "workspace_revision": baseline.workspace_revision, "working_directory": working_directory,\n',
     '        "working_directory": working_directory,\n'),
    ("replay the full suite again for every dispute (cache ignored)", PS,
     "    entry = cache.get(context_id)\n", "    entry = None\n"),
    ("single same-context replay", PS, "BASELINE_REPLAYS = 2", "BASELINE_REPLAYS = 1"),
    ("allow a stable candidate-changed message through", VB,
     "        if any(status == FIELD_STABLE for status in statuses.values()):\n"
     "            level2[shown] = DeltaClassification.CHANGED_FAILURE",
     "        if False:\n            level2[shown] = DeltaClassification.CHANGED_FAILURE"),
    ("restore aggregate-output authority", VB, LEVEL2_REASONS,
     LEVEL2_REASONS + "    if level1.classification in TERMINAL_BLOCKING_CLASSIFICATIONS:\n"
                      '        reasons.append(f"level1:{level1.classification.value}")\n'),
    # ---- carried over: still meaningful on the new code
    ("treat a volatile baseline field as stable", VB,
     "        if any(status == FIELD_STABLE for status in statuses.values()):",
     "        if any(status in (FIELD_STABLE, FIELD_VOLATILE) for status in statuses.values()):"),
    ("unresolved stability excused as volatile", VB,
     "        elif all(status == FIELD_VOLATILE for status in statuses.values()):",
     "        elif all(status in (FIELD_VOLATILE, FIELD_UNRESOLVED) for status in statuses.values()):"),
    ("test absent from a replay treated as observed", PS, "    if any(o is None for o in observed):", "    if False:"),
    ("skip the pre-replay revision check", PS, PRE_CHECK, "    replays: List[PytestSuiteEvidence] = []"),
    ("skip the post-replay revision check", PS, POST_CHECK, '    return {"context_id"'),
    ("accept an incomplete replay", PS,
     "        if replayed is None or not replayed.complete:", "        if replayed is None:"),
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
     "        if before is None:\n            if after.failing:", "        if before is None:\n            if False:"),
    ("ignore missing test", VB,
     "        if after is None:\n            level2[shown] = DeltaClassification.NEWLY_SKIPPED_OR_NOT_EXECUTED\n"
     "            continue",
     "        if after is None:\n            continue"),
    ("accept incomplete report", VB,
     "                   if evidence is None or not evidence.complete]", "                   if evidence is None]"),
    ("aggregate match passes incomplete evidence", VB,
     "        if post.success:\n            return replace(legacy, pytest_evidence_status=status)",
     "        if post.success or not legacy.blocking:\n            return replace(legacy, pytest_evidence_status=status)"),
    ("allow old checkpoint without new evidence", VB,
     '    return isinstance(outcome, dict) and "pytest_evidence" not in outcome', "    return False"),
    ("skip JUnit integrity check", VB,
     '    elif not isinstance(raw_cases, dict) or integrity.get("ok") is not True:',
     "    elif not isinstance(raw_cases, dict):"),
    ("raw-evidence integrity ignored (duplicates/counts)", TE,
     "    reason = None\n    if suites == 0 or undeclared:", "    reason = None\n    if False:"),
    ("failure message omitted from signature", VB,
     '        return {"message": self.message_digest, "body": self.body_digest}[name]',
     '        return {"message": None, "body": self.body_digest}[name]'),
    ("failure body omitted from signature", VB,
     '        return {"message": self.message_digest, "body": self.body_digest}[name]',
     '        return {"message": self.message_digest, "body": None}[name]'),
    ("normalization too aggressive (digits stripped)", VB,
     '    return content_revision(normalize_failure_text(text or ""))',
     '    return content_revision(re.sub(r"\\d", "", normalize_failure_text(text or "")))'),
    ("session facts include volatile header lines", VB,
     '_PYTEST_SESSION_FACT_RE = re.compile(r"^(platform|rootdir|configfile|testpaths|plugins):? (.+)$", re.MULTILINE)',
     '_PYTEST_SESSION_FACT_RE = re.compile(r"^(platform|rootdir|configfile|testpaths|plugins|Using|asyncio):? (.+)$",'
     ' re.MULTILINE)'),
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
            print(f"NOT_APPLIED {name}", flush=True)
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
