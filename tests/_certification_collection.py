"""PRD-034: collection truth for the canonical certification.

Zero skipped tests proves nothing when a mandatory group never reached the run (WAVE5-LIVE-MODEL-MARKED-MAVEN-TEST-001: a real-Maven test carried
the ``live_model`` marker, so the canonical ``-m "not live_model"`` population silently lacked it and nothing reported a skip). This module audits the
POPULATION, not the outcomes:

* ``--collection-census FILE`` (tests/conftest.py) records every collected test: node id, marker names, and the tier facts below.
* ``audit``: the unfiltered census vs the census of the canonical selection. Every deselected test must be ``live_model`` and nothing else; every
  mandatory tier must collect at least its recorded floor; the selection expression must be exactly ``not live_model``.
* ``executed``: the canonical run's junit file vs its census. Every test of a mandatory tier must appear and have PASSED (not skipped, not missing).

Tier facts are by explicit rule over what the test itself declares (markers, the skip reasons of its skip/skipif marks), never by file name alone.
A floor moves only when the tier legitimately changes, in the same commit as the change (like the architecture pins).
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from _certification import platform_satisfied, required_platform

CANONICAL_SELECTION = "not live_model"
DESELECTABLE_TIER = "live_model"  # the ONLY tier the canonical selection may leave out
TIER_MARKERS = ("live_model", "live_static_analysis", "real_toolchain")

# Mandatory tiers and their floors (MEASURED 2026-11-02 at the canonical selection: 19638 selected of 19683, 45 deselected). docker: tests whose skip reason names docker (the
# tests that start real containers); jvm: tests that declare a JDK/Maven requirement; gradle_container: Docker tests of the Gradle containment.
MANDATORY_TIER_FLOORS: Dict[str, int] = {
    "real_toolchain": 5,
    "live_static_analysis": 11,
    "docker": 283,
    "jvm": 86,
    "gradle_container": 5,
}
_JVM_REASON = re.compile(r"\b(jdk|javac|java|mvn|maven)\b", re.IGNORECASE)


def census_record(nodeid: str, markers: Iterable[Any], needs_docker: bool) -> Dict[str, Any]:
    markers = list(markers)  # item.iter_markers() is a generator: both reads below need it
    names = sorted({marker.name for marker in markers})
    reasons = sorted({str(marker.kwargs.get("reason", "")) for marker in markers if marker.name in ("skip", "skipif")} - {""})
    return {"id": nodeid, "markers": names, "skip_reasons": reasons, "docker": bool(needs_docker), "requires_platform": required_platform(markers)}


def tiers_of(record: Mapping[str, Any]) -> List[str]:
    tiers = {name for name in record.get("markers", ()) if name in TIER_MARKERS}
    if record.get("docker"):
        tiers.add("docker")
        if "gradle" in str(record["id"]).lower():
            tiers.add("gradle_container")
    if "real_toolchain" in tiers or any(_JVM_REASON.search(reason) for reason in record.get("skip_reasons", ())):
        tiers.add("jvm")
    return sorted(tiers)


def write_census(path: str, records: Sequence[Mapping[str, Any]], *, selection: str, args: Sequence[str]) -> None:
    payload = {"selection": selection, "args": list(args), "count": len(records),
               "tests": sorted(records, key=lambda record: record["id"])}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")


def _load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _tier_counts(records: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        for tier in tiers_of(record):
            counts[tier] = counts.get(tier, 0) + 1
    return dict(sorted(counts.items()))


def audit(all_census: Mapping[str, Any], selected_census: Mapping[str, Any],
          floors: Optional[Mapping[str, int]] = None) -> Dict[str, Any]:
    """The collection/deselection verdict. ``problems`` empty means the canonical population is exactly what was intended."""
    floors = MANDATORY_TIER_FLOORS if floors is None else floors
    everything = {record["id"]: record for record in all_census["tests"]}
    selected = {record["id"]: record for record in selected_census["tests"]}
    deselected = {nodeid: record for nodeid, record in everything.items() if nodeid not in selected}
    problems: List[str] = []
    if selected_census.get("selection") != CANONICAL_SELECTION:
        problems.append(f"selection is {selected_census.get('selection')!r}, not the canonical {CANONICAL_SELECTION!r}")
    if all_census.get("selection"):
        problems.append(f"the unfiltered census was itself filtered by {all_census.get('selection')!r}")
    for nodeid in sorted(set(selected) - set(everything)):
        problems.append(f"selected but absent from the unfiltered census: {nodeid}")
    for nodeid, record in sorted(deselected.items()):
        if DESELECTABLE_TIER not in record["markers"]:
            problems.append(f"DESELECTED but not {DESELECTABLE_TIER}: {nodeid}")
    for nodeid, record in sorted(selected.items()):
        if DESELECTABLE_TIER in record["markers"]:
            problems.append(f"{DESELECTABLE_TIER} test selected by the canonical run: {nodeid}")
    for tier in TIER_MARKERS:
        if tier == DESELECTABLE_TIER:
            continue
        missing = sorted(nodeid for nodeid, record in everything.items() if tier in record["markers"] and nodeid not in selected)
        problems += [f"{tier} test missing from the canonical population: {nodeid}" for nodeid in missing]
    selected_tiers = _tier_counts(selected.values())
    for tier, floor in sorted(floors.items()):
        if selected_tiers.get(tier, 0) < max(1, floor):
            problems.append(f"mandatory tier {tier!r} collects {selected_tiers.get(tier, 0)} < floor {max(1, floor)}")
    return {"ok": not problems, "problems": problems, "total": len(everything), "selected": len(selected),
            "deselected": len(deselected), "deselected_ids": sorted(deselected),
            "tiers_selected": selected_tiers, "tiers_all": _tier_counts(everything.values()),
            "floors": dict(sorted(floors.items())), "selection": selected_census.get("selection"),
            "selected_args": selected_census.get("args")}


def _junit_key(nodeid: str):
    head, bracket, params = nodeid.partition("[")
    parts = head.split("::")
    module = parts[0][:-3] if parts[0].endswith(".py") else parts[0]
    return ".".join([module.replace("/", ".")] + parts[1:-1]), parts[-1] + bracket + params


def junit_outcomes(path: str) -> Dict[Any, str]:
    outcomes: Dict[Any, str] = {}
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        status = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                status = "failed"
            elif child.tag == "skipped" and status == "passed":
                status = "skipped"
        outcomes[(case.get("classname", ""), case.get("name", ""))] = status
    return outcomes


def executed(selected_census: Mapping[str, Any], outcomes: Mapping[Any, str],
             floors: Optional[Mapping[str, int]] = None, platform: Optional[str] = None) -> Dict[str, Any]:
    """Every test of every mandatory tier appears in the run and PASSED (the outcome, not merely the absence of a skip).

    A test mandatory on ANOTHER platform (``requires_platform``) cannot be satisfied here: whatever it did locally, it is PENDING platform evidence
    (``platform_pending``), never counted as a pass, and the certification summary cannot be CERTIFIED while one is pending. On the required platform it
    must have PASSED like any mandatory test."""
    floors = MANDATORY_TIER_FLOORS if floors is None else floors
    per_tier: Dict[str, Dict[str, int]] = {tier: {"passed": 0, "skipped": 0, "failed": 0, "missing": 0} for tier in floors}
    problems: List[str] = []
    pending: List[Dict[str, str]] = []
    for record in selected_census["tests"]:
        required = record.get("requires_platform")
        if required:
            status = outcomes.get(_junit_key(record["id"]), "missing")
            if platform_satisfied(required, platform):
                if status != "passed":
                    problems.append(f"platform {required}: {record['id']} -> {status}")
            else:
                pending.append({"id": record["id"], "required_platform": required, "local_outcome": status})
        for tier in tiers_of(record):
            if tier not in per_tier:
                continue
            status = outcomes.get(_junit_key(record["id"]), "missing")
            per_tier[tier][status] += 1
            if status != "passed":
                problems.append(f"{tier}: {record['id']} -> {status}")
    for tier, counts in per_tier.items():
        if counts["passed"] < max(1, floors[tier]):
            problems.append(f"mandatory tier {tier!r}: {counts['passed']} passed < floor {max(1, floors[tier])}")
    return {"ok": not problems, "problems": problems, "per_tier": per_tier, "platform_pending": pending}


def render_audit(report: Mapping[str, Any]) -> str:
    lines = [f"# Collection audit: **{'PASS' if report['ok'] else 'FAIL'}**", "",
             f"- total collected unfiltered: {report['total']}; canonical selection `{report['selection']}`: {report['selected']} selected, "
             f"{report['deselected']} deselected", "- every deselected test must be `live_model` and nothing else", ""]
    lines += [f"- PROBLEM: {problem}" for problem in report["problems"]]
    lines += ["", "| Tier | All | Selected | Floor |", "|---|---|---|---|"]
    for tier in sorted(set(report["tiers_all"]) | set(report["floors"])):
        lines.append(f"| {tier} | {report['tiers_all'].get(tier, 0)} | {report['tiers_selected'].get(tier, 0)} | {report['floors'].get(tier, '-')} |")
    return "\n".join(lines) + "\n"


def _write(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: Sequence[str]) -> int:
    usage = "usage: _certification_collection.py audit ALL.json SELECTED.json OUT_DIR | executed SELECTED.json JUNIT.xml OUT.json"
    if len(argv) == 5 and argv[1] == "audit":
        report = audit(_load(argv[2]), _load(argv[3]))
        _write(f"{argv[4]}/collection-audit.json", report)
        with open(f"{argv[4]}/collection-audit.md", "w", encoding="utf-8") as handle:
            handle.write(render_audit(report))
        with open(f"{argv[4]}/deselected-node-ids.txt", "w", encoding="utf-8") as handle:
            handle.write("".join(f"{nodeid}\n" for nodeid in report["deselected_ids"]))
        print(f"[collection-audit] {'PASS' if report['ok'] else 'FAIL'}: {report['selected']} selected / {report['deselected']} deselected"
              + "".join(f"\n  - {problem}" for problem in report["problems"][:20]))
        return 0 if report["ok"] else 1
    if len(argv) == 5 and argv[1] == "executed":
        report = executed(_load(argv[2]), junit_outcomes(argv[3]))
        _write(argv[4], report)
        print(f"[tier-execution] {'PASS' if report['ok'] else 'FAIL'}: {json.dumps(report['per_tier'], sort_keys=True)}"
              + "".join(f"\n  - PLATFORM EVIDENCE PENDING ({item['required_platform']}): {item['id']}" for item in report["platform_pending"])
              + "".join(f"\n  - {problem}" for problem in report["problems"][:20]))
        return 0 if report["ok"] else 1
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
