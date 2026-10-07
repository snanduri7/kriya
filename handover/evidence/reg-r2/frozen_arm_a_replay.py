"""REG-R2 deterministic replay of the POST-REG-R1 Arm-A regression decision from its SEALED observations (no model, no
test execution).

Run 20261007T071630-e21ca9b2 sealed every observation its full-regression decision used, as M1 gate.result records
with the raw pytest stdout/stderr/JUnit: seq 23 the baseline full suite, seq 93 the candidate's POST full suite, seq
94/95 the two same-context stability replays (in that order). Each is rebuilt through Kriya's own collector
(test_execution.prepare/observe/collect - the same code the gate ran) and checked against what the live decision
sealed (regression.decision seq 96: baseline_cases/post_cases evidence, and the per-test stability observations).
Only then is the decision re-made by the importing Kriya's classify_with_baseline_stability, its replays served from
the sealed replay observations. Run under the pre-fix revision this must reproduce the live verdict; under the fix it
is the REG-R2 verdict on exactly the evidence that produced the false negative.

usage (cwd neutral, PYTHONPATH=<kriya revision>): python frozen_arm_a_replay.py <state_dir> <run_id> <out.json>
"""
import json
import os
import sys
import tempfile

from kriya.core.attempt_evidence import reader
from kriya.tools import test_execution
from kriya.workflow import pytest_stability
from kriya.workflow.pytest_stability import classify_with_baseline_stability
from kriya.workflow.validation_baseline import (
    ValidationInvocation,
    build_validation_outcome,
    capture_validation_baseline,
    pytest_suite_evidence,
)

BASELINE, POST, REPLAYS, DECISION = 23, 93, (94, 95), 96
TIMING = "tests.test_ts_import_type_arguments::test_ts_normalizer_scales_linearly_on_large_files"
REVISION = "sealed-arm-a-base"


def rebuild(run, record):
    """The run_tests result of one sealed full-suite gate, through the production collector."""
    blob = {name: run.blob(digest) for name, digest in record["blobs"].items()}
    with tempfile.TemporaryDirectory() as workspace:
        binding = test_execution.prepare(workspace, "pytest")
        with open(os.path.join(workspace, binding.pytest_report), "wb") as handle:
            handle.write(blob["pytest_junit"])
        stdout, stderr = blob["pytest_stdout"].decode(), blob["pytest_stderr"].decode()
        success = bool(record["payload"]["success"])
        binding.observe({"returncode": 0 if success else 1, "stdout": stdout, "stderr": stderr})
        report = test_execution.collect(binding)
    return {"success": success, "output": stdout + "\n" + stderr, "pytest_evidence": report.pytest_evidence()}


def main(state_dir, run_id, out):
    run = reader.open_run(state_dir, run_id)
    verification = run.verify()
    assert verification.status == reader.VERIFIED and verification.sealed, verification
    records = {r["seq"]: r for r in run.records()}
    assert all(records[s]["kind"] == "gate.result" for s in (BASELINE, POST, *REPLAYS))
    sealed = records[DECISION]
    assert sealed["kind"] == "regression.decision" and sealed["payload"]["scope"] == "full_regression"
    sealed_blobs = {name: json.loads(run.blob(digest)) for name, digest in sealed["blobs"].items()}

    raw = {seq: rebuild(run, records[seq]) for seq in (BASELINE, POST, *REPLAYS)}
    evidence = {seq: pytest_suite_evidence(result) for seq, result in raw.items()}
    # faithfulness: the rebuilt evidence is exactly what the live decision judged
    assert evidence[BASELINE].to_dict() == sealed_blobs["baseline_cases"], "baseline evidence differs from sealed"
    assert evidence[POST].to_dict() == sealed_blobs["post_cases"], "POST evidence differs from sealed"
    [sealed_timing] = [r for r in sealed_blobs["stability"] if r["test"] == TIMING]
    rebuilt_observations = [evidence[s].by_key()[TIMING].identity() for s in (BASELINE, *REPLAYS)]
    assert rebuilt_observations == sealed_timing["observations"], "replay observations differ from sealed"

    served = []

    def replay(selection):
        assert selection is None, "the live baseline is a full-suite baseline"
        served.append(REPLAYS[len(served)])
        return raw[served[-1]]

    baseline = capture_validation_baseline(
        workspace_revision=REVISION, run_id=run_id,
        invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"), raw_result=raw[BASELINE])
    delta, measurement = classify_with_baseline_stability(
        baseline=baseline, post=build_validation_outcome(raw[POST]), post_environment=None, cache={},
        replay=replay, current_revision=lambda: REVISION, working_directory="/sealed/arm-a/base")
    timing_shown = next((k for k in delta.level2 if k.endswith("test_ts_normalizer_scales_linearly_on_large_files")),
                        None)
    counts = {}
    for classification in delta.level2.values():
        counts[classification.value] = counts.get(classification.value, 0) + 1
    report = {
        "kriya": os.path.dirname(os.path.dirname(pytest_stability.__file__)),
        "stability_policy": pytest_stability.STABILITY_POLICY_VERSION,
        "run_id": run_id, "m1": {"status": verification.status, "sealed": verification.sealed},
        "faithful_to_sealed": {"baseline_cases": True, "post_cases": True, "timing_observations": True},
        "replays_served": served,
        "timing_test_observations": {name: {"outcome": evidence[s].by_key()[TIMING].outcome,
                                            "failure_type": evidence[s].by_key()[TIMING].failure_type,
                                            "message": evidence[s].by_key()[TIMING].message_digest}
                                     for name, s in (("baseline", BASELINE), ("replay1", REPLAYS[0]),
                                                     ("replay2", REPLAYS[1]), ("candidate", POST))},
        "live_sealed_verdict": {"blocking": sealed["payload"]["blocking"],
                                "blocking_reasons": sealed["payload"]["blocking_reasons"]},
        "replayed_verdict": {"authority": delta.authority, "blocking": delta.blocking,
                             "blocking_reasons": list(delta.blocking_reasons),
                             "diagnostic_level1": delta.level1.classification.value, "level2_counts": counts,
                             "timing_test": delta.level2[timing_shown].value if timing_shown else None,
                             "non_pre_existing": {k: v.value for k, v in delta.level2.items()
                                                  if v.value != "PRE_EXISTING_FAILURE"}},
        "stability_records": measurement.records if measurement is not None else [],
    }
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=str)
    print(json.dumps({k: v for k, v in report.items() if k != "stability_records"}, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:4])
