"""PRD-034: the certification summary of one scripts/certify.sh run.

Reads OUT_DIR/stages.jsonl, the junit files, the skip audits
(skips.json), environment.json, release-identity.json and doctor.json. Writes
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

MANDATORY = ("static", "pytest", "images", "scanner", "release")


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
    for tier in ("pytest", "scanner"):
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
    for name in MANDATORY:
        status = stages.get(name, {}).get("status", "MISSING")
        if status != "PASS":
            problems.append(f"{name}: {status}")
    if stages.get("doctor", {}).get("status") != "RECORDED":
        problems.append("doctor: no parseable JSON")
    for tier, data in tiers.items():
        if data["unexpected_skips"]:
            problems.append(f"{tier}: {data['unexpected_skips']} unexpected skip(s)")
    identity = _json(os.path.join(out, "release-identity.json"))
    kriya = (identity or {}).get("kriya") if isinstance(identity, dict) else None
    if not isinstance(identity, dict) or not identity.get("digest"):
        problems.append("release_identity: not recorded")
    elif not isinstance(kriya, dict) or kriya.get("revision") in (None, "unavailable") or kriya.get("dirty") is not False:
        problems.append("release_identity: Kriya revision not pinned (dirty or unavailable)")
    return {
        "status": "CERTIFIED" if not problems else "NOT_CERTIFIED", "problems": problems,
        "stages": stages, "tiers": tiers, "doctor": doctor_summary,
        "environment": _json(os.path.join(out, "environment.json")),
        "release_identity": identity,
    }


def render(summary: Dict[str, Any]) -> str:
    lines = [f"# Kriya production certification: **{summary['status']}**", ""]
    lines += [f"- {problem}" for problem in summary["problems"]] or ["- every mandatory stage passed"]
    lines += ["", "| Stage | Status | Detail |", "|---|---|---|"]
    for name, record in summary["stages"].items():
        lines.append(f"| {name} | {record['status']} | {str(record.get('detail') or '')[:160]} |")
    lines += ["", "| Tier | Tests | Passed | Failed | Errors | Skipped | Unexpected skips |", "|---|---|---|---|---|---|---|"]
    for tier, data in summary["tiers"].items():
        junit = data["junit"] or {}
        lines.append(f"| {tier} | {junit.get('tests', '-')} | {junit.get('passed', '-')} | {junit.get('failures', '-')} | "
                     f"{junit.get('errors', '-')} | {junit.get('skipped', '-')} | {data['unexpected_skips']} |")
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
    return 0 if summary["status"] == "CERTIFIED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
