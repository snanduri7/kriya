"""REG-R1 Part 2: feed the preserved gate outputs through the a049c02 production comparator, unchanged.

For each pair (PRE, POST) the gate result {"success", "output"} each run returned is passed to
build_validation_outcome (the live POST call) and to classify_baseline_delta with a captured ValidationBaseline built
from the PRE outcome (environment fingerprints omitted on both sides: the live PRE and POST environment identities
were equal, so the comparison reached level 1/2 exactly as here). Also reports classify_level1_delta alone.

usage: venv-gr1/bin/python reg_r1_compare.py <runs_dir> <out.json>
"""
import json
import os
import sys
from collections import Counter

from kriya.workflow.validation_baseline import (
    ValidationBaseline,
    ValidationInvocation,
    build_validation_outcome,
    classify_baseline_delta,
    classify_level1_delta,
)

PAIRS = [("BASE-1", "BASE-2"), ("CANDIDATE-1", "CANDIDATE-2"), ("BASE-1", "CANDIDATE-1"), ("BASE-2", "CANDIDATE-2"),
         ("BASE-1", "CANDIDATE-2"), ("BASE-2", "CANDIDATE-1")]


def gate_result(runs, label):
    with open(os.path.join(runs, f"{label}.meta.json"), encoding="utf-8") as handle:
        meta = json.load(handle)
    with open(os.path.join(runs, f"{label}.gate_output.txt"), encoding="utf-8", newline="") as handle:
        output = handle.read()
    return {"success": meta["success"], "output": output}


def compare(pre_result, post_result):
    pre, post = build_validation_outcome(pre_result), build_validation_outcome(post_result)
    baseline = ValidationBaseline(workspace_revision="reg-r1", run_id="reg-r1",
                                  invocation=ValidationInvocation(command_identity="reg-r1", selection_identity="full"),
                                  captured_at="reg-r1", outcome=pre)
    delta = classify_baseline_delta(baseline, post)
    return {
        "whole_output_level1": classify_level1_delta(pre, post).classification.value,
        "pre_fingerprint": pre.failure_fingerprint, "post_fingerprint": post.failure_fingerprint,
        "per_test_level2": dict(Counter(v.value for v in delta.level2.values())),
        "per_test_non_preexisting": {k: v.value for k, v in delta.level2.items() if v.value != "PRE_EXISTING_FAILURE"},
        "pre_counts": pre.aggregate_counts, "post_counts": post.aggregate_counts,
        "pre_parsed_failures": len(pre.test_outcomes or ()), "post_parsed_failures": len(post.test_outcomes or ()),
        "aggregate_drop_detected": delta.aggregate_drop_detected, "blocking": delta.blocking,
        "blocking_reasons": list(delta.blocking_reasons), "level2_available": delta.level2_available,
    }


def main(runs, out):
    results = {f"{a} vs {b}": compare(gate_result(runs, a), gate_result(runs, b)) for a, b in PAIRS}
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=1)
    for name, r in results.items():
        print(f"{name:28} level1={r['whole_output_level1']:22} level2={r['per_test_level2']} "
              f"blocking={r['blocking']} reasons={r['blocking_reasons']} counts={r['pre_counts']}->{r['post_counts']}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
