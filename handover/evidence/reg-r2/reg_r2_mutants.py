"""REG-R2 baseline-behavior-envelope mutation suite: each mutant is one exact source substitution in the worktree; the
focused tests must fail. Every mutated file is restored byte-for-byte after its run (and verified).

usage (cwd = worktree): python reg_r2_mutants.py <python> <out.json>
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

VB = "kriya/workflow/validation_baseline.py"
PS = "kriya/workflow/pytest_stability.py"
TE = "kriya/tools/test_execution.py"
TESTS = ["tests/test_reg_r2_flaky_baseline_envelope.py", "tests/test_reg_r2_workflow_reproducer.py",
         "tests/test_reg_r1_pytest_regression_authority.py", "tests/test_reg_r1_workflow_reproducer.py",
         "tests/test_validation_baseline.py", "tests/test_baseline_surefire.py"]

CACHE_LOOKUP = ('    entry = cache.get(context_id)\n    source, failure = "cache", None\n'
                '    if not (isinstance(entry, dict) and entry.get("context_id") == context_id\n')
LEVEL2_REASONS = ('    reasons = [f"level2:{test_id}:{classification.value}" for test_id, classification in '
                  'per_test.level2.items()\n'
                  '               if classification in TERMINAL_BLOCKING_CLASSIFICATIONS]\n')

MUTANTS = [
    # ---- the owner's REG-R2 list
    ("privilege the original baseline observation over its replays", PS,
     "            observed = [pre_cases[key], *(r.get(key) for r in replayed)]",
     "            observed = [pre_cases[key]] * (1 + len(replayed))"),
    ("add the candidate state to the baseline envelope", VB,
     "        state = envelope.state(after) if envelope.unresolved is None else None",
     "        state = (envelope.state(after) or BaselineStateEvidence(after.outcome, after.failure_type, 1))"
     " if envelope.unresolved is None else None"),
    ("ignore an unseen candidate outcome (match on type only)", VB,
     "        return next((s for s in self.states if (s.outcome, s.failure_type) == (case.outcome, case.failure_type)),",
     "        return next((s for s in self.states if s.failure_type == case.failure_type),"),
    ("ignore an unseen candidate exception type (match on outcome only)", VB,
     "        return next((s for s in self.states if (s.outcome, s.failure_type) == (case.outcome, case.failure_type)),",
     "        return next((s for s in self.states if s.outcome == case.outcome),"),
    ("treat one conditional observation as stable message evidence", VB,
     "                status = (FIELD_NOT_ESTABLISHED if len(same) == 1",
     "                status = (FIELD_STABLE if len(same) == 1"),
    ("treat a known volatile conditional message as blocking", VB,
     "            elif status == FIELD_VOLATILE:\n                volatile.append(name)",
     "            elif status == FIELD_VOLATILE:\n                changed.append(name)"),
    ("reuse an envelope across verification-context identity", PS, CACHE_LOOKUP,
     '    entry = next(iter(cache.values()), None)\n    source, failure = "cache", None\n'
     '    if not (isinstance(entry, dict)\n'),
    ("reuse an envelope across base revision", PS,
     '        "workspace_revision": baseline.workspace_revision, "working_directory": working_directory,\n',
     '        "working_directory": working_directory,\n'),
    ("allow incomplete replay evidence", PS,
     "        if replayed is None or not replayed.complete:", "        if replayed is None:"),
    ("allow an incomplete report", VB,
     "                   if evidence is None or not evidence.complete]", "                   if evidence is None]"),
    ("restore aggregate-output authority", VB, LEVEL2_REASONS,
     LEVEL2_REASONS + "    if level1.classification in TERMINAL_BLOCKING_CLASSIFICATIONS:\n"
                      '        reasons.append(f"level1:{level1.classification.value}")\n'),
    # ---- REG-R2 additional
    ("lack of stability evidence becomes evidence against the candidate", VB,
     "            elif status == FIELD_NOT_ESTABLISHED:\n                not_established.append(name)",
     "            elif status == FIELD_NOT_ESTABLISHED:\n                changed.append(name)"),
    ("a stable same-state field the candidate changed is not blamed", VB,
     "            if after.field_digest(name) in state.observed_digests(name):\n                continue",
     "            if True:\n                continue"),
    ("a state change never requests the envelope (original privileged)", VB,
     "            required[key] = differing or (ENVELOPE_STATE,)",
     "            if differing:\n                required[key] = differing"),
    ("an unmeasured state change is excused", VB,
     "            required[key] = differing or (ENVELOPE_STATE,)\n            level2[shown] = provisional",
     "            required[key] = differing or (ENVELOPE_STATE,)\n"
     "            level2[shown] = DeltaClassification.PRE_EXISTING_FAILURE"),
    ("a candidate state outside the envelope is excused", VB,
     "            level2[shown] = (DeltaClassification.STABILITY_UNRESOLVED if same_state else provisional)",
     "            level2[shown] = DeltaClassification.PRE_EXISTING_FAILURE"),
    ("a test absent from a replay is treated as observed", VB,
     "    if not observations or any(o is None for o in observations):",
     "    observations = [o for o in observations if o is not None]\n    if not observations:"),
    ("a flaky test loses its FLAKY_PREEXISTING label", VB,
     "        level2[shown] = (DeltaClassification.FLAKY_PREEXISTING if envelope.flaky",
     "        level2[shown] = (DeltaClassification.PRE_EXISTING_FAILURE if envelope.flaky"),
    ("FLAKY_PREEXISTING made blocking", VB,
     "    DeltaClassification.STABILITY_UNRESOLVED,\n})",
     "    DeltaClassification.STABILITY_UNRESOLVED,\n    DeltaClassification.FLAKY_PREEXISTING,\n})"),
    ("a known envelope never relabels a non-blocking resolution", VB,
     "            level2[shown] = DeltaClassification.FLAKY_PREEXISTING if known is not None else provisional",
     "            level2[shown] = provisional"),
    ("the flake-rate verdict is not recorded", PS,
     "    return {test: FLAKE_RATE_REGRESSION for test, classification in delta.level2.items()",
     "    return {test: FLAKE_RATE_REGRESSION for test, classification in {}.items()"),
    # ---- REG-R1 mutants still meaningful on the new code
    ("reuse isolated-test stability for the suite context (replay only the failing tests)", PS,
     "            result = replay(baseline.invocation.target_test)",
     "            result = replay(tuple(c.node_id for c in evidence.cases if c.failing))"),
    ("ignore context binding: working directory", PS,
     '        "workspace_revision": baseline.workspace_revision, "working_directory": working_directory,\n',
     '        "workspace_revision": baseline.workspace_revision,\n'),
    ("ignore context binding: pytest session facts", PS,
     '        "session": [list(fact) for fact in evidence.session] if evidence is not None else None,\n', ""),
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
    ("replay the full suite again for every dispute (cache ignored)", PS,
     "    entry = cache.get(context_id)\n", "    entry = None\n"),
    ("single same-context replay", PS, "BASELINE_REPLAYS = 2", "BASELINE_REPLAYS = 1"),
    ("skip the pre-replay revision check", PS,
     '    if current_revision() != baseline.workspace_revision:\n        return None, "BASELINE_REVISION_CHANGED"\n'
     '    replays: List[PytestSuiteEvidence] = []',
     "    replays: List[PytestSuiteEvidence] = []"),
    ("skip the post-replay revision check", PS,
     '    if current_revision() != baseline.workspace_revision:\n        return None, "BASELINE_REVISION_CHANGED"\n'
     '    return {"context_id"',
     '    return {"context_id"'),
    ("ignore a new failing test", VB,
     "        if before is None:\n            if after.failing:", "        if before is None:\n            if False:"),
    ("ignore missing test", VB,
     "        if after is None:\n            level2[shown] = DeltaClassification.NEWLY_SKIPPED_OR_NOT_EXECUTED\n"
     "            continue",
     "        if after is None:\n            continue"),
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
]


def run_tests(python):
    proc = subprocess.run([python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                          capture_output=True, text=True, check=False, env={**os.environ})
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
