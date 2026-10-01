"""PRD-032 chaos report: a pure builder over collected chaos results.

The report has two parts:

- ``content``: every scenario of tests/_chaos_harness.py::SCENARIOS with its
  verdict, typed outcome and content-free evidence. It holds no timestamps,
  durations, temp paths or random ids, so two runs of the same revision
  produce the same ``content_digest`` (the reproducibility check).
- ``run``: the metadata of this run (revision, interpreter, time, selected
  tiers), kept apart so it never changes the digest.

tests/conftest.py collects the results and calls ``write_report`` when
``--chaos-report DIR`` is given.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import pytest

REPORT_SCHEMA = "kriya.chaos_report/1"
PASSED, FAILED, SKIPPED, NOT_RUN = "PASSED", "FAILED", "SKIPPED", "NOT_RUN"
_VERDICT_ORDER = (FAILED, PASSED, SKIPPED)

# Per test item: {"setup": report, "call": report} (read by the chaos_case
# fixture to require an observation from every passing chaos test).
PHASE_REPORTS = pytest.StashKey[Dict[str, Any]]()
# Per session: the collected results.
RESULTS = pytest.StashKey[List[Dict[str, Any]]]()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def item_verdict(phases: Mapping[str, Any]) -> str:
    """One test's verdict from its setup/call/teardown reports."""
    reports = [phases[w] for w in ("setup", "call", "teardown") if w in phases]
    if any(r.failed for r in reports):
        return FAILED
    if any(r.skipped for r in reports):
        return SKIPPED
    return PASSED if "call" in phases and phases["call"].passed else NOT_RUN


def _scenario_verdict(verdicts: Iterable[str]) -> str:
    present = set(verdicts)
    for verdict in _VERDICT_ORDER:
        if verdict in present:
            return verdict
    return NOT_RUN


def build_report(
    results: Iterable[Mapping[str, Any]], scenarios: Mapping[str, Any],
    supporting: Mapping[str, Tuple[str, ...]], run: Mapping[str, Any],
) -> Dict[str, Any]:
    by_id: Dict[str, List[Mapping[str, Any]]] = {}
    for result in results:
        by_id.setdefault(result["scenario_id"], []).append(result)
    unknown = sorted(set(by_id) - set(scenarios))
    if unknown:
        raise ValueError(f"chaos results for unregistered scenarios: {unknown}")
    rows = []
    for scenario_id in sorted(scenarios):
        scenario = scenarios[scenario_id]
        found = sorted(by_id.get(scenario_id, []), key=lambda r: r["nodeid"])
        observation = next((r["observation"] for r in found if r.get("observation")), None) or {}
        rows.append({
            "scenario_id": scenario_id,
            "family": scenario.family,
            "boundary": scenario.boundary,
            "tier": scenario.tier,
            "injected_failure": scenario.injected,
            "expected_invariant": scenario.invariant,
            "verdict": _scenario_verdict(r["verdict"] for r in found),
            "observed_outcome": observation.get("outcome"),
            "evidence": observation.get("evidence", {}),
            "identity": observation.get("identity", {}),
            "tests": [r["nodeid"] for r in found],
            "supporting_suites": list(supporting.get(scenario.family, ())),
        })
    summary: Dict[str, Any] = {"total": len(rows)}
    for verdict in (PASSED, FAILED, SKIPPED, NOT_RUN):
        summary[verdict.lower()] = sum(1 for row in rows if row["verdict"] == verdict)
    content = {"schema": REPORT_SCHEMA, "summary": summary, "scenarios": rows}
    return {"content": content, "content_digest": canonical_digest(content), "run": dict(run)}


def render_markdown(report: Mapping[str, Any]) -> str:
    content = report["content"]
    summary = content["summary"]
    run = report["run"]
    lines = [
        "# Kriya chaos report (PRD-032)",
        "",
        f"- Content digest: `{report['content_digest']}`",
        f"- Revision: `{run.get('revision')}`; selection: `{run.get('selection')}`",
        f"- Scenarios: {summary['total']} (passed {summary['passed']}, failed {summary['failed']}, "
        f"skipped {summary['skipped']}, not run {summary['not_run']})",
        "",
        "| Scenario | Family | Tier | Injected failure | Expected invariant | Verdict | Observed outcome |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in content["scenarios"]:
        lines.append(
            f"| {row['scenario_id']} | {row['family']} | {row['tier']} | {row['injected_failure']} | "
            f"{row['expected_invariant']} | {row['verdict']} | {row['observed_outcome'] or '-'} |"
        )
    lines += ["", "## Evidence", ""]
    for row in content["scenarios"]:
        lines.append(f"- **{row['scenario_id']}** `{json.dumps(row['evidence'], sort_keys=True)}`; "
                     f"identity `{json.dumps(row['identity'], sort_keys=True)}`; tests: "
                     + (", ".join(f"`{t}`" for t in row["tests"]) or "none"))
    return "\n".join(lines) + "\n"


def write_report(directory: str, report: Mapping[str, Any]) -> Tuple[str, str]:
    os.makedirs(directory, exist_ok=True)
    json_path = os.path.join(directory, "chaos-report.json")
    md_path = os.path.join(directory, "chaos-report.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    return json_path, md_path
