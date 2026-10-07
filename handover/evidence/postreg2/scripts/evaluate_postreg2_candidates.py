"""POST-REG-R2 comparison: independent post-freeze evaluation of every staged candidate of one arm (no model call).

Runs ONLY after the arm's terminal freeze and M1 seal (refuses otherwise). Reads the sealed M1 store through Kriya's
reader; each staged candidate (the candidate.change records of one attempt, in record order) is rebuilt byte-exact
from its content-addressed blobs on a fresh scratch clone of the frozen base and scored with the reviewed acceptance
suite, the external Graphify evaluator and the canonical regression tests. The evaluator is executed, never read.
Results are written to evidence only and never reach a model or the run.

usage: <venv-postreg2 python> evaluate_postreg2_candidates.py <a|b> <scratch_dir>
"""
import gzip
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

from kriya.core.attempt_evidence import reader

D = pathlib.Path.home() / "kriya-m1-live"
SOURCE_WS = pathlib.Path.home() / "kriya-live-validation/graphify-canonical-853aa43/workspace"
BASE = "67f99bd0059dd1bac9e44382907ef9f10098b39f"
V = pathlib.Path.home() / "kriya-live-validation/val001-g1-graphify-c3406"
PY = V / "g1_venv/bin/python3"
SUITE = pathlib.Path.home() / "kriya-wt/p3d/handover/evidence/gr0/graphify_prospective/kriya_acceptance_graphify.py"


def run(cmd, cwd, env=None):
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})
    return proc.returncode, proc.stdout + proc.stderr


def score(tree, label, out):
    acceptance_code, acceptance = run([str(PY), "-m", "pytest", "-p", "no:cacheprovider", "-q", "--noconftest",
                                       "-W", "ignore::pytest.PytestUnknownMarkWarning", "kriya_acceptance.py"],
                                      tree.parent, env={"PYTHONPATH": str(tree)})
    external_code, external = run([str(PY), str(V / "g1_evidence/acceptance/check_acceptance.py"), "--repo", str(tree)],
                                  tree.parent)
    regression_code, regression = run([str(PY), "-m", "pytest", "-p", "no:cacheprovider", "-q",
                                       "tests/test_csharp_type_resolution.py", "tests/test_csharp_member_calls.py"], tree)
    for name, text in (("acceptance", acceptance), ("external", external), ("regression76", regression)):
        (out / f"{label}.{name}.txt").write_text(text)
    passed = re.search(r"(\d+) passed", acceptance.splitlines()[-1] if acceptance.strip() else "")
    ext = re.search(r"(\d+)/5 expected calls edges present", external)
    per_site = {site: status for status, site in re.findall(r"\[(PASS|FAIL)\] (\w)\(\)", external)}
    return {"acceptance_passed": int(passed.group(1)) if passed else 0, "acceptance_exit": acceptance_code,
            "external_score": int(ext.group(1)) if ext else None, "external_exit": external_code,
            "external_sites": per_site,
            "regression76": (regression.strip().splitlines() or [""])[-1], "regression_exit": regression_code}


def main(arm, scratch):
    name = f"postreg2-{arm}"
    out = D / "evidence/postreg2" / f"run-{arm}"
    assert (out / "frozen" / "FROZEN").exists(), "freeze the arm first"
    run_id = (out / "run_id.txt").read_text().strip()
    state = D / f"state-{name}"
    store = reader.open_run(str(state), run_id)
    verification = store.verify()
    assert verification.status == reader.VERIFIED and verification.sealed, verification
    # A later work unit starts from the units already committed: carry forward the last staged candidate of every
    # unit that closed with its quality gates passed.
    candidates, current, carried, unit_last = [], None, {}, None
    for record in store.records():
        payload = record["payload"]
        if record["kind"] == "unit.opened":
            unit_last = None
        elif record["kind"] == "unit.closed":
            if payload.get("quality_gates_passed") and unit_last is not None:
                carried.update(unit_last["files"])
        elif record["kind"] == "attempt.opened":
            current = {"attempt_seq": record["seq"], "unit": record.get("unit_id"), "carried": dict(carried),
                       "files": {}, "decisions": []}
            candidates.append(current)
        elif record["kind"] == "attempt.closed" and current is not None and current["files"]:
            unit_last = current
        elif record["kind"] == "candidate.change" and current is not None:
            current["decisions"].append(payload.get("decision"))
            if payload.get("decision") == "STAGED":
                current["files"][payload["path"]] = None if payload.get("deleted") else payload.get("after_digest")
    staged = [c for c in candidates if c["files"]]
    results, best = [], None
    evaluation = out / "candidates"
    evaluation.mkdir(exist_ok=True)
    scratch = pathlib.Path(scratch)
    for index, candidate in enumerate(staged, 1):
        root = scratch / f"cand{index}"
        tree = root / "ws"
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        subprocess.run(["git", "-C", str(tree), "fetch", "-q", "--no-tags", str(SOURCE_WS), BASE], check=True)
        subprocess.run(["git", "-C", str(tree), "checkout", "-q", "--detach", BASE], check=True)
        shutil.copyfile(SUITE, root / "kriya_acceptance.py")
        for path, digest in {**candidate["carried"], **candidate["files"]}.items():
            target = tree / path
            if digest is None:
                target.unlink(missing_ok=True)
                continue
            raw = digest.split(":", 1)[-1]
            blob = state / "attempt-evidence" / run_id / "blobs" / raw[:2] / f"{raw}.gz"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.decompress(blob.read_bytes()))
        diff = subprocess.run(["git", "-C", str(tree), "diff"], capture_output=True, text=True, check=True).stdout
        (evaluation / f"candidate{index}.diff").write_text(diff)
        result = {"candidate": index, "attempt_seq": candidate["attempt_seq"], "unit": candidate["unit"],
                  "carried_from_committed_units": sorted(candidate["carried"]), "files": candidate["files"],
                  **score(tree, f"candidate{index}", evaluation)}
        best = max(best or 0, result["external_score"] or 0)
        result["best_external_so_far"] = best
        results.append(result)
        print(json.dumps({k: result[k] for k in ("candidate", "files", "acceptance_passed", "external_score",
                                                 "external_sites", "regression76", "best_external_so_far")}), flush=True)
    summary = {"run_id": run_id, "m1": {"status": verification.status, "sealed": verification.sealed},
               "attempts": len(candidates), "stageable_candidates": len(staged), "candidates": results,
               "best_external": best}
    (evaluation / "trajectory.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "candidates"}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
