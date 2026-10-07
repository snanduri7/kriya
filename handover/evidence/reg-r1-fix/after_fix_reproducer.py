"""REG-R1 AFTER measurement: the same two runs of the UNTOUCHED generic reproducer project, decided by the production
stability-aware entry point (pytest_stability.classify_with_baseline_stability) with REAL untouched-baseline replays.

usage: PYTHONPATH=<worktree> python after_fix_reproducer.py <out.json>   (cwd = worktree)
"""
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path.cwd() / "tests"))
from _reg_r1_fixture import validator_for, write_project  # noqa: E402

from kriya.workflow.pytest_stability import classify_with_baseline_stability  # noqa: E402
from kriya.workflow.validation_baseline import (  # noqa: E402
    ValidationBaseline,
    ValidationInvocation,
    build_validation_outcome,
)


def main(out):
    with tempfile.TemporaryDirectory() as scratch:
        root = write_project(pathlib.Path(scratch) / "pristine")
        first = validator_for(root).run_tests()
        second = validator_for(root).run_tests()
        replays = []

        def replay(node_ids):
            copy = pathlib.Path(scratch) / f"replay{len(replays)}"
            shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".kriya", "__pycache__", ".pytest_cache"))
            replays.append(list(node_ids))
            return validator_for(copy).run_tests(target_test=list(node_ids))

        baseline = ValidationBaseline(workspace_revision="r", run_id="after", captured_at=0.0,
                                      outcome=build_validation_outcome(first),
                                      invocation=ValidationInvocation(command_identity="c", selection_identity="full_suite"))
        delta, measurement = classify_with_baseline_stability(
            baseline=baseline, post=build_validation_outcome(second), post_environment=None, cache={},
            replay=replay, current_revision=lambda: "r")
    report = {"authority": delta.authority, "diagnostic_level1": delta.level1.classification.value,
              "level2": {k: v.value for k, v in delta.level2.items()}, "blocking": delta.blocking,
              "blocking_reasons": list(delta.blocking_reasons),
              "volatile_fields_ignored": {k: list(v) for k, v in delta.volatile_fields_ignored.items()},
              "replays": replays, "stability": measurement.records if measurement else None}
    pathlib.Path(out).write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("authority", "diagnostic_level1", "level2", "blocking",
                                             "blocking_reasons", "volatile_fields_ignored", "replays")}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
