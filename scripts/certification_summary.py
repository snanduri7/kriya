"""PRD-034: the certification summary of one scripts/certify.sh run.

Reads OUT_DIR/stages.jsonl, the junit file, the skip audit
(skips.json), the collection audit and tier-execution records, thresholds.json (when frozen),
doctor-mode.json, environment.json, release-identity.json and doctor.json. Writes
certification-summary.json and certification-summary.md. The overall status
is CERTIFIED only when every mandatory stage (static, pytest, images,
scanner, release) is PASS, the doctor produced parseable JSON, and no unexpected skip
remains, and the release identity (kriya/core/release_identity.py:
Kriya revision, platform providers and capabilities, containment backend,
environment) was recorded from a clean, pinned revision. The doctor's own
PRODUCTION_READY verdict is reported exactly as it is. The exit status is
0 when CERTIFIED and 1 otherwise.
"""
import json
import os
import sys
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Optional

# Exit status: 0 CERTIFIED; 1 NOT_CERTIFIED; 3 INCOMPLETE_PLATFORM_EVIDENCE (never a pass).
MANDATORY = ("static", "maven_repository", "maven_served", "python_wheelhouse", "python_served", "product_images", "images_unchanged", "collection", "perf_probe", "pytest", "tier_execution", "images", "scanner", "release")


def _junit(path: str) -> Optional[Dict[str, int]]:
    if not os.path.exists(path):
        return None
    root = ElementTree.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    return totals


def _json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def summarize(out: str) -> Dict[str, Any]:
    stages: Dict[str, Dict[str, Any]] = {}
    with open(os.path.join(out, "stages.jsonl"), "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                stages[record["stage"]] = record
    tiers = {}
    for tier in ("pytest",):  # the scanner tier runs INSIDE the canonical pytest population (PRD-034 C3): no second junit
        skips = _json(os.path.join(out, tier, "skips.json")) or {}
        tiers[tier] = {"junit": _junit(os.path.join(out, tier, "junit.xml")),
                       "skipped": skips.get("skipped"), "unexpected_skips": skips.get("unexpected")}
    doctor = _json(os.path.join(out, "doctor.json"))
    doctor_summary = None
    if isinstance(doctor, dict):
        checks = doctor.get("checks") or []
        doctor_summary = {
            "production_ready": doctor.get("production_ready"),
            "failed_required": sorted(c["id"] for c in checks if c.get("required") and c.get("status") == "FAIL"),
            "statuses": {status: sum(1 for c in checks if c.get("status") == status)
                         for status in sorted({c.get("status") for c in checks})},
        }
    problems: List[str] = []
    if stages.get("thresholds", {}).get("status", "RECORDED") != "RECORDED":
        problems.append(f"thresholds: {stages['thresholds']['status']}")
    for name in MANDATORY:
        status = stages.get(name, {}).get("status", "MISSING")
        if status != "PASS":
            problems.append(f"{name}: {status}")
    if stages.get("doctor", {}).get("status") != "RECORDED":
        problems.append("doctor: no parseable JSON")
    for tier, data in tiers.items():
        if data["unexpected_skips"]:
            problems.append(f"{tier}: {data['unexpected_skips']} unexpected skip(s)")
    tier_execution = _json(os.path.join(out, "tier-execution.json")) or {}
    pending = list(tier_execution.get("platform_pending") or [])  # mandatory on ANOTHER platform: never a pass here
    identity = _json(os.path.join(out, "release-identity.json"))
    kriya = (identity or {}).get("kriya") if isinstance(identity, dict) else None
    if not isinstance(identity, dict) or not identity.get("digest"):
        problems.append("release_identity: not recorded")
    elif not isinstance(kriya, dict) or kriya.get("revision") in (None, "unavailable") or kriya.get("dirty") is not False:
        problems.append("release_identity: Kriya revision not pinned (dirty or unavailable)")
    # A final CERTIFIED needs every required platform's evidence. Executable evidence all passing while a platform's is still missing is the
    # distinct INCOMPLETE_PLATFORM_EVIDENCE: not a pass, not a failure of what ran.
    status = "NOT_CERTIFIED" if problems else ("INCOMPLETE_PLATFORM_EVIDENCE" if pending else "CERTIFIED")
    return {
        "status": status, "problems": problems, "platform_pending": pending,
        "stages": stages, "tiers": tiers, "doctor": doctor_summary,
        "environment": _json(os.path.join(out, "environment.json")),
        "collection": _json(os.path.join(out, "collection", "collection-audit.json")),
        "tier_execution": _json(os.path.join(out, "tier-execution.json")),
        "doctor_mode": _json(os.path.join(out, "doctor-mode.json")),
        "thresholds": _json(os.path.join(out, "thresholds.json")),
        "maven_repository": _json(os.path.join(out, "maven-repo.json")),
        "python_wheelhouse": _json(os.path.join(out, "python-wheelhouse.json")),
        "release_identity": identity,
    }


def render(summary: Dict[str, Any]) -> str:
    lines = [f"# Kriya production certification: **{summary['status']}**", ""]
    lines += [f"- {problem}" for problem in summary["problems"]] or ["- every mandatory stage that is executable here passed"]
    for item in summary.get("platform_pending") or []:
        lines.append(f"- REQUIRED {item['required_platform'].upper()}-HOST EVIDENCE - NOT EXERCISED LOCALLY (OPEN until a {item['required_platform']} certification "
                     f"executes it and it passes): `{item['id']}`")
    lines += ["", "| Stage | Status | Detail |", "|---|---|---|"]
    for name, record in summary["stages"].items():
        lines.append(f"| {name} | {record['status']} | {str(record.get('detail') or '')[:160]} |")
    lines += ["", "| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |", "|---|---|---|---|---|---|---|"]
    for tier, data in summary["tiers"].items():
        junit = data["junit"] or {}
        lines.append(f"| {tier} | {junit.get('tests', '-')} | {junit.get('passed', '-')} | {junit.get('failures', '-')} | "
                     f"{junit.get('errors', '-')} | {junit.get('skipped', '-')} | {data['unexpected_skips']} |")
    collection = summary.get("collection") or {}
    lines += ["", "## Population", "",
              f"- canonical selection `{collection.get('selection')}`: {collection.get('selected')} selected, {collection.get('deselected')} deselected "
              f"(of {collection.get('total')}); tiers selected: {json.dumps(collection.get('tiers_selected'), sort_keys=True)}",
              f"- tier execution: {json.dumps((summary.get('tier_execution') or {}).get('per_tier'), sort_keys=True)}",
              f"- doctor mode: {json.dumps(summary.get('doctor_mode'), sort_keys=True)}"]
    maven = summary.get("maven_repository")
    if maven:
        lines += [f"- deterministic Maven repository: manifest content digest `{maven.get('manifest_content_digest')}` ({maven.get('files')} files, attested: {maven.get('attested')}); "
                  f"the run fetched {maven.get('served_paths')} distinct files in {maven.get('requests')} requests {json.dumps(maven.get('status_counts'), sort_keys=True)}; "
                  f"requests outside the frozen set: {len(maven.get('not_in_manifest_requests') or [])}; bytes-differ refusals: {maven.get('bytes_differ_refusals')}"]
    wheels = summary.get("python_wheelhouse")
    if wheels:
        lines += [f"- frozen Python wheelhouse: hashed lock sha256 `{wheels.get('hashed_requirements_sha256')}`, {wheels.get('wheelhouse_files')} files, wheelhouse digest `{wheels.get('wheelhouse_sha256')}` "
                  "(release stage: --no-index --require-hashes, fresh venv)"]
    thresholds = summary.get("thresholds")
    if thresholds:
        lines += [f"- thresholds: {thresholds.get('source_path')} sha256 {thresholds.get('file_sha256')} loader digest {thresholds.get('loader_digest')}; "
                  f"effective {json.dumps(thresholds.get('effective'), sort_keys=True)}"]
    doctor = summary["doctor"]
    lines += ["", "## Production doctor (recorded as reported)", "",
              "```json", json.dumps(doctor, indent=2, sort_keys=True), "```",
              "", "## Environment", "", "```json", json.dumps(summary["environment"], indent=2, sort_keys=True), "```",
              "", "## Release identity (a material change makes this certification stale)", "",
              "```json", json.dumps(summary["release_identity"], indent=2, sort_keys=True), "```"]
    return "\n".join(lines) + "\n"


def main(out: str) -> int:
    summary = summarize(out)
    with open(os.path.join(out, "certification-summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(os.path.join(out, "certification-summary.md"), "w", encoding="utf-8") as handle:
        handle.write(render(summary))
    print(f"[certify] {summary['status']}" + "".join(f"\n  - {p}" for p in summary["problems"]))
    return {"CERTIFIED": 0, "INCOMPLETE_PLATFORM_EVIDENCE": 3}.get(summary["status"], 1)  # 3: all executable evidence passed, other-platform evidence open


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
