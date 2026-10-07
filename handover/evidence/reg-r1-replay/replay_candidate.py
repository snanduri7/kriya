"""REG-R1 deterministic replay of the preserved qwen3.8 staged candidate (no model, no generation).

The live full-regression gate shape of run 20261006T231821-fb31caad, under the certified REG-R1 code:
baseline = PolymorphicValidator(<root>/.kriya/worktree).run_tests() on the frozen Graphify base (67f99bd);
POST = PolymorphicValidator(<root>/.kriya/worktrees/candidate-reg-r1, original=<base>).run_tests() with the
staged engine.py e47eacba...; decision = pytest_stability.classify_with_baseline_stability with REAL untouched-baseline
replays (replay_on_untouched_baseline on the base worktree) - exactly the production wiring in workflow.py.
The a049c02 whole-output verdict on the same two outcomes is reported for contrast.

usage (cwd = SEC-009-approved workspace, env sourced, PYTHONPATH=<certified worktree>):
  python replay_candidate.py <config.yaml> <root> <out_dir>
"""
import json
import os
import sys
import time

from kriya.config.config import load_config
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.pytest_stability import classify_with_baseline_stability, replay_on_untouched_baseline
from kriya.workflow.validation_baseline import (
    ValidationInvocation,
    _whole_output_delta,
    build_validation_outcome,
    capture_validation_baseline,
    classify_level1_delta,
)


def main(config_path, root, out):
    cfg = load_config(config_path)
    base = os.path.join(root, ".kriya", "worktree")
    cand = os.path.join(root, ".kriya", "worktrees", "candidate-reg-r1")
    revision = compute_workspace_content_hash(base)
    started = time.time()
    pre_raw = PolymorphicValidator(base, original_workspace_path=base, autonomy_cfg=cfg.autonomy).run_tests()
    baseline = capture_validation_baseline(
        workspace_revision=revision, run_id="reg-r1-replay",
        invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"), raw_result=pre_raw)
    post_raw = PolymorphicValidator(cand, original_workspace_path=base, autonomy_cfg=cfg.autonomy).run_tests()
    post = build_validation_outcome(post_raw)
    replays = []

    def replay(node_ids):
        replays.append(list(node_ids))
        return replay_on_untouched_baseline(node_ids, workspace_path=base, autonomy_cfg=cfg.autonomy)

    delta, measurement = classify_with_baseline_stability(
        baseline=baseline, post=post, post_environment=None, cache={}, replay=replay,
        current_revision=lambda: compute_workspace_content_hash(base))
    old = _whole_output_delta(baseline.outcome, post, classify_level1_delta(baseline.outcome, post))
    old_blocking = old.blocking  # a049c02: level 1 blocks on its own
    report = {
        "base_revision": revision, "wall_seconds": round(time.time() - started, 1),
        "pre": {"evidence": baseline.outcome.pytest_evidence.complete if baseline.outcome.pytest_evidence else None,
                "reason": baseline.outcome.pytest_evidence.reason if baseline.outcome.pytest_evidence else None,
                "collected": baseline.outcome.pytest_evidence.collected if baseline.outcome.pytest_evidence else None},
        "post": {"evidence": post.pytest_evidence.complete if post.pytest_evidence else None,
                 "collected": post.pytest_evidence.collected if post.pytest_evidence else None},
        "a049c02_whole_output": {"level1": old.level1.classification.value, "blocking": old_blocking,
                                 "reasons": list(old.blocking_reasons)[:5]},
        "reg_r1": {"authority": delta.authority, "blocking": delta.blocking,
                   "blocking_reasons": list(delta.blocking_reasons),
                   "diagnostic_level1": delta.level1.classification.value,
                   "level2_counts": {v: sum(1 for c in delta.level2.values() if c.value == v)
                                     for v in sorted({c.value for c in delta.level2.values()})},
                   "non_pre_existing": {k: v.value for k, v in delta.level2.items() if v.value != "PRE_EXISTING_FAILURE"},
                   "volatile_fields_ignored": {k: list(v) for k, v in delta.volatile_fields_ignored.items()}},
        "stability_replays": replays,
        "stability": measurement.records if measurement is not None else [],
    }
    with open(os.path.join(out, "replay_result.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    print(json.dumps({k: v for k, v in report.items() if k != "stability"}, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:4])
