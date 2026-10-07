"""REG-R2: re-score the exact frozen Arm-A candidate (run 20261007T071630-e21ca9b2, attempt 2, engine.py) on a fresh
clone of the frozen Graphify base - no model call. Bytes come from the sealed M1 content-addressed blob and must match
the staged digest; scoring is the POST-REG-R2 scorer's own ``score`` (reviewed acceptance suite, external evaluator -
executed, never read - and the 76 canonical regression tests).

usage: python rescore_arm_a_candidate.py <scripts_dir> <scratch_dir> <out_dir>
"""
import gzip
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

D = pathlib.Path.home() / "kriya-m1-live"
RUN_ID = "20261007T071630-e21ca9b2"
PATH = "graphify/extractors/engine.py"


def main(scripts, scratch, out):
    sys.path.insert(0, scripts)
    import evaluate_postreg2_candidates as scorer  # the fixed per-site parse

    trajectory = json.loads((D / "evidence/postreg/run-a/candidates/trajectory.json").read_text())
    [candidate] = [c for c in trajectory["candidates"] if c["files"]]
    digest = candidate["files"][PATH]
    raw = digest.split(":", 1)[-1]
    data = gzip.decompress((D / "state-postreg-a/attempt-evidence" / RUN_ID / "blobs" / raw[:2] / f"{raw}.gz").read_bytes())
    assert hashlib.sha256(data).hexdigest() == raw, "candidate bytes differ from the staged digest"
    root = pathlib.Path(scratch) / "arm-a-candidate"
    tree = root / "ws"
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    subprocess.run(["git", "-C", str(tree), "fetch", "-q", "--no-tags", str(scorer.SOURCE_WS), scorer.BASE], check=True)
    subprocess.run(["git", "-C", str(tree), "checkout", "-q", "--detach", scorer.BASE], check=True)
    shutil.copyfile(scorer.SUITE, root / "kriya_acceptance.py")
    (tree / PATH).write_bytes(data)
    out = pathlib.Path(out)
    (out / "arm_a_candidate.diff").write_text(subprocess.run(["git", "-C", str(tree), "diff"], capture_output=True,
                                                             text=True, check=True).stdout)
    result = {"run_id": RUN_ID, "attempt_seq": candidate["attempt_seq"], "path": PATH, "sha256": raw,
              **scorer.score(tree, "arm_a_candidate", out)}
    (out / "arm_a_candidate_score.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
