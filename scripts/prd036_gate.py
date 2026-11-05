#!/usr/bin/env python3
"""PRD-036 final production release gate.

Reads the evidence and decides; it runs nothing live. VERIFIED only when
every criterion passes, each named with its evidence:

  prd036_gate.py --candidate FROZEN.json --hosted-certification HOSTED_ARTIFACT_DIR --private-record F9B_RECORD.json \\
      --canary CANARY_DIR --hosted RUN.json [--hosted RUN.json ...] --out GATE.json

- deterministic certification (owner ruling 2026-11-05, PRD-034 authority chain: hosted Linux production certification + the exact
  private F9b gate + fail-closed verification = CERTIFIED). The gate RE-VERIFIES the hosted artifact and the private record itself
  (``certification_private_gate.verify_binding``); a hosted job colour or a supplied final.json is never taken on trust. The
  certificate is bound to the candidate's commit, tree and certification-config identity (recomputed from git at the candidate
  revision); the hosted mandatory tiers executed and passed (floors met, none missing, no unexpected skip); the scanner /
  static-analysis tier is proved from the hosted evidence's canonical equivalent (``tier_execution.per_tier.live_static_analysis``
  plus the ``scanner`` stage), not from a field spelling; and every identity field the two authorities share matches exactly.
  The hosted certificate and the full candidate digest are DIFFERENT identity domains and are not compared with each other;
- canary authority: the operator's requirement/acceptance authority (requirements, acceptance suite, approval, verification authority and the
  original-goal -> requirement mapping) is sealed into the candidate digest; the gate RECOMPUTES it from the files through Kriya's own loaders and it
  must equal the candidate's and the one the canary ran with;
- production doctor: PRODUCTION_READY=true on the release config, before and
  after the canary run;
- canary PASS on the same candidate (its candidate digest);
- live streak: RELEASE_STREAK_REQUIRED consecutive complete matrices on the
  candidate (every trial carries the full candidate digest), with every trial's evidence present and no contradiction;
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
# The hosted certification's mandatory tiers (PRD-034 floors): every one must be declared, executed and passed.
MANDATORY_HOSTED_TIERS = ("docker", "gradle_container", "jvm", "live_static_analysis", "real_toolchain")


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
    certificate = inputs["certificate"]
    final, summary = certificate.get("final") or {}, certificate.get("summary") or {}
    hosted_release, hosted_environment = certificate.get("release_identity") or {}, certificate.get("environment") or {}
    record = certificate.get("record") or {}
    record_kriya = record.get("kriya") or {}
    stages = summary.get("stages") or {}
    floors = (summary.get("collection") or {}).get("floors") or {}
    tier_execution = summary.get("tier_execution") or {}
    per_tier = tier_execution.get("per_tier") or {}
    pytest_tier = (summary.get("tiers") or {}).get("pytest") or {}
    junit = pytest_tier.get("junit") or {}

    criterion("deterministic_certificate_final",
              final.get("contract") == "kriya.final-certification.v1" and final.get("status") == "CERTIFIED" and not final.get("problems"),
              status=final.get("status"), problems=final.get("problems"), gate=final.get("gate"))
    revisions = {"candidate": revision, "hosted_release_identity": (hosted_release.get("kriya") or {}).get("revision"),
                 "hosted_environment": hosted_environment.get("git_revision"), "private_record": record_kriya.get("head"),
                 "final_verdict": final.get("hosted_revision")}
    criterion("certificate_bound_to_candidate_revision", bool(revision) and all(v == revision for v in revisions.values())
              and (hosted_release.get("kriya") or {}).get("dirty") is False, revisions=revisions)
    criterion("certificate_bound_to_candidate_tree", bool(certificate.get("git_tree")) and record_kriya.get("tree") == certificate.get("git_tree"),
              private_record=record_kriya.get("tree"), git_tree_of_candidate=certificate.get("git_tree"))
    criterion("certificate_bound_to_config_identity",
              bool(certificate.get("git_config_identity")) and record_kriya.get("config_identity") == certificate.get("git_config_identity"),
              private_record=record_kriya.get("config_identity"), recomputed_at_candidate=certificate.get("git_config_identity"))
    tier_problems = [name for name in MANDATORY_HOSTED_TIERS if name not in floors] + [name for name, floor in floors.items()
                     if (per_tier.get(name) or {}).get("passed", 0) < max(int(floor), 1) or (per_tier.get(name) or {}).get("failed")
                     or (per_tier.get(name) or {}).get("missing") or (per_tier.get(name) or {}).get("skipped")]
    criterion("hosted_mandatory_tiers_passed",
              bool(floors) and tier_execution.get("ok") is True and not tier_execution.get("problems") and not tier_problems
              and junit.get("failures") == 0 and junit.get("errors") == 0 and junit.get("passed", 0) > 0
              and pytest_tier.get("unexpected_skips") == 0,
              tiers_not_met=tier_problems, pytest=pytest_tier, tier_execution_ok=tier_execution.get("ok"))
    scanner = per_tier.get("live_static_analysis") or {}
    criterion("scanner_execution_proved", int(floors.get("live_static_analysis", 0)) >= 1 and scanner.get("passed", 0) >= int(floors["live_static_analysis"])
              and not scanner.get("failed") and not scanner.get("missing") and (stages.get("scanner") or {}).get("status") == "PASS",
              live_static_analysis=scanner, scanner_stage=(stages.get("scanner") or {}).get("status"))
    criterion("release_stage_hosted", (stages.get("release") or {}).get("status") == "PASS")
    criterion("private_gate_bound_to_candidate",
              bool(record) and record.get("pytest_exit_code") == 0 and bool(record.get("results"))
              and all(v == "passed" for v in (record.get("results") or {}).values())
              and final.get("private_evidence_digest") == record.get("evidence_digest") and bool(record.get("evidence_digest"))
              and record_kriya.get("head") == revision and record.get("gate") == final.get("gate"),
              gate=record.get("gate"), head=record_kriya.get("head"))

    canary = inputs["canary"]
    criterion("canary", canary.get("verdict") == "PASS" and inputs["canary_candidate_digest"] == candidate["digest"],
              verdict=canary.get("verdict"), failed=[k for k, c in (canary.get("checks") or {}).items()
                                                    if not c.get("passed")])
    authority = inputs.get("canary_authority") or {}
    sealed = candidate.get("canary_authority") or {}
    criterion("canary_authority_bound",
              bool(sealed.get("digest")) and not authority.get("error") and not authority.get("changes")
              and authority.get("recomputed") == sealed.get("digest") and authority.get("canary_run") == sealed.get("digest"),
              sealed_in_candidate=sealed.get("digest"), recomputed_from_files=authority.get("recomputed"),
              used_by_canary=authority.get("canary_run"), changes=authority.get("changes"), error=authority.get("error"))
    checks = canary.get("checks") or {}
    readiness = {name: c.get("passed") for name, c in checks.items()
                 if name.endswith(("production_ready_before", "production_ready_after"))}
    criterion("production_doctor_ready", len(readiness) >= 2 and all(readiness.values()), checks=readiness)

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


def _private_gate_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("certification_private_gate", os.path.join(ROOT, "scripts", "certification_private_gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_certificate(hosted_dir: str, record_path: str, revision: str) -> Dict[str, Any]:
    """The deterministic-certification evidence, RE-VERIFIED from the artifacts: the hosted certification output and the private F9b
    record go back through ``verify_binding``; the candidate's tree and certification-config identity are recomputed from git."""
    private = _private_gate_module()
    record = _read(record_path)
    try:
        final = private.verify_binding(hosted_dir, record, ROOT)
    except Exception as error:  # noqa: BLE001 - unreadable evidence is a refusal with its reason, never a crash and never a pass
        final = {"contract": private.FINAL_CONTRACT, "status": "NOT_CERTIFIED", "problems": [f"verification could not run: {error}"]}
    expected = {}
    try:
        expected = private.kriya_identity(ROOT, revision)
    except Exception as error:  # noqa: BLE001
        final = {**final, "status": "NOT_CERTIFIED", "problems": [*final.get("problems", []), f"cannot recompute the candidate identity: {error}"]}

    def optional(name: str) -> Dict[str, Any]:
        path = os.path.join(hosted_dir, name)
        return _read(path) if os.path.exists(path) else {}

    return {"final": final, "record": record, "summary": optional("certification-summary.json"),
            "release_identity": optional("release-identity.json"), "environment": optional("environment.json"),
            "git_tree": expected.get("tree"), "git_config_identity": expected.get("config_identity")}


def collect_canary_authority(args: argparse.Namespace, candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """The canary authority RECOMPUTED from the files (Kriya's own loaders), compared with the one sealed into the candidate and the one the canary used."""
    import prd036_canary_authority as canary_authority

    used_path = os.path.join(args.canary, "canary-authority.json")
    result: Dict[str, Any] = {"canary_run": _read(used_path).get("digest") if os.path.exists(used_path) else None}
    try:
        current = canary_authority.identity(args.canary_authority_dir, args.goal, args.workspace, args.expect)
    except canary_authority.CanaryAuthorityError as error:
        return {**result, "error": "; ".join(error.problems)}
    return {**result, "recomputed": current["digest"], "changes": canary_authority.compare(candidate.get("canary_authority") or {}, current)}


def collect(args: argparse.Namespace) -> Dict[str, Any]:
    candidate = _read(args.candidate)
    revision = candidate["release"]["kriya"]["revision"]
    streak = mc.streak_status(candidate["digest"])
    with open(os.path.join(ROOT, "handover", "BACKLOG_REGISTRY.csv"), "r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    certificate = collect_certificate(args.hosted_certification, args.private_record, revision)
    return {
        "candidate": candidate,
        "certificate": certificate,
        "canary_authority": collect_canary_authority(args, candidate),
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
    for name in ("--candidate", "--hosted-certification", "--private-record", "--canary", "--canary-authority-dir", "--goal", "--workspace", "--expect", "--out"):
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
