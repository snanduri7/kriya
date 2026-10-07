"""Graphify E2E milestone: deterministic replay of the successful Kriya + qwen3.8 run 20261007T093122-df65c5d3 against
the INTEGRATED Kriya revision (imported from PYTHONPATH). No model call, no generation.

1. Rebuild the run's final applied candidate (every STAGED file, last write wins) from the sealed M1 content-addressed
   blobs onto a fresh clone of the frozen Graphify base; score it with the POST-REG-R2 scorer (reviewed acceptance,
   external evaluator - executed, never read - and the 76 canonical regression tests).
2. Re-decide every sealed full-regression decision with the integrated classify_with_baseline_stability, from the
   sealed observations (baseline / candidate POST / the same-context replays, rebuilt through the production collector
   and asserted equal to what the live decision sealed), and compare with the live verdict.

usage: python milestone_replay.py <scripts_dir> <scratch_dir> <out.json>
"""
import gzip
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
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

D = pathlib.Path.home() / "kriya-m1-live"
RUN_ID = "20261007T093122-df65c5d3"
STATE = D / "state-postreg2-b"


def rebuild(run, record):
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


def regression_replays(run):
    records = list(run.records())
    tests = [r for r in records if r["kind"] == "gate.result" and r["payload"].get("gate") == "tests"
             and "pytest_junit" in (r.get("blobs") or {})]
    results = []
    for decision in (r for r in records if r["kind"] == "regression.decision"):
        sealed = {name: json.loads(run.blob(d)) for name, d in decision["blobs"].items()}
        built = {r["seq"]: rebuild(run, r) for r in tests if r["seq"] < decision["seq"]}
        evid = {seq: pytest_suite_evidence(raw).to_dict() for seq, raw in built.items()}
        [base_seq] = [s for s, e in evid.items() if e == sealed["baseline_cases"]][:1]
        post_seq = max(s for s, e in evid.items() if e == sealed["post_cases"])
        replay_seqs = [s for s in sorted(built) if post_seq < s < decision["seq"]]
        disputed = [r for r in sealed["stability"] if r.get("observations")]
        for record in disputed:   # faithfulness of the replay observations
            got = [pytest_suite_evidence(built[s]).by_key()[record["test"]].identity() for s in (base_seq, *replay_seqs)]
            assert got == record["observations"], (record["test"], got, record["observations"])
        served = []

        def replay(selection, _seqs=replay_seqs, _served=served, _built=built):
            assert selection is None
            _served.append(_seqs[len(_served)])
            return _built[_served[-1]]

        baseline = capture_validation_baseline(
            workspace_revision="sealed", run_id=RUN_ID,
            invocation=ValidationInvocation("polymorphic_validator.run_tests", "full_suite"), raw_result=built[base_seq])
        delta, measurement = classify_with_baseline_stability(
            baseline=baseline, post=build_validation_outcome(built[post_seq]), post_environment=None, cache={},
            replay=replay, current_revision=lambda: "sealed", working_directory="/sealed")
        counts = {}
        for c in delta.level2.values():
            counts[c.value] = counts.get(c.value, 0) + 1
        live = json.loads(run.blob(decision["blobs"]["comparison"]))["level2"]
        results.append({
            "decision_seq": decision["seq"], "baseline_seq": base_seq, "post_seq": post_seq, "replay_seqs": replay_seqs,
            "live_blocking": decision["payload"]["blocking"], "replayed_blocking": delta.blocking,
            "replayed_reasons": list(delta.blocking_reasons), "level2_counts": counts,
            "non_pre_existing": {k: v.value for k, v in delta.level2.items() if v.value != "PRE_EXISTING_FAILURE"},
            "level2_identical_to_live": {k: v.value for k, v in delta.level2.items()} == live,
            "flaky_preexisting": pytest_stability.decision_summary(delta, measurement)["flaky_preexisting"],
        })
    assert results, 'no regression.decision record found - refusing a vacuous pass'
    return results


def final_candidate(run):
    files = {}
    for r in run.records():
        p = r["payload"]
        if r["kind"] == "candidate.change" and p.get("decision") == "STAGED":
            files[p["path"]] = None if p.get("deleted") else p.get("after_digest")
    return files


def main(scripts, scratch, out):
    sys.path.insert(0, scripts)
    import evaluate_postreg2_candidates as scorer

    run = reader.open_run(str(STATE), RUN_ID)
    verification = run.verify()
    assert verification.status == reader.VERIFIED and verification.sealed
    files = final_candidate(run)
    root = pathlib.Path(scratch) / "milestone-candidate"
    tree = root / "ws"
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    subprocess.run(["git", "-C", str(tree), "fetch", "-q", "--no-tags", str(scorer.SOURCE_WS), scorer.BASE], check=True)
    subprocess.run(["git", "-C", str(tree), "checkout", "-q", "--detach", scorer.BASE], check=True)
    shutil.copyfile(scorer.SUITE, root / "kriya_acceptance.py")
    digests = {}
    for path, digest in files.items():
        raw = digest.split(":", 1)[-1]
        data = gzip.decompress((STATE / "attempt-evidence" / RUN_ID / "blobs" / raw[:2] / f"{raw}.gz").read_bytes())
        assert hashlib.sha256(data).hexdigest() == raw
        (tree / path).parent.mkdir(parents=True, exist_ok=True)
        (tree / path).write_bytes(data)
        digests[path] = raw
    applied = D / "ws-postreg2-b" / "gr1-graphify"
    same_as_applied = all((applied / p).read_bytes() == (tree / p).read_bytes() for p in files)
    outdir = pathlib.Path(out).parent
    score = scorer.score(tree, "milestone_candidate", outdir)
    report = {"kriya": os.path.dirname(os.path.dirname(pytest_stability.__file__)),
              "stability_policy": pytest_stability.STABILITY_POLICY_VERSION, "run_id": RUN_ID,
              "m1": {"status": verification.status, "sealed": verification.sealed},
              "candidate_files": digests, "identical_to_frozen_applied_workspace": same_as_applied,
              "score": score, "regression_decisions": regression_replays(run)}
    pathlib.Path(out).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:4])
