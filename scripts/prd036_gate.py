#!/usr/bin/env python3
"""PRD-036 final production release gate.

Reads the evidence and decides; it runs nothing live. VERIFIED only when
every criterion passes, each named with its evidence:

  prd036_gate.py --candidate FROZEN.json --certification CERT_DIR --canary CANARY_DIR \\
      --hosted HOSTED.json [--hosted HOSTED.json ...] --out GATE.json

- deterministic certification: certify.sh CERTIFIED at the candidate
  revision (release identity equal to the candidate's), 0 failed, 0 errors,
  0 unexpected skips, and the scanner tier executed;
- production doctor: PRODUCTION_READY=true on the release config, before and
  after the canary run;
- canary PASS on the same candidate;
- live streak: RELEASE_STREAK_REQUIRED consecutive complete matrices on the
  candidate, with every trial's evidence present and no contradiction (no
  passing trial with a failed case, and every trial on one candidate);
- hosted CI at the candidate revision: every job succeeded (or was skipped
  by design), including Linux production certification and the blocking
  Windows import/guard job;
- no open P0/P1 in the backlog registry;
- a clean tracked tree, where every change after the candidate is
  evidence-only (handover/, docs/, evidence/, *.md, *.csv).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kriya.core import model_certification as mc  # noqa: E402 - after sys.path

EVIDENCE_ONLY_PREFIXES = ("handover/", "docs/", "evidence/")
EVIDENCE_ONLY_SUFFIXES = (".md", ".csv")
EXECUTABLE_PREFIXES = ("kriya/", "plugins/", "scripts/", "tests/", ".github/")
EXECUTABLE_FILES = ("pyproject.toml", "requirements.lock", "uv.lock", "setup.py", "setup.cfg")
REQUIRED_HOSTED_JOBS = ("Production certification", "Platform import and architecture guard (Windows)")
BLOCKING_PRIORITIES = ("P0", "P1")


def evidence_only(paths: Sequence[str]) -> List[str]:
    """The paths that are NOT evidence-only (empty = the diff is evidence-only)."""
    offending = []
    for path in paths:
        executable = path.startswith(EXECUTABLE_PREFIXES) or path in EXECUTABLE_FILES
        evidence = path.startswith(EVIDENCE_ONLY_PREFIXES) or path.endswith(EVIDENCE_ONLY_SUFFIXES)
        if executable or not evidence:
            offending.append(path)
    return offending


def open_blocking_items(rows: Sequence[Mapping[str, str]]) -> List[str]:
    return [row["id"] for row in rows
            if row.get("priority") in BLOCKING_PRIORITIES and row.get("status") != "CLOSED"]


def evaluate(inputs: Mapping[str, Any]) -> Dict[str, Any]:
    """The pure decision over collected evidence (see collect())."""
    criteria: Dict[str, Dict[str, Any]] = {}

    def criterion(name: str, passed: bool, **detail: Any) -> None:
        criteria[name] = {"passed": bool(passed), **detail}

    candidate = inputs["candidate"]
    revision = ((candidate.get("release") or {}).get("kriya") or {}).get("revision")
    cert = inputs["certification"]
    tiers = cert.get("tiers") or {}
    pytest_tier, scanner_tier = tiers.get("pytest") or {}, tiers.get("scanner") or {}
    criterion("deterministic_certification",
              cert.get("status") == "CERTIFIED" and not cert.get("problems")
              and (pytest_tier.get("junit") or {}).get("failures") == 0
              and (pytest_tier.get("junit") or {}).get("errors") == 0
              and pytest_tier.get("unexpected_skips") == 0 and (pytest_tier.get("junit") or {}).get("passed", 0) > 0,
              status=cert.get("status"), pytest=pytest_tier, problems=cert.get("problems"))
    criterion("scanner_executed",
              (scanner_tier.get("junit") or {}).get("passed", 0) > 0
              and (scanner_tier.get("junit") or {}).get("failures") == 0
              and (scanner_tier.get("junit") or {}).get("errors") == 0,
              scanner=scanner_tier)
    criterion("release_stage", ((cert.get("stages") or {}).get("release") or {}).get("status") == "PASS")
    criterion("certified_candidate_identity",
              (cert.get("release_identity") or {}).get("digest") == (candidate.get("release") or {}).get("digest"),
              certified=(cert.get("release_identity") or {}).get("digest"),
              candidate=(candidate.get("release") or {}).get("digest"))

    canary = inputs["canary"]
    criterion("canary", canary.get("verdict") == "PASS" and inputs["canary_candidate_digest"] == candidate["digest"],
              verdict=canary.get("verdict"), failed=[k for k, c in (canary.get("checks") or {}).items()
                                                    if not c.get("passed")])
    checks = canary.get("checks") or {}
    criterion("production_doctor_ready",
              all((checks.get(name) or {}).get("passed") for name in ("production_ready_before", "production_ready_after")))

    streak = inputs["streak"]
    trials = streak.get("trials") or []
    tail = trials[-mc.RELEASE_STREAK_REQUIRED:]
    contradictions = [t["trial"] for t in trials
                      if t.get("outcome") == mc.CERTIFIED and (t.get("reasons") or any(
                          c.get("verdict") != "PASSED" for c in t.get("cases") or []))]
    contradictions += [t["trial"] for t in trials if t.get("candidate_digest") != candidate["digest"]]
    missing_evidence = [t["trial"] for t in tail if not inputs["trial_evidence_present"].get(t["trial"])]
    criterion("live_streak",
              streak.get("status") == mc.CERTIFIED and len(tail) == mc.RELEASE_STREAK_REQUIRED
              and all(t.get("outcome") == mc.CERTIFIED and len(t.get("cases") or []) == len(
                  (candidate.get("case_set") or {}).get("case_ids") or [None]) for t in tail)
              and not contradictions and not missing_evidence,
              streak=streak.get("streak"), trials=[{k: t.get(k) for k in ("trial", "outcome", "streak_after")}
                                                   for t in trials],
              contradictions=contradictions, missing_evidence=missing_evidence)

    hosted_ok, hosted_detail = bool(inputs["hosted"]), []
    for run in inputs["hosted"]:
        jobs = {job["name"]: job.get("conclusion") for job in run.get("jobs") or []}
        bad = {name: c for name, c in jobs.items() if c not in ("success", "skipped")}
        absent = [name for name in REQUIRED_HOSTED_JOBS if jobs.get(name) != "success"]
        ok = run.get("headSha") == revision and not bad and not absent
        hosted_ok = hosted_ok and ok
        hosted_detail.append({"run": run.get("databaseId"), "head": run.get("headSha"), "ok": ok,
                              "not_successful": bad, "required_not_successful": absent})
    criterion("hosted_ci_at_candidate", hosted_ok, runs=hosted_detail)

    criterion("no_open_p0_p1", not inputs["open_blocking"], open=inputs["open_blocking"])
    criterion("tracked_tree_clean", not inputs["tracked_changes"], changes=inputs["tracked_changes"])
    offending = evidence_only(inputs["changed_since_candidate"])
    criterion("evidence_only_since_candidate", not offending, offending=offending,
              changed=inputs["changed_since_candidate"])
    return {"result": "VERIFIED" if all(c["passed"] for c in criteria.values()) else "NOT_VERIFIED",
            "candidate": candidate["digest"], "revision": revision, "criteria": criteria}


def _read(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _git(*args: str) -> List[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def collect(args: argparse.Namespace) -> Dict[str, Any]:
    candidate = _read(args.candidate)
    revision = candidate["release"]["kriya"]["revision"]
    streak = mc.streak_status(candidate["digest"])
    with open(os.path.join(ROOT, "handover", "BACKLOG_REGISTRY.csv"), "r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {
        "candidate": candidate,
        "certification": _read(os.path.join(args.certification, "certification-summary.json")),
        "canary": _read(os.path.join(args.canary, "canary.json")),
        "canary_candidate_digest": _read(os.path.join(args.canary, "release-candidate.json")).get("digest"),
        "streak": streak,
        "trial_evidence_present": {t["trial"]: os.path.exists(os.path.join(t["evidence"], "model-certification.json"))
                                   for t in streak.get("trials") or []},
        "hosted": [_read(path) for path in args.hosted or []],
        "open_blocking": open_blocking_items(rows),
        "tracked_changes": _git("status", "--porcelain", "--untracked-files=no"),
        "changed_since_candidate": _git("diff", "--name-only", f"{revision}..HEAD"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("--candidate", "--certification", "--canary", "--out"):
        parser.add_argument(name, required=True)
    parser.add_argument("--hosted", action="append")
    args = parser.parse_args(argv)
    report = evaluate(collect(args))
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    failed = [name for name, c in report["criteria"].items() if not c["passed"]]
    print(f"[prd036-gate] {report['result']}" + (f": failed {', '.join(failed)}" if failed else ""))
    return 0 if report["result"] == "VERIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
