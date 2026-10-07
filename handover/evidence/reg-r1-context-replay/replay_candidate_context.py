"""REG-R1 context-stability deterministic replay of the preserved qwen3.8 candidate (no model, no generation).

Production wiring (workflow.py, 5c55630): the baseline is the base worktree's full-suite gate; a stability replay is
that SAME gate call (the baseline's own selection, in place on the untouched base, revision checked before and
after). Two decisions share one context cache: the untouched base against a second untouched-base run (self-block
check) and the staged candidate e47eacba against the baseline.

usage (cwd = SEC-009-approved workspace, env sourced, PYTHONPATH=<certified worktree>):
  python replay_candidate_context.py <config.yaml> <root> <out_dir>
"""
import json
import os
import sys
import time

from kriya.config.config import load_config
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.pytest_stability import classify_with_baseline_stability
from kriya.workflow.validation_baseline import (
    PytestSuiteEvidence,
    ValidationInvocation,
    build_validation_outcome,
    capture_validation_baseline,
)

TIMING = "tests.test_ts_import_type_arguments::test_ts_normalizer_scales_linearly_on_large_files"


def summary(evidence):
    counts = {}
    for case in evidence.cases:
        counts[case.outcome] = counts.get(case.outcome, 0) + 1
    timing = evidence.by_key().get(TIMING)
    return {"complete": evidence.complete, "cases": evidence.collected, "counts": counts,
            "timing_test": None if timing is None else {"outcome": timing.outcome, "type": timing.failure_type,
                                                        "message": timing.message_digest, "body": timing.body_digest}}


def decision(delta, measurement):
    return {"authority": delta.authority, "blocking": delta.blocking, "blocking_reasons": list(delta.blocking_reasons),
            "diagnostic_level1": delta.level1.classification.value,
            "level2_counts": {v: sum(1 for c in delta.level2.values() if c.value == v)
                              for v in sorted({c.value for c in delta.level2.values()})},
            "non_pre_existing": {k: v.value for k, v in delta.level2.items() if v.value != "PRE_EXISTING_FAILURE"},
            "volatile_fields_ignored": {k: list(v) for k, v in delta.volatile_fields_ignored.items()},
            "stability": measurement.records if measurement is not None else []}


def main(config_path, root, out):
    cfg = load_config(config_path)
    base = os.path.join(root, ".kriya", "worktree")
    cand = os.path.join(root, ".kriya", "worktrees", "candidate-reg-r1")
    started = time.time()
    replays = []

    def baseline_suite_run(target_test):          # workflow.py's baseline_suite_run, verbatim shape
        return PolymorphicValidator(base, original_workspace_path=base, autonomy_cfg=cfg.autonomy).run_tests(
            target_test=target_test)

    def replay(selection):
        replays.append(selection)
        return baseline_suite_run(selection)

    revision = compute_workspace_content_hash(base)
    baseline = capture_validation_baseline(
        workspace_revision=revision, run_id="reg-r1-context-replay",
        invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"),
        raw_result=baseline_suite_run(None))
    self_post = build_validation_outcome(baseline_suite_run(None))
    cand_post = build_validation_outcome(
        PolymorphicValidator(cand, original_workspace_path=base, autonomy_cfg=cfg.autonomy).run_tests())
    cache = {}
    common = {"baseline": baseline, "post_environment": None, "cache": cache, "replay": replay,
              "current_revision": lambda: compute_workspace_content_hash(base),
              "working_directory": os.path.realpath(base)}
    self_delta, self_measurement = classify_with_baseline_stability(post=self_post, **common)
    cand_delta, cand_measurement = classify_with_baseline_stability(post=cand_post, **common)
    entry = next(iter(cache.values()), None)
    report = {
        "base_revision": revision, "wall_seconds": round(time.time() - started, 1), "replay_selections": replays,
        "observations": {
            "original": summary(baseline.outcome.pytest_evidence),
            **({f"replay{i + 1}": summary(PytestSuiteEvidence.from_dict(r)) for i, r in enumerate(entry["replays"])}
               if entry else {}),
            "untouched_base_second_run": summary(self_post.pytest_evidence),
            "candidate": summary(cand_post.pytest_evidence),
        },
        "context_id": next(iter(cache), None),
        "untouched_base_vs_itself": decision(self_delta, self_measurement),
        "candidate_vs_baseline": decision(cand_delta, cand_measurement),
    }
    with open(os.path.join(out, "replay_result.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    brief = {k: report[k] for k in ("base_revision", "wall_seconds", "replay_selections", "observations", "context_id")}
    for name in ("untouched_base_vs_itself", "candidate_vs_baseline"):
        brief[name] = {k: v for k, v in report[name].items() if k != "stability"}
        brief[name]["stability"] = [{k: r.get(k) for k in ("test", "source", "reason", "outcome", "failure_type",
                                                           "message", "body")} for r in report[name]["stability"]]
    print(json.dumps(brief, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:4])
