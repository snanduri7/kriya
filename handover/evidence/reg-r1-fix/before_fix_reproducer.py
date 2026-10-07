"""REG-R1 BEFORE measurement on the unmodified product: two runs of the UNTOUCHED generic reproducer project through
the production test gate (PolymorphicValidator.run_tests) and the production comparator (classify_baseline_delta).

usage: PYTHONPATH=<worktree> python before_fix_reproducer.py <out.json>
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path.cwd() / "tests"))
from _reg_r1_fixture import validator_for, write_project  # noqa: E402

from kriya.workflow.validation_baseline import (  # noqa: E402
    ValidationBaseline,
    ValidationInvocation,
    build_validation_outcome,
    classify_baseline_delta,
)


def main(out):
    with tempfile.TemporaryDirectory() as scratch:
        root = write_project(pathlib.Path(scratch))
        first = validator_for(root).run_tests()
        second = validator_for(root).run_tests()
    pre, post = build_validation_outcome(first), build_validation_outcome(second)
    baseline = ValidationBaseline(workspace_revision="r", run_id="before", captured_at=0.0, outcome=pre,
                                  invocation=ValidationInvocation(command_identity="c", selection_identity="full_suite"))
    delta = classify_baseline_delta(baseline, post)
    report = {"level1": delta.level1.classification.value, "level2": {k: v.value for k, v in delta.level2.items()},
              "blocking": delta.blocking, "blocking_reasons": list(delta.blocking_reasons),
              "outputs_identical": first["output"] == second["output"]}
    pathlib.Path(out).write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
