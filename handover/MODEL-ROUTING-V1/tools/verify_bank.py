#!/usr/bin/env python3
"""Package-integrity verification of the frozen MODEL-ROUTING-V1 bank (experiment-only, no model calls, read-only).

For every task in bank/final_ids.json: required files present; every digest recorded in task.json recomputed from the files on disk;
solver workspace is a single-commit clean git tree with no remote whose content equals the candidate base tree; certification verdict
is CERTIFIED or CERTIFIED-WITH-LIMITS and the 3 reference runs were stable. Bank level: the set of packages equals final_ids.json,
vendored evaluator tools are byte-identical to the repo tools, no package is REJECTED. Writes bank/frozen/INTEGRITY.json; exit 1 on any failure.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeze_bank as fb  # noqa: E402

BANK, FROZEN, TOOLS = fb.BANK, fb.FROZEN, fb.TOOLS
REQUIRED = ["task.json", "goal.txt", "task_source.json", "oracle/accept.sh", "oracle/regress.sh", "oracle/spec.json", "oracle/meta.json",
            "oracle/regression_set.json", "evaluator/cert.json", "evaluator/ref_prod.patch", "evaluator/ref_test.patch", "evaluator/probe.json"]
OK_VERDICTS = ("CERTIFIED", "CERTIFIED-WITH-LIMITS")


def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True, check=False).stdout


def check(tid):
    d, bad = FROZEN / tid, []
    for rel in REQUIRED + ["oracle/hidden", "ws", "evaluator/ref_tree"]:
        if not (d / rel).exists():
            bad.append(f"missing {rel}")
    if bad:
        return bad
    pkg = json.loads((d / "task.json").read_text())
    src = json.loads((d / "task_source.json").read_text())
    pairs = {
        "goal_digest": (pkg["goal_digest"], fb.sha_bytes((d / "goal.txt").read_bytes())),
        "accept script": (pkg["accept_oracle"]["digest"], fb.sha_file(d / "oracle/accept.sh")),
        "spec": (pkg["accept_oracle"]["spec_digest"], fb.sha_file(d / "oracle/spec.json")),
        "hidden tests": (pkg["accept_oracle"]["hidden_tests_digest"], fb.sha_tree(d / "oracle/hidden")),
        "regress script": (pkg["regression_oracle"]["digest"], fb.sha_file(d / "oracle/regress.sh")),
        "regression set": (pkg["regression_oracle"]["set_digest"], fb.sha_file(d / "oracle/regression_set.json")),
        "meta": (pkg["oracle_meta_digest"], fb.sha_file(d / "oracle/meta.json")),
        "ref patch": (pkg["reference_fix"]["patch_digest"], fb.sha_file(d / "evaluator/ref_prod.patch")),
        "ref test patch": (pkg["reference_fix"]["test_patch_digest"], fb.sha_file(d / "evaluator/ref_test.patch")),
        "ref tree": (pkg["reference_fix"]["ref_tree_digest"], fb.sha_tree(d / "evaluator/ref_tree")),
        "ws tree": (pkg["provenance"]["ws_tree_digest"], fb.sha_tree(d / "ws")),
    }
    cand = BANK / "tasks" / src["candidate_id"].replace("#", "_") / "base_tree"
    if cand.exists():
        pairs["ws == candidate base tree"] = (fb.sha_tree(d / "ws"), fb.sha_tree(cand))
    bad += [f"digest mismatch: {k}" for k, (a, b) in pairs.items() if a != b]
    ws = d / "ws"
    if len(git(ws, "rev-list", "--all").split()) != 1:
        bad.append("ws git is not a single commit")
    if git(ws, "status", "--porcelain").strip():
        bad.append("ws worktree not clean")
    if git(ws, "remote").strip():
        bad.append("ws git has a remote")
    cert = pkg["certification"]
    if cert["verdict"] not in OK_VERDICTS:
        bad.append(f"certification verdict {cert['verdict']}")
    if not cert["known_good_stable"]:
        bad.append("reference not stable across repeats")
    spec = json.loads((d / "oracle/spec.json").read_text())
    if pkg["accept_oracle"]["required_tests"] != len(spec["accept_set"]):
        bad.append("required_tests != len(accept_set)")
    for kind in ("accept", "regress"):
        if not (d / "oracle" / f"{kind}.sh").stat().st_mode & 0o111:
            bad.append(f"{kind}.sh not executable")
    return bad


def main():
    ids = json.loads((BANK / "final_ids.json").read_text())
    res = {tid: check(tid) for tid in sorted(ids)}
    present = sorted(p.name for p in FROZEN.iterdir() if p.name.startswith("MR-"))
    bank_bad = []
    if present != sorted(ids):
        bank_bad.append(f"frozen packages {present} != final_ids {sorted(ids)}")
    for name in ("bank_tool.py", "bank_eval.py", "oracle_preflight.py"):
        if fb.sha_file(TOOLS / name) != fb.sha_file(FROZEN / "_tools" / name):
            bank_bad.append(f"vendored {name} differs from repo tool")
    if len(ids) != 20:
        bank_bad.append(f"{len(ids)} tasks, expected 20")
    langs = {}
    for tid in ids:
        pk = json.loads((FROZEN / tid / "task.json").read_text())
        langs[pk["language"]] = langs.get(pk["language"], 0) + 1
    if langs != {"java": 10, "python": 10}:
        bank_bad.append(f"language balance {langs}")
    out = {"tasks": {t: ("PASS" if not b else b) for t, b in res.items()}, "bank": bank_bad or "PASS", "languages": langs,
           "vendored_tool_sha256": {n: fb.sha_file(FROZEN / "_tools" / n) for n in ("bank_tool.py", "bank_eval.py", "oracle_preflight.py")}}
    (FROZEN / "INTEGRITY.json").write_text(json.dumps(out, indent=1))
    failed = [t for t, b in res.items() if b] + (["BANK"] if bank_bad else [])
    print(json.dumps(out, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
