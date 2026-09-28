#!/usr/bin/env python3
"""PRD-036 canary evidence: resource snapshots and the verdict.

  prd036_canary.py snapshot WORKSPACE OUT.json
  prd036_canary.py verdict EVIDENCE_DIR WORKSPACE --expect-changed PATH [--expect-changed PATH ...]

``snapshot`` records what a run could leak: the Docker containers and
networks, the workspace's git worktrees, whether the workspace run lock is
free, processes whose command line names the workspace, and the tracked
workspace diff. ``verdict`` reads the run's own output
(EVIDENCE_DIR/stdout.json and exit_code), the RunRecord, both snapshots and
the independent re-verification results, and writes canary.json. Each check
either passes or fails, and the canary is PASS only if every check passes.
It never runs Kriya itself (scripts/prd036_canary.sh does).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kriya.control.commit_state import assess_workspace_commit_state  # noqa: E402 - after sys.path
from kriya.control.persistence import run_record_path  # noqa: E402 - after sys.path
from kriya.control.run_ownership import WorkspaceLockHeldError, acquire_run_lock  # noqa: E402 - after sys.path

STATIC_ANALYSIS_EVIDENCE_PREFIX = "static_analysis:"


def _lines(argv: List[str], cwd: str = None) -> List[str]:
    completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {completed.stderr.strip()}")
    return sorted(line for line in completed.stdout.splitlines() if line.strip())


def _lock_free(workspace: str) -> bool:
    if not os.path.exists(os.path.join(workspace, ".kriya", "run.lock")):
        return True
    try:
        with acquire_run_lock(workspace):
            return True
    except WorkspaceLockHeldError:
        return False


def snapshot(workspace: str) -> Dict[str, Any]:
    workspace = os.path.realpath(workspace)
    own = {str(os.getpid()), str(os.getppid())}
    processes = [line for line in _lines(["ps", "-axo", "pid=,command="])
                 if workspace in line and line.split(None, 1)[0] not in own and "prd036_canary" not in line]
    return {
        "containers": _lines(["docker", "ps", "-aq", "--no-trunc"]),
        "networks": _lines(["docker", "network", "ls", "-q", "--no-trunc"]),
        "worktrees": [line for line in _lines(["git", "worktree", "list", "--porcelain"], cwd=workspace)
                      if line.startswith("worktree ")],
        "lock_free": _lock_free(workspace),
        "processes": processes,
        "tracked_changes": _lines(["git", "status", "--porcelain", "--untracked-files=no"], cwd=workspace),
        "head": _lines(["git", "rev-parse", "HEAD"], cwd=workspace),
    }


def _read(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _result(evidence: str) -> Dict[str, Any]:
    with open(os.path.join(evidence, "stdout.json"), "r", encoding="utf-8") as handle:
        raw = handle.read()
    result = json.loads(raw[raw.index("{"):])
    return result.get("result", result) if isinstance(result, dict) else {}


def verdict(evidence: str, workspace: str, expect_changed: List[str]) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}

    def check(name: str, passed: bool, **detail: Any) -> None:
        checks[name] = {"passed": bool(passed), **detail}

    with open(os.path.join(evidence, "exit_code"), "r", encoding="utf-8") as handle:
        exit_code = int(handle.read().strip())
    result = _result(evidence)
    check("cli_exit_zero", exit_code == 0, exit_code=exit_code)
    check("run_success", result.get("status") == "success" and result.get("quality_gates_passed") is True,
          status=result.get("status"), quality_gates_passed=result.get("quality_gates_passed"),
          failure_category=result.get("failure_category"))
    subtasks = result.get("subtask_results") or []
    check("enforce_controller_exercised", bool(subtasks) and all(s.get("status") == "completed" for s in subtasks),
          subtasks=[{k: s.get(k) for k in ("subtask_id", "status")} for s in subtasks])
    commit = result.get("commit_evidence") or {}
    check("commit_committed", commit.get("state") == "committed",
          transaction_id=commit.get("transaction_id"), state=commit.get("state"))

    run_id = result.get("run_id") or ""
    record_file = run_record_path(workspace, run_id) if run_id else ""
    record = _read(record_file) if record_file and os.path.exists(record_file) else {}
    evidence_ids = record.get("verification_evidence_ids") or []
    check("run_record_success", record.get("lifecycle_state") == "SUCCESS" and bool(record.get("commits")),
          run_id=run_id, lifecycle_state=record.get("lifecycle_state"), commits=len(record.get("commits") or []))
    check("static_analysis_evidence_bound",
          any(str(item).startswith(STATIC_ANALYSIS_EVIDENCE_PREFIX) for item in evidence_ids),
          verification_evidence_ids=evidence_ids)
    assessment = assess_workspace_commit_state(workspace)
    check("no_uncertain_commit_state", assessment.safe, reason_codes=assessment.reason_codes)

    before, after = _read(os.path.join(evidence, "before.json")), _read(os.path.join(evidence, "after.json"))
    for key in ("containers", "networks", "worktrees", "processes"):
        leaked = sorted(set(after[key]) - set(before[key]))
        check(f"no_leaked_{key}", not leaked, leaked=leaked)
    check("workspace_lock_released", after["lock_free"] is True)
    check("head_unchanged", before["head"] == after["head"], before=before["head"], after=after["head"])
    changed = sorted(line[3:] for line in after["tracked_changes"])
    check("only_authorized_workspace_writes", not before["tracked_changes"] and changed == sorted(expect_changed),
          changed=changed, expected=sorted(expect_changed))

    for name in ("post-run-compile", "post-run-test"):
        path = os.path.join(evidence, f"{name}.exit")
        code = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                code = int(handle.read().strip())
        check(f"independent_{name.replace('-', '_')}", code == 0, exit_code=code)
    candidate = _read(os.path.join(evidence, "release-candidate-check.json"))
    check("release_candidate_current", candidate.get("status") == "CURRENT", changes=candidate.get("changes"))
    # Before the run, and after it (the run builds the code index, so the
    # context-recall certification becomes required).
    for name in ("doctor-before", "doctor-after"):
        doctor = _read(os.path.join(evidence, f"{name}.json"))
        check(f"production_ready_{name.split('-')[1]}", doctor.get("production_ready") is True,
              blocking=[c["id"] for c in doctor.get("checks", [])
                        if c.get("required") and c.get("status") in ("FAIL", "UNAVAILABLE")])

    report = {"verdict": "PASS" if all(c["passed"] for c in checks.values()) else "FAIL",
              "run_id": run_id, "checks": checks}
    with open(os.path.join(evidence, "canary.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("workspace")
    snap.add_argument("out")
    judge = sub.add_parser("verdict")
    judge.add_argument("evidence")
    judge.add_argument("workspace")
    judge.add_argument("--expect-changed", action="append", required=True)
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(snapshot(args.workspace), handle, indent=2, sort_keys=True)
        return 0
    report = verdict(args.evidence, os.path.realpath(args.workspace), args.expect_changed)
    failed = [name for name, c in report["checks"].items() if not c["passed"]]
    print(f"[canary] {report['verdict']}" + (f": failed {', '.join(failed)}" if failed else ""))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
