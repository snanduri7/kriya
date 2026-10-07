"""POST-REG-R2 comparison: deterministic REG-R1/REG-R2 smoke gate for one arm (no model call).

The untouched Graphify base's full-suite gate (the baseline), a second same-context observation of it (POST), and the
production decision (pytest_stability.classify_with_baseline_stability with the baseline's own gate call as replay),
all under the arm's executable, config and runtime. Expected: NO REGRESSION (whole-output difference diagnostic).

usage (cwd = the arm's SEC-009-approved workspace, arm env sourced): python postreg2_smoke.py <config> <root> <out.json>
"""
import json
import os
import sys
import time

from kriya.build_info import version_report
from kriya.config.config import load_config
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.pytest_stability import classify_with_baseline_stability
from kriya.workflow.validation_baseline import (
    ValidationInvocation,
    build_validation_outcome,
    capture_validation_baseline,
)


def main(config_path, root, out):
    cfg = load_config(config_path)
    base = os.path.join(root, ".kriya", "worktree")
    started = time.time()
    selections = []

    def baseline_suite_run(target_test):
        return PolymorphicValidator(base, original_workspace_path=base, autonomy_cfg=cfg.autonomy).run_tests(
            target_test=target_test)

    def replay(selection):
        selections.append(selection)
        return baseline_suite_run(selection)

    revision = compute_workspace_content_hash(base)
    baseline = capture_validation_baseline(
        workspace_revision=revision, run_id="postreg2-smoke",
        invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"), raw_result=baseline_suite_run(None))
    post = build_validation_outcome(baseline_suite_run(None))
    delta, measurement = classify_with_baseline_stability(
        baseline=baseline, post=post, post_environment=None, cache={}, replay=replay,
        current_revision=lambda: compute_workspace_content_hash(base), working_directory=os.path.realpath(base))
    report = {"kriya": version_report(), "base_revision": revision, "wall_seconds": round(time.time() - started, 1),
              "baseline_complete": baseline.outcome.pytest_evidence.complete if baseline.outcome.pytest_evidence else None,
              "post_complete": post.pytest_evidence.complete if post.pytest_evidence else None,
              "authority": delta.authority, "blocking": delta.blocking, "blocking_reasons": list(delta.blocking_reasons),
              "diagnostic_level1": delta.level1.classification.value,
              "level2_counts": {v: sum(1 for c in delta.level2.values() if c.value == v)
                                for v in sorted({c.value for c in delta.level2.values()})},
              "volatile_fields_ignored": {k: list(v) for k, v in delta.volatile_fields_ignored.items()},
              "replay_selections": selections,
              "unestablished_fields_ignored": {k: list(v) for k, v in delta.unestablished_fields_ignored.items()},
              "stability": [{k: r.get(k) for k in ("test", "disputed", "reason", "flaky", "envelope_digest", "envelope")}
                            for r in (measurement.records if measurement else [])],
              "verdict": "NO REGRESSION" if not delta.blocking else "BLOCKING"}
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    print(json.dumps({k: v for k, v in report.items() if k != "kriya"}, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:4])
